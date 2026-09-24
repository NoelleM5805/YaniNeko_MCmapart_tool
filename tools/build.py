# -*- coding: utf-8 -*-
"""
打包成单文件 exe（PyInstaller onefile）
=====================================

产出：
    dist/maptool/Pictomapart.exe      单文件程序
    dist/maptool/使用说明.txt          从 packaging/ 复制
    dist/maptool/换端口启动.bat        从 packaging/ 复制（GBK 编码）

打包进去的资源（运行时由 maptool.config 定位到 _MEIPASS）：
    web/     前端（index.html + css + js）
    data/    block_icons.png、minecraft_blocks_mapcolor.json
    maptool.data.blockdata 作为普通模块打进包里（不需要当资源）

用法（在工作区根目录执行）：
    python tools/build.py            # 完整打包
    python tools/build.py --check    # 只做打包前的检查，不真的打包
"""

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
WEB = os.path.join(SRC, "maptool", "web")
DATA = os.path.join(SRC, "maptool", "data")
PACKAGING = os.path.join(ROOT, "packaging")
BUILD = os.path.join(ROOT, "build")
DIST = os.path.join(ROOT, "dist", "maptool")

NAME = "Pictomapart"
ENTRY = os.path.join(SRC, "run_maptool.py")

HIDDEN = [
    "maptool.data.blockdata",
    "litemapy", "nbtlib", "numpy",
    "anyio", "sniffio", "h11", "click", "multipart", "python_multipart",
]
COLLECT = ["fastapi", "uvicorn", "starlette", "pydantic"]


def preflight():
    """打包前先确认该有的文件都在、包能导入。"""
    problems = []
    for p, what in ((os.path.join(WEB, "index.html"), "前端首页"),
                    (os.path.join(WEB, "css", "style.css"), "样式表"),
                    (os.path.join(WEB, "js", "00-util.js"), "前端脚本"),
                    (os.path.join(DATA, "blockdata.py"), "方块数据"),
                    (os.path.join(DATA, "block_icons.png"), "图标贴图集"),
                    (ENTRY, "打包入口")):
        if not os.path.isfile(p):
            problems.append("缺少%s：%s" % (what, p))
    if problems:
        for x in problems:
            print("  [×] " + x)
        raise SystemExit("打包前检查未通过")
    print("  [√] 资源文件齐全")

    env = dict(os.environ, PYTHONPATH=SRC, PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, "-c",
                        "import maptool.webapp as w;"
                        "print(len(w.app.routes), 'routes')"],
                       cwd=SRC, env=env, capture_output=True)
    if r.returncode != 0:
        print(r.stdout.decode("utf-8", "replace"))
        print(r.stderr.decode("utf-8", "replace"))
        raise SystemExit("包导入失败，先修好再打包")
    print("  [√] maptool 包可导入：" + r.stdout.decode("utf-8", "replace").strip())


def build():
    if shutil.which("pyinstaller") is None:
        r = subprocess.run([sys.executable, "-m", "PyInstaller", "--version"],
                           capture_output=True)
        if r.returncode != 0:
            raise SystemExit("装一下 PyInstaller：pip install pyinstaller")
        pyi = [sys.executable, "-m", "PyInstaller"]
    else:
        pyi = ["pyinstaller"]

    for d in (BUILD, os.path.join(ROOT, "dist")):
        shutil.rmtree(d, ignore_errors=True)

    cmd = pyi + [
        "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", NAME,
        "--distpath", os.path.join(BUILD, "dist"),
        "--workpath", os.path.join(BUILD, "work"),
        "--specpath", BUILD,
        "--paths", SRC,
        "--add-data", WEB + ";web",
        "--add-data", os.path.join(DATA, "block_icons.png") + ";data",
        "--add-data", os.path.join(DATA, "minecraft_blocks_mapcolor.json") + ";data",
    ]
    for h in HIDDEN:
        cmd += ["--hidden-import", h]
    for c in COLLECT:
        cmd += ["--collect-all", c]
    cmd.append(ENTRY)

    print("\n运行 PyInstaller…")
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0:
        raise SystemExit("PyInstaller 失败（返回码 %d）" % r.returncode)

    exe = os.path.join(BUILD, "dist", NAME + ".exe")
    if not os.path.isfile(exe):
        raise SystemExit("没找到生成的 exe：%s" % exe)

    os.makedirs(DIST, exist_ok=True)
    shutil.copy2(exe, os.path.join(DIST, NAME + ".exe"))
    for fn in ("使用说明.txt", "换端口启动.bat"):
        s = os.path.join(PACKAGING, fn)
        if os.path.isfile(s):
            shutil.copy2(s, os.path.join(DIST, fn))
        else:
            print("  ! packaging/%s 不存在，发行版里就没有这个文件" % fn)

    size = os.path.getsize(os.path.join(DIST, NAME + ".exe")) / 1024 / 1024
    print("\n" + "=" * 60)
    print("打包完成：%s  （%.1f MB）" % (os.path.join(DIST, NAME + ".exe"), size))
    for fn in sorted(os.listdir(DIST)):
        print("   %s" % fn)
    print("=" * 60)


def main():
    check_only = "--check" in sys.argv
    print("打包前检查：")
    preflight()
    if check_only:
        return
    build()


if __name__ == "__main__":
    main()
