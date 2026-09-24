# -*- coding: utf-8 -*-
import os
import sys
import threading



# ============================================================
# 常量
# ============================================================
HOST = "127.0.0.1"


def _env_port(default=8765):
    """
    端口可用环境变量覆盖，方便和别的实例并存：
        set MAPART_PORT=8899
    """
    raw = os.environ.get("MAPART_PORT", "").strip()
    if raw:
        try:
            p = int(raw)
            if 1 <= p <= 65535:
                return p
        except ValueError:
            pass
    return default


PORT = _env_port()
TASKS = {}
TASK_LOCK = threading.Lock()
KEEP_TASKS = 15

TARGET_BLOCK_ID = "minecraft:glow_lichen"
FACE_KEYS = ["down", "up", "north", "south", "east", "west"]

# 预览分辨率上限（越大越清晰越慢）
PREVIEW_MAX_SIDE = 1024

# 同色多选方块时的随机分配种子。固定种子 = 同参数同图片永远得到同一份成品，
# 预览里的用量统计也和实际生成一致。
DEFAULT_SEED = 20240922


# ============================================================
# 资源目录
# ============================================================
# 资源根目录下固定是这个布局，源码运行和打包运行一致：
#     <root>/web/    前端（index.html / css / js）
#     <root>/data/   生成数据（blockdata.py / block_icons.png）
WEB_DIR = "web"
DATA_DIR = "data"
INDEX_FILE = "index.html"


def get_base_dir():
    """
    资源根目录：包含 web/index.html 的那个目录。

    依次尝试：
        PyInstaller 解包目录 (_MEIPASS)
        本包的所在目录           —— 源码直接运行
        可执行文件所在目录        —— 绿色版把 web/ 和 data/ 放在 exe 旁边
        当前工作目录
    都找不到就退回本包目录。
    """
    package_dir = os.path.dirname(os.path.abspath(__file__))
    for d in _root_candidates():
        if os.path.isfile(os.path.join(d, WEB_DIR, INDEX_FILE)):
            return d
    return package_dir


def _root_candidates():
    """资源根目录的候选位置（去重，保持顺序）。"""
    cands = []
    if hasattr(sys, "_MEIPASS"):
        cands.append(sys._MEIPASS)
    cands.append(os.path.dirname(os.path.abspath(__file__)))
    cands.append(os.path.dirname(os.path.abspath(sys.executable)))
    cands.append(os.getcwd())
    out = []
    seen = set()
    for d in cands:
        if d and d not in seen:
            seen.add(d)
            out.append(d)
    return out


def get_resource_path(filename, subdir=""):
    """
    在候选根目录的 <root>/、<root>/web/、<root>/data/ 里查找资源文件，
    找不到返回 None。subdir 可以指定优先子目录。
    """
    subs = [subdir] if subdir else []
    subs += [DATA_DIR, WEB_DIR, ""]
    seen = set()
    for d in _root_candidates():
        for sd in subs:
            p = os.path.join(d, sd, filename) if sd else os.path.join(d, filename)
            if p in seen:
                continue
            seen.add(p)
            if os.path.isfile(p):
                return p
    return None


def web_index_path():
    """前端首页的绝对路径（找不到返回 None）。"""
    p = os.path.join(get_base_dir(), WEB_DIR, INDEX_FILE)
    return p if os.path.isfile(p) else get_resource_path(INDEX_FILE, WEB_DIR)


