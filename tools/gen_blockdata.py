# -*- coding: utf-8 -*-
"""
生成方块数据表与图标贴图集
==========================

输入：
    src/maptool/data/minecraft_blocks_mapcolor.json   颜色 + 中文名原始数据
    zh_cn.json                       Minecraft 官方中文语言文件（用于 中文名 -> 方块 ID）
    <纹理目录>/*.png                  Minecraft 方块贴图

输出（都写到 src/maptool/data/，服务端直接按包内模块读）：
    src/maptool/data/blockdata.py      方块表（ID / 中文名 / 颜色 / 图标坐标 / 方块状态）
    src/maptool/data/block_icons.png   图标贴图集（16px 一格）

用法（在工作区根目录执行）：
    python tools/gen_blockdata.py [--textures <目录>] [--lang <zh_cn.json>]

纹理目录缺省会依次尝试：
    1) 命令行 --textures
    2) tools/textures_block/
    3) tools/_build_cache/textures/（上次联网下载的缓存）
    4) 从 raw.githubusercontent.com 按需下载（需要联网）
"""

import argparse
import io
import json
import os
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))       # <root>/tools
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "src", "maptool", "data")     # 生成物落在包里

SRC_JSON = os.path.join(DATA, "minecraft_blocks_mapcolor.json")
OUT_PY = os.path.join(DATA, "blockdata.py")
OUT_PNG = os.path.join(DATA, "block_icons.png")

ICON_COLS = 16
ICON_SIZE = 16

ASSET_REF = "1.21.4"
TEX_URL = ("https://raw.githubusercontent.com/InventivetalentDev/minecraft-assets/"
           + ASSET_REF + "/assets/minecraft/textures/block/")
LANG_URL = ("https://raw.githubusercontent.com/InventivetalentDev/minecraft-assets/"
            + ASSET_REF + "/assets/minecraft/lang/zh_cn.json")

# 原始数据里的写法与官方语言文件不一致的少量条目
NAME_FIX = {
    "腹枝之心": "creaking_heart",     # 官方译名「嘎枝之心」
}

# 取图标时按顺序尝试的贴图名后缀（方块的"正面"识别度最高）
TEX_SUFFIXES = ("", "_top", "_side", "_front", "_still", "_end", "_outer")

COLOR16 = ("white", "orange", "magenta", "light_blue", "yellow", "lime", "pink",
           "gray", "light_gray", "cyan", "purple", "blue", "brown", "green",
           "red", "black")

# 贴图名与方块 ID 对不上时的映射
TEX_ALIAS = {
    # 地毯沿用羊毛贴图
    **{c + "_carpet": c + "_wool" for c in COLOR16},
    # 「木头 / 菌核」用对应原木 / 菌柄的侧面贴图
    "oak_wood": "oak_log", "stripped_oak_wood": "stripped_oak_log",
    "spruce_wood": "spruce_log", "stripped_spruce_wood": "stripped_spruce_log",
    "birch_wood": "birch_log", "stripped_birch_wood": "stripped_birch_log",
    "jungle_wood": "jungle_log", "stripped_jungle_wood": "stripped_jungle_log",
    "acacia_wood": "acacia_log", "stripped_acacia_wood": "stripped_acacia_log",
    "dark_oak_wood": "dark_oak_log", "stripped_dark_oak_wood": "stripped_dark_oak_log",
    "mangrove_wood": "mangrove_log", "stripped_mangrove_wood": "stripped_mangrove_log",
    "cherry_wood": "cherry_log", "stripped_cherry_wood": "stripped_cherry_log",
    "pale_oak_wood": "pale_oak_log", "stripped_pale_oak_wood": "stripped_pale_oak_log",
    "crimson_hyphae": "crimson_stem", "stripped_crimson_hyphae": "stripped_crimson_stem",
    "warped_hyphae": "warped_stem", "stripped_warped_hyphae": "stripped_warped_stem",
    # 少数贴图名与方块 ID 不同的方块
    "snow_block": "snow",
    "magma_block": "magma",
    "smooth_sandstone": "sandstone_top",
    "smooth_red_sandstone": "red_sandstone_top",
    "smooth_quartz": "quartz_block_top",
    # 测重压力板没有独立贴图，直接复用金属块贴图
    # （查 models/block/light_weighted_pressure_plate.json -> textures.texture = block/gold_block）
    "light_weighted_pressure_plate": "gold_block",
    "heavy_weighted_pressure_plate": "iron_block",
}


def tex_base(block_id):
    """方块 ID -> 贴图基名。"""
    if block_id in TEX_ALIAS:
        return TEX_ALIAS[block_id]
    if block_id.startswith("waxed_"):          # 涂蜡铜系列沿用未涂蜡的贴图
        return block_id[len("waxed_"):]
    return block_id


# 需要指定方块状态（blockstate）的方块：ID -> "键=值,键=值"
# 地图画是单层平铺，这些方块的朝向会影响外观，所以要写死在投影里。
BLOCK_PROPS = {
    # 发光地衣：贴在下表面（down=true），其余面全关
    "glow_lichen": ("down=true,up=false,north=false,south=false,"
                    "east=false,west=false,waterlogged=false"),
    # 铁活板门：俯视放置（贴地，处于方块下半部），朝北、关闭
    "iron_trapdoor": ("facing=north,half=bottom,open=false,"
                      "powered=false,waterlogged=false"),
    # 轻质测重压力板：无实体踩踏，power=0
    "light_weighted_pressure_plate": "power=0",
}


# ------------------------------------------------------------
# 载入
# ------------------------------------------------------------
def load_source():
    if not os.path.isfile(SRC_JSON):
        raise SystemExit("找不到数据文件：%s" % SRC_JSON)
    with io.open(SRC_JSON, encoding="utf-8") as f:
        data = json.load(f)
    rows = data["blocks"] if isinstance(data, dict) else data

    # 去重：同名方块取「后面」出现的那条（新数据覆盖旧数据），
    # 位置沿用首次出现的地方，保证顺序稳定。
    order, seen, conflicts = [], {}, []
    for it in rows:
        name = str(it["name"]).strip()
        color = str(it["color"]).strip().upper()
        if not name or not color.startswith("#"):
            continue
        if name in seen:
            if seen[name] != color:
                conflicts.append((name, seen[name], color))
                seen[name] = color          # 后面的覆盖前面的
            continue
        seen[name] = color
        order.append(name)
    return [(n, seen[n]) for n in order], conflicts


def load_lang(path):
    if not path or not os.path.isfile(path):
        cache_dir = os.path.join(HERE, "_build_cache")
        os.makedirs(cache_dir, exist_ok=True)
        cache = os.path.join(cache_dir, "zh_cn.json")
        if os.path.isfile(cache):
            path = cache
        else:
            print("下载 Minecraft 中文语言文件 …")
            urllib.request.urlretrieve(LANG_URL, cache)
            path = cache
    with io.open(path, encoding="utf-8") as f:
        lang = json.load(f)
    cn2id = {}
    for k, v in lang.items():
        if k.startswith("block.minecraft."):
            cn2id.setdefault(v, k[len("block.minecraft."):])
    return cn2id


# ------------------------------------------------------------
# 纹理
# ------------------------------------------------------------
def list_local_textures(d):
    if not d or not os.path.isdir(d):
        return None
    names = set()
    for fn in os.listdir(d):
        if fn.lower().endswith(".png"):
            names.add(fn[:-4])
    return names or None


def download_texture(base, cache_dir):
    """本地没命中时，逐个后缀试着下载。"""
    os.makedirs(cache_dir, exist_ok=True)
    for suf in TEX_SUFFIXES:
        fn = base + suf + ".png"
        dest = os.path.join(cache_dir, fn)
        if not os.path.isfile(dest):
            try:
                urllib.request.urlretrieve(TEX_URL + fn, dest)
            except Exception:
                if os.path.isfile(dest):
                    os.unlink(dest)
                continue
        return fn
    return None


def resolve_texture(block_id, local_names, cache_dir, offline):
    base = tex_base(block_id)
    for suf in TEX_SUFFIXES:
        fn = base + suf + ".png"
        if local_names is not None and fn[:-4] in local_names:
            return fn
    if offline:
        return None
    return download_texture(base, cache_dir)


# ------------------------------------------------------------
# 主流程
# ------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--textures", default=os.path.join(HERE, "textures_block"),
                    help="方块贴图目录（含 *.png）")
    ap.add_argument("--lang", default=None, help="zh_cn.json 路径")
    ap.add_argument("--offline", action="store_true", help="只使用本地贴图，不联网")
    args = ap.parse_args()

    pairs, conflicts = load_source()
    print("原始条目去重后：%d 个方块" % len(pairs))
    if conflicts:
        print("!! 同名但颜色冲突（取后面出现的那条）：")
        for n, a, b in conflicts:
            print("     %s  %s -> %s" % (n, a, b))

    cn2id = load_lang(args.lang)
    print("语言文件提供 %d 个方块译名" % len(cn2id))

    local_names = list_local_textures(args.textures)
    print("本地贴图目录：%s（%s 个贴图）"
          % (args.textures, len(local_names) if local_names else "无"))

    cache_dir = os.path.join(HERE, "_build_cache", "textures")

    # ---------- 名称 -> ID ----------
    resolved, unresolved = [], []
    for name, color in pairs:
        bid = NAME_FIX.get(name) or cn2id.get(name)
        if not bid:
            unresolved.append((name, color))
            continue
        resolved.append((bid, name, color))

    print("解析成功 %d / %d" % (len(resolved), len(pairs)))
    if unresolved:
        print("!! 无法解析中文名（将跳过）：")
        for n, c in unresolved:
            print("     %s  %s" % (n, c))

    # ---------- 贴图 ----------
    def work(item):
        bid, name, color = item
        tex = resolve_texture(bid, local_names, cache_dir, args.offline)
        return bid, name, color, tex

    with ThreadPoolExecutor(max_workers=16) as ex:
        results = list(ex.map(work, resolved))

    no_icon = [(b, n) for b, n, c, t in results if not t]
    print("取到图标 %d / %d" % (len(results) - len(no_icon), len(results)))
    if no_icon:
        print("!! 没有图标的方块（用纯色块代替）：")
        print("     " + " / ".join("%s(%s)" % (n, b) for b, n in no_icon))

    # ---------- 拼贴图集 ----------
    from PIL import Image

    ready = [(b, n, c, t) for b, n, c, t in results if t]
    ready.sort(key=lambda r: (r[2], r[1]))          # 颜色 -> 名称，稳定顺序
    missing = [(b, n, c) for b, n, c, t in results if not t]

    cells = ready + [None] * 0
    total = len(ready) + len(missing)
    cols = ICON_COLS
    rows = (total + cols - 1) // cols
    sheet = Image.new("RGBA", (cols * ICON_SIZE, rows * ICON_SIZE), (0, 0, 0, 0))

    icon_index = {}
    idx = 0
    for bid, name, color, tex in ready:
        src = os.path.join(args.textures, tex)
        if not os.path.isfile(src):
            src = os.path.join(cache_dir, tex)
        im = Image.open(src).convert("RGBA")
        if im.size != (ICON_SIZE, ICON_SIZE):
            im = im.resize((ICON_SIZE, ICON_SIZE), Image.NEAREST)
        x, y = (idx % cols) * ICON_SIZE, (idx // cols) * ICON_SIZE
        sheet.paste(im, (x, y))
        icon_index[bid] = (idx % cols, idx // cols)
        idx += 1

    # 没有贴图的方块：给一个纯色格，保证坐标完整
    for bid, name, color in missing:
        x, y = (idx % cols) * ICON_SIZE, (idx // cols) * ICON_SIZE
        c = hex_to_rgb(color)
        sheet.paste(Image.new("RGBA", (ICON_SIZE, ICON_SIZE), c + (255,)), (x, y))
        icon_index[bid] = (idx % cols, idx // cols)
        idx += 1

    sheet.save(OUT_PNG)
    print("贴图集：%s  %dx%d  %d 格" % (OUT_PNG, sheet.width, sheet.height, idx))

    # ---------- 写数据模块 ----------
    lines = [
        "# -*- coding: utf-8 -*-",
        '"""',
        "自动生成 —— 请勿手工编辑，改数据请重跑 tools/gen_blockdata.py。",
        "",
        "数据来源：src/maptool/data/minecraft_blocks_mapcolor.json",
        "方块 ID / 中文名：Minecraft %s 官方语言文件 zh_cn.json" % ASSET_REF,
        "图标：Minecraft %s 官方方块贴图，拼成同目录下的 block_icons.png" % ASSET_REF,
        "",
        "每行：(方块 ID, 中文名, 颜色 hex, 图标列, 图标行, 方块状态)",
        "方块状态形如 \"down=true,up=false\"，没有特殊状态就是空串。",
        '"""',
        "",
        "ICON_SHEET = \"block_icons.png\"",
        "ICON_SIZE = %d" % ICON_SIZE,
        "ICON_COLS = %d" % cols,
        "ICON_ROWS = %d" % rows,
        "",
        "BLOCK_ROWS = [",
    ]
    rows_sorted = sorted(results, key=lambda r: (r[2], r[1]))
    n_props = 0
    for bid, name, color, tex in rows_sorted:
        cx, cy = icon_index[bid]
        props = BLOCK_PROPS.get(bid, "")
        if props:
            n_props += 1
        lines.append('    ("%s", "%s", "%s", %d, %d, "%s"),'
                     % (bid, name, color, cx, cy, props))
    lines.append("]")
    lines.append("")

    with io.open(OUT_PY, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
    print("数据模块：%s  %d 个方块（其中 %d 个带方块状态）"
          % (OUT_PY, len(rows_sorted), n_props))
    print()
    print("颜色组：%d" % len({r[2] for r in rows_sorted}))
    return 0


def hex_to_rgb(h):
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


if __name__ == "__main__":
    sys.exit(main())
