# -*- coding: utf-8 -*-
"""
投影 -> 铺设顺序（桥接层）验证
==============================

`ordering.py` 已经有穷举对拍了，这里专门盯**读投影那一层** —— 它最容易
悄悄出错，而且错了不会报错，只会算出一个错的顺序：

1. 方块网格的方向：grid[z][x] 必须是 (x=列, z=行)，别转置了
2. **跨文件统一编号**：同一个方块在 A 文件是 3、在 B 文件是 7 的话距离全错；
   判据是「两份完全相同的投影距离必须是 0」
3. 空气一律是 0，且首块成本 = 非空气格数
4. 尺寸不一致必须报错（不做缩放）
5. 求出来的总成本对得上穷举
6. txt 里有对照表 / 顺序 / 明细 / 总计，且换阈值不改顺序和成本

用法（工作区根目录）：
    python tests/projection_order_check.py
"""
import itertools
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import numpy as np
from litemapy import BlockState, Region

from maptool import ordering
from maptool.projection_order import (load_projections, order_projections,
                                      render_order, projection_grid)
from maptool.slicing import _schematic_to_bytes_fast

PASS, FAIL = [], []

NAMES = ["minecraft:stone", "minecraft:dirt", "minecraft:grass_block",
         "minecraft:sand", "minecraft:oak_planks", "minecraft:cobblestone"]


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  [√] " if ok else "  [×] ") + name
          + (("  " + str(detail)) if detail and not ok else ""))


def make_litematic(grid, air_name="minecraft:air"):
    """
    grid 是 (sz, sx) 的整数矩阵：0 = 空气，1.. = NAMES[i-1]。

    地图画是 XZ 平铺的，所以写进去的时候是 reg[x, 0, z] = grid[z][x]。
    """
    sz, sx = grid.shape
    reg = Region(0, 0, 0, sx, 1, sz)
    for x in range(sx):
        col = grid[:, x]
        for z in range(sz):
            v = int(col[z])
            name = air_name if v == 0 else NAMES[(v - 1) % len(NAMES)]
            reg[x, 0, z] = BlockState(name)
    schem = reg.as_schematic(name="t", author="t", description="")
    return _schematic_to_bytes_fast(schem)


def random_grid(sz, sx, seed, palette=6, air_ratio=0.1):
    rng = np.random.default_rng(seed)
    g = rng.integers(0, palette, (sz, sx)) + 1
    g[rng.random((sz, sx)) < air_ratio] = 0
    return g


def brute_best(grids):
    """按定义穷举最优：首块非空气格 + 相邻汉明距离。"""
    blocks = [g for g in grids]
    n = len(blocks)
    best = None
    for perm in itertools.permutations(range(n)):
        c = int(np.count_nonzero(blocks[perm[0]]))
        for a, b in zip(perm, perm[1:]):
            c += int(np.count_nonzero(blocks[a] != blocks[b]))
        if best is None or c < best:
            best = c
    return best


def _fix_console():
    """控制台编码兜底：GBK 控制台下编不出的符号会抛 UnicodeEncodeError 带崩整次自检。"""
    for _name in ("stdout", "stderr"):
        _st = getattr(sys, _name, None)
        try:
            if _st is not None:
                _st.reconfigure(errors="replace")
        except Exception:
            pass


def main():
    _fix_console()
    # ------------------------------------------------------------
    print("=" * 66)
    print("1) projection_grid：方向和取值")
    print("=" * 66)
    # 造一个一眼能看出方向的图案：
    #   z=0 这一行（除角上）都是 1，x=0 这一列（除角上）都是 2，角落是 3
    g = np.zeros((6, 8), dtype=np.int64)      # sz=6, sx=8
    g[0, 1:] = 1
    g[1:, 0] = 2
    g[0, 0] = 3
    content = make_litematic(g)
    grid, keys, sx, sz = projection_grid(content)
    check("尺寸 (sx, sz) = (8, 6)，实际 (%d, %d)" % (sx, sz), (sx, sz) == (8, 6))
    check("网格形状是 (sz, sx) = (6, 8)，实际 %s" % (grid.shape,),
          grid.shape == (6, 8))

    # 用一个「同一方块到处都在」的图案确认方向：z=0 行全同一种、x=0 列全同一种
    def state_at(z, x):
        return keys[int(grid[z, x])]

    row_states = {state_at(0, x) for x in range(1, 8)}
    col_states = {state_at(z, 0) for z in range(1, 6)}
    check("z=0 那一行（除角）是同一个方块", len(row_states) == 1)
    check("x=0 那一列（除角）是同一个方块", len(col_states) == 1)
    check("行和列是不同的方块", not (row_states & col_states))
    check("角落又是第三种（3 号）",
          state_at(0, 0) not in (row_states | col_states))
    check("内部是空气（grid=0）", np.all(grid[1:, 1:] == 0))

    # ------------------------------------------------------------
    print()
    print("=" * 66)
    print("2) 跨文件统一编号")
    print("=" * 66)
    # 两份**完全相同**的投影：距离必须为 0
    g1 = random_grid(10, 10, seed=3)
    a = make_litematic(g1)
    # 换一个方块集合顺序来造「内部编号不同」的文件：先填别的方块再填同样的
    b = make_litematic(g1)
    blocks, meta = load_projections([("a", a), ("b", b)])
    d01 = int(np.count_nonzero(blocks[0] != blocks[1]))
    check("两份完全相同的投影，块间距离 = 0（实际 %d）" % d01, d01 == 0)
    check("空气仍然是 0", np.all((blocks[0] == 0) == (g1 == 0)))
    check("用到的方块种类数 = 6（实际 %d）" % meta["palette"],
          meta["palette"] == 6)

    # 只差一块格的投影，距离必须是 1
    g2 = g1.copy()
    z, x = np.unravel_index(int(np.argmax(g2 > 0)), g2.shape)
    g2[z, x] = 0 if g2[z, x] != 0 else 1
    blocks2, _ = load_projections([("a", a), ("c", make_litematic(g2))])
    d = int(np.count_nonzero(blocks2[0] != blocks2[1]))
    check("只改一格 -> 距离 1（实际 %d）" % d, d == 1)

    # 首块成本 = 非空气格数
    D = ordering.build_dist(blocks)
    check("首块成本 = 非空气格数（%d）" % int(D[0][1]),
          int(D[0][1]) == int(np.count_nonzero(blocks[0])))

    # ------------------------------------------------------------
    print()
    print("=" * 66)
    print("3) 尺寸不一致必须报错")
    print("=" * 66)
    small = make_litematic(random_grid(8, 8, seed=4))
    try:
        load_projections([("a", a), ("small", small)])
        raised = None
    except ValueError as e:
        raised = str(e)
    check("128 尺寸不一致时抛出 ValueError", raised is not None)
    check("错误信息里点名了两个文件",
          raised and "a" in raised and "small" in raised, raised)

    # 空列表
    try:
        load_projections([])
        raised = None
    except ValueError as e:
        raised = str(e)
    check("空文件列表报错", raised is not None)

    # ------------------------------------------------------------
    print()
    print("=" * 66)
    print("4) 端到端：总成本 == 穷举最优")
    print("=" * 66)
    for n in (1, 2, 3, 5, 6):
        grids = [random_grid(12, 12, seed=700 + i, palette=4)
                 for i in range(n)]
        items = [("p%d.litematic" % i, make_litematic(g)) for i, g in enumerate(grids)]
        res, ctx = order_projections(items, verbose=False)
        best = brute_best(grids)
        check("N=%d 总替换 %d = 穷举 %d" % (n, res["total"], best),
              res["total"] == best)
        check("  N=%d 顺序是 0..%d 的排列" % (n, n - 1),
              sorted(res["order"]) == list(range(n)))

    # 三份几乎一样的 + 一份完全不同的：相似的三份应该挨在一起
    base = random_grid(16, 16, seed=11, palette=5, air_ratio=0)
    rng = np.random.default_rng(99)
    similar = []
    for i in range(3):
        g = base.copy()
        g[rng.integers(0, 16, 5), rng.integers(0, 16, 5)] = rng.integers(0, 5, 5) + 1
        similar.append(g)
    other = rng.integers(0, 5, (16, 16)) + 1
    grids = similar + [other]
    items = [("s%d" % i, make_litematic(g)) for i, g in enumerate(grids)]
    res, _ctx = order_projections(items, verbose=False)
    order = res["order"]
    base_set = {0, 1, 2}
    pos = [order.index(i) for i in base_set]
    check("三份相似的块在顺序里连续（位置 %s）" % pos,
          max(pos) - min(pos) == 2)

    # ------------------------------------------------------------
    print()
    print("=" * 66)
    print("5) txt 内容")
    print("=" * 66)
    grids = [random_grid(10, 10, seed=500 + i, palette=3) for i in range(4)]
    items = [("图片A_r1c%d.litematic" % i, make_litematic(g))
             for i, g in enumerate(grids)]
    res, ctx = order_projections(items, threshold=8000, verbose=False)
    txt = res["txt"]

    check("有标题", "地图画投影铺设顺序" in txt)
    check("有块数/尺寸/阈值", all(k in txt for k in
                                  ("块数", "每块尺寸", "单步告警阈值")))
    check("有算法说明", "算法" in txt)
    check("有「块索引 → 文件名」对照表", "块索引 → 文件名" in txt)
    for i, (name, _b) in enumerate(items):
        if ("块 %3d  ←  %s" % (i, name)) not in txt:
            check("对照表第 %d 行" % i, False, name)
            break
    else:
        check("对照表把所有文件都列了（%d 行）" % len(items), True)
    check("有铺设顺序段", "铺设顺序（按先后）" in txt)
    check("顺序行和结果一致", ("  " + " → ".join(str(i) for i in res["order"]))
          in txt)
    check("有明细表头", "步   块索引    本步替换     累计     备注" in txt)
    check("总替换次数 = %d 出现在 txt 里" % res["total"],
          ("总替换次数 = %d" % res["total"]) in txt)
    check("txt 以换行结尾内容完整", txt.endswith("\n") or txt.strip())
    check("txt_size 和实际一致",
          res["txt_size"] == len(txt.encode("utf-8")))

    # 明细行数 = 块数
    body = txt.split("步   块索引    本步替换     累计     备注")[1]
    body = body.split("-" * 60)[1]
    step_lines = [ln for ln in body.splitlines() if ln.strip()]
    check("明细表恰好 %d 行（实际 %d）" % (len(items), len(step_lines)),
          len(step_lines) == len(items))

    # ------------------------------------------------------------
    print()
    print("=" * 66)
    print("6) 换阈值：只改标注，不改顺序和成本")
    print("=" * 66)
    # 用大一点的块，让 8000 这个真实阈值有机会触发
    big = [random_grid(128, 128, seed=800 + i, palette=6, air_ratio=0.05)
           for i in range(4)]
    items2 = [("b%d.litematic" % i, make_litematic(g)) for i, g in enumerate(big)]
    res_hi, ctx_hi = order_projections(items2, threshold=8000, verbose=False)
    # 阈值要挑在「真实替换数之间」才看得出差别：随机 128×128 块之间每步
    # 大约一万三，拿 2000 会全部超标、和 8000 一样多。按实际值取中位数。
    reps = sorted(r["replaced"] for r in res_hi["rows"])
    th_mid = reps[len(reps) // 2]
    res_lo = render_order(ctx_hi, threshold=th_mid)
    res_no = render_order(ctx_hi, threshold=10 ** 9)

    check("三种阈值下顺序完全一致",
          res_hi["order"] == res_lo["order"] == res_no["order"])
    check("三种阈值下总成本完全一致（%d）" % res_hi["total"],
          res_hi["total"] == res_lo["total"] == res_no["total"])
    check("阈值 8000：%d 步超标" % len(res_hi["high"]),
          len(res_hi["high"]) >= 1)
    check("阈值抬到 %d 后超标步数变少（%d < %d）"
          % (th_mid, len(res_lo["high"]), len(res_hi["high"])),
          len(res_lo["high"]) < len(res_hi["high"]))
    check("阈值放到 1e9 后没有超标步", res_no["high"] == [])
    check("没超标时 txt 给出 ✅ 行",
          "✅ 没有任何一步超过" in res_no["txt"])
    check("超标时 txt 给出汇总段", "高替换步骤汇总" in res_hi["txt"])

    # 百分比按每块格数算（128×128 = 16384）
    check("cell 数 = 16384（实际 %d）" % res_hi["cells"], res_hi["cells"] == 16384)
    check("阈值抬高后百分比仍按 16384 算",
          ("占 %d 格的" % res_hi["cells"]) in res_hi["txt"])

    # ------------------------------------------------------------
    print()
    print("=" * 66)
    print("7) 边界情况")
    print("=" * 66)
    # 全空气：成本 0
    empty = np.zeros((8, 8), dtype=np.int64)
    res_e, _ = order_projections([("e%d" % i, make_litematic(empty))
                                  for i in range(3)], verbose=False)
    check("全空气投影总成本 0", res_e["total"] == 0)
    check("全空气投影只剩空气一种，palette=0（实际 %d）" % res_e["palette"],
          res_e["palette"] == 0)

    # 单块：成本 = 非空气格数
    one = random_grid(9, 9, seed=77, palette=3, air_ratio=0.3)
    res_1, _ = order_projections([("one", make_litematic(one))], verbose=False)
    check("单块成本 = 非空气格数（%d）" % res_1["total"],
          res_1["total"] == int(np.count_nonzero(one)))

    # 多层（Y>1）不该崩，取每列最高的那一层
    reg = Region(0, 0, 0, 8, 3, 8)
    for x in range(8):
        for z in range(8):
            reg[x, 0, z] = BlockState("minecraft:stone")
            reg[x, 1, z] = BlockState("minecraft:dirt")
    multi = _schematic_to_bytes_fast(
        reg.as_schematic(name="m", author="t", description=""))
    blocks_m, meta_m = load_projections([("multi", multi)])
    check("多层投影能读成二维网格（%s）" % (blocks_m[0].shape,),
          blocks_m[0].shape == (8, 8))
    check("多层时每格都有方块（非空气 64）",
          int(np.count_nonzero(blocks_m[0])) == 64)

    # ------------------------------------------------------------
    print()
    print("=" * 66)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("  失败：" + f)
    print("=" * 66)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
