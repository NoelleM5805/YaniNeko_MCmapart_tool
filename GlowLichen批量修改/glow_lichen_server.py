# -*- coding: utf-8 -*-
"""
Glow Lichen 批量修改工具 - 浏览器版后端
==========================================
接收上传的 .litematic 文件 → 修改 glow_lichen 面属性 → 返回处理后的文件。

依赖：
    pip install fastapi uvicorn litemapy python-multipart

运行：
    python glow_lichen_server.py
    浏览器会自动打开 http://127.0.0.1:8765

打包：
    pyinstaller --noconfirm --clean --onefile --windowed ^
        --name "GlowLichen批量修改" ^
        --add-data "index.html;." ^
        --hidden-import litemapy ^
        --hidden-import nbtlib ^
        --hidden-import numpy ^
        --collect-all fastapi ^
        --collect-all uvicorn ^
        --collect-all starlette ^
        --collect-all pydantic ^
        glow_lichen_server.py
"""

import os
import sys
import json
import time
import uuid
import tempfile
import threading
import traceback

import uvicorn
from fastapi import FastAPI, File, UploadFile, Form
from fastapi.responses import HTMLResponse, Response, JSONResponse

try:
    from litemapy import Schematic
except ImportError:
    print("缺少 litemapy 库，请执行：pip install litemapy")
    sys.exit(1)


# ============================================================
# 常量
# ============================================================
TARGET_BLOCK_ID = "minecraft:glow_lichen"
FACE_KEYS = ["down", "up", "north", "south", "east", "west"]
HOST = "127.0.0.1"
PORT = 8765

TASKS = {}
TASK_LOCK = threading.Lock()
MAX_LOGS = 1000
KEEP_TASKS = 10


def get_base_dir():
    """获取资源目录（兼容 PyInstaller 打包后的 sys._MEIPASS）。"""
    if hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


# ============================================================
# FastAPI
# ============================================================
app = FastAPI()


def add_log(task, msg):
    with TASK_LOCK:
        logs = task["logs"]
        logs.append(f"[{time.strftime('%H:%M:%S')}] {msg}")
        if len(logs) > MAX_LOGS:
            del logs[:len(logs) - MAX_LOGS]


# ------------------------------------------------------------
# 后台处理逻辑
# ------------------------------------------------------------
def process_file(task_id, file_bytes, original_name, faces_dict, waterlogged):
    task = TASKS[task_id]
    tmp_path = None
    try:
        # 写入临时文件（litemapy 需要文件路径）
        with tempfile.NamedTemporaryFile(suffix=".litematic", delete=False) as f:
            tmp_path = f.name
            f.write(file_bytes)

        target_faces = {k: ("true" if faces_dict.get(k) else "false")
                        for k in FACE_KEYS}
        target_faces["waterlogged"] = "true" if waterlogged else "false"

        add_log(task, "=" * 50)
        add_log(task, f"文件名：{original_name}")
        add_log(task, f"大小：{len(file_bytes) / 1024 / 1024:.2f} MB")
        add_log(task, f"目标方块：{TARGET_BLOCK_ID}")
        add_log(task, f"面属性：{target_faces}")
        add_log(task, "-" * 50)
        add_log(task, "正在加载原理图…")

        schem = Schematic.load(tmp_path)
        regions = dict(schem.regions)
        add_log(task, f"加载成功，共 {len(regions)} 个区域")

        total_blocks = 0
        total_replaced = 0

        for reg_name, reg in regions.items():
            add_log(task, f"▶ 处理区域：{reg_name}")
            try:
                positions = list(reg.block_positions())
            except Exception as e:
                add_log(task, f"  [跳过] 无法读取方块位置：{e}")
                continue

            cnt = 0
            rep = 0
            skipped = 0
            for (x, y, z) in positions:
                cnt += 1
                try:
                    blk = reg[x, y, z]
                except Exception:
                    continue
                if blk is None or getattr(blk, "id", "") != TARGET_BLOCK_ID:
                    continue
                try:
                    reg[x, y, z] = blk.with_properties(**target_faces)
                    rep += 1
                except Exception:
                    skipped += 1
                    if skipped <= 3:
                        add_log(task, f"  [替换失败] {skipped}")

            total_blocks += cnt
            total_replaced += rep
            line = f"  扫描 {cnt}，替换 {rep}"
            if skipped:
                line += f"，失败 {skipped}"
            add_log(task, line)

        add_log(task, "-" * 50)
        add_log(task, f"总计：扫描 {total_blocks} 方块，替换 {total_replaced} 个")
        add_log(task, "正在生成输出文件…")

        schem.save(tmp_path)
        with open(tmp_path, "rb") as f:
            result_bytes = f.read()

        task["result_bytes"] = result_bytes
        task["result_name"] = _make_result_name(original_name)
        task["result"] = {
            "regions": len(regions),
            "blocks": total_blocks,
            "replaced": total_replaced,
            "size": len(result_bytes),
        }
        add_log(task, "完成 ✓")

    except Exception as e:
        task["error"] = str(e)
        add_log(task, "❌ 处理失败：")
        add_log(task, traceback.format_exc())
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        task["running"] = False
        task["done"] = True


def _make_result_name(name):
    """生成结果文件名，例如 xxx.litematic → xxx_修改.litematic"""
    if not name:
        return "output.litematic"
    base, ext = os.path.splitext(name)
    if not ext.lower().endswith(".litematic"):
        ext = ".litematic"
    return f"{base}_修改{ext}"


# ------------------------------------------------------------
# 路由
# ------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def index():
    path = os.path.join(get_base_dir(), "index.html")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return HTMLResponse(
            "<h1>找不到 index.html</h1>"
            "<p>请确保 index.html 与 glow_lichen_server.py 在同一目录。</p>",
            status_code=500)


@app.post("/api/process")
async def api_process(
    file: UploadFile = File(...),
    faces: str = Form("{}"),
    waterlogged: str = Form("false"),
):
    # 检查是否有正在运行的任务
    with TASK_LOCK:
        for t in TASKS.values():
            if t.get("running"):
                return JSONResponse(
                    {"ok": False, "msg": "已有任务在处理中，请稍候完成后再试"},
                    status_code=409)

    try:
        faces_dict = json.loads(faces) if faces else {}
        if not isinstance(faces_dict, dict):
            faces_dict = {}
    except (json.JSONDecodeError, TypeError):
        faces_dict = {}

    waterlogged_bool = str(waterlogged).lower() == "true"

    content = await file.read()
    if not content:
        return JSONResponse({"ok": False, "msg": "上传文件为空"}, status_code=400)

    # 创建任务
    task_id = uuid.uuid4().hex[:12]
    task = {
        "id": task_id,
        "filename": file.filename,
        "running": True,
        "done": False,
        "logs": [],
        "error": None,
        "result_bytes": None,
        "result_name": None,
        "result": None,
        "started": time.time(),
    }
    TASKS[task_id] = task

    # 清理旧任务（只保留最近 KEEP_TASKS 个）
    if len(TASKS) > KEEP_TASKS:
        old = sorted(TASKS.keys(), key=lambda k: TASKS[k].get("started", 0))
        for k in old[:-KEEP_TASKS]:
            TASKS.pop(k, None)

    threading.Thread(
        target=process_file,
        args=(task_id, content, file.filename, faces_dict, waterlogged_bool),
        daemon=True,
    ).start()

    return {"ok": True, "task_id": task_id}


@app.get("/api/status/{task_id}")
def api_status(task_id: str):
    task = TASKS.get(task_id)
    if not task:
        return JSONResponse({"ok": False, "msg": "任务不存在"}, status_code=404)
    with TASK_LOCK:
        return {
            "ok": True,
            "running": task["running"],
            "done": task["done"],
            "logs": list(task["logs"]),
            "error": task["error"],
            "result": task["result"],
        }


@app.get("/api/download/{task_id}")
def api_download(task_id: str):
    task = TASKS.get(task_id)
    if not task or not task.get("result_bytes"):
        return JSONResponse({"ok": False, "msg": "结果不存在"}, status_code=404)

    filename = task["result_name"] or "output.litematic"
    # HTTP 头里文件名若含中文，用 RFC 5987 编码
    from urllib.parse import quote
    quoted = quote(filename)

    return Response(
        content=task["result_bytes"],
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quoted}",
            "Content-Length": str(len(task["result_bytes"])),
        },
    )


# ============================================================
# 入口
# ============================================================
def main():
    import webbrowser
    url = f"http://{HOST}:{PORT}"
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    print(f"服务已启动：{url}")
    print("关闭此窗口即可停止服务。")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()