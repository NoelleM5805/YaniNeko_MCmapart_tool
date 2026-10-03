# -*- coding: utf-8 -*-
import os, re, subprocess, sys

ROOT = r"D:\地图画工具"
VER = "1.37"
APP = "YaniNeko_MCmapart_tool"
BUILD = os.path.join(ROOT, "build", "release-v1.37")
INIT = os.path.join(ROOT, "src", "maptool", "__init__.py")
LOG = os.path.join(BUILD, "pyinstaller.log")

os.makedirs(BUILD, exist_ok=True)

def main():
    with open(INIT, "r", encoding="utf-8") as f:
        orig = f.read()
    try:
        new = re.sub(r'__version__\s*=\s*"[^"]+"', f'__version__ = "{VER}"', orig, count=1)
        with open(INIT, "w", encoding="utf-8", newline="\n") as f:
            f.write(new)

        args = [
            sys.executable, "-m", "PyInstaller",
            "--noconfirm", "--clean", "--onedir", "--windowed",
            "--name", APP,
            "--distpath", os.path.join(BUILD, "dist"),
            "--workpath", os.path.join(BUILD, "work"),
            "--specpath", BUILD,
            "--paths", os.path.join(ROOT, "src"),
            "--add-data", os.path.join(ROOT, "src", "maptool", "web") + ";web",
            "--add-data", os.path.join(ROOT, "src", "maptool", "data", "block_icons.png") + ";data",
            "--add-data", os.path.join(ROOT, "src", "maptool", "data", "minecraft_blocks_mapcolor.json") + ";data",
            "--add-data", os.path.join(ROOT, "src", "maptool", "native", "maptool_native.dll") + ";native",
        ]
        hidden = ["maptool.data.blockdata", "litemapy", "nbtlib", "numpy", "anyio",
                  "sniffio", "h11", "click", "multipart", "python_multipart"]
        for h in hidden:
            args += ["--hidden-import", h]
        for c in ["fastapi", "uvicorn", "starlette", "pydantic"]:
            args += ["--collect-all", c]
        args.append(os.path.join(ROOT, "src", "run_maptool.py"))

        print("Building v%s ..." % VER, flush=True)
        with open(LOG, "w", encoding="utf-8") as log:
            p = subprocess.run(args, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        print("PyInstaller returncode:", p.returncode, flush=True)
        with open(LOG, "r", encoding="utf-8", errors="replace") as log:
            text = log.read()
        print(text[-2500:], flush=True)
        return p.returncode
    finally:
        with open(INIT, "w", encoding="utf-8", newline="\n") as f:
            f.write(orig)
        print("restored __init__.py", flush=True)

if __name__ == "__main__":
    raise SystemExit(main())