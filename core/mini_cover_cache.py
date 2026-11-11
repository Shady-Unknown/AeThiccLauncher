import os
from PIL import Image
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QPixmap

from core.paths import PathManager
from core.cover_cache import to_file_url
from managers.save_manager import safe_game_folder_name

MINI_W = 200
MINI_H = 300
COVER_FILENAME = "mini_cover.jpg"
STEAM_ASSETS_DIR = "Steam_Assets"
COVER_CANDIDATES = ("Cover.jpg", "Cover.jpeg", "Cover.png")


def _valid_cover(path):
    try:
        return os.path.isfile(path) and os.path.getsize(path) > 1024
    except OSError:
        return False


class MiniCoverManager:

    @staticmethod
    def get_local_cache_dir():
        d = PathManager.get_app_data_dir() / "minicovers_cache"
        os.makedirs(str(d), exist_ok=True)
        return str(d)

    @staticmethod
    def local_cache_path(game_title):
        return os.path.join(
            MiniCoverManager.get_local_cache_dir(),
            safe_game_folder_name(game_title) + ".jpg",
        )

    @staticmethod
    def local_cache_url(game_title):
        path = MiniCoverManager.local_cache_path(game_title)
        return to_file_url(path) if os.path.isfile(path) else ""

    @staticmethod
    def _local_path(path_or_url):
        if not path_or_url:
            return ""
        raw = str(path_or_url)
        if raw.startswith("file://"):
            return QUrl(raw).toLocalFile()
        return raw

    @staticmethod
    def is_vertical_cover(path_or_url):
        """True when the image is box-art shaped (portrait).

        Uses Pillow so it is safe to call from CoverWorker threads; QPixmap
        must stay on the GUI thread.
        """
        local = MiniCoverManager._local_path(path_or_url)
        if not local or not os.path.isfile(local):
            return False
        try:
            with Image.open(local) as img:
                width, height = img.size
            return height > width
        except Exception:
            return False

    @staticmethod
    def persist_pixmap(game_title, pixmap, sync_folder=""):
        """Write a vertical cover into the mini cache (and sync folder).

        Used when a detail sheet resolves a better IGDB/SGDB cover than the
        horizontal SteamRIP featured image cached by the initial fallback.
        """
        if not game_title or pixmap is None or pixmap.isNull():
            return None
        scaled = pixmap.scaled(
            MINI_W, MINI_H,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        local_path = MiniCoverManager.local_cache_path(game_title)
        try:
            scaled.save(local_path, "JPEG", 85)
        except Exception as e:
            print("[MiniCover] Failed to persist pixmap: {}".format(e))
            return None
        if sync_folder:
            try:
                sync_game_dir = os.path.join(
                    sync_folder, safe_game_folder_name(game_title))
                os.makedirs(sync_game_dir, exist_ok=True)
                scaled.save(
                    os.path.join(sync_game_dir, COVER_FILENAME),
                    "JPEG", 85,
                )
            except Exception as e:
                print("[MiniCover] Failed to save sync pixmap: {}".format(e))
        return to_file_url(local_path)

    @staticmethod
    def persist_cover(game_title, installed_games, sync_folder):
        if not game_title or not sync_folder:
            return False

        safe_title = safe_game_folder_name(game_title)
        source_path = MiniCoverManager._locate_source_cover(game_title, installed_games)
        if not source_path:
            return False

        px = QPixmap(source_path)
        if px.isNull():
            return False

        scaled = px.scaled(
            MINI_W, MINI_H,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

        success = False

        local_dir = MiniCoverManager.get_local_cache_dir()
        local_path = os.path.join(local_dir, safe_title + ".jpg")
        try:
            scaled.save(local_path, "JPEG", 85)
            success = True
        except Exception as e:
            print("[MiniCover] Failed to save local cache: {}".format(e))

        sync_game_dir = os.path.join(sync_folder, safe_title)
        os.makedirs(sync_game_dir, exist_ok=True)
        sync_path = os.path.join(sync_game_dir, COVER_FILENAME)
        try:
            scaled.save(sync_path, "JPEG", 85)
            success = True
        except Exception as e:
            print("[MiniCover] Failed to save sync folder: {}".format(e))

        return success

    @staticmethod
    def resolve_cover_path(game_title, installed_games, sync_folder):
        if not game_title:
            return None

        # 1. Installed game — Steam_Assets
        if installed_games:
            meta = installed_games.get(game_title)
            if meta and meta.get("install_dir"):
                assets_dir = os.path.join(meta["install_dir"], STEAM_ASSETS_DIR)
                for fname in COVER_CANDIDATES:
                    path = os.path.join(assets_dir, fname)
                    if _valid_cover(path):
                        return to_file_url(path)

        safe_title = safe_game_folder_name(game_title)

        # 2. Local mini cache (ignora file vuoti/corrotti)
        local_path = os.path.join(MiniCoverManager.get_local_cache_dir(), safe_title + ".jpg")
        if _valid_cover(local_path):
            return to_file_url(local_path)

        # 3. Sync folder portable fallback
        if sync_folder:
            sync_path = os.path.join(sync_folder, safe_title, COVER_FILENAME)
            if _valid_cover(sync_path):
                return to_file_url(sync_path)

        return None

    @staticmethod
    def _locate_source_cover(game_title, installed_games):
        if not installed_games:
            return None
        meta = installed_games.get(game_title)
        if not meta or not meta.get("install_dir"):
            return None
        assets_dir = os.path.join(meta["install_dir"], STEAM_ASSETS_DIR)
        for fname in COVER_CANDIDATES:
            path = os.path.join(assets_dir, fname)
            if os.path.isfile(path):
                return path
        return None
