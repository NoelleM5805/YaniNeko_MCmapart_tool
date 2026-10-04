# -*- coding: utf-8 -*-
"""
打包发行版（PyInstaller onefile）
================================

产出（全部在 dist/ 下，互不覆盖）：

    dist/YaniNeko_MCmapart_tool/YaniNeko_MCmapart_tool.exe
    dist/YaniNeko_MCmapart_tool/使用说明.txt
    dist/YaniNeko_MCmapart_tool_v<版本>.zip      （整个文件夹打成一个 zip，方便直接发）

打包进去的资源（运行时由 maptool.config 定位到 _MEIPASS）：
    web/       前端（index.html + css + js）
    data/      block_icons.png、minecraft_blocks_mapcolor.json
    native/    maptool_native.dll（C++ 抖动核心，可选）
    maptool.data.blockdata 作为普通模块打进包里（不需要当资源）

用法（在工作区根目录执行）：
    python tools/build.py            # 完整打包
    python tools/build.py --check    # 只做打包前的检查，不真的打包
    python tools/build.py --no-zip   # 不生成 zip
"""

import os
import re
import shutil
import subprocess
import sys
import time
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
WEB = os.path.join(SRC, "maptool", "web")
DATA = os.path.join(SRC, "maptool", "data")
NATIVE = os.path.join(SRC, "maptool", "native")
NATIVE_DLL = os.path.join(NATIVE, "maptool_native.dll")
PACKAGING = os.path.join(ROOT, "packaging")
BUILD = os.path.join(ROOT, "build")
DIST = os.path.join(ROOT, "dist")

APP_NAME = "YaniNeko_MCmapart_tool"
ENTRY = os.path.join(SRC, "run_maptool.py")

HIDDEN = [
    "maptool.data.blockdata",
    "litemapy", "nbtlib", "numpy",
    "anyio", "sniffio", "h11", "click", "multipart", "python_multipart",
]
COLLECT = ["fastapi", "uvicorn", "starlette", "pydantic"]

# PyInstaller 把 INFO 日志全写到 stderr，在 PowerShell / cmd 里看着像报错。
# 这些才是要看的。
NOISE = re.compile(r"^\d+ (INFO|DEBUG|TRACE)\b")
KEEP = re.compile(r"(WARNING|ERROR|Traceback|Exception|Error:)")


def version():
    """从包里读版本号（__init__.py 只有文档字符串，导入很轻）。"""
    p = os.path.join(SRC, "maptool", "__init__.py")
    txt = open(p, encoding="utf-8").read()
    m = re.search(r'^__version__\s*=\s*"([^"]+)"', txt, re.M)
    if not m:
        raise SystemExit("读不到版本号：%s" % p)
    return m.group(1)


def rm_rf(path, tries=8, wait=0.6):
    """
    删目录/文件，带重试。

    刚写完的大文件（比如 34 MB 的 exe / zip）常常还被 Windows Defender 或者
    搜索索引器占着一小会儿，直接删会 WinError 32。等一下再试通常就好了。
    """
    if not os.path.exists(path):
        return
    last = None
    for i in range(tries):
        try:
            shutil.rmtree(path, ignore_errors=False)
            return
        except (PermissionError, OSError) as e:
            last = e
            time.sleep(wait)
    raise SystemExit(
        "删不掉 %s：%s\n"
        "多半是还有进程占着它 —— 关掉正在运行的 exe、或者稍等几秒再打包。"
        % (path, last))


def rm_file(path, tries=8, wait=0.6):
    if not os.path.exists(path):
        return
    last = None
    for i in range(tries):
        try:
            os.unlink(path)
            return
        except (PermissionError, OSError) as e:
            last = e
            time.sleep(wait)
    raise SystemExit(
        "删不掉 %s：%s\n"
        "多半是还有进程占着它（杀毒软件扫描、或者 zip 还开着）。稍等几秒再打包。"
        % (path, last))


def exe_stem():
    return APP_NAME


def preflight(ver):
    """打包前先确认该有的文件都在、包能导入。"""
    problems = []
    for p, what in ((os.path.join(WEB, "index.html"), "前端首页"),
                    (os.path.join(WEB, "css", "style.css"), "样式表"),
                    (os.path.join(WEB, "js", "00-util.js"), "前端脚本"),
                    (os.path.join(WEB, "js", "88-repair.js"), "噪点修正脚本"),
                    (os.path.join(WEB, "js", "89-undo.js"), "撤回脚本"),
                    (os.path.join(DATA, "blockdata.py"), "方块数据"),
                    (os.path.join(DATA, "block_icons.png"), "图标贴图集"),
                    (ENTRY, "打包入口")):
        if not os.path.isfile(p):
            problems.append("缺少%s：%s" % (what, p))
    if problems:
        for x in problems:
            print("  [×] " + x)
        raise SystemExit("打包前检查未通过")
    print("  [√] 资源文件齐全（版本 %s）" % ver)

    # C++ 抖动核心是可选的：没有 DLL 会自动退回纯 Python（结果一样，慢一些）
    if os.path.isfile(NATIVE_DLL):
        print("  [√] C++ 抖动核心：%.1f KB" % (os.path.getsize(NATIVE_DLL) / 1024.0))
    else:
        print("  ! 没有 %s —— 打包出来的程序会走纯 Python 抖动（结果一样，慢一些）"
              % os.path.basename(NATIVE_DLL))
        print("    要带上就装个 MinGW-w64 然后跑：python tools\\build_native.py")

    env = dict(os.environ, PYTHONPATH=SRC, PYTHONIOENCODING="utf-8")
    r = subprocess.run(
        [sys.executable, "-c",
         "import maptool.webapp as w, maptool.native as n;"
         "print(len(w.app.routes), 'routes;',"
         "'native', 'on' if n.available() else 'off')"],
        cwd=SRC, env=env, capture_output=True)
    if r.returncode != 0:
        print(r.stdout.decode("utf-8", "replace"))
        print(r.stderr.decode("utf-8", "replace"))
        raise SystemExit("包导入失败，先修好再打包")
    print("  [√] maptool 包可导入：" + r.stdout.decode("utf-8", "replace").strip())


def kill_running_exe():
    """
    上一次打包出来的 exe 如果还在跑，文件被占用，PyInstaller 复制时会
    PermissionError。这里先把它关掉（同名进程，只看名字，不动别的）。
    """
    try:
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq %s.exe" % exe_stem()],
                           capture_output=True)
        out = r.stdout.decode("utf-8", "replace").lower()
        if exe_stem().lower() + ".exe" in out:
            print("  ! 检测到 %s.exe 正在运行，先结束它（否则文件被占用没法覆盖）"
                  % exe_stem())
            subprocess.run(["taskkill", "/IM", "%s.exe" % exe_stem(), "/F"],
                           capture_output=True)
            import time
            time.sleep(1.5)
            return True
    except Exception:                                        # noqa: BLE001
        pass
    return False


def run_pyinstaller():
    if shutil.which("pyinstaller") is None:
        r = subprocess.run([sys.executable, "-m", "PyInstaller", "--version"],
                           capture_output=True)
        if r.returncode != 0:
            raise SystemExit("装一下 PyInstaller：pip install pyinstaller")
        pyi = [sys.executable, "-m", "PyInstaller"]
    else:
        pyi = ["pyinstaller"]

    # 中间产物每次都清掉；dist/ 下别的发行版不动
    shutil.rmtree(BUILD, ignore_errors=True)

    cmd = pyi + [
        "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", exe_stem(),
        "--distpath", os.path.join(BUILD, "dist"),
        "--workpath", os.path.join(BUILD, "work"),
        "--specpath", BUILD,
        "--paths", SRC,
        "--add-data", WEB + ";web",
        "--add-data", os.path.join(DATA, "block_icons.png") + ";data",
        "--add-data", os.path.join(DATA, "minecraft_blocks_mapcolor.json") + ";data",
    ]
    if os.path.isfile(NATIVE_DLL):
        # maptool/native/__init__.py 的 _candidates() 会在 _MEIPASS/native/ 下找
        cmd += ["--add-data", NATIVE_DLL + ";native"]
    for h in HIDDEN:
        cmd += ["--hidden-import", h]
    for c in COLLECT:
        cmd += ["--collect-all", c]
    cmd.append(ENTRY)

    print("\n运行 PyInstaller（日志只显示 WARNING / ERROR）…")
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True)
    lines = (r.stdout.decode("utf-8", "replace") + "\n"
             + r.stderr.decode("utf-8", "replace")).splitlines()
    shown = [ln for ln in lines if (KEEP.search(ln) and not NOISE.match(ln.strip()))
             or ln.strip().startswith("Traceback")]
    for ln in shown[:40]:
        print("   " + ln.strip()[:200])
    if len(shown) > 40:
        print("   …还有 %d 行" % (len(shown) - 40))
    if not shown:
        print("   （没有警告）")

    exe = os.path.join(BUILD, "dist", exe_stem() + ".exe")
    if r.returncode != 0 or not os.path.isfile(exe):
        raise SystemExit("PyInstaller 失败（返回码 %d）" % r.returncode)
    return exe


def make_zip(out_dir, ver, tries=4, wait=0.5):
    """
    把发行版目录打成一个 zip。

    zip 只是顺手的方便件，**不是必须的**：刚写完的大文件有时会被
    Windows Defender / 搜索索引器占住一小会儿，删不掉也改不了名。
    那种情况下就换个名字写，并且只警告、不中断打包 ——
    真正要交付的是那个目录。
    """
    target = os.path.join(DIST, "%s_v%s.zip" % (APP_NAME, ver))

    def write(path):
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            for fn in sorted(os.listdir(out_dir)):
                z.write(os.path.join(out_dir, fn), os.path.join(APP_NAME, fn))
        return path

    # 1) 目标位置可用就直接写
    for i in range(tries):
        if not os.path.exists(target):
            break
        try:
            os.unlink(target)
            break
        except OSError:
            time.sleep(wait)
    if not os.path.exists(target):
        try:
            return write(target)
        except OSError as e:
            print("  ! 写不进 %s（%s）" % (target, e))

    # 2) 目标被占用 -> 换个名字，别让整次打包失败
    alt = os.path.join(DIST, "%s_v%s_new.zip" % (APP_NAME, ver))
    for i in range(tries):
        try:
            if os.path.exists(alt):
                os.unlink(alt)
            p = write(alt)
            print("  ! 原来的 zip 被别的程序占着删不掉，这次写成了：%s" % p)
            print("    （关掉占用的程序后重跑一次，就会回到正常文件名）")
            return p
        except OSError:
            time.sleep(wait)
    print("  ! 生成 zip 失败（文件被占用），跳过 —— 发行版目录本身是好的")
    return None


def assemble(exe, ver, want_zip=True):
    out = os.path.join(DIST, APP_NAME)
    # 只清掉这一个发行版目录（dist/ 下别的版本/其他东西不动）
    rm_rf(out)
    os.makedirs(out, exist_ok=True)

    target_exe = os.path.join(out, exe_stem() + ".exe")
    shutil.copy2(exe, target_exe)

    # 说明文件：从 packaging/ 复制，顺手把里面的版本号替换掉
    txt_src = os.path.join(PACKAGING, "使用说明.txt")
    if os.path.isfile(txt_src):
        body = open(txt_src, encoding="utf-8").read()
        body = body.replace("__VERSION__", ver)
        open(os.path.join(out, "使用说明.txt"), "w", encoding="utf-8",
             newline="\r\n").write(body)
    else:
        print("  ! packaging/使用说明.txt 不存在，发行版里就没有它")


    zp = make_zip(out, ver) if want_zip else None
    return out, zp


def main():
    check_only = "--check" in sys.argv
    make_zip = "--no-zip" not in sys.argv
    ver = version()

    print("打包 %s v%s" % (APP_NAME, ver))
    print("打包前检查：")
    preflight(ver)
    if check_only:
        return

    kill_running_exe()
    exe = run_pyinstaller()
    out, zp = assemble(exe, ver, make_zip)

    size = os.path.getsize(os.path.join(out, exe_stem() + ".exe")) / 1024 / 1024
    print("\n" + "=" * 62)
    print("打包完成  %s v%s" % (APP_NAME, ver))
    print("=" * 62)
    print("目录：%s" % out)
    for fn in sorted(os.listdir(out)):
        print("   %-34s %8.1f KB" % (fn, os.path.getsize(os.path.join(out, fn)) / 1024.0))
    print("主程序：%.1f MB" % size)
    if zp:
        print("压缩包：%s  （%.1f MB）"
              % (zp, os.path.getsize(zp) / 1024 / 1024))
    print("=" * 62)


if __name__ == "__main__":
    main()
