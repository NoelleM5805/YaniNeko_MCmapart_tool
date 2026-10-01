# -*- coding: utf-8 -*-
"""
解耦前后等价性回归测试
======================

同时启动两个服务：
  · 旧版单文件   MCmapart_tool/mapart_toolkit_server.py
  · 新版包       src/maptool  (python -m maptool)

然后对两边跑同一套请求，逐项比对响应，确保解耦没有改变行为。

用法（在工作区根目录）：
    python tests/regression_check.py
"""
import base64
import gzip
import io
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(ROOT, "src")


def _legacy(*parts):
    """旧单文件版在 legacy/ 下（也兼容还没归档时的位置）。"""
    for base in (os.path.join(ROOT, "legacy", "MCmapart_tool"),
                 os.path.join(ROOT, "MCmapart_tool")):
        p = os.path.join(base, *parts)
        if os.path.exists(p):
            return p
    return os.path.join(ROOT, "legacy", "MCmapart_tool", *parts)


OLD_CWD = os.path.dirname(_legacy("mapart_toolkit_server.py"))
OLD_SCRIPT = _legacy("mapart_toolkit_server.py")

OLD_PORT = 8801
NEW_PORT = 8802

TMP = os.path.join(ROOT, "tests", "_tmp")   # 测试产出的临时目录，可随时删
os.makedirs(TMP, exist_ok=True)

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  [√] " if ok else "  [×] ") + name + (("  " + detail) if detail and not ok else ""))


# ----------------------------------------------------------------- 工具
def req(url, data=None, headers=None, method=None, timeout=600):
    r = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def jreq(url, obj=None, timeout=600):
    h = {"Content-Type": "application/json"}
    body = json.dumps(obj).encode() if obj is not None else None
    st, raw = req(url, body, h, timeout=timeout)
    try:
        return st, json.loads(raw.decode("utf-8"))
    except Exception:
        return st, {"_raw": raw[:200].decode("utf-8", "replace")}


def multipart(fields, files):
    b = "----boundary" + uuid.uuid4().hex
    out = io.BytesIO()
    for k, v in fields.items():
        out.write(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
                   % (b, k, v)).encode("utf-8"))
    for k, (fn, data, ct) in files.items():
        out.write(("--%s\r\nContent-Disposition: form-data; name=\"%s\"; filename=\"%s\"\r\n"
                   "Content-Type: %s\r\n\r\n" % (b, k, fn, ct)).encode("utf-8"))
        out.write(data)
        out.write(b"\r\n")
    out.write(("--%s--\r\n" % b).encode())
    return out.getvalue(), {"Content-Type": "multipart/form-data; boundary=" + b}


def _strip_nbt_times(blob):
    """
    清掉 NBT 里的 TimeCreated / TimeModified。

    这两个字段是 litemapy 保存时写入的毫秒时间戳，同一个进程里连做两次
    都不一样，属于容器自带的噪声，跟解耦无关。比对前先归零。
    布局：01 字节 tag 类型(0x04=long) + 2 字节名字长度 + 名字 + 8 字节值。
    """
    if not isinstance(blob, (bytes, bytearray)):
        return blob
    out = bytearray(blob)
    for tag, name in ((b"\x04", b"TimeCreated"), (b"\x04", b"TimeModified")):
        key = tag + len(name).to_bytes(2, "big") + name
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


def canon_litematic(b):
    """投影文件是压缩容器，容器里的时间戳会变；只比对解压后的内容。"""
    if b[:2] == b"PK":
        z = zipfile.ZipFile(io.BytesIO(b))
        return {i.filename: _strip_nbt_times(z.read(i.filename))
                for i in sorted(z.infolist(), key=lambda x: x.filename)}
    if b[:2] == b"\x1f\x8b":
        return _strip_nbt_times(gzip.decompress(b))
    return _strip_nbt_times(b)


def wait_task(base, tid, limit=600):
    t0 = time.time()
    while time.time() - t0 < limit:
        st, js = jreq("%s/api/status/%s" % (base, tid))
        if js.get("done"):
            return js
        time.sleep(0.3)
    raise RuntimeError("任务超时 " + tid)


# ----------------------------------------------------------------- 启动
def start_servers():
    env_old = dict(os.environ, MAPART_PORT=str(OLD_PORT), PYTHONIOENCODING="utf-8")
    env_new = dict(os.environ, MAPART_PORT=str(NEW_PORT), PYTHONIOENCODING="utf-8",
                   PYTHONPATH=SRC_DIR)
    old = subprocess.Popen([sys.executable, OLD_SCRIPT], cwd=OLD_CWD, env=env_old,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    new = subprocess.Popen([sys.executable, "-m", "maptool"], cwd=SRC_DIR, env=env_new,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for port, name in ((OLD_PORT, "旧版"), (NEW_PORT, "新版")):
        ok = False
        for _ in range(200):
            try:
                st, _b = req("http://127.0.0.1:%d/api/palette" % port, timeout=2)
                if st == 200:
                    ok = True
                    break
            except Exception:
                time.sleep(0.15)
        if not ok:
            raise RuntimeError("%s 服务没起来 (端口 %d)" % (name, port))
    return old, new


# ----------------------------------------------------------------- 测试图
def make_test_image(path):
    """造一张有渐变 / 色块 / 噪声的「类照片」测试图，确定性的。"""
    import numpy as np
    from PIL import Image
    rng = np.random.default_rng(7)
    w, h = 320, 200
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    r = 40 + 180 * (xx / w)
    g = 30 + 200 * (yy / h)
    b = 90 + 120 * np.sin((xx + yy) / 40.0)
    img = np.stack([r, g, b], axis=-1)
    img[20:80, 30:110] = (230, 60, 40)
    img[100:180, 180:300] = (30, 90, 220)
    img[90:120, :] *= 0.4
    img += rng.normal(0, 9, img.shape)
    arr = np.clip(img, 0, 255).astype(np.uint8)
    Image.fromarray(arr, "RGB").save(path)
    return path


BATTERY_PREVIEW = [
    dict(algo="weighted", dither="none", strength=100, size_mode="grid", grid_x=3, grid_y=3,
         fit_mode="stretch"),
    dict(algo="euclidean", dither="none", strength=100, size_mode="max", max_size=120,
         fit_mode="contain"),
    dict(algo="redmean", dither="floyd", strength=80, size_mode="grid",
         grid_x=2, grid_y=2, fit_mode="cover"),
    dict(algo="cie94", dither="bayer8", strength=60, size_mode="grid", grid_x=2, grid_y=2,
         fit_mode="stretch"),
    dict(algo="ciede2000", dither="sierra_lite", strength=100, size_mode="grid",
         grid_x=2, grid_y=1, fit_mode="stretch"),
    dict(algo="weighted", dither="none", strength=100, size_mode="grid", grid_x=2, grid_y=2,
         fit_mode="stretch", exposure=20, contrast=-15, saturation=30, vignette=40,
         dispersion=25, tint=10),
]


def battery(base, img_path, sel_some):
    """
    在某个服务上跑一整套请求，返回可比对的规范化结果。

    sel_some 是**调用方给定**的方块选择（从旧版调色板里挑出来的），两边用同一份 ——
    调色板后来新增了方块（砂轮 / 冰），如果各自拿自己的调色板去挑，选项就不一样了，
    比出来的差异是调色板差异而不是代码差异。
    """
    out = {}

    st, js = jreq(base + "/api/palette")
    ji = js.get("icon", {})
    ji.pop("url", None)
    out["palette"] = {"status": st, "groups": js.get("groups"), "icon": ji,
                      "defaults": js.get("defaults"),
                      "total_groups": js.get("total_groups"),
                      "total_blocks": js.get("total_blocks")}

    st, raw = req(base + "/api/icons.png")
    out["icons"] = {"status": st, "len": len(raw), "sha": hashlib_sha(raw)}

    with open(img_path, "rb") as f:
        data = f.read()
    body, hdr = multipart({}, {"file": ("test.png", data, "image/png")})
    st, js = req(base + "/api/mapart/upload", body, hdr)
    up = json.loads(js.decode("utf-8"))
    out["upload"] = {"status": st, "ok": up.get("ok"), "size": up.get("size"),
                     "w": up.get("width"), "h": up.get("height")}
    sid = up.get("sid")

    # 显式调色板（两边同一份）+ 不传 blocks（各用各自的服务端默认，不参与比对）
    # 新版预览响应多了一个 repair 字段（局部噪点修正）；这一轮没带 repair 参数，
    # 它必然是 None，摘出来单独断言，不然会跟旧版比出「新版多出字段」。
    repair_seen = []
    for i, p in enumerate(BATTERY_PREVIEW):
        for tag, sel in (("sel", sel_some),):
            payload = dict(p)
            payload["sid"] = sid
            payload["blocks"] = sel
            st, js = jreq(base + "/api/mapart/preview", payload)
            if isinstance(js, dict):
                # 记录 repair 字段的情况：成功的响应必须是 repair=None，
                # 失败/提前返回的响应（比如参数不合法）不带这个字段也正常。
                repair_seen.append({
                    "case": i,
                    "ok": bool(js.get("ok")),
                    "has_key": "repair" in js,
                    "val": js.pop("repair", None),
                })
            out["preview_%d_%s" % (i, tag)] = {"status": st, "body": js}
    out["_repair_field"] = repair_seen

    # 生成 + 下载
    st, js = jreq(base + "/api/mapart/generate",
                  dict(BATTERY_PREVIEW[0], sid=sid, filename="测试图 v2!.png",
                       alloc="cycle", blocks=sel_some))
    if js.get("ok"):
        stt = wait_task(base, js["task_id"])
        out["generate"] = {"status": st, "result": stt.get("result"),
                           "error": stt.get("error")}
        st2, raw = req(base + "/api/download/" + js["task_id"])
        out["generate"]["dl_status"] = st2
        out["generate"]["dl"] = canon_litematic(raw)
        out["generate"]["dl_canon_len"] = canon_len(out["generate"]["dl"])
        st3, raw3 = req(base + "/api/result-image/" + js["task_id"])
        out["generate"]["img_status"] = st3
        out["generate"]["img_sha"] = hashlib_sha(raw3)
        litem = raw
    else:
        out["generate"] = {"status": st, "body": js}
        litem = None

    # 切分
    if litem:
        body, hdr = multipart({"cols": "2", "rows": "3", "filename": "测试图 v2"},
                              {"file": ("mapart.litematic", litem, "application/octet-stream")})
        st, raw = req(base + "/api/slice/process", body, hdr)
        js = json.loads(raw.decode("utf-8"))
        stt = wait_task(base, js["task_id"])
        res = stt.get("result") or {}
        files = []
        for i in range(res.get("count", 0)):
            s2, b2 = req("%s/api/slice/file/%s/%d" % (base, js["task_id"], i))
            c = canon_litematic(b2)
            files.append({"status": s2, "canon_len": canon_len(c), "body": c})
            # 服务端报的 size 是压缩后的字节数，gzip 里带着时间戳，
            # 同一个进程连做两次都能差 1~2 字节（见 tests/slice_byte_noise.py）。
            # 换成规范化后的体积再比，否则比的是噪声。
            try:
                res["files"][i]["size"] = canon_len(c)
            except Exception:
                pass
        res["size"] = sum(f["canon_len"] for f in files)
        out["slice"] = {"status": st, "result": res, "files": files,
                        "error": stt.get("error")}

    # lichen
    if litem:
        body, hdr = multipart({"faces": json.dumps({"down": True, "up": False, "north": True}),
                               "waterlogged": "true"},
                              {"file": ("mapart.litematic", litem, "application/octet-stream")})
        st, raw = req(base + "/api/lichen/process", body, hdr)
        js = json.loads(raw.decode("utf-8"))
        stt = wait_task(base, js["task_id"])
        s2, b2 = req(base + "/api/download/" + js["task_id"])
        out["lichen"] = {"status": st, "result": stt.get("result"),
                         "error": stt.get("error"),
                         "dl": canon_litematic(b2)}
        out["lichen"]["canon_len"] = canon_len(out["lichen"]["dl"])
    return out


def hashlib_sha(b):
    import hashlib
    return hashlib.sha256(b).hexdigest()[:16]


def canon_len(v):
    """规范化之后的体积，避免被压缩/时间戳的字节抖动干扰。"""
    if isinstance(v, dict):
        return sum(canon_len(x) for x in v.values())
    if isinstance(v, (bytes, bytearray)):
        return len(v)
    return 0


def compare(a, b, path=""):
    """递归比对，收集第一层不同的路径。"""
    diffs = []
    if type(a) is not type(b):
        return [path + " 类型 %s != %s" % (type(a).__name__, type(b).__name__)]
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a:
                diffs.append(path + "." + k + " 新版多出")
            elif k not in b:
                diffs.append(path + "." + k + " 新版缺失")
            else:
                diffs += compare(a[k], b[k], path + "." + k)
    elif isinstance(a, list):
        if len(a) != len(b):
            diffs.append(path + " 长度 %d != %d" % (len(a), len(b)))
        else:
            for i, (x, y) in enumerate(zip(a, b)):
                diffs += compare(x, y, path + "[%d]" % i)
    else:
        if a != b:
            sa, sb = str(a), str(b)
            if len(sa) > 60:
                sa = sa[:60] + "…(%d)" % len(str(a))
            if len(sb) > 60:
                sb = sb[:60] + "…(%d)" % len(str(b))
            diffs.append("%s  %s != %s" % (path, sa, sb))
    return diffs


# ---------------------------------------------------------------------------
# 调色板相对旧版**有意**新增的方块。
# 除此之外调色板必须和旧版完全一致，否则算回归。
# 注意：`_build_palette_groups` 组内按中文名排序、默认项 = 第一个，
# 所以新增方块会让所在颜色组的默认项跟着变 —— 这是已知且接受的副作用，
# 下面单独把这两处列出来，其它位置仍然严格比对。
# ---------------------------------------------------------------------------
ADDED_BLOCKS = {
    "#909090": [("minecraft:grindstone", "砂轮")],
    "#8A8ADC": [("minecraft:ice", "冰")],
}
# 新增方块后默认项会变的颜色组
DEFAULT_CHANGED = {
    "#909090": ("minecraft:lodestone", "minecraft:grindstone"),
    "#8A8ADC": ("minecraft:packed_ice", "minecraft:ice"),
}


def _strip_added(pal):
    """
    把有意新增的方块从调色板里摘掉，剩下的必须和旧版一致。

    同时丢掉 cx/cy（图标在贴图集里的格子坐标）：新增 2 个方块后，贴图集
    从 256×288 变成 256×304，每个方块所处的格子必然整体重排，坐标没法比。
    坐标的正确性由 tools/check_icons.py 单独保证 —— 它会逐格比对像素，
    确认每个格子正好就是该方块应有的贴图（当前 289/289 全部一致）。
    """
    groups = []
    for g in pal.get("groups") or []:
        g = dict(g)
        drop = {bid for bid, _lbl in ADDED_BLOCKS.get(g["hex"], [])}
        blocks = []
        for b in g["blocks"]:
            if b["id"] in drop:
                continue
            b = dict(b)
            b.pop("cx", None)
            b.pop("cy", None)
            blocks.append(b)
        g["blocks"] = blocks
        groups.append(g)
    out = dict(pal)
    out["groups"] = groups
    out.pop("total_blocks", None)
    out.pop("defaults", None)
    out.pop("icon", None)       # 贴图集元信息由下面的 compare_palette 单独比
    return out


def compare_palette(a, b):
    """旧版 vs 新版调色板：允许上面登记的差异，其余严格比。"""
    diffs = compare(_strip_added(a), _strip_added(b), "palette")
    diffs += compare(a.get("total_groups"), b.get("total_groups"), "palette.total_groups")

    n_new = sum(len(v) for v in ADDED_BLOCKS.values())
    if b.get("total_blocks") != (a.get("total_blocks") or 0) + n_new:
        diffs.append("palette.total_blocks 应为 %d，实际 %s"
                     % ((a.get("total_blocks") or 0) + n_new, b.get("total_blocks")))

    # 图标贴图集：格子数变了（新增方块），只比格子尺寸和列数
    ia, ib = a.get("icon") or {}, b.get("icon") or {}
    for k in ("size", "cols"):
        if ia.get(k) != ib.get(k):
            diffs.append("palette.icon.%s %s != %s" % (k, ia.get(k), ib.get(k)))
    if ib.get("rows") != (ia.get("rows") or 0) + 1:
        diffs.append("palette.icon.rows 应为 %s，实际 %s"
                     % ((ia.get("rows") or 0) + 1, ib.get("rows")))

    # 默认项：只允许登记过的那两组变化
    da = {g["hex"]: g["blocks"][0]["id"] for g in a.get("groups") or [] if g["blocks"]}
    db = {g["hex"]: g["blocks"][0]["id"] for g in b.get("groups") or [] if g["blocks"]}
    for hexv in sorted(set(da) | set(db)):
        if da.get(hexv) == db.get(hexv):
            continue
        want = DEFAULT_CHANGED.get(hexv)
        if want and want == (da.get(hexv), db.get(hexv)):
            continue
        diffs.append("palette.defaults[%s] %s != %s" % (hexv, da.get(hexv), db.get(hexv)))
    return diffs


def compare_icons(a, b):
    """图标贴图集内容会随新增方块而变，这里只要求都取得到、且新版更大。"""
    if a.get("status") != 200 or b.get("status") != 200:
        return ["icons 状态 %s / %s" % (a.get("status"), b.get("status"))]
    if b.get("len", 0) <= a.get("len", 0):
        return ["icons 字节数没有随新增方块变大：%s -> %s" % (a.get("len"), b.get("len"))]
    return []


def shared_selection(base):
    """
    从「基准服务」的调色板里挑一份方块选择，两边共用。

    调色板后来有意新增了方块（砂轮 / 冰），如果各自拿自己的调色板去挑，
    选项就不一样了，比出来的差异是调色板差异而不是代码差异。
    """
    _st, pal = jreq(base + "/api/palette")
    return [g["blocks"][0]["id"] for g in (pal.get("groups") or [])[::3]]


def compare_any(key, a, b):
    """按 key 选合适的比对方式（调色板 / 图标要放行有意变更的那部分）。"""
    if key == "palette":
        return compare_palette(a, b)
    if key == "icons":
        return compare_icons(a, b)
    return compare(a, b, key)


def main():
    img = make_test_image(os.path.join(TMP, "test.png"))
    old_proc = new_proc = None
    try:
        print("启动两个服务…")
        old_proc, new_proc = start_servers()
        old_base = "http://127.0.0.1:%d" % OLD_PORT
        new_base = "http://127.0.0.1:%d" % NEW_PORT

        # 两边共用同一份方块选择（取自旧版调色板；这些 ID 新版也都还在）
        sel = shared_selection(old_base)
        print("共用方块选择：%d 个" % len(sel))

        print("跑旧版测试集…")
        a = battery(old_base, img, sel)
        print("跑新版测试集…")
        b = battery(new_base, img, sel)

        print("\n比对结果：")
        for k in sorted(set(a) | set(b)):
            if k.startswith("_"):
                continue
            d = compare_any(k, a.get(k), b.get(k))
            check(k, not d, "; ".join(d[:6]))

        # 没带 repair 参数时：旧版一定没有 repair 字段；新版凡是成功的响应，
        # repair 必须是 None（不能凭空产生修正）。失败的响应不带这个字段是正常的。
        old_rows = a.get("_repair_field") or []
        new_rows = b.get("_repair_field") or []
        old_clean = all(not r["has_key"] for r in old_rows)
        new_clean = all((r["ok"] and r["has_key"] and r["val"] is None)
                        or (not r["ok"] and not r["has_key"])
                        for r in new_rows)
        n_ok = sum(1 for r in new_rows if r["ok"])
        check("不带 repair 参数时 repair 恒为 None（%d/%d 个成功响应）"
              % (n_ok, len(new_rows)),
              old_clean and new_clean,
              "旧 %s / 新 %s" % (old_rows, new_rows))
    finally:
        for p in (old_proc, new_proc):
            if p:
                p.kill()

    print("\n" + "=" * 60)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败：" + ", ".join(FAIL))
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
