// 数学函数一致性探测：MinGW libm vs Python/numpy
// 每条记录 2 个 float64 (a, b)，结果按位十六进制打印，由 Python 侧比对。
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <cmath>

static void emit(double v) {
    uint64_t u;
    std::memcpy(&u, &v, 8);
    std::printf("%016llx\n", (unsigned long long)u);
}

int main(int argc, char** argv) {
    if (argc < 2) { std::fprintf(stderr, "need input file\n"); return 2; }
    FILE* f = std::fopen(argv[1], "rb");
    if (!f) { std::fprintf(stderr, "open fail\n"); return 2; }
    double buf[2];
    const double PI = 3.141592653589793238462643383279502884;
    while (std::fread(buf, sizeof(double), 2, f) == 2) {
        double a = buf[0], b = buf[1];
        double t = std::fabs(a);

        emit(std::hypot(a, b));            // 0  hypot
        emit(std::sqrt(a * a + b * b));    // 1  sqrt(a*a+b*b)
        emit(std::atan2(a, b));            // 2  arctan2
        emit(a * (180.0 / PI));            // 3  degrees
        emit(a * (PI / 180.0));            // 4  radians
        emit(std::cos(a));                 // 5  cos
        emit(std::exp(a));                 // 6  exp
        emit(std::sqrt(t));                // 7  sqrt(|a|)
        emit(std::pow(t, 2.4));            // 8  pow(2.4)
        emit(std::pow(t, 1.0 / 3.0));      // 9  pow(1/3)
        emit(std::cbrt(t));                // 10 cbrt
        emit(std::pow(t, 7.0));            // 11 pow(7)
        emit(((t * t * t) * (t * t * t)) * t);  // 12 手动 7 次方
        emit(std::pow(t, 2.0));            // 13 pow(2)
        emit(t * t);                       // 14 t*t
        emit(std::sin(a));                 // 15 sin
    }
    std::fclose(f);
    return 0;
}
