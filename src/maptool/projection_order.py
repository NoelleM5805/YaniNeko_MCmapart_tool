# -*- coding: utf-8 -*-
"""
投影 -> 铺设顺序（ordering）的桥接层
====================================

`ordering.py` 只认 `list[np.ndarray]`，不碰 litematic。这里负责三件事：

1. **读**：把 `.litematic` 读成 (Z, X) 的整数方块网格。空气 = 0，
   其余方块按「全集合内第一次出现」编号 —— 注意是**跨文件统一编号**，
   否则同一个方块在 A 文件里是 3、在 B 文件里是 7，距离就全错了。
2. **算**：调用 `ordering.solve()`。
3. **出 txt**：把明细表 + 「块索引 → 文件名」对照表拼成可施工的纯文本。

关键约束：**所有投影必须同样大小**。切出来的块要能逐格对齐才谈得上
「替换几次」，尺寸不一致直接报错，不做任何缩放（缩放会凭空造出替换）。
另外一律按投影的**完整包围盒**取网格，不裁到内容范围 —— 裁了以后不同
文件的留白位置会不一样，同样是对不齐。

`render_order()` 单独拆出来，是为了**只改阈值时不用重新上传、重新求解**：
把求解结果（距离矩阵 + 路径）留在任务里，换阈值只重渲染 txt。
"""
from __future__ import annotations

import time

import numpy as np

from . import ordering
from .slicing import _SourceIndex, _load_schematic, _state_key


# ============================================================
# 读：litematic -> 方块网格
# ============================================================
def projection_grid(content):
    """
    把一个 .litematic 读成 (grid, keys, sx, sz)。

        grid   形状 (sz, sx) 的 int32，值是 key 下标，0 = 空气
        keys   keys[i] 是方块状态的标识（方块 id + 方块状态属性）
        sx, sz 包围盒尺寸

    只看 XZ 平面：地图画是平铺的，Y 方向有多个图层时取「最高那层」的方块
    （`arr.max(axis=1)`），这样多层投影也能排。
    """
    schem = _load_schematic(content)
    src = _SourceIndex(schem)
    sx, sz = src.sx, src.sz

    # src.states 里 0 号是 None（空气），其余是 BlockState
    keys = [None] + [_state_key(s) for s in src.states[1:]]

    grid = np.zeros((sz, sx), dtype=np.int32)
    for _reg, arr, ox, oz, _step in src.index_arrays():
        # arr 是 (x, y, z)；压成 (x, z) 再转成画布需要的 (z, x)
        flat = arr[:, 0, :] if arr.shape[1] == 1 else arr.max(axis=1)
        if flat.shape[0] == 0 or flat.shape[1] == 0:
            continue
        arr2 = flat.T                      # (z, x)
        tx, tz = ox, oz                    # 已相对包围盒原点
        sxa = max(0, -tx); sza = max(0, -tz)
        txa = max(0, tx); tza = max(0, tz)
        nx = min(arr2.shape[1] - sxa, sx - txa)
        nz = min(arr2.shape[0] - sza, sz - tza)
        if nx <= 0 or nz <= 0:
            continue
        grid[tza:tza + nz, txa:txa + nx] = arr2[sza:sza + nz, sxa:sxa + nx]
    return grid, keys, sx, sz


def load_projections(items):
    """
    批量读投影，统一编号后返回 (blocks, meta)。

        items  [(文件名, litematic 字节), ...]

        blocks  list[np.ndarray] 每个 (sz, sx) int32，0 = 空气
        meta    {"sx","sz","cells","palette","count","names"}

    对所有投影统一建立 key -> 1..K 的编号，保证同一个方块在任何一块里
    都是同一个整数。
    """
    if not items:
        raise ValueError("没有可用的投影文件")

    parsed = []
    for name, content in items:
        try:
            grid, keys, sx, sz = projection_grid(content)
        except Exception as e:
            raise ValueError("读不出「%s」：%s" % (name, e))
        parsed.append((name, grid, keys, sx, sz))

    sizes = {(p[3], p[4]) for p in parsed}
    if len(sizes) > 1:
        detail = "、".join("%s=%d×%d" % (p[0], p[3], p[4]) for p in parsed[:6])
        raise ValueError(
            "投影尺寸不一致（%s）。切分出来的块必须同样大小才能算替换次数"
            % detail)

    sx, sz = parsed[0][3], parsed[0][4]
    if sx <= 0 or sz <= 0:
        raise ValueError("投影尺寸无效：%d×%d" % (sx, sz))

    # 跨所有文件统一编号：key -> 1..K
    index = {}
    blocks = []
    for _name, grid, keys, _sx, _sz in parsed:
        flat = grid.reshape(-1)
        out = np.zeros(flat.size, dtype=np.int32)
        for k in np.unique(flat):
            k = int(k)
            if k == 0:
                continue                      # 空气保持 0
            key = keys[k]
            gi = index.get(key)
            if gi is None:
                gi = len(index) + 1
                index[key] = gi
            out[flat == k] = gi
        blocks.append(out.reshape(sz, sx))

    return blocks, {
        "sx": sx, "sz": sz, "cells": sx * sz,
        "palette": len(index), "count": len(blocks),
        "names": [p[0] for p in parsed],
    }


# ============================================================
# 上下文：把「求解结果」留下来，之后换阈值只重渲染
# ============================================================
def make_context(D, raw_path, method, meta, source):
    return {
        "D": D,
        "raw_path": [int(x) for x in raw_path],
        "method": method,
        "meta": meta,
        "source": source,
    }


def _txt_header(ctx, threshold):
    meta = ctx["meta"]
    names = meta["names"]
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "地图画投影铺设顺序",
        "=" * 40,
        "生成时间    ：%s" % now,
        "数据来源    ：%s" % ctx["source"],
        "块数        ：%d" % meta["count"],
        "每块尺寸    ：%d × %d（%d 格）" % (meta["sx"], meta["sz"], meta["cells"]),
        "用到的方块  ：%d 种" % meta["palette"],
        "单步告警阈值：%d 格（只影响标注，不影响优化目标）" % threshold,
        "",
        "算法        ：%s" % ctx["method"],
        "",
        "块索引 → 文件名（块索引就是下面表格里的编号，0 起）",
        "-" * 60,
    ]
    for i, name in enumerate(names):
        lines.append("  块 %3d  ←  %s" % (i, name))
    lines.append("")
    lines.append("铺设顺序（按先后）")
    lines.append("-" * 60)
    return lines


def render_order(ctx, threshold=ordering.THRESHOLD):
    """
    用已求解的上下文渲染一份结果（txt + 概要），换阈值时调用。

    返回 dict（可直接当接口响应的主体）。
    """
    meta = ctx["meta"]
    cells = meta["cells"]
    header = _txt_header(ctx, threshold)
    order = [int(p) - 1 for p in ctx["raw_path"][1:]]
    header.append("  " + " → ".join("%d" % i for i in order))

    text, total, rows = ordering.render_report(
        ctx["D"], ctx["raw_path"], threshold=threshold,
        cells=cells, header=header)

    high = [{
        "step": r["step"],
        "prev": r["prev"],
        "block": r["block"],
        "replaced": r["replaced"],
        "pct": round(100.0 * r["replaced"] / cells, 1) if cells else 0.0,
    } for r in rows if r["over"]]

    return {
        "total": total,
        "order": order,
        "order_text": " → ".join(str(i) for i in order),
        "rows": rows,
        "high": high,
        "method": ctx["method"],
        "count": meta["count"],
        "sx": meta["sx"], "sz": meta["sz"], "cells": cells,
        "palette": meta["palette"],
        "threshold": threshold,
        "names": meta["names"],
        "txt": text,
        "txt_name": "投影铺设顺序_%d块.txt" % meta["count"],
        "txt_size": len(text.encode("utf-8")),
    }


def order_projections(items, threshold=ordering.THRESHOLD, source="上传的投影",
                      verbose=False):
    """
    读投影 -> 求最优顺序 -> 渲染结果。

    返回 (result, ctx)。ctx 留着换阈值重渲染用。
    """
    blocks, meta = load_projections(items)
    res = ordering.solve(blocks, threshold=threshold, verbose=verbose)
    ctx = make_context(res["D"], res["raw_path"], res["method"], meta, source)
    out = render_order(ctx, threshold=threshold)
    if out["total"] != res["total"]:
        raise AssertionError("txt 总成本 %d 与求解结果 %d 不一致"
                             % (out["total"], res["total"]))
    return out, ctx
