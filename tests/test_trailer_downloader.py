"""Unit tests for the HLS trailer downloader helpers.

Network/ffmpeg are not exercised here: the playlists are parsed from canned
fixtures and the cache eviction runs on a temporary folder.
"""
import os
import tempfile
import time
import unittest
from unittest import mock

from network import trailer_downloader as td

MASTER = """#EXTM3U
#EXT-X-VERSION:7
#EXT-X-INDEPENDENT-SEGMENTS
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="Default",AUTOSELECT=YES,DEFAULT=YES,URI="hls_264_4_audio.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=5800000,CODECS="avc1.640029,mp4a.40.2",RESOLUTION=1920x1080,FRAME-RATE=30,AUDIO="audio"
hls_264_0_video.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2600000,CODECS="avc1.640029,mp4a.40.2",RESOLUTION=1280x720,FRAME-RATE=30,AUDIO="audio"
hls_264_1_video.m3u8
"""

MEDIA = """#EXTM3U
#EXT-X-VERSION:7
#EXT-X-TARGETDURATION:3
#EXT-X-MAP:URI="dash_h264/init-stream0.m4s"
#EXTINF:3
dash_h264/chunk-stream0-00001.m4s
#EXTINF:3
dash_h264/chunk-stream0-00002.m4s
"""


class HlsParsingTests(unittest.TestCase):
    def test_master_audio_and_variants(self):
        base = "https://cdn.example/store/123/hls_264_master.m3u8"
        parsed = td.parse_master(MASTER, base)
        self.assertEqual(
            parsed["audio"],
            "https://cdn.example/store/123/hls_264_4_audio.m3u8")
        self.assertEqual([v["label"] for v in parsed["variants"]],
                         ["1080p", "720p"])
        self.assertEqual(parsed["variants"][0]["url"],
                         "https://cdn.example/store/123/hls_264_0_video.m3u8")
        self.assertEqual(parsed["variants"][1]["height"], 720)

    def test_media_playlist_init_and_segments(self):
        base = "https://cdn.example/store/123/hls_264_0_video.m3u8"
        init, segments = td.parse_media_playlist(MEDIA, base)
        self.assertEqual(init,
                         "https://cdn.example/store/123/dash_h264/init-stream0.m4s")
        self.assertEqual(segments, [
            "https://cdn.example/store/123/dash_h264/chunk-stream0-00001.m4s",
            "https://cdn.example/store/123/dash_h264/chunk-stream0-00002.m4s",
        ])

    def test_media_playlist_rejects_byte_ranges(self):
        text = MEDIA.replace("#EXTINF:3", "#EXT-X-BYTERANGE:100@0\n#EXTINF:3")
        with self.assertRaises(td.TrailerDownloadError):
            td.parse_media_playlist(text, "https://cdn.example/x.m3u8")

    def test_pick_variant_defaults_to_1080p(self):
        parsed = td.parse_master(MASTER, "https://cdn.example/hls")
        chosen = td.pick_variant(parsed["variants"])
        self.assertEqual(chosen["label"], "1080p")

    def test_pick_variant_honours_an_explicit_label(self):
        parsed = td.parse_master(MASTER, "https://cdn.example/hls")
        chosen = td.pick_variant(parsed["variants"], "720p")
        self.assertEqual(chosen["label"], "720p")

    def test_pick_variant_falls_back_to_the_best_available(self):
        variants = [{"label": "480p", "height": 480, "bandwidth": 1},
                    {"label": "720p", "height": 720, "bandwidth": 2},
                    {"label": "2160p", "height": 2160, "bandwidth": 9}]
        self.assertEqual(td.pick_variant(variants)["label"], "2160p")
        self.assertEqual(td.pick_variant(variants, "1080p")["label"], "2160p")


class CacheEvictionTests(unittest.TestCase):
    def test_prune_removes_the_least_recently_used_files(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = []
            now = time.time()
            for index in range(4):
                path = os.path.join(folder, f"trailer-{index}.mp4")
                with open(path, "wb") as handle:
                    handle.write(b"x" * 100)
                os.utime(path, (now - 400 + index * 100,) * 2)
                paths.append(path)
            # Fake an interrupted download directory that the sweep removes.
            parts = os.path.join(folder, "broken.parts")
            os.makedirs(parts)
            os.utime(parts, (now - 7 * 3600,) * 2)

            with mock.patch.object(td, "cache_dir", return_value=folder):
                td.prune_cache(max_bytes=250)

            remaining = sorted(os.listdir(folder))
            self.assertEqual(remaining, ["trailer-2.mp4", "trailer-3.mp4"])

    def test_prune_keeps_everything_under_the_cap(self):
        with tempfile.TemporaryDirectory() as folder:
            for index in range(2):
                path = os.path.join(folder, f"trailer-{index}.mp4")
                with open(path, "wb") as handle:
                    handle.write(b"x" * 100)
            with mock.patch.object(td, "cache_dir", return_value=folder):
                td.prune_cache(max_bytes=250)
            self.assertEqual(len(os.listdir(folder)), 2)


if __name__ == "__main__":
    unittest.main()
