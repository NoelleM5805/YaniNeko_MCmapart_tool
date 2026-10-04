# -*- coding: utf-8 -*-
"""
打包「多文件（onedir）」发行版
==============================

和 tools/build.py 的 onefile 版对应：PyInstaller 用 --onedir 模式，产出一个目录
（exe + _internal/ 依赖），启动更快（不用每次解包到临时目录），但文件多、要整个
目录一起发。这里把目录打成 zip，方便上传 / 分发。

用法（工作区根目录执行）：
    python tools/build_onedir.py

产出：
    dist/YaniNeko_MCmapart_tool_onedir/               ← 目录（可整个拷给别人）
    dist/YaniNeko_MCmapart_tool_v<版本>_onedir.zip    ← 上面目录打的包
"""

import os
import re
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
WEB = os.path.join(SRC, "maptool", "web")
DATA = os.path.join(SRC, "maptool", "data")
NATIVE_DLL = os.path.join(SRC, "maptool", "native", "maptool_native.dll")
PACKAGING = os.path.join(ROOT, "packaging")
BUILD = os.path.join(ROOT, "build", "onedir")
DIST = os.path.join(ROOT, "dist")

APP_NAME = "YaniNeko_MCmapart_tool"
ENTRY = os.path.join(SRC, "run_maptool.py")

HIDDEN = ["maptool.data.blockdata", "litemapy", "nbtlib", "numpy",
          "anyio", "sniffio", "h11", "click", "multipart", "python_multipart"]
COLLECT = ["fastapi", "uvicorn", "starlette", "pydantic"]

# PyInstaller 把 INFO 日志全写到 stderr，在 PowerShell / cmd 里看着像报错，只留重点
NOISE = re.compile(r"^\d+ (INFO|DEBUG|TRACE)\b")
KEEP = re.compile(r"(WARNING|ERROR|Traceback|Exception|Error:)")


def version():
    p = os.path.join(SRC, "maptool", "__init__.py")
    txt = open(p, encoding="utf-8").read()
    m = re.search(r'^__version__\s*=\s*"([^"]+)"', txt, re.M)
    if not m:
        raise SystemExit("读不到版本号：%s" % p)
    return m.group(1)


def main():
    ver = version()
    print("打包多文件（onedir）版 %s v%s" % (APP_NAME, ver))

    if not os.path.isfile(NATIVE_DLL):
        print("  ! 没有 %s —— 打包出来的程序会退回纯 Python 抖动（结果一样，慢一些）"
              % os.path.basename(NATIVE_DLL))

    shutil.rmtree(BUILD, ignore_errors=True)

    cmd = [sys.executable, "-m", "PyInstaller",
           "--noconfirm", "--clean", "--onedir", "--windowed",
           "--name", APP_NAME,
           "--distpath", os.path.join(BUILD, "dist"),
           "--workpath", os.path.join(BUILD, "work"),
           "--specpath", BUILD,
           "--paths", SRC,
           "--add-data", WEB + ";web",
           "--add-data", os.path.join(DATA, "block_icons.png") + ";data",
           "--add-data", os.path.join(DATA, "minecraft_blocks_mapcolor.json") + ";data",
           ]
    if os.path.isfile(NATIVE_DLL):
        cmd += ["--add-data", NATIVE_DLL + ";native"]
    for h in HIDDEN:
        cmd += ["--hidden-import", h]
    for c in COLLECT:
        cmd += ["--collect-all", c]
    cmd.append(ENTRY)

    print("\n运行 PyInstaller --onedir ...")
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True)
    lines = (r.stdout.decode("utf-8", "replace") + "\n"
             + r.stderr.decode("utf-8", "replace")).splitlines()
    shown = [ln for ln in lines if (KEEP.search(ln) and not NOISE.match(ln.strip()))
             or ln.strip().startswith("Traceback")]
    for ln in shown[:40]:
        print("   " + ln.strip()[:200])
    if len(shown) > 40:
        print("   ...还有 %d 行" % (len(shown) - 40))
    if not shown:
        print("   （没有警告）")

    built = os.path.join(BUILD, "dist", APP_NAME)
    if r.returncode != 0 or not os.path.isdir(built):
        raise SystemExit("PyInstaller 失败（返回码 %d）" % r.returncode)

    # 说明文件：从 packaging/ 复制，替换版本号
    txt_src = os.path.join(PACKAGING, "使用说明.txt")
    if os.path.isfile(txt_src):
        body = open(txt_src, encoding="utf-8").read().replace("__VERSION__", ver)
        open(os.path.join(built, "使用说明.txt"), "w", encoding="utf-8",
             newline="\r\n").write(body)

    # 复制到 dist/，并打成 zip
    out = os.path.join(DIST, APP_NAME + "_onedir")
    shutil.rmtree(out, ignore_errors=True)
    shutil.copytree(built, out)

    zip_path = os.path.join(DIST, "%s_v%s_onedir.zip" % (APP_NAME, ver))
    if os.path.exists(zip_path):
        os.unlink(zip_path)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for dirpath, _dirnames, filenames in os.walk(out):
            for fn in filenames:
                full = os.path.join(dirpath, fn)
                arc = os.path.join(APP_NAME + "_onedir",
                                   os.path.relpath(full, out))
                z.write(full, arc)

    size = os.path.getsize(zip_path) / 1024 / 1024
    print("\n" + "=" * 62)
    print("多文件版打包完成  %s v%s" % (APP_NAME, ver))
    print("=" * 62)
    print("目录：%s" % out)
    print("压缩包：%s  （%.1f MB）" % (zip_path, size))
    return 0


if __name__ == "__main__":
    sys.exit(main())
