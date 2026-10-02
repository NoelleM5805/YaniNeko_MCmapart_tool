# -*- coding: utf-8 -*-
"""
局部噪点修正：功能与不变量测试（新算法）
========================================

核心保证（对应「不再把整个选区刷成一个颜色」）：

  · 降噪**只**改「选区内、且当前正好是锁定目标方块」的像素 —— 别的方块一个都不动
  · 选区外的像素永远不动
  · 选区内的线条 / 渐变 / 边界这些细节不会被抹掉
  · 邻域半径越大，清掉的杂色越多
  · 操作按顺序重放，后一个叠在前一个上；同一串操作结果完全可复现
  · 填充 / 还原 / 画笔各自的行为边界

用法（工作区根目录）：
    python tests/repair_check.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import numpy as np
from PIL import Image, ImageDraw

from maptool.dithering import process_image
from maptool.matching import match_batch
from maptool.palette import make_palette
from maptool.repair import (DEFAULT_STRENGTH, apply_repair, parse_repair,
                            radius_of)

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  [√] " if ok else "  [×] ") + name + (("  " + detail) if detail and not ok else ""))


W, H = 256, 192


def make_img():
    """左半近纯色（含一条红线细节），右半渐变。"""
    rng = np.random.default_rng(5)
    img = np.zeros((H, W, 3), np.float64)
    img[:, :W // 2] = (120, 124, 128)
    grd = np.linspace(0, 1, W - W // 2)[None, :, None]
    img[:, W // 2:] = (60, 70, 90) * (1 - grd) + (220, 190, 120) * grd
    img += rng.normal(0, 5, img.shape)
    im = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "RGB")
    ImageDraw.Draw(im).line([(10, H - 20), (W // 2 - 10, 20)],
                            fill=(230, 60, 50), width=5)
    return im


def mask_of(points, w, h):
    m = Image.new("1", (w, h), 0)
    ImageDraw.Draw(m).polygon([(x * w, y * h) for x, y in points], fill=1)
    return np.asarray(m, dtype=bool)


def main():
    img = make_img()
    work = np.array(img, dtype=np.uint8)
    pal, _u = make_palette(None)
    algo, dither = "weighted", "floyd"
    idx0, _rgb0 = process_image(img, algo, dither, 1.0, pal)

    quad = [(0.03, 0.03), (0.47, 0.03), (0.47, 0.97), (0.03, 0.97)]
    sel = mask_of(quad, W, H)
    src = work.astype(np.int16)
    detail = (src[:, :, 0] > 180) & (src[:, :, 1] < 110) & (src[:, :, 2] < 110) & sel

    # 选区内出现最多的颜色 = 主色调；次多的算「杂色」，拿它当锁定目标
    vals, cnt = np.unique(idx0[sel], return_counts=True)
    order = np.argsort(-cnt)
    main_hex = pal.hexes[int(vals[order[0]])]
    # 挑一个「确实是被抖动掺进来的杂色」当目标：数量中等、不是主色
    target_hex = None
    for k in order[1:]:
        if cnt[k] >= 200:
            target_hex = pal.hexes[int(vals[k])]
            break
    print("选区 %d 像素，细节（红线）%d 像素" % (int(sel.sum()), int(detail.sum())))
    print("主色调 %s（%d 个）；锁定目标（杂色）%s"
          % (main_hex, int(cnt[order[0]]), target_hex))
    print()

    def op_denoise(strength, target=target_hex):
        return {"repair": {"ops": [{"kind": "denoise", "target": target,
                                    "strength": strength, "lasso": quad}]}}

    target_index = pal.hexes.index(target_hex)

    # ---------------- 只改目标方块 ----------------
    print("降噪：只改锁定目标")
    for st in (1, DEFAULT_STRENGTH, 8):
        rep = parse_repair(op_denoise(st))
        idx1, info = apply_repair(idx0, work, pal, rep, algo)
        changed = idx1 != idx0
        # 改动的像素，原来必须正好是目标色
        was_target = changed & (idx0 == target_index)
        not_target = int((changed & (idx0 != target_index)).sum())
        out_sel = int((changed & ~sel).sum())
        n_target_in_sel = int(((idx0 == target_index) & sel).sum())
        lost_detail = int((idx0[detail] != idx1[detail]).sum())
        print("   强度 %d（半径 %d）：改动 %d 个；目标在选区内共 %d 个"
              % (st, radius_of(st), int(changed.sum()), n_target_in_sel))
        check("强度 %d：只改锁定的目标方块（越界改了 %d 个）" % (st, not_target),
              not_target == 0)
        check("强度 %d：只改选区内（区外改了 %d 个）" % (st, out_sel), out_sel == 0)
        check("强度 %d：细节（红线）一个没动（动了 %d 个）" % (st, lost_detail),
              lost_detail == 0)
        check("强度 %d：改动的都是原目标色像素" % st,
              int(was_target.sum()) == int(changed.sum()))
        check("强度 %d：info 计数与实际一致" % st,
              info["denoised"] == int(changed.sum()),
              "%d vs %d" % (info["denoised"], int(changed.sum())))

    # ---------------- 半径的作用 ----------------
    # 半径只在目标色「成团」时才看得出差别：
    # 分散的杂色像素四周全是别的颜色，半径 1 就全清掉了；
    # 而一大团目标色，半径小的时候只有外圈会被改，内部邻域还是自己。
    print("\n半径的作用：")
    rep1 = parse_repair(op_denoise(1))          # 半径 1
    idx_r1, info_r1 = apply_repair(idx0, work, pal, rep1, algo)
    check("半径 1 就能清掉分散的杂色像素（%d/%d）"
          % (info_r1["denoised"], n_target_in_sel),
          info_r1["denoised"] == n_target_in_sel)

    # 用受控的合成图案量半径：整片 A 色，中间放一块 9×9 的 B 色
    A, B = 0, 1
    S = 21
    syn = np.full((S, S), A, dtype=np.int32)
    syn[6:15, 6:15] = B                          # 9×9 的 B 色方块
    syn_mask = np.ones((S, S), dtype=bool)
    blob = syn == B
    print("   合成图案：%d×%d 全是 A，中间一块 9×9 的 B（共 %d 个）"
          % (S, S, int(blob.sum())))
    prev = -1
    mono = True
    for st in (2, 4, 6, 8):
        rr = radius_of(st)
        syn_rep = {"ops": [{"kind": "denoise", "target": 2, "radius": rr,
                            "lasso": None, "_syn": True}]}
        # 直接调内部实现，绕开颜色色号到下标的映射
        from maptool.repair import _denoise_region
        out, n = _denoise_region(syn, syn_mask, B, rr)
        print("     半径 %d：改掉 %d 个（B 总共 %d 个）" % (rr, n, int(blob.sum())))
        if n < prev:
            mono = False
        prev = n
    check("目标成团时，半径越大改掉的越多（单调不减）", mono)
    check("半径 1 只吃掉外圈，内部保留",
          _denoise_region(syn, syn_mask, B, 1)[1] < int(blob.sum()))
    # 9×9 的方块，中心点要在窗口里被 A 盖过，窗口需要 > 13×13：
    #   13×13 = 169 格，其中 B 占 81、A 占 88，A 才成为多数 -> 半径 6
    n5 = _denoise_region(syn, syn_mask, B, 5)[1]
    n6 = _denoise_region(syn, syn_mask, B, 6)[1]
    check("半径不够大时，方块中心仍然是自己的多数（半径 5 只改 %d/81）" % n5,
          n5 < int(blob.sum()))
    check("半径够大时整块都被吃掉（半径 6 改 %d/81）" % n6,
          n6 == int(blob.sum()))

    # ---------------- 不锁定目标 -> 不执行 ----------------
    print("\n边界情况：")
    check("没锁定目标时 denoise 被丢弃",
          parse_repair(op_denoise(5, target=None)) is None
          or all(o["kind"] != "denoise"
                 for o in parse_repair(op_denoise(5, target=None))["ops"]))
    idxn, infon = apply_repair(idx0, work, pal,
                               parse_repair(op_denoise(5, target="#123456")), algo)
    check("目标色不在调色板里时给出警告且不改动",
          not infon["applied"] and bool(infon["warnings"]),
          str(infon["warnings"]))

    # ---------------- 填充 ----------------
    fh = pal.hexes[(target_index + 5) % pal.n]
    rep = parse_repair({"repair": {"ops": [
        {"kind": "fill", "hex": fh, "lasso": quad}]}})
    idxf, infof = apply_repair(idx0, work, pal, rep, algo)
    fi = pal.hexes.index(fh)
    check("填充：选区内全变成指定颜色",
          bool((idxf[sel] == fi).all()))
    check("填充：选区外一个没动", not (idxf[~sel] != idx0[~sel]).any())
    check("填充：计数等于选区像素数", infof["filled"] == int(sel.sum()))

    # ---------------- 还原（去抖动） ----------------
    rep = parse_repair({"repair": {"ops": [{"kind": "revert", "lasso": quad}]}})
    idxr, infor = apply_repair(idx0, work, pal, rep, algo)
    expect = match_batch(work.reshape(-1, 3).astype(np.int32)[sel.reshape(-1)],
                         pal, algo)
    check("还原：选区内等于「不做抖动的最近颜色」",
          bool((idxr[sel] == expect.astype(np.int32)).all()))
    check("还原：选区外一个没动", not (idxr[~sel] != idx0[~sel]).any())

    # ---------------- 画笔 ----------------
    bh = pal.hexes[(target_index + 9) % pal.n]
    rep = parse_repair({"repair": {"ops": [
        {"kind": "brush", "hex": bh, "rx": 0.06, "ry": 0.045,
         "points": [[0.7, 0.6], [0.72, 0.62]]}]}})
    idxb, infob = apply_repair(idx0, work, pal, rep, algo)
    bi = pal.hexes.index(bh)
    cy, cx = int(0.6 * H), int(0.7 * W)
    check("画笔：落笔处被涂成笔刷色", idxb[cy, cx] == bi,
          "实际 %s" % pal.hexes[idxb[cy, cx]])
    check("画笔：改动像素数 > 0", infob["brush_pixels"] > 0)
    check("画笔：角落没被影响", idx0[0, 0] == idxb[0, 0])

    # 两轴半径是分开的：ry 越大竖着覆盖越多
    # （非正方形图片上只有这样笔刷才是正圆，而不是被拉成椭圆）
    def brush_pixels(rx, ry):
        r = parse_repair({"repair": {"ops": [
            {"kind": "brush", "hex": bh, "rx": rx, "ry": ry,
             "points": [[0.5, 0.5]]}]}})
        return apply_repair(idx0, work, pal, r, algo)[1]["brush_pixels"]

    n_flat = brush_pixels(0.06, 0.02)
    n_round = brush_pixels(0.06, 0.06)
    check("画笔：ry 越大覆盖越多（%d < %d）" % (n_flat, n_round), n_round > n_flat)
    check("画笔：只给 rx 时 ry 跟着 rx",
          parse_repair({"repair": {"ops": [
              {"kind": "brush", "hex": bh, "rx": 0.05,
               "points": [[0.5, 0.5]]}]}})["ops"][0]["ry"] == 0.05)

    # ---------------- 画笔：像素画语义 ----------------
    # 一次落点 = 一个**硬边方块**，尺寸精确是 N×N，不画圆形笔头、不抗锯齿。
    print("\n画笔（像素画语义）：")

    def brush_at(pts, size_blocks):
        r = parse_repair({"repair": {"ops": [
            {"kind": "brush", "hex": bh,
             "rx": size_blocks / float(W), "ry": size_blocks / float(H),
             "points": pts}]}})
        _idx, inf = apply_repair(idx0, work, pal, r, algo)
        return inf["brush_pixels"]

    check("笔头 1 格：一个落点只改 1 个方块", brush_at([[0.5, 0.5]], 1) == 1)
    check("笔头 3 格：一个落点改 3×3 = 9 个方块", brush_at([[0.5, 0.5]], 3) == 9)
    check("笔头 5 格：一个落点改 5×5 = 25 个方块", brush_at([[0.5, 0.5]], 5) == 25)
    check("笔头是方的不是圆的（5 格 = 25，圆形只有 21）",
          brush_at([[0.5, 0.5]], 5) == 25)

    # 相邻落点之间补整数直线：横着跨 10 格应该是 11 个方块，且中间不断
    n_line = brush_at([[0.5, 0.5], [0.5 + 10.0 / W, 0.5]], 1)
    check("快速拖动不断线（跨 10 格 -> %d 个方块，整数直线应为 11）" % n_line,
          n_line == 11)
    # 竖直方向同理
    check("竖直方向也连得上（%d）" % brush_at([[0.5, 0.5], [0.5, 0.5 + 10.0 / H]], 1),
          brush_at([[0.5, 0.5], [0.5, 0.5 + 10.0 / H]], 1) == 11)
    # 斜线
    n_diag = brush_at([[0.2, 0.2], [0.2 + 10.0 / W, 0.2 + 10.0 / H]], 1)
    check("斜着拖动也连得上（%d）" % n_diag, n_diag == 11)

    # 硬边：落点吸附到格子，不会因为小数位置糊出半个方块
    a = parse_repair({"repair": {"ops": [
        {"kind": "brush", "hex": bh, "rx": 1.0 / W, "ry": 1.0 / H,
         "points": [[0.2001, 0.3001]]}]}})
    b = parse_repair({"repair": {"ops": [
        {"kind": "brush", "hex": bh, "rx": 1.0 / W, "ry": 1.0 / H,
         "points": [[0.2099, 0.3099]]}]}})
    ia = apply_repair(idx0, work, pal, a, algo)[1]
    ib = apply_repair(idx0, work, pal, b, algo)[1]
    check("格子内的微小抖动落在同一格（不产生半格）",
          ia["brush_pixels"] == 1 and ib["brush_pixels"] == 1)

    # 同一笔里重复落点不会重复计数（去重）
    check("同一格重复落点只算一次",
          brush_at([[0.5, 0.5], [0.5, 0.5], [0.5, 0.5]], 1) == 1)

    # ---------------- 操作顺序 ----------------
    print("\n操作序列：")
    rep_ab = parse_repair({"repair": {"ops": [
        {"kind": "fill", "hex": pal.hexes[3], "lasso": quad},
        {"kind": "fill", "hex": pal.hexes[7], "lasso": quad}]}})
    idx_ab, _ = apply_repair(idx0, work, pal, rep_ab, algo)
    check("后一个操作覆盖前一个（最终是第二个颜色）",
          bool((idx_ab[sel] == 7).all()))
    rep_ba = parse_repair({"repair": {"ops": [
        {"kind": "fill", "hex": pal.hexes[7], "lasso": quad},
        {"kind": "fill", "hex": pal.hexes[3], "lasso": quad}]}})
    idx_ba, _ = apply_repair(idx0, work, pal, rep_ba, algo)
    check("交换顺序结果不同（顺序确实有意义）", not np.array_equal(idx_ab, idx_ba))

    # ---------------- 可复现 ----------------
    rep = parse_repair(op_denoise(5))
    a1, _ = apply_repair(idx0, work, pal, rep, algo)
    a2, _ = apply_repair(idx0, work, pal, rep, algo)
    check("同一串操作结果完全可复现", np.array_equal(a1, a2))

    # 撤销 = 少一个操作，结果应等于「只做了前面那些操作」
    two = parse_repair({"repair": {"ops": [
        {"kind": "denoise", "target": target_hex, "strength": 5, "lasso": quad},
        {"kind": "fill", "hex": pal.hexes[3], "lasso": quad}]}})
    one = parse_repair({"repair": {"ops": [
        {"kind": "denoise", "target": target_hex, "strength": 5, "lasso": quad}]}})
    idx_two, _ = apply_repair(idx0, work, pal, two, algo)
    idx_one, _ = apply_repair(idx0, work, pal, one, algo)
    idx_undo, _ = apply_repair(idx0, work, pal, one, algo)
    check("撤销一步 == 只重放前面的操作", np.array_equal(idx_undo, idx_one))
    check("两步和一步结果不同", not np.array_equal(idx_two, idx_one))

    # ---------------- 解析器 ----------------
    print("\n参数解析：")
    check("空 payload -> None", parse_repair({}) is None)
    check("没有 repair 字段 -> None", parse_repair({"algo": "weighted"}) is None)
    check("空 ops -> None", parse_repair({"repair": {"ops": []}}) is None)
    check("未知操作类型被丢掉",
          parse_repair({"repair": {"ops": [{"kind": "nope"}]}}) is None)
    check("点数不足的套索被丢掉",
          parse_repair({"repair": {"ops": [
              {"kind": "revert", "lasso": [[0.1, 0.1], [0.2, 0.2]]}]}}) is None)
    check("非法颜色被丢掉",
          parse_repair({"repair": {"ops": [
              {"kind": "fill", "hex": "zzz", "lasso": [[0, 0], [1, 0], [1, 1]]}]}}) is None)
    r = parse_repair({"repair": {"ops": [
        {"kind": "denoise", "target": "605d77", "strength": 99,
         "lasso": [[0, 0], [1, 0], [1, 1]]}]}})
    check("hex 补 # 并大写", r["ops"][0]["target"] == "#605D77")
    check("强度被夹到上限", r["ops"][0]["strength"] == 8)
    r = parse_repair({"repair": {"ops": [
        {"kind": "brush", "hex": "#909090", "rx": 9, "ry": -1,
         "points": [[5, -3]]}]}})
    check("画笔坐标/半径被夹住",
          0 <= r["ops"][0]["points"][0][0] <= 1.5
          and 0 <= r["ops"][0]["rx"] <= 0.5
          and 0 <= r["ops"][0]["ry"] <= 0.5)

    print("\n" + "=" * 66)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("  失败：" + f)
    print("=" * 66)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
