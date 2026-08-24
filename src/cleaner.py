"""C 盘清理核心逻辑 (cleaner.py)
安全原则 / Safety principles:
- 仅针对白名单目录 (临时文件 / 回收站 / 浏览器缓存 / 更新缓存与 WinSxS / 应用缓存 / 用户确认的大文件 / 用户确认的重复文件)
  Only whitelisted locations are ever touched.
- 绝不自动清理 Desktop / Downloads / Documents / 用户个人目录；大文件与重复文件须经用户逐条勾选确认。
  Never auto-cleans Desktop / Downloads / Documents / personal folders; large & duplicate files need explicit per-file confirmation.
- 文件删除统一走回收站 (可恢复); 仅 WinSxS 由系统 DISM 维护 (需管理员, 不可恢复, 已明确提示)
  File deletion goes to Recycle Bin (recoverable); only WinSxS uses system DISM (admin, not recoverable, clearly warned).
"""
import os
import re
import ctypes
import subprocess
import threading
import math
import hashlib

# ---------------- Windows Shell 常量 / constants ----------------
FO_DELETE = 0x0003
FOF_ALLOWUNDO = 0x0040
FOF_NOCONFIRMATION = 0x0010
FOF_NOERRORUI = 0x0400
FOF_SILENT = 0x0004
SHERB_NOCONFIRMATION = 0x0001
SHERB_NOPROGRESSUI = 0x0002

_shell32 = ctypes.windll.shell32
_kernel32 = ctypes.windll.kernel32


class SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("wFunc", ctypes.c_uint),
        ("pFrom", ctypes.c_wchar_p),
        ("pTo", ctypes.c_wchar_p),
        ("fFlags", ctypes.c_ushort),
        ("fAnyOperationsAborted", ctypes.c_bool),
        ("lpszProgressTitle", ctypes.c_wchar_p),
    ]


class SHQUERYRBINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("i64Size", ctypes.c_longlong),
        ("i64NumItems", ctypes.c_longlong),
    ]


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


class ULARGE_INTEGER(ctypes.Structure):
    _fields_ = [("QuadPart", ctypes.c_ulonglong)]


_shell32.SHFileOperationW.argtypes = [ctypes.POINTER(SHFILEOPSTRUCTW)]
_shell32.SHFileOperationW.restype = ctypes.c_int
_shell32.SHEmptyRecycleBinW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint]
_shell32.SHEmptyRecycleBinW.restype = ctypes.c_int
_shell32.SHQueryRecycleBinW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(SHQUERYRBINFO)]
_shell32.SHQueryRecycleBinW.restype = ctypes.c_int

_kernel32.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(MEMORYSTATUSEX)]
_kernel32.GlobalMemoryStatusEx.restype = ctypes.c_bool
_kernel32.GetDiskFreeSpaceExW.argtypes = [
    ctypes.c_wchar_p,
    ctypes.POINTER(ULARGE_INTEGER),
    ctypes.POINTER(ULARGE_INTEGER),
    ctypes.POINTER(ULARGE_INTEGER),
]
_kernel32.GetDiskFreeSpaceExW.restype = ctypes.c_bool
_kernel32.GetLogicalDrives.restype = ctypes.c_uint
_kernel32.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
_kernel32.GetDriveTypeW.restype = ctypes.c_uint

DRIVE_FIXED = 3


def is_admin():
    try:
        return bool(_shell32.IsUserAnAdmin())
    except Exception:
        return False


def _local_appdata():
    return os.environ.get("LOCALAPPDATA") or os.path.expanduser(r"~\AppData\Local")


def _roaming_appdata():
    return os.environ.get("APPDATA") or os.path.expanduser(r"~\AppData\Roaming")


def _user_profile():
    return os.environ.get("USERPROFILE") or os.path.expanduser("~")


def _expand_paths(raw_paths):
    """把模板路径展开为实际存在的绝对路径列表."""
    result = []
    for p in raw_paths:
        if callable(p):
            try:
                result.extend(_expand_paths(p()))
            except Exception:
                pass
            continue
        try:
            p = os.path.expandvars(p)
            p = os.path.expanduser(p)
        except Exception:
            continue
        if os.path.exists(p):
            result.append(os.path.abspath(p))
    return result


def dir_size(path, max_depth=None, _depth=0):
    total = 0
    if max_depth is not None and _depth > max_depth:
        return 0
    try:
        for root, dirs, files in os.walk(path):
            # 跳过重解析点/符号链接，避免进入系统挂载点
            dirs[:] = [
                d for d in dirs
                if not _is_system_junction_or_symlink(os.path.join(root, d))
            ]
            for name in files:
                try:
                    fp = os.path.join(root, name)
                    if os.path.isfile(fp) or os.path.islink(fp):
                        total += os.path.getsize(fp)
                except OSError:
                    pass
    except OSError:
        pass
    return total


def send_to_recycle_bin(path):
    """删除文件/目录到回收站 (可恢复). 返回 (ok, msg)."""
    if not os.path.exists(path):
        return True, "skip"
    from_path = os.path.abspath(path) + "\0\0"
    fo = SHFILEOPSTRUCTW(
        0, FO_DELETE, from_path, None,
        FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_NOERRORUI | FOF_SILENT,
        False, None,
    )
    res = _shell32.SHFileOperationW(ctypes.byref(fo))
    # res==0 表示成功。部分无桌面会话会返回非零码(如 2)但文件已移入回收站,
    # 因此以"文件是否仍在原位置"作为最终结果判定, 避免漏报成功。
    if res == 0 or not os.path.exists(path):
        return True, "ok"
    return False, f"code {res}"


def empty_recycle_bin(drive="C:"):
    res = _shell32.SHEmptyRecycleBinW(None, drive, SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI)
    return res == 0


def recycle_bin_info(drive="C:"):
    info = SHQUERYRBINFO(ctypes.sizeof(SHQUERYRBINFO), 0, 0)
    r = _shell32.SHQueryRecycleBinW(drive, ctypes.byref(info))
    if r == 0:
        return max(0, info.i64Size), max(0, info.i64NumItems)
    return 0, 0


def _is_system_junction_or_symlink(path):
    """跳过 Windows 系统重解析点/符号链接，避免误扫 WindowsApps 等."""
    try:
        if os.path.islink(path):
            return True
        attr = ctypes.windll.kernel32.GetFileAttributesW(path)
        return attr != -1 and (attr & 0x400)
    except Exception:
        return False


# ---------------- 磁盘信息 / Drive info ----------------
def get_drives():
    """返回系统中所有固定磁盘列表，如 ['C:', 'D:']."""
    drives = []
    mask = _kernel32.GetLogicalDrives()
    for i in range(26):
        if mask & (1 << i):
            letter = chr(ord('A') + i)
            path = f"{letter}:\\"
            if _kernel32.GetDriveTypeW(path) == DRIVE_FIXED:
                drives.append(f"{letter}:")
    return drives


def _drive_space(drive):
    """返回 (total, free, used) 字节，失败返回 (0,0,0)."""
    free_avail = ULARGE_INTEGER()
    total = ULARGE_INTEGER()
    total_free = ULARGE_INTEGER()
    # arg3 = total bytes, arg4 = total free bytes
    ok = _kernel32.GetDiskFreeSpaceExW(
        drive,
        ctypes.byref(free_avail),
        ctypes.byref(total),
        ctypes.byref(total_free),
    )
    if not ok:
        return 0, 0, 0
    free = total_free.QuadPart
    used = total.QuadPart - free
    return total.QuadPart, free, used


def _folder_size_safe(path):
    """安全地统计目录大小（跳过无权限/重解析点）."""
    if not os.path.isdir(path):
        return 0
    return dir_size(path)


def _drive_user_dirs(drive):
    """获取某盘上的用户数据目录（优先 USERPROFILE，否则根下常见目录）."""
    user = _user_profile()
    if os.path.splitdrive(user)[0].upper() == drive.upper():
        base = user
    else:
        base = drive + "\\"
    names = ["Downloads", "Documents", "Videos", "Desktop", "Music", "Pictures"]
    dirs = []
    for n in names:
        p = os.path.join(base, n)
        if os.path.isdir(p):
            dirs.append(p)
    return dirs


def get_disk_info(drive="C:"):
    """返回磁盘空间与分类估算（用户/应用/系统/回收站/其他）."""
    total, free, used = _drive_space(drive)
    if total == 0:
        return {
            "drive": drive,
            "total": 0, "free": 0, "used": 0,
            "categories": [], "error": "无法读取该磁盘 / cannot read drive",
        }

    # 分类估算（仅用于 UI 展示，非精确值）
    user_dirs = _drive_user_dirs(drive)
    user_size = sum(_folder_size_safe(p) for p in user_dirs)

    app_dirs = [
        os.path.join(drive, "\\", "Program Files"),
        os.path.join(drive, "\\", "Program Files (x86)"),
        os.path.join(drive, "\\", "ProgramData"),
    ]
    app_size = sum(_folder_size_safe(p) for p in app_dirs)

    sys_dir = os.path.join(drive, "\\", "Windows")
    sys_size = _folder_size_safe(sys_dir)

    rb_size, rb_count = recycle_bin_info(drive)

    accounted = user_size + app_size + sys_size + rb_size
    # 目录估算可能因重解析点/硬链接而超过实际已用空间，归一化保证进度条不溢出
    if accounted > used and used > 0:
        ratio = used / accounted
        user_size = int(user_size * ratio)
        app_size = int(app_size * ratio)
        sys_size = int(sys_size * ratio)
        rb_size = int(rb_size * ratio)
        accounted = user_size + app_size + sys_size + rb_size
    other_size = max(0, used - accounted)

    categories = [
        {"id": "user", "name": "用户文件", "name_en": "User files", "size": user_size, "color": "#ef4444"},
        {"id": "app", "name": "应用文件", "name_en": "App files", "size": app_size, "color": "#f59e0b"},
        {"id": "system", "name": "系统文件", "name_en": "System files", "size": sys_size, "color": "#3b82f6"},
        {"id": "recycle", "name": "回收站", "name_en": "Recycle Bin", "size": rb_size, "color": "#22c55e"},
        {"id": "other", "name": "其他", "name_en": "Other", "size": other_size, "color": "#64748b"},
    ]

    return {
        "drive": drive,
        "total": total,
        "free": free,
        "used": used,
        "recycle_count": rb_count,
        "categories": categories,
    }


# ---------------- 已知应用缓存配置 / Known app cache configs ----------------
def _known_apps():
    la = _local_appdata()
    ra = _roaming_appdata()
    pf = _user_profile()
    return [
        {
            "id": "doubao",
            "name": "豆包",
            "name_en": "Doubao",
            "paths": [
                os.path.join(la, "Doubao"),
                os.path.join(la, "DoubaoPC"),
                os.path.join(ra, "Doubao"),
                os.path.join(ra, "DoubaoPC"),
            ],
        },
        {
            "id": "python",
            "name": "Python",
            "name_en": "Python",
            "paths": [
                os.path.join(la, "pip", "Cache"),
                os.path.join(la, "Programs", "Python"),
            ],
        },
        {
            "id": "youdao",
            "name": "有道词典",
            "name_en": "Youdao Dict",
            "paths": [
                os.path.join(la, "youdao", "dict"),
                os.path.join(ra, "youdao", "dict"),
            ],
        },
        {
            "id": "douyin",
            "name": "抖音",
            "name_en": "Douyin",
            "paths": [
                os.path.join(la, "douyin"),
                os.path.join(la, "TikTok"),
                os.path.join(ra, "douyin"),
                os.path.join(ra, "TikTok"),
            ],
        },
        {
            "id": "wechat",
            "name": "微信",
            "name_en": "WeChat",
            "paths": [
                os.path.join(ra, "Tencent", "WeChat", "log"),
                os.path.join(la, "Tencent", "WeChat"),
                os.path.join(ra, "Tencent", "WeChat"),
            ],
            "note": "仅清理日志与缓存，不包含聊天记录",
            "note_en": "Only logs & cache, not chat history",
        },
        {
            "id": "edge_app",
            "name": "Edge 应用数据",
            "name_en": "Edge App Data",
            "paths": [
                os.path.join(la, "Microsoft", "Edge", "User Data", "Default", "Service Worker"),
                os.path.join(la, "Microsoft", "Edge", "User Data", "Default", "blob_storage"),
                os.path.join(la, "Microsoft", "Edge", "User Data", "Default", "File System"),
            ],
        },
        {
            "id": "quark",
            "name": "夸克网盘",
            "name_en": "Quark Cloud Drive",
            "paths": [
                os.path.join(la, "QuarkCloudDrive"),
                os.path.join(la, "Quark"),
                os.path.join(ra, "QuarkCloudDrive"),
            ],
        },
        {
            "id": "qq",
            "name": "QQ",
            "name_en": "QQ",
            "paths": [
                os.path.join(la, "Tencent", "QQ", "Temp"),
                os.path.join(ra, "Tencent", "QQ"),
            ],
        },
        {
            "id": "dingtalk",
            "name": "钉钉",
            "name_en": "DingTalk",
            "paths": [
                os.path.join(la, "DingTalk"),
                os.path.join(ra, "DingTalk"),
            ],
        },
        {
            "id": "netease_music",
            "name": "网易云音乐",
            "name_en": "NetEase CloudMusic",
            "paths": [
                os.path.join(la, "Netease", "CloudMusic", "Cache"),
                os.path.join(la, "Netease", "CloudMusic", "Temp"),
            ],
        },
    ]


def _app_item(app):
    paths = [p for p in app.get("paths", []) if os.path.exists(p)]
    size = sum(dir_size(p) for p in paths)
    return {
        "id": f"apps_{app['id']}",
        "name": app["name"],
        "name_en": app.get("name_en", app["name"]),
        "size": size,
        "paths": paths,
        "note": app.get("note", "清理该应用的缓存与临时文件"),
        "note_en": app.get("note_en", "Clears this app's cache & temp files"),
    }


def list_app_cleaners():
    children = []
    for app in _known_apps():
        item = _app_item(app)
        if item["size"] > 0 or item["paths"]:
            children.append(item)
    total = sum(c["size"] for c in children)
    return {
        "id": "apps",
        "name": "其他应用清理项",
        "name_en": "Other App Cleaners",
        "size": total,
        "requires_admin": False,
        "note": f"共 {len(children)} 个应用，可逐项勾选",
        "note_en": f"{len(children)} apps, select individually",
        "children": children,
    }


def clean_apps(ids):
    lookup = {a["id"]: a for a in _known_apps()}
    results = []
    total_freed = 0
    for raw_id in ids:
        key = raw_id.split("_", 1)[1] if raw_id.startswith("apps_") else raw_id
        if key not in lookup:
            continue
        app = lookup[key]
        paths = [p for p in app.get("paths", []) if os.path.exists(p)]
        freed = 0
        errs = []
        for p in paths:
            before = dir_size(p)
            ok, msg = send_to_recycle_bin(p)
            if ok:
                freed += before
            else:
                errs.append(f"{p}: {msg}")
        results.append({
            "id": raw_id,
            "name": app["name"],
            "freed": freed,
            "errors": errs,
        })
        total_freed += freed
    return {"ok": True, "total_freed": total_freed, "results": results}


# ---------------- 大文件扫描 / Large files ----------------
def _large_file_roots(drive="C:"):
    """返回指定盘上的大文件扫描根目录."""
    drive = drive.upper().rstrip("\\")
    roots = []

    # 用户目录（若在该盘）
    user = _user_profile()
    if os.path.splitdrive(user)[0].upper() == drive:
        base = user
    else:
        base = drive + "\\"
    for name in ["Downloads", "Documents", "Videos", "Desktop", "Music", "Pictures"]:
        p = os.path.join(base, name)
        if os.path.isdir(p):
            roots.append(p)

    # 系统/公共临时目录
    if drive == os.path.splitdrive(os.environ.get("SystemRoot", "C:\\Windows"))[0].upper():
        roots.append(os.path.join(drive, "Windows", "Temp"))
        roots.append(os.path.join(drive, "ProgramData"))
        t = os.environ.get("TEMP") or os.environ.get("TMP")
        if t and os.path.isdir(t):
            roots.append(t)
    else:
        roots.append(os.path.join(drive, "ProgramData"))

    return [os.path.abspath(r) for r in roots if os.path.isdir(r)]


def scan_large_files(threshold_gb=1.0, drive="all", progress_callback=None):
    """扫描大于 threshold_gb GB 的文件. drive 可为 'all' 或 'C:' 等."""
    try:
        threshold = float(threshold_gb)
    except Exception:
        threshold = 1.0
    if threshold <= 0:
        threshold = 0.1
    threshold_bytes = int(threshold * 1024 ** 3)

    drives = get_drives() if drive == "all" else [drive.rstrip("\\")]
    roots = []
    for d in drives:
        roots.extend(_large_file_roots(d))

    found = []
    total_dirs = len(roots)

    for idx, root in enumerate(roots):
        if progress_callback:
            progress_callback(int(100 * idx / total_dirs))
        try:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [
                    d for d in dirnames
                    if not _is_system_junction_or_symlink(os.path.join(dirpath, d))
                ]
                for name in filenames:
                    try:
                        fp = os.path.join(dirpath, name)
                        if _is_system_junction_or_symlink(fp):
                            continue
                        s = os.path.getsize(fp)
                        if s >= threshold_bytes:
                            found.append({
                                "path": fp,
                                "name": name,
                                "size": s,
                            })
                    except OSError:
                        pass
        except OSError:
            pass

    if progress_callback:
        progress_callback(100)
    found.sort(key=lambda x: x["size"], reverse=True)
    return found


def delete_large_files(paths):
    """删除用户勾选的大文件，统一走回收站."""
    results = []
    total_freed = 0
    for p in paths:
        if not isinstance(p, str) or not os.path.exists(p):
            continue
        before = os.path.getsize(p)
        ok, msg = send_to_recycle_bin(p)
        if ok:
            total_freed += before
        results.append({"path": p, "size": before, "ok": ok, "msg": msg})
    return {"ok": True, "total_freed": total_freed, "results": results}


# ---------------- 重复文件扫描 / Duplicate files ----------------
def _scan_file_candidates(drive="all"):
    """扫描重复文件候选（大小>=1MB的文件），返回 {size: [path, ...]}."""
    drives = get_drives() if drive == "all" else [drive.rstrip("\\")]
    roots = []
    for d in drives:
        roots.extend(_large_file_roots(d))

    min_bytes = 1 * 1024 * 1024  # 只关注 >=1MB 的文件
    size_map = {}
    for root in roots:
        try:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [
                    d for d in dirnames
                    if not _is_system_junction_or_symlink(os.path.join(dirpath, d))
                ]
                for name in filenames:
                    try:
                        fp = os.path.join(dirpath, name)
                        if _is_system_junction_or_symlink(fp):
                            continue
                        s = os.path.getsize(fp)
                        if s < min_bytes:
                            continue
                        size_map.setdefault(s, []).append(fp)
                    except OSError:
                        pass
        except OSError:
            pass
    return size_map


def _file_hash(path, algorithm=hashlib.md5, block_size=1024 * 1024):
    """计算文件 MD5，失败返回 None."""
    try:
        h = algorithm()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(block_size)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def scan_duplicate_files(drive="all", progress_callback=None):
    """扫描重复文件，按大小+MD5分组."""
    size_map = _scan_file_candidates(drive)
    groups = []
    candidate_sizes = [s for s, files in size_map.items() if len(files) > 1]
    total = len(candidate_sizes)

    for i, size in enumerate(candidate_sizes):
        if progress_callback:
            progress_callback(int(100 * i / max(total, 1)))
        hash_map = {}
        for fp in size_map[size]:
            digest = _file_hash(fp)
            if digest:
                hash_map.setdefault(digest, []).append(fp)
        for digest, files in hash_map.items():
            if len(files) < 2:
                continue
            groups.append({
                "size": size,
                "total_size": size * len(files),
                "files": [{"path": p, "name": os.path.basename(p)} for p in files],
            })

    if progress_callback:
        progress_callback(100)
    # 按可释放空间（总大小-单份）降序
    groups.sort(key=lambda g: g["total_size"], reverse=True)
    return groups


def delete_duplicate_files(keep_path, paths):
    """保留 keep_path，删除 paths 中其余文件到回收站."""
    results = []
    total_freed = 0
    for p in paths:
        if p == keep_path:
            continue
        if not isinstance(p, str) or not os.path.exists(p):
            continue
        before = os.path.getsize(p)
        ok, msg = send_to_recycle_bin(p)
        if ok:
            total_freed += before
        results.append({"path": p, "size": before, "ok": ok, "msg": msg})
    return {"ok": True, "total_freed": total_freed, "results": results}


# ---------------- 快速加速 / Quick boost ----------------
def get_memory_info():
    """返回内存占用百分比与字节数."""
    mem = MEMORYSTATUSEX()
    mem.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if not _kernel32.GlobalMemoryStatusEx(ctypes.byref(mem)):
        return {"percent": 0, "total": 0, "available": 0, "used": 0}
    total = mem.ullTotalPhys
    avail = mem.ullAvailPhys
    used = total - avail if total else 0
    percent = int(round(100 * used / total)) if total else 0
    return {"percent": percent, "total": total, "available": avail, "used": used}


def quick_boost():
    """一键加速：清理 temp + 刷新 DNS + 空闲任务整理. 返回释放的临时文件大小与内存信息."""
    freed = 0
    details = []

    for p in temp_paths():
        before = dir_size(p)
        ok, msg = send_to_recycle_bin(p)
        if ok:
            freed += before
            details.append(f"cleaned temp: +{fmt_bytes(before)}")
        else:
            details.append(f"temp failed: {msg}")

    try:
        r = subprocess.run(
            ["ipconfig", "/flushdns"],
            capture_output=True, text=True, encoding="utf-8", errors="ignore",
            timeout=30, shell=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        details.append("DNS flushed" if r.returncode == 0 else f"DNS flush rc={r.returncode}")
    except Exception as e:
        details.append(f"DNS flush error: {e}")

    try:
        subprocess.Popen(
            "rundll32.exe advapi32.dll,ProcessIdleTasks",
            shell=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        details.append("idle tasks scheduled")
    except Exception as e:
        details.append(f"idle tasks error: {e}")

    return {"ok": True, "freed": freed, "memory": get_memory_info(), "details": details}


def fmt_bytes(bytes_):
    if bytes_ is None or bytes_ == 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    idx = int(math.log(bytes_, 1024)) if bytes_ > 0 else 0
    idx = min(idx, len(units) - 1)
    return f"{bytes_ / (1024 ** idx):.2f} {units[idx]}"


# ---------------- 原有四类清理 / Original 4 categories ----------------
def temp_paths():
    paths = []
    t = os.environ.get("TEMP") or os.environ.get("TMP")
    if t and os.path.isdir(t):
        paths.append(t)
    sys_temp = r"C:\Windows\Temp"
    if os.path.isdir(sys_temp):
        paths.append(sys_temp)
    return paths


def browser_cache_paths():
    la = _local_appdata()
    subs = ["Cache", "Code Cache", "GPUCache", "ShaderCache", "Media Cache"]
    found = []
    for brand, root in (
        ("Edge", os.path.join(la, "Microsoft", "Edge", "User Data", "Default")),
        ("Chrome", os.path.join(la, "Google", "Chrome", "User Data", "Default")),
    ):
        for sub in subs:
            p = os.path.join(root, sub)
            if os.path.isdir(p):
                found.append((f"{brand} {sub}", p))
    return found


def update_paths():
    download = r"C:\Windows\SoftwareDistribution\Download"
    return [download] if os.path.isdir(download) else []


def winsxs_cleanable_bytes():
    try:
        out = subprocess.run(
            ["dism.exe", "/Online", "/Cleanup-Image", "/AnalyzeComponentStore"],
            capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=150,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        txt = out.stdout + "\n" + out.stderr
    except Exception:
        return 0
    m = re.search(
        r"(?:可释放的空间|Space can be freed)\s*[:：]\s*([\d.]+)\s*(GB|MB|KB)",
        txt, re.IGNORECASE,
    )
    if not m:
        return 0
    val = float(m.group(1))
    unit = m.group(2).upper()
    mult = {"KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3}[unit]
    return int(val * mult)


def list_items():
    """扫描清理项, 返回前端结构. 不含任何个人文件."""
    items = []

    tp = temp_paths()
    tsize = sum(dir_size(p) for p in tp)
    items.append({
        "id": "temp",
        "name": "系统与用户临时文件",
        "name_en": "System & User Temp Files",
        "size": tsize,
        "requires_admin": False,
        "note": "清理 %TEMP% 与 C:\\Windows\\Temp",
        "note_en": "Clears %TEMP% and C:\\Windows\\Temp",
    })

    rsize, rcount = recycle_bin_info("C:")
    items.append({
        "id": "recycle",
        "name": "回收站 (C:)",
        "name_en": "Recycle Bin (C:)",
        "size": rsize,
        "requires_admin": False,
        "note": f"共 {rcount} 项, 清空后不可恢复",
        "note_en": f"{rcount} items, permanently removed after emptying",
    })

    bp = browser_cache_paths()
    bsize = sum(dir_size(p) for _, p in bp)
    items.append({
        "id": "browser",
        "name": "浏览器缓存 (Edge / Chrome)",
        "name_en": "Browser Cache (Edge / Chrome)",
        "size": bsize,
        "requires_admin": False,
        "note": "清理缓存与代码缓存, 不影响登录与书签",
        "note_en": "Clears cache & code cache; keeps logins & bookmarks",
    })

    up = update_paths()
    dsize = sum(dir_size(p) for p in up)
    wsize = winsxs_cleanable_bytes()
    items.append({
        "id": "update",
        "name": "更新缓存与 WinSxS",
        "name_en": "Update Cache & WinSxS",
        "size": dsize + wsize,
        "requires_admin": not is_admin(),
        "note": "SoftwareDistribution\\Download + WinSxS (DISM, 需管理员)",
        "note_en": "SoftwareDistribution\\Download + WinSxS (DISM, needs admin)",
    })

    items.append(list_app_cleaners())

    return items


def clean_items(ids):
    """逐项清理. 返回汇总. 删除统一走回收站; WinSxS 走 DISM."""
    results = []
    total_freed = 0

    app_ids = [cid for cid in ids if cid.startswith("apps_")]
    standard_ids = [cid for cid in ids if not cid.startswith("apps_")]

    if app_ids:
        app_res = clean_apps(app_ids)
        results.extend(app_res.get("results", []))
        total_freed += app_res.get("total_freed", 0)

    for cid in standard_ids:
        if cid == "temp":
            freed = 0
            errs = []
            for p in temp_paths():
                before = dir_size(p)
                ok, msg = send_to_recycle_bin(p)
                if ok:
                    freed += before
                else:
                    errs.append(f"{p}: {msg}")
            results.append({"id": cid, "freed": freed, "errors": errs})
            total_freed += freed

        elif cid == "recycle":
            before_size, _ = recycle_bin_info("C:")
            ok = empty_recycle_bin("C:")
            freed = before_size if ok else 0
            total_freed += freed
            results.append({
                "id": cid, "freed": freed, "emptied": ok,
                "errors": [] if ok else ["清空回收站失败 / failed"],
            })

        elif cid == "browser":
            freed = 0
            errs = []
            for name, p in browser_cache_paths():
                before = dir_size(p)
                ok, msg = send_to_recycle_bin(p)
                if ok:
                    freed += before
                else:
                    errs.append(f"{name}: {msg}")
            results.append({"id": cid, "freed": freed, "errors": errs})
            total_freed += freed

        elif cid == "update":
            freed = 0
            errs = []
            for p in update_paths():
                before = dir_size(p)
                ok, msg = send_to_recycle_bin(p)
                if ok:
                    freed += before
                else:
                    errs.append(f"{p}: {msg}")
            total_freed += freed
            if is_admin():
                try:
                    r = subprocess.run(
                        ["dism.exe", "/Online", "/Cleanup-Image", "/StartComponentCleanup"],
                        capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=300,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    if r.returncode != 0:
                        errs.append("DISM WinSxS 清理返回 " + str(r.returncode))
                except Exception as e:
                    errs.append("DISM: " + str(e))
            else:
                errs.append("未以管理员运行, 跳过 WinSxS 清理 / not admin, skipped WinSxS")
            results.append({"id": cid, "freed": freed, "errors": errs})

    return {"ok": True, "total_freed": total_freed, "results": results}


# ---------------- 进程管理 / Process management ----------------
_PROTECTED_PROCESSES = {
    "system", "registry", "smss.exe", "csrss.exe", "wininit.exe",
    "services.exe", "lsass.exe", "svchost.exe", "fontdrvhost.exe",
    "dwm.exe", "winlogon.exe", "taskeng.exe", "searchindexer.exe",
    "sihost.exe", "ctfmon.exe",
}

def list_processes():
    """列出进程 (PID, name, memory). 不用 psutil, 用 tasklist CSV."""
    procs = []
    try:
        out = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        lines = [l.strip() for l in out.stdout.splitlines() if l.strip()]
        for line in lines:
            # "Image Name","PID","Session Name","Session#","Mem Usage"
            parts = [p.strip('"').strip() for p in line.split('","')]
            if len(parts) < 5:
                continue
            name, pid_s, _, _, mem_s = parts[0], parts[1], parts[2], parts[3], parts[4]
            try:
                pid = int(pid_s)
            except ValueError:
                continue
            # parse "1,234 K" -> bytes
            mem = 0
            try:
                mem_s = mem_s.replace(",", "").upper().replace("K", "").strip()
                mem = int(float(mem_s) * 1024)
            except ValueError:
                pass
            if pid == 0 or name.lower() in _PROTECTED_PROCESSES:
                continue
            procs.append({
                "pid": pid,
                "name": name,
                "memory": mem,
            })
    except Exception as e:
        return {"ok": False, "error": str(e)}
    procs.sort(key=lambda x: x["memory"], reverse=True)
    return {"ok": True, "processes": procs, "count": len(procs)}


def kill_process(pid):
    """结束指定 PID 的进程."""
    try:
        pid = int(pid)
    except (ValueError, TypeError):
        return {"ok": False, "error": "invalid pid"}
    if pid <= 0:
        return {"ok": False, "error": "invalid pid"}
    try:
        r = subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"],
            capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if r.returncode == 0:
            return {"ok": True, "pid": pid}
        return {"ok": False, "pid": pid, "error": (r.stderr or r.stdout or "taskkill failed").strip()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------- 开机管理 / Startup items ----------------
import winreg

_STARTUP_KEYS = [
    (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run"),
    (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run"),
    (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run"),
]

# 常见启动项名称 -> (显示名, 图标 emoji, 预估启动耗时秒数)
_STARTUP_KNOWN = {
    "boot camp manager": ("Boot Camp Manager", "💻", 1.0),
    "ollama app": ("Ollama", "🦙", 0.6),
    "ace client tray": ("ACE Client Tray", "🛡️", 0.5),
    "doubao launcher": ("豆包", "🤖", 0.8),
    "doubao": ("豆包", "🤖", 0.8),
    "douyin": ("抖音", "🎵", 1.0),
    "weixin": ("微信", "💬", 1.2),
    "qq": ("QQ", "🐧", 2.5),
    "qqnt": ("QQ", "🐧", 2.5),
    "wxwork": ("企业微信", "💼", 1.5),
    "weixin": ("微信", "💬", 1.2),
    "cloudmusic": ("网易云音乐", "🎵", 1.5),
    "sodamusic": ("网易云音乐", "🎵", 1.5),
    "uc": ("UC", "🌐", 1.0),
    "microsoftedgeautolaunch": ("Edge 自动启动", "🌐", 0.5),
    "qqpctray": ("QQ 电脑管家", "🛡️", 1.5),
    "java update scheduler": ("Java Update Scheduler", "☕", 0.8),
    "gaijin.net updater": ("Gaijin Updater", "🎮", 1.0),
    "steam": ("Steam", "🎮", 2.0),
    "epicgameslauncher": ("Epic Games", "🎮", 2.0),
    "onedrive": ("OneDrive", "☁️", 1.0),
    "dropbox": ("Dropbox", "📦", 1.0),
    "everything": ("Everything", "🔍", 0.3),
    "powertoys": ("PowerToys", "⚡", 1.0),
    "clash": ("Clash", "🌐", 0.5),
    "v2rayn": ("v2rayN", "🌐", 0.5),
    "watt": ("Watt Toolkit", "🔧", 0.8),
}

def _startup_meta(name):
    """返回 (显示名, 图标, 预估耗时秒) ; 未知返回 (首字母大写名, 占位图标, None)."""
    key = name.lower().replace("_disabled", "").strip()
    if key in _STARTUP_KNOWN:
        return _STARTUP_KNOWN[key]
    return (key.replace("_", " ").title(), "🔲", None)


def _impact_level(sec):
    if sec is None:
        return "中"
    if sec >= 2.0:
        return "高"
    if sec >= 1.0:
        return "中"
    return "低"


def _resolve_impact(target_path, name):
    """返回 (预估耗时秒, 影响等级). 已知应用用内置估值, 未知按目标 exe 体积估算."""
    disp, icon, sec = _startup_meta(name)
    if sec is not None:
        return sec, _impact_level(sec)
    if target_path:
        try:
            sz = os.path.getsize(target_path)
        except OSError:
            sz = 0
        if sz >= 150 * 1024 * 1024:
            return 3.0, "高"
        if sz >= 50 * 1024 * 1024:
            return 2.0, "中"
        if sz >= 5 * 1024 * 1024:
            return 1.0, "中"
        return 0.3, "低"
    return 1.0, "中"


def _lnk_target(path):
    """读取 .lnk 目标路径; 非 lnk 或失败直接返回原路径."""
    if not path or not path.lower().endswith(".lnk"):
        return path
    ps = (
        "try { "
        "$ws = New-Object -ComObject WScript.Shell; "
        "$s = $ws.CreateShortcut('" + path.replace("'", "''") + "'); "
        "Write-Output $s.TargetPath "
        "} catch { Write-Output '' }"
    )
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        t = (r.stdout or "").strip()
        return t if t and os.path.exists(t) else path
    except Exception:
        return path


def _startup_folders():
    folders = []
    au = os.environ.get("APPDATA")
    if au:
        folders.append((os.path.join(au, "Microsoft", "Windows", "Start Menu", "Programs", "Startup"), "用户"))
    pd = os.environ.get("ProgramData")
    if pd:
        folders.append((os.path.join(pd, "Microsoft", "Windows", "Start Menu", "Programs", "Startup"), "所有用户"))
    return folders


def _reg_exe(value):
    """从注册表命令行提取可执行路径."""
    val = (value or "").strip()
    if not val:
        return ""
    if val.startswith('"'):
        return val.split('"')[1] if '"' in val[1:] else ""
    return val.split()[0] if val.split() else ""


def list_startup_items():
    """聚合三类真实开机启动源: 注册表 Run 键、开始菜单启动文件夹、任务计划程序(登录触发)."""
    items = []
    seen = set()

    # 1) 注册表 Run 键
    for hkey, path in _STARTUP_KEYS:
        try:
            with winreg.OpenKey(hkey, path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
                i = 0
                while True:
                    try:
                        name, value, _ = winreg.EnumValue(key, i)
                        i += 1
                    except OSError:
                        break
                    origin = "HKLM" if hkey == winreg.HKEY_LOCAL_MACHINE else "HKCU"
                    is_64 = "x64" if "WOW6432Node" not in path else "x86"
                    enabled = not name.endswith("_disabled")
                    raw = name.replace("_disabled", "")
                    key_id = f"reg_{origin}_{is_64}_{raw}"
                    if key_id in seen:
                        continue
                    seen.add(key_id)
                    disp, icon, _ = _startup_meta(raw)
                    target = _reg_exe(str(value))
                    sec, lvl = _resolve_impact(target, raw)
                    items.append({
                        "id": key_id,
                        "type": "registry",
                        "name": raw,
                        "display_name": disp,
                        "icon": icon,
                        "enabled": enabled,
                        "impact": sec,
                        "impact_level": lvl,
                        "command": str(value),
                        "location": f"注册表 {origin} ({is_64})",
                        "hkey": origin,
                        "path": path,
                        "value_name": name,
                        "needs_admin": hkey == winreg.HKEY_LOCAL_MACHINE,
                    })
        except OSError:
            continue

    # 2) 开始菜单启动文件夹 (含 Disabled 子文件夹中的已禁用项)
    for folder, scope in _startup_folders():
        if not os.path.isdir(folder):
            continue
        for fn in os.listdir(folder):
            fp = os.path.join(folder, fn)
            if not os.path.isfile(fp):
                continue
            if not (fn.lower().endswith((".lnk", ".exe", ".url"))):
                continue
            key_id = f"folder_{scope}_{fn}"
            if key_id in seen:
                continue
            seen.add(key_id)
            disp = os.path.splitext(fn)[0]
            target = _lnk_target(fp)
            sec, lvl = _resolve_impact(target, disp)
            items.append({
                "id": key_id,
                "type": "folder",
                "name": fn,
                "display_name": disp,
                "icon": "📁",
                "enabled": True,
                "impact": sec,
                "impact_level": lvl,
                "command": target,
                "location": f"启动文件夹 ({scope})",
                "file_path": fp,
                "folder": folder,
                "needs_admin": scope == "所有用户",
            })
        # 已禁用的 (在 Disabled 子文件夹)
        disabled_dir = os.path.join(folder, "Disabled")
        if os.path.isdir(disabled_dir):
            for fn in os.listdir(disabled_dir):
                fp = os.path.join(disabled_dir, fn)
                if not os.path.isfile(fp):
                    continue
                if not fn.lower().endswith((".lnk", ".exe", ".url")):
                    continue
                key_id = f"folder_{scope}_{fn}"
                if key_id in seen:
                    continue
                seen.add(key_id)
                disp = os.path.splitext(fn)[0]
                target = _lnk_target(fp)
                sec, lvl = _resolve_impact(target, disp)
                items.append({
                    "id": key_id,
                    "type": "folder",
                    "name": fn,
                    "display_name": disp,
                    "icon": "📁",
                    "enabled": False,
                    "impact": sec,
                    "impact_level": lvl,
                    "command": target,
                    "location": f"启动文件夹 ({scope}) · 已禁用",
                    "file_path": fp,
                    "folder": folder,
                    "needs_admin": scope == "所有用户",
                })

    # 3) 任务计划程序 (登录触发的任务)
    try:
        ps = (
            "Get-ScheduledTask | Where-Object { "
            "($_.Triggers | ForEach-Object { $_.CimClass.CimClassName }) -contains 'LogonTrigger' "
            "} | ForEach-Object { "
            "[PSCustomObject]@{Name=$_.TaskName; State=$_.State.ToString(); "
            "Exe=(($_.Actions | Select-Object -First 1).Execute)} "
            "} | ConvertTo-Json -Compress"
        )
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=40,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        txt = (r.stdout or "").strip()
        if txt:
            import json
            data = json.loads(txt) if txt.startswith("[") else [json.loads(txt)]
            for t in data:
                tn = (t.get("Name") or "").strip()
                if not tn:
                    continue
                key_id = f"task_{tn}"
                if key_id in seen:
                    continue
                seen.add(key_id)
                enabled = (t.get("State") != "Disabled")
                exe = (t.get("Exe") or "").strip().strip('"')
                disp = tn.split("\\")[-1]
                sec, lvl = _resolve_impact(exe, disp)
                items.append({
                    "id": key_id,
                    "type": "task",
                    "name": tn,
                    "display_name": disp,
                    "icon": "⏰",
                    "enabled": enabled,
                    "impact": sec,
                    "impact_level": lvl,
                    "command": exe,
                    "location": "任务计划程序 (登录)",
                    "task_name": tn,
                    "needs_admin": tn.lower().startswith("microsoft\\windows\\"),
                })
    except Exception:
        pass

    items.sort(key=lambda x: (not x["enabled"], x["display_name"].lower()))
    return {"ok": True, "items": items, "count": len(items)}


def set_startup_state(item, enabled):
    """按类型启用/禁用启动项. item 为 list_startup_items 返回的字典."""
    typ = item.get("type")
    if typ == "registry":
        return set_startup_enabled(item["hkey"], item["path"], item["value_name"], enabled)
    if typ == "folder":
        return _set_folder_startup(item, enabled)
    if typ == "task":
        return _set_task_startup(item["task_name"], enabled)
    return {"ok": False, "error": "unknown type"}


def _set_folder_startup(item, enabled):
    fp = item["file_path"]
    folder = item["folder"]
    disabled_dir = os.path.join(folder, "Disabled")
    try:
        if enabled:
            src = os.path.join(disabled_dir, os.path.basename(fp))
            if not os.path.exists(src):
                return {"ok": False, "error": "未找到已禁用的快捷方式 / disabled shortcut missing"}
            os.makedirs(folder, exist_ok=True)
            os.rename(src, fp)
            return {"ok": True, "enabled": True}
        os.makedirs(disabled_dir, exist_ok=True)
        dst = os.path.join(disabled_dir, os.path.basename(fp))
        os.rename(fp, dst)
        return {"ok": True, "enabled": False}
    except PermissionError:
        return {"ok": False, "error": "权限不足，需要管理员运行 / need admin"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _set_task_startup(task_name, enabled):
    verb = "Enable-ScheduledTask" if enabled else "Disable-ScheduledTask"
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             f"{verb} -TaskName '{task_name.replace(chr(39), chr(39) * 2)}'"],
            capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if r.returncode == 0:
            return {"ok": True, "enabled": enabled}
        return {"ok": False, "error": (r.stderr or r.stdout or "failed").strip()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def set_startup_enabled(hkey_str, path, value_name, enabled):
    """启用/禁用注册表 Run 项. 通过改名 value_name 实现 (加/去 _disabled 后缀)."""
    try:
        hkey = winreg.HKEY_LOCAL_MACHINE if hkey_str == "HKLM" else winreg.HKEY_CURRENT_USER
        access = winreg.KEY_READ | winreg.KEY_WRITE | winreg.KEY_WOW64_64KEY
        with winreg.OpenKey(hkey, path, 0, access) as key:
            try:
                value, typ = winreg.QueryValueEx(key, value_name)
            except OSError:
                return {"ok": False, "error": "item not found"}
            # 改名
            base = value_name.replace("_disabled", "")
            new_name = base if enabled else base + "_disabled"
            if new_name == value_name:
                return {"ok": True}
            winreg.SetValueEx(key, new_name, 0, typ, value)
            winreg.DeleteValue(key, value_name)
        return {"ok": True, "enabled": enabled, "new_name": new_name}
    except PermissionError:
        return {"ok": False, "error": "permission denied (need admin to edit HKLM)"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------- 全面体检 / System check ----------------
def system_check():
    """综合体检：磁盘空间、临时文件、回收站、内存、启动项、进程、大文件、重复文件; 给出 0-100 评分."""
    issues = []
    total = 0
    def add(name, name_en, weight, bad, tip, tip_en):
        nonlocal total
        total += weight
        issues.append({
            "name": name, "name_en": name_en,
            "weight": weight, "bad": bad,
            "tip": tip, "tip_en": tip_en,
        })

    info = get_disk_info("C:")
    used_pct = info["used"] / info["total"] if info["total"] else 0
    add(
        "C 盘空间", "C: drive space",
        20,
        used_pct > 0.85,
        "C 盘已用超过 85%，建议清理" if used_pct > 0.85 else "C 盘空间正常",
        "C: drive usage over 85%, consider cleanup" if used_pct > 0.85 else "C: drive space OK",
    )

    try:
        ti = list_items()
        cleanable = sum(it.get("size", 0) for it in _flatten_items(ti))
        add(
            "可清理垃圾", "Cleanable junk",
            20,
            cleanable > 2 * 1024**3,
            f"可清理 {fmt_bytes(cleanable)} 垃圾" if cleanable else "暂无垃圾",
            f"{fmt_bytes(cleanable)} cleanable" if cleanable else "No junk found",
        )
    except Exception:
        add("可清理垃圾", "Cleanable junk", 20, False, "扫描失败", "Scan failed")

    try:
        mem = get_memory_info()
        add(
            "内存占用", "Memory usage",
            15,
            mem["percent"] > 85,
            f"内存占用 {mem['percent']}%" if mem["percent"] > 60 else "内存占用正常",
            f"Memory usage {mem['percent']}%" if mem["percent"] > 60 else "Memory usage OK",
        )
    except Exception:
        add("内存占用", "Memory usage", 15, False, "获取失败", "Read failed")

    try:
        su = list_startup_items()
        boot_count = su.get("count", 0)
        add(
            "开机启动项", "Startup items",
            10,
            boot_count > 20,
            f"有 {boot_count} 个开机启动项" if boot_count else "无启动项",
            f"{boot_count} startup items" if boot_count else "No startup items",
        )
    except Exception:
        add("开机启动项", "Startup items", 10, False, "获取失败", "Read failed")

    try:
        procs = list_processes()
        proc_count = procs.get("count", 0)
        add(
            "运行进程", "Running processes",
            10,
            proc_count > 400,
            f"有 {proc_count} 个进程运行中" if proc_count else "无进程信息",
            f"{proc_count} processes running" if proc_count else "No process info",
        )
    except Exception:
        add("运行进程", "Running processes", 10, False, "获取失败", "Read failed")

    try:
        rb_size, rb_count = recycle_bin_info("C:")
        add(
            "回收站", "Recycle Bin",
            15,
            rb_size > 1024**3 or rb_count > 100,
            f"回收站有 {fmt_bytes(rb_size)} ({rb_count} 项)" if rb_count else "回收站为空",
            f"Recycle Bin {fmt_bytes(rb_size)} ({rb_count} items)" if rb_count else "Recycle Bin empty",
        )
    except Exception:
        add("回收站", "Recycle Bin", 15, False, "获取失败", "Read failed")

    # 扣分制
    bad_score = sum(it["weight"] for it in issues if it["bad"])
    score = max(0, 100 - bad_score)
    return {"ok": True, "score": score, "issues": issues}


def _flatten_items(items):
    out = []
    for it in items:
        out.append(it)
        out.extend(it.get("children", []))
    return out


# ---------------- 应用管理 / Installed apps & uninstall ----------------
_UNINSTALL_KEYS = [
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
]


def _parse_uninstall_command(cmd):
    """拆分卸载命令为可安全传给 subprocess 的列表; 对 MsiExec 自动加 /qn."""
    if not cmd:
        return None
    cmd = cmd.strip()
    if not cmd:
        return None
    lower = cmd.lower()
    if lower.startswith("msiexec"):
        # msiexec /I{GUID} 或 /X{GUID}
        parts = cmd.replace("/i", "/X").split()
        parts = [p for p in parts if p.lower() not in ("/i", "/x", "/uninstall")]
        return ["msiexec.exe", "/X", parts[0] if parts else cmd, "/qn", "/norestart"]
    # 普通 exe 路径，带空格需加引号；这里简单按第一个引号块拆分
    if cmd.startswith('"'):
        end = cmd.find('"', 1)
        if end > 0:
            exe = cmd[1:end]
            rest = cmd[end + 1:].strip()
            out = [exe]
            if rest:
                out.extend(rest.split())
            return out
    parts = cmd.split()
    return parts if parts else None


def list_installed_apps():
    """读取注册表 Uninstall 项，返回已安装应用列表."""
    apps = []
    seen = set()
    for hkey, path in _UNINSTALL_KEYS:
        try:
            with winreg.OpenKey(hkey, path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
                i = 0
                while True:
                    try:
                        sub_name = winreg.EnumKey(key, i)
                        i += 1
                        with winreg.OpenKey(key, sub_name, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as sub:
                            try:
                                display_name, _ = winreg.QueryValueEx(sub, "DisplayName")
                            except OSError:
                                continue
                            try:
                                publisher, _ = winreg.QueryValueEx(sub, "Publisher")
                            except OSError:
                                publisher = ""
                            try:
                                install_date, _ = winreg.QueryValueEx(sub, "InstallDate")
                            except OSError:
                                install_date = ""
                            try:
                                version, _ = winreg.QueryValueEx(sub, "DisplayVersion")
                            except OSError:
                                version = ""
                            try:
                                uninstall_cmd, _ = winreg.QueryValueEx(sub, "UninstallString")
                            except OSError:
                                uninstall_cmd = ""
                            try:
                                quiet_cmd, _ = winreg.QueryValueEx(sub, "QuietUninstallString")
                            except OSError:
                                quiet_cmd = ""
                            try:
                                icon_path, _ = winreg.QueryValueEx(sub, "DisplayIcon")
                            except OSError:
                                icon_path = ""
                            try:
                                estimate_size, _ = winreg.QueryValueEx(sub, "EstimatedSize")
                                size = int(estimate_size) * 1024
                            except (OSError, ValueError):
                                size = 0

                        app_id = f"{hkey}_{path}_{sub_name}"
                        if app_id in seen:
                            continue
                        seen.add(app_id)

                        cmd = quiet_cmd or uninstall_cmd
                        parsed = _parse_uninstall_command(cmd)
                        apps.append({
                            "id": app_id,
                            "key_name": sub_name,
                            "name": str(display_name).strip(),
                            "publisher": str(publisher).strip(),
                            "install_date": str(install_date).strip(),
                            "version": str(version).strip(),
                            "size": size,
                            "uninstall_string": str(uninstall_cmd).strip(),
                            "quiet_string": str(quiet_cmd).strip(),
                            "icon": str(icon_path).strip(),
                            "command": parsed,
                            "hkey": "HKLM" if hkey == winreg.HKEY_LOCAL_MACHINE else "HKCU",
                            "path": path,
                        })
                    except OSError:
                        break
        except OSError:
            continue
    apps.sort(key=lambda a: a["name"].lower())
    return {"ok": True, "apps": apps, "count": len(apps)}


def uninstall_app(app_id):
    """调用应用卸载命令; 成功后（或命令已提交）返回结果."""
    r = list_installed_apps()
    if not r.get("ok"):
        return r
    app = next((a for a in r["apps"] if a["id"] == app_id), None)
    if not app:
        return {"ok": False, "error": "应用未找到 / App not found"}
    cmd = app["command"]
    if not cmd:
        return {"ok": False, "error": "没有可用的卸载命令 / No uninstall command"}
    try:
        cp = subprocess.run(
            cmd,
            capture_output=True, text=True, encoding="utf-8", errors="ignore",
            timeout=120, shell=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        # msiexec 返回 0 或 3010(需要重启) 都算成功
        success = cp.returncode in (0, 3010)
        return {
            "ok": success,
            "returncode": cp.returncode,
            "stdout": cp.stdout[:500],
            "stderr": cp.stderr[:500],
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}
