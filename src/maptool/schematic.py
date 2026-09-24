# -*- coding: utf-8 -*-
from litemapy import Region, BlockState, Schematic
import numpy as np
import os
import re
import tempfile

from .palette import BLOCK_INDEX, BLOCK_PROPS_MAP


# ============================================================
# Litematic 构建（默认地面朝向 XZ）
# ============================================================
# 同一颜色组里勾了多个方块时，实际放哪一个：
#   random —— 每个格子随机挑一个，纹理有自然噪点感（默认）
#   cycle  —— 按格子顺序轮流使用，分布最均匀
#   first  —— 只用排在最前面的那个（前端"置顶"过的优先项）
ALLOC_MODES = ("random", "cycle", "first")

ALLOC_LABELS = {
    "random": "随机分配",
    "cycle": "轮换分配",
    "first": "只用优先项",
}


def parse_alloc(payload):
    mode = str(payload.get("alloc") or "random").strip().lower()
    return mode if mode in ALLOC_MODES else "random"


def pick_block_names(idx, pal, rng, alloc="random"):
    """
    为每个像素选定实际方块名。

    颜色完全一致，只有方块材质不同，所以怎么挑不影响成品颜色，
    只影响纹理观感。三种策略见 ALLOC_MODES。
    """
    H, W = idx.shape
    names = np.empty((H, W), dtype=object)
    flat_names = names.reshape(-1)
    flat_idx = idx.reshape(-1)

    for gi, grp in enumerate(pal.groups):
        ids = [b["id"] for b in grp["blocks"]]
        if not ids:
            continue
        mask = (flat_idx == gi)
        n = int(mask.sum())
        if n == 0:
            continue

        if len(ids) == 1 or alloc == "first":
            flat_names[mask] = ids[0]
        elif alloc == "cycle":
            # cumsum 给出该颜色第几次出现，按次数轮流取
            occ = np.cumsum(mask) - 1
            k = len(ids)
            flat_names[mask] = [ids[int(o) % k] for o in occ[mask]]
        else:
            picks = rng.integers(0, len(ids), size=n)
            flat_names[mask] = [ids[int(p)] for p in picks]

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


def build_mapart_schematic(idx, pal, seed=None, with_counts=False, alloc="random"):
    """
    构建地图画投影：
      固定 XZ 地面朝向（图片宽 → X，图片高 → Z）
      厚度 1（Y 方向），无底板，只放一层地图画方块
      图片左上角对应 (0, 0, 0)，向右为 +X，向下为 +Z

    with_counts=True 时额外返回每种方块的用量统计。
    """
    H, W = idx.shape
    rng = np.random.default_rng(seed)
    names = pick_block_names(idx, pal, rng, alloc)
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
                # 部分方块（发光地衣 / 铁活板门 / 压力板…）要带上固定方块状态
                bs = BlockState(name, **BLOCK_PROPS_MAP.get(name, {}))
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


def safe_stem(name):
    """把文件名整理成安全的「主名」（不含扩展名、无非法字符）。"""
    if not name:
        return ""
    name = str(name).strip().split("/")[-1].split("\\")[-1]
    name = re.sub(r"\.litematic$", "", name, flags=re.I)
    name = re.sub(r"\.[^.]+$", "", name)
    name = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", name)
    name = name.strip(" .")
    return name[:80]


def safe_litematic_name(name, fallback="mapart.litematic"):
    """
    把用户/图片文件名整理成安全的 .litematic 文件名：
    去掉路径分隔符与控制字符、限制长度、补上扩展名。
    """
    stem = safe_stem(name)
    if not stem:
        return fallback
    return stem + ".litematic"


