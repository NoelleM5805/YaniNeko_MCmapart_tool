# -*- coding: utf-8 -*-
"""
投影文件噪点修正
================

输入、输出都是 .litematic 投影文件。
把投影的 XZ 俯视图当成一张「方块索引画布」：
每个 (x,z) 列取第一个非空气方块作为可见像素；
噪点修正操作直接改这张索引画布，最后回写到投影文件。
"""
import base64
import math

import numpy as np

from .palette import BLOCK_INDEX
from .repair import (_brush_mask, _denoise_region, _polygon_mask,
                     _clean_points, radius_of, STRENGTH_MIN, STRENGTH_MAX)
from .schematic import safe_stem
from .slicing import (_build_cell_region, _extract_cell, _load_schematic,
                      _schematic_to_bytes_fast, _SourceIndex)


# ---------------------------------------------------------------- 工具
def _hex_rgb(hexv):
    h = str(hexv).lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _state_props(state):
    props = getattr(state, "_BlockState__properties", None) or {}
    return tuple(sorted((str(k), str(v)) for k, v in props.items()))


def _state_key(state):
    """稳定地区分「方块 ID + 方块状态」，而不是只按颜色。"""
    if state is None:
        return "air"
    props = _state_props(state)
    if props:
        return state.id + "|" + ",".join("%s=%s" % kv for kv in props)
    return state.id


def _state_label(state):
    if state is None:
        return "空气"
    props = _state_props(state)
    if props:
        return state.id + " [" + ",".join("%s=%s" % kv for kv in props) + "]"
    return state.id


def _state_hex(state):
    if state is None:
        return "#000000"
    hexv = BLOCK_INDEX.get(getattr(state, "id", ""), (None, None, None))[2]
    return str(hexv).upper() if hexv else "#808080"


def _clean_key(v):
    if not isinstance(v, str):
        return None
    v = v.strip()
    return v or None


# ---------------------------------------------------------------- 载入
def load_canvas(content):
    """把 .litematic 读成 XZ 俯视索引画布。"""
    schem = _load_schematic(content)
    src = _SourceIndex(schem)
    # 内容范围（相对 src 包围盒）
    bx0, bz0, bx1, bz1 = src.content_bbox()
    cell = _extract_cell(src, src.min_x + bx0, src.min_x + bx1,
                         src.min_z + bz0, src.min_z + bz1)
    if cell is None:
        raise ValueError("投影里没有找到任何方块")

    data = cell["data"]              # (X, Y, Z)，全局状态下标
    nx, ny, nz = data.shape
    if nx <= 0 or nz <= 0:
        raise ValueError("投影包围盒无效")

    # 每列取第一个非空气方块；同时记下它的 y，输出时只改这一格。
    ypos = np.zeros((nz, nx), dtype=np.int32)
    surface = np.zeros((nz, nx), dtype=np.int32)
    for x in range(nx):
        col = data[x]                 # (Y, Z)
        for z in range(nz):
            ys = np.flatnonzero(col[:, z])
            if ys.size:
                # 俯视图取该列最上层的非空气方块；输出时也只改这一格。
                yy = int(ys[-1])
                ypos[z, x] = yy
                surface[z, x] = int(col[yy, z])

    # 压缩成前端调色板下标：0 = 空气，1..n = 投影里出现过的方块状态
    present = sorted({int(v) for v in np.unique(surface).tolist() if int(v) != 0})
    state_to_pal = {0: 0}
    palette_states = [None]
    for gi in present:
        state_to_pal[gi] = len(palette_states)
        palette_states.append(src.states[gi])
    surface_idx = np.zeros_like(surface, dtype=np.int32)
    for gi, pi in state_to_pal.items():
        surface_idx[surface == gi] = pi

    palette_keys = [_state_key(s) for s in palette_states]
    palette_hex = [_state_hex(s) for s in palette_states]
    palette_labels = [_state_label(s) for s in palette_states]
    return {
        "schem": schem,
        "src": src,
        "cell": cell,
        "data": data,
        "ypos": ypos,                 # (Z, X)
        "surface": surface,           # (Z, X) 全局状态下标
        "surface_idx": surface_idx,   # (Z, X) 压缩调色板下标
        "palette_states": palette_states,
        "palette_keys": palette_keys,
        "palette_hex": palette_hex,
        "palette_labels": palette_labels,
        "state_to_pal": state_to_pal,
        "pal_to_state": {v: k for k, v in state_to_pal.items()},
        "width": int(nx),
        "height": int(nz),
    }


def preview_payload(content, filename=""):
    cv = load_canvas(content)
    idx = cv["surface_idx"].astype("<u2")
    pixels_b64 = base64.b64encode(idx.tobytes(order="C")).decode()
    return {
        "ok": True,
        "filename": filename or "projection.litematic",
        "width": cv["width"], "height": cv["height"],
        "pixels_b64": pixels_b64,
        "pixels_encoding": "u16le",
        "pixels_width": cv["width"], "pixels_height": cv["height"],
        "palette": cv["palette_hex"],
        "palette_keys": cv["palette_keys"],
        "palette_labels": cv["palette_labels"],
        "blocks": int(np.count_nonzero(cv["surface_idx"])),
        "colors_used": int(len(cv["palette_keys"])),
    }


# ---------------------------------------------------------------- 操作解析
def parse_ops(payload):
    if not isinstance(payload, dict):
        return []
    raw = payload.get("ops")
    if not isinstance(raw, list):
        return []
    out = []
    for it in raw:
        if not isinstance(it, dict):
            continue
        kind = it.get("kind")
        if kind == "brush":
            key = _clean_key(it.get("key"))
            pts = _clean_points(it.get("points"))
            if not key or not pts:
                continue
            try:
                size = int(it.get("size", 6))
            except (TypeError, ValueError):
                size = 6
            out.append({"kind": "brush", "key": key,
                        "size": max(1, min(128, size)), "points": pts})
            continue
        lasso = _clean_points(it.get("lasso"))
        if len(lasso) < 3:
            continue
        if kind == "denoise":
            key = _clean_key(it.get("target"))
            if not key:
                continue
            try:
                st = int(it.get("strength", 5))
            except (TypeError, ValueError):
                st = 5
            st = max(STRENGTH_MIN, min(STRENGTH_MAX, st))
            out.append({"kind": "denoise", "target": key,
                        "strength": st, "radius": radius_of(st),
                        "lasso": lasso})
        elif kind == "fill":
            key = _clean_key(it.get("key"))
            if not key:
                continue
            out.append({"kind": "fill", "key": key, "lasso": lasso})
        elif kind == "revert":
            out.append({"kind": "revert", "lasso": lasso})
    return out


def apply_ops_to_canvas(cv, ops):
    idx = cv["surface_idx"].copy()
    base = cv["surface_idx"]
    H, W = idx.shape
    key_to_pal = {k: i for i, k in enumerate(cv["palette_keys"])}
    info = {"applied": False, "op_count": len(ops), "denoised": 0,
            "filled": 0, "reverted": 0, "brush_pixels": 0,
            "warnings": [], "ops": []}
    for i, op in enumerate(ops):
        detail = {"i": i, "kind": op["kind"]}
        try:
            if op["kind"] == "brush":
                key = op["key"]
                pi = key_to_pal.get(key)
                if pi is None:
                    info["warnings"].append("笔刷方块不在投影调色板里：%s" % key)
                else:
                    m = _brush_mask(op["points"], op["size"] / max(1, W),
                                    op["size"] / max(1, H), H, W)
                    n = int(m.sum())
                    idx[m] = pi
                    info["brush_pixels"] += n
                    detail.update(key=key, pixels=n)
            else:
                m = _polygon_mask(op["lasso"], H, W)
                if not m.any():
                    detail["pixels"] = 0
                elif op["kind"] == "denoise":
                    key = op["target"]
                    pi = key_to_pal.get(key)
                    if pi is None:
                        info["warnings"].append("降噪目标不在投影调色板里：%s" % key)
                    else:
                        src_n = int((m & (idx == pi)).sum())
                        idx, n = _denoise_region(idx, m, pi, op["radius"])
                        info["denoised"] += n
                        detail.update(target=key, radius=op["radius"],
                                      source=src_n, changed=n)
                elif op["kind"] == "fill":
                    key = op["key"]
                    pi = key_to_pal.get(key)
                    if pi is None:
                        info["warnings"].append("填充方块不在投影调色板里：%s" % key)
                    else:
                        n = int(m.sum())
                        idx[m] = pi
                        info["filled"] += n
                        detail.update(key=key, pixels=n)
                elif op["kind"] == "revert":
                    n = int(m.sum())
                    idx[m] = base[m]
                    info["reverted"] += n
                    detail["pixels"] = n
        except Exception as e:  # noqa: BLE001
            info["warnings"].append("第 %d 个操作失败：%s" % (i, e))
            detail["error"] = str(e)
        info["ops"].append(detail)
    info["applied"] = bool(info["denoised"] or info["filled"]
                           or info["reverted"] or info["brush_pixels"])
    return idx, info


def apply_ops_preview(content, ops, filename=""):
    cv = load_canvas(content)
    new_idx, info = apply_ops_to_canvas(cv, ops)
    payload = preview_payload(content, filename)
    cv2 = load_canvas(content)
    cv2["surface_idx"] = new_idx
    arr = new_idx.astype("<u2")
    payload["pixels_b64"] = base64.b64encode(arr.tobytes(order="C")).decode()
    payload["blocks"] = int(np.count_nonzero(new_idx))
    payload["repair"] = info
    return payload


def process_projection(content, ops, filename=""):
    cv = load_canvas(content)
    new_idx, info = apply_ops_to_canvas(cv, ops)

    out_data = cv["data"].copy()
    pal_to_state = cv["pal_to_state"]
    ypos = cv["ypos"]
    H, W = new_idx.shape
    # 只回写每列「第一个非空气方块」那一格；地图画投影通常是单层。
    for z in range(H):
        for x in range(W):
            yi = int(ypos[z, x])
            gi = pal_to_state.get(int(new_idx[z, x]), 0)
            out_data[x, yi, z] = gi

    cell2 = dict(cv["cell"])
    cell2["data"] = out_data
    cell2["present"] = np.array(
        sorted({int(v) for v in np.unique(out_data).tolist() if int(v) != 0}),
        dtype=np.int64)
    if cell2["present"].size == 0:
        raise ValueError("修改后没有任何方块")
    reg, nblocks = _build_cell_region(cell2, cv["src"])
    sub = reg.as_schematic(
        name=safe_stem(filename) or "projection_repair",
        author="Toolkit",
        description="Projection noise repair")
    data = _schematic_to_bytes_fast(sub)
    info["blocks"] = nblocks
    return data, info
