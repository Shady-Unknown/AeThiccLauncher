import os
import shutil
import sys
from pathlib import Path
from core.paths import PathManager

def move_file(src, dst):
    if os.path.exists(src):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(src, dst)
        print(f"Moved {src} to {dst}")
    else:
        print(f"Skipped {src} (not found)")

def migrate():
    # Initialize the PathManager to create directories
    PathManager.initialize()

    # Move config.json
    move_file("config.json", str(PathManager.get_config_dir() / "config.json"))

    # Move master_catalog.json
    move_file("master_catalog.json", str(PathManager.get_dbs_dir() / "master_catalog.json"))

    # Move store_cache.json
    move_file("store_cache.json", str(PathManager.get_dbs_dir() / "store_cache.json"))

    # Move contents of local_dbs
    old_local_dbs = "local_dbs"
    if os.path.exists(old_local_dbs) and os.path.isdir(old_local_dbs):
        for item in os.listdir(old_local_dbs):
            src_path = os.path.join(old_local_dbs, item)
            dst_path = str(PathManager.get_dbs_dir() / item)
            move_file(src_path, dst_path)
        # Optional: remove old local_dbs folder if empty
        try:
            os.rmdir(old_local_dbs)
            print(f"Removed old {old_local_dbs} directory.")
        except OSError:
            print(f"Could not remove {old_local_dbs} (might not be empty).")

    print("Migration completed.")

if __name__ == "__main__":
    migrate()
