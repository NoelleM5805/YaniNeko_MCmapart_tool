# -*- coding: utf-8 -*-
"""查一下砂轮的 blockstate / model，确定俯视时看到的是哪张贴图。"""
import io
import json
import sys
import urllib.request

BASE = ("https://raw.githubusercontent.com/InventivetalentDev/minecraft-assets/"
        "1.21.4/assets/minecraft/")
TARGETS = [
    "blockstates/grindstone.json",
    "models/block/grindstone.json",
    "models/block/grindstone_side.json",
]


def get(rel):
    url = BASE + rel
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            return r.read().decode("utf-8")
    except Exception as e:
        return "!! 取不到 %s：%s" % (rel, e)


for rel in TARGETS:
    print("=" * 70)
    print(rel)
    print("=" * 70)
    txt = get(rel)
    try:
        print(json.dumps(json.loads(txt), indent=1, ensure_ascii=False)[:2600])
    except Exception:
        print(txt[:600])
    print()
