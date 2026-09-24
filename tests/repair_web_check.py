# -*- coding: utf-8 -*-
"""
局部噪点修正：浏览器端功能测试
==============================

用无头 Edge 打开页面，在真实 DOM 上验证：
  · 侧边栏出现了「局部噪点修正」区域，两个页签能切
  · 预览图上自动挂上了修正画布（.rp-canvas），尺寸跟图片一致
  · 调色板色块列表（画笔取色用）渲染出来，点一下能改笔刷颜色
  · 套索：模拟拖拽 -> 生成选区 -> 按 S 识别 -> 服务端返回受害者数量
  · 滑块改强度会重新请求，且强度 2→4 改回的方块数递增
  · 画笔：模拟涂抹 -> 服务端返回覆盖像素数
  · 修正只影响圈定/涂抹范围（对比范围外像素）

做法：注入一段探针脚本，把结果写进 <body data-rptest="...">，再 dump-dom 取出来。
探针里会把 EventSource 打桩（否则 SSE 长连接会让 --virtual-time-budget 永远等下去）。

用法（工作区根目录）：
    python tests/repair_web_check.py
"""
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(ROOT, "src")
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PORT = 8852

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  [√] " if ok else "  [×] ") + name + (("  " + detail) if detail and not ok else ""))


# 必须在**应用脚本之前**打桩 EventSource：
# keepaliveConnect() 在 99-start.js 里就跑掉了，等页面底部再打桩已经晚了 ——
# 已经打开的 SSE 长连接会让 --virtual-time-budget 永远等下去。
PROBE_HEAD = r"""
<script>
(function () {
  window.EventSource = function () {
    return { close: function () {}, addEventListener: function () {},
             set onmessage(v) {}, set onerror(v) {}, set onopen(v) {} };
  };
})();
</script>
"""

# 探针主体：先给页面加一个默认上传的测试图，再走完整流程
PROBE = r"""
<script>
(function () {
  const out = { steps: [], errors: [] };
  function rec(k, v) { out[k] = v; }
  window.__rp = out;

  function cvs() { return document.querySelector("#mp-preview-pal .rp-canvas"); }
  function pimg() { return document.querySelector("#mp-preview-pal img"); }
  function el(id) { return document.getElementById(id); }

  function mouse(target, type, x, y, extra) {
    const r = target.getBoundingClientRect();
    const ev = new MouseEvent(type, Object.assign({
      bubbles: true, cancelable: true, clientX: r.left + x, clientY: r.top + y,
      view: window,
    }, extra || {}));
    target.dispatchEvent(ev);
  }

  // 预览每次重绘都会换掉画布，所以一定要拿「当前」那块，并用它自己的 rect 换算坐标
  function settle() {
    return waitFor(() => {
      const box = el("mp-preview-pal");
      const c = cvs();
      return c && RP.canvas === c && !box.classList.contains("updating")
        && c.width > 2 && c.getBoundingClientRect().width > 2;
    }, 30000);
  }

  function dragOnCanvas(fn) {
    const c = RP.canvas;
    const r = c.getBoundingClientRect();
    return { c: c, r: r, mouse: (type, nx, ny, extra) =>
      mouse(c, type, r.width * nx, r.height * ny, extra) };
  }
  function wkey(k) {
    document.dispatchEvent(new KeyboardEvent("keydown",
      { key: k, bubbles: true, cancelable: true }));
  }

  function makeImage() {
    // 一张带渐变 + 大块近纯色的图，抖动一定会掺色
    const c = document.createElement("canvas");
    c.width = 200; c.height = 150;
    const g = c.getContext("2d");
    const grd = g.createLinearGradient(0, 0, 200, 150);
    grd.addColorStop(0, "#6b7c8a");
    grd.addColorStop(1, "#c8a06a");
    g.fillStyle = grd; g.fillRect(0, 0, 200, 150);
    g.fillStyle = "#767a7e"; g.fillRect(8, 8, 90, 70);
    return new Promise(res => c.toBlob(res, "image/png"));
  }

  async function upload() {
    const blob = await makeImage();
    const file = new File([blob], "rp-test.png", { type: "image/png" });
    const dt = new DataTransfer();
    dt.items.add(file);
    const input = el("mp-file");
    input.files = dt.files;
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function waitFor(fn, ms) {
    const t0 = Date.now();
    return new Promise((resolve, reject) => {
      (function loop() {
        let v = null;
        try { v = fn(); } catch (e) { v = null; }
        if (v) return resolve(v);
        if (Date.now() - t0 > (ms || 20000)) return reject(new Error("超时"));
        setTimeout(loop, 60);
      })();
    });
  }

  function previewStats() {
    // 注意：每次预览重绘都会换掉 <img> 元素，所以必须现取，不能用早先抓到的引用
    const img = pimg();
    if (!img || !img.naturalWidth) return { w: 0, h: 0, total: 0, colors: new Map() };
    const c = document.createElement("canvas");
    c.width = img.naturalWidth; c.height = img.naturalHeight;
    const g = c.getContext("2d");
    g.drawImage(img, 0, 0);
    const d = g.getImageData(0, 0, c.width, c.height).data;
    const uniq = new Map();
    let n = 0;
    for (let i = 0; i < d.length; i += 4) {
      const k = (d[i] << 16) | (d[i + 1] << 8) | d[i + 2];
      uniq.set(k, (uniq.get(k) || 0) + 1);
      n++;
    }
    return { w: c.width, h: c.height, total: n, colors: uniq };
  }

  function colorAt(nx, ny) {
    const img = pimg();
    const c = document.createElement("canvas");
    c.width = img.naturalWidth; c.height = img.naturalHeight;
    const g = c.getContext("2d"); g.drawImage(img, 0, 0);
    const d = g.getImageData(Math.floor(nx * c.width), Math.floor(ny * c.height), 1, 1).data;
    return "#" + [d[0], d[1], d[2]].map(v => v.toString(16).padStart(2, "0")).join("").toUpperCase();
  }

  (async function run() {
    try {
      document.documentElement.setAttribute("data-rp-ready", "0");

      // ---------- 1. UI 存在 ----------
      rec("hasSection", !!el("rp-sec"));
      rec("tabs", document.querySelectorAll(".rp-tab").length);
      rec("chipsBeforePalette", document.querySelectorAll("#rp-pal .rp-chip").length);
      rec("levelDesc", (el("rp-level-desc") || {}).textContent || "");

      // 页签切换
      const brushTab = document.querySelector('.rp-tab[data-rpmode="brush"]');
      brushTab.click();
      rec("brushPaneVisible", !el("rp-pane-brush").hidden);
      rec("lassoPaneHidden", el("rp-pane-lasso").hidden);
      document.querySelector('.rp-tab[data-rpmode="lasso"]').click();
      rec("lassoPaneVisible", !el("rp-pane-lasso").hidden);

      // ---------- 2. 上传 + 首次预览 ----------
      // 抖动默认是「无抖动」。没有抖动时每个像素本来就取最近的颜色，
      // 自然没有「受害者」——所以先打开 Floyd-Steinberg，这才是这个功能的使用场景。
      const beforeSrc = (pimg() || {}).src || "";
      el("mp-dither").value = "floyd";
      el("mp-dither").dispatchEvent(new Event("change", { bubbles: true }));

      await upload();
      await waitFor(() => pimg() && pimg().naturalWidth > 0, 30000);
      await waitFor(() => cvs(), 30000);
      await waitFor(() => pimg() && pimg().src !== beforeSrc && !el("mp-preview-pal").classList.contains("updating"), 30000);
      rec("canvasAttached", !!cvs());
      const img = pimg(), cv = cvs();
      rec("canvasMatchesImage",
          Math.abs(cv.clientWidth - img.clientWidth) <= 1 &&
          Math.abs(cv.clientHeight - img.clientHeight) <= 1);
      // 诊断：布局尺寸到底是多大
      const ir = img.getBoundingClientRect();
      rec("diag", {
        imgClient: [img.clientWidth, img.clientHeight],
        imgNatural: [img.naturalWidth, img.naturalHeight],
        imgRect: [Math.round(ir.width), Math.round(ir.height)],
        cvBacking: [cv.width, cv.height],
        cvClient: [cv.clientWidth, cv.clientHeight],
        wrapRect: (function () {
          const w = img.closest(".rp-wrap");
          if (!w) return null;
          const r = w.getBoundingClientRect();
          return [Math.round(r.width), Math.round(r.height)];
        })(),
        palGroups: (typeof PAL !== "undefined" && PAL.groups) ? PAL.groups.length : -1,
        palLoaded: (typeof PAL !== "undefined") ? !!PAL.loaded : null,
        rpPalChildren: el("rp-pal").children.length,
        dither: el("mp-dither").value,
        topColors: (function () {
          const a = [...previewStats().colors.entries()]
            .sort((x, y) => y[1] - x[1]).slice(0, 6)
            .map(([k, v]) => "#" + k.toString(16).padStart(6, "0") + "×" + v);
          return a;
        })(),
      });
      rec("baseColors", previewStats().colors.size);
      // 调色板加载完之后色块列表才有内容
      rec("chips", document.querySelectorAll("#rp-pal .rp-chip").length);

      // ---------- 3. 套索：拖一圈 ----------
      // 圈「渐变」那一片：平坦区域本来就只有一个颜色，没有受害者，
      // 抖动掺色只发生在渐变上 —— 这也正是这个功能要修的典型地方。
      const r = cv.getBoundingClientRect();
      const W = r.width, H = r.height;
      const pts = [[0.55, 0.50], [0.95, 0.52], [0.94, 0.94], [0.56, 0.92], [0.55, 0.50]];
      mouse(cv, "mousedown", pts[0][0] * W, pts[0][1] * H);
      for (const p of pts.slice(1)) mouse(cv, "mousemove", p[0] * W, p[1] * H);
      window.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
      await waitFor(() => RP.lassos.length > 0, 5000);
      rec("lassoCount", RP.lassos.length);
      rec("lassoPoints", RP.lassos[0] ? RP.lassos[0].points.length : 0);
      rec("lassoXY", RP.lassos[0] ? RP.lassos[0].points.slice(0, 3) : null);
      rec("rpPayload", (typeof rpPayload === "function") ? rpPayload() : null);

      // ---------- 4. 按 S 识别受害者 ----------
      wkey("s");
      await waitFor(() => RP.lastInfo && RP.lastInfo.identify, 30000);
      rec("identifyVictims", RP.lastInfo.victims);
      rec("identifyDominant", RP.lastInfo.dominant);
      rec("statAfterS", el("rp-stat").textContent.slice(0, 120));
      rec("identifyRequested", RP.identify);

      // 高亮图上受害者处应该是洋红
      const hs = previewStats();
      rec("highlightHasMagenta", hs.colors.has((255 << 16) | (0 << 8) | 200));

      // ---------- 5. 强度 2 -> 4 ----------
      el("rp-apply").click();
      await waitFor(() => RP.lastInfo && !RP.lastInfo.identify && RP.lastInfo.applied, 30000);
      const r2 = RP.lastInfo.repaired;
      const after2 = previewStats().colors.size;

      const setLevel = (lv) => {
        el("rp-level").value = String(lv);
        el("rp-level").dispatchEvent(new Event("input", { bubbles: true }));
      };
      RP.lastInfo = null;
      setLevel(4);
      await waitFor(() => RP.lastInfo && RP.lastInfo.repaired, 30000);
      const r4 = RP.lastInfo.repaired;
      const after4 = previewStats().colors.size;

      rec("repairedL2", r2);
      rec("repairedL4", r4);
      rec("monotone", r4 >= r2);
      rec("colorsL2", after2);
      rec("colorsL4", after4);
      rec("fewerColorsAfterRepair", after4 <= after2);
      rec("levelDesc4", el("rp-level-desc").textContent.slice(0, 20));

      // ---------- 6. 修正只在选区内 ----------
      // 选区在左上角，右下角应该完全没变
      const p1 = previewStats();
      rec("previewW", p1.w);
      const cornerKey = (() => {
        const c = document.createElement("canvas");
        c.width = img.naturalWidth; c.height = img.naturalHeight;
        const g = c.getContext("2d"); g.drawImage(img, 0, 0);
        const d = g.getImageData(c.width - 4, c.height - 4, 1, 1).data;
        return (d[0] << 16) | (d[1] << 8) | d[2];
      })();
      rec("cornerColorNow", cornerKey);

      // ---------- 7. 画笔 ----------
      const brush = document.querySelector('.rp-tab[data-rpmode="brush"]');
      brush.click();
      // 选一个和右下角当前色不同的调色板颜色
      const chips = [...document.querySelectorAll("#rp-pal .rp-chip")];
      let picked = null;
      for (const c of chips) {
        const hex = c.dataset.hex;
        const v = parseInt(hex.slice(1), 16);
        if (v !== cornerKey) { c.click(); picked = hex; break; }
      }
      rec("brushHex", RP.brushHex);
      rec("brushPicked", picked);

      const cv2 = cvs();
      const r2b = cv2.getBoundingClientRect();
      // 选一个在套索**外面**的点（套索是 x 0.55~0.95 / y 0.50~0.94），
      // 这样能验证「画笔确实按自己的坐标生效」，不会被套索的修正混淆
      const PX = 0.22, PY = 0.75;
      await settle();
      rec("paintSpotBefore", colorAt(PX, PY));
      rec("brushRadius", rpBrushRadius());
      rec("paintNorm", RP.strokes.length);

      const dg = dragOnCanvas();
      dg.mouse("mousedown", PX, PY);
      dg.mouse("mousemove", PX, PY);
      window.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
      rec("strokesAfterDrag", RP.strokes.length);
      rec("lastStroke", RP.strokes[RP.strokes.length - 1] || null);
      await waitFor(() => RP.lastInfo && RP.lastInfo.brush_pixels > 0, 30000);
      rec("brushPixels", RP.lastInfo.brush_pixels);
      rec("strokeCount", RP.strokes.length);
      await settle();
      rec("paintSpotAfter", colorAt(PX, PY));

      // ---------- 8. 清除 ----------
      el("rp-reset").click();
      await waitFor(() => RP.lastInfo === null || !RP.lastInfo.applied, 30000);
      rec("afterResetLassos", RP.lassos.length);
      rec("afterResetStrokes", RP.strokes.length);
      rec("afterResetColors", previewStats().colors.size);

      rec("ok", true);
    } catch (e) {
      out.errors.push(String(e && e.stack || e));
      rec("ok", false);
    }
    document.documentElement.setAttribute("data-rp-ready", "1");
    // base64 里没有引号和 &，放进属性最省事，不会和 HTML 转义打架
    document.documentElement.setAttribute(
      "data-rptest", btoa(unescape(encodeURIComponent(JSON.stringify(out)))));
  })();
})();
</script>
"""


def start_server():
    env = dict(os.environ, MAPART_PORT=str(PORT), PYTHONIOENCODING="utf-8",
               PYTHONPATH=SRC_DIR)
    p = subprocess.Popen([sys.executable, "-m", "maptool"], cwd=SRC_DIR, env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(300):
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/api/palette" % PORT, timeout=2).read()
            return p
        except Exception:
            time.sleep(0.15)
    raise RuntimeError("服务没起来")


def dump_dom(url, budget=60000, timeout=300):
    prof = tempfile.mkdtemp(prefix="edgeprof_")
    try:
        cmd = [EDGE, "--headless", "--no-sandbox", "--disable-gpu",
               "--disable-extensions", "--no-first-run", "--disable-sync",
               "--disable-background-networking",
               "--virtual-time-budget=%d" % budget,
               "--user-data-dir=" + prof, "--dump-dom", url]
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return p.stdout.decode("utf-8", "replace")
    finally:
        shutil.rmtree(prof, ignore_errors=True)


def main():
    if not os.path.isfile(EDGE):
        print("找不到 Edge：%s" % EDGE)
        return 2

    # 把探针注入到页面副本里，通过 /static 提供（web/ 目录内）
    web = os.path.join(SRC_DIR, "maptool", "web")
    probe_path = os.path.join(web, "_rp_probe.html")
    html = open(os.path.join(web, "index.html"), encoding="utf-8").read()
    html = html.replace("<head>", "<head>\n" + PROBE_HEAD, 1)
    html = html.replace("</body>", PROBE + "\n</body>", 1)
    # 探针页面里的相对地址需要绝对化：页面位于 /static/_rp_probe.html
    open(probe_path, "w", encoding="utf-8").write(html)

    proc = None
    try:
        proc = start_server()
        dom = dump_dom("http://127.0.0.1:%d/static/_rp_probe.html" % PORT)
        try:
            with open(os.path.join(ROOT, "tests", "_tmp", "rp_dom.html"),
                      "w", encoding="utf-8") as f:
                f.write(dom)
        except OSError:
            pass
        m = re.search(r'data-rptest="([A-Za-z0-9+/=]*)"', dom)
        if not m:
            print("页面上没有找到探针结果（data-rptest）。")
            print("页面可能没跑到最后一步，或者脚本报错了。DOM 片段：")
            print(dom[:2000])
            return 2
        out = json.loads(base64.b64decode(m.group(1)).decode("utf-8"))

        print("探针结果：")
        print(json.dumps(out, ensure_ascii=False, indent=1)[:3000])
        print()
        if out.get("errors"):
            print("页面报错：")
            for e in out["errors"]:
                print("   " + e[:600])
            print()

        check("侧边栏有「局部噪点修正」区域", out.get("hasSection") is True)
        check("两个页签（套索/画笔）", out.get("tabs") == 2)
        check("页签能切换", out.get("brushPaneVisible") and out.get("lassoPaneVisible"))
        check("调色板色块列表已渲染（%s 个）" % out.get("chips"),
              (out.get("chips") or 0) > 10)
        check("强度说明文字非空", bool(out.get("levelDesc")))
        check("预览图上挂上了修正画布", out.get("canvasAttached") is True)
        check("画布尺寸跟预览图一致", out.get("canvasMatchesImage") is True)
        check("套索拖拽产生了选区（%s 个点）" % out.get("lassoPoints"),
              (out.get("lassoCount") or 0) == 1 and (out.get("lassoPoints") or 0) >= 3)
        check("按 S 识别出受害者（%s 个，主色 %s）"
              % (out.get("identifyVictims"), out.get("identifyDominant")),
              (out.get("identifyVictims") or 0) > 0)
        check("识别时预览里有洋红高亮", out.get("highlightHasMagenta") is True)
        check("强度 4 改回数量 ≥ 强度 2（%s vs %s）"
              % (out.get("repairedL2"), out.get("repairedL4")),
              out.get("monotone") is True)
        check("修正后用色数不增加（%s -> %s）"
              % (out.get("colorsL2"), out.get("colorsL4")),
              out.get("fewerColorsAfterRepair") is True)
        check("强度 4 的说明文字跟着变", bool(out.get("levelDesc4")))
        check("选中了画笔颜色（%s）" % out.get("brushHex"), bool(out.get("brushHex")))
        check("画笔涂抹生效（覆盖 %s 个像素，%s 笔）"
              % (out.get("brushPixels"), out.get("strokeCount")),
              (out.get("brushPixels") or 0) > 0)
        check("画笔半径按成品方块数换算（%s）" % out.get("brushRadius"),
              (out.get("brushRadius") or 0) > 0.01)
        check("涂抹点被涂成了笔刷色（%s，涂前 %s，笔刷 %s）"
              % (out.get("paintSpotAfter"), out.get("paintSpotBefore"),
                 out.get("brushHex")),
              out.get("paintSpotAfter") == out.get("brushHex")
              and out.get("paintSpotBefore") != out.get("brushHex"))
        check("清除后选区/笔画都清空",
              out.get("afterResetLassos") == 0 and out.get("afterResetStrokes") == 0)
        check("清除后用色数回到基准（%s vs 基准 %s）"
              % (out.get("afterResetColors"), out.get("baseColors")),
              out.get("afterResetColors") == out.get("baseColors"))
        check("整段流程无异常", out.get("ok") is True
              and not out.get("errors"))
    finally:
        if proc:
            proc.kill()
        try:
            os.unlink(probe_path)
        except OSError:
            pass

    print()
    print("=" * 66)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("  失败：" + f)
    print("=" * 66)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
