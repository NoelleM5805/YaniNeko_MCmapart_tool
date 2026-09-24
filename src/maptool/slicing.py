# -*- coding: utf-8 -*-
from litemapy import Region, BlockState, Schematic
import os
import tempfile

from .schematic import safe_stem, schem_to_bytes


def split_boundaries(total, parts):
    total = int(total); parts = max(1, min(int(parts), total))
    return [int(round(i*total/parts)) for i in range(parts+1)]


def do_slice(content, cols, rows, progress_cb=None, base_name="slice"):
    base = safe_stem(base_name) or "slice"
    tmp = tempfile.NamedTemporaryFile(suffix=".litematic", delete=False)
    tmp.write(content); tmp.close()
    try:
        schem = Schematic.load(tmp.name)
    finally:
        try: os.unlink(tmp.name)
        except OSError: pass

    regions = dict(schem.regions)
    min_x = min_y = min_z = 0
    max_x = max_y = max_z = 0
    for reg in regions.values():
        rx, ry, rz = reg.x, reg.y, reg.z
        rw, rh, rl = abs(reg.width), abs(reg.height), abs(reg.length)
        min_x = min(min_x, rx); max_x = max(max_x, rx + rw)
        min_y = min(min_y, ry); max_y = max(max_y, ry + rh)
        min_z = min(min_z, rz); max_z = max(max_z, rz + rl)

    sx = max_x - min_x
    sz = max_z - min_z
    if sx <= 0 or sz <= 0:
        raise ValueError("包围盒尺寸无效")

    xb = split_boundaries(sx, cols)
    zb = split_boundaries(sz, rows)
    outputs = []
    total = rows * cols

    for r in range(rows):
        for c in range(cols):
            x0 = min_x + xb[c]; x1 = min_x + xb[c+1]
            z0 = min_z + zb[r]; z1 = min_z + zb[r+1]
            blocks = {}
            for reg in regions.values():
                rx, rz = reg.x, reg.z
                rw, rl = abs(reg.width), abs(reg.length)
                ry, rh = reg.y, abs(reg.height)
                ox0 = max(rx, x0); ox1 = min(rx + rw, x1)
                oz0 = max(rz, z0); oz1 = min(rz + rl, z1)
                if ox0 >= ox1 or oz0 >= oz1: continue
                for lx in range(rw):
                    wx = rx + lx
                    if wx < x0 or wx >= x1: continue
                    for lz in range(rl):
                        wz = rz + lz
                        if wz < z0 or wz >= z1: continue
                        for ly in range(rh):
                            wy = ry + ly
                            try:
                                blk = reg[lx, ly, lz]
                            except Exception:
                                continue
                            if blk is None: continue
                            if getattr(blk, "id", "") == "minecraft:air": continue
                            blocks[(wx, wy, wz)] = blk
            if not blocks:
                if progress_cb: progress_cb(len(outputs), total)
                continue
            ys = [p[1] for p in blocks.keys()]
            y_min = min(ys); y_max = max(ys)
            sub = Region(0, y_min, 0,
                         x1-x0, y_max-y_min+1, z1-z0)
            for (wx, wy, wz), blk in blocks.items():
                try:
                    sub[wx-x0, wy-y_min, wz-z0] = blk
                except Exception:
                    pass
            sub_schem = sub.as_schematic(
                name=f"{base}_r{r+1}c{c+1}",
                author="Toolkit",
                description=f"Slice r{r+1}c{c+1}")
            outputs.append({
                # 按原文件名 + 行列号命名，直接输出单个投影文件，不打包
                "filename": f"{base}_r{r+1}c{c+1}.litematic",
                "row": r + 1,
                "col": c + 1,
                "bytes": schem_to_bytes(sub_schem),
            })
            if progress_cb: progress_cb(len(outputs), total)
    return outputs


