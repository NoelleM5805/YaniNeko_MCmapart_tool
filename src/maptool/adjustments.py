# -*- coding: utf-8 -*-
from PIL import Image, ImageFilter
import numpy as np



# ============================================================
# 图片调整（左侧边栏）
#   基础：曝光 / 对比 / 饱和 / 亮度
#   影调：高光 / 暗部
#   颜色：色温 / 色调
#   细节：锐化 / 清晰 / 色散
#   效果：暗角
# ============================================================
ADJUST_KEYS = ("exposure", "contrast", "saturation", "brightness",
               "highlights", "shadows", "temperature", "tint",
               "sharpen", "clarity", "dispersion", "vignette")

ADJUST_LABELS = {
    "exposure": "曝光",
    "contrast": "对比",
    "saturation": "饱和",
    "brightness": "亮度",
    "highlights": "高光",
    "shadows": "暗部",
    "temperature": "色温",
    "tint": "色调",
    "sharpen": "锐化",
    "clarity": "清晰",
    "dispersion": "色散",
    "vignette": "暗角",
}

# 这几个只有正向有意义（负值等于不做）
ADJUST_UNIPOLAR = ("sharpen", "clarity", "vignette")


def parse_adjust(payload):
    """从请求里取出调整参数并夹到合法范围。全为 0 时返回 None（跳过处理）。"""
    raw = payload.get("adjust") or {}
    if not isinstance(raw, dict):
        return None
    out = {}
    for k in ADJUST_KEYS:
        try:
            v = int(round(float(raw.get(k, 0))))
        except (TypeError, ValueError):
            v = 0
        v = max(-100, min(100, v))
        if k in ADJUST_UNIPOLAR and v < 0:
            v = 0
        out[k] = v
    if all(v == 0 for v in out.values()):
        return None
    return out


def _apply_dispersion(img, amount):
    """
    色散（镜头色差）：红/蓝通道以画面中心为原点做轻微径向位移，
    越靠边偏移越大，中间基本不动 —— 和真实镜头的横向色差一致。
    """
    n = float(amount) * 3.0                       # ±3 像素
    if abs(n) < 0.05:
        return img
    arr = np.asarray(img, dtype=np.float32) / 255.0
    h, w = arr.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    cx = max((w - 1) / 2.0, 1e-6)
    cy = max((h - 1) / 2.0, 1e-6)
    dx = (xx - cx) / cx
    dy = (yy - cy) / cy
    out = arr.copy()
    for ch, sign in ((0, 1.0), (2, -1.0)):        # 红往外、蓝往内（负值反过来）
        sx = np.clip(np.rint(xx + dx * n * sign).astype(np.int32), 0, w - 1)
        sy = np.clip(np.rint(yy + dy * n * sign).astype(np.int32), 0, h - 1)
        out[..., ch] = arr[sy, sx, ch]
    return Image.fromarray((np.clip(out, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8), "RGB")


def apply_image_adjust(img, adj):
    """
    在「已缩放成成品尺寸」的图上做调整。
    顺序：曝光 -> 亮度 -> 对比 -> 高光/暗部 -> 饱和 -> 色温/色调
          -> 清晰 -> 暗角 -> 色散 -> 锐化
    暗角 / 色散按成品画幅计算，所以必须先缩放再处理。
    """
    if not adj:
        return img

    exposure = adj.get("exposure", 0) / 100.0
    contrast = adj.get("contrast", 0) / 100.0
    saturation = adj.get("saturation", 0) / 100.0
    brightness = adj.get("brightness", 0) / 100.0
    highlights = adj.get("highlights", 0) / 100.0
    shadows = adj.get("shadows", 0) / 100.0
    temperature = adj.get("temperature", 0) / 100.0
    tint = adj.get("tint", 0) / 100.0
    sharpen = adj.get("sharpen", 0) / 100.0
    clarity = adj.get("clarity", 0) / 100.0
    dispersion = adj.get("dispersion", 0) / 100.0
    vignette = adj.get("vignette", 0) / 100.0

    base = img.convert("RGB")
    pixel_ops = (exposure, contrast, saturation, brightness, highlights, shadows,
                 temperature, tint, clarity, dispersion, vignette)

    if any(pixel_ops):
        a = np.asarray(base, dtype=np.float32) / 255.0

        # --- 基础 ---
        if exposure:
            a = a * (2.0 ** (exposure * 1.5))          # ±1.5 档
        if brightness:
            a = a + brightness * 0.5                   # ±50% 亮度偏移
        if contrast:
            a = (a - 0.5) * (1.0 + contrast) + 0.5     # ±100% 对比
        a = np.clip(a, 0.0, 1.0)

        # --- 影调：只作用在亮部 / 暗部 ---
        if highlights or shadows:
            lum = a[..., 0] * 0.299 + a[..., 1] * 0.587 + a[..., 2] * 0.114
            if highlights:
                w = np.clip((lum - 0.5) * 2.0, 0.0, 1.0)[..., None]
                a = a + highlights * 0.55 * w
            if shadows:
                w = np.clip((0.5 - lum) * 2.0, 0.0, 1.0)[..., None]
                a = a + shadows * 0.55 * w
            a = np.clip(a, 0.0, 1.0)

        # --- 饱和 ---
        if saturation:
            lum = (a[..., 0] * 0.299 + a[..., 1] * 0.587 + a[..., 2] * 0.114)[..., None]
            a = lum + (a - lum) * (1.0 + saturation)
            a = np.clip(a, 0.0, 1.0)

        # --- 色温 / 色调 ---
        if temperature:
            a = a.copy()
            a[..., 0] += temperature * 0.13
            a[..., 2] -= temperature * 0.13
        if tint:
            a = a.copy()
            a[..., 1] += tint * 0.10
            a[..., 0] -= tint * 0.05
            a[..., 2] -= tint * 0.05
        a = np.clip(a, 0.0, 1.0)

        # --- 清晰：中频局部对比 ---
        if clarity:
            cur = Image.fromarray((a * 255.0 + 0.5).astype(np.uint8), "RGB")
            blurred = np.asarray(cur.filter(ImageFilter.GaussianBlur(4)),
                                 dtype=np.float32) / 255.0
            a = np.clip(a + clarity * 0.9 * (a - blurred), 0.0, 1.0)

        # --- 暗角 ---
        if vignette:
            h, w = a.shape[:2]
            yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
            cx = max((w - 1) / 2.0, 1e-6)
            cy = max((h - 1) / 2.0, 1e-6)
            r = np.sqrt(((xx - cx) / cx) ** 2 + ((yy - cy) / cy) ** 2)
            falloff = np.clip((r - 0.35) / 0.75, 0.0, 1.0) ** 1.4
            a = a * (1.0 - vignette * falloff)[:, :, None]

        img = Image.fromarray((np.clip(a, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8), "RGB")

        # --- 色散 ---
        if dispersion:
            img = _apply_dispersion(img, dispersion)
    else:
        img = base

    if sharpen:
        img = img.filter(ImageFilter.UnsharpMask(
            radius=1.6, percent=int(round(sharpen * 250)), threshold=0))
    return img


