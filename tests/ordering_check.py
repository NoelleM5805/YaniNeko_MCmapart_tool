# -*- coding: utf-8 -*-
"""
铺设顺序优化器：正确性验证
==========================

这个模块算的是「最短路」，错了不会报错、只会给出一个更贵的顺序，
所以必须拿**穷举**当基准：

1. `build_dist` 的每一项和逐格暴力比对
2. `path_cost` 和手算比对
3. 状压 DP 和全排列穷举比对（N ≤ 7）
4. 贪心 / 2-opt / Or-opt 单调不劣，且路径始终是合法排列
5. `report` 的替换数、累计、⚠️ 标注、汇总行都对
6. 阈值只影响标注，不影响路径（同输入换阈值，顺序必须一模一样）

用法（工作区根目录）：
    python tests/ordering_check.py
"""
import itertools
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "src", "maptool"))

import numpy as np

from maptool import ordering as O

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  [√] " if ok else "  [×] ") + name
          + (("  " + str(detail)) if detail and not ok else ""))


def brute_cost(blocks, order, air_id=0):
    """按定义直接算：首块非空气格数 + 相邻汉明距离之和。"""
    total = int(np.count_nonzero(blocks[order[0]] != air_id))
    for a, b in zip(order, order[1:]):
        total += int(np.count_nonzero(blocks[a] != blocks[b]))
    return total


def brute_best(blocks, air_id=0):
    """全排列穷举最优（只适合很小规模）。"""
    n = len(blocks)
    best = None
    best_order = None
    for perm in itertools.permutations(range(n)):
        c = brute_cost(blocks, perm, air_id)
        if best is None or c < best:
            best, best_order = c, list(perm)
    return best, best_order


def make_blocks(n, side=8, palette=5, seed=1, air_ratio=0.15):
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n):
        b = rng.integers(0, palette, size=(side, side), dtype=np.int32) + 1
        mask = rng.random((side, side)) < air_ratio
        b[mask] = 0
        out.append(b)
    return out


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
    print("1) 距离矩阵：和逐格暴力比对")
    print("=" * 66)
    blocks = make_blocks(6, side=8, seed=3)
    D = O.build_dist(blocks)
    n = len(blocks)
    ok_shape = D.shape == (n + 1, n + 1)
    check("形状是 (N+1, N+1) = %s" % (D.shape,), ok_shape)

    bad = []
    for i in range(n):
        want0 = int(np.count_nonzero(blocks[i]))
        if int(D[0][i + 1]) != want0 or int(D[i + 1][0]) != want0:
            bad.append(("0<->%d" % i, int(D[0][i + 1]), want0))
        for j in range(n):
            want = int(np.sum(blocks[i] != blocks[j]))
            if int(D[i + 1][j + 1]) != want:
                bad.append(("%d<->%d" % (i, j), int(D[i + 1][j + 1]), want))
    check("%d 项（含首块成本、对角 0）全部一致" % (n + n * n), not bad, bad[:3])

    check("D 对称", np.array_equal(D, D.T))
    check("对角线全 0", np.all(np.diag(D) == 0))
    check("D 非负", np.all(D >= 0))

    # 空气 ID 不是 0 时也要对
    blocks2 = [b + 5 for b in make_blocks(4, side=6, seed=9)]
    blocks2[0][blocks2[0] == 5] = -1        # 用 -1 当空气
    D2 = O.build_dist(blocks2, air_id=-1)
    want0 = int(np.count_nonzero(blocks2[0] != -1))
    check("air_id != 0 时首块成本正确（%d）" % int(D2[0][1]),
          int(D2[0][1]) == want0)

    # ------------------------------------------------------------
    print()
    print("=" * 66)
    print("2) path_cost")
    print("=" * 66)
    # 手搓一个小矩阵，路径成本一眼能算出来
    Dm = np.array([
        [0, 10, 20, 30],
        [10, 0, 5, 40],
        [20, 5, 0, 7],
        [30, 40, 7, 0],
    ], dtype=np.int64)
    check("手算 [0,1,2,3] = 22（10+5+7）",
          O.path_cost(Dm, [0, 1, 2, 3]) == 22)
    check("手算 [0,3,2,1] = 42（30+7+5）",
          O.path_cost(Dm, [0, 3, 2, 1]) == 42)
    check("单节点路径成本 0", O.path_cost(Dm, [0]) == 0)

    # ------------------------------------------------------------
    print()
    print("=" * 66)
    print("3) 状压 DP：和全排列穷举比对（N ≤ 7）")
    print("=" * 66)
    for n in range(1, 8):
        blk = make_blocks(n, side=5, palette=4, seed=100 + n)
        Dn = O.build_dist(blk)
        path, cost = O.exact_dp(Dn)
        best, best_order = brute_best(blk)
        # DP 的路径也要真的能算出这个成本
        ok_perm = sorted(path[1:]) == list(range(1, n + 1)) and path[0] == 0
        check("N=%d DP 最优 %d = 穷举最优 %d" % (n, cost, best),
              cost == best and ok_perm and O.path_cost(Dn, path) == cost)
        if n <= 5:
            check("  N=%d DP 顺序与穷举同价（DP %s）"
                  % (n, [p - 1 for p in path[1:]]),
                  brute_cost(blk, [p - 1 for p in path[1:]]) == best)

    # ------------------------------------------------------------
    print()
    print("=" * 66)
    print("4) 贪心 / 2-opt / Or-opt：单调不劣 + 合法排列")
    print("=" * 66)
    for n, side in ((6, 6), (12, 8), (20, 6), (40, 4)):
        blk = make_blocks(n, side=side, palette=6, seed=200 + n)
        Dn = O.build_dist(blk)

        g = O.greedy_best(Dn)
        cg = O.path_cost(Dn, g)
        t = O.two_opt(Dn, g)
        ct = O.path_cost(Dn, t)
        r = O.or_opt(Dn, t)
        cr = O.path_cost(Dn, r)

        ok_perm = (all(sorted(p[1:]) == list(range(1, n + 1)) and p[0] == 0
                       for p in (g, t, r)))
        check("N=%-3d 贪心 %-7d → 2-opt %-7d → Or-opt %-7d"
              % (n, cg, ct, cr), ok_perm and ct <= cg and cr <= ct)
        # 2-opt 之后必须已经是 2-opt 局部最优：再跑一次不该有变化
        t2 = O.two_opt(Dn, t)
        check("  N=%d 再跑一次 2-opt 不再变化" % n,
              O.path_cost(Dn, t2) == ct)

    # 2-opt 不能把虚拟节点 0 翻到后面去
    blk = make_blocks(10, side=6, seed=77)
    Dn = O.build_dist(blk)
    t = O.two_opt(Dn, O.greedy_best(Dn))
    check("2-opt 后虚拟节点仍在首位（path[0]=%d）" % t[0], t[0] == 0)
    r = O.or_opt(Dn, t)
    check("Or-opt 后虚拟节点仍在首位（path[0]=%d）" % r[0], r[0] == 0)

    # ------------------------------------------------------------
    print()
    print("=" * 66)
    print("5) report：数字 / 累计 / ⚠️ 标注 / 汇总")
    print("=" * 66)
    blk = make_blocks(5, side=10, palette=4, seed=31, air_ratio=0.0)
    Dn = O.build_dist(blk)
    path = [0, 1, 2, 3, 4, 5]

    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        total, rows = O.report(Dn, path, threshold=O.THRESHOLD)
    finally:
        sys.stdout = old
    text = buf.getvalue()

    want_total = brute_cost(blk, [0, 1, 2, 3, 4])
    check("返回的总数和暴力算的一致（%d）" % total, total == want_total)
    check("明细行数 = 块数（%d）" % len(rows), len(rows) == 5)

    # 累计列 = 前缀和
    cum_ok = all(r["cum"] == sum(x["replaced"] for x in rows[:r["step"]])
                 for r in rows)
    check("累计列是逐步前缀和", cum_ok)

    # 每步替换 = 对应距离
    rep_ok = True
    for r in rows:
        if r["step"] == 1:
            exp = int(Dn[0][r["block"] + 1])
        else:
            exp = int(Dn[r["prev"] + 1][r["block"] + 1])
        if r["replaced"] != exp:
            rep_ok = False
    check("每步替换数 = 对应距离矩阵项", rep_ok)

    check("阈值 8000、单步最大仅 %d -> 输出 ✅ 行"
          % max(r["replaced"] for r in rows),
          "✅ 没有任何一步超过 8000" in text)
    check("没有超过阈值时 rows 里没有 over",
          not any(r["over"] for r in rows))

    # 故意把阈值压低，验证 ⚠️ 标注和汇总
    buf = io.StringIO()
    sys.stdout = buf
    try:
        total2, rows2 = O.report(Dn, path, threshold=10)
    finally:
        sys.stdout = old
    text2 = buf.getvalue()
    check("阈值 10 时总数不变（%d）" % total2, total2 == total)
    check("压低阈值后出现 ⚠️ 汇总行", "⚠️ 高替换步骤汇总（>10）" in text2)
    n_over = sum(1 for r in rows2 if r["over"])
    check("汇总里写的步数和实际超阈步数一致（%d）" % n_over,
          ("共 %d 步" % n_over) in text2)
    check("首块超阈值时用的是「首块非空气格」措辞",
          "首块非空气格" in text2)
    check("普通步用的是「块A → 块B」措辞",
          ("（块%d → 块%d）" % (rows2[1]["prev"], rows2[1]["block"])) in text2)
    check("百分比按 16384 格算",
          "占 16384 格的" in text2)

    # ------------------------------------------------------------
    print()
    print("=" * 66)
    print("6) 阈值只影响标注，不影响路径")
    print("=" * 66)
    blk_big = make_blocks(16, side=8, seed=555)
    # 8×8 的块最多替换 64 格，阈值要按这个量级选，否则永远不会触发
    r1 = O.solve(blk_big, threshold=60, verbose=False)
    r2 = O.solve(blk_big, threshold=20, verbose=False)
    r3 = O.solve(blk_big, threshold=10 ** 9, verbose=False)
    check("阈值 60 / 20 / 1e9 得到同一条顺序",
          r1["path"] == r2["path"] == r3["path"])
    check("阈值变化时总成本不变（%d）" % r1["total"],
          r1["total"] == r2["total"] == r3["total"])
    check("阈值 1e9 时没有任何 ⚠️",
          not any(r["over"] for r in r3["rows"]))
    check("阈值 20 时比 60 有更多 ⚠️（%d > %d）"
          % (sum(r["over"] for r in r2["rows"]),
             sum(r["over"] for r in r1["rows"])),
          sum(r["over"] for r in r2["rows"])
          > sum(r["over"] for r in r1["rows"]))

    # ------------------------------------------------------------
    print()
    print("=" * 66)
    print("7) solve 端到端")
    print("=" * 66)
    # 小规模走 DP，必须等于穷举
    for n in (1, 2, 3, 6):
        blk = make_blocks(n, side=6, seed=900 + n)
        res = O.solve(blk, verbose=False)
        best, _ = brute_best(blk)
        check("N=%d solve 总成本 %d = 穷举 %d" % (n, res["total"], best),
              res["total"] == best)
        check("  N=%d 路径是 0..%d 的排列" % (n, n - 1),
              sorted(res["path"]) == list(range(n)))
        check("  N=%d report 总数 == 路径成本" % n,
              res["total"] == O.path_cost(res["D"],
                                          [0] + [p + 1 for p in res["path"]]))

    # 中等规模走启发式，至少不能比贪心差
    blk = make_blocks(25, side=8, seed=4242)
    res = O.solve(blk, verbose=False)
    Dn = res["D"]
    g = O.greedy_best(Dn)
    check("N=25 启发式 %d ≤ 纯贪心 %d" % (res["total"], O.path_cost(Dn, g)),
          res["total"] <= O.path_cost(Dn, g))
    check("N=25 路径合法",
          sorted(res["path"]) == list(range(25)))
    check("N=25 用的是启发式（%s）" % res["method"][:24],
          "贪心" in res["method"])

    # 块数 > OR_OPT_MAX 时应该跳过 Or-opt
    blk = make_blocks(8, side=4, seed=1)
    res = O.solve(blk, verbose=False, or_opt_max=0, exact_max=0)
    check("or_opt_max=0 时跳过 Or-opt",
          "跳过 Or-opt" in res["method"])

    # 结果可复现
    a = O.solve(blk, verbose=False)
    b = O.solve(blk, verbose=False)
    check("同输入两次结果完全相同", a["path"] == b["path"]
          and a["total"] == b["total"])

    # 完全相同的块：任意顺序成本一样，不该崩
    same = [make_blocks(1, side=6, seed=5)[0].copy() for _ in range(4)]
    res = O.solve(same, verbose=False)
    check("4 个完全相同的块：总成本 %d = 首块非空气格 %d"
          % (res["total"], int(np.count_nonzero(same[0]))),
          res["total"] == int(np.count_nonzero(same[0])))

    # 全空气：成本 0
    air = [np.zeros((4, 4), dtype=np.int32) for _ in range(3)]
    res = O.solve(air, verbose=False)
    check("全空气块总成本 0", res["total"] == 0)

    # 尺寸不一致要报错
    try:
        O.build_dist([np.zeros((4, 4)), np.zeros((4, 5))])
        raised = False
    except ValueError as e:
        raised = "不一致" in str(e)
    check("尺寸不一致时报 ValueError", raised)

    # 非法路径要报错
    blk = make_blocks(3, side=4, seed=2)
    Dn = O.build_dist(blk)
    for bad_path, why in (([1, 0, 2, 3], "首位不是 0"),
                          ([0, 1, 1, 2], "重复节点")):
        try:
            O.report(Dn, bad_path, verbose=False)
            raised = False
        except ValueError:
            raised = True
        check("report 拒绝非法路径（%s）" % why, raised)

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
