# -*- coding: utf-8 -*-
"""
投影切分：功能与不变量
======================

切分逻辑重写成了「numpy 向量化 + 自己位打包」，最容易悄悄坏掉的地方是
**方块的坐标映射**（写反轴、位打包顺序错、空气判断错都会生成能打开但内容
乱掉的投影），所以这里把三件事钉死：

1. **逐方块等价**：源投影的每一格，和切出来的每一块对应格子逐格比对
   （方块状态字符串完全一致，包括 glow_lichen 这类带属性的）。
2. **序列化逐字节等价**：快速序列化的输出和原来
   `Region.to_nbt() -> schem.save()` 那条路径完全一致（剥掉时间戳后）。
3. **推荐 / 预览**：按尺寸推荐的切法每块都 ≤128×128；预览的每块统计和
   实际切出来的方块数对得上；封面缩略图不是空的。

用法（工作区根目录）：
    python tests/slice_check.py
"""
import gzip
import io
import os
import sys
import time

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import numpy as np
from litemapy import Schematic as LSchematic
from nbtlib import File
from PIL import Image

from maptool import slicing
from maptool.dithering import process_image
from maptool.palette import BLOCK_INDEX, make_palette
from maptool.schematic import build_mapart_schematic, schem_to_bytes
from maptool.slicing import (MAP_SIZE, do_slice, plan_split, preview_slice,
                             recommend_split, split_boundaries)

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  [√] " if ok else "  [×] ") + name
          + (("  " + str(detail)) if detail and not ok else ""))


def strip_timestamps(blob):
    """把 TimeCreated / TimeModified 清零，剩下的内容必须逐字节相同。"""
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


def load_bytes(blob):
    return LSchematic.from_nbt(File.parse(io.BytesIO(gzip.decompress(blob))))


def make_schem(side, seed=13):
    rng = np.random.default_rng(seed)
    arr = np.clip(rng.normal(128, 60, (side, side, 3)), 0, 255).astype(np.uint8)
    pal, _ = make_palette(None)
    idx, _ = process_image(Image.fromarray(arr, "RGB"), "weighted", "none", 1.0, pal)
    schem, placed = build_mapart_schematic(idx, pal, seed=seed)
    return schem, placed


# ============================================================
def main():
    print("=" * 70)
    print("1) 序列化：快速路径 vs 原 schem_to_bytes（剥时间戳后逐字节）")
    print("=" * 70)
    for side in (24, 64, 128):
        schem, placed = make_schem(side)
        orig = schem_to_bytes(schem)
        fast = slicing._schematic_to_bytes_fast(schem)
        a, b = strip_timestamps(orig), strip_timestamps(fast)
        ok = a == b
        check("%dx%d 序列化内容一致（%d 方块，%d 字节）" % (side, side, placed, len(a)), ok)
        if not ok:
            for i in range(min(len(a), len(b))):
                if a[i] != b[i]:
                    print("     首个差异 @%d: %r vs %r" % (i, a[i:i + 12], b[i:i + 12]))
                    break
            print("     长度 %d vs %d" % (len(a), len(b)))

    # 位打包：随便造几组下标，和 litemapy 的逐格实现比。
    # 注意要比「载入回来之后的下标序列」，不能直接比 long 数组 ——
    # litemapy 会把每个 long & (2^64-1) 存成无符号，我们的 int64 里
    # 同样的位模式是负数，直接比数值会误报。
    from litemapy.storage import LitematicaBitArray
    pack_ok, pack_n = True, 0
    for n, nbits, mod in ((1, 2, 4), (7, 3, 8), (64, 5, 32), (65, 6, 64),
                          (300, 6, 64), (1000, 9, 512), (33, 63, 1 << 40)):
        vals = np.arange(n, dtype=np.int64) % mod
        got_arr = slicing._pack_bitarray(vals, nbits)
        unpacked = LitematicaBitArray.from_nbt_long_array(
            got_arr._to_nbt_long_array(), n, nbits)
        ref = LitematicaBitArray(n, nbits)
        for i, v in enumerate(vals):
            ref[i] = int(v)
        pack_n += 1
        if [unpacked[i] for i in range(n)] != [ref[i] for i in range(n)]:
            pack_ok = False
            print("     位打包不一致 n=%d nbits=%d" % (n, nbits))
    check("位打包和 LitematicaBitArray 一致（%d 组边界值）" % pack_n, pack_ok)

    print()
    print("=" * 70)
    print("2) 逐方块映射：源投影的每一格都能在对应子块里找到")
    print("=" * 70)
    schem, placed = make_schem(100)          # 故意不是 128 的整数倍
    content = schem_to_bytes(schem)
    src = slicing._load_schematic(content)
    sreg = list(src.regions.values())[0]
    SX, SY, SZ = abs(sreg.width), abs(sreg.height), abs(sreg.length)
    want = np.empty((SX, SY, SZ), dtype=object)
    for x in range(SX):
        for y in range(SY):
            for z in range(SZ):
                want[x, y, z] = str(sreg[x, y, z])

    for cols, rows in ((1, 1), (2, 2), (3, 3), (4, 1), (1, 4)):
        plan = plan_split(SX, SZ, MAP_SIZE, cols, rows)
        outs = do_slice(content, cols, rows, base_name="t")
        by_rc = {(o["row"], o["col"]): o for o in outs}
        bad, checked, covered = 0, 0, 0
        for c in plan["cells"]:
            o = by_rc.get((c["row"], c["col"]))
            for lx in range(c["w"]):
                for lz in range(c["h"]):
                    w = want[c["x0"] + lx, 0, c["z0"] + lz]
                    if o is None:
                        if w != "minecraft:air":
                            bad += 1
                        continue
                    sub = load_bytes(o["bytes"])
                    r2 = list(sub.regions.values())[0]
                    checked += 1
                    if str(r2[lx, 0, lz]) != w:
                        bad += 1
                    covered += 1
        check("%dx%d -> %2d 块，逐格核对 %d 格（不符 %d）"
              % (cols, rows, len(outs), checked, bad),
              bad == 0 and checked == SX * SZ and covered == SX * SZ)

    # 全量方块数不能凭空增减
    total = sum(o["blocks"] for o in do_slice(content, 3, 3, base_name="t"))
    check("切分前后总方块数不变（%d = %d）" % (total, placed), total == placed)

    print()
    print("=" * 70)
    print("3) 自动推荐：每块都不能超过 128×128")
    print("=" * 70)
    bad = []
    for sx, sz in ((64, 64), (128, 128), (129, 129), (256, 128), (300, 200),
                   (384, 384), (512, 640), (1000, 1000), (2000, 384),
                   (127, 1), (1, 1000), (1024, 1024)):
        plan, _ = recommend_split(sx, sz)
        tw = max(c["w"] for c in plan["cells"])
        th = max(c["h"] for c in plan["cells"])
        if tw > MAP_SIZE or th > MAP_SIZE:
            bad.append((sx, sz, tw, th))
    check("12 组尺寸推荐出来的每块都 ≤128×128", not bad, bad)

    plan, _ = recommend_split(384, 384)
    check("384×384 推荐 3×3（正好每块 128）",
          plan["cols"] == 3 and plan["rows"] == 3 and plan["count"] == 9)
    plan, txt = recommend_split(1000, 1000)
    check("1000×1000 推荐 8×8 且说明里带推荐理由（%s）" % txt[:34],
          plan["cols"] == 8 and plan["rows"] == 8 and "128" in txt)
    plan, txt = recommend_split(64, 64)
    check("64×64 不需要切（%s）" % txt, plan["count"] == 1 and "不用切" in txt)

    # 手动指定就是最终结果：写了 2x3 就切 2x3，不因为「每块太大」擅自改刀数
    p = plan_split(100, 100, MAP_SIZE, 3, 3)
    check("100×100 手动 3×3 均匀（%s）" % (p["x_bounds"],),
          p["x_bounds"] == split_boundaries(100, 3) and p["cols"] == 3)
    p = plan_split(128, 128, MAP_SIZE, 2, 2)
    check("128×128 手动 2×2 就是 2×2（%s）" % (p["x_bounds"],),
          p["x_bounds"] == [0, 64, 128])
    p = plan_split(384, 384, MAP_SIZE, 2, 3)
    check("384×384 手动 2×3 原样保留（%s × %s）"
          % (p["x_bounds"], p["z_bounds"]),
          p["x_bounds"] == [0, 192, 384] and p["z_bounds"] == [0, 128, 256, 384]
          and p["cols"] == 2 and p["rows"] == 3)
    check("手动超标时给出提醒（over=%s）" % (p["over"],),
          p["over"] == [["X", 192]])
    p = plan_split(384, 384, MAP_SIZE)
    check("不指定时推荐仍然是 3×3，每块 ≤128（over=%s）" % (p["over"],),
          p["cols"] == 3 and p["rows"] == 3 and p["over"] == [] and p["auto"])
    p = plan_split(512, 128, MAP_SIZE, 1, 1)
    check("手动 1×1 也原样保留（%s，over=%s）" % (p["x_bounds"], p["over"]),
          p["x_bounds"] == [0, 512] and p["over"] == [["X", 512]])
    p = plan_split(100, 100, 64)
    check("max_size=64 时 100×100 推荐 2×2（%s）" % (p["x_bounds"],),
          p["cols"] == 2 and max(c["w"] for c in p["cells"]) <= 64)
    p = plan_split(100, 100, 512, 2, 2)
    check("上限放到 512 时手动 2×2 不报超标（over=%s）" % (p["over"],),
          p["x_bounds"] == [0, 50, 100] and p["over"] == [])
    p = plan_split(100, 100, 64)
    check("max_size=64 时 100×100 切 2×2（%s）" % (p["x_bounds"],),
          p["cols"] == 2 and max(c["w"] for c in p["cells"]) <= 64)

    print()
    print("=" * 70)
    print("4) 预览：统计 / 缩略图 / 推荐对比")
    print("=" * 70)
    pv = preview_slice(content, base_name="预览图")
    check("预览拿到投影尺寸（%d×%d）" % (pv["sx"], pv["sz"]),
          (pv["sx"], pv["sz"]) == (100, 100))
    check("预览返回了缩略图 PNG（%s 字节）"
          % (len(pv["preview_png"]) if pv["preview_png"] else 0),
          bool(pv["preview_png"]) and pv["preview_png"][:4] == b"\x89PNG")
    check("默认方案 = 推荐方案（%d 块）" % pv["count"],
          pv["count"] == pv["recommend"]["count"] == 1)
    check("预览总方块数 = 实际（%d = %d）" % (pv["total_blocks"], placed),
          pv["total_blocks"] == placed)

    pv2 = preview_slice(content, 2, 2, base_name="预览图")
    check("手动 2×2 预览 4 块", pv2["count"] == 4 and len(pv2["cell_stats"]) == 4)
    stat_sum = sum(s["blocks"] for s in pv2["cell_stats"])
    real = sum(o["blocks"] for o in do_slice(content, 2, 2, base_name="t"))
    check("预览每块统计之和 = 实际切出方块数（%d = %d）" % (stat_sum, real),
          stat_sum == real)
    check("每块尺寸都是 50×50",
          all(s["w"] == 50 and s["h"] == 50 for s in pv2["cell_stats"]))
    check("推荐仍然是 1×1，和手动方案分开",
          pv2["recommend"]["cols"] == 1 and pv2["plan"]["cols"] == 2)

    # 缩略图分辨率：大投影必须下采样，不能把原尺寸铺出来
    big, _ = make_schem(512)
    bigc = schem_to_bytes(big)
    pv3 = preview_slice(bigc, base_name="big")
    thumb = Image.open(io.BytesIO(pv3["preview_png"]))
    check("512 的预览图是全分辨率（%s，step=%d）" % (thumb.size, pv3["step"]),
          thumb.size == (512, 512) and pv3["step"] == 1)
    check("预览图尺寸 = 投影内容尺寸（%d×%d）" % (pv3["sx"], pv3["sz"]),
          thumb.size == (pv3["sx"], pv3["sz"]))

    # 逐像素：预览图上每个像素必须正好是那个方块的代表色
    pv4 = preview_slice(content, base_name="pix")
    tim = Image.open(io.BytesIO(pv4["preview_png"])).convert("RGB")
    src4 = slicing._SourceIndex(slicing._load_schematic(content))
    sreg4 = list(src4.schem.regions.values())[0]
    cmap = src4.colors()
    colors4 = None
    bad_px = 0
    total_px = 0
    for z in range(pv4["sz"]):
        for x in range(pv4["sx"]):
            blk = sreg4[x, 0, z]
            bid = getattr(blk, "id", "")
            hexv = BLOCK_INDEX.get(bid, (None, None, None))[2]
            if not hexv:
                continue          # 空气 / 认不出来的方块：预览是纯黑，跳过
            want = slicing._hex_rgb(hexv)
            got = tim.getpixel((x, z))
            total_px += 1
            if tuple(got) != tuple(want):
                bad_px += 1
                if bad_px <= 3:
                    print("     像素 (%d,%d) %s 期望 %s 实际 %s"
                          % (x, z, bid, want, got))
    check("逐像素比对 %d 个方块，颜色不符 %d" % (total_px, bad_px),
          bad_px == 0 and total_px > 9000)

    print()
    print("=" * 70)
    print("5) 空块 / 边界")
    print("=" * 70)
    # 造一个中间是空的投影：只填左上角和右下角
    from litemapy import BlockState, Region
    reg = Region(0, 0, 0, 40, 1, 40)
    for x in range(0, 10):
        for z in range(0, 10):
            reg[x, 0, z] = BlockState("minecraft:stone")
    for x in range(30, 40):
        for z in range(30, 40):
            reg[x, 0, z] = BlockState("minecraft:stone")
    hole = reg.as_schematic(name="hole", author="t", description="")
    holec = slicing._schematic_to_bytes_fast(hole)
    outs = do_slice(holec, 4, 4, base_name="hole")
    check("中间空的块不产出文件（4×4 里只有 2 块有内容，得到 %d 个）" % len(outs),
          len(outs) == 2)
    check("产出的块行列号和内容对得上（%s）"
          % [(o["row"], o["col"]) for o in outs],
          sorted((o["row"], o["col"]) for o in outs) == [(1, 1), (4, 4)])
    pvh = preview_slice(holec, 4, 4, base_name="hole")
    check("预览里空的块统计为 0（filled=%d）" % pvh["filled"], pvh["filled"] == 2
          and len(pvh["cell_stats"]) == 16)
    check("空块在统计里是 0 方块",
          [s["blocks"] for s in pvh["cell_stats"] if s["blocks"] == 0] == [0] * 14)

    print()
    print("=" * 70)
    print("6) 性能（回归守护：别退回逐格循环）")
    print("=" * 70)
    t0 = time.perf_counter()
    outs = do_slice(bigc, 8, 8, base_name="perf")
    dt = time.perf_counter() - t0
    check("512×512 切 8×8 = 64 个文件耗时 %.2fs（上限 6s）" % dt, dt < 6.0)
    t0 = time.perf_counter()
    preview_slice(bigc, base_name="perf")
    dt2 = time.perf_counter() - t0
    check("512×512 预览耗时 %.2fs（上限 4s）" % dt2, dt2 < 4.0)

    print()
    print("=" * 66)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("  失败：" + f)
    print("=" * 66)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
