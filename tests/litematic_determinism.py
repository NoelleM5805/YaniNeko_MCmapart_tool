# -*- coding: utf-8 -*-
"""同一个进程内连做两次，比对 .litematic 字节，判断差异是不是时间戳造成的。"""
import io
import gzip
import os
import sys
import zipfile

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))

import numpy as np
from PIL import Image

from maptool.palette import PALETTE_GROUPS, make_palette
from maptool.dithering import process_image
from maptool.schematic import build_mapart_schematic, schem_to_bytes

rng = np.random.default_rng(7)
w, h = 96, 64
arr = np.clip(rng.normal(128, 60, (h, w, 3)), 0, 255).astype(np.uint8)
img = Image.fromarray(arr, "RGB")

pal, used = make_palette(None)
idx, rgb = process_image(img, "weighted", "none", 1.0, pal)
print("idx digest:", hash(idx.tobytes()))

out = []
for i in range(3):
    schem, placed, counts = build_mapart_schematic(idx, pal, seed=20240922,
                                                  with_counts=True, alloc="random")
    b = schem_to_bytes(schem)
    if b[:2] == b"\x1f\x8b":
        inner = gzip.decompress(b)
    elif b[:2] == b"PK":
        z = zipfile.ZipFile(io.BytesIO(b))
        inner = z.read(z.namelist()[0])
    else:
        inner = b
    out.append((len(b), inner))
    print("run %d: zip=%d nbt=%d nbt_sha=%s placed=%d" % (
        i, len(b), len(inner), __import__("hashlib").sha256(inner).hexdigest()[:16], placed))

print("nbt equal 0vs1:", out[0][1] == out[1][1])
print("nbt equal 0vs2:", out[0][1] == out[2][1])
print("zip equal 0vs1:", out[0][0] == out[1][0])
if out[0][1] != out[1][1]:
    a, bb = out[0][1], out[1][1]
    n = min(len(a), len(bb))
    for k in range(n):
        if a[k] != bb[k]:
            print("first diff at byte", k, "of", len(a), len(bb))
            print("  ctx A:", a[max(0, k - 40):k + 20])
            print("  ctx B:", bb[max(0, k - 40):k + 20])
            break
