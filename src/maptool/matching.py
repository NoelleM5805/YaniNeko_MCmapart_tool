# -*- coding: utf-8 -*-
import math
import numpy as np

from . import native
from .colorspace import rgb_to_lab


# ============================================================
# 颜色匹配算法
# ============================================================
# 这组函数在误差扩散里会被调用几十万次，所以做了两层提速：
#
#   1. euclidean / weighted 的距离可以按通道拆开：
#          d = f(r) + g(g) + h(b)
#      于是预计算三张 256×N 的表，每次匹配只剩「两次数组加法 + argmin」。
#
#   2. cie76 / cie94 / ciede2000 原来是对着调色板一条条 Python 循环，
#      现在改成把整张调色板一次性丢进 numpy 算。公式一字未改，
#      只是从「59 次 Python 循环」变成「一次数组运算」。
#
# 批量接口 match_batch(rgbs) 则把「每个像素各自算一次」改成「一批像素一起算」，
# 无抖动 / Bayer 这类不依赖前序结果的路径可以直接整批处理。

def _make_dist_tables(algo_key, pal):
    """
    euclidean / weighted 的距离可以拆成三个通道各自贡献，返回 (TR, TG, TB)，
    每张都是 256×N 的表：TR[v, i] 表示该通道取值 v 对上第 i 个颜色的代价。
    其它算法返回 None。

    精度上必须和原来的写法对齐，否则临界情况下会选到相邻的另一个颜色：
    原式是 (r - pal.r) 用整数通道相减（精确），再乘系数（float64）。
    所以这里也用整数通道 + float64，并且加法顺序保持 ((TR+TG)+TB)。
    """
    if algo_key not in ("euclidean", "weighted"):
        return None
    v = np.arange(256, dtype=np.float64)
    # 用 int32 通道值转 float64：v - pr 对 0..255 的整数是精确的
    dr = v[:, None] - pal.r.astype(np.float64)[None, :]
    dg = v[:, None] - pal.g.astype(np.float64)[None, :]
    db = v[:, None] - pal.b.astype(np.float64)[None, :]
    if algo_key == "euclidean":
        return dr * dr, dg * dg, db * db
    return 0.30 * dr * dr, 0.59 * dg * dg, 0.11 * db * db


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
    dL = L - pal.lab_l
    da = a - pal.lab_a
    db = b - pal.lab_b
    return int((dL*dL + da*da + db*db).argmin())


def _match_cie94(L, a, b, pal):
    C1 = math.hypot(a, b)
    dL = L - pal.lab_l
    dC = C1 - pal.lab_c
    da = a - pal.lab_a
    db = b - pal.lab_b
    dH2 = np.maximum(0.0, da*da + db*db - dC*dC)
    SC = 1 + 0.045*C1
    SH = 1 + 0.015*C1
    d = dL*dL + (dC/SC)**2 + dH2/(SH*SH)
    return int(d.argmin())


def _ciede2000_dist(L1, a1, b1, pal):
    """CIEDE2000：L1/a1/b1 可以是标量也可以是数组，返回与调色板同形的距离数组。"""
    C1 = np.hypot(a1, b1)
    C1_7 = C1 ** 7
    L2, a2, b2 = pal.lab_l, pal.lab_a, pal.lab_b
    C2 = pal.lab_c
    C2_7 = C2 ** 7
    Cbar = (C1 + C2) * 0.5
    Cbar_7 = Cbar ** 7
    G = 0.5 * (1 - np.sqrt(Cbar_7 / (Cbar_7 + 25**7)))
    a1p = a1 * (1 + G)
    a2p = a2 * (1 + G)
    C1p = np.hypot(a1p, b1)
    C2p = np.hypot(a2p, b2)
    h1p = np.degrees(np.arctan2(b1, a1p)) % 360
    h2p = np.degrees(np.arctan2(b2, a2p)) % 360
    dLp = L2 - L1
    dCp = C2p - C1p

    dh = h2p - h1p
    dhp = np.where(np.abs(dh) <= 180, dh,
                   np.where(dh > 180, dh - 360, dh + 360))
    dHp = 2 * np.sqrt(C1p * C2p) * np.sin(np.radians(dhp) * 0.5)

    Lbarp = (L1 + L2) * 0.5
    Cbarp = (C1p + C2p) * 0.5
    hsum = h1p + h2p
    hbarp = np.where(C1p * C2p == 0, h1p + h2p,
                     np.where(np.abs(h1p - h2p) <= 180, hsum * 0.5,
                              np.where(hsum < 360, (hsum + 360) * 0.5,
                                       (hsum - 360) * 0.5)))

    T = (1 - 0.17 * np.cos(np.radians(hbarp - 30))
         + 0.24 * np.cos(np.radians(2 * hbarp))
         + 0.32 * np.cos(np.radians(3 * hbarp + 6))
         - 0.20 * np.cos(np.radians(4 * hbarp - 63)))
    dTheta = 30 * np.exp(-(((hbarp - 275) / 25) ** 2))
    Cbarp_7 = Cbarp ** 7
    RC = 2 * np.sqrt(Cbarp_7 / (Cbarp_7 + 25**7))
    SL = 1 + (0.015 * (Lbarp - 50)**2) / np.sqrt(20 + (Lbarp - 50)**2)
    SC = 1 + 0.045 * Cbarp
    SH = 1 + 0.015 * Cbarp * T
    RT = -np.sin(np.radians(2 * dTheta)) * RC
    return (dLp/SL)**2 + (dCp/SC)**2 + (dHp/SH)**2 + RT*(dCp/SC)*(dHp/SH)


def _match_ciede2000(L1, a1, b1, pal):
    return int(_ciede2000_dist(L1, a1, b1, pal).argmin())


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


# ------------------------------------------------------------
# 批量匹配：一次算一批像素，而不是一个像素一次
# ------------------------------------------------------------
BATCH_CHUNK = 8192          # 每批像素数，控制内存占用


def _rgb_to_lab_batch(rgb):
    """(P,3) uint8/float 数组 -> (L, a, b) 三个 float64 数组。"""
    c = np.asarray(rgb, dtype=np.float64) / 255.0
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = lin[:, 0], lin[:, 1], lin[:, 2]
    x = (r * 0.4124564 + g * 0.3575761 + b * 0.1804375) / 0.95047
    y = (r * 0.2126729 + g * 0.7151522 + b * 0.0721750) / 1.00000
    z = (r * 0.0193339 + g * 0.1191920 + b * 0.9503041) / 1.08883

    def f(t):
        return np.where(t > 0.008856, np.cbrt(t), 7.787 * t + 16.0 / 116.0)

    fx, fy, fz = f(x), f(y), f(z)
    return 116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz)


def _dist_block(blk, pal, algo_key, tables):
    """
    一批像素 (P,3) 到调色板每个颜色的距离矩阵 (P,n)。
    match_batch 和 dist_batch 都走这里，保证「选哪个」和「差多少」用的是同一套公式。
    """
    if tables is not None:
        TR, TG, TB = tables
        return TR[blk[:, 0]] + TG[blk[:, 1]] + TB[blk[:, 2]]
    if algo_key == "redmean":
        # 与原式一致：整数相减 -> 再转 float64 参与乘除
        rf = blk[:, 0].astype(np.float64)[:, None]
        gf = blk[:, 1].astype(np.float64)[:, None]
        bf = blk[:, 2].astype(np.float64)[:, None]
        pi_r = pal.r.astype(np.float64)[None, :]
        pi_g = pal.g.astype(np.float64)[None, :]
        pi_b = pal.b.astype(np.float64)[None, :]
        rmean = (rf + pi_r) * 0.5
        dr = rf - pi_r
        dg = gf - pi_g
        db = bf - pi_b
        return ((2 + rmean / 256) * dr * dr + 4 * dg * dg
                + (2 + (255 - rmean) / 256) * db * db)

    L, a, b = _rgb_to_lab_batch(blk)
    if algo_key == "cie76":
        dL = L[:, None] - pal.lab_l[None, :]
        da = a[:, None] - pal.lab_a[None, :]
        db = b[:, None] - pal.lab_b[None, :]
        return dL * dL + da * da + db * db
    if algo_key == "cie94":
        C1 = np.hypot(a, b)[:, None]
        dL = L[:, None] - pal.lab_l[None, :]
        dC = C1 - pal.lab_c[None, :]
        da = a[:, None] - pal.lab_a[None, :]
        db = b[:, None] - pal.lab_b[None, :]
        dH2 = np.maximum(0.0, da * da + db * db - dC * dC)
        SC = 1 + 0.045 * C1
        SH = 1 + 0.015 * C1
        return dL * dL + (dC / SC) ** 2 + dH2 / (SH * SH)
    return _ciede2000_dist(L[:, None], a[:, None], b[:, None], pal)


def dist_batch(rgb_int, pal, algo_key, tables=None):
    """
    返回 (P, n) 的距离矩阵：每个像素到调色板每个颜色的距离。

    「局部噪点修正」要比较「像素到主色的距离」和「像素到当前色的距离」，
    需要的是距离本身而不是最小值，所以单独开这个接口。
    刻意只走 numpy：调色板通常只有 2 个颜色（主色 + 当前色），
    走 C++ 的往返开销反而更亏。
    """
    c = np.asarray(rgb_int, dtype=np.int32)
    P = c.shape[0]
    out = np.empty((P, pal.n), dtype=np.float64)
    for s in range(0, P, BATCH_CHUNK):
        e = min(s + BATCH_CHUNK, P)
        out[s:e] = _dist_block(c[s:e], pal, algo_key, tables)
    return out


def match_batch(rgb_int, pal, algo_key, tables=None):
    """
    rgb_int: (P,3) 的整数数组（0..255），返回 (P,) 调色板下标。

    优先走 C++（native 里的匹配器每个颜色约 100 ns，numpy 批量约 1.5 µs/色）；
    没编译 DLL、或这个算法还没通过全空间等价性验证时，退回下面的 numpy 实现。
    两条路的结果逐位一致 —— 见 tests/native_match_equiv.py。
    """
    c = np.asarray(rgb_int, dtype=np.int32)
    P = c.shape[0]
    if P == 0:
        return np.empty(0, dtype=np.int32)

    if native.mode_for(algo_key) is not None:
        try:
            h = native.make_handle(algo_key, pal)
            return h.match_list(c.astype(np.uint8)).astype(np.int32)
        except Exception as e:                       # noqa: BLE001
            print("[匹配] C++ 路径失败，退回 numpy：%s" % e)

    out = np.empty(P, dtype=np.int32)
    for s in range(0, P, BATCH_CHUNK):
        e = min(s + BATCH_CHUNK, P)
        out[s:e] = _dist_block(c[s:e], pal, algo_key, tables).argmin(axis=1)
    return out


ALGO_LABELS = {
    "euclidean": "欧几里得 RGB（最快）",
    "weighted": "加权 RGB（推荐）",
    "redmean": "Redmean",
    "cie76": "CIE76",
    "cie94": "CIE94",
    "ciede2000": "CIEDE2000（最精确）",
}


