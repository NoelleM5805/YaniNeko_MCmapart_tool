# -*- coding: utf-8 -*-
import numpy as np

from . import native
from .matching import _make_dist_tables, get_matcher, match_batch


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
def _diffuse_native(buf, H, W, pal, kernel, strength, algo_key):
    """
    C++ 版误差扩散。整个循环和颜色匹配都在 DLL 里跑。

    结果和下面 `_diffuse_flat` 逐像素一致，这是证明过的而不是「应该一致」：
    tests/native_match_equiv.py 会把 256³ = 1677 万种量化颜色的匹配结果和
    numpy 版逐个比一遍，全过才用这条路径。

    tap 偏移按 Python 那边的算法算好传进去：offset = (dy*PW + dx)*3，
    其中 PW = W + 2*PAD，PAD = 2 —— 和 Python 版的 padding 保持一致。
    """
    PAD = 2
    PW = W + 2 * PAD
    taps = [((dy * PW + dx) * 3, k) for dx, dy, k in kernel]
    off = [t[0] for t in taps]
    wgt = [t[1] for t in taps]

    h = native.make_handle(algo_key, pal)
    return h.diffuse(buf, H, W, strength, off, wgt)


def _diffuse_flat(buf, H, W, pal, kernel, strength, tables, matcher, cache):
    """
    误差扩散：逐像素回头修改后面的像素，所以没法整批并行。
    这里把能省的都省掉：
      · 工作缓冲摊成一维 Python 列表 —— 标量读写比 numpy 下标快得多
      · 四周留 2 圈空白，核的偏移量事先算好，内层循环不再判断越界
      · 调色板颜色用 list of tuple 取，避免 numpy 标量装箱
    """
    PAD = 2
    PW = W + 2 * PAD
    PH = H + 2 * PAD
    pad = np.zeros((PH, PW, 3), dtype=np.float32)
    pad[PAD:PAD + H, PAD:PAD + W, :] = buf
    work = pad.reshape(-1).tolist()

    taps = [((dy * PW + dx) * 3, k) for dx, dy, k in kernel]
    colors = pal.rgb_list
    out = [0] * (H * W)
    npix = 0
    TR = TG = TB = None
    if tables is not None:
        TR, TG, TB = tables

    get = cache.get
    row_start = ((PAD) * PW + PAD) * 3
    stride = PW * 3

    for y in range(H):
        i = row_start + y * stride
        for _ in range(W):
            r = work[i]
            g = work[i + 1]
            b = work[i + 2]
            rc = int(r + 0.5)
            if rc < 0: rc = 0
            elif rc > 255: rc = 255
            gc = int(g + 0.5)
            if gc < 0: gc = 0
            elif gc > 255: gc = 255
            bc = int(b + 0.5)
            if bc < 0: bc = 0
            elif bc > 255: bc = 255

            key = (rc, gc, bc)
            pi = get(key)
            if pi is None:
                if TR is not None:
                    pi = int((TR[rc] + TG[gc] + TB[bc]).argmin())
                else:
                    pi = matcher(rc, gc, bc)
                cache[key] = pi

            out[npix] = pi
            npix += 1

            pr, pg, pb = colors[pi]
            er = (r - pr) * strength
            eg = (g - pg) * strength
            eb = (b - pb) * strength
            for off, k in taps:
                m = i + off
                work[m] += er * k
                work[m + 1] += eg * k
                work[m + 2] += eb * k
            i += 3

    return np.array(out, dtype=np.int32).reshape(H, W)


def _match_unique(rgb_int, pal, algo_key, tables):
    """
    先去重再整批匹配，最后映射回去。
    照片这类颜色重复度高的图，去重后要算的次数能少一个数量级；
    CIE 系列尤其吃这个。

    去重时把 RGB 打包成一个 int32 再 unique —— 直接 np.unique(axis=0) 要按行
    字典序排序，100 万像素时这一步本身就是秒级开销。打包后是纯一维排序，
    快好几倍。两种写法给出的结果完全一样：`uniq_idx[inv]` 只关心「每个像素
    对应哪个去重后的颜色」，跟去重后的顺序无关。
    """
    c = np.ascontiguousarray(rgb_int, dtype=np.int32)
    packed = (c[:, 0] << 16) | (c[:, 1] << 8) | c[:, 2]
    uniq, inv = np.unique(packed, return_inverse=True)
    u = np.stack([(uniq >> 16) & 0xFF, (uniq >> 8) & 0xFF, uniq & 0xFF],
                 axis=1).astype(np.int32)
    uniq_idx = match_batch(u, pal, algo_key, tables)
    return uniq_idx[inv.reshape(-1)]


def process_image(img, algo_key, dither_key, strength, pal):
    W, H = img.size
    tables = _make_dist_tables(algo_key, pal)
    matcher = get_matcher(algo_key, pal)

    # ---------------- 无抖动 ----------------
    if dither_key == "none":
        arr = np.array(img, dtype=np.uint8)
        flat = arr.reshape(-1, 3).astype(np.int32)
        if tables is not None:
            # 可分离算法直接整批算，不需要去重（本来就很快）
            idx = match_batch(flat, pal, algo_key, tables)
        else:
            idx = _match_unique(flat, pal, algo_key, tables)
        idx = idx.reshape(H, W)
        return idx, pal.rgb[idx].astype(np.uint8)

    # ---------------- Bayer 阈值抖动 ----------------
    if dither_key in ("bayer4", "bayer8"):
        matrix = BAYER_4 if dither_key == "bayer4" else BAYER_8
        bh, bw = matrix.shape
        buf = np.array(img, dtype=np.float32)
        tile = np.tile(matrix, (H // bh + 1, W // bw + 1))[:H, :W]
        thresh = (tile / (bh * bw)) - 0.5
        buf = np.clip(buf + thresh[:, :, None] * (strength * 64.0), 0, 255)
        # 这一步等价于逐像素的 int(min(255, max(0, v + 0.5)))
        q = np.clip((buf + 0.5).astype(np.int32), 0, 255).reshape(-1, 3)
        if tables is not None:
            idx = match_batch(q, pal, algo_key, tables)
        else:
            idx = _match_unique(q, pal, algo_key, tables)
        idx = idx.reshape(H, W)
        return idx, pal.rgb[idx].astype(np.uint8)

    # ---------------- 误差扩散 ----------------
    buf = np.array(img, dtype=np.float32)
    kernel = DIFFUSION_KERNELS[dither_key]

    # 优先走 C++：内层循环和匹配都在 DLL 里，快 20~100 倍。
    # DLL 不在（或这个算法还没验证过）就退回纯 Python，结果一样。
    if native.available() and native.mode_for(algo_key) is not None:
        try:
            idx = _diffuse_native(buf, H, W, pal, kernel, strength, algo_key)
            return idx, pal.rgb[idx].astype(np.uint8)
        except Exception as e:                       # noqa: BLE001
            print("[抖动] C++ 路径失败，退回纯 Python：%s" % e)

    cache = {}
    idx = _diffuse_flat(buf, H, W, pal, kernel, strength, tables, matcher, cache)
    return idx, pal.rgb[idx].astype(np.uint8)


