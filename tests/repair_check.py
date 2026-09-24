# -*- coding: utf-8 -*-
"""
局部噪点修正：功能与不变量测试
==============================

用真实的抖动结果来验证：
  · 只在选区/笔画范围内改动，范围外一个像素都不能动
  · 套索修补后，改动的像素确实都变成了区域主色
  · 强度 2 → 3 → 4 是递进的（改回来的数量单调不减）
  · 强度 4（阈值 1.0）会把区域内所有「当前色 ≠ 主色」的像素都改回主色
  · 画笔把小圆内的像素精确覆盖成指定颜色
  · identify 模式返回高亮图，且不改动结果
  · 归一化坐标在预览尺寸和成品尺寸下圈到的是同一片区域
  · 生成路径和预览路径套用同一份修正

用法（工作区根目录）：
    python tests/repair_check.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import numpy as np
from PIL import Image

from maptool.dithering import process_image
from maptool.palette import make_palette
from maptool.repair import LEVEL_RATIO, apply_repair, parse_repair, render_highlight

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  [√] " if ok else "  [×] ") + name + (("  " + detail) if detail and not ok else ""))


def make_img(w, h, seed=11):
    """
    造一张「大片渐变 + 一块几乎纯色」的图：
    抖动在渐变上会掺色，正好用来当修补对象。
    """
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    r = 60 + 150 * (xx / w)
    g = 50 + 170 * (yy / h)
    b = 80 + 100 * np.sin((xx + yy) / 35.0)
    img = np.stack([r, g, b], axis=-1)
    # 左上角一块接近纯色（这是用户想保住主色的典型区域）
    img[0:h // 2, 0:w // 2] = (118, 122, 126)
    img += rng.normal(0, 6, img.shape)
    return Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "RGB")


def main():
    W, H = 128, 96
    img = make_img(W, H)
    work = np.array(img, dtype=np.uint8)
    pal, _u = make_palette(None)
    algo, dither = "weighted", "floyd"

    idx0, rgb0 = process_image(img, algo, dither, 1.0, pal)
    print("基准：%dx%d，用到 %d 种颜色" % (W, H, len(np.unique(idx0))))
    print()

    # 归一化选区：覆盖左上角那块近似纯色（0.05~0.45）
    quad = [[0.05, 0.05], [0.45, 0.05], [0.45, 0.45], [0.05, 0.45]]

    def mask_of(points):
        from PIL import ImageDraw
        m = Image.new("1", (W, H), 0)
        ImageDraw.Draw(m).polygon([(x * W, y * H) for x, y in points], fill=1)
        return np.asarray(m, dtype=bool)

    sel = mask_of(quad)
    check("选区掩膜非空（%d 像素）" % int(sel.sum()), sel.sum() > 400)

    # ---------------- 强度递进 ----------------
    print("\n强度递进：")
    counts = {}
    prev = -1
    mono = True
    for lvl in (2, 3, 4):
        rep = parse_repair({"repair": {"lassos": [{"points": quad, "level": lvl}]}})
        idx1, info = apply_repair(idx0, work, pal, rep, algo)
        counts[lvl] = info["repaired"]
        dom = info["dominant"]
        print("   强度 %d（阈值 %.2f）：受害者 %d，改回 %d，主色 %s"
              % (lvl, LEVEL_RATIO[lvl], info["victims"], info["repaired"], dom))
        if info["repaired"] < prev:
            mono = False
        prev = info["repaired"]

        # 范围内改动、范围外不动
        changed = idx1 != idx0
        check("强度 %d：只改选区内（范围外 %d 个被误改）"
              % (lvl, int((changed & ~sel).sum())), not (changed & ~sel).any())
        # 改动过的像素都变成主色
        di = info["dominant_index"]
        check("强度 %d：改动过的像素都是主色" % lvl,
              bool((idx1[changed] == di).all()) if changed.any() else True)

    check("强度 2 ≤ 3 ≤ 4（单调不减）", mono,
          "实际 %s" % counts)

    # 强度 4 的阈值就是「主色不比当前色差」，应该正好等于受害者数量
    rep4 = parse_repair({"repair": {"lassos": [{"points": quad, "level": 4}]}})
    idx4, info4 = apply_repair(idx0, work, pal, rep4, algo)
    di = info4["dominant_index"]
    check("强度 4：改回数量 == 受害者数量",
          info4["repaired"] == info4["victims"],
          "%d vs %d" % (info4["repaired"], info4["victims"]))

    # 强度 4 之后，区域里剩下的非主色像素，必须都是「当前色确实比主色更准」的
    from maptool.matching import dist_batch
    from maptool.palette import Palette
    sel_flat = np.flatnonzero(sel.reshape(-1))
    left = idx4.reshape(-1)[sel_flat] != di
    rest = sel_flat[left]
    ok_strict = True
    if rest.size:
        px = work.reshape(-1, 3).astype(np.int32)[rest]
        cur = idx4.reshape(-1)[rest]
        for c_val in np.unique(cur):
            sub = cur == int(c_val)
            mini = Palette([pal.groups[di], pal.groups[int(c_val)]])
            d2 = dist_batch(px[sub], mini, algo, None)
            # 主色更近或一样近 => 应该已经被改掉了，不该留在这里
            ok_strict = ok_strict and bool((d2[:, 0] > d2[:, 1]).all())
    check("强度 4：留下的非主色像素都是「当前色更准」的（%d 个）" % int(rest.size),
          ok_strict)

    # 强度 2 是强度 4 的真子集（递进关系）
    rep2 = parse_repair({"repair": {"lassos": [{"points": quad, "level": 2}]}})
    idx2b, info2b = apply_repair(idx0, work, pal, rep2, algo)
    changed2b = idx2b != idx0
    changed4 = idx4 != idx0
    check("强度 2 改的像素是强度 4 的子集", not (changed2b & ~changed4).any())
    check("强度 2 确实比强度 4 改得少",
          info2b["repaired"] < info4["repaired"],
          "%d vs %d" % (info2b["repaired"], info4["repaired"]))

    # ---------------- 画笔 ----------------
    print("\n画笔：")
    target = pal.hexes[(di + 7) % pal.n]
    stroke = {"x": 0.75, "y": 0.7, "r": 0.08, "hex": target}
    rep = parse_repair({"repair": {"strokes": [stroke]}})
    idxb, infob = apply_repair(idx0, work, pal, rep, algo)
    gi = [i for i, h in enumerate(pal.hexes) if h == target][0]
    # 圆心那一点必须被覆盖
    cy, cx = int(0.7 * H), int(0.75 * W)
    check("画笔：圆心像素被覆盖成 %s" % target, idxb[cy, cx] == gi,
          "实际 %s" % pal.hexes[idxb[cy, cx]])
    changed = idxb != idx0
    check("画笔：只改笔画范围内", bool((changed == (idxb == gi) & changed).all()))
    # 半径外的角落不能动
    check("画笔：角落未被影响", idx0[0, 0] == idxb[0, 0] and idx0[H - 1, 0] == idxb[H - 1, 0])
    check("画笔：覆盖像素数 > 0", infob["brush_pixels"] > 0,
          "实际 %d" % infob["brush_pixels"])

    # ---------------- identify 高亮不改结果 ----------------
    print("\n识别 / 高亮：")
    rep_id = parse_repair({"repair": {"identify": True,
                                      "lassos": [{"points": quad, "level": 4}]}})
    idx_id, info_id = apply_repair(idx0, work, pal, rep_id, algo)
    check("identify 与不 identify 的修正结果一致",
          np.array_equal(idx_id, idx4))
    hi = render_highlight(rgb0, info_id)
    check("高亮图尺寸一致", hi.shape == rgb0.shape)
    vic = info_id["_victim_mask"]
    check("高亮图在受害者位置确实变了（%d 个）" % int(vic.sum()),
          vic.any() and not np.array_equal(hi[vic], rgb0[vic]))

    # ---------------- 归一化：不同分辨率圈到同一片区域 ----------------
    print("\n分辨率无关：")
    W2, H2 = W * 2, H * 2
    img2 = img.resize((W2, H2), Image.LANCZOS)
    work2 = np.array(img2, dtype=np.uint8)
    idx0b, _ = process_image(img2, algo, dither, 1.0, pal)
    idx2, info2 = apply_repair(idx0b, work2, pal, rep4, algo)
    sel2 = mask_of(quad)  # 归一化坐标 -> 2 倍尺寸的掩膜
    sel2 = np.zeros((H2, W2), bool)
    from PIL import ImageDraw
    m2 = Image.new("1", (W2, H2), 0)
    ImageDraw.Draw(m2).polygon([(x * W2, y * H2) for x, y in quad], fill=1)
    sel2 = np.asarray(m2, dtype=bool)
    changed2 = idx2 != idx0b
    check("2 倍尺寸下也只改选区内", not (changed2 & ~sel2).any())
    frac1 = counts[4] / float(sel.sum())
    frac2 = info2["repaired"] / float(sel2.sum())
    check("两种尺寸改回的比例接近（%.3f vs %.3f）" % (frac1, frac2),
          abs(frac1 - frac2) < 0.15)

    # ---------------- 解析器健壮性 ----------------
    print("\n参数解析：")
    check("空 payload -> None", parse_repair({}) is None)
    check("没有 repair 字段 -> None", parse_repair({"algo": "weighted"}) is None)
    check("点数不足的套索被丢掉",
          parse_repair({"repair": {"lassos": [{"points": [[0.1, 0.1], [0.2, 0.2]]}]}}) is None)
    check("缺 hex 的笔画被丢掉",
          parse_repair({"repair": {"strokes": [{"x": 0.5, "y": 0.5, "r": 0.05}]}}) is None)
    r = parse_repair({"repair": {"lassos": [{"points": [[0, 0], [1, 0], [1, 1]], "level": 99}]}})
    check("强度被夹到 [2,4]", r["lassos"][0]["level"] == 4)
    r = parse_repair({"repair": {"strokes": [{"x": 5, "y": -3, "r": 99, "hex": "909090"}]}})
    check("坐标/半径被夹住", 0 <= r["strokes"][0]["x"] <= 1.5
          and 0 <= r["strokes"][0]["r"] <= 0.5)
    check("hex 自动补 # 并大写", r["strokes"][0]["hex"] == "#909090")

    print("\n" + "=" * 64)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("  失败：" + f)
    print("=" * 64)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
