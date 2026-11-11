"""Download/remux di un trailer Steam HLS in un MP4 locale.

L'HLS di Steam (fMP4) non e' seekabile in modo affidabile con il backend
FFmpeg di QtMultimedia: il player imposta la posizione ma non riprende.
Scaricando una volta il trailer in MP4 locale (ffmpeg, stream copy) il seek
diventa istantaneo e il file resta in cache.
"""
import os
import re
import shutil
import subprocess
import sys
import threading

from PyQt6.QtCore import QThread, pyqtSignal

from core.config import get_base_dir
from core.paths import PathManager

CACHE_DIRNAME = "trailers"
MAX_CACHE_BYTES = 800 * 1024 * 1024   # ~800 MB
MIN_VALID_BYTES = 60 * 1024


def _bundled_ffmpeg():
    """ffmpeg.exe accanto all'app; se c'e' solo lo zip lo estrae una volta.

    GitHub rifiuta file singoli > 100 MB e l'eseguibile statico ne pesa ~100,
    quindi lo distribuiamo compresso in bin/ffmpeg.zip.
    """
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
    try:
        files = []
        for name in os.listdir(cache_dir()):
            if not name.lower().endswith(".mp4"):
                continue
            path = os.path.join(cache_dir(), name)
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

    def __init__(self, url, key, duration_ms=0, parent=None):
        super().__init__(parent)
        self._url = url
        self._out = cached_path(key)
        self._duration_ms = int(duration_ms or 0)
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            self.failed.emit("ffmpeg non disponibile")
            return
        if not self._url or not self._out:
            self.failed.emit("url trailer mancante")
            return
        if os.path.isfile(self._out) and os.path.getsize(self._out) > MIN_VALID_BYTES:
            self.ready.emit(self._out)
            return

        tmp = (self._out[:-4] + ".part.mp4") if self._out.lower().endswith(".mp4") else (self._out + ".part.mp4")
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass

        # 720p quando c'e' (secondo stream video del master), altrimenti il primo
        cmd = [
            ffmpeg, "-y", "-loglevel", "info",
            "-i", self._url,
            "-map", "0:v:1", "-map", "0:a:0?",
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
                # ffmpeg prints the media duration before downloading. The
                # caller does not know it upfront, so use it to turn the
                # progress line determinate.
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
