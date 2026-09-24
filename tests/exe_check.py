# -*- coding: utf-8 -*-
"""
发行版验证：Pictomapart.exe 与解耦前单文件版逐项比对
==================================================

用的是和 tests/regression_check.py 完全同一套请求（直接 import 复用），
只是把「新版」换成打包出来的 exe，确认打包之后行为一样。

顺带检查 exe 里打进去的前端资源能不能正常取到。

用法（在工作区根目录执行）：
    python tests/exe_check.py
"""
import os
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import regression_check as R   # noqa: E402

EXE = os.path.join(ROOT, "dist", "maptool", "Pictomapart.exe")
EXE_PORT = 8832

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  [√] " if ok else "  [×] ") + name + (("  " + detail) if detail and not ok else ""))


def kill_exe():
    subprocess.run(["taskkill", "/IM", "Pictomapart.exe", "/F"],
                   capture_output=True)


def wait_port(port, timeout=90):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/api/palette" % port,
                                   timeout=3).read()
            return True
        except Exception:
            time.sleep(0.4)
    return False


def main():
    if not os.path.isfile(EXE):
        print("找不到 exe：%s\n先跑 python tools/build.py" % EXE)
        return 2

    kill_exe()
    img = R.make_test_image(os.path.join(R.TMP, "test.png"))

    old_proc = None
    try:
        print("启动旧版单文件服务…")
        env_old = dict(os.environ, MAPART_PORT=str(R.OLD_PORT),
                       PYTHONIOENCODING="utf-8")
        old_proc = subprocess.Popen([sys.executable, R.OLD_SCRIPT],
                                    cwd=R.OLD_CWD, env=env_old,
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
        if not wait_port(R.OLD_PORT):
            raise RuntimeError("旧版服务没起来")

        print("启动 Pictomapart.exe（端口 %d）…" % EXE_PORT)
        env_exe = dict(os.environ, MAPART_PORT=str(EXE_PORT))
        subprocess.Popen([EXE], env=env_exe, cwd=os.path.dirname(EXE),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not wait_port(EXE_PORT):
            raise RuntimeError("exe 没起来（端口 %d）" % EXE_PORT)
        print("  exe 就绪")

        # 打包进去的前端资源
        for path, must in (("/", b"<script src=\"/static/js/00-util.js\">"),
                           ("/static/css/style.css", b"box-sizing"),
                           ("/static/js/00-util.js", b"function $"),
                           ("/static/js/99-start.js", b"keepaliveConnect"),
                           ("/api/icons.png", b"\x89PNG")):
            st, raw = R.req("http://127.0.0.1:%d%s" % (EXE_PORT, path))
            check("exe %s（%d 字节）" % (path, len(raw)),
                  st == 200 and must in raw, "状态 %d" % st)

        # 越界读取必须被挡住
        st, _raw = R.req("http://127.0.0.1:%d/static/../run_maptool.py" % EXE_PORT)
        check("exe /static 挡越界读取", st == 404, "状态 %d" % st)

        print("\n跑旧版测试集…")
        a = R.battery("http://127.0.0.1:%d" % R.OLD_PORT, img)
        print("跑 exe 测试集…")
        b = R.battery("http://127.0.0.1:%d" % EXE_PORT, img)

        print("\n比对结果：")
        for k in sorted(set(a) | set(b)):
            d = R.compare(a.get(k), b.get(k), k)
            check(k, not d, "; ".join(d[:6]))
    finally:
        kill_exe()
        if old_proc:
            old_proc.kill()

    print("\n" + "=" * 60)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("  失败：" + f)
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
