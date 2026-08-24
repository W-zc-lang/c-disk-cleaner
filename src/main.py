"""pywebview 入口 + JS API (main.py)"""
import os
import sys

import webview

from cleaner import (
    list_items, clean_items, is_admin,
    scan_large_files, delete_large_files,
    get_memory_info, quick_boost,
    get_drives, get_disk_info,
    scan_duplicate_files, delete_duplicate_files,
)


class Api:
    def scan(self):
        try:
            return {"ok": True, "items": list_items(), "admin": is_admin()}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def clean(self, ids):
        if not isinstance(ids, list):
            ids = []
        try:
            return clean_items(ids)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def is_admin(self):
        return is_admin()

    def get_drives(self):
        try:
            return {"ok": True, "drives": get_drives()}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def get_disk_info(self, drive="C:"):
        try:
            return {"ok": True, "info": get_disk_info(drive)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def scan_large_files(self, threshold_gb=1.0, drive="all"):
        try:
            files = scan_large_files(threshold_gb=threshold_gb, drive=drive)
            return {"ok": True, "files": files}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def delete_large_files(self, paths):
        if not isinstance(paths, list):
            paths = []
        try:
            return delete_large_files(paths)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def scan_duplicate_files(self, drive="all"):
        try:
            groups = scan_duplicate_files(drive=drive)
            return {"ok": True, "groups": groups}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def delete_duplicate_files(self, keep, paths):
        if not isinstance(keep, str):
            keep = ""
        if not isinstance(paths, list):
            paths = []
        try:
            return delete_duplicate_files(keep, paths)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def get_memory_info(self):
        try:
            return {"ok": True, "memory": get_memory_info()}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def quick_boost(self):
        try:
            return quick_boost()
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}


def resource_path(rel):
    base = getattr(sys, "_MEIPASS", None)
    if base is None:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, rel)


if __name__ == "__main__":
    api = Api()
    index = resource_path(os.path.join("gui", "index.html"))
    icon = resource_path(os.path.join("gui", "assets", "icon.ico"))
    kwargs = {
        "title": "系统存储空间管理 / System Storage Manager",
        "url": index,
        "js_api": api,
        "width": 960,
        "height": 760,
    }
    if os.path.exists(icon):
        kwargs["icon"] = icon
    webview.create_window(**kwargs)
    webview.start()
