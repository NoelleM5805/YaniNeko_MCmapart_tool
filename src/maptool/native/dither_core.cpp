// ============================================================================
// YaniNeko_MCmapart_tool 抖动计算核心（C++）
// ============================================================================
//
// 为什么是「普通 C DLL + ctypes」而不是 Python 扩展模块：
//   这台机器上 CPython 是 MSVC 编译的，而唯一的编译器是 MinGW-w64 g++。
//   用 MinGW 编 CPython 扩展要跨 CRT（msvcrt vs ucrt），PyObject* 传过边界
//   容易出问题。改成导出纯 C ABI（只有指针和整数），ctypes 调用就完全没有
//   ABI 风险，也不需要 Python 头文件。
//
// 为什么能保证和 Python 版逐像素一致：
//   误差扩散只有两个热点 —— 内层循环和「找最接近的调色板颜色」。
//   这两块都搬到这里，但**每一个浮点运算的顺序都和 Python/numpy 版逐一对应**：
//     · 工作缓冲用 double（Python 那边 .tolist() 之后也是 double）
//     · 整数通道相减、先乘后加、左结合，全部照抄
//     · hypot 一律用 sqrt(a*a+b*b) —— 实测和 math.hypot / np.hypot 逐位一致
//   剩下的不确定点（numpy 的 cos/sin 和 libm 差 1 ULP）由测试盖住：
//   tests/native_match_equiv.py 会把 256x256x256 全部 1677 万种量化颜色的
//   匹配结果和 numpy 版逐个比一遍。全过 = 任意输入下都等价。
//
// 编译：python tools/build_native.py
// ============================================================================

#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <new>

#if defined(_WIN32)
#define MP_EXPORT extern "C" __declspec(dllexport)
#else
#define MP_EXPORT extern "C" __attribute__((visibility("default")))
#endif

namespace {

constexpr double PI = 3.141592653589793238462643383279502884;
constexpr double POW25_7 = 6103515625.0;   // 25**7

// Python / numpy 的取模语义（结果的符号跟着除数）
inline double pymod(double x, double y) {
    double r = std::fmod(x, y);
    if (r != 0.0 && ((r < 0.0) != (y < 0.0))) r += y;
    return r;
}

// 7 次方。这里刻意不用 std::pow(x, 7.0)：pow 对浮点指数是通用实现，
// 一次要几十纳秒；而 CIEDE2000 里每个「像素 × 调色板颜色」要算两次，
// 100 万像素 × 59 色就是上亿次调用，实测占了 ciede2000 的大部分时间。
// 换成乘法链的前提是结果逐位一致 —— tools 里的数学探测脚本确认过
// std::pow(x,7.0) 和 ((x*x*x)*(x*x*x))*x 在 4 万组样本上完全相同，
// 而且两者都和 numpy 的 x**7 一致。全空间等价性测试会再兜一次底。
inline double pow7(double x) {
    double x2 = x * x;
    double x3 = x2 * x;
    return (x3 * x3) * x;
}

// ---------------------------------------------------------------- 颜色空间
// 与 maptool/colorspace.py 的 _srgb_to_linear / rgb_to_lab 逐运算对齐
inline double srgb_to_linear(double c) {
    c = c / 255.0;
    return c <= 0.04045 ? c / 12.92 : std::pow((c + 0.055) / 1.055, 2.4);
}

inline void rgb_to_lab(int r, int g, int b, double& L, double& A, double& B) {
    double rl = srgb_to_linear((double)r);
    double gl = srgb_to_linear((double)g);
    double bl = srgb_to_linear((double)b);
    double x = rl * 0.4124564 + gl * 0.3575761 + bl * 0.1804375;
    double y = rl * 0.2126729 + gl * 0.7151522 + bl * 0.0721750;
    double z = rl * 0.0193339 + gl * 0.1191920 + bl * 0.9503041;
    x /= 0.95047;
    y /= 1.00000;
    z /= 1.08883;
    double fx = x > 0.008856 ? std::pow(x, 1.0 / 3.0) : 7.787 * x + 16.0 / 116.0;
    double fy = y > 0.008856 ? std::pow(y, 1.0 / 3.0) : 7.787 * y + 16.0 / 116.0;
    double fz = z > 0.008856 ? std::pow(z, 1.0 / 3.0) : 7.787 * z + 16.0 / 116.0;
    L = 116.0 * fy - 16.0;
    A = 500.0 * (fx - fy);
    B = 200.0 * (fy - fz);
}

// ---------------------------------------------------------------- 匹配算法
enum Mode {
    M_SEPARABLE = 0,   // euclidean / weighted：距离可拆成三张 256xN 的表
    M_REDMEAN = 1,
    M_CIE76 = 2,
    M_CIE94 = 3,
    M_CIEDE2000 = 4,
};

struct Palette {
    int mode = M_SEPARABLE;
    int n = 0;
    const double* colors = nullptr;   // n*3 RGB
    const int32_t* pr = nullptr;      // n   （redmean 用整数通道）
    const int32_t* pg = nullptr;
    const int32_t* pb = nullptr;
    const double* TR = nullptr;       // 256*n
    const double* TG = nullptr;
    const double* TB = nullptr;
    const double* lab = nullptr;      // n*3
    const double* labc = nullptr;     // n
};

// 第一条比当前最小值还小时取它（等价 numpy argmin 的「取第一个最小」）
inline bool better(int i, double d, double best) { return i == 0 || d < best; }

inline int match_separable(int r, int g, int b, const Palette& p) {
    int best = 0;
    double bestv = 0.0;
    for (int i = 0; i < p.n; ++i) {
        // python: int((TR[rc] + TG[gc] + TB[bc]).argmin())
        double d = (p.TR[(size_t)r * p.n + i] + p.TG[(size_t)g * p.n + i])
                   + p.TB[(size_t)b * p.n + i];
        if (better(i, d, bestv)) { bestv = d; best = i; }
    }
    return best;
}

inline int match_redmean(int r, int g, int b, const Palette& p) {
    int best = 0;
    double bestv = 0.0;
    for (int i = 0; i < p.n; ++i) {
        int pri = p.pr[i], pgi = p.pg[i], pbi = p.pb[i];
        double rmean = (double)(r + pri) * 0.5;
        int dr = r - pri, dg = g - pgi, db = b - pbi;
        // 顺序照抄 numpy：(A + B) + C，其中 B 是 int32 的 4*dg*dg
        double term1 = ((2.0 + rmean / 256.0) * (double)dr) * (double)dr;
        int term2i = (4 * dg) * dg;
        double term3 = ((2.0 + (255.0 - rmean) / 256.0) * (double)db) * (double)db;
        double d = (term1 + (double)term2i) + term3;
        if (better(i, d, bestv)) { bestv = d; best = i; }
    }
    return best;
}

inline int match_cie76(double L, double A, double B, const Palette& p) {
    int best = 0;
    double bestv = 0.0;
    for (int i = 0; i < p.n; ++i) {
        double dL = L - p.lab[(size_t)i * 3 + 0];
        double da = A - p.lab[(size_t)i * 3 + 1];
        double db = B - p.lab[(size_t)i * 3 + 2];
        double d = (dL * dL + da * da) + db * db;
        if (better(i, d, bestv)) { bestv = d; best = i; }
    }
    return best;
}

inline int match_cie94(double L, double A, double B, const Palette& p) {
    double C1 = std::sqrt(A * A + B * B);          // == math.hypot(A, B)
    double SC = 1 + 0.045 * C1;
    double SH = 1 + 0.015 * C1;
    int best = 0;
    double bestv = 0.0;
    for (int i = 0; i < p.n; ++i) {
        double dL = L - p.lab[(size_t)i * 3 + 0];
        double dC = C1 - p.labc[i];
        double da = A - p.lab[(size_t)i * 3 + 1];
        double db = B - p.lab[(size_t)i * 3 + 2];
        double inner = (da * da + db * db) - dC * dC;
        double dH2 = inner > 0.0 ? inner : 0.0;    // np.maximum(0.0, inner)
        double q = dC / SC;
        double d = (dL * dL + q * q) + dH2 / (SH * SH);
        if (better(i, d, bestv)) { bestv = d; best = i; }
    }
    return best;
}

inline int match_ciede2000(double L1, double A1, double B1, const Palette& p) {
    // 注：Python 版的 _ciede2000_dist 里还有 C1_7 = C1**7 和 C2_7 = C2**7，
    // 两个都没被用到（标准 CIEDE2000 公式里的遗留），这里就不算了 ——
    // 是纯计算、没有副作用，省掉不影响任何结果。
    double C1 = std::sqrt(A1 * A1 + B1 * B1);      // np.hypot(A1, B1)
    int best = 0;
    double bestv = 0.0;
    for (int i = 0; i < p.n; ++i) {
        double L2 = p.lab[(size_t)i * 3 + 0];
        double A2 = p.lab[(size_t)i * 3 + 1];
        double B2 = p.lab[(size_t)i * 3 + 2];
        double C2 = p.labc[i];
        double Cbar = (C1 + C2) * 0.5;
        double Cbar_7 = pow7(Cbar);
        double G = 0.5 * (1 - std::sqrt(Cbar_7 / (Cbar_7 + POW25_7)));
        double A1p = A1 * (1 + G);
        double A2p = A2 * (1 + G);
        double C1p = std::sqrt(A1p * A1p + B1 * B1);
        double C2p = std::sqrt(A2p * A2p + B2 * B2);
        double h1p = pymod(std::atan2(B1, A1p) * (180.0 / PI), 360.0);
        double h2p = pymod(std::atan2(B2, A2p) * (180.0 / PI), 360.0);
        double dLp = L2 - L1;
        double dCp = C2p - C1p;

        double dh = h2p - h1p;
        double adh = std::fabs(dh);
        double dhp = adh <= 180.0 ? dh : (dh > 180.0 ? dh - 360.0 : dh + 360.0);
        double dHp = 2.0 * std::sqrt(C1p * C2p)
                     * std::sin((dhp * (PI / 180.0)) * 0.5);

        double Lbarp = (L1 + L2) * 0.5;
        double Cbarp = (C1p + C2p) * 0.5;
        double hsum = h1p + h2p;
        double hbarp;
        if (C1p * C2p == 0.0) {
            hbarp = h1p + h2p;
        } else if (std::fabs(h1p - h2p) <= 180.0) {
            hbarp = hsum * 0.5;
        } else if (hsum < 360.0) {
            hbarp = (hsum + 360.0) * 0.5;
        } else {
            hbarp = (hsum - 360.0) * 0.5;
        }

        double T = 1.0 - 0.17 * std::cos((hbarp - 30.0) * (PI / 180.0))
                       + 0.24 * std::cos((2.0 * hbarp) * (PI / 180.0))
                       + 0.32 * std::cos((3.0 * hbarp + 6.0) * (PI / 180.0))
                       - 0.20 * std::cos((4.0 * hbarp - 63.0) * (PI / 180.0));
        double u = (hbarp - 275.0) / 25.0;
        double dTheta = 30.0 * std::exp(-(u * u));
        double Cbarp_7 = pow7(Cbarp);
        double RC = 2.0 * std::sqrt(Cbarp_7 / (Cbarp_7 + POW25_7));
        double Lm = Lbarp - 50.0;
        double Lm2 = Lm * Lm;
        double SL = 1.0 + (0.015 * Lm2) / std::sqrt(20.0 + Lm2);
        double SC = 1.0 + 0.045 * Cbarp;
        double SH = 1.0 + 0.015 * Cbarp * T;
        double RT = -std::sin((2.0 * dTheta) * (PI / 180.0)) * RC;

        double q1 = dLp / SL;
        double q2 = dCp / SC;
        double q3 = dHp / SH;
        double d = ((q1 * q1 + q2 * q2) + q3 * q3) + (RT * q2) * q3;
        if (better(i, d, bestv)) { bestv = d; best = i; }
    }
    return best;
}

inline int match_rgb(int r, int g, int b, const Palette& p) {
    switch (p.mode) {
        case M_SEPARABLE:  return match_separable(r, g, b, p);
        case M_REDMEAN:    return match_redmean(r, g, b, p);
        case M_CIE76: {
            double L, A, B;
            rgb_to_lab(r, g, b, L, A, B);
            return match_cie76(L, A, B, p);
        }
        case M_CIE94: {
            double L, A, B;
            rgb_to_lab(r, g, b, L, A, B);
            return match_cie94(L, A, B, p);
        }
        default: {
            double L, A, B;
            rgb_to_lab(r, g, b, L, A, B);
            return match_ciede2000(L, A, B, p);
        }
    }
}

// ------------------------------------------------- 量化颜色的哈希缓存
// 误差扩散里同一个量化颜色会反复出现，缓存住就不用每次重算。
// 用开放寻址哈希表：keys 全 0xFFFFFFFF 表示空，每次调用前 memset 一次。
struct MatchCache {
    uint32_t* keys = nullptr;
    uint8_t* vals = nullptr;
    uint32_t mask = 0;

    bool init(size_t npix) {
        size_t cap = 16;
        while (cap < npix * 2) cap <<= 1;
        keys = (uint32_t*)std::malloc(cap * sizeof(uint32_t));
        vals = (uint8_t*)std::malloc(cap);
        if (!keys || !vals) return false;
        mask = (uint32_t)(cap - 1);
        std::memset(keys, 0xFF, cap * sizeof(uint32_t));
        return true;
    }
    void destroy() { std::free(keys); std::free(vals); keys = nullptr; vals = nullptr; }

    inline uint32_t hash(uint32_t k) const {
        k ^= k >> 16; k *= 0x7feb352dU; k ^= k >> 15; k *= 0x846ca68bU; k ^= k >> 16;
        return k & mask;
    }
    inline int get(uint32_t key) const {
        uint32_t i = hash(key);
        while (true) {
            uint32_t stored = keys[i];
            if (stored == key) return vals[i];
            if (stored == 0xFFFFFFFFu) return -1;
            i = (i + 1) & mask;
        }
    }
    inline void put(uint32_t key, uint8_t val) {
        uint32_t i = hash(key);
        while (keys[i] != 0xFFFFFFFFu) {
            if (keys[i] == key) { vals[i] = val; return; }
            i = (i + 1) & mask;
        }
        keys[i] = key;
        vals[i] = val;
    }
};

}  // namespace

// ============================================================================
// 导出的 C ABI
// ============================================================================

MP_EXPORT const char* mp_version() { return "yanineko_mcmapart_tool-dither-cpp-1"; }

MP_EXPORT void* mp_pal_new(int mode, int n,
                           const double* colors, const int32_t* rgb_int,
                           const double* TR, const double* TG, const double* TB,
                           const double* lab, const double* labc) {
    if (n <= 0 || n > 255) return nullptr;
    Palette* p = new (std::nothrow) Palette();
    if (!p) return nullptr;
    p->mode = mode;
    p->n = n;
    p->colors = colors;
    if (rgb_int) {
        p->pr = rgb_int;
        p->pg = rgb_int + n;
        p->pb = rgb_int + 2 * n;
    }
    p->TR = TR; p->TG = TG; p->TB = TB;
    p->lab = lab;
    p->labc = labc;
    return (void*)p;
}

MP_EXPORT void mp_pal_free(void* h) { delete (Palette*)h; }

// 任意 RGB 列表的整批匹配（用于和 numpy 版做等价性验证）
MP_EXPORT int mp_match_rgb_list(void* h, const uint8_t* rgb, int count, uint8_t* out) {
    if (!h || !rgb || !out || count < 0) return -1;
    const Palette& p = *(const Palette*)h;
    for (int i = 0; i < count; ++i) {
        out[i] = (uint8_t)match_rgb(rgb[i * 3], rgb[i * 3 + 1], rgb[i * 3 + 2], p);
    }
    return 0;
}

// 把 256x256x256 全部量化颜色的匹配结果算出来（16 MB）。
// 这是等价性验证的入口：和 numpy 版逐个比一遍，全过就说明任意输入都一致。
MP_EXPORT int mp_match_lut(void* h, uint8_t* out) {
    if (!h || !out) return -1;
    const Palette& p = *(const Palette*)h;
    size_t k = 0;
    for (int r = 0; r < 256; ++r)
        for (int g = 0; g < 256; ++g)
            for (int b = 0; b < 256; ++b)
                out[k++] = (uint8_t)match_rgb(r, g, b, p);
    return 0;
}

// ---------------------------------------------------------------- 误差扩散
// src: H*W*3 float32 行优先；out: H*W int32
// tap_off/tap_w: 核的偏移（已换算成「元素下标增量」）和权重，顺序与 Python 一致
MP_EXPORT int mp_diffuse(void* h,
                         const float* src, int H, int W,
                         double strength,
                         const int32_t* tap_off, const double* tap_w, int ntaps,
                         int32_t* out) {
    if (!h || !src || !out || H <= 0 || W <= 0) return -1;
    const Palette& p = *(const Palette*)h;

    const int PAD = 2;
    const int PW = W + 2 * PAD;
    const int PH = H + 2 * PAD;
    const size_t npix = (size_t)H * W;

    double* work = (double*)std::calloc((size_t)PH * PW * 3, sizeof(double));
    if (!work) return -2;
    // 把 float32 源图搬进 double 工作缓冲（Python 的 .tolist() 也是这个效果）
    for (int y = 0; y < H; ++y) {
        const float* srow = src + (size_t)y * W * 3;
        double* drow = work + ((size_t)(y + PAD) * PW + PAD) * 3;
        for (size_t i = 0; i < (size_t)W * 3; ++i) drow[i] = (double)srow[i];
    }

    MatchCache cache;
    if (!cache.init(npix)) { std::free(work); return -3; }

    const int stride = PW * 3;
    const int row0 = (PAD * PW + PAD) * 3;
    size_t np = 0;

    for (int y = 0; y < H; ++y) {
        int idx = row0 + y * stride;
        for (int x = 0; x < W; ++x) {
            double r = work[idx];
            double g = work[idx + 1];
            double b = work[idx + 2];

            int rc = (int)(r + 0.5);
            if (rc < 0) rc = 0; else if (rc > 255) rc = 255;
            int gc = (int)(g + 0.5);
            if (gc < 0) gc = 0; else if (gc > 255) gc = 255;
            int bc = (int)(b + 0.5);
            if (bc < 0) bc = 0; else if (bc > 255) bc = 255;

            uint32_t key = ((uint32_t)rc << 16) | ((uint32_t)gc << 8) | (uint32_t)bc;
            int pi = cache.get(key);
            if (pi < 0) {
                pi = match_rgb(rc, gc, bc, p);
                cache.put(key, (uint8_t)pi);
            }
            out[np++] = pi;

            double pr = p.colors[(size_t)pi * 3 + 0];
            double pg = p.colors[(size_t)pi * 3 + 1];
            double pb = p.colors[(size_t)pi * 3 + 2];
            double er = (r - pr) * strength;
            double eg = (g - pg) * strength;
            double eb = (b - pb) * strength;

            for (int t = 0; t < ntaps; ++t) {
                int m = idx + tap_off[t];
                double k = tap_w[t];
                work[m] += er * k;
                work[m + 1] += eg * k;
                work[m + 2] += eb * k;
            }
            idx += 3;
        }
    }

    cache.destroy();
    std::free(work);
    return 0;
}
