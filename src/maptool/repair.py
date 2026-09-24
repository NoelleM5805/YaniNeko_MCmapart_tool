# -*- coding: utf-8 -*-
"""
局部噪点修正（手动抖动修补）
============================

抖动算法为了还原渐变，会故意在局部掺入别的颜色 —— 大部分时候这是想要的噪点感，
但有些地方（大片纯色、人脸、文字）用户希望恢复成「本来想要的主色调」。
这个模块就是在抖动结果上做局部修补。

两种方式：

  1. 套索（区域级）
     在预览图上圈一片区域，找出该区域里的「受害者方块」——
     即**本来该是主色、却被抖动掺成了别的颜色**的方块 —— 然后把它们改回主色。
     强度 2~4 控制「改多少」：数字越大，判定越宽松，改回来的越多。

  2. 画笔（像素级）
     直接在预览上涂抹，把被污染的方块覆盖成指定颜色。颜色可以从预览上吸取，
     也可以从当前调色板里挑。

「受害者」的判定
----------------
区域内的主色 D 取该区域出现次数最多的调色板颜色（也可以由前端指定）。

对区域内每个 `当前颜色 C ≠ D` 的像素，比较两个距离：
    dD = 原始像素色 到 D 的距离
    dC = 原始像素色 到 C 的距离
`dD <= dC` 说明这个像素用 D 表示**不比用 C 差** —— 也就是抖动把它改坏了，
这就是「受害者方块」（按 S 高亮出来的就是这一批，与强度无关）。

强度决定「改多少」，条件是 `dD <= ratio * dC`：

    强度 2  ratio = 0.50   只改主色明显更合适的（近至少 2 倍），杂色感保留最多
    强度 3  ratio = 0.75   改主色更合适的（近至少 1.33 倍）
    强度 4  ratio = 1.00   改掉全部受害者

所以强度 4 之后，区域里剩下的非主色像素都是「用当前颜色确实比主色更准」的那些 ——
渐变该有的层次保留了，被抖动弄脏的部分清干净了。这一点是刻意设计的：
无脑把整片区域刷成主色会把渐变也压平，那不是「修正」而是「涂掉」。

距离用的是**当前选的颜色算法**（matching.dist_batch），所以「选哪个颜色」和
「离哪个更近」是同一套度量，不会出现自相矛盾的判定。
"""

import numpy as np
from PIL import Image, ImageDraw

from .matching import dist_batch
from .palette import Palette

# 强度 -> 距离比阈值。判定条件：dD <= ratio * dC
#   （dD = 原始像素到主色 D 的距离，dC = 到它当前颜色 C 的距离）
LEVEL_RATIO = {2: 0.50, 3: 0.75, 4: 1.00}
LEVEL_LABELS = {
    2: "轻度修复：只改「主色明显更合适」的（主色近至少 2 倍）",
    3: "中度修复：改「主色更合适」的（主色近至少 1.33 倍）",
    4: "强力修复：改掉全部受害者（主色不比当前色差的都改）",
}
# 高亮受害者用的颜色（洋红，和地图画的常见配色都不撞）
HIGHLIGHT_RGB = (255, 0, 200)


# ---------------------------------------------------------------- 参数解析
def _clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


def _clean_hex(h):
    if not isinstance(h, str):
        return None
    h = h.strip()
    if not h.startswith("#"):
        h = "#" + h
    h = h.upper()
    return h if len(h) == 7 else None


def parse_repair(payload):
    """
    把前端传来的 repair 字段规范化。没有任何有效内容就返回 None（= 不做修正）。

    repair = {
      "identify": bool,                       # True 时预览会把受害者高亮出来
      "lassos":   [{"points": [[x,y],...],    # 归一化坐标 0~1，按真实图像算
                    "level": 2..4,
                    "hex": "#RRGGBB"|null}],  # 不给就自动取区域主色
      "strokes":  [{"x":, "y":, "r":, "hex": "#RRGGBB"}],   # r 也归一化（按宽度）
    }
    """
    if not isinstance(payload, dict):
        return None
    r = payload.get("repair")
    if not isinstance(r, dict):
        return None

    lassos = []
    for it in (r.get("lassos") or []):
        if not isinstance(it, dict):
            continue
        pts = []
        for p in (it.get("points") or []):
            try:
                x, y = float(p[0]), float(p[1])
            except (TypeError, ValueError, IndexError):
                continue
            if np.isfinite(x) and np.isfinite(y):
                pts.append((_clamp(x, -0.5, 1.5), _clamp(y, -0.5, 1.5)))
        if len(pts) < 3:
            continue
        try:
            level = int(it.get("level", 2))
        except (TypeError, ValueError):
            level = 2
        lassos.append({"points": pts,
                       "level": int(_clamp(level, 2, 4)),
                       "hex": _clean_hex(it.get("hex"))})

    strokes = []
    for it in (r.get("strokes") or []):
        if not isinstance(it, dict):
            continue
        hx = _clean_hex(it.get("hex"))
        if not hx:
            continue
        try:
            x = float(it.get("x"))
            y = float(it.get("y"))
            rad = float(it.get("r", 0.01))
        except (TypeError, ValueError):
            continue
        if not (np.isfinite(x) and np.isfinite(y) and np.isfinite(rad)):
            continue
        strokes.append({"x": _clamp(x, -0.5, 1.5), "y": _clamp(y, -0.5, 1.5),
                        "r": _clamp(rad, 0.0005, 0.5), "hex": hx})

    if not lassos and not strokes and not r.get("identify"):
        return None
    return {"identify": bool(r.get("identify")),
            "lassos": lassos,
            "strokes": strokes}


# ---------------------------------------------------------------- 掩膜
def _polygon_mask(points, H, W):
    """归一化多边形 -> 布尔掩膜。用 PIL 光栅化，稳定且快。"""
    if len(points) < 3:
        return np.zeros((H, W), dtype=bool)
    img = Image.new("1", (W, H), 0)
    ImageDraw.Draw(img).polygon(
        [(x * W, y * H) for x, y in points], fill=1)
    return np.asarray(img, dtype=bool)


def _stroke_mask(strokes, H, W, only=None):
    """笔画 -> 布尔掩膜。半径按宽度归一化，保证不同分辨率下形状一致。"""
    img = Image.new("1", (W, H), 0)
    d = ImageDraw.Draw(img)
    for s in strokes:
        if only is not None and s is not only:
            continue
        cx, cy = s["x"] * W, s["y"] * H
        rx = s["r"] * W
        ry = rx * (W / float(H)) if H else rx
        d.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=1)
    return np.asarray(img, dtype=bool)


# ---------------------------------------------------------------- 主逻辑
def _group_index_by_hex(pal, hexv):
    for i, g in enumerate(pal.groups):
        if g["hex"].upper() == hexv:
            return i
    return None


def apply_repair(idx, work_rgb, pal, repair, algo_key, tables=None):
    """
    在抖动结果 `idx`（H×W 调色板下标）上做局部修正。

    work_rgb: 送进抖动之前的像素 (H,W,3) uint8 —— 判定「本该是什么颜色」要用它。
    pal:      本次请求的运行时调色板（Palette）。
    返回 (新的 idx, 诊断信息 dict)。没有有效修正时原样返回。
    """
    info = {"applied": False, "mask_pixels": 0, "victims": 0, "repaired": 0,
            "brush_pixels": 0, "dominant": None, "dominant_index": None,
            "lassos": [], "identify": bool(repair.get("identify")),
            "level_labels": []}
    if idx.size == 0 or pal.n == 0:
        return idx, info

    H, W = idx.shape
    idx = idx.copy()
    work_rgb = np.ascontiguousarray(work_rgb, dtype=np.uint8)

    # 选区掩膜（套索 + 笔画），只用来显示和高亮
    sel_mask = np.zeros((H, W), dtype=bool)
    for la in repair["lassos"]:
        sel_mask |= _polygon_mask(la["points"], H, W)

    # 受害者掩膜（高亮用）
    victim_mask = np.zeros((H, W), dtype=bool)
    flat_rgb = work_rgb.reshape(-1, 3).astype(np.int32)

    # ---------------- 套索：向主色修复 ----------------
    for la in repair["lassos"]:
        m = _polygon_mask(la["points"], H, W)
        npix = int(m.sum())
        li = {"pixels": npix, "dominant": None, "victims": 0,
              "repaired": 0, "level": la["level"]}
        if npix == 0:
            info["lassos"].append(li)
            continue

        cur = idx[m]
        vals, counts = np.unique(cur, return_counts=True)
        d_idx = int(vals[int(counts.argmax())])
        if la["hex"]:
            forced = _group_index_by_hex(pal, la["hex"])
            if forced is not None:
                d_idx = forced
        li["dominant"] = pal.hexes[d_idx]
        li["dominant_index"] = d_idx

        # 目标色单独建一个 2 色迷你调色板：主色 D + 当前出现的某个颜色 C
        ratio_t = LEVEL_RATIO[la["level"]]
        rows = np.flatnonzero(m.reshape(-1))
        other = cur != d_idx

        if other.any():
            r_idx = rows[other]
            cur_c = cur[other]
            px = flat_rgb[r_idx]
            do_repair = np.zeros(r_idx.size, dtype=bool)
            is_victim = np.zeros(r_idx.size, dtype=bool)
            for c_val in np.unique(cur_c):
                c_val = int(c_val)
                sub = cur_c == c_val
                mini = Palette([pal.groups[d_idx], pal.groups[c_val]])
                d2 = dist_batch(px[sub], mini, algo_key, None)
                dD = d2[:, 0]
                dC = d2[:, 1]
                # 「受害者」= 用主色 D 表示至少不比当前色 C 差（dD <= dC）。
                # 用乘法而不是除法，dC == 0（原始像素正好就是 C）时自然是 False。
                is_victim[sub] = dD <= dC
                do_repair[sub] = dD <= ratio_t * dC

            # 受害者掩膜（S 键高亮的就是这一批，和强度无关）
            if is_victim.any():
                vm = np.zeros(idx.size, dtype=bool)
                vm[r_idx[is_victim]] = True
                victim_mask |= vm.reshape(H, W)
            li["victims"] = int(is_victim.sum())

            hit = r_idx[do_repair]
            if hit.size:
                idx.reshape(-1)[hit] = d_idx
                li["repaired"] = int(hit.size)

        info["lassos"].append(li)
        info["level_labels"].append("强度 %d：%s" % (la["level"], LEVEL_LABELS[la["level"]]))

    # ---------------- 画笔：直接覆盖 ----------------
    for s in repair["strokes"]:
        gi = _group_index_by_hex(pal, s["hex"])
        if gi is None:
            continue
        m = _stroke_mask([s], H, W)
        n = int(m.sum())
        if n:
            idx[m] = gi
            info["brush_pixels"] += n
        sel_mask |= m

    if info["lassos"]:
        info["dominant"] = info["lassos"][0]["dominant"]
        info["dominant_index"] = info["lassos"][0].get("dominant_index")
        info["victims"] = sum(x["victims"] for x in info["lassos"])
        info["repaired"] = sum(x["repaired"] for x in info["lassos"])
    info["mask_pixels"] = int(sel_mask.sum())
    info["applied"] = bool(info["repaired"] or info["brush_pixels"])

    # 高亮掩膜交给调用方渲染：把「受害者」和「选区」带出去，
    # 但不塞进 info（里面是 numpy 数组，不适合直接进 JSON）
    info["_victim_mask"] = victim_mask
    info["_sel_mask"] = sel_mask
    return idx, info


def render_highlight(rgb, info):
    """
    把选区/受害者画到预览图上：
      选区     -- 整体压暗一点，让用户看清圈了哪里
      受害者   -- 换成洋红，一眼就能数出来
    只应在 identify=True 时调用。
    """
    out = np.array(rgb, dtype=np.uint8).copy()
    sel = info.get("_sel_mask")
    vic = info.get("_victim_mask")
    if sel is not None and sel.any():
        out[sel] = (out[sel].astype(np.float64) * 0.62).astype(np.uint8)
    if vic is not None and vic.any():
        out[vic] = HIGHLIGHT_RGB
    return out


def public_info(info):
    """去掉内部掩膜，剩下可以进 JSON 的部分。"""
    return {k: v for k, v in info.items() if not k.startswith("_")}
