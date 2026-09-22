# -*- coding: utf-8 -*-
"""
地图画工具箱 - 浏览器版后端
=============================
整合三个工具 + 实时预览：
  1. 图片转 2D 地图画（.litematic）—— 支持调参实时预览、点击放大
  2. 投影切分（.litematic → zip）
  3. Glow Lichen 面属性批量修改

地图画默认：XZ 地面朝向 · 厚度 1 · 无底板

调色板：
    来自 minecraft_blocks_mapcolor.json（工作区原始数据：颜色 + 中文方块名）。
    方块 ID / 中文名由 Minecraft 1.21.4 官方语言文件 zh_cn.json 解析，
    图标取自官方方块贴图并拼成 block_icons.png。
    上面这些由 gen_blockdata.py 生成到 blockdata.py，服务端按「颜色值」分组，
    前端在侧边栏按颜色挑选方块（单选 / 多选 / 全不选）。
    勾选的方块颜色去重后即为匹配调色板；同色多选时成品在它们之间随机分配。

依赖：
    pip install fastapi uvicorn litemapy pillow numpy python-multipart

    ⚠ numpy 必须 >= 2.1（Python 3.13 / 3.14 尤其重要）。
      numpy 1.26.x 官方并不支持 Python 3.13+，网上流传的 MINGW-W64 构建
      在 import numpy 时就会 ACCESS_VIOLATION (0xC0000005) 直接崩溃，
      表现为脚本连启动都进不去。检查与修复：
          python -c "import numpy; print(numpy.__version__, numpy.__file__)"
          python -m pip install -U "numpy>=2.1"

打包：
    pyinstaller --noconfirm --clean --onefile --windowed ^
        --name "地图画工具箱" ^
        --add-data "index.html;." ^
        --add-data "block_icons.png;." ^
        --hidden-import blockdata ^
        --hidden-import litemapy --hidden-import nbtlib --hidden-import numpy ^
        --hidden-import anyio --hidden-import sniffio --hidden-import h11 ^
        --hidden-import click --hidden-import multipart --hidden-import python_multipart ^
        --collect-all fastapi --collect-all uvicorn --collect-all starlette --collect-all pydantic ^
        mapart_toolkit_server.py
"""

import os
import sys
import io
import json
import math
import time
import uuid
import socket
import zipfile
import tempfile
import base64
import colorsys
import threading
import traceback
import webbrowser
from collections import OrderedDict
from urllib.parse import quote

import numpy as np
from PIL import Image, ImageFilter
from fastapi import FastAPI, File, UploadFile, Form
from fastapi.responses import HTMLResponse, Response, JSONResponse
import uvicorn

try:
    from litemapy import Region, BlockState, Schematic
except ImportError:
    print("缺少 litemapy 库，请执行：pip install litemapy")
    sys.exit(1)


# ============================================================
# 常量
# ============================================================
HOST = "127.0.0.1"
PORT = 8765
TASKS = {}
TASK_LOCK = threading.Lock()
KEEP_TASKS = 15

TARGET_BLOCK_ID = "minecraft:glow_lichen"
FACE_KEYS = ["down", "up", "north", "south", "east", "west"]

# 预览分辨率上限（越大越清晰越慢）
PREVIEW_MAX_SIDE = 384

# 同色多选方块时的随机分配种子。固定种子 = 同参数同图片永远得到同一份成品，
# 预览里的用量统计也和实际生成一致。
DEFAULT_SEED = 20240922


# ============================================================
# 资源目录
# ============================================================
def get_base_dir():
    if hasattr(sys, "_MEIPASS"):
        b = sys._MEIPASS
        if os.path.isfile(os.path.join(b, "index.html")):
            return b
    sd = os.path.dirname(os.path.abspath(__file__))
    if os.path.isfile(os.path.join(sd, "index.html")):
        return sd
    ed = os.path.dirname(os.path.abspath(sys.executable))
    if os.path.isfile(os.path.join(ed, "index.html")):
        return ed
    cwd = os.getcwd()
    if os.path.isfile(os.path.join(cwd, "index.html")):
        return cwd
    return sd


def get_resource_path(filename):
    """在若干候选目录里查找资源文件，找不到返回 None。"""
    cands = []
    if hasattr(sys, "_MEIPASS"):
        cands.append(sys._MEIPASS)
    cands.append(os.path.dirname(os.path.abspath(__file__)))
    cands.append(os.path.dirname(os.path.abspath(sys.executable)))
    cands.append(get_base_dir())
    cands.append(os.getcwd())
    seen = set()
    for d in cands:
        if d in seen:
            continue
        seen.add(d)
        p = os.path.join(d, filename)
        if os.path.isfile(p):
            return p
    return None


# ============================================================
# 调色板数据（minecraft_blocks_mapcolor.json · 按颜色值分组）
# ============================================================
BLOCKDATA_FILE = "blockdata.py"
BLOCK_SOURCE_FILE = "minecraft_blocks_mapcolor.json"
ICON_FILE = "block_icons.png"


def _hex_to_rgb(h):
    h = h.lstrip("#")
    if len(h) != 6:
        raise ValueError("bad hex: " + h)
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _color_sort_key(hexv):
    """灰阶排前面（亮的在上），彩色按色相排，同色相亮的在上。"""
    r, g, b = _hex_to_rgb(hexv)
    h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
    if s < 0.15:
        return (0, 0.0, 1.0 - v, hexv)
    return (1, h, 1.0 - v, hexv)


def _load_blockdata():
    """
    方块表由 gen_blockdata.py 生成到 blockdata.py。
    每行：(方块 ID, 中文名, 颜色 hex, 图标列, 图标行)
    """
    try:
        import blockdata as bd
        rows = [(str(b), str(n), str(c).upper(), int(cx), int(cy))
                for b, n, c, cx, cy in bd.BLOCK_ROWS]
    except Exception as e:
        print("=" * 60)
        print("[错误] 读不到方块数据 blockdata.py：%s" % e)
        print("请先执行： python gen_blockdata.py")
        print("=" * 60)
        sys.exit(1)

    if not rows:
        print("[错误] blockdata.py 中没有方块数据。")
        sys.exit(1)

    icon = {
        "file": str(getattr(bd, "ICON_SHEET", ICON_FILE)),
        "size": int(getattr(bd, "ICON_SIZE", 16)),
        "cols": int(getattr(bd, "ICON_COLS", 16)),
        "rows": int(getattr(bd, "ICON_ROWS", 1)),
    }
    print("[方块表] blockdata.py：%d 个方块 / %d 种颜色"
          % (len(rows), len({r[2] for r in rows})))
    return rows, icon


def _build_palette_groups(rows):
    """
    按颜色值分组：一个颜色一组，组内是该颜色的所有方块。
    返回 [{"hex": "#606060", "rgb": (96, 96, 96),
          "blocks": [{"id": "minecraft:stone", "label": "石头",
                      "name_eng": "stone", "cx": 0, "cy": 0}, ...]}, ...]
    """
    bucket = {}
    for bid, label, hexv, cx, cy in rows:
        g = bucket.get(hexv)
        if g is None:
            g = {"hex": hexv, "rgb": _hex_to_rgb(hexv), "blocks": []}
            bucket[hexv] = g
        g["blocks"].append({
            "id": "minecraft:" + bid,
            "name_eng": bid,
            "label": label,
            "cx": cx,
            "cy": cy,
        })
    for g in bucket.values():
        g["blocks"].sort(key=lambda b: b["label"])
    return [bucket[h] for h in sorted(bucket.keys(), key=_color_sort_key)]


BLOCK_ROWS, ICON_META = _load_blockdata()
PALETTE_GROUPS = _build_palette_groups(BLOCK_ROWS)

# 全部可选方块 ID 与「每个颜色只选一个」的默认配置
ALL_BLOCK_IDS = frozenset(
    b["id"] for g in PALETTE_GROUPS for b in g["blocks"])
DEFAULT_BLOCK_IDS = [g["blocks"][0]["id"] for g in PALETTE_GROUPS]

# 供 /api/palette 返回的静态描述
PALETTE_META = [
    {
        "hex": g["hex"],
        "rgb": list(g["rgb"]),
        "blocks": [dict(b) for b in g["blocks"]],
    }
    for g in PALETTE_GROUPS
]

# 方块 ID -> 分组下标 / 中文名 / 颜色，用于统计与展示
BLOCK_INDEX = {}
for _gi, _g in enumerate(PALETTE_GROUPS):
    for _b in _g["blocks"]:
        BLOCK_INDEX[_b["id"]] = (_gi, _b["label"], _g["hex"])


# ============================================================
# 颜色空间
# ============================================================
def _srgb_to_linear(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def rgb_to_lab(r, g, b):
    rl, gl, bl = _srgb_to_linear(r), _srgb_to_linear(g), _srgb_to_linear(b)
    x = rl * 0.4124564 + gl * 0.3575761 + bl * 0.1804375
    y = rl * 0.2126729 + gl * 0.7151522 + bl * 0.0721750
    z = rl * 0.0193339 + gl * 0.1191920 + bl * 0.9503041
    x /= 0.95047; y /= 1.00000; z /= 1.08883
    def f(t):
        return t ** (1/3) if t > 0.008856 else 7.787*t + 16/116
    fx, fy, fz = f(x), f(y), f(z)
    return (116*fy - 16, 500*(fx-fy), 200*(fy-fz))


# ============================================================
# 运行时调色板（按每次请求提交的方块选择构建）
# ============================================================
class Palette:
    """一次请求实际使用的调色板：只包含被启用的颜色组。"""

    __slots__ = ("groups", "hexes", "rgb", "r", "g", "b", "lab", "n")

    def __init__(self, groups):
        self.groups = groups
        self.hexes = [g["hex"] for g in groups]
        self.rgb = np.array([g["rgb"] for g in groups],
                            dtype=np.float32).reshape(-1, 3)
        self.r = self.rgb[:, 0].astype(np.int32)
        self.g = self.rgb[:, 1].astype(np.int32)
        self.b = self.rgb[:, 2].astype(np.int32)
        self.lab = [rgb_to_lab(*g["rgb"]) for g in groups]
        self.n = len(groups)


def make_palette(selected_ids):
    """
    根据前端提交的方块 ID 列表构建调色板：

      · 同一颜色组内只保留被选中的方块
      · 该组一个方块都没选 -> 整组从调色板移除（这个颜色不再被使用）

    selected_ids 为 None（请求里没带这个字段）时使用默认配置（每组第一个方块）；
    传了列表但结果为空 -> 返回空调色板，由调用方报错提示。
    返回 (Palette, 实际生效的方块 ID 集合)。
    """
    if selected_ids is None:
        sel = set(DEFAULT_BLOCK_IDS)
    else:
        sel = {str(x) for x in selected_ids} & ALL_BLOCK_IDS

    groups = []
    used = set()
    for g in PALETTE_GROUPS:
        picked = [b for b in g["blocks"] if b["id"] in sel]
        if not picked:
            continue
        for b in picked:
            used.add(b["id"])
        groups.append({"hex": g["hex"], "rgb": g["rgb"], "blocks": picked})

    return Palette(groups), used


# ============================================================
# 颜色匹配算法
# ============================================================
def _match_euclidean(r, g, b, pal):
    d = (r - pal.r)**2 + (g - pal.g)**2 + (b - pal.b)**2
    return int(d.argmin())


def _match_weighted(r, g, b, pal):
    d = 0.30*(r-pal.r)**2 + 0.59*(g-pal.g)**2 + 0.11*(b-pal.b)**2
    return int(d.argmin())


def _match_redmean(r, g, b, pal):
    rmean = (r + pal.r) * 0.5
    dr = r - pal.r; dg = g - pal.g; db = b - pal.b
    d = (2 + rmean/256)*dr*dr + 4*dg*dg + (2 + (255-rmean)/256)*db*db
    return int(d.argmin())


def _match_cie76(L, a, b, pal):
    best, bd = 0, 1e30
    for i, (pl, pa, pb) in enumerate(pal.lab):
        dL = L - pl; da = a - pa; db = b - pb
        d = dL*dL + da*da + db*db
        if d < bd: bd, best = d, i
    return best


def _match_cie94(L, a, b, pal):
    best, bd = 0, 1e30
    C1 = math.hypot(a, b)
    for i, (pl, pa, pb) in enumerate(pal.lab):
        C2 = math.hypot(pa, pb)
        dL = L - pl; dC = C1 - C2
        da = a - pa; db = b - pb
        dH2 = max(0, da*da + db*db - dC*dC)
        SC = 1 + 0.045*C1; SH = 1 + 0.015*C1
        d = dL*dL + (dC/SC)**2 + dH2/(SH*SH)
        if d < bd: bd, best = d, i
    return best


def _match_ciede2000(L1, a1, b1, pal):
    best, bd = 0, 1e30
    C1 = math.hypot(a1, b1)
    C1_7 = C1 ** 7
    for i, (L2, a2, b2) in enumerate(pal.lab):
        C2 = math.hypot(a2, b2)
        C2_7 = C2 ** 7
        Cbar = (C1 + C2) * 0.5
        Cbar_7 = Cbar ** 7
        G = 0.5 * (1 - math.sqrt(Cbar_7 / (Cbar_7 + 25**7)))
        a1p = a1 * (1 + G); a2p = a2 * (1 + G)
        C1p = math.hypot(a1p, b1); C2p = math.hypot(a2p, b2)
        h1p = math.degrees(math.atan2(b1, a1p)) % 360
        h2p = math.degrees(math.atan2(b2, a2p)) % 360
        dLp = L2 - L1; dCp = C2p - C1p
        if C1p * C2p == 0:
            dhp = 0.0
        else:
            dh = h2p - h1p
            if abs(dh) <= 180: dhp = dh
            elif dh > 180: dhp = dh - 360
            else: dhp = dh + 360
        dHp = 2 * math.sqrt(C1p * C2p) * math.sin(math.radians(dhp) * 0.5)
        Lbarp = (L1 + L2) * 0.5; Cbarp = (C1p + C2p) * 0.5
        if C1p * C2p == 0:
            hbarp = h1p + h2p
        else:
            hsum = h1p + h2p
            if abs(h1p - h2p) <= 180: hbarp = hsum * 0.5
            elif hsum < 360: hbarp = (hsum + 360) * 0.5
            else: hbarp = (hsum - 360) * 0.5
        T = (1 - 0.17*math.cos(math.radians(hbarp - 30))
             + 0.24*math.cos(math.radians(2*hbarp))
             + 0.32*math.cos(math.radians(3*hbarp + 6))
             - 0.20*math.cos(math.radians(4*hbarp - 63)))
        dTheta = 30 * math.exp(-(((hbarp - 275) / 25) ** 2))
        Cbarp_7 = Cbarp ** 7
        RC = 2 * math.sqrt(Cbarp_7 / (Cbarp_7 + 25**7))
        SL = 1 + (0.015 * (Lbarp - 50)**2) / math.sqrt(20 + (Lbarp - 50)**2)
        SC = 1 + 0.045 * Cbarp
        SH = 1 + 0.015 * Cbarp * T
        RT = -math.sin(math.radians(2 * dTheta)) * RC
        d = (dLp/SL)**2 + (dCp/SC)**2 + (dHp/SH)**2 + RT*(dCp/SC)*(dHp/SH)
        if d < bd: bd, best = d, i
    return best


def get_matcher(algo, pal):
    if algo == "euclidean":
        return lambda r, g, b: _match_euclidean(r, g, b, pal)
    if algo == "redmean":
        return lambda r, g, b: _match_redmean(r, g, b, pal)
    if algo == "cie76":
        return lambda r, g, b: _match_cie76(*rgb_to_lab(r, g, b), pal)
    if algo == "cie94":
        return lambda r, g, b: _match_cie94(*rgb_to_lab(r, g, b), pal)
    if algo == "ciede2000":
        return lambda r, g, b: _match_ciede2000(*rgb_to_lab(r, g, b), pal)
    return lambda r, g, b: _match_weighted(r, g, b, pal)



ALGO_LABELS = {
    "euclidean": "欧几里得 RGB（最快）",
    "weighted": "加权 RGB（推荐）",
    "redmean": "Redmean",
    "cie76": "CIE76",
    "cie94": "CIE94",
    "ciede2000": "CIEDE2000（最精确）",
}


# ============================================================
# 抖动算法
# ============================================================
DIFFUSION_KERNELS = {
    "floyd": [(1,0,7/16),(-1,1,3/16),(0,1,5/16),(1,1,1/16)],
    "atkinson": [(1,0,1/8),(2,0,1/8),(-1,1,1/8),(0,1,1/8),(1,1,1/8),(0,2,1/8)],
    "jarvis": [(1,0,7/48),(2,0,5/48),(-2,1,3/48),(-1,1,5/48),(0,1,7/48),
               (1,1,5/48),(2,1,3/48),(-2,2,1/48),(-1,2,3/48),(0,2,5/48),
               (1,2,3/48),(2,2,1/48)],
    "stucki": [(1,0,8/42),(2,0,4/42),(-2,1,2/42),(-1,1,4/42),(0,1,8/42),
               (1,1,4/42),(2,1,2/42),(-2,2,1/42),(-1,2,2/42),(0,2,4/42),
               (1,2,2/42),(2,2,1/42)],
    "burkes": [(1,0,8/32),(2,0,4/32),(-2,1,2/32),(-1,1,4/32),(0,1,8/32),
               (1,1,4/32),(2,1,2/32)],
    "sierra": [(1,0,5/32),(2,0,3/32),(-2,1,2/32),(-1,1,4/32),(0,1,5/32),
               (1,1,4/32),(2,1,2/32),(-1,2,2/32),(0,2,3/32),(1,2,2/32)],
    "sierra_lite": [(1,0,2/4),(-1,1,1/4),(0,1,1/4)],
}

DITHER_LABELS = {
    "none": "无抖动",
    "bayer4": "Bayer 4×4",
    "bayer8": "Bayer 8×8",
    "floyd": "Floyd-Steinberg",
    "atkinson": "Atkinson",
    "jarvis": "Jarvis-Judice-Ninke",
    "stucki": "Stucki",
    "burkes": "Burkes",
    "sierra": "Sierra",
    "sierra_lite": "Sierra-Lite",
}


def _make_bayer(n):
    if n == 1: return np.array([[0]], dtype=np.float32)
    p = _make_bayer(n // 2)
    return np.block([[4*p, 4*p+2], [4*p+3, 4*p+1]])


BAYER_4 = _make_bayer(4)
BAYER_8 = _make_bayer(8)


# ============================================================
# 图像处理
# ============================================================
def process_image(img, algo_key, dither_key, strength, pal):
    W, H = img.size
    matcher = get_matcher(algo_key, pal)

    if dither_key == "none":
        arr = np.array(img, dtype=np.uint8)
        flat = arr.reshape(-1, 3)
        if algo_key in ("euclidean", "weighted", "redmean"):
            chunk = 1 << 15
            out = np.empty(flat.shape[0], dtype=np.int32)
            for s in range(0, flat.shape[0], chunk):
                e = min(s + chunk, flat.shape[0])
                f = flat[s:e].astype(np.float32)
                diff = f[:, None, :] - pal.rgb[None, :, :]
                if algo_key == "euclidean":
                    d = (diff * diff).sum(axis=-1)
                elif algo_key == "weighted":
                    wgt = np.array([0.30, 0.59, 0.11], dtype=np.float32)
                    d = (diff * diff * wgt[None, None, :]).sum(axis=-1)
                else:
                    rmean = (f[:, None, 0] + pal.rgb[None, :, 0]) * 0.5
                    d = ((2 + rmean/256)*diff[:,:,0]**2
                         + 4*diff[:,:,1]**2
                         + (2 + (255-rmean)/256)*diff[:,:,2]**2)
                out[s:e] = d.argmin(axis=1)
            idx = out.reshape(H, W)
        else:
            src = arr.reshape(-1, 3)
            out = np.empty(src.shape[0], dtype=np.int32)
            cache = {}
            for i in range(src.shape[0]):
                key = (int(src[i, 0]), int(src[i, 1]), int(src[i, 2]))
                v = cache.get(key)
                if v is None:
                    v = matcher(*key); cache[key] = v
                out[i] = v
            idx = out.reshape(H, W)
        return idx, pal.rgb[idx].astype(np.uint8)

    buf = np.array(img, dtype=np.float32)

    if dither_key in ("bayer4", "bayer8"):
        matrix = BAYER_4 if dither_key == "bayer4" else BAYER_8
        bh, bw = matrix.shape
        tile = np.tile(matrix, (H // bh + 1, W // bw + 1))[:H, :W]
        thresh = (tile / (bh * bw)) - 0.5
        buf = np.clip(buf + thresh[:,:,None] * (strength * 64.0), 0, 255)
        src = buf.reshape(-1, 3).astype(np.int32)
        out = np.empty(src.shape[0], dtype=np.int32)
        cache = {}
        for i in range(src.shape[0]):
            key = (int(src[i, 0]), int(src[i, 1]), int(src[i, 2]))
            v = cache.get(key)
            if v is None:
                v = matcher(*key); cache[key] = v
            out[i] = v
        idx = out.reshape(H, W)
        return idx, pal.rgb[idx].astype(np.uint8)

    kernel = DIFFUSION_KERNELS[dither_key]
    idx = np.zeros((H, W), dtype=np.int32)
    cache = {}
    for y in range(H):
        for x in range(W):
            r = buf[y, x, 0]; g = buf[y, x, 1]; b = buf[y, x, 2]
            rc = int(min(255, max(0, r + 0.5)))
            gc = int(min(255, max(0, g + 0.5)))
            bc = int(min(255, max(0, b + 0.5)))
            key = (rc, gc, bc)
            pi = cache.get(key)
            if pi is None:
                pi = matcher(rc, gc, bc); cache[key] = pi
            idx[y, x] = pi
            pr, pg, pb = pal.rgb[pi]
            er = (r - pr) * strength
            eg = (g - pg) * strength
            eb = (b - pb) * strength
            for dx, dy, k in kernel:
                nx, ny = x + dx, y + dy
                if 0 <= nx < W and 0 <= ny < H:
                    buf[ny, nx, 0] += er * k
                    buf[ny, nx, 1] += eg * k
                    buf[ny, nx, 2] += eb * k
    return idx, pal.rgb[idx].astype(np.uint8)


def flatten_image(img):
    if img.mode == "P": img = img.convert("RGBA")
    if img.mode in ("RGBA", "LA"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        return bg
    return img.convert("RGB")


def fit_image(img, tw, th, mode):
    if mode == "stretch":
        return img.resize((tw, th), Image.LANCZOS)
    iw, ih = img.size
    ta = tw / th; ia = iw / ih
    if mode == "fit":
        if ia > ta: nw, nh = tw, max(1, int(round(ih * tw / iw)))
        else: nh, nw = th, max(1, int(round(iw * th / ih)))
        r = img.resize((nw, nh), Image.LANCZOS)
        c = Image.new("RGB", (tw, th), (255, 255, 255))
        c.paste(r, ((tw-nw)//2, (th-nh)//2))
        return c
    else:
        if ia > ta: nh, nw = th, max(1, int(round(iw * th / ih)))
        else: nw, nh = tw, max(1, int(round(ih * tw / iw)))
        r = img.resize((nw, nh), Image.LANCZOS)
        left = (nw - tw) // 2; top = (nh - th) // 2
        return r.crop((left, top, left + tw, top + th))


# ============================================================
# 图片调整（左侧边栏：曝光 / 亮度 / 对比度 / 锐化 / 暗角）
# ============================================================
ADJUST_KEYS = ("exposure", "brightness", "contrast", "sharpen", "vignette")

ADJUST_LABELS = {
    "exposure": "曝光",
    "brightness": "亮度",
    "contrast": "对比度",
    "sharpen": "锐化",
    "vignette": "暗角",
}


def parse_adjust(payload):
    """从请求里取出调整参数并夹到合法范围。全为 0 时返回 None（跳过处理）。"""
    raw = payload.get("adjust") or {}
    if not isinstance(raw, dict):
        return None
    out = {}
    for k in ADJUST_KEYS:
        try:
            v = int(round(float(raw.get(k, 0))))
        except (TypeError, ValueError):
            v = 0
        out[k] = max(-100, min(100, v))
    if out["sharpen"] < 0:
        out["sharpen"] = 0
    if out["vignette"] < 0:
        out["vignette"] = 0
    if all(v == 0 for v in out.values()):
        return None
    return out


def apply_image_adjust(img, adj):
    """
    在「已缩放成成品尺寸」的图上做调整，顺序：曝光 -> 亮度 -> 对比度 -> 暗角 -> 锐化。
    暗角按成品画幅计算，所以必须先缩放再处理。
    """
    if not adj:
        return img

    exposure = adj.get("exposure", 0) / 100.0
    brightness = adj.get("brightness", 0) / 100.0
    contrast = adj.get("contrast", 0) / 100.0
    sharpen = adj.get("sharpen", 0) / 100.0
    vignette = adj.get("vignette", 0) / 100.0

    if exposure or brightness or contrast or vignette:
        a = np.asarray(img.convert("RGB"), dtype=np.float32) / 255.0
        if exposure:
            a = a * (2.0 ** (exposure * 1.5))          # ±1.5 档
        if brightness:
            a = a + brightness * 0.5                   # ±50% 亮度偏移
        if contrast:
            a = (a - 0.5) * (1.0 + contrast) + 0.5     # ±100% 对比度
        a = np.clip(a, 0.0, 1.0)
        if vignette:
            h, w = a.shape[:2]
            yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
            cx = max((w - 1) / 2.0, 1e-6)
            cy = max((h - 1) / 2.0, 1e-6)
            r = np.sqrt(((xx - cx) / cx) ** 2 + ((yy - cy) / cy) ** 2)
            falloff = np.clip((r - 0.35) / 0.75, 0.0, 1.0) ** 1.4
            a = a * (1.0 - vignette * falloff)[:, :, None]
        img = Image.fromarray((a * 255.0 + 0.5).astype(np.uint8), "RGB")

    if sharpen:
        img = img.filter(ImageFilter.UnsharpMask(
            radius=1.6, percent=int(round(sharpen * 250)), threshold=0))
    return img


# ============================================================
# 比例推荐
# ============================================================
def recommend_ratios(img_w, img_h, max_units=6):
    if img_w <= 0 or img_h <= 0: return []
    target = img_w / img_h
    lt = math.log(target)
    cands = []
    for x in range(1, max_units+1):
        for y in range(1, max_units+1):
            diff = abs(math.log(x / y) - lt)
            size_pen = (x*y) / (max_units*max_units) * 0.15
            cands.append((diff + size_pen, x, y, diff))
    cands.sort(key=lambda t: t[0])
    seen = set(); out = []
    for score, x, y, diff in cands:
        if (x, y) in seen: continue
        seen.add((x, y))
        out.append({"x": x, "y": y, "diff": diff,
                    "px_w": x*128, "px_h": y*128})
        if len(out) >= 6: break
    return out


# ============================================================
# Litematic 构建（默认地面朝向 XZ）
# ============================================================
def pick_block_names(idx, pal, rng):
    """
    为每个像素选定实际方块名。

    同一颜色组内勾选了多个方块时 —— 该颜色的每个格子随机取其中一个，
    成品纹理会有自然的噪点感（颜色完全一致，只有方块材质不同）。
    """
    H, W = idx.shape
    names = np.empty((H, W), dtype=object)
    for gi, grp in enumerate(pal.groups):
        ids = [b["id"] for b in grp["blocks"]]
        mask = (idx == gi)
        n = int(mask.sum())
        if n == 0:
            continue
        if len(ids) == 1:
            names[mask] = ids[0]
        else:
            picks = [ids[int(p)] for p in rng.integers(0, len(ids), size=n)]
            names[mask] = picks
    return names


def count_block_usage(names):
    """
    统计每种方块用了多少个，返回按用量倒序的列表：
        [{"id","label","hex","count","percent"}, ...]
    """
    flat = names.reshape(-1)
    total = int(flat.size)
    tally = {}
    for name in flat:
        if name:
            tally[name] = tally.get(name, 0) + 1
    out = []
    for bid, cnt in tally.items():
        _, label, hexv = BLOCK_INDEX.get(bid, (None, bid, "#888888"))
        out.append({
            "id": bid,
            "label": label,
            "hex": hexv,
            "count": cnt,
            "percent": round(cnt * 100.0 / total, 2) if total else 0.0,
        })
    out.sort(key=lambda d: (-d["count"], d["label"]))
    return out, total


def build_mapart_schematic(idx, pal, seed=None, with_counts=False):
    """
    构建地图画投影：
      固定 XZ 地面朝向（图片宽 → X，图片高 → Z）
      厚度 1（Y 方向），无底板，只放一层地图画方块
      图片左上角对应 (0, 0, 0)，向右为 +X，向下为 +Z

    with_counts=True 时额外返回每种方块的用量统计。
    """
    H, W = idx.shape
    rng = np.random.default_rng(seed)
    names = pick_block_names(idx, pal, rng)
    counts, _ = count_block_usage(names)

    # XZ 平面：Y 方向厚度为 1
    region = Region(0, 0, 0, W, 1, H)
    placed = 0
    cache = {}
    for row in range(H):
        for col in range(W):
            name = names[row, col]
            bs = cache.get(name)
            if bs is None:
                bs = BlockState(name)
                cache[name] = bs
            try:
                region[col, 0, row] = bs
                placed += 1
            except Exception:
                pass
    schem = region.as_schematic(
        name="MapArt", author="Toolkit",
        description=f"MapArt {W}x{H} (XZ ground)")
    if with_counts:
        return schem, placed, counts
    return schem, placed


def schem_to_bytes(schem):
    tmp = tempfile.NamedTemporaryFile(suffix=".litematic", delete=False)
    tmp.close()
    try:
        schem.save(tmp.name)
        with open(tmp.name, "rb") as f:
            return f.read()
    finally:
        try: os.unlink(tmp.name)
        except OSError: pass


def split_boundaries(total, parts):
    total = int(total); parts = max(1, min(int(parts), total))
    return [int(round(i*total/parts)) for i in range(parts+1)]


def do_slice(content, cols, rows, progress_cb=None):
    tmp = tempfile.NamedTemporaryFile(suffix=".litematic", delete=False)
    tmp.write(content); tmp.close()
    try:
        schem = Schematic.load(tmp.name)
    finally:
        try: os.unlink(tmp.name)
        except OSError: pass

    regions = dict(schem.regions)
    min_x = min_y = min_z = 0
    max_x = max_y = max_z = 0
    for reg in regions.values():
        rx, ry, rz = reg.x, reg.y, reg.z
        rw, rh, rl = abs(reg.width), abs(reg.height), abs(reg.length)
        min_x = min(min_x, rx); max_x = max(max_x, rx + rw)
        min_y = min(min_y, ry); max_y = max(max_y, ry + rh)
        min_z = min(min_z, rz); max_z = max(max_z, rz + rl)

    sx = max_x - min_x
    sz = max_z - min_z
    if sx <= 0 or sz <= 0:
        raise ValueError("包围盒尺寸无效")

    xb = split_boundaries(sx, cols)
    zb = split_boundaries(sz, rows)
    outputs = []
    total = rows * cols

    for r in range(rows):
        for c in range(cols):
            x0 = min_x + xb[c]; x1 = min_x + xb[c+1]
            z0 = min_z + zb[r]; z1 = min_z + zb[r+1]
            blocks = {}
            for reg in regions.values():
                rx, rz = reg.x, reg.z
                rw, rl = abs(reg.width), abs(reg.length)
                ry, rh = reg.y, abs(reg.height)
                ox0 = max(rx, x0); ox1 = min(rx + rw, x1)
                oz0 = max(rz, z0); oz1 = min(rz + rl, z1)
                if ox0 >= ox1 or oz0 >= oz1: continue
                for lx in range(rw):
                    wx = rx + lx
                    if wx < x0 or wx >= x1: continue
                    for lz in range(rl):
                        wz = rz + lz
                        if wz < z0 or wz >= z1: continue
                        for ly in range(rh):
                            wy = ry + ly
                            try:
                                blk = reg[lx, ly, lz]
                            except Exception:
                                continue
                            if blk is None: continue
                            if getattr(blk, "id", "") == "minecraft:air": continue
                            blocks[(wx, wy, wz)] = blk
            if not blocks:
                if progress_cb: progress_cb(len(outputs), total)
                continue
            ys = [p[1] for p in blocks.keys()]
            y_min = min(ys); y_max = max(ys)
            sub = Region(0, y_min, 0,
                         x1-x0, y_max-y_min+1, z1-z0)
            for (wx, wy, wz), blk in blocks.items():
                try:
                    sub[wx-x0, wy-y_min, wz-z0] = blk
                except Exception:
                    pass
            sub_schem = sub.as_schematic(
                name=f"Slice_{r+1}_{c+1}",
                author="Toolkit",
                description=f"Slice r{r+1}c{c+1}")
            outputs.append({
                "filename": f"slice_{r+1}_{c+1}.litematic",
                "bytes": schem_to_bytes(sub_schem),
            })
            if progress_cb: progress_cb(len(outputs), total)
    return outputs


def do_glow_lichen(content, target_faces, progress_cb=None):
    tmp = tempfile.NamedTemporaryFile(suffix=".litematic", delete=False)
    tmp.write(content); tmp.close()
    try:
        schem = Schematic.load(tmp.name)
    finally:
        try: os.unlink(tmp.name)
        except OSError: pass

    regions = dict(schem.regions)
    total_blocks = 0
    total_replaced = 0
    for reg in regions.values():
        try:
            positions = list(reg.block_positions())
        except Exception:
            continue
        for (x, y, z) in positions:
            total_blocks += 1
            try:
                blk = reg[x, y, z]
            except Exception:
                continue
            if blk is None or getattr(blk, "id", "") != TARGET_BLOCK_ID:
                continue
            try:
                reg[x, y, z] = blk.with_properties(**target_faces)
                total_replaced += 1
            except Exception:
                pass
        if progress_cb:
            progress_cb(total_replaced, total_blocks)

    return schem_to_bytes(schem), {"blocks": total_blocks, "replaced": total_replaced}


# ============================================================
# 图片缓存
# ============================================================
IMAGE_CACHE = OrderedDict()
IMAGE_CACHE_LOCK = threading.Lock()
MAX_IMAGE_CACHE = 12


def cache_put_image(img):
    sid = uuid.uuid4().hex[:12]
    with IMAGE_CACHE_LOCK:
        IMAGE_CACHE[sid] = img
        IMAGE_CACHE.move_to_end(sid)
        while len(IMAGE_CACHE) > MAX_IMAGE_CACHE:
            IMAGE_CACHE.popitem(last=False)
    return sid


def cache_get_image(sid):
    if not sid:
        return None
    with IMAGE_CACHE_LOCK:
        img = IMAGE_CACHE.get(sid)
        if img is not None:
            IMAGE_CACHE.move_to_end(sid)
        return img


# ============================================================
# 任务系统
# ============================================================
def add_log(task, msg):
    with TASK_LOCK:
        task["logs"].append(f"[{time.strftime('%H:%M:%S')}] {msg}")
        if len(task["logs"]) > 800:
            del task["logs"][:len(task["logs"]) - 800]


def create_task(name):
    tid = uuid.uuid4().hex[:12]
    TASKS[tid] = {
        "id": tid, "name": name,
        "running": True, "done": False,
        "logs": [], "error": None,
        "result_bytes": None, "result_name": None,
        "result": None, "started": time.time(),
    }
    if len(TASKS) > KEEP_TASKS:
        old = sorted(TASKS.keys(), key=lambda k: TASKS[k].get("started", 0))
        for k in old[:-KEEP_TASKS]:
            TASKS.pop(k, None)
    return tid


def finish_task(tid):
    t = TASKS.get(tid)
    if t:
        t["running"] = False
        t["done"] = True


# ============================================================
# FastAPI
# ============================================================
app = FastAPI()


@app.get("/", response_class=HTMLResponse)
def index():
    p = os.path.join(get_base_dir(), "index.html")
    try:
        with open(p, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return HTMLResponse("<h1>缺少 index.html</h1>", status_code=500)


# ------------------------------------------------------------
# 调色板（供前端渲染按颜色分组的方块选择面板）
# ------------------------------------------------------------
@app.get("/api/palette")
def api_palette():
    return {
        "ok": True,
        "source": BLOCK_SOURCE_FILE,
        "groups": PALETTE_META,
        "icon": ICON_META,
        "defaults": DEFAULT_BLOCK_IDS,
        "total_groups": len(PALETTE_GROUPS),
        "total_blocks": len(ALL_BLOCK_IDS),
    }


@app.get("/api/icons.png")
def api_icons():
    p = get_resource_path(ICON_META["file"])
    if not p:
        return JSONResponse({"ok": False, "msg": "缺少图标文件 " + ICON_META["file"]},
                            status_code=404)
    with open(p, "rb") as f:
        data = f.read()
    return Response(content=data, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=86400"})


# ------------------------------------------------------------
# 地图画 - 上传
# ------------------------------------------------------------
@app.post("/api/mapart/upload")
async def api_mapart_upload(file: UploadFile = File(...)):
    content = await file.read()
    try:
        img = Image.open(io.BytesIO(content))
        img.load()
    except Exception as e:
        return JSONResponse({"ok": False, "msg": f"无法读取图片：{e}"},
                            status_code=400)

    src = flatten_image(img)
    sid = cache_put_image(src)
    w, h = src.size
    recs = recommend_ratios(w, h)

    thumb = src.copy()
    thumb.thumbnail((400, 400), Image.LANCZOS)
    buf = io.BytesIO()
    thumb.save(buf, format="PNG")
    orig_b64 = base64.b64encode(buf.getvalue()).decode()

    return {
        "ok": True,
        "sid": sid,
        "width": w,
        "height": h,
        "recommendations": recs,
        "original": "data:image/png;base64," + orig_b64,
    }


# ------------------------------------------------------------
# 地图画 - 预览
# ------------------------------------------------------------
@app.post("/api/mapart/preview")
async def api_mapart_preview(payload: dict):
    sid = payload.get("sid")
    src = cache_get_image(sid)
    if src is None:
        return JSONResponse({"ok": False, "msg": "会话已过期，请重新上传图片"},
                            status_code=410)

    size_mode = payload.get("size_mode", "grid")
    grid_x = int(payload.get("grid_x", 1))
    grid_y = int(payload.get("grid_y", 1))
    max_size = int(payload.get("max_size", 128))
    fit_mode = payload.get("fit_mode", "stretch")
    algo = payload.get("algo", "weighted")
    dither = payload.get("dither", "none")
    strength = int(payload.get("strength", 100))
    selected = payload.get("blocks")
    adj = parse_adjust(payload)

    pal, used = make_palette(selected)
    if pal.n == 0:
        return JSONResponse(
            {"ok": False, "msg": "没有选择任何方块，请至少勾选一个方块"},
            status_code=400)

    try:
        if size_mode == "grid":
            gx = max(1, min(16, grid_x))
            gy = max(1, min(16, grid_y))
            tw = gx * 128
            th = gy * 128
            if tw * th > 1024 * 1024:
                return JSONResponse(
                    {"ok": False, "msg": f"尺寸过大 {tw}×{th}，超过 100 万像素"},
                    status_code=400)
            work = fit_image(src, tw, th, fit_mode)
            real_w, real_h = tw, th
        else:
            ms = max(8, min(512, max_size))
            iw, ih = src.size
            if max(iw, ih) > ms:
                s = ms / max(iw, ih)
                iw = max(1, int(round(iw * s)))
                ih = max(1, int(round(ih * s)))
            work = src.resize((iw, ih), Image.LANCZOS)
            real_w, real_h = iw, ih

        # 图片调整在成品尺寸上做（暗角要贴合画幅），预览与生成一致
        work = apply_image_adjust(work, adj)

        side = PREVIEW_MAX_SIDE
        try:
            side = int(payload.get("preview_side", PREVIEW_MAX_SIDE))
        except (TypeError, ValueError):
            pass
        side = max(128, min(768, side))

        if max(real_w, real_h) > side:
            scale = side / max(real_w, real_h)
            pw = max(1, int(round(real_w * scale)))
            ph = max(1, int(round(real_h * scale)))
            work = work.resize((pw, ph), Image.LANCZOS)
        else:
            pw, ph = real_w, real_h

        st = max(0.0, min(1.0, strength / 100.0))
        idx, rgb = process_image(work, algo, dither, st, pal)
        prev_img = Image.fromarray(rgb, mode="RGB")

        buf = io.BytesIO()
        prev_img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()

        # 按成品尺寸估算用量：预览图可能被缩小过，所以用比例换算回真实方块数
        names = pick_block_names(idx, pal, np.random.default_rng(DEFAULT_SEED))
        counts, _ = count_block_usage(names)
        ratio = (real_w * real_h) / float(max(1, idx.size))
        if abs(ratio - 1.0) > 1e-9:
            for c in counts:
                c["count"] = int(round(c["count"] * ratio))
                c["percent"] = round(c["count"] * 100.0 / max(1, real_w * real_h), 2)
            counts.sort(key=lambda d: (-d["count"], d["label"]))

        return {
            "ok": True,
            "preview": "data:image/png;base64," + b64,
            "width": real_w,
            "height": real_h,
            "preview_width": pw,
            "preview_height": ph,
            "blocks": real_w * real_h,
            "groups": pal.n,
            "colors_used": [pal.hexes[int(i)] for i in np.unique(idx)],
            "counts": counts,
            "total_blocks": real_w * real_h,
            "estimated": abs(ratio - 1.0) > 1e-9,
            "adjust": adj,
        }
    except Exception as e:
        traceback.print_exc()
        return JSONResponse({"ok": False, "msg": str(e)}, status_code=500)


# ------------------------------------------------------------
# 地图画 - 生成（XZ 地面朝向 · 厚度 1 · 无底板）
# ------------------------------------------------------------
@app.post("/api/mapart/generate")
async def api_mapart_generate(payload: dict):
    sid = payload.get("sid")
    src = cache_get_image(sid)
    if src is None:
        return JSONResponse({"ok": False, "msg": "会话已过期，请重新上传图片"},
                            status_code=410)

    size_mode = payload.get("size_mode", "grid")
    grid_x = int(payload.get("grid_x", 1))
    grid_y = int(payload.get("grid_y", 1))
    max_size = int(payload.get("max_size", 128))
    fit_mode = payload.get("fit_mode", "stretch")
    algo = payload.get("algo", "weighted")
    dither = payload.get("dither", "none")
    strength = int(payload.get("strength", 100))
    selected = payload.get("blocks")
    adj = parse_adjust(payload)

    pal, used = make_palette(selected)
    if pal.n == 0:
        return JSONResponse(
            {"ok": False, "msg": "没有选择任何方块，请至少勾选一个方块"},
            status_code=400)

    tid = create_task("mapart")

    def worker():
        task = TASKS[tid]
        try:
            add_log(task, "读取缓存的图片…")
            add_log(task, f"原图：{src.size[0]}×{src.size[1]}")

            if size_mode == "grid":
                gx = max(1, min(16, grid_x))
                gy = max(1, min(16, grid_y))
                tw = gx * 128
                th = gy * 128
                add_log(task, f"目标：{tw}×{th}（{gx}×{gy} 格）")
                if tw * th > 1024 * 1024:
                    raise ValueError(f"尺寸过大 {tw}×{th}，超过 100 万像素")
                work = fit_image(src, tw, th, fit_mode)
            else:
                ms = max(8, min(512, max_size))
                iw, ih = src.size
                if max(iw, ih) > ms:
                    s = ms / max(iw, ih)
                    iw = max(1, int(round(iw * s)))
                    ih = max(1, int(round(ih * s)))
                work = src.resize((iw, ih), Image.LANCZOS)
                add_log(task, f"缩放后：{iw}×{ih}")

            if adj:
                add_log(task, "图片调整：" + "，".join(
                    "%s %+d" % (ADJUST_LABELS[k], adj[k])
                    for k in ADJUST_KEYS if adj.get(k)))
                work = apply_image_adjust(work, adj)

            add_log(task, f"颜色算法：{ALGO_LABELS.get(algo, algo)}")
            add_log(task, f"抖动算法：{DITHER_LABELS.get(dither, dither)}，强度 {strength}%")
            add_log(task, f"调色板：启用 {pal.n} 个颜色 / {len(used)} 个方块")
            add_log(task, "处理像素…")
            st = max(0.0, min(1.0, strength / 100.0))
            idx, _ = process_image(work, algo, dither, st, pal)

            add_log(task, "构建投影（XZ 地面朝向 · 厚度 1 · 无底板）…")
            schem, placed, counts = build_mapart_schematic(
                idx, pal, seed=DEFAULT_SEED, with_counts=True)

            add_log(task, "保存文件…")
            data = schem_to_bytes(schem)

            task["result_bytes"] = data
            task["result_name"] = "mapart.litematic"
            task["result"] = {
                "width": idx.shape[1],
                "height": idx.shape[0],
                "placed": placed,
                "blocks_used": len(used),
                "groups_used": pal.n,
                "counts": counts,
                "total_blocks": placed,
            }
            add_log(task, f"完成 ✓ {idx.shape[1]}×{idx.shape[0]}，{placed} 方块，"
                          f"用到 {len(counts)} 种方块")
        except Exception as e:
            task["error"] = str(e)
            add_log(task, "❌ " + str(e))
            add_log(task, traceback.format_exc())
        finally:
            finish_task(tid)

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True, "task_id": tid}


# ------------------------------------------------------------
# 投影切分
# ------------------------------------------------------------
@app.post("/api/slice/process")
async def api_slice_process(
    file: UploadFile = File(...),
    cols: int = Form(4),
    rows: int = Form(4),
):
    content = await file.read()
    tid = create_task("slice")

    def worker():
        task = TASKS[tid]
        try:
            c = max(1, min(64, cols))
            r = max(1, min(64, rows))
            add_log(task, f"列(X)：{c}，行(Z)：{r}")

            def cb(done, total):
                if done % 5 == 0 or done == total:
                    add_log(task, f"进度：{done}/{total}")

            add_log(task, "正在切分…")
            outputs = do_slice(content, c, r, progress_cb=cb)
            add_log(task, f"生成 {len(outputs)} 个子文件")

            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for o in outputs:
                    zf.writestr(o["filename"], o["bytes"])
            zb = buf.getvalue()

            task["result_bytes"] = zb
            task["result_name"] = "slices.zip"
            task["result"] = {"count": len(outputs), "size": len(zb)}
            add_log(task, f"完成 ✓ {len(zb)/1024:.1f} KB")
        except Exception as e:
            task["error"] = str(e)
            add_log(task, "❌ " + str(e))
            add_log(task, traceback.format_exc())
        finally:
            finish_task(tid)

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True, "task_id": tid}


# ------------------------------------------------------------
# Glow Lichen
# ------------------------------------------------------------
@app.post("/api/lichen/process")
async def api_lichen_process(
    file: UploadFile = File(...),
    faces: str = Form("{}"),
    waterlogged: str = Form("false"),
):
    content = await file.read()
    try:
        faces_dict = json.loads(faces) if faces else {}
        if not isinstance(faces_dict, dict): faces_dict = {}
    except (json.JSONDecodeError, TypeError):
        faces_dict = {}
    wl = str(waterlogged).lower() == "true"

    tid = create_task("lichen")

    def worker():
        task = TASKS[tid]
        try:
            tf = {k: ("true" if faces_dict.get(k) else "false") for k in FACE_KEYS}
            tf["waterlogged"] = "true" if wl else "false"
            add_log(task, f"面属性：{tf}")

            def cb(rep, total):
                pass

            add_log(task, "正在处理…")
            data, info = do_glow_lichen(content, tf, progress_cb=cb)

            task["result_bytes"] = data
            task["result_name"] = "modified.litematic"
            task["result"] = info
            add_log(task, f"扫描 {info['blocks']}，替换 {info['replaced']} 个")
            add_log(task, "完成 ✓")
        except Exception as e:
            task["error"] = str(e)
            add_log(task, "❌ " + str(e))
            add_log(task, traceback.format_exc())
        finally:
            finish_task(tid)

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True, "task_id": tid}


# ------------------------------------------------------------
# 状态 / 下载
# ------------------------------------------------------------
@app.get("/api/status/{tid}")
def api_status(tid: str):
    t = TASKS.get(tid)
    if not t:
        return JSONResponse({"ok": False, "msg": "任务不存在"}, status_code=404)
    with TASK_LOCK:
        return {
            "ok": True,
            "running": t["running"],
            "done": t["done"],
            "logs": list(t["logs"]),
            "error": t["error"],
            "result": t["result"],
        }


@app.get("/api/download/{tid}")
def api_download(tid: str):
    t = TASKS.get(tid)
    if not t or not t.get("result_bytes"):
        return JSONResponse({"ok": False, "msg": "结果不存在"}, status_code=404)
    filename = t["result_name"] or "result.bin"
    quoted = quote(filename)
    return Response(
        content=t["result_bytes"],
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quoted}",
            "Content-Length": str(len(t["result_bytes"])),
        },
    )


# ============================================================
# 入口
# ============================================================
def main():
    print("=" * 60)
    print("地图画工具箱 - 后端服务")
    print(f"Python   : {sys.version.split()[0]}")
    print(f"运行目录 : {os.getcwd()}")
    print(f"脚本目录 : {os.path.dirname(os.path.abspath(__file__))}")
    print(f"资源目录 : {get_base_dir()}")

    idx = os.path.join(get_base_dir(), "index.html")
    print(f"index.html: {'✓ 存在' if os.path.isfile(idx) else '✗ 缺失'}  ({idx})")
    print(f"监听地址 : http://{HOST}:{PORT}")
    print("=" * 60)

    if not os.path.isfile(idx):
        msg = (f"找不到 index.html！\n\n"
               f"期望路径：{idx}\n\n"
               f"请把 index.html 放到与脚本同一目录。")
        print("[错误] " + msg)
        try:
            if sys.platform.startswith("win"):
                import ctypes
                ctypes.windll.user32.MessageBoxW(0, msg, "启动失败", 0x10)
        except Exception:
            pass
        try:
            input("按回车键退出…")
        except EOFError:
            pass
        sys.exit(1)

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((HOST, PORT))
    except OSError as e:
        msg = (f"端口 {PORT} 无法绑定：{e}\n\n"
               f"可能有一个旧的进程还占着这个端口。\n\n"
               f"解决方法：\n"
               f"  1. 命令行执行：\n"
               f"     netstat -ano | findstr :{PORT}\n"
               f"     找到 PID 后：taskkill /PID <PID> /F\n"
               f"  2. 或修改脚本里的 PORT 为其他值（比如 8899）")
        print("[错误] " + msg)
        try:
            if sys.platform.startswith("win"):
                import ctypes
                ctypes.windll.user32.MessageBoxW(0, msg, "启动失败", 0x10)
        except Exception:
            pass
        try:
            input("按回车键退出…")
        except EOFError:
            pass
        sys.exit(2)

    url = f"http://{HOST}:{PORT}"
    server_error = {"msg": None}

    def run_server():
        try:
            uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
        except Exception as e:
            server_error["msg"] = str(e)
            traceback.print_exc()

    t = threading.Thread(target=run_server, daemon=True)
    t.start()

    ready = False
    for _ in range(150):
        if server_error["msg"]:
            break
        try:
            with socket.create_connection((HOST, PORT), timeout=0.3):
                ready = True
                break
        except OSError:
            time.sleep(0.1)

    if server_error["msg"]:
        msg = f"服务器启动失败：{server_error['msg']}"
        print("[错误] " + msg)
        try:
            if sys.platform.startswith("win"):
                import ctypes
                ctypes.windll.user32.MessageBoxW(0, msg, "启动失败", 0x10)
        except Exception:
            pass
        try:
            input("按回车键退出…")
        except EOFError:
            pass
        sys.exit(3)

    if ready:
        print(f"✓ 服务器就绪：{url}")
        try:
            webbrowser.open(url)
            print("✓ 浏览器已打开")
        except Exception as e:
            print(f"⚠ 自动打开浏览器失败：{e}")
            print(f"  请手动访问：{url}")
    else:
        print(f"⚠ 等待超时，请手动访问：{url}")

    print("-" * 60)
    print("服务运行中。要停止服务，按 Ctrl+C 或直接关闭本窗口。")
    print("-" * 60)

    try:
        while True:
            time.sleep(1)
            if not t.is_alive():
                print("[警告] 服务器线程已退出")
                break
    except KeyboardInterrupt:
        print("\n已收到 Ctrl+C，正在退出…")


if __name__ == "__main__":
    main()