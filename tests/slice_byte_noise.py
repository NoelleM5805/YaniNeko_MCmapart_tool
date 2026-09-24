# -*- coding: utf-8 -*-
"""同一个进程里连切三次，看 do_slice 报出的字节数是否本身就会抖动。"""
import gzip
import hashlib
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))

import numpy as np
from PIL import Image

from maptool.palette import make_palette
from maptool.dithering import process_image
from maptool.schematic import build_mapart_schematic, schem_to_bytes
from maptool.slicing import do_slice


def _strip(blob):
    if blob[:2] == b"\x1f\x8b":
        blob = gzip.decompress(blob)
    out = bytearray(blob)
    for name in (b"TimeCreated", b"TimeModified"):
        key = b"\x04" + len(name).to_bytes(2, "big") + name
        start = 0
        while True:
            i = out.find(key, start)
            if i < 0:
                break
            v = i + len(key)
            for k in range(v, min(v + 8, len(out))):
                out[k] = 0
            start = v + 8
    return bytes(out)


rng = np.random.default_rng(7)
arr = np.clip(rng.normal(128, 60, (128, 128, 3)), 0, 255).astype(np.uint8)
img = Image.fromarray(arr, "RGB")
pal, used = make_palette(None)
idx, rgb = process_image(img, "weighted", "none", 1.0, pal)
schem, placed, counts = build_mapart_schematic(idx, pal, seed=20240922,
                                               with_counts=True, alloc="random")
content = schem_to_bytes(schem)
print("源文件字节:", len(content))

for run in range(3):
    outs = do_slice(content, 2, 3, base_name="t")
    info = [(o["filename"], len(o["bytes"]),
             hashlib.sha256(_strip(o["bytes"])).hexdigest()[:12]) for o in outs]
    print("run %d:" % run)
    for x in info:
        print("   ", x)
