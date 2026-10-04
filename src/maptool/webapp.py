# -*- coding: utf-8 -*-
from PIL import Image, ImageFilter
from fastapi import FastAPI, File, UploadFile, Form, Request
from fastapi.responses import HTMLResponse, Response, JSONResponse, StreamingResponse
from urllib.parse import quote
import asyncio
import base64
import io
import json
import numpy as np
import os
import sys
import threading
import time
import traceback
import zipfile

from . import __version__, native
from .adjustments import ADJUST_KEYS, ADJUST_LABELS, apply_image_adjust, parse_adjust
from .config import (DEFAULT_SEED, FACE_KEYS, PREVIEW_MAX_SIDE, TASKS, TASK_LOCK,
                     WEB_DIR, get_base_dir, get_resource_path, web_index_path)
from .matching import ALGO_LABELS
from .dithering import DITHER_LABELS, process_image
from .imageops import fit_image, flatten_image, recommend_ratios
from .keepalive import KEEPALIVE, KEEPALIVE_GRACE, KEEPALIVE_LOCK, KEEPALIVE_TICK, _keepalive_note
from .palette import ALL_BLOCK_IDS, BLOCK_SOURCE_FILE, DEFAULT_BLOCK_IDS, ICON_META, PALETTE_GROUPS, PALETTE_META, make_palette
from .repair import apply_repair, parse_repair, public_info
from .schematic import build_mapart_schematic, count_block_usage, parse_alloc, pick_block_names, safe_litematic_name, safe_stem, schem_to_bytes
from .lichen import do_glow_lichen
from .projection_repair import (apply_ops_preview, parse_ops, preview_payload,
                                process_projection)
from .slicing import MAP_SIZE, do_slice, preview_slice
from .tasks import (add_log, cache_get_image, cache_get_slice, cache_put_image,
                    cache_put_slice, create_task, finish_task)


# ============================================================
# FastAPI
# ============================================================
app = FastAPI()


@app.get("/", response_class=HTMLResponse)
def index():
    p = web_index_path()
    if not p:
        return HTMLResponse("<h1>缺少 web/index.html</h1>", status_code=500)
    try:
        with open(p, "r", encoding="utf-8") as f:
            body = f.read()
    except OSError:
        return HTMLResponse("<h1>缺少 web/index.html</h1>", status_code=500)
    # 本地工具，页面永远取最新的，免得改了前端还看到旧版
    return HTMLResponse(body, headers={"Cache-Control": "no-cache, must-revalidate"})


# 前端静态资源后缀 -> MIME 类型。资源根目录固定是 <root>/web/。
_STATIC_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
}


@app.get("/static/{path:path}")
def static_file(path: str):
    """
    提供 web/ 目录下的前端资源。

    只允许读 web/ 里面的文件：拼出来的绝对路径必须仍然落在 web/ 下面，
    否则一律 404，避免 ../ 之类的越界读取。
    """
    root = os.path.join(get_base_dir(), WEB_DIR)
    full = os.path.normpath(os.path.join(root, path.replace("\\", "/")))
    root_norm = os.path.normpath(root)
    if not (full == root_norm or full.startswith(root_norm + os.sep)):
        return Response(status_code=404)
    if not os.path.isfile(full):
        return Response(status_code=404)
    ext = os.path.splitext(full)[1].lower()
    ctype = _STATIC_TYPES.get(ext, "application/octet-stream")
    try:
        with open(full, "rb") as f:
            data = f.read()
    except OSError:
        return Response(status_code=404)
    # 本地工具，改了前端刷新就能看到，不做长缓存
    return Response(content=data, media_type=ctype,
                    headers={"Cache-Control": "no-cache, must-revalidate"})


# ------------------------------------------------------------
# 调色板（供前端渲染按颜色分组的方块选择面板）
# ------------------------------------------------------------
def _icon_url():
    """
    图标贴图集的带版本地址。

    贴图集每次重新生成，每个方块所处的格子都会变；如果浏览器还拿着
    旧图 + 新坐标，就会出现整体错位。所以地址上挂一个内容指纹，
    内容一变地址就变，缓存自然失效。
    """
    p = get_resource_path(ICON_META["file"])
    if not p:
        return "/api/icons.png"
    try:
        st = os.stat(p)
        return "/api/icons.png?v=%d-%d" % (int(st.st_mtime), int(st.st_size))
    except OSError:
        return "/api/icons.png"


@app.get("/api/palette")
def api_palette():
    return {
        "ok": True,
        "source": BLOCK_SOURCE_FILE,
        "groups": PALETTE_META,
        "icon": dict(ICON_META, url=_icon_url()),
        "defaults": DEFAULT_BLOCK_IDS,
        "total_groups": len(PALETTE_GROUPS),
        "total_blocks": len(ALL_BLOCK_IDS),
        # 版本 / 运行环境 / C++ 核心状态。打包出问题时看这几个字段最快。
        "version": __version__,
        "frozen": bool(getattr(sys, "frozen", False)),
        "native": {
            "available": native.available(),
            "version": native.version() if native.available() else None,
            "error": None if native.available() else native.load_error(),
        },
    }


@app.get("/api/keepalive")
async def api_keepalive(request: Request, exit_on_close: int = 1):
    """
    SSE 长连接。页面开着就一直连着；标签页一关，连接立刻断开，
    看门狗线程据此判断"页面全关了"，再等一小会儿就退出进程。

    相比定时 ping 的好处：后台标签页的定时器会被浏览器限流，长连接不会。
    """
    _keepalive_note(exit_on_close)

    async def stream():
        with KEEPALIVE_LOCK:
            KEEPALIVE["conns"] += 1
            KEEPALIVE["armed"] = True
            KEEPALIVE["last_seen"] = time.time()
        try:
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected():
                    break
                with KEEPALIVE_LOCK:
                    KEEPALIVE["last_seen"] = time.time()
                yield ": ping\n\n"
                await asyncio.sleep(KEEPALIVE_TICK)
        except asyncio.CancelledError:
            pass
        finally:
            with KEEPALIVE_LOCK:
                KEEPALIVE["conns"] = max(0, KEEPALIVE["conns"] - 1)
                KEEPALIVE["last_seen"] = time.time()

    return StreamingResponse(stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache, no-store",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    })


@app.get("/api/keepalive/status")
def api_keepalive_status():
    with KEEPALIVE_LOCK:
        return {
            "ok": True,
            "pages": KEEPALIVE["conns"],
            "armed": KEEPALIVE["armed"],
            "exit_on_close": KEEPALIVE["exit_on_close"],
            "grace": KEEPALIVE_GRACE,
        }


@app.get("/api/icons.png")
def api_icons():
    p = get_resource_path(ICON_META["file"])
    if not p:
        return JSONResponse({"ok": False, "msg": "缺少图标文件 " + ICON_META["file"]},
                            status_code=404)
    with open(p, "rb") as f:
        data = f.read()
    # 地址带版本，所以可以放心长缓存
    return Response(content=data, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=86400",
                             "ETag": '"%s"' % _icon_url().split("v=")[-1]})


# ------------------------------------------------------------
# 地图画 - 上传
# ------------------------------------------------------------
@app.post("/api/mapart/upload")
async def api_mapart_upload(file: UploadFile = File(...)):
    content = await file.read()
    try:
        img = Image.open(io.BytesIO(content))
        img.load()
    except Exception as e:
        return JSONResponse({"ok": False, "msg": f"无法读取图片：{e}"},
                            status_code=400)

    src = flatten_image(img)
    sid = cache_put_image(src)
    w, h = src.size
    recs = recommend_ratios(w, h)

    thumb = src.copy()
    thumb.thumbnail((400, 400), Image.LANCZOS)
    buf = io.BytesIO()
    thumb.save(buf, format="PNG")
    orig_b64 = base64.b64encode(buf.getvalue()).decode()

    return {
        "ok": True,
        "sid": sid,
        "width": w,
        "height": h,
        "recommendations": recs,
        "original": "data:image/png;base64," + orig_b64,
    }


# ------------------------------------------------------------
# 地图画 - 预览
# ------------------------------------------------------------
@app.post("/api/mapart/preview")
async def api_mapart_preview(payload: dict):
    sid = payload.get("sid")
    src = cache_get_image(sid)
    if src is None:
        return JSONResponse({"ok": False, "msg": "会话已过期，请重新上传图片"},
                            status_code=410)

    size_mode = payload.get("size_mode", "grid")
    grid_x = int(payload.get("grid_x", 1))
    grid_y = int(payload.get("grid_y", 1))
    max_size = int(payload.get("max_size", 128))
    fit_mode = payload.get("fit_mode", "stretch")
    algo = payload.get("algo", "weighted")
    dither = payload.get("dither", "none")
    strength = int(payload.get("strength", 100))
    selected = payload.get("blocks")
    adj = parse_adjust(payload)
    alloc = parse_alloc(payload)
    preview_full = bool(payload.get("preview_full"))

    pal, used = make_palette(selected)
    if pal.n == 0:
        return JSONResponse(
            {"ok": False, "msg": "没有选择任何方块，请至少勾选一个方块"},
            status_code=400)

    try:
        if size_mode == "grid":
            gx = max(1, min(16, grid_x))
            gy = max(1, min(16, grid_y))
            tw = gx * 128
            th = gy * 128
            if tw * th > 1024 * 1024:
                return JSONResponse(
                    {"ok": False, "msg": f"尺寸过大 {tw}×{th}，超过 100 万像素"},
                    status_code=400)
            work = fit_image(src, tw, th, fit_mode)
            real_w, real_h = tw, th
        else:
            ms = max(8, min(512, max_size))
            iw, ih = src.size
            if max(iw, ih) > ms:
                s = ms / max(iw, ih)
                iw = max(1, int(round(iw * s)))
                ih = max(1, int(round(ih * s)))
            work = src.resize((iw, ih), Image.LANCZOS)
            real_w, real_h = iw, ih

        # 图片调整在成品尺寸上做（暗角要贴合画幅），预览与生成一致
        work = apply_image_adjust(work, adj)

        # preview_full=True：预览也用成品 1:1 像素，不再缩小，避免方块被插值糊掉。
        # 旧调用不传这个字段时保留原来的降采样行为（兼容回归测试）。
        if preview_full:
            pw, ph = real_w, real_h
        else:
            side = PREVIEW_MAX_SIDE
            try:
                side = int(payload.get("preview_side", PREVIEW_MAX_SIDE))
            except (TypeError, ValueError):
                pass
            side = max(128, min(PREVIEW_MAX_SIDE, side))

            if max(real_w, real_h) > side:
                scale = side / max(real_w, real_h)
                pw = max(1, int(round(real_w * scale)))
                ph = max(1, int(round(real_h * scale)))
                work = work.resize((pw, ph), Image.LANCZOS)
            else:
                pw, ph = real_w, real_h

        st = max(0.0, min(1.0, strength / 100.0))
        idx, rgb = process_image(work, algo, dither, st, pal)

        # 局部噪点修正：把前端记录的操作序列重放到抖动结果上（详见 repair.py）
        repair = parse_repair(payload)
        rep_info = None
        if repair:
            idx, rep = apply_repair(idx, np.array(work, dtype=np.uint8), pal,
                                    repair, algo)
            # 修正改的是 idx，必须按新的 idx 重新渲染，否则页面上看不到任何变化
            rgb = pal.rgb[idx].astype(np.uint8)
            rep_info = public_info(rep)
            if not rep["applied"] and not rep["warnings"]:
                rep_info["note"] = "这一步没有改动任何方块"

        # 专业像素画数据：发调色板索引而不是只发 PNG，前端按索引查色块。
        # 调色板数量很小，用 u8 即可；保留 u16 分支以防调色板扩展。
        if pal.n <= 255:
            pix_arr = idx.astype(np.uint8)
            pix_encoding = "u8"
        else:
            pix_arr = idx.astype("<u2")
            pix_encoding = "u16le"
        pixels_b64 = base64.b64encode(pix_arr.tobytes(order="C")).decode()

        prev_img = Image.fromarray(rgb, mode="RGB")

        buf = io.BytesIO()
        prev_img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()

        # 按成品尺寸估算用量：预览图可能被缩小过，所以用比例换算回真实方块数
        names = pick_block_names(idx, pal, np.random.default_rng(DEFAULT_SEED), alloc)
        counts, _ = count_block_usage(names)
        ratio = (real_w * real_h) / float(max(1, idx.size))
        if abs(ratio - 1.0) > 1e-9:
            for c in counts:
                c["count"] = int(round(c["count"] * ratio))
                c["percent"] = round(c["count"] * 100.0 / max(1, real_w * real_h), 2)
            counts.sort(key=lambda d: (-d["count"], d["label"]))

        return {
            "ok": True,
            "preview": "data:image/png;base64," + b64,
            "width": real_w,
            "height": real_h,
            "preview_width": pw,
            "preview_height": ph,
            "preview_full": preview_full,
            "pixels_b64": pixels_b64,
            "pixels_encoding": pix_encoding,
            "pixels_width": int(idx.shape[1]),
            "pixels_height": int(idx.shape[0]),
            "palette": list(pal.hexes[:pal.n]),
            "dither_mode": dither,
            "dither_strength": st,
            "blocks": real_w * real_h,
            "groups": pal.n,
            "colors_used": [pal.hexes[int(i)] for i in np.unique(idx)],
            "counts": counts,
            "total_blocks": real_w * real_h,
            "estimated": abs(ratio - 1.0) > 1e-9,
            "adjust": adj,
            "repair": rep_info,
        }
    except Exception as e:
        traceback.print_exc()
        return JSONResponse({"ok": False, "msg": str(e)}, status_code=500)


# ------------------------------------------------------------
# 地图画 - 生成（XZ 地面朝向 · 厚度 1 · 无底板）
# ------------------------------------------------------------
@app.post("/api/mapart/generate")
async def api_mapart_generate(payload: dict):
    sid = payload.get("sid")
    src = cache_get_image(sid)
    if src is None:
        return JSONResponse({"ok": False, "msg": "会话已过期，请重新上传图片"},
                            status_code=410)

    size_mode = payload.get("size_mode", "grid")
    grid_x = int(payload.get("grid_x", 1))
    grid_y = int(payload.get("grid_y", 1))
    max_size = int(payload.get("max_size", 128))
    fit_mode = payload.get("fit_mode", "stretch")
    algo = payload.get("algo", "weighted")
    dither = payload.get("dither", "none")
    strength = int(payload.get("strength", 100))
    selected = payload.get("blocks")
    adj = parse_adjust(payload)
    alloc = parse_alloc(payload)
    out_name = safe_litematic_name(payload.get("filename"))
    repair = parse_repair(payload)

    pal, used = make_palette(selected)
    if pal.n == 0:
        return JSONResponse(
            {"ok": False, "msg": "没有选择任何方块，请至少勾选一个方块"},
            status_code=400)

    tid = create_task("mapart")

    def worker():
        task = TASKS[tid]
        try:
            add_log(task, "读取缓存的图片…")
            add_log(task, f"原图：{src.size[0]}×{src.size[1]}")

            if size_mode == "grid":
                gx = max(1, min(16, grid_x))
                gy = max(1, min(16, grid_y))
                tw = gx * 128
                th = gy * 128
                add_log(task, f"目标：{tw}×{th}（{gx}×{gy} 格）")
                if tw * th > 1024 * 1024:
                    raise ValueError(f"尺寸过大 {tw}×{th}，超过 100 万像素")
                work = fit_image(src, tw, th, fit_mode)
            else:
                ms = max(8, min(512, max_size))
                iw, ih = src.size
                if max(iw, ih) > ms:
                    s = ms / max(iw, ih)
                    iw = max(1, int(round(iw * s)))
                    ih = max(1, int(round(ih * s)))
                work = src.resize((iw, ih), Image.LANCZOS)
                add_log(task, f"缩放后：{iw}×{ih}")

            if adj:
                add_log(task, "图片调整：" + "，".join(
                    "%s %+d" % (ADJUST_LABELS[k], adj[k])
                    for k in ADJUST_KEYS if adj.get(k)))
                work = apply_image_adjust(work, adj)

            add_log(task, f"颜色算法：{ALGO_LABELS.get(algo, algo)}")
            add_log(task, f"抖动算法：{DITHER_LABELS.get(dither, dither)}，强度 {strength}%")
            add_log(task, f"调色板：启用 {pal.n} 个颜色 / {len(used)} 个方块")
            add_log(task, "处理像素…")
            st = max(0.0, min(1.0, strength / 100.0))
            idx, rgb = process_image(work, algo, dither, st, pal)

            # 局部噪点修正（和预览走同一个函数，操作序列一样，结果一致）
            if repair:
                idx, rep = apply_repair(idx, np.array(work, dtype=np.uint8), pal,
                                        repair, algo)
                rgb = pal.rgb[idx].astype(np.uint8)
                add_log(task, "局部噪点修正：重放 %d 个操作" % rep["op_count"])
                for d in rep["ops"]:
                    if d["kind"] == "denoise":
                        add_log(task, "  降噪：锁定 %s，半径 %d，选区 %d 个方块，"
                                      "改动 %d 个"
                                % (d.get("target"), d.get("radius", 0),
                                   d.get("source", 0), d.get("changed", 0)))
                    elif d["kind"] == "fill":
                        add_log(task, "  填充 %s：%d 个方块"
                                % (d.get("hex"), d.get("pixels", 0)))
                    elif d["kind"] == "revert":
                        add_log(task, "  还原（去抖动）：%d 个方块" % d.get("pixels", 0))
                    elif d["kind"] == "brush":
                        add_log(task, "  画笔 %s：%d 个方块"
                                % (d.get("hex"), d.get("pixels", 0)))
                    if d.get("error"):
                        add_log(task, "  ! 第 %d 个操作失败：%s" % (d["i"], d["error"]))
                for wn in rep["warnings"]:
                    add_log(task, "  ! " + wn)

            add_log(task, "构建投影（XZ 地面朝向 · 厚度 1 · 无底板）…")
            schem, placed, counts = build_mapart_schematic(
                idx, pal, seed=DEFAULT_SEED, with_counts=True, alloc=alloc)

            add_log(task, "保存文件…")
            data = schem_to_bytes(schem)

            # 全尺寸最终效果图：方块分布的 1:1 还原，下载前确认用
            try:
                ibuf = io.BytesIO()
                Image.fromarray(rgb, mode="RGB").save(ibuf, format="PNG")
                task["result_image"] = ibuf.getvalue()
            except Exception as e:
                add_log(task, "预览图生成失败（不影响下载）：%s" % e)

            task["result_bytes"] = data
            task["result_name"] = out_name
            task["result"] = {
                "width": idx.shape[1],
                "height": idx.shape[0],
                "placed": placed,
                "blocks_used": len(used),
                "groups_used": pal.n,
                "counts": counts,
                "total_blocks": placed,
                "filename": out_name,
                "has_image": bool(task.get("result_image")),
            }
            add_log(task, f"完成 ✓ {idx.shape[1]}×{idx.shape[0]}，{placed} 方块，"
                          f"用到 {len(counts)} 种方块")
            add_log(task, f"文件名：{out_name}")
        except Exception as e:
            task["error"] = str(e)
            add_log(task, "❌ " + str(e))
            add_log(task, traceback.format_exc())
        finally:
            finish_task(tid)

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True, "task_id": tid}


# ------------------------------------------------------------
# 投影切分
# ------------------------------------------------------------
# 预览缩略图按内容指纹存，前端 <img src> 直接用（不重复传 base64）
_PREVIEW_PNG = {}


def _parse_slice_params(cols, rows, max_size):
    """把前端传上来的切分参数收成 (cols, rows, max_size)，非法值给默认。"""
    def _num(v, lo, hi):
        try:
            n = int(v)
        except (TypeError, ValueError):
            return 0
        if n < lo:
            return 0
        return min(n, hi)

    c = _num(cols, 1, 256)
    r = _num(rows, 1, 256)
    m = _num(max_size, 16, 1024) or MAP_SIZE
    # 只给了一个方向就当成「两个都给」处理会很难解释，所以要么都用要么都不用
    if not (c and r):
        c = r = 0
    return c, r, m


@app.post("/api/slice/preview")
async def api_slice_preview(
    file: UploadFile = File(...),
    cols: int = Form(0),
    rows: int = Form(0),
    max_size: int = Form(MAP_SIZE),
    filename: str = Form(""),
):
    """
    切分预览：不写任何文件、不建任务，直接算出「这么切会切出什么」。
    返回切割方案 + 每块统计 + 缩略图，并把文件内容按指纹缓存住，
    后面真正切分时前端只回传指纹，不用再上传一遍。
    """
    content = await file.read()
    if not content:
        return JSONResponse({"ok": False, "msg": "文件是空的"}, status_code=400)
    c, r, m = _parse_slice_params(cols, rows, max_size)
    name = filename or getattr(file, "filename", "") or ""
    try:
        info = await asyncio.to_thread(
            preview_slice, content, c or None, r or None,
            base_name=name, max_size=m)
    except Exception as e:
        return JSONResponse({"ok": False, "msg": "读不出投影文件：%s" % e},
                            status_code=400)

    key = cache_put_slice(content)
    png = info.pop("preview_png", None)
    info["ok"] = True
    info["key"] = key
    info["filename"] = getattr(file, "filename", "") or name
    # 缩略图单独挂一条 URL 给 <img src> 用，不塞 base64 —— 大投影的
    # PNG 能到几百 KB，塞进 JSON 会让整个预览响应白胖一圈。
    info["preview_url"] = ("/api/slice/preview.png/%s" % key) if png else None
    if png:
        _PREVIEW_PNG[key] = png
        while len(_PREVIEW_PNG) > 12:
            _PREVIEW_PNG.pop(next(iter(_PREVIEW_PNG)), None)
    return JSONResponse(info)


@app.get("/api/slice/preview.png/{key}")
def api_slice_preview_png(key: str):
    png = _PREVIEW_PNG.get(key)
    if not png:
        return JSONResponse({"ok": False, "msg": "预览已过期"}, status_code=404)
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "no-store"})


@app.post("/api/slice/process")
async def api_slice_process(
    file: UploadFile = File(None),
    key: str = Form(""),
    cols: int = Form(0),
    rows: int = Form(0),
    max_size: int = Form(MAP_SIZE),
    filename: str = Form(""),
):
    """
    真正切分。

    优先用预览缓存下来的内容（key）；没有 key 时回退成直接读上传的文件，
    所以老的前端/直接调 API 的用法都还能用。
    """
    content = None
    if key:
        content = cache_get_slice(key)
    if content is None and file is not None:
        content = await file.read()
    if not content:
        return JSONResponse({"ok": False, "msg": "文件已过期，请重新上传"},
                            status_code=400)

    c, r, m = _parse_slice_params(cols, rows, max_size)
    name = filename or getattr(file, "filename", "") or ""
    tid = create_task("slice")

    def worker():
        task = TASKS[tid]
        try:
            base = safe_stem(name) or safe_stem(getattr(file, "filename", "")) or "slice"
            plan_txt = ("列(X)：%d，行(Z)：%d" % (c, r)) if (c and r) else \
                "自动推荐（每块 ≤%d×%d）" % (m, m)
            add_log(task, plan_txt)
            add_log(task, "输出文件名：%s_r?c?.litematic" % base)

            def cb(done, total):
                if done % 5 == 0 or done == total:
                    add_log(task, "进度：%d/%d" % (done, total))

            add_log(task, "正在切分…")
            t0 = time.time()
            outputs = do_slice(content, c or None, r or None,
                               progress_cb=cb, base_name=base, max_size=m)
            add_log(task, "生成 %d 个投影文件，用时 %.2fs" % (len(outputs), time.time() - t0))

            # 不再压成 zip，逐个文件存进任务，前端逐个下载。
            # 这里只放「文件名 / 大小 / 行列号」——每块尺寸和方块数由
            # /api/slice/preview 提供，这里再放一份就是重复数据。
            files = [{"name": o["filename"], "size": len(o["bytes"]),
                      "row": o["row"], "col": o["col"]}
                     for o in outputs]
            total_bytes = sum(len(o["bytes"]) for o in outputs)

            task["slice_files"] = [o["bytes"] for o in outputs]
            task["result_name"] = base
            task["result"] = {
                "count": len(outputs),
                "size": total_bytes,
                "files": files,
                "base": base,
            }
            add_log(task, "完成 ✓ %d 个文件，共 %.1f KB" % (len(outputs), total_bytes / 1024))
        except Exception as e:
            task["error"] = str(e)
            add_log(task, "❌ " + str(e))
            add_log(task, traceback.format_exc())
        finally:
            finish_task(tid)

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True, "task_id": tid}


@app.get("/api/slice/file/{tid}/{index}")
def api_slice_file(tid: str, index: int):
    """切分结果里的第 index 个投影文件。"""
    t = TASKS.get(tid)
    files = (t or {}).get("slice_files") or []
    if not t or index < 0 or index >= len(files):
        return JSONResponse({"ok": False, "msg": "文件不存在"}, status_code=404)
    name = "slice_%d.litematic" % (index + 1)
    try:
        name = t["result"]["files"][index]["name"]
    except Exception:
        pass
    return Response(
        content=files[index],
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''%s" % quote(name),
            "Content-Length": str(len(files[index])),
            "Cache-Control": "no-store",
        },
    )


@app.get("/api/slice/zip/{tid}")
def api_slice_zip(tid: str):
    """
    把切分结果打成一个 zip 流式吐出来。

    Chrome 会拦「一次下很多文件」，64 个投影逐个下载要等十几秒还容易被
    拦掉；这里给一个一次点完的出口。目录选择器可用时前端仍优先写目录。
    """
    t = TASKS.get(tid)
    files = (t or {}).get("slice_files") or []
    if not t or not files:
        return JSONResponse({"ok": False, "msg": "文件不存在"}, status_code=404)
    names = []
    try:
        names = [f["name"] for f in t["result"]["files"]]
    except Exception:
        names = ["slice_%d.litematic" % (i + 1) for i in range(len(files))]
    base = t.get("result_name") or "slice"

    def gen():
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name, data in zip(names, files):
                z.writestr(name, data)
        yield buf.getvalue()

    return StreamingResponse(
        gen(),
        media_type="application/zip",
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''%s"
                                   % quote(base + "_投影切分.zip"),
            "Cache-Control": "no-store",
        },
    )


# ------------------------------------------------------------
# 投影噪点修正
# ------------------------------------------------------------
@app.post("/api/projection-repair/load")
async def api_projection_repair_load(file: UploadFile = File(...)):
    content = await file.read()
    if not content:
        return JSONResponse({"ok": False, "msg": "文件是空的"}, status_code=400)
    name = getattr(file, "filename", "") or "projection.litematic"
    try:
        info = await asyncio.to_thread(preview_payload, content, name)
    except Exception as e:
        return JSONResponse({"ok": False, "msg": "读不出投影文件：%s" % e},
                            status_code=400)
    info["key"] = cache_put_slice(content)
    return JSONResponse(info)


@app.post("/api/projection-repair/apply")
async def api_projection_repair_apply(payload: dict):
    key = payload.get("key")
    content = cache_get_slice(key) if key else None
    if content is None:
        return JSONResponse({"ok": False, "msg": "文件已过期，请重新上传"},
                            status_code=410)
    ops = parse_ops(payload)
    try:
        info = await asyncio.to_thread(
            apply_ops_preview, content, ops, payload.get("filename", ""))
    except Exception as e:
        return JSONResponse({"ok": False, "msg": str(e)}, status_code=400)
    return JSONResponse(info)


@app.post("/api/projection-repair/process")
async def api_projection_repair_process(payload: dict):
    key = payload.get("key")
    content = cache_get_slice(key) if key else None
    if content is None:
        return JSONResponse({"ok": False, "msg": "文件已过期，请重新上传"},
                            status_code=410)
    ops = parse_ops(payload)
    name = payload.get("filename") or "projection_repair.litematic"
    try:
        data, info = await asyncio.to_thread(
            process_projection, content, ops, name)
    except Exception as e:
        return JSONResponse({"ok": False, "msg": str(e)}, status_code=400)
    out_name = safe_litematic_name(name)
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": "attachment; filename*=UTF-8''%s" % quote(out_name),
            "Content-Length": str(len(data)),
            "Cache-Control": "no-store",
            "X-Repair-Blocks": str(info.get("blocks", 0)),
        },
    )


# ------------------------------------------------------------
# Glow Lichen
# ------------------------------------------------------------
@app.post("/api/lichen/process")
async def api_lichen_process(
    file: UploadFile = File(...),
    faces: str = Form("{}"),
    waterlogged: str = Form("false"),
):
    content = await file.read()
    try:
        faces_dict = json.loads(faces) if faces else {}
        if not isinstance(faces_dict, dict): faces_dict = {}
    except (json.JSONDecodeError, TypeError):
        faces_dict = {}
    wl = str(waterlogged).lower() == "true"

    tid = create_task("lichen")

    def worker():
        task = TASKS[tid]
        try:
            tf = {k: ("true" if faces_dict.get(k) else "false") for k in FACE_KEYS}
            tf["waterlogged"] = "true" if wl else "false"
            add_log(task, f"面属性：{tf}")

            def cb(rep, total):
                pass

            add_log(task, "正在处理…")
            data, info = do_glow_lichen(content, tf, progress_cb=cb)

            task["result_bytes"] = data
            task["result_name"] = "modified.litematic"
            task["result"] = info
            add_log(task, f"扫描 {info['blocks']}，替换 {info['replaced']} 个")
            add_log(task, "完成 ✓")
        except Exception as e:
            task["error"] = str(e)
            add_log(task, "❌ " + str(e))
            add_log(task, traceback.format_exc())
        finally:
            finish_task(tid)

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True, "task_id": tid}


# ------------------------------------------------------------
# 状态 / 下载
# ------------------------------------------------------------
@app.get("/api/status/{tid}")
def api_status(tid: str):
    t = TASKS.get(tid)
    if not t:
        return JSONResponse({"ok": False, "msg": "任务不存在"}, status_code=404)
    with TASK_LOCK:
        return {
            "ok": True,
            "running": t["running"],
            "done": t["done"],
            "logs": list(t["logs"]),
            "error": t["error"],
            "result": t["result"],
        }


@app.get("/api/result-image/{tid}")
def api_result_image(tid: str):
    """生成任务的全尺寸最终效果图（1 像素 = 1 方块），下载前确认用。"""
    t = TASKS.get(tid)
    if not t or not t.get("result_image"):
        return JSONResponse({"ok": False, "msg": "预览图不存在"}, status_code=404)
    return Response(content=t["result_image"], media_type="image/png",
                    headers={"Cache-Control": "no-store"})


@app.get("/api/download/{tid}")
def api_download(tid: str):
    t = TASKS.get(tid)
    if not t or not t.get("result_bytes"):
        return JSONResponse({"ok": False, "msg": "结果不存在"}, status_code=404)
    filename = t["result_name"] or "result.bin"
    quoted = quote(filename)
    return Response(
        content=t["result_bytes"],
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quoted}",
            "Content-Length": str(len(t["result_bytes"])),
        },
    )


