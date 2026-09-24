# -*- coding: utf-8 -*-
"""
算出砂轮在三种 face 状态下，从正上方看下去是什么样。

地图画是单层平铺、俯视观看，所以只有「朝上的那些面」决定颜色。
blockstate 的旋转规则（Minecraft：先绕 X 转 x 度，再绕 Y 转 y 度）：
    face=floor   x=0    -> 模型自身的 up 面朝上
    face=wall    x=90   -> 模型自身的 north 面朝上
    face=ceiling x=180  -> 模型自身的 down 面朝上
这里按面积加权算出俯视的平均颜色，跟 #909090 比一下。
"""
import io
import json
import os
import urllib.request

import numpy as np
from PIL import Image

BASE = ("https://raw.githubusercontent.com/InventivetalentDev/minecraft-assets/"
        "1.21.4/assets/minecraft/")
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gs_tex")
os.makedirs(CACHE, exist_ok=True)


def fetch(rel):
    p = os.path.join(CACHE, rel.replace("/", "_"))
    if not os.path.isfile(p):
        with urllib.request.urlopen(BASE + rel, timeout=30) as r:
            data = r.read()
        with open(p, "wb") as f:
            f.write(data)
    return io.open(p, "rb").read()


def rot_normal(n, x, y):
    """把面法线按 blockstate 的 x/y 旋转。"""
    v = np.array(n, dtype=float)
    if x:
        t = np.radians(x)
        c, s = np.cos(t), np.sin(t)
        v = np.array([v[0], v[1] * c - v[2] * s, v[1] * s + v[2] * c])
    if y:
        t = np.radians(y)
        c, s = np.cos(t), np.sin(t)
        v = np.array([v[0] * c + v[2] * s, v[1], -v[0] * s + v[2] * c])
    return np.round(v).astype(int)


NORMALS = {
    "north": (0, 0, -1), "south": (0, 0, 1),
    "west": (-1, 0, 0), "east": (1, 0, 0),
    "up": (0, 1, 0), "down": (0, -1, 0),
}
# 旋转后朝上的面，其原始尺寸里落在水平面内的两个轴的索引
PLANE_AXIS = {   # 面名 -> 该面的两个水平轴（旋转前）
    "north": (0, 1), "south": (0, 1),
    "east": (1, 2), "west": (1, 2),
    "up": (0, 2), "down": (0, 2),
}


def main():
    bs = json.loads(fetch("blockstates/grindstone.json").decode("utf-8"))
    model = json.loads(fetch("models/block/grindstone.json").decode("utf-8"))
    edir = "models/block/"
    def resolve_model(m):
        name = m.split(":", 1)[1] if ":" in m else m
        return json.loads(fetch(edir + name + ".json").decode("utf-8"))

    tex_cache = {}

    def tex_avg(name, uv):
        """取 texture 上 uv 子矩形的平均颜色。uv 是 16 分制 [x1,y1,x2,y2]。"""
        rel = "textures/" + name + ".png"
        if rel not in tex_cache:
            tex_cache[rel] = Image.open(io.BytesIO(fetch(rel))).convert("RGBA")
        img = tex_cache[rel]
        W, H = img.size
        x1, y1, x2, y2 = uv
        a = np.array(img, dtype=np.float64)
        px1, px2 = sorted((int(round(x1 / 16 * W)), int(round(x2 / 16 * W))))
        py1, py2 = sorted((int(round(y1 / 16 * H)), int(round(y2 / 16 * H))))
        px2 = max(px2, px1 + 1); py2 = max(py2, py1 + 1)
        sub = a[py1:py2, px1:px2]
        # 只统计不透明的部分
        alpha = sub[:, :, 3:4] / 255.0
        w = alpha.sum()
        if w <= 0:
            return None, 0.0
        rgb = (sub[:, :, :3] * alpha).sum(axis=(0, 1)) / w
        return rgb, float(w)

    print("模型元素数：%d" % len(model["elements"]))
    for face_val, xr, yr in (("floor", 0, 0), ("wall", 90, 0), ("ceiling", 180, 0)):
        print()
        print("=" * 66)
        print("face=%s   (x=%d, y=%d)   俯视能看到的面：" % (face_val, xr, yr))
        print("=" * 66)
        total_w = 0.0
        acc = np.zeros(3)
        rows = []
        for el in model["elements"]:
            frm, to = np.array(el["from"], float), np.array(el["to"], float)
            for fname, fdef in el.get("faces", {}).items():
                n = rot_normal(NORMALS[fname], xr, yr)
                if tuple(n) != (0, 1, 0):
                    continue
                tex = fdef["texture"].lstrip("#")
                texname = model["textures"].get(tex, tex)
                # 统一成 "block/xxx" 这种完整路径
                texname = texname.split(":", 1)[1] if ":" in texname else texname
                if "/" not in texname:
                    texname = "block/" + texname
                uv = fdef.get("uv")
                if uv is None:
                    a1, a2 = PLANE_AXIS[fname]
                    uv = [frm[a1], frm[a2], to[a1], to[a2]]
                cube = to - frm
                a1, a2 = PLANE_AXIS[fname]
                area = abs(cube[a1] * cube[a2])
                rgb, w = tex_avg(texname, uv)
                if rgb is None or area <= 0:
                    continue
                rows.append((area, texname, uv, rgb))
                acc += rgb * area
                total_w += area
        rows.sort(key=lambda r: -r[0])
        for area, texname, uv, rgb in rows:
            print("   面积 %6.1f  %-20s uv=%-22s  平均色 #%02X%02X%02X"
                  % (area, texname, str(uv), *[int(round(c)) for c in rgb]))
        if total_w:
            avg = acc / total_w
            print("   -> 俯视面积加权平均色： #%02X%02X%02X  (%.1f, %.1f, %.1f)"
                  % (int(round(avg[0])), int(round(avg[1])), int(round(avg[2])),
                     avg[0], avg[1], avg[2]))
            print("      目标 #909090 = (144, 144, 144)，差 %.1f"
                  % float(np.linalg.norm(avg - np.array([144, 144, 144]))))
        else:
            print("   （没有朝上的面）")


main()
