# -*- coding: utf-8 -*-
"""
我的世界地图画投影「铺设顺序优化器」
====================================

前半段（图片 -> 整块投影 -> 切成 128×128 的小块）已经做完了，这里解决后半段：

    给定 N 块 128×128 的方块图，求「从空地开始，逐块铺过去」的
    最小总替换次数，以及对应的铺设顺序。

问题建模
--------
引入**虚拟节点 0 = 空地**，其余节点 1..N 对应 blocks[0..N-1]：

    D[0][i] = D[i][0] = 第 i 块的**非空气格数**（从空地铺首块，全都要放）
    D[i][j] = D[j][i] = 块 i 与块 j 的**汉明距离**（同位置不同方块 ID 的格数）
    D[i][i] = 0

于是原问题 = 在 N+1 个节点的完全图上，找一条**从 0 出发、每个节点恰好访问
一次**的最小成本路径（对称 TSP 路径问题，起点固定为 0）。

索引约定：路径里 0 是空地节点，**块索引 = 路径元素 - 1**。

算法
----
    N ≤ 15   ->  状压 DP（Held-Karp），精确最优
    N > 15   ->  多起点贪心最近邻  ->  2-opt  ->  Or-opt（N ≤ 100 时）

8000 阈值
---------
8000 只是**输出层面的告警阈值**（一个 128×128 块有 16384 格，8000 约等于
一半），用来提醒「这一刀要重铺一半以上，能不能换个顺序」。

它**不参与优化目标**：总成本始终只最小化 Σ替换次数，绝不会为了压低单步
最大值而牺牲总成本。
"""
from __future__ import annotations

import time

import numpy as np

# ============================================================
# 常量
# ============================================================
TILE = 128
TILE_CELLS = TILE * TILE      # 16384
THRESHOLD = 8000              # 单步替换告警阈值

EXACT_MAX = 15                # ≤ 这个规模用状压 DP 求精确解
OR_OPT_MAX = 100              # > 这个规模跳过 Or-opt（太慢）
GREEDY_ALL_STARTS_MAX = 200   # > 这个规模只从「首块便宜」的若干个起跑

TWO_OPT_MAX_ITER = 5000
OR_OPT_MAX_ITER = 200

INF = np.int64(1) << 60


# ============================================================
# 1. 距离矩阵
# ============================================================
def _as_matrix(blocks):
    """把 list[(h, w)] 摊平成 (N, h*w) 的 int32 矩阵，顺便校验尺寸一致。"""
    if blocks is None or len(blocks) == 0:
        raise ValueError("blocks 不能为空")
    mats = []
    shape0 = None
    for i, b in enumerate(blocks):
        a = np.asarray(b)
        if a.ndim != 2:
            raise ValueError("第 %d 块应该是二维数组，实际 shape=%r" % (i, a.shape))
        if shape0 is None:
            shape0 = a.shape
        elif a.shape != shape0:
            raise ValueError("第 %d 块尺寸 %r 与第 0 块 %r 不一致"
                             % (i, a.shape, shape0))
        mats.append(a.reshape(-1))
    return np.stack(mats).astype(np.int32, copy=False)


def build_dist(blocks, air_id=0):
    """
    构建 (N+1, N+1) 距离矩阵，0 号是虚拟空地节点。

    做法：把每块摊平成一维（128×128 -> 16384），叠成 (N, 16384) 矩阵 M，
    然后**一次算一整行**：

        D[i+1, j+1] = np.count_nonzero(M[j] != M[i])

    不要写成广播 `M[:, None, :] != M[None, :, :]` —— 那是 N²×16384 个 bool，
    N=100 就要 1.6 亿字节。按行算峰值内存只有 O(N×16384)。
    又因为对称，只算 j > i 一半，左右同时写。

    复杂度：时间 O(N² × 16384)（numpy 内层向量化），空间 O(N² + N×16384)。
    """
    M = _as_matrix(blocks)
    n = M.shape[0]
    D = np.zeros((n + 1, n + 1), dtype=np.int64)

    # 虚拟节点 <-> 各块：首块要放的格数
    nonair = np.count_nonzero(M != air_id, axis=1).astype(np.int64)
    D[0, 1:] = nonair
    D[1:, 0] = nonair

    # 块 <-> 块：汉明距离，只算上三角再对称填
    for i in range(n - 1):
        row = np.count_nonzero(M[i + 1:] != M[i], axis=1).astype(np.int64)
        D[i + 1, i + 2:] = row
        D[i + 2:, i + 1] = row

    # 对角线天然是 0（同块与自身逐格相同）；再显式钉一次，防浮点/脏数据
    np.fill_diagonal(D, 0)
    return D


# ============================================================
# 2. 路径成本
# ============================================================
def path_cost(D, path):
    """路径总成本 = D[path[0]][path[1]] + ... + D[path[-1]][path[-2]]。"""
    if path is None or len(path) < 2:
        return 0
    p = np.asarray(path, dtype=np.int64)
    return int(D[p[:-1], p[1:]].sum(dtype=np.int64))


# ============================================================
# 3. 多起点贪心最近邻
# ============================================================
def _greedy_from(D, start, n):
    """从 start 块出发的贪心最近邻，返回 [0, start, ...]。"""
    visited = np.zeros(n + 1, dtype=bool)
    visited[0] = True
    visited[start] = True
    path = [0, int(start)]
    cur = int(start)
    for _ in range(n - 1):
        row = D[cur].copy()
        row[visited] = INF
        nxt = int(np.argmin(row))
        visited[nxt] = True
        path.append(nxt)
        cur = nxt
    return path


def greedy_best(D, max_starts=None):
    """
    多起点贪心最近邻：以每个块为首块各跑一次，取总成本最小的一条。

    复杂度 O(N³)（N 个起点 × N 步 × 每步 O(N) 取最小）。N 很大时用
    `max_starts` 只从「首块成本最低」的若干块起跑，避免不必要的爆炸。
    """
    n = D.shape[0] - 1
    if n <= 0:
        return [0]
    if n == 1:
        return [0, 1]

    starts = np.arange(1, n + 1)
    if max_starts is None:
        max_starts = GREEDY_ALL_STARTS_MAX
    if n > max_starts:
        # 首块成本低 = 要放的格子少，通常是个好起点
        starts = np.argsort(D[0, 1:], kind="stable")[:max_starts] + 1

    best = None
    best_cost = None
    for s in starts:
        p = _greedy_from(D, int(s), n)
        c = path_cost(D, p)
        if best_cost is None or c < best_cost:
            best, best_cost = p, c
    return best


# ============================================================
# 4. 2-opt
# ============================================================
def two_opt(D, path, max_iter=TWO_OPT_MAX_ITER):
    """
    2-opt：固定虚拟节点 0 在首位，反转 path[i..j]（i ≥ 1）。

    边 (a,b) + (c,e) 换成 (a,c) + (b,e)，收益
        delta = D[a][b] + D[c][e] - D[a][c] - D[b][e]
    delta > 0 才接受。j 是末尾时没有 e，用
        delta = D[a][b] - D[a][c]

    每轮扫全部 (i, j) 取**收益最大**的一步（best-improvement），
    一轮只走一步，走完重新扫 —— 收敛需要的轮数远小于 max_iter。
    max_iter 只是安全上限。
    """
    path = [int(x) for x in path]
    L = len(path)
    if L < 4:                    # 0 + 至少 2 块才谈得上 2-opt
        return path

    arr = np.asarray(path, dtype=np.int64)
    for _ in range(max_iter):
        best_gain = 0
        bi = bj = -1
        for i in range(1, L - 1):          # i 从 1 开始：不反转虚拟起点
            a = arr[i - 1]
            b = arr[i]
            dab = int(D[a, b])
            cs = arr[i + 1:]               # j = i+1 .. L-1
            g = np.empty(cs.size, dtype=np.int64)
            if cs.size > 1:
                # 非末尾的 j：还要看 e = path[j+1]
                es = arr[i + 2:]
                g[:-1] = (dab + D[cs[:-1], es]
                          - D[a, cs[:-1]] - D[b, es])
            # j 是末尾：只有 (a,b) -> (a,c)
            g[-1] = dab - int(D[a, cs[-1]])
            k = int(np.argmax(g))
            if g[k] > best_gain:
                best_gain = int(g[k])
                bi = i
                bj = i + 1 + k

        if best_gain <= 0:
            break
        arr[bi:bj + 1] = arr[bi:bj + 1][::-1]
        path = arr.tolist()
    return path


# ============================================================
# 5. Or-opt（单块搬家）
# ============================================================
def or_opt(D, path, max_iter=OR_OPT_MAX_ITER):
    """
    Or-opt：把单个块从路径里拿出来，插到别的位置，只接受改进。

    把 b 从 a-b-c 里抽出：省下 D[a][b] + D[b][c] - D[a][c]
    （b 在末尾时只省 D[a][b]）。
    插到 p-q 之间：多花 D[p][b] + D[b][q] - D[p][q]（插到末尾只多花 D[p][b]）。
    收益 = 省下的 - 多花的，> 0 才搬。

    不能插到虚拟节点前面（0 必须始终在首位），但可以紧跟在 0 后面
    （那个块成为首块）。
    """
    path = [int(x) for x in path]
    L = len(path)
    if L < 4:
        return path

    arr = np.asarray(path, dtype=np.int64)
    for _ in range(max_iter):
        best_gain = 0
        best_move = None
        for i in range(1, L):
            b = arr[i]
            a = arr[i - 1]
            dab = int(D[a, b])
            if i + 1 < L:
                c = arr[i + 1]
                save = dab + int(D[b, c]) - int(D[a, c])
            else:
                c = None
                save = dab                     # b 在末尾，抽出只断开一条边
            if save <= 0:
                # 抽出来本身就不省，插哪都不会更好（插入成本 >= 0）
                continue

            # 所有插入位置：想插在 path[k] 和 path[k+1] 之间
            ks = np.arange(L)
            ks = ks[(ks != i - 1) & (ks != i)]   # 原地 / 同一个缺口，跳过
            if ks.size == 0:
                continue
            p = arr[ks]
            has_q = (ks + 1) < L
            q = arr[np.minimum(ks + 1, L - 1)]   # 末尾时这个值不会被用到
            base = D[p, b]                       # 插到末尾时的成本
            with_q = base + D[b, q] - D[p, q]    # 插到 p-q 之间的成本
            cost = np.where(has_q, with_q, base)
            gain = save - cost
            k = int(np.argmax(gain))
            if gain[k] > best_gain:
                best_gain = int(gain[k])
                best_move = (i, int(ks[k]))

        if best_move is None or best_gain <= 0:
            break
        i, k = best_move
        b = int(arr[i])
        rest = arr[:i].tolist() + arr[i + 1:].tolist()
        # 在第 k 个元素之后插入；被删掉的 b 在 k 之前时下标要减 1
        j = k if k < i else k - 1
        rest.insert(j + 1, b)
        arr = np.asarray(rest, dtype=np.int64)
        path = rest
    return path


# ============================================================
# 6. 状压 DP（精确解，N ≤ 15）
# ============================================================
def exact_dp(D):
    """
    Held-Karp：dp[mask][j] = 从空地出发、走完 mask、最后停在块 j 的最小成本。

    状态数 2^N × N，转移 O(N)，总 O(2^N × N²)。N=15 时约 740 万次转移。
    返回 ([0, ...], 总成本)。
    """
    n = D.shape[0] - 1
    if n == 0:
        return [0], 0
    if n > EXACT_MAX:
        raise ValueError("状压 DP 只适用于 n <= %d（当前 %d）"
                         % (EXACT_MAX, n))

    size = 1 << n
    dp = np.full((size, n), INF, dtype=np.int64)
    par = np.full((size, n), -1, dtype=np.int32)
    Dm = D[1:, 1:].astype(np.int64)     # 块 <-> 块
    d0 = D[0, 1:].astype(np.int64)      # 空地 -> 块

    for j in range(n):
        dp[1 << j, j] = d0[j]

    for mask in range(1, size):
        row = dp[mask]
        if not np.any(row < INF):
            continue
        ks = [k for k in range(n) if not (mask >> k) & 1]
        if not ks:
            continue
        ks_arr = np.asarray(ks, dtype=np.int64)
        # 对每个还没访问的 k：min_j (dp[mask][j] + D[j][k])
        cand = row[:, None] + Dm[:, ks_arr]
        best = cand.min(axis=0)
        arg = cand.argmin(axis=0)
        for t, k in enumerate(ks):
            nm = mask | (1 << k)
            v = best[t]
            if v < dp[nm, k]:
                dp[nm, k] = v
                par[nm, k] = int(arg[t])

    full = size - 1
    end = int(np.argmin(dp[full]))
    total = int(dp[full, end])

    out = []
    mask, cur = full, end
    while cur >= 0:
        out.append(cur + 1)
        nxt = int(par[mask, cur])
        mask ^= (1 << cur)
        cur = nxt
    out.reverse()
    return [0] + out, total


# ============================================================
# 7. 报表
# ============================================================
def _fmt_pct(part, whole):
    return 100.0 * part / whole if whole else 0.0


def build_rows(D, path, threshold=THRESHOLD):
    """把路径展开成明细行（不打印）。总成本和替换数都来自这里。"""
    n = D.shape[0] - 1
    if len(path) != n + 1:
        raise ValueError("路径长度应该是 %d（0 + %d 块），实际 %d"
                         % (n + 1, n, len(path)))
    if int(path[0]) != 0:
        raise ValueError("路径必须以虚拟空地节点 0 开头，实际开头是 %r" % (path[0],))
    if sorted(int(x) for x in path) != list(range(n + 1)):
        raise ValueError("路径必须恰好遍历 0..%d 每个节点一次" % n)

    rows = []
    cum = 0
    prev = None
    for step in range(1, n + 1):
        node = int(path[step])
        blk = node - 1                      # 块索引 = 路径元素 - 1
        if step == 1:
            rep = int(D[0, node])
            note = "⚠️ 首块非空气格 %d > %d" % (rep, threshold)
            prev_blk = None
        else:
            rep = int(D[prev, node])
            prev_blk = prev - 1
            note = "⚠️ 单步替换 %d > %d（块%d → 块%d）" % (
                rep, threshold, prev_blk, blk)
        cum += rep
        over = rep > threshold
        rows.append({
            "step": step,
            "block": blk,
            "prev": prev_blk,
            "replaced": rep,
            "cum": cum,
            "over": over,
            "note": note if over else "",
        })
        prev = node
    return rows, cum


def render_report(D, path, threshold=THRESHOLD, cells=None, header=None,
                  footer=None):
    """
    把明细表渲染成**纯文本**（控制台打印和 txt 下载用的是同一份）。

    返回 (文本, 总替换次数, 明细行列表)。

    header / footer 是额外附加的行（比如「块索引 → 文件名」对照表），
    让导出的 txt 能直接拿去施工。
    """
    if cells is None:
        cells = TILE_CELLS
    rows, cum = build_rows(D, path, threshold=threshold)

    lines = []
    if header:
        lines.extend(header)
        if lines and lines[-1] != "":
            lines.append("")

    lines.append("步   块索引    本步替换     累计     备注")
    lines.append("-" * 60)
    for r in rows:
        line = "%3d  %6d  %10d  %8d" % (
            r["step"], r["block"], r["replaced"], r["cum"])
        if r["over"]:
            line += "   " + r["note"]
        lines.append(line)
    lines.append("-" * 60)
    lines.append("总替换次数 = %d" % cum)
    lines.append("")

    over_rows = [r for r in rows if r["over"]]
    if over_rows:
        lines.append("⚠️ 高替换步骤汇总（>%d）：共 %d 步"
                     % (threshold, len(over_rows)))
        for r in over_rows:
            pct = _fmt_pct(r["replaced"], cells)
            if r["step"] == 1:
                lines.append(
                    "  第 %3d 步：空地 → 块 %3d，替换 %d 格（占 %d 格的 %.1f%%）"
                    % (r["step"], r["block"], r["replaced"], cells, pct))
            else:
                lines.append(
                    "  第 %3d 步：块 %3d → 块 %3d，替换 %d 格（占 %d 格的 %.1f%%）"
                    % (r["step"], r["prev"], r["block"],
                       r["replaced"], cells, pct))
    else:
        lines.append("✅ 没有任何一步超过 %d，铺设顺序平稳。" % threshold)

    if footer:
        lines.append("")
        lines.extend(footer)

    return "\n".join(lines), cum, rows


def report(D, path, threshold=THRESHOLD, cells=None, verbose=True,
           header=None, footer=None):
    """
    打印逐步明细 + 高替换步骤汇总，返回 (总替换次数, 明细行列表)。

    每一步的「本步替换」超过 threshold 就在备注里打 ⚠️：
      · 第一步：⚠️ 首块非空气格 XXXX > 8000
      · 其余  ：⚠️ 单步替换 XXXX > 8000（块A → 块B）

    注意 threshold 只影响**标注**，不影响路径本身。
    """
    text, cum, rows = render_report(D, path, threshold=threshold, cells=cells,
                                    header=header, footer=footer)
    if verbose:
        print(text)
    return cum, rows


# ============================================================
# 8. 主流程
# ============================================================
def solve(blocks, air_id=0, threshold=THRESHOLD, verbose=True,
          exact_max=EXACT_MAX, or_opt_max=OR_OPT_MAX):
    """
    求最小替换次数与铺设顺序。

    参数
        blocks      list[np.ndarray]，每块 (128,128)，整数方块 ID，air_id 表示空气
        air_id      空气的 ID（默认 0）
        threshold   单步替换告警阈值（默认 8000，只影响标注）
        verbose     是否打印明细表
        exact_max   ≤ 这个块数用状压 DP 求精确解
        or_opt_max  ≤ 这个块数才跑 Or-opt

    返回 dict：
        total       最小总替换次数
        path        铺设顺序（**块索引**列表，已去掉虚拟节点）
        raw_path    含虚拟节点的路径（[0, ...]）
        rows        明细行（report 的返回值）
        method      用了哪些算法（一句话）
        D           距离矩阵
        order_text  顺序的可读形式 "3 → 8 → 1"
    """
    if blocks is None or len(blocks) == 0:
        raise ValueError("blocks 不能为空")
    n = len(blocks)
    t0 = time.time()
    D = build_dist(blocks, air_id=air_id)
    t_dist = time.time() - t0
    cells = int(np.asarray(blocks[0]).size)

    if n == 1:
        path = [0, 1]
        method = "单块，无需优化"
    elif n <= exact_max:
        path, _ = exact_dp(D)
        method = "状压 DP 精确解（2^%d × %d）" % (n, n)
    else:
        t1 = time.time()
        path = greedy_best(D)
        c_greedy = path_cost(D, path)
        t2 = time.time()

        path = two_opt(D, path, max_iter=TWO_OPT_MAX_ITER)
        c_2opt = path_cost(D, path)
        t3 = time.time()

        method = ("多起点贪心 %d → 2-opt %d" % (c_greedy, c_2opt))
        if n <= or_opt_max:
            path = or_opt(D, path, max_iter=OR_OPT_MAX_ITER)
            c_or = path_cost(D, path)
            method += " → Or-opt %d" % c_or
            t4 = time.time()
        else:
            t4 = t3
        method += "（N=%d 跳过 Or-opt）" % n if n > or_opt_max else ""
        # 记录各阶段耗时，方便判断要不要调参
        method += "  [建表 %.2fs / 贪心 %.2fs / 2-opt %.2fs / Or-opt %.2fs]" % (
            t_dist, t2 - t1, t3 - t2, t4 - t3)

    # 防御：启发式有可能因为 bug 跑出比贪心更差的路径
    total, rows = report(D, path, threshold=threshold, cells=cells,
                         verbose=verbose)
    cand = path_cost(D, path)
    if total != cand:
        raise AssertionError("report 算出的总成本 %d 和路径成本 %d 不一致"
                             % (total, cand))

    seq = [p - 1 for p in path[1:]]
    return {
        "total": total,
        "path": seq,
        "raw_path": [int(p) for p in path],
        "rows": rows,
        "method": method,
        "D": D,
        "order_text": " → ".join(str(x) for x in seq),
    }


# ============================================================
# 演示
# ============================================================
def _demo_blocks(n=12, side=TILE, palette=24, seed=20241003):
    """
    造 n 块「像同一个地图画被切开」的样例：

    先造一张底图，每块随机改掉一部分格子，再挖掉几块空气。
    这样既有共享底色（距离小）又有差异，比纯随机有说服力。
    """
    rng = np.random.default_rng(seed)
    base = (rng.integers(0, palette, size=(side, side), dtype=np.int32) + 1)
    out = []
    for _ in range(n):
        b = base.copy()
        k = int(side * side * (0.05 + 0.25 * rng.random()))
        ys = rng.integers(0, side, k)
        xs = rng.integers(0, side, k)
        b[ys, xs] = rng.integers(0, palette, k) + 1
        if rng.random() < 0.35:             # 挖一块空气，制造「首块成本」差异
            h = int(rng.integers(8, 48))
            w = int(rng.integers(8, 48))
            y0 = int(rng.integers(0, side - h))
            x0 = int(rng.integers(0, side - w))
            b[y0:y0 + h, x0:x0 + w] = 0
        out.append(b)
    return out


if __name__ == "__main__":
    print("=" * 66)
    print("地图画投影铺设顺序优化器 —— 演示")
    print("=" * 66)

    blocks = _demo_blocks(12)
    n = len(blocks)
    cells = blocks[0].size
    print("共 %d 块，每块 %d 格（%d×%d）" % (n, cells, TILE, TILE))
    print()

    res = solve(blocks)

    print()
    print("=" * 66)
    print("结果")
    print("=" * 66)
    print("算法        ：" + res["method"])
    print("最小总替换  ：%d" % res["total"])
    print("铺设顺序    ：" + res["order_text"])
    print()

    # 超过 15 块就走启发式（贪心 -> 2-opt -> Or-opt）
    print("=" * 66)
    print("中等规模样例（N=20，走启发式）")
    print("=" * 66)
    mid = _demo_blocks(20, seed=2024)
    res2 = solve(mid)
    print()
    print("算法：" + res2["method"])
    print("顺序：" + res2["order_text"])
    print()

    # 小规模样例，展示精确解
    print("=" * 66)
    print("小规模样例（N=6，走状压 DP 精确解）")
    print("=" * 66)
    small = _demo_blocks(6, palette=10, seed=7)
    res3 = solve(small)
    print()
    print("算法：" + res3["method"])
    print("顺序：" + res3["order_text"])
