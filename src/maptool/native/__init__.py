# -*- coding: utf-8 -*-
"""
抖动计算核心的 C++ 加速层（可选）
==================================

`maptool_native.dll` 里是误差扩散的内层循环和颜色匹配，用 ctypes 调用。
DLL 不存在或者加载失败时，`available()` 返回 False，调用方（dithering）
就退回纯 Python 实现 —— 两条路算出来的结果逐像素一致，只是速度差很多。

为什么是「普通 C DLL + ctypes」：本机 CPython 是 MSVC 编译的，编译器只有
MinGW-w64 g++。MinGW 编 CPython 扩展要跨 CRT，PyObject* 过边界容易出问题；
导出纯 C ABI（只有指针和整数）用 ctypes 调就没有 ABI 风险，也不需要 Python
头文件。编译见 tools/build_native.py。

对应关系（C++ 侧 maptool::Mode）：
    0 separable(euclidean/weighted)  1 redmean  2 cie76  3 cie94  4 ciede2000
"""

import ctypes
import os
import sys
import threading

import numpy as np

# ---------------------------------------------------------------- 库定位
_LIB_NAME = "maptool_native.dll" if sys.platform.startswith("win") else "maptool_native.so"

_lock = threading.Lock()
_lib = None
_tried = False
_error = None


def _candidates():
    """按优先级找 DLL：包内 native/ -> 包目录 -> 可执行文件旁边。"""
    here = os.path.dirname(os.path.abspath(__file__))
    yields = [os.path.join(here, "native", _LIB_NAME),
              os.path.join(here, _LIB_NAME)]
    if getattr(sys, "frozen", False):
        yields.insert(0, os.path.join(getattr(sys, "_MEIPASS", ""), "native", _LIB_NAME))
        yields.append(os.path.join(os.path.dirname(os.path.abspath(sys.executable)),
                                   _LIB_NAME))
    return yields


def _bind(lib):
    lib.mp_version.restype = ctypes.c_char_p
    lib.mp_version.argtypes = []

    lib.mp_pal_new.restype = ctypes.c_void_p
    lib.mp_pal_new.argtypes = [
        ctypes.c_int, ctypes.c_int,
        ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p,
    ]

    lib.mp_pal_free.restype = None
    lib.mp_pal_free.argtypes = [ctypes.c_void_p]

    lib.mp_match_rgb_list.restype = ctypes.c_int
    lib.mp_match_rgb_list.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                      ctypes.c_int, ctypes.c_void_p]

    lib.mp_match_lut.restype = ctypes.c_int
    lib.mp_match_lut.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

    lib.mp_diffuse.restype = ctypes.c_int
    lib.mp_diffuse.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
        ctypes.c_double,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
        ctypes.c_void_p,
    ]


def _load():
    global _lib, _tried, _error
    with _lock:
        if _tried:
            return _lib
        _tried = True
        for p in _candidates():
            if not os.path.isfile(p):
                continue
            try:
                lib = ctypes.CDLL(p)
                _bind(lib)
                _lib = lib
                _error = None
                return _lib
            except Exception as e:                    # noqa: BLE001
                _error = "%s：%s" % (p, e)
        # 安卓：.so 在 App 的 nativeLibraryDir（不跟 .py 在一起），按裸名交给
        # 动态链接器找（Chaquopy / python-for-android 都会把该目录加进搜索路径）。
        if not sys.platform.startswith("win"):
            try:
                lib = ctypes.CDLL(_LIB_NAME)
                _bind(lib)
                _lib = lib
                _error = None
                return _lib
            except Exception as e:                    # noqa: BLE001
                _error = "%s：%s" % (_LIB_NAME, e)
        if _error is None:
            _error = "没找到 %s（跑 python tools/build_native.py 可以编译）" % _LIB_NAME
        return None


def reset():
    """丢掉已加载的库，下次调用重新找（编译完不用重启进程）。"""
    global _lib, _tried, _error
    with _lock:
        _lib = None
        _tried = False
        _error = None


def available():
    return _load() is not None


def load_error():
    _load()
    return _error


def version():
    lib = _load()
    if not lib:
        return None
    return lib.mp_version().decode()


# ---------------------------------------------------------------- 指针工具
def _ptr(arr):
    return None if arr is None else arr.ctypes.data_as(ctypes.c_void_p)


def _f64(a):
    return None if a is None else np.ascontiguousarray(a, dtype=np.float64)


def _i32(a):
    return None if a is None else np.ascontiguousarray(a, dtype=np.int32)


# ---------------------------------------------------------------- 调色板句柄
_MODE_BY_ALGO = {
    "euclidean": 0,
    "weighted": 0,
    "redmean": 1,
    "cie76": 2,
    "cie94": 3,
    "ciede2000": 4,
}

# 允许走 C++ 路径的算法白名单。
# 加进来的前提是 tests/native_match_equiv.py 在**全部 256³ = 1677 万种**
# 量化颜色上验证过匹配结果和 numpy 版逐位一致 —— 不是抽样，是全空间。
# 某个算法一旦有差异就从这里去掉，它会自动退回纯 Python（结果仍然正确，只是慢）。
#
# 2026-xx 实测结论（59 色默认调色板 + 11 色小调色板，各 1677 万种颜色）：
#   euclidean / weighted / redmean / cie76 / cie94 / ciede2000  全部 0 处不同。
#   ciede2000 原本是最担心的（MinGW 的 cos/sin 与 numpy 差 1 ULP），
#   实测这点差异没有造成任何一次 argmin 翻转。
VERIFIED = frozenset({"euclidean", "weighted", "redmean", "cie76",
                      "cie94", "ciede2000"})

_enabled = True


def set_enabled(on):
    """测试用：关掉后所有调用方都退回纯 Python 实现。"""
    global _enabled
    _enabled = bool(on)


def is_enabled():
    return _enabled


def mode_for(algo_key):
    """
    算法名 -> C++ 侧的 matcher 模式号。
    没验证过的算法、或全局关掉了，都返回 None，调用方就退回 Python 版。
    """
    if not _enabled or algo_key not in VERIFIED:
        return None
    return _MODE_BY_ALGO.get(algo_key)


def make_handle(algo_key, pal):
    """
    按 `Palette` 建 C++ 侧句柄。`tables` 复用 matching 里那张可分离距离表，
    保证和 Python 版用的是同一份数据。
    """
    from ..matching import _make_dist_tables

    mode = mode_for(algo_key)
    if mode is None:
        raise ValueError("native 不支持的颜色算法：%r" % (algo_key,))

    colors = np.asarray(pal.rgb_list, dtype=np.float64).reshape(-1, 3)
    rgb_int = TR = TG = TB = lab = labc = None

    if mode == 0:
        t = _make_dist_tables(algo_key, pal)
        if t is None:
            raise ValueError("%s 没有可分离距离表" % algo_key)
        TR, TG, TB = (np.ascontiguousarray(x, dtype=np.float64) for x in t)
    elif mode == 1:
        rgb_int = np.concatenate([
            np.asarray(pal.r, dtype=np.int32),
            np.asarray(pal.g, dtype=np.int32),
            np.asarray(pal.b, dtype=np.int32)])
    else:
        lab = np.stack([np.asarray(pal.lab_l, dtype=np.float64),
                        np.asarray(pal.lab_a, dtype=np.float64),
                        np.asarray(pal.lab_b, dtype=np.float64)], axis=1)
        labc = np.asarray(pal.lab_c, dtype=np.float64)

    return PaletteHandle(mode, int(pal.n), colors, rgb_int, TR, TG, TB, lab, labc)


class PaletteHandle:
    """
    把 C++ 侧的 Palette 包起来，顺便**持有**所有传进去的 numpy 数组，
    否则 ctypes 传完指针数组就被回收了，C++ 那边会读到野内存。
    """

    __slots__ = ("_h", "_keep", "mode", "n")

    def __init__(self, mode, n, colors, rgb_int=None,
                 TR=None, TG=None, TB=None, lab=None, labc=None):
        lib = _load()
        if not lib:
            raise RuntimeError("native 库不可用")
        self._keep = [_f64(colors), _i32(rgb_int), _f64(TR), _f64(TG), _f64(TB),
                      _f64(lab), _f64(labc)]
        colors_, rgb_int_, TR_, TG_, TB_, lab_, labc_ = self._keep
        self.mode = mode
        self.n = n
        h = lib.mp_pal_new(mode, n, _ptr(colors_), _ptr(rgb_int_),
                           _ptr(TR_), _ptr(TG_), _ptr(TB_),
                           _ptr(lab_), _ptr(labc_))
        if not h:
            raise RuntimeError("mp_pal_new 失败（n=%d）" % n)
        self._h = ctypes.c_void_p(h)

    def __del__(self):
        try:
            if getattr(self, "_h", None):
                _load().mp_pal_free(self._h)
                self._h = None
        except Exception:                              # noqa: BLE001
            pass

    # ---- 匹配 ----
    def match_list(self, rgb_u8):
        """rgb_u8: (P,3) uint8 -> (P,) uint8 调色板下标。"""
        rgb_u8 = np.ascontiguousarray(rgb_u8, dtype=np.uint8)
        out = np.empty(rgb_u8.shape[0], dtype=np.uint8)
        rc = _load().mp_match_rgb_list(self._h, _ptr(rgb_u8),
                                       int(rgb_u8.shape[0]), _ptr(out))
        if rc != 0:
            raise RuntimeError("mp_match_rgb_list 返回 %d" % rc)
        return out

    def match_lut(self):
        """把 256×256×256 全部量化颜色的匹配结果算出来（16 MB）。"""
        out = np.empty(256 * 256 * 256, dtype=np.uint8)
        rc = _load().mp_match_lut(self._h, _ptr(out))
        if rc != 0:
            raise RuntimeError("mp_match_lut 返回 %d" % rc)
        return out.reshape(256, 256, 256)

    # ---- 误差扩散 ----
    def diffuse(self, src_f32, H, W, strength, tap_off, tap_w):
        src = np.ascontiguousarray(src_f32, dtype=np.float32)
        off = _i32(np.asarray(tap_off, dtype=np.int32))
        wgt = _f64(np.asarray(tap_w, dtype=np.float64))
        out = np.empty(H * W, dtype=np.int32)
        rc = _load().mp_diffuse(self._h, _ptr(src), int(H), int(W),
                                float(strength), _ptr(off), _ptr(wgt),
                                int(off.shape[0]), _ptr(out))
        if rc != 0:
            raise RuntimeError("mp_diffuse 返回 %d" % rc)
        return out.reshape(H, W)
