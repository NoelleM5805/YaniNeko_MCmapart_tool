# -*- coding: utf-8 -*-
from litemapy import Region, BlockState, Schematic
import os
import tempfile

from .config import TARGET_BLOCK_ID
from .schematic import schem_to_bytes


def do_glow_lichen(content, target_faces, progress_cb=None):
    tmp = tempfile.NamedTemporaryFile(suffix=".litematic", delete=False)
    tmp.write(content); tmp.close()
    try:
        schem = Schematic.load(tmp.name)
    finally:
        try: os.unlink(tmp.name)
        except OSError: pass

    regions = dict(schem.regions)
    total_blocks = 0
    total_replaced = 0
    for reg in regions.values():
        try:
            positions = list(reg.block_positions())
        except Exception:
            continue
        for (x, y, z) in positions:
            total_blocks += 1
            try:
                blk = reg[x, y, z]
            except Exception:
                continue
            if blk is None or getattr(blk, "id", "") != TARGET_BLOCK_ID:
                continue
            try:
                reg[x, y, z] = blk.with_properties(**target_faces)
                total_replaced += 1
            except Exception:
                pass
        if progress_cb:
            progress_cb(total_replaced, total_blocks)

    return schem_to_bytes(schem), {"blocks": total_blocks, "replaced": total_replaced}


