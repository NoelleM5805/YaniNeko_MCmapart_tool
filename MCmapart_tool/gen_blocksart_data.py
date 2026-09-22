# -*- coding: utf-8 -*-
"""
重新生成 blocksart_data.py（内置调色板回退数据）
================================================

服务端优先读取同目录下的 blocksArt.json；只有读不到时才回退到
blocksart_data.py。所以升级/替换 blocksArt.json 之后，建议跑一次本脚本
把内置回退数据同步过去。

用法：
    python gen_blocksart_data.py                    # 用同目录的 blocksArt.json
    python gen_blocksart_data.py 别的路径.json       # 指定输入
"""

import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DST = os.path.join(HERE, "blocksart_data.py")

SOURCE_URL = ("https://github.com/TgkRuobin/vue3-mcpixelart"
              "/blob/main/src/assets/data/blocksArt.json")


def parse(obj):
    """blocksArt.json -> [(分类英文, 分类中文, 中文名, 英文 ID, normal hex), ...]"""
    rows = []
    if not isinstance(obj, list):
        raise ValueError("blocksArt.json 顶层应为数组")
    for cat in obj:
        if not isinstance(cat, dict):
            continue
        c_eng = str(cat.get("bname_eng") or "").strip()
        c_cn = str(cat.get("bname") or "").strip()
        for k in cat.get("bclass") or []:
            if not isinstance(k, dict):
                continue
            name_eng = str(k.get("name_eng") or "").strip()
            normal = str(k.get("normal") or "").strip().upper()
            if not name_eng or not normal:
                continue
            if not normal.startswith("#"):
                normal = "#" + normal
            if len(normal) != 7:
                continue
            rows.append((c_eng, c_cn, str(k.get("name") or "").strip(),
                         name_eng, normal))
    return rows


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "blocksArt.json")
    if not os.path.isfile(src):
        print("找不到输入文件：%s" % src)
        print("请从 %s 下载后放到脚本同目录。" % SOURCE_URL)
        return 1

    with io.open(src, encoding="utf-8") as f:
        rows = parse(json.load(f))
    if not rows:
        print("解析结果为空，请检查 %s 的结构。" % src)
        return 1

    out = []
    out.append("# -*- coding: utf-8 -*-")
    out.append('"""')
    out.append("自动生成 —— 请勿手工编辑，改数据请改 blocksArt.json 后重跑")
    out.append("gen_blocksart_data.py。")
    out.append("")
    out.append("数据来源：blocksArt.json")
    out.append("（%s）" % SOURCE_URL)
    out.append("")
    out.append("每行：(分类英文, 分类中文, 方块中文名, 方块英文 ID, normal 颜色 hex)")
    out.append("仅保留 normal 颜色值，用于按颜色分组构建调色板。")
    out.append('"""')
    out.append("")
    out.append("BLOCKS_ART_ROWS = [")
    for c_eng, c_cn, name_cn, name_eng, hexv in rows:
        out.append('    ("%s", "%s", "%s", "%s", "%s"),'
                   % (c_eng, c_cn, name_cn, name_eng, hexv))
    out.append("]")
    out.append("")

    with io.open(DST, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out))

    groups = len({r[4] for r in rows})
    print("已写入 %s" % DST)
    print("  %d 条色值 -> %d 个颜色组" % (len(rows), groups))
    return 0


if __name__ == "__main__":
    sys.exit(main())
