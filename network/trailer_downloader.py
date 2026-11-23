"""Download/remux di un trailer Steam HLS in un MP4 locale.

Steam serves the trailers as HLS: a master playlist with one audio group and
several video variants, each a media playlist of short fMP4 segments. A
single HTTP connection is capped by the CDN, but the aggregate scales across
connections, so the segments of the chosen variant are downloaded with a
small thread pool, concatenated in order and remuxed into a regular seekable
MP4 with ffmpeg.

The old "one ffmpeg process on the master playlist" path is kept as a
fallback for playlists the parser does not understand.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin

import requests
from PyQt6.QtCore import QThread, pyqtSignal

from core.config import get_base_dir
from core.paths import PathManager

CACHE_DIRNAME = "trailers"
MAX_CACHE_BYTES = 1024 * 1024 * 1024   # 1 GB, LRU eviction
MIN_VALID_BYTES = 60 * 1024
PARALLEL_DOWNLOADS = 8
SEGMENT_RETRIES = 3
REQUEST_TIMEOUT = 20
DEFAULT_VARIANT = "1080p"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


class TrailerExpiredError(Exception):
    """The HLS URL returned 403/404: the cached appdetails URL is stale."""


class TrailerDownloadError(Exception):
    """A playlist or a segment could not be downloaded."""


class _Cancelled(Exception):
    pass


# ── HLS parsing ──────────────────────────────────────────────────────

_ATTR_RE = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)')


def _parse_attrs(text):
    attrs = {}
    for key, value in _ATTR_RE.findall(text or ""):
        attrs[key] = value.strip('"')
    return attrs


def parse_master(text, master_url):
    """Return ``{"audio": url, "variants": [{label, height, url}]}``."""
    audio = ""
    variants = []
    info = None
    for raw in (text or "").splitlines():
        line = raw.strip()
        if line.startswith("#EXT-X-MEDIA:"):
            attrs = _parse_attrs(line[len("#EXT-X-MEDIA:"):])
            if attrs.get("TYPE") == "AUDIO" and attrs.get("URI"):
                if not audio or attrs.get("DEFAULT") == "YES":
                    audio = urljoin(master_url, attrs["URI"])
        elif line.startswith("#EXT-X-STREAM-INF:"):
            info = _parse_attrs(line[len("#EXT-X-STREAM-INF:"):])
        elif info is not None and line and not line.startswith("#"):
            height = 0
            match = re.match(r"(\d+)x(\d+)", info.get("RESOLUTION", ""))
            if match:
                height = int(match.group(2))
            try:
                bandwidth = int(info.get("BANDWIDTH", "0"))
            except ValueError:
                bandwidth = 0
            label = f"{height}p" if height else f"{bandwidth // 1000}k"
            variants.append({
                "label": label,
                "height": height,
                "bandwidth": bandwidth,
                "url": urljoin(master_url, line),
            })
            info = None
    return {"audio": audio, "variants": variants}


def parse_media_playlist(text, playlist_url):
    """Return ``(init_url, [segment_url, ...])`` for a video/audio playlist."""
    init_url = ""
    segments = []
    has_byterange = False
    for raw in (text or "").splitlines():
        line = raw.strip()
        if line.startswith("#EXT-X-MAP:"):
            attrs = _parse_attrs(line[len("#EXT-X-MAP:"):])
            if attrs.get("URI"):
                init_url = urljoin(playlist_url, attrs["URI"])
        elif line.startswith("#EXT-X-BYTERANGE"):
            has_byterange = True
        elif line and not line.startswith("#"):
            segments.append(urljoin(playlist_url, line))
    if has_byterange:
        raise TrailerDownloadError("byte-range playlists are not supported")
    return init_url, segments


def pick_variant(variants, preferred=DEFAULT_VARIANT):
    """Prefer ``preferred`` (1080p by default), else the best available."""
    if not variants:
        return None
    for variant in variants:
        if preferred and variant.get("label") == preferred:
            return variant
    for variant in variants:
        if variant.get("height") == 1080:
            return variant
    return max(variants, key=lambda v: (v.get("height") or 0,
                                        v.get("bandwidth") or 0))


# ── HTTP ─────────────────────────────────────────────────────────────

def _get(url):
    try:
        response = requests.get(url, headers={"User-Agent": UA},
                                timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        raise TrailerDownloadError(str(exc))
    if response.status_code in (403, 404):
        raise TrailerExpiredError(url)
    if response.status_code != 200:
        raise TrailerDownloadError(f"HTTP {response.status_code} for {url}")
    return response


# ── ffmpeg ───────────────────────────────────────────────────────────

def _bundled_ffmpeg():
    """ffmpeg.exe accanto all'app; se c'e' solo lo zip lo estrae una volta."""
    if not sys.platform.startswith("win"):
        return None
    base = get_base_dir()
    exe = os.path.join(base, "bin", "ffmpeg.exe")
    if os.path.isfile(exe):
        return exe
    archive = os.path.join(base, "bin", "ffmpeg.zip")
    if not os.path.isfile(archive):
        return None
    try:
        import zipfile

        tmp = exe + ".tmp"
        with zipfile.ZipFile(archive) as z:
            name = next((n for n in z.namelist()
                         if n.lower().endswith("ffmpeg.exe")), None)
            if not name:
                return None
            with z.open(name) as src, open(tmp, "wb") as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
        os.replace(tmp, exe)
        print("[TrailerDownload] ffmpeg estratto da bin/ffmpeg.zip")
        return exe
    except Exception as e:
        print(f"[TrailerDownload] estrazione ffmpeg fallita: {e}")
        return None


def find_ffmpeg():
    bundled = _bundled_ffmpeg()
    if bundled:
        return bundled
    candidates = [
        os.path.join(get_base_dir(), "ffmpeg.exe"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg  # opzionale
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


# ── cache ────────────────────────────────────────────────────────────

def cache_dir():
    d = PathManager.get_cache_dir() / CACHE_DIRNAME
    os.makedirs(str(d), exist_ok=True)
    return str(d)


def cached_path(key):
    if not key:
        return ""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(key))
    return os.path.join(cache_dir(), safe + ".mp4")


def prune_cache(max_bytes=MAX_CACHE_BYTES):
    """LRU eviction: keep the trailer cache under ``max_bytes``.

    Files are ordered by mtime (download time, or last playback because the
    player touches the local file), so the least recently used ones go first.
    """
    try:
        folder = cache_dir()
        files = []
        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            if os.path.isdir(path) and name.endswith(".parts"):
                # Leftover from an interrupted parallel download.
                try:
                    if time.time() - os.path.getmtime(path) > 6 * 3600:
                        shutil.rmtree(path, ignore_errors=True)
                except OSError:
                    pass
                continue
            if not name.lower().endswith(".mp4"):
                continue
            try:
                files.append((os.path.getmtime(path), os.path.getsize(path), path))
            except OSError:
                pass
        total = sum(size for _m, size, _p in files)
        if total <= max_bytes:
            return
        files.sort()
        for _mtime, size, path in files:
            if total <= max_bytes:
                break
            try:
                os.remove(path)
                total -= size
            except OSError:
                pass
    except Exception:
        pass


_TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+\.?\d*)")
_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


class TrailerDownloadWorker(QThread):

    progress = pyqtSignal(int)      # 0-100
    ready = pyqtSignal(str)         # path locale
    failed = pyqtSignal(str)
    expired = pyqtSignal()          # HLS URL stale: the caller should refresh

    def __init__(self, url, key, duration_ms=0, variant_label=DEFAULT_VARIANT,
                 parent=None):
        super().__init__(parent)
        self._url = url
        self._out = cached_path(key)
        self._duration_ms = int(duration_ms or 0)
        self._variant_label = variant_label or DEFAULT_VARIANT
        self._variant_index = 0
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        if os.path.isfile(self._out) and os.path.getsize(self._out) > MIN_VALID_BYTES:
            self.progress.emit(100)
            self.ready.emit(self._out)
            return
        if not self._url or not self._out:
            self.failed.emit("url trailer mancante")
            return
        try:
            self._download_parallel()
            return
        except TrailerExpiredError:
            self.expired.emit()
            return
        except _Cancelled:
            return
        except TrailerDownloadError as exc:
            print(f"[TrailerDownload] parallel download failed: {exc}")
        except Exception as exc:
            print(f"[TrailerDownload] parallel download error: {exc}")
        self._download_sequential()

    # ── parallel path ────────────────────────────────────────────────

    def _download_parallel(self):
        master = parse_master(_get(self._url).text, self._url)
        variants = master["variants"]
        variant = pick_variant(variants, self._variant_label)
        if variant is None:
            raise TrailerDownloadError("no video variant in the master playlist")
        try:
            self._variant_index = variants.index(variant)
        except ValueError:
            self._variant_index = 0

        video_init, video_segments = parse_media_playlist(
            _get(variant["url"]).text, variant["url"])
        if not video_segments:
            raise TrailerDownloadError("empty video playlist")

        audio_init, audio_segments = "", []
        if master.get("audio"):
            try:
                audio_init, audio_segments = parse_media_playlist(
                    _get(master["audio"]).text, master["audio"])
            except TrailerDownloadError as exc:
                print(f"[TrailerDownload] audio playlist skipped: {exc}")
                audio_init, audio_segments = "", []

        total = len(video_segments) + len(audio_segments)
        base_name = os.path.basename(self._out).replace(".mp4", "")
        work = tempfile.mkdtemp(prefix=base_name + ".", suffix=".parts",
                                dir=cache_dir())
        try:
            video_path = os.path.join(work, "video.mp4")
            done = self._fetch_stream(
                video_init, video_segments, video_path, 0, total, work, "v")
            audio_path = ""
            if audio_segments:
                audio_path = os.path.join(work, "audio.mp4")
                self._fetch_stream(
                    audio_init, audio_segments, audio_path, done, total, work, "a")

            final_tmp = os.path.join(work, "trailer.mp4")
            self._mux(video_path, audio_path, final_tmp)
            if not os.path.isfile(final_tmp) or os.path.getsize(final_tmp) < MIN_VALID_BYTES:
                raise TrailerDownloadError("ffmpeg produced an invalid file")

            os.replace(final_tmp, self._out)
            prune_cache()
            self.progress.emit(100)
            self.ready.emit(self._out)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _fetch_stream(self, init_url, segments, dest, done, total, work, tag):
        """Download ``segments`` in parallel and concatenate them into ``dest``."""
        with open(dest, "wb") as handle:
            if init_url:
                handle.write(_get(init_url).content)

        def fetch(index, url):
            part = os.path.join(work, f"{tag}_{index:05d}.part")
            for attempt in range(SEGMENT_RETRIES):
                if self._stop.is_set():
                    raise _Cancelled()
                try:
                    with open(part, "wb") as handle:
                        handle.write(_get(url).content)
                    return index, part
                except TrailerExpiredError:
                    raise
                except TrailerDownloadError:
                    if attempt == SEGMENT_RETRIES - 1:
                        raise
                    time.sleep(0.5 * (attempt + 1))
            raise TrailerDownloadError(f"segment failed: {url}")

        futures = {}
        with ThreadPoolExecutor(max_workers=PARALLEL_DOWNLOADS) as pool:
            for index, url in enumerate(segments):
                futures[pool.submit(fetch, index, url)] = index
            try:
                results = {}
                for future in as_completed(futures):
                    index, part = future.result()
                    results[index] = part
                    done += 1
                    self.progress.emit(min(99, int(done * 100 / max(1, total))))
            except Exception:
                for future in futures:
                    future.cancel()
                raise

        with open(dest, "ab") as handle:
            for index in range(len(segments)):
                part = results[index]
                with open(part, "rb") as source:
                    shutil.copyfileobj(source, handle, 1024 * 1024)
        return done

    def _mux(self, video_path, audio_path, dest):
        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            raise TrailerDownloadError("ffmpeg non disponibile")
        cmd = [ffmpeg, "-y", "-loglevel", "error", "-i", video_path]
        if audio_path:
            cmd += ["-i", audio_path, "-map", "0:v:0", "-map", "1:a:0?"]
        else:
            cmd += ["-map", "0:v:0"]
        cmd += ["-c", "copy", "-movflags", "+faststart", "-f", "mp4", dest]
        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                timeout=180,
            )
        except Exception as exc:
            raise TrailerDownloadError(f"ffmpeg mux failed: {exc}")
        if proc.returncode != 0:
            raise TrailerDownloadError("ffmpeg mux failed")

    # ── sequential fallback (legacy ffmpeg-on-the-master path) ───────

    def _download_sequential(self):
        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            self.failed.emit("ffmpeg non disponibile")
            return
        tmp = (self._out[:-4] + ".part.mp4") if self._out.lower().endswith(".mp4") else (self._out + ".part.mp4")
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass

        # Use the chosen variant when the master was parsed, else the first.
        video_index = max(0, int(self._variant_index or 0))
        cmd = [
            ffmpeg, "-y", "-loglevel", "info",
            "-i", self._url,
            "-map", f"0:v:{video_index}", "-map", "0:a:0?",
            "-c", "copy", "-movflags", "+faststart",
            "-f", "mp4",
            tmp,
        ]
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as e:
            self.failed.emit(str(e))
            return

        last_pct = -1
        try:
            for line in proc.stderr:
                if self._stop.is_set():
                    proc.kill()
                    break
                if not self._duration_ms:
                    dm = _DURATION_RE.search(line)
                    if dm:
                        self._duration_ms = int(
                            (int(dm.group(1)) * 3600
                             + int(dm.group(2)) * 60
                             + float(dm.group(3))) * 1000
                        )
                        if self._duration_ms > 0:
                            self.progress.emit(0)
                if not self._duration_ms:
                    continue
                m = _TIME_RE.search(line)
                if not m:
                    continue
                seconds = (int(m.group(1)) * 3600 + int(m.group(2)) * 60
                           + float(m.group(3)))
                pct = int(min(99, seconds * 1000 * 100 / self._duration_ms))
                if pct != last_pct:
                    last_pct = pct
                    self.progress.emit(pct)
        except Exception:
            pass

        try:
            proc.wait(timeout=120)
        except Exception:
            proc.kill()
            proc.wait()

        if self._stop.is_set():
            self._cleanup(tmp)
            return

        if proc.returncode != 0 and not self._stop.is_set():
            # fallback: master con un solo stream video
            cmd = [
                ffmpeg, "-y", "-loglevel", "error",
                "-i", self._url,
                "-map", "0:v:0", "-map", "0:a:0?",
                "-c", "copy", "-movflags", "+faststart",
                "-f", "mp4",
                tmp,
            ]
            self._cleanup(tmp)
            try:
                proc = subprocess.run(
                    cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                    text=True, encoding="utf-8", errors="replace",
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    timeout=180,
                )
            except Exception as e:
                self.failed.emit(str(e))
                return

        if (proc.returncode == 0 and os.path.isfile(tmp)
                and os.path.getsize(tmp) > MIN_VALID_BYTES):
            try:
                os.replace(tmp, self._out)
            except OSError as e:
                self._cleanup(tmp)
                self.failed.emit(str(e))
                return
            prune_cache()
            self.progress.emit(100)
            self.ready.emit(self._out)
        else:
            self._cleanup(tmp)
            self.failed.emit(f"ffmpeg exit {proc.returncode}")

    @staticmethod
    def _cleanup(path):
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass
