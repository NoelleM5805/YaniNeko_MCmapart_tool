# -*- coding: utf-8 -*-
import colorsys
import numpy as np
import sys

from .colorspace import rgb_to_lab


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


def _parse_block_props(s):
    """把 "down=true,up=false" 解析成 {"down": "true", "up": "false"}。"""
    out = {}
    if not s:
        return out
    for part in str(s).split(","):
        k, sep, v = part.partition("=")
        k, v = k.strip(), v.strip()
        if sep and k:
            out[k] = v
    return out


def _load_blockdata():
    """
    方块表由 tools/gen_blockdata.py 生成到 maptool/data/blockdata.py。
    每行：(方块 ID, 中文名, 颜色 hex, 图标列, 图标行, 方块状态)
    方块状态形如 "down=true,up=false"，没有就是空串。
    """
    try:
        from .data import blockdata as bd
        rows = []
        for r in bd.BLOCK_ROWS:
            bid, label, hexv, cx, cy = r[0], r[1], r[2], r[3], r[4]
            props = r[5] if len(r) > 5 else ""
            rows.append((str(bid), str(label), str(hexv).upper(),
                         int(cx), int(cy), str(props)))
    except Exception as e:
        print("=" * 60)
        print("[错误] 读不到方块数据 data/blockdata.py：%s" % e)
        print("请先执行： python tools/gen_blockdata.py")
        print("=" * 60)
        sys.exit(1)

    if not rows:
        print("[错误] data/blockdata.py 中没有方块数据。")
        sys.exit(1)

    icon = {
        "file": str(getattr(bd, "ICON_SHEET", ICON_FILE)),
        "size": int(getattr(bd, "ICON_SIZE", 16)),
        "cols": int(getattr(bd, "ICON_COLS", 16)),
        "rows": int(getattr(bd, "ICON_ROWS", 1)),
    }
    n_props = sum(1 for r in rows if r[5])
    print("[方块表] blockdata.py：%d 个方块 / %d 种颜色 / %d 个带方块状态"
          % (len(rows), len({r[2] for r in rows}), n_props))
    return rows, icon


def _build_palette_groups(rows):
    """
    按颜色值分组：一个颜色一组，组内是该颜色的所有方块。
    返回 [{"hex": "#606060", "rgb": (96, 96, 96),
          "blocks": [{"id": "minecraft:stone", "label": "石头",
                      "name_eng": "stone", "cx": 0, "cy": 0,
                      "props": "down=true"}, ...]}, ...]
    """
    bucket = {}
    for bid, label, hexv, cx, cy, props in rows:
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
            "props": props,
        })
    for g in bucket.values():
        g["blocks"].sort(key=lambda b: b["label"])
    return [bucket[h] for h in sorted(bucket.keys(), key=_color_sort_key)]


BLOCK_ROWS, ICON_META = _load_blockdata()
PALETTE_GROUPS = _build_palette_groups(BLOCK_ROWS)

# 方块 ID -> 方块状态（构建投影时用）
BLOCK_PROPS_MAP = {}
for _g in PALETTE_GROUPS:
    for _b in _g["blocks"]:
        _p = _parse_block_props(_b["props"])
        if _p:
            BLOCK_PROPS_MAP[_b["id"]] = _p

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
# 运行时调色板（按每次请求提交的方块选择构建）
# ============================================================
class Palette:
    """一次请求实际使用的调色板：只包含被启用的颜色组。"""

    __slots__ = ("groups", "hexes", "rgb", "rgb_list", "r", "g", "b",
                 "lab", "lab_l", "lab_a", "lab_b", "lab_c",
                 "n")

    def __init__(self, groups):
        self.groups = groups
        self.hexes = [g["hex"] for g in groups]
        self.rgb = np.array([g["rgb"] for g in groups],
                            dtype=np.float32).reshape(-1, 3)
        # 误差扩散的内层循环要反复取色，list of tuple 比 numpy 快得多
        self.rgb_list = self.rgb.tolist()
        self.r = self.rgb[:, 0].astype(np.int32)
        self.g = self.rgb[:, 1].astype(np.int32)
        self.b = self.rgb[:, 2].astype(np.int32)
        self.lab = [rgb_to_lab(*g["rgb"]) for g in groups]
        # Lab 的各个分量摊平成 numpy 数组，CIE 系列匹配就能整批算
        labs = np.array(self.lab, dtype=np.float64).reshape(-1, 3)
        self.lab_l = labs[:, 0]
        self.lab_a = labs[:, 1]
        self.lab_b = labs[:, 2]
        self.lab_c = np.hypot(self.lab_a, self.lab_b)
        self.n = len(groups)


def make_palette(selected_ids):
    """
    根据前端提交的方块 ID 列表构建调色板：

      · 同一颜色组内只保留被选中的方块
      · 该组一个方块都没选 -> 整组从调色板移除（这个颜色不再被使用）
      · 组内的先后顺序沿用提交上来的顺序 —— 这样前端把某个方块"置顶"之后，
        分配策略选「只用优先项」时用的就是它

    selected_ids 为 None（请求里没带这个字段）时使用默认配置（每组第一个方块）；
    传了列表但结果为空 -> 返回空调色板，由调用方报错提示。
    返回 (Palette, 实际生效的方块 ID 集合)。
    """
    if selected_ids is None:
        sel = set(DEFAULT_BLOCK_IDS)
        rank = {}
    else:
        seq = [str(x) for x in selected_ids]
        rank = {}
        for i, bid in enumerate(seq):
            if bid in ALL_BLOCK_IDS and bid not in rank:
                rank[bid] = i
        sel = set(rank)

    groups = []
    used = set()
    for g in PALETTE_GROUPS:
        picked = [b for b in g["blocks"] if b["id"] in sel]
        if not picked:
            continue
        # 按前端给的顺序排；没给的（默认模式）保持调色板原顺序
        picked.sort(key=lambda b: rank.get(b["id"], 1 << 30))
        for b in picked:
            used.add(b["id"])
        groups.append({"hex": g["hex"], "rgb": g["rgb"], "blocks": picked})

    return Palette(groups), used


