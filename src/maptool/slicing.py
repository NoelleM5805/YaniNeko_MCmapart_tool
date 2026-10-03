# -*- coding: utf-8 -*-
"""
投影切分。

设计要点（都是性能/功能上必须的，不要退回逐格循环）：

1. **不再逐格读写**。litemapy 的 `Region.__getitem__/__setitem__` 每次都要
   算坐标 + 查调色板 list，一个 384×384 的地图画有 14.7 万格，切 64 块就是
   上千万次 Python 级调用。这里改成直接对 `Region.__blocks` 这个 numpy
   数组做切片：子区域 = `blocks[x0:x1, :, z0:z1]`，再把区域调色板下标
   映射成「全局调色板下标」，只对**出现过的**下标建 `BlockState`。
2. **不再落临时文件**。原来 `Schematic.load(临时文件)`、`schem.save(临时文件)`
   各写一遍磁盘；nbtlib 的 `File.parse/File.write` 本来就接受文件对象，
   改成 `BytesIO` 内存里做完。
3. **自己写 BlockStates 位打包**。原来的 `Region.to_nbt()` 是
   `for x: for y: for z:` 逐格调 `LitematicaBitArray.__setitem__`，
   一个 128×128 的切片要 1.6 万次、且每次都在 Python 里移位。
   位打包是纯位移+或运算，可以整个用 numpy 向量化，**输出逐位一致**。
4. **切块尺寸按需求固定 128×128**。见 `recommend_split`：

投影尺寸 -> 推荐切法 -> 每块 ≤ 128×128（超了就多切一刀，不硬塞）
"""
import gzip
import io
import math
import zlib

import numpy as np
from litemapy import Region, Schematic
from litemapy.storage import LitematicaBitArray
from nbtlib import Compound, File, Int, List, Long, String

from .palette import BLOCK_INDEX, PALETTE_GROUPS
from .schematic import safe_stem

try:
    from litemapy.schematic import (AIR, LITEMAPY_NAME, LITEMAPY_VERSION,
                                    LITEMATIC_SUBVERSION, LITEMATIC_VERSION,
                                    MC_DATA_VERSION)
except Exception:  # pragma: no cover - 只影响元数据里的版本字段
    from litemapy import BlockState
    AIR = BlockState("minecraft:air")
    LITEMATIC_VERSION = 6
    LITEMATIC_SUBVERSION = 1
    MC_DATA_VERSION = 3465
    LITEMAPY_NAME = "Litemapy"
    LITEMAPY_VERSION = "unknown"


# ============================================================
# 地图画标准分块尺寸
# ============================================================
# 一张地图在游戏里就是 128×128 方块，所以「按投影尺寸自动切」的目标
# 尺寸就是 128 —— 切出来的每一块正好能贴一张地图。
MAP_SIZE = 128

# 一块最大允许多大。给个上限是为了别把单块做得太大导致游戏里卡
MAX_TILE = 1024


# ============================================================
# 切分方案（边界 + 推荐）
# ============================================================
def split_boundaries(total, parts):
    """
    把 [0, total] 平均切成 parts 段，返回 parts+1 个边界。

    和以前完全一致（四舍五入），保证 `parts` 模式下的切割结果不变。
    """
    total = int(total); parts = max(1, min(int(parts), total))
    return [int(round(i * total / parts)) for i in range(parts + 1)]


def _capped_boundaries(total, parts, max_size):
    """
    把 [0, total] 切成 parts 段，并保证**每段 ≤ max_size**。

    `parts` 由调用方保证已经 ≥ ceil(total / max_size)（见 `plan_split`），
    所以这里只是均匀切一下，不会再动段数。留着这个断言是为了别的地方
    误用时立刻暴露，而不是悄悄切出超标的大块。
    """
    total = int(total)
    max_size = max(1, int(max_size))
    need = max(1, int(math.ceil(total / max_size)))
    parts = max(1, min(int(parts), total))
    assert parts >= need, "parts=%d 切不出 ≤%d 的段（%d 格至少要 %d 段）" % (
        parts, max_size, total, need)
    return split_boundaries(total, parts)


def plan_split(sx, sz, max_size=MAP_SIZE, cols=None, rows=None):
    """
    算出一份切割方案。

    sx / sz     投影包围盒在 X / Z 方向的尺寸（方块数）
    max_size    每块最大边长（默认 128 = 一张地图）
    cols / rows 手动指定的列数 / 行数

    **手动指定就是最终结果**：写了 2×3 就切 2×3，哪怕每块会有 192 格。
    用户是拿着自己地图的编号来切的，工具不该擅自改刀数。
    max_size 只驱动「没指定时怎么推荐」，以及手动超标时的提醒
    （返回 `over` 列表）。

    返回 dict（前端直接拿去渲染）：
        {
          "sx","sz","max_size",
          "cols","rows","count",
          "x_bounds","z_bounds",            # 边界数组，长度 = cols/rows + 1
          "cells":[{"row","col","x0","z0","w","h"}, ...],
          "auto": bool,                     # 是否用了自动推荐
          "over": [[方向, 实际边长], ...],   # 超过 max_size 的块（手动时可能非空）
        }
    """
    sx = max(1, int(sx)); sz = max(1, int(sz))
    max_size = max(1, min(int(max_size or MAP_SIZE), MAX_TILE))

    if cols and rows:
        xb = split_boundaries(sx, max(1, int(cols)))
        zb = split_boundaries(sz, max(1, int(rows)))
        auto = False
    else:
        # 推荐：每块 ≤ max_size，段数就是下界，然后均匀分
        nc = max(1, int(math.ceil(sx / max_size)))
        nr = max(1, int(math.ceil(sz / max_size)))
        xb = _capped_boundaries(sx, nc, max_size)
        zb = _capped_boundaries(sz, nr, max_size)
        auto = True

    cols_n = len(xb) - 1
    rows_n = len(zb) - 1
    cells = []
    for r in range(rows_n):
        for c in range(cols_n):
            cells.append({
                "row": r + 1, "col": c + 1,
                "x0": xb[c], "z0": zb[r],
                "w": xb[c + 1] - xb[c], "h": zb[r + 1] - zb[r],
            })

    tw = max(c["w"] for c in cells)
    th = max(c["h"] for c in cells)
    over = []
    if tw > max_size:
        over.append(["X", tw])
    if th > max_size:
        over.append(["Z", th])
    return {
        "sx": sx, "sz": sz, "max_size": max_size,
        "cols": cols_n, "rows": rows_n,
        "count": cols_n * rows_n,
        "x_bounds": xb, "z_bounds": zb,
        "cells": cells,
        "auto": auto,
        "over": over,
    }


def recommend_split(sx, sz, max_size=MAP_SIZE):
    """
    按投影尺寸推荐切割方式 —— 默认「每块不超过 128×128」。

    规则很简单，且刻意不引入"哪个更好"的主观判断：
      列数 = ceil(宽 / 128)，行数 = ceil(高 / 128)，然后**均匀**分。
    均匀分意味着每块通常略小于 128（比如 2000 宽切 16 列 = 每列 125）。

    返回 (plan, text)，text 是给日志/界面用的一句话说明。
    """
    sx = max(1, int(sx)); sz = max(1, int(sz))
    max_size = max(1, min(int(max_size or MAP_SIZE), MAX_TILE))
    cols = max(1, int(math.ceil(sx / max_size)))
    rows = max(1, int(math.ceil(sz / max_size)))
    plan = plan_split(sx, sz, max_size=max_size, cols=cols, rows=rows)

    tw = max(c["w"] for c in plan["cells"])
    th = max(c["h"] for c in plan["cells"])
    if cols == 1 and rows == 1:
        text = "投影只有 %d×%d，还不到一张地图，不用切" % (sx, sz)
    else:
        text = ("按每块 ≤%d×%d 推荐：X 切 %d 列、Z 切 %d 行，共 %d 块，"
                "最大一块 %d×%d" % (max_size, max_size, cols, rows,
                                    plan["count"], tw, th))
    return plan, text


# ============================================================
# 读入：内存里解析，不落临时文件
# ============================================================
def _load_schematic(content):
    """从 bytes 直接读一个 Schematic（nbtlib 只做纯解析，不需要磁盘路径）。"""
    if content[:2] == b"\x1f\x8b":
        raw = gzip.decompress(content)
    else:
        raw = content
    nbt = File.parse(io.BytesIO(raw))
    return Schematic.from_nbt(nbt)


def _region_bbox(regions):
    """所有 region 的包围盒（X/Z 方向）。"""
    min_x = min_z = 0
    max_x = max_z = 0
    for reg in regions:
        rx, rz = reg.x, reg.z
        rw, rl = abs(reg.width), abs(reg.length)
        min_x = min(min_x, rx); max_x = max(max_x, rx + rw)
        min_z = min(min_z, rz); max_z = max(max_z, rz + rl)
    return min_x, max_x, min_z, max_z


# ============================================================
# 位打包（纯 numpy，逐位等价于 LitematicaBitArray）
# ============================================================
def _pack_longs(vals, nbits):
    """
    把调色板下标数组按 Litematica 的「跨 long 不分割」方式打包成 int64 数组。

    这就是 `LitematicaBitArray.__setitem__` 对每个下标做的那套：
        start = i * nbits; a = start >> 6; b = (start + nbits - 1) >> 6
        off = start & 63
        array[a] |= val << off
        if a != b: array[b] |= val >> (64 - off)

    因为 `nbits <= 64`，一个值最多横跨两个 long，所以用 np.add.at 分两次
    累加（而不是逐格 |=），顺序与原实现无关，结果完全一致。
    """
    vals = np.ascontiguousarray(vals, dtype=np.int64).reshape(-1)
    nbits = int(nbits)
    n = int(vals.size)
    if n == 0:
        return np.zeros(0, dtype=np.int64)

    total_bits = n * nbits
    n_longs = (total_bits + 63) // 64
    out = np.zeros(n_longs, dtype=np.int64)

    start = np.arange(n, dtype=np.int64) * nbits
    a = start >> 6
    off = (start & 63).astype(np.int64)
    mask = (1 << nbits) - 1
    low = (vals & mask) << off
    np.add.at(out, a, low)

    b = (start + (nbits - 1)) >> 6
    cross = b != a
    if np.any(cross):
        hi_shift = (64 - off[cross]).astype(np.int64)
        hi = (vals[cross] & mask) >> hi_shift
        np.add.at(out, b[cross], hi)
    return out


def _pack_bitarray(vals, nbits):
    """打包成 litemapy 的 LitematicaBitArray（只借它的 NBT 输出格式）。"""
    out = _pack_longs(vals, nbits)
    ba = LitematicaBitArray(int(vals.size), int(nbits))
    ba.array = [int(v) for v in out]
    return ba


def _region_to_nbt_fast(reg):
    """
    和 `Region.to_nbt()` 输出一致，但 BlockStates 用向量化打包。

    `reg._optimize_palette()` 必须先跑过（调用方负责），否则调色板里可能
    还有没被引用的项，nbits 会算错。
    """
    pal = reg._Region__palette
    blocks = reg._Region__blocks

    root = Compound()
    pos = Compound()
    pos["x"] = Int(reg.x); pos["y"] = Int(reg.y); pos["z"] = Int(reg.z)
    root["Position"] = pos
    size = Compound()
    size["x"] = Int(reg.width)
    size["y"] = Int(reg.height)
    size["z"] = Int(reg.length)
    root["Size"] = size
    root["BlockStatePalette"] = List[Compound]([b.to_nbt() for b in pal])
    root["Entities"] = List[Compound]([e.to_nbt() for e in reg.entities])
    root["TileEntities"] = List[Compound](
        [t.to_nbt() for t in reg.tile_entities])
    root["PendingBlockTicks"] = List[Compound](reg._Region__block_ticks)
    root["PendingFluidTicks"] = List[Compound](reg._Region__fluid_ticks)

    nbits = max(int(math.ceil(math.log(len(pal), 2))), 2) if len(pal) > 1 else 2
    # litemapy 的下标顺序是 index = y*(W*L) + z*W + x，而 Region.__blocks 的
    # 内存布局是 (x, y, z)。直接 reshape(-1) 得到的是 x 优先的顺序，位打包
    # 出来的东西会完全错位（文件还是能读，但方块全乱）。必须先转成 (y, z, x)。
    flat = np.ascontiguousarray(blocks.transpose(1, 2, 0)).reshape(-1)
    arr = _pack_bitarray(flat, nbits)
    root["BlockStates"] = arr._to_nbt_long_array()
    return root


def _gzip_bytes(raw):
    """
    把 NBT 字节流压成 gzip。

    必须用 zlib 手工拼，不能用 `gzip.GzipFile`：后者会按当前平台写 gzip
    头的 OS 字节（Windows 是 0x0B），而 `gzip.open` 固定写 0xFF。OS 字节
    不同 -> 整个 DEFLATE 流的字节都不同，长度也会差几十字节。
    这里逐项对齐 `gzip.open(path, "wb")`（compresslevel=9，mtime=0）：
        header: 1f 8b 08 00 <mtime:4> 02 ff   (XFL=2, OS=255)
        crc32 + isize
    """
    co = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)
    body = co.compress(raw) + co.flush()
    return (b"\x1f\x8b\x08\x00" + (0).to_bytes(4, "little") + b"\x02\xff"
            + body
            + (zlib.crc32(raw) & 0xFFFFFFFF).to_bytes(4, "little")
            + (len(raw) & 0xFFFFFFFF).to_bytes(4, "little"))


def _schematic_to_bytes_fast(schem):
    """
    把 Schematic 序列化成 .litematic 字节。

    结构完全照 `Schematic.to_nbt()` 拼（键顺序都一致），只把最耗时的
    `region.to_nbt()` 换成向量化版本。返回与原来逐字节相同的内容。
    """
    schem.update_metadata()
    regions = schem.regions
    if len(regions) < 1:
        raise ValueError("Empty schematic does not have any regions")

    root = Compound()
    root["Version"] = Int(schem.lm_version)
    root["SubVersion"] = Int(schem.lm_subversion)
    root["MinecraftDataVersion"] = Int(schem.mc_version)

    meta = Compound()
    enclose = Compound()
    enclose["x"] = Int(schem.width)
    enclose["y"] = Int(schem.height)
    enclose["z"] = Int(schem.length)
    meta["EnclosingSize"] = enclose
    meta["Author"] = String(schem.author)
    meta["Description"] = String(schem.description)
    meta["Name"] = String(schem.name)
    meta["Software"] = String(LITEMAPY_NAME + "_" + LITEMAPY_VERSION)
    meta["RegionCount"] = Int(len(regions))
    meta["TimeCreated"] = Long(schem.created)
    meta["TimeModified"] = Long(schem.modified)
    total_blocks = 0
    total_volume = 0
    for reg in regions.values():
        total_blocks += int(np.count_nonzero(reg._Region__blocks))
        total_volume += abs(reg.width * reg.height * reg.length)
    meta["TotalBlocks"] = Int(total_blocks)
    meta["TotalVolume"] = Int(total_volume)
    meta["PreviewImageData"] = schem._Schematic__preview
    root["Metadata"] = meta

    regs = Compound()
    for name, reg in regions.items():
        regs[name] = _region_to_nbt_fast(reg)
    root["Regions"] = regs

    f = File(root, gzipped=True, byteorder="big")
    buf = io.BytesIO()
    f.write(buf)
    return _gzip_bytes(buf.getvalue())


# ============================================================
# 区域索引（切分和预览共用）
# ============================================================
class _SourceIndex:
    """
    把一个 Schematic 的方块读成「全局调色板下标」大数组，避免逐格访问。

    - 每个源 region 建一张 `区域下标 -> 全局下标` 表
    - 全局调色板同时存下每个下标的代表颜色（预览用）
    - 按需下采样：预览只需要缩略图，用 numpy 步进切片，不会物化全尺寸
    """

    def __init__(self, schem):
        self.schem = schem
        self.regions = list(schem.regions.values())
        self.min_x, self.max_x, self.min_z, self.max_z = _region_bbox(self.regions)
        self.sx = self.max_x - self.min_x
        self.sz = self.max_z - self.min_z
        if self.sx <= 0 or self.sz <= 0:
            raise ValueError("包围盒尺寸无效")

        self.states = []          # 全局调色板：下标 -> BlockState
        self.state_key = {}
        self.region_luts = []     # 每个源 region 一张 区域下标 -> 全局下标
        self.nx = self.ny = self.nz = 1
        for reg in self.regions:
            lut = np.zeros(len(reg._Region__palette), dtype=np.int32)
            for i, state in enumerate(reg._Region__palette):
                # 0 号永远是空气这一档；调色板里**别的**位置也可能再放一个
                # 空气条目（litemapy 的 Region 从 NBT 读进来时就是这么干的），
                # 那些也要映射到 0，否则「有没有方块」的判断会出错。
                if i == 0 or getattr(state, "id", "") == "minecraft:air":
                    lut[i] = 0
                    continue
                key = _state_key(state)
                gi = self.state_key.get(key)
                if gi is None:
                    gi = len(self.states) + 1    # 先占位 +1，最后统一插到前面
                    self.state_key[key] = gi
                    self.states.append(state)
                lut[i] = gi
            self.region_luts.append(lut)
        self.states.insert(0, None)   # 0 号 = 空气

        # 全局调色板按「在投影里第一次出现的先后」重排一次。
        #
        # 为什么要有这一步：切出来的每一块，调色板顺序是按块内首次出现排的，
        # 而这里的顺序决定「同一个方块在不同块里的下标」。不重排的话，下标
        # 顺序完全跟随源文件内部的调色板顺序 —— 内容是对的，但同一个投影
        # 换台机器/换条路径切出来的字节可能不一样，也没法做基准比对。
        # 排成「按 x,y,z 遍历的首次出现顺序」既是确定性的，又和原来那版
        # 逐格 `sub[x,y,z] = blk` 写出来的调色板顺序完全一致。
        self._reorder_states()

    def _reorder_states(self):
        order = {}    # 全局下标 -> 在整个投影里 (x,y,z) C 序下的首次位置
        flat_off = 0
        for reg, lut in zip(self.regions, self.region_luts):
            mapped = lut[reg._Region__blocks].reshape(-1)
            uniq, first = np.unique(mapped, return_index=True)
            for u, f in zip(uniq.tolist(), first.tolist()):
                if u == 0:
                    continue
                pos = flat_off + f
                if u not in order or pos < order[u]:
                    order[u] = pos
            flat_off += mapped.size

        if not order:
            return
        new_order = sorted(order.keys(), key=lambda u: order[u])
        remap = np.zeros(len(self.states), dtype=np.int32)
        states = [None]
        for new_i, old_i in enumerate(new_order, start=1):
            remap[old_i] = new_i
            states.append(self.states[old_i])
        self.states = states
        for i, lut in enumerate(self.region_luts):
            self.region_luts[i] = remap[lut]

    def colors(self):
        """全局下标 -> (r,g,b)。认不出来的方块用 0x808080 兜底。"""
        lut = np.full((len(self.states), 3), 128, dtype=np.uint8)
        lut[0] = (0, 0, 0)
        for i, state in enumerate(self.states):
            if i == 0 or state is None:
                continue
            hexv = BLOCK_INDEX.get(getattr(state, "id", ""), (None, None, None))[2]
            if hexv:
                lut[i] = _hex_rgb(hexv)
        return lut

    def index_arrays(self, max_side=None):
        """
        返回 [(region, arr, ox, oz, step), ...]，arr 是全局下标数组，形状
        **(x, y, z)**，ox/oz 是 arr[0, .., 0] 对应的全局坐标（相对包围盒
        原点）。y 只有 1 层的平面地图画用 arr[:, 0, :]。

        **默认全分辨率（step=1）**：预览是「一格方块 = 一个像素」逐个渲染的，
        不能再下采样 —— 采样会把细线条整个跳过去，预览和实际切出来的东西
        对不上。传 `max_side` 才会按需降采样（只有内部统计走这条路）。
        """
        if max_side:
            step = max(1, int(math.ceil(max(self.sx, self.sz) / float(max_side))))
        else:
            step = 1
        out = []
        for reg, lut in zip(self.regions, self.region_luts):
            blocks = reg._Region__blocks
            # 负尺寸的 region 原点在另一端，取「存储坐标 -> 世界坐标」的偏移
            ox = reg.x + (reg.width + 1 if reg.width < 0 else 0)
            oz = reg.z + (reg.length + 1 if reg.length < 0 else 0)
            sub = blocks if step == 1 else blocks[::step, :, ::step]
            arr = lut[sub]
            out.append((reg, arr, ox - self.min_x, oz - self.min_z, step))
        return out

    def content_bbox(self, arrays=None, step=None):
        """
        真正有方块的那一小块范围（相对包围盒原点），返回 (x0, z0, x1, z1)。

        region 的声明尺寸常常比内容大（构建投影时按图片尺寸建，但地图画
        可能没铺满），所以推荐切法和缩略图都按**内容**来算，和实际切出
        来的东西才一致。
        """
        if arrays is None:
            arrays = self.index_arrays()
        x0 = z0 = None
        x1 = z1 = 0
        for _reg, arr, ox, oz, st in arrays:
            # arr 是 (x, y, z)，多层压成一层：xz 平面上「这一列有没有方块」
            flat = arr[:, 0, :] if arr.shape[1] == 1 else arr.any(axis=1)
            if flat.size == 0:
                continue
            nx_any = flat.any(axis=1)      # 沿 x
            nz_any = flat.any(axis=0)      # 沿 z
            xs = np.flatnonzero(nx_any)
            zs = np.flatnonzero(nz_any)
            if xs.size == 0 or zs.size == 0:
                continue
            bx0 = ox + int(xs[0]) * st
            bx1 = ox + int(xs[-1]) * st + st
            bz0 = oz + int(zs[0]) * st
            bz1 = oz + int(zs[-1]) * st + st
            x0 = bx0 if x0 is None else min(x0, bx0)
            z0 = bz0 if z0 is None else min(z0, bz0)
            x1 = max(x1, bx1); z1 = max(z1, bz1)
        if x0 is None:
            return 0, 0, self.sx, self.sz
        return x0, z0, min(x1, self.sx), min(z1, self.sz)


def _state_key(state):
    """BlockState 的哈希键（id + 方块状态），带上属性避免不同状态被合并。"""
    props = getattr(state, "_BlockState__properties", None)
    if props:
        return (state.id, tuple(sorted((str(k), str(v)) for k, v in props.items())))
    return (state.id, ())


def _hex_rgb(hexv):
    h = str(hexv).lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


# ============================================================
# 切分主体
# ============================================================
def _extract_cell(src, x0, x1, z0, z1):
    """
    从各个源 region 里抠出 [x0,x1) × [z0,z1) 这一块。

    返回 (cols, rows, global_blocks, y_min, y_max, data)：
        global_blocks —— 该块出现过的一维下标数组（升序，0 是空气）
        data          —— 形状 (x, y, z) 的全局下标数组，y 已经按 y_min 对齐
    """
    x0i, x1i = int(x0), int(x1)
    z0i, z1i = int(z0), int(z1)
    parts = []
    for reg, lut in zip(src.regions, src.region_luts):
        rx, rz = reg.x, reg.z
        rw, rl = abs(reg.width), abs(reg.length)
        ox0 = max(rx, x0i); ox1 = min(rx + rw, x1i)
        oz0 = max(rz, z0i); oz1 = min(rz + rl, z1i)
        if ox0 >= ox1 or oz0 >= oz1:
            continue
        blocks = reg._Region__blocks
        ox = rx + (reg.width + 1 if reg.width < 0 else 0)
        oz = rz + (reg.length + 1 if reg.length < 0 else 0)
        # 世界坐标 -> 存储下标
        sx0 = ox0 - ox; sx1 = ox1 - ox
        sz0 = oz0 - oz; sz1 = oz1 - oz
        sub = lut[blocks[sx0:sx1, :, sz0:sz1]]
        if not sub.any():
            continue
        parts.append((ox0, oz0, sub))

    if not parts:
        return None

    gx0 = min(p[0] for p in parts)
    gz0 = min(p[1] for p in parts)
    gx1 = max(p[0] + p[2].shape[0] for p in parts)
    gz1 = max(p[1] + p[2].shape[2] for p in parts)
    height = max(p[2].shape[1] for p in parts)

    # 把各块贴进一个 (gx, height, gz) 的缓冲区（原点统一到 gx0/gz0）
    buf = np.zeros((gx1 - gx0, height, gz1 - gz0), dtype=np.int32)
    for ox, oz, sub in parts:
        nx, ny, nz = sub.shape
        buf[ox - gx0:ox - gx0 + nx, :ny, oz - gz0:oz - gz0 + nz] = sub

    nz_any = buf.any(axis=(0, 1))
    nx_any = buf.any(axis=(1, 2))
    ny_any = buf.any(axis=(0, 2))
    xs = np.flatnonzero(nx_any); zs = np.flatnonzero(nz_any)
    ys = np.flatnonzero(ny_any)
    if xs.size == 0 or ys.size == 0 or zs.size == 0:
        return None

    data = buf[xs[0]:xs[-1] + 1, ys[0]:ys[-1] + 1, zs[0]:zs[-1] + 1]
    # 「出现过的下标」按**本块内首次出现**排序，而不是按数值大小。
    #
    # 这个顺序决定子块调色板的排列。原来那版是一格一格 `sub[x,y,z] = blk`
    # 写进去的，litemapy 的调色板就是「首次出现就 append」，所以顺序是
    # x→y→z 遍历下的首次出现。按数值排会得到同样的方块、不同的下标排列，
    # 成品能开但字节对不上（也没法做基准比对）。
    uniq, first = np.unique(data.reshape(-1), return_index=True)
    keep = uniq != 0
    uniq = uniq[keep]; first = first[keep]
    if uniq.size == 0:
        return None
    present = uniq[np.argsort(first, kind="stable")]
    return {
        "x0": gx0 + int(xs[0]),
        "z0": gz0 + int(zs[0]),
        "y0": int(ys[0]),
        "data": data,
        "present": present,
    }


def _build_cell_region(cell, src):
    """
    用抠出来的下标数组造一个 litemapy Region。

    调色板按 `_optimize_palette()` 的语义手工摊好：
      0 号固定是空气，其余按「首次出现」顺序，去重后只保留被用到的。
    这样 `_region_to_nbt_fast` 不需要再跑一次逐格的 `_optimize_palette`。
    """
    present = cell["present"]
    remap = np.zeros(int(present.max()) + 1, dtype=np.int64)
    remap[present] = np.arange(1, present.size + 1, dtype=np.int64)
    data = remap[cell["data"]].astype(np.uint32, copy=False)

    nx, ny, nz = data.shape
    reg = Region(0, cell["y0"], 0, nx, ny, nz)
    reg._Region__blocks = data
    reg._Region__palette = [AIR] + [src.states[int(i)] for i in present]
    return reg, int(np.count_nonzero(data))


def do_slice(content, cols=None, rows=None, progress_cb=None, base_name="slice",
             max_size=MAP_SIZE, plan=None):
    """
    把 .litematic 切成若干独立投影。

    content     .litematic 的字节
    cols/rows   手动指定列数/行数（为 None 时用 128 自动推荐）
    max_size    每块最大尺寸，默认 128
    plan        已经算好的方案（前端预览过的那份），给了就直接用，
                保证「预览看到的切法」和「实际切出来的」完全一致

    返回 [{"filename","row","col","bytes","blocks","w","h"}, ...]
    只包含**有方块**的块（空块不产出文件）。
    """
    base = safe_stem(base_name) or "slice"
    schem = _load_schematic(content)
    src = _SourceIndex(schem)

    if plan is None:
        if cols and rows:
            plan = plan_split(src.sx, src.sz, max_size=max_size,
                              cols=cols, rows=rows)
        else:
            plan, _ = recommend_split(src.sx, src.sz, max_size=max_size)

    outputs = []
    total = len(plan["cells"])
    for i, c in enumerate(plan["cells"]):
        cell = _extract_cell(src, src.min_x + c["x0"], src.min_x + c["x0"] + c["w"],
                             src.min_z + c["z0"], src.min_z + c["z0"] + c["h"])
        if cell is None:
            if progress_cb:
                progress_cb(i + 1, total)
            continue
        reg, nblocks = _build_cell_region(cell, src)
        sub = reg.as_schematic(
            name="%s_r%dc%d" % (base, c["row"], c["col"]),
            author="Toolkit",
            description="Slice r%dc%d" % (c["row"], c["col"]))
        outputs.append({
            "filename": "%s_r%dc%d.litematic" % (base, c["row"], c["col"]),
            "row": c["row"], "col": c["col"],
            "w": int(reg.width), "h": int(reg.length),
            "blocks": nblocks,
            "bytes": _schematic_to_bytes_fast(sub),
        })
        if progress_cb:
            progress_cb(i + 1, total)
    return outputs


# ============================================================
# 预览
# ============================================================
def preview_slice(content, cols=None, rows=None, base_name="slice",
                  max_size=MAP_SIZE, max_side=None):
    """
    不写任何文件，只算出「这么切会切出什么」。

    **预览图是全分辨率逐像素渲染的**：一个方块 = 一个像素，不做任何下采样。
    下采样会把细线条（1 格宽的边、文字笔画）整个跳过去，预览就和实际切出来
    的东西对不上；宁可图大一点，也要和成品一致。前端用
    `image-rendering: pixelated` 显示，缩放时不会糊。

    max_side 只对**统计**生效（大投影时数方块用降采样够准），传 None
    表示统计也走全分辨率。

    返回 dict：
        {
          "plan":        实际要用的方案（前端预览过的那份）
          "recommend":   自动推荐的方案（前端可以对比、一键套用）
          "text":        推荐理由（一句话）
          "base":        输出文件名前缀
          "cols","rows","count","max_size","sx","sz",
          "cell_stats":  [{"row","col","x0","z0","w","h","blocks","fill"}, ...]
          "preview_png": 预览图（PNG 字节，尺寸 = sx × sz），没内容时为 None
        }
    """
    base = safe_stem(base_name) or "slice"
    schem = _load_schematic(content)
    src = _SourceIndex(schem)

    # 全分辨率：这一步决定预览图的像素尺寸 = 投影的方块数
    arrays = src.index_arrays()
    bx0, bz0, bx1, bz1 = src.content_bbox(arrays)
    sx = max(1, bx1 - bx0)
    sz = max(1, bz1 - bz0)

    rec_plan, rec_text = recommend_split(sx, sz, max_size=max_size)
    if cols and rows:
        plan = plan_split(sx, sz, max_size=max_size, cols=cols, rows=rows)
    else:
        plan = rec_plan

    colors = src.colors()

    # 预览图：把所有源 region 逐像素叠到同一张 (Z, X) 画布上
    cx0 = bx0
    cz0 = bz0
    canvas = np.zeros((sz, sx), dtype=np.int32)
    for _reg, arr, ox, oz, _st in arrays:
        # arr 是 (x, y, z)；转到画布的 (z, x)
        flat = arr[:, 0, :] if arr.shape[1] == 1 else arr.max(axis=1)
        if flat.shape[0] == 0 or flat.shape[1] == 0:
            continue
        arr2 = flat.T                       # (z, x)
        # 目标画布上的起点（可能为负，裁掉）
        tx = ox - cx0
        tz = oz - cz0
        sxa = max(0, -tx); sza = max(0, -tz)
        txa = max(0, tx); tza = max(0, tz)
        nx2 = min(arr2.shape[1] - sxa, sx - txa)
        nz2 = min(arr2.shape[0] - sza, sz - tza)
        if nx2 <= 0 or nz2 <= 0:
            continue
        canvas[tza:tza + nz2, txa:txa + nx2] = \
            arr2[sza:sza + nz2, sxa:sxa + nx2]

    rgb = colors[canvas]
    preview_png = _encode_png(rgb) if np.any(canvas) else None

    # 每块统计：全分辨率时数组已经是 1:1，直接数就是精确值
    stat_arrays = arrays if max_side is None else src.index_arrays(max_side=max_side)
    cell_stats = []
    for c in plan["cells"]:
        blocks = int(_count_blocks(stat_arrays, c["x0"], c["x0"] + c["w"],
                                   c["z0"], c["z0"] + c["h"]))
        cell_stats.append({
            "row": c["row"], "col": c["col"],
            "x0": c["x0"], "z0": c["z0"], "w": c["w"], "h": c["h"],
            "blocks": blocks,
            "fill": round(blocks / float(c["w"] * c["h"]), 3) if c["w"] * c["h"] else 0.0,
        })
    filled = [s for s in cell_stats if s["blocks"] > 0]

    return {
        "plan": plan,
        "recommend": rec_plan,
        "text": rec_text,
        "base": base,
        "sx": sx, "sz": sz,
        "max_size": plan["max_size"],
        "cols": plan["cols"], "rows": plan["rows"], "count": plan["count"],
        "filled": len(filled),
        "total_blocks": int(sum(s["blocks"] for s in cell_stats)),
        "cell_stats": cell_stats,
        # 一格方块 = 一个像素，前端据此用 pixelated 放大显示
        "pixel_scale": 1,
        "step": 1,
        "preview_w": sx,
        "preview_h": sz,
        "preview_png": preview_png,
    }


def _count_blocks(arrays, x0, x1, z0, z1):
    """按下采样数组数方块（预览用，允许小误差），范围是内容坐标系里的方块数。"""
    total = 0
    for _reg, arr, ox, oz, st in arrays:
        # arr 是 (x, y, z)，预览按 xz 平面看
        flat = arr[:, 0, :] if arr.shape[1] == 1 else arr.max(axis=1)
        ax0 = max(0, int(math.floor((x0 - ox) / float(st))))
        ax1 = min(flat.shape[0], int(math.ceil((x1 - ox) / float(st))))
        az0 = max(0, int(math.floor((z0 - oz) / float(st))))
        az1 = min(flat.shape[1], int(math.ceil((z1 - oz) / float(st))))
        if ax1 <= ax0 or az1 <= az0:
            continue
        total += int(np.count_nonzero(flat[ax0:ax1, az0:az1]))
    return total


def _encode_png(rgb):
    """(H, W, 3) uint8 -> PNG 字节。"""
    from PIL import Image
    buf = io.BytesIO()
    Image.fromarray(rgb, "RGB").save(buf, "PNG", optimize=False)
    return buf.getvalue()


def slice_source_bbox(content):
    """只读包围盒，给「上传后立刻显示投影尺寸」用。"""
    schem = _load_schematic(content)
    src = _SourceIndex(schem)
    return src.sx, src.sz
