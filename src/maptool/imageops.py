# -*- coding: utf-8 -*-
from PIL import Image, ImageFilter
import math



def flatten_image(img):
    if img.mode == "P": img = img.convert("RGBA")
    if img.mode in ("RGBA", "LA"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        return bg
    return img.convert("RGB")


def fit_image(img, tw, th, mode):
    if mode == "stretch":
        return img.resize((tw, th), Image.LANCZOS)
    iw, ih = img.size
    ta = tw / th; ia = iw / ih
    if mode == "fit":
        if ia > ta: nw, nh = tw, max(1, int(round(ih * tw / iw)))
        else: nh, nw = th, max(1, int(round(iw * th / ih)))
        r = img.resize((nw, nh), Image.LANCZOS)
        c = Image.new("RGB", (tw, th), (255, 255, 255))
        c.paste(r, ((tw-nw)//2, (th-nh)//2))
        return c
    else:
        if ia > ta: nh, nw = th, max(1, int(round(iw * th / ih)))
        else: nw, nh = tw, max(1, int(round(ih * tw / iw)))
        r = img.resize((nw, nh), Image.LANCZOS)
        left = (nw - tw) // 2; top = (nh - th) // 2
        return r.crop((left, top, left + tw, top + th))



# ============================================================
# 比例推荐
# ============================================================
def recommend_ratios(img_w, img_h, max_units=6):
    if img_w <= 0 or img_h <= 0: return []
    target = img_w / img_h
    lt = math.log(target)
    cands = []
    for x in range(1, max_units+1):
        for y in range(1, max_units+1):
            diff = abs(math.log(x / y) - lt)
            size_pen = (x*y) / (max_units*max_units) * 0.15
            cands.append((diff + size_pen, x, y, diff))
    cands.sort(key=lambda t: t[0])
    seen = set(); out = []
    for score, x, y, diff in cands:
        if (x, y) in seen: continue
        seen.add((x, y))
        out.append({"x": x, "y": y, "diff": diff,
                    "px_w": x*128, "px_h": y*128})
        if len(out) >= 6: break
    return out


