# -*- coding: utf-8 -*-
"""
局部噪点修正（手动抖动修补）
============================

抖动算法为了还原渐变会故意在局部掺入别的颜色。大部分时候这正是想要的噪点感，
但有些地方（大片纯色、人脸、文字）用户希望把「被抖动弄脏的方块」修回去。

工作方式
--------
前端维护一串**操作序列**，服务端每次都从抖动结果 `idx` 出发按顺序重放这一串操作：

    {"kind": "denoise", "target": "#605D77", "radius": 2, "lasso": [[x,y],...]}
    {"kind": "fill",    "hex": "#848484",    "lasso": [...]}
    {"kind": "revert",  "lasso": [...]}
    {"kind": "brush",   "hex": "#848484",    "radius": 0.02, "points": [[x,y],...]}

这么做有三个好处：

1. **可重放**：预览和最终生成用的是同一串操作，得到的修改一致。
2. **撤回是天然的**：撤回 = 去掉最后一个操作再重新渲染，不需要保存整张图。
3. **不依赖上一次的结果**：改调色板 / 改抖动算法之后，操作序列照样对得上。

「降噪」用的是**锁定目标方块的邻域多数替换**（邻居怎么做的就怎么做）：
    只在选区内、且当前方块正好是 `target` 的像素上动手，
    把它换成邻域 (2r+1)² 里出现次数最多的那个方块。
这样只清掉指定的那种杂色，选区内其它内容（线条、渐变、边界）一概不碰 ——
不会像「整片刷成一个颜色」那样把画面压平。

「还原」= 把选区内的方块恢复成**不做抖动**时最近的颜色，也就是这一片直接关掉抖动。
"""

import numpy as np
from PIL import Image, ImageDraw

from .matching import match_batch

# 修复强度 -> 邻域半径（和参考实现一致：radius = floor(strength / 2)，最小 1）
STRENGTH_MIN = 1
STRENGTH_MAX = 8
DEFAULT_STRENGTH = 5


def radius_of(strength):
    return max(1, int(strength) // 2)


KINDS = ("denoise", "fill", "revert", "brush")


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
    if len(h) != 7:
        return None
    try:
        int(h[1:], 16)
    except ValueError:
        return None
    return h


def _clean_points(raw):
    pts = []
    for p in (raw or []):
        try:
            x, y = float(p[0]), float(p[1])
        except (TypeError, ValueError, IndexError):
            continue
        if np.isfinite(x) and np.isfinite(y):
            pts.append((_clamp(x, -0.5, 1.5), _clamp(y, -0.5, 1.5)))
    return pts


def parse_repair(payload):
    """
    把前端传来的 repair 规范化成 {"ops": [...]}；没有有效内容返回 None。

    ops 的顺序有意义（后面的操作叠在前面的结果上），所以原样保留。
    """
    if not isinstance(payload, dict):
        return None
    r = payload.get("repair")
    if not isinstance(r, dict):
        return None

    ops = []
    for it in (r.get("ops") or []):
        if not isinstance(it, dict):
            continue
        kind = it.get("kind")
        if kind not in KINDS:
            continue

        if kind == "brush":
            hx = _clean_hex(it.get("hex"))
            if not hx:
                continue
            pts = _clean_points(it.get("points"))
            if not pts:
                continue
            try:
                rx = float(it.get("rx", 0.01))
                ry = float(it.get("ry", rx))
            except (TypeError, ValueError):
                continue
            if not (np.isfinite(rx) and np.isfinite(ry)):
                continue
            ops.append({"kind": "brush", "hex": hx,
                        "rx": _clamp(rx, 0.0002, 0.5),
                        "ry": _clamp(ry, 0.0002, 0.5),
                        "points": pts})
            continue

        lasso = _clean_points(it.get("lasso"))
        if len(lasso) < 3:
            continue

        if kind == "denoise":
            hx = _clean_hex(it.get("target"))
            if not hx:
                continue          # 没锁定目标就不做降噪
            try:
                st = int(it.get("strength", DEFAULT_STRENGTH))
            except (TypeError, ValueError):
                st = DEFAULT_STRENGTH
            ops.append({"kind": "denoise", "target": hx,
                        "strength": int(_clamp(st, STRENGTH_MIN, STRENGTH_MAX)),
                        "radius": radius_of(_clamp(st, STRENGTH_MIN, STRENGTH_MAX)),
                        "lasso": lasso})
        elif kind == "fill":
            hx = _clean_hex(it.get("hex"))
            if not hx:
                continue
            ops.append({"kind": "fill", "hex": hx, "lasso": lasso})
        elif kind == "revert":
            ops.append({"kind": "revert", "lasso": lasso})

    if not ops:
        return None
    return {"ops": ops}


# ---------------------------------------------------------------- 掩膜
def _polygon_mask(points, H, W):
    """归一化多边形 -> 布尔掩膜。用 PIL 光栅化，稳定且快。"""
    if len(points) < 3:
        return np.zeros((H, W), dtype=bool)
    img = Image.new("1", (W, H), 0)
    ImageDraw.Draw(img).polygon([(x * W, y * H) for x, y in points], fill=1)
    return np.asarray(img, dtype=bool)


def _line_blocks(x0, y0, x1, y1):
    """
    两个方块之间的整数直线（Bresenham）。

    快速拖动时相邻两个鼠标位置可能隔好几个方块，直接逐个落点会画成虚线，
    所以中间要补上。这是像素画软件画线的标准做法，不是「笔触」——
    补出来的每个点都精确落在方块格子上。
    """
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    while True:
        yield x0, y0
        if x0 == x1 and y0 == y1:
            return
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


def _brush_mask(points, rx, ry, H, W):
    """
    画笔轨迹 -> 布尔掩膜（**像素画逻辑**）。

    和上一版「沿轨迹盖一堆圆形笔头」不同，这里是：
      · 落点吸附到方块格子（`int(x*W)`），不做抗锯齿
      · 笔头是 N×N 的**方块**，以落点为中心，边缘是硬的
      · 相邻落点之间用 Bresenham 补线，快速拖动也不会断成虚线

    尺寸按两个分量给（各自按对应轴归一化）：rx 相对宽度、ry 相对高度。
    成品上一个方块是正方形，而预览可能和成品不同尺寸、甚至被 stretch 拉过，
    所以两个方向必须分开换算。
    """
    m = np.zeros((H, W), dtype=bool)
    if not points or H <= 0 or W <= 0:
        return m

    # 和前端保持一致：+0.5 再取整（JS 的 Math.round 是四舍五入，
    # Python 的 round 是银行家舍入，直接用 int(x+0.5) 对齐）
    sw = max(1, int(rx * W + 0.5))
    sh = max(1, int(ry * H + 0.5))
    ox = (sw - 1) // 2          # 让方块以落点为中心
    oy = (sh - 1) // 2

    pts = []
    for x, y in points:
        bx = int(x * W)
        by = int(y * H)
        bx = 0 if bx < 0 else (W - 1 if bx >= W else bx)
        by = 0 if by < 0 else (H - 1 if by >= H else by)
        if not pts or pts[-1] != (bx, by):
            pts.append((bx, by))

    centers = []
    if len(pts) == 1:
        centers = pts
    else:
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            centers.extend(_line_blocks(x0, y0, x1, y1))

    for bx, by in centers:
        x0 = bx - ox
        y0 = by - oy
        xa, xb = max(0, x0), min(W, x0 + sw)
        ya, yb = max(0, y0), min(H, y0 + sh)
        if xa < xb and ya < yb:
            m[ya:yb, xa:xb] = True
    return m


# ---------------------------------------------------------------- 邻域统计
def _box_sum(a, r):
    """
    (2r+1)×(2r+1) 窗口和，用积分图算，O(H*W)。
    a 是布尔数组，返回同形的 float64 数组（含中心点自身）。
    """
    H, W = a.shape
    P = np.zeros((H + 2 * r, W + 2 * r), dtype=np.int64)
    P[r:r + H, r:r + W] = a
    I = P.cumsum(0).cumsum(1)
    I = np.pad(I, ((1, 0), (1, 0)))
    k = 2 * r + 1
    return (I[k:, k:] - I[:-k, k:] - I[k:, :-k] + I[:-k, :-k]).astype(np.float64)


def _denoise_region(idx, mask, target, radius):
    """
    锁定目标方块的邻域多数替换。

    只处理 `mask` 内、且当前下标正好等于 `target` 的像素；
    替换成邻域 (2r+1)² 内出现次数最多的方块（不含中心点自己）。
    没找到更合适的（邻域里全是它自己）就不动。

    只在掩膜的包围盒里算 —— 圈一小块时开销和选区大小成正比，而不是整张图。
    """
    H, W = idx.shape
    ys, xs = np.nonzero(mask)
    if ys.size == 0:
        return idx, 0

    r = max(1, int(radius))
    y0 = max(0, int(ys.min()) - r)
    y1 = min(H, int(ys.max()) + r + 1)
    x0 = max(0, int(xs.min()) - r)
    x1 = min(W, int(xs.max()) + r + 1)

    sub = idx[y0:y1, x0:x1]
    sub_mask = mask[y0:y1, x0:x1]
    cand = sub_mask & (sub == target)
    if not cand.any():
        return idx, 0

    # 只用包围盒里出现过的颜色，省掉无关颜色的窗口统计
    present = np.unique(sub)
    best_cnt = np.zeros(sub.shape, dtype=np.float64)
    best_idx = np.full(sub.shape, target, dtype=np.int32)
    for c in present:
        c = int(c)
        s = _box_sum(sub == c, r)
        s -= (sub == c)                     # 参考实现里邻域不含中心点
        upd = s > best_cnt
        if upd.any():
            best_cnt[upd] = s[upd]
            best_idx[upd] = c

    hit = cand & (best_idx != target)
    n = int(hit.sum())
    if n:
        out = idx.copy()
        out[y0:y1, x0:x1][hit] = best_idx[hit]
        return out, n
    return idx, 0


# ---------------------------------------------------------------- 主逻辑
def _index_by_hex(pal, hexv):
    for i, g in enumerate(pal.groups):
        if g["hex"].upper() == hexv:
            return i
    return None


def apply_repair(idx, work_rgb, pal, repair, algo_key, tables=None):
    """
    把操作序列重放到抖动结果 `idx` 上。

    work_rgb: 送进抖动之前的像素 (H,W,3) uint8 —— 「还原」操作要用它重新匹配。
    pal:      本次请求的运行时调色板。
    返回 (新的 idx, 诊断信息 dict)。
    """
    info = {"applied": False, "ops": [], "op_count": 0,
            "denoised": 0, "filled": 0, "reverted": 0, "brush_pixels": 0,
            "targets": [], "warnings": []}
    if idx.size == 0 or pal.n == 0 or not repair:
        return idx, info

    H, W = idx.shape
    idx = idx.copy()
    work_flat = np.ascontiguousarray(work_rgb, dtype=np.uint8).reshape(-1, 3)

    for i, op in enumerate(repair["ops"]):
        kind = op["kind"]
        detail = {"i": i, "kind": kind}
        try:
            if kind == "brush":
                m = _brush_mask(op["points"], op["rx"], op["ry"], H, W)
                gi = _index_by_hex(pal, op["hex"])
                if gi is None:
                    info["warnings"].append("笔刷颜色 %s 不在本次调色板里" % op["hex"])
                else:
                    n = int(m.sum())
                    if n:
                        idx[m] = gi
                        info["brush_pixels"] += n
                    detail.update(hex=op["hex"], pixels=n)
            else:
                m = _polygon_mask(op["lasso"], H, W)
                if not m.any():
                    detail["pixels"] = 0
                elif kind == "denoise":
                    gi = _index_by_hex(pal, op["target"])
                    if gi is None:
                        info["warnings"].append(
                            "锁定目标 %s 不在本次调色板里" % op["target"])
                    else:
                        # 先数一下选区内有多少个目标方块（改之前数）
                        src_n = int((m & (idx == gi)).sum())
                        idx, n = _denoise_region(idx, m, gi, op["radius"])
                        info["denoised"] += n
                        if op["target"] not in info["targets"]:
                            info["targets"].append(op["target"])
                        detail.update(target=op["target"], radius=op["radius"],
                                      source=src_n, changed=n)
                elif kind == "fill":
                    gi = _index_by_hex(pal, op["hex"])
                    if gi is None:
                        info["warnings"].append(
                            "填充颜色 %s 不在本次调色板里" % op["hex"])
                    else:
                        n = int(m.sum())
                        idx[m] = gi
                        info["filled"] += n
                        detail.update(hex=op["hex"], pixels=n)
                elif kind == "revert":
                    rows = np.flatnonzero(m.reshape(-1))
                    if rows.size:
                        newv = match_batch(work_flat[rows], pal, algo_key, tables)
                        idx.reshape(-1)[rows] = newv.astype(np.int32)
                        info["reverted"] += int(rows.size)
                    detail["pixels"] = int(rows.size)
        except Exception as e:                              # noqa: BLE001
            info["warnings"].append("第 %d 个操作（%s）失败：%s" % (i, kind, e))
            detail["error"] = str(e)
        info["ops"].append(detail)

    info["op_count"] = len(repair["ops"])
    info["applied"] = bool(info["denoised"] or info["filled"]
                           or info["reverted"] or info["brush_pixels"])
    return idx, info


def public_info(info):
    """去掉内部字段，剩下可以进 JSON 的部分。"""
    return {k: v for k, v in info.items() if not k.startswith("_")}
