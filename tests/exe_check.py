# -*- coding: utf-8 -*-
"""
发行版验证：打包出来的 exe 与解耦前单文件版逐项比对
==================================================

用的是和 tests/regression_check.py 完全同一套请求（直接 import 复用），
只是把「新版」换成打包出来的 exe，确认打包之后行为一样。

另外还检查：
  · 打进去的前端资源（含噪点修正 / 撤回脚本）能不能取到
  · 版本号、是否 frozen、C++ 抖动核心在包里有没有生效
  · /static/ 的越界读取有没有挡住
  · 打包出来的 换端口启动.bat 能不能真的换端口

用法（在工作区根目录执行）：
    python tests/exe_check.py
    先跑 python tools/build.py
"""
import glob
import json
import os
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import regression_check as R   # noqa: E402

DIST = os.path.join(ROOT, "dist")


def find_release():
    """找 dist/ 下的发行版：优先最新的，带 exe 的那个目录。"""
    best = None
    for d in glob.glob(os.path.join(DIST, "*")):
        if not os.path.isdir(d):
            continue
        for exe in glob.glob(os.path.join(d, "*.exe")):
            m = os.path.getmtime(exe)
            if best is None or m > best[2]:
                best = (d, exe, m)
    return best


_REL = find_release()
EXE = _REL[1] if _REL else os.path.join(DIST, "YaniNeko_MCmapart_tool",
                                        "YaniNeko_MCmapart_tool.exe")
EXE_DIR = os.path.dirname(EXE)
EXE_PORT = 8832

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  [√] " if ok else "  [×] ") + name + (("  " + detail) if detail and not ok else ""))


def kill_exe():
    subprocess.run(["taskkill", "/IM", os.path.basename(EXE), "/F"],
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

        print("启动 %s（端口 %d）…" % (os.path.basename(EXE), EXE_PORT))
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
                           ("/static/js/88-repair.js", b"rpExecDenoise"),
                           ("/static/js/89-undo.js", b"function undoStep"),
                           ("/static/js/99-start.js", b"keepaliveConnect"),
                           ("/api/icons.png", b"\x89PNG")):
            st, raw = R.req("http://127.0.0.1:%d%s" % (EXE_PORT, path))
            check("exe %s（%d 字节）" % (path, len(raw)),
                  st == 200 and must in raw, "状态 %d" % st)

        # 版本 / 运行环境 / C++ 核心有没有打进包里
        st, raw = R.req("http://127.0.0.1:%d/api/palette" % EXE_PORT)
        pal = json.loads(raw.decode("utf-8")) if st == 200 else {}
        check("exe 报告版本号：%s" % pal.get("version"), bool(pal.get("version")))
        check("exe 处于 frozen（真打包运行）", pal.get("frozen") is True)
        nat = pal.get("native") or {}
        check("C++ 抖动核心已打进包里并加载成功（%s）" % nat.get("version"),
              nat.get("available") is True,
              "load_error=%s" % nat.get("error"))

        # 越界读取必须被挡住
        st, _raw = R.req("http://127.0.0.1:%d/static/../run_maptool.py" % EXE_PORT)
        check("exe /static 挡越界读取", st == 404, "状态 %d" % st)

        # 发行版里的 换端口启动.bat：内容要是 GBK，且指向真正存在的 exe
        bat = os.path.join(EXE_DIR, "换端口启动.bat")
        if os.path.isfile(bat):
            with open(bat, "rb") as f:
                txt = f.read().decode("gbk")          # 解不出来就说明编码错了
            check("换端口启动.bat 是 GBK 且写好了端口",
                  "MAPART_PORT=8899" in txt and "8899" in txt)
            check("换端口启动.bat 指向存在的 exe",
                  ('"%~dp0' + os.path.basename(EXE) + '"') in txt)
        else:
            check("发行版里有 换端口启动.bat", False, bat)
        check("发行版里有 使用说明.txt",
              os.path.isfile(os.path.join(EXE_DIR, "使用说明.txt")))

        print("\n跑旧版测试集…")
        sel = R.shared_selection("http://127.0.0.1:%d" % R.OLD_PORT)
        print("  共用方块选择：%d 个" % len(sel))
        a = R.battery("http://127.0.0.1:%d" % R.OLD_PORT, img, sel)
        print("跑 exe 测试集…")
        b = R.battery("http://127.0.0.1:%d" % EXE_PORT, img, sel)

        print("\n比对结果：")
        for k in sorted(set(a) | set(b)):
            if k.startswith("_"):
                continue
            d = R.compare_any(k, a.get(k), b.get(k))
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
