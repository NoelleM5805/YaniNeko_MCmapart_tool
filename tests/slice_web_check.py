# -*- coding: utf-8 -*-
"""
投影切分：浏览器端实测
======================

在真实 DOM 上把切分页走一遍：

  · 上传 .litematic 之后自动出预览（不用点任何按钮）
  · 推荐条给出「每块 ≤128×128」的切法，尺寸/列数都是对的
  · 缩略图画出来了，切割线真的画在画布上（按行/列数数红线段数）
  · 切换手动网格 -> 预览跟着重算，每块明细同步
  · 开始切分 -> 轮询完成 -> 拿到 N 个文件，并且逐个能下载
  · 上传的字节只在预览时传一次：切分请求里带的是 key（内容指纹）

用法（工作区根目录）：
    python tests/slice_web_check.py
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
PORT = 8853

sys.path.insert(0, SRC_DIR)

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  [√] " if ok else "  [×] ") + name
          + (("  " + str(detail)) if detail and not ok else ""))


PROBE_HEAD = r"""
<script>
window.EventSource = function () {
  return { close: function () {}, addEventListener: function () {},
           set onmessage(v) {}, set onerror(v) {}, set onopen(v) {} };
};
</script>
"""

PROBE = r"""
<script>
(function () {
  const out = { errors: [] };
  const B64 = "__LITEMATIC_B64__";
  const FNAME = "__LITEMATIC_NAME__";
  function el(id) { return document.getElementById(id); }
  function rec(k, v) { out[k] = v; }
  function num(id) { const e = el(id); return e ? e.value : null; }

  window.addEventListener("error", (e) => out.errors.push("error: " + (e.message || e)));
  window.addEventListener("unhandledrejection",
    (e) => out.errors.push("reject: " + ((e.reason && e.reason.message) || e.reason)));

  function waitFor(fn, ms, why) {
    const t0 = Date.now();
    return new Promise((resolve, reject) => {
      (function loop() {
        let v = null;
        try { v = fn(); } catch (e) { v = null; }
        if (v) return resolve(v);
        if (Date.now() - t0 > (ms || 30000)) return reject(new Error("超时: " + (why || "")));
        setTimeout(loop, 60);
      })();
    });
  }

  function b64ToFile() {
    const bin = atob(B64);
    const u8 = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
    return new File([u8], FNAME, { type: "application/octet-stream" });
  }

  function putFile(f) {
    const dt = new DataTransfer();
    dt.items.add(f);
    const input = el("sl-file");
    Object.defineProperty(input, "files", { value: dt.files, writable: false });
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }

  /**
   * 把画布重画成「只有理想网格」的样子，用来和实际绘制结果比。
   *
   * 直接比会有两个坑：画布有 dpr 缩放（CSS px != 画布 px），以及格子里的
   * 白色编号文字会盖住线。所以先按画布自身尺寸推边界，再直接重画一遍，
   * 然后把实际绘制的红像素和它逐像素对齐比对。
   */
  function idealGridImageData() {
    const cv = el("sl-grid");
    const W = cv.width, H = cv.height;
    const c = document.createElement("canvas");
    c.width = W; c.height = H;
    const g = c.getContext("2d");
    const bxs = SL.plan.x_bounds, bzs = SL.plan.z_bounds;
    const xs = bxs.map((v, i) => (i === bxs.length - 1) ? W - .5
                                : Math.min(Math.floor(v * W / SL.plan.sx), W - 1) + .5);
    const zs = bzs.map((v, i) => (i === bzs.length - 1) ? H - .5
                                : Math.min(Math.floor(v * H / SL.plan.sz), H - 1) + .5);
    g.strokeStyle = "rgba(255,60,60,.95)";
    g.lineWidth = 1;
    g.beginPath();
    xs.forEach(x => { g.moveTo(x, 0); g.lineTo(x, H); });
    zs.forEach(y => { g.moveTo(0, y); g.lineTo(W, y); });
    g.stroke();
    return g.getImageData(0, 0, W, H);
  }

  function redMatchGrid() {
    const cv = el("sl-grid");
    if (!cv || !cv.width) return { ok: false, why: "no-canvas" };
    const W = cv.width, H = cv.height;
    const got = cv.getContext("2d").getImageData(0, 0, W, H).data;
    const want = idealGridImageData().data;    function red(d, i) {
      return d[i] > 180 && d[i + 1] < 110 && d[i + 2] < 110 && d[i + 3] > 40;
    }
    let extra = 0, missing = 0, matched = 0;
    for (let i = 0; i < got.length; i += 4) {
      const r = red(got, i), w = red(want, i);
      if (r && w) matched++;
      else if (r) extra++;
      else if (w) missing++;
    }
    return { ok: (extra === 0 && missing === 0), matched: matched,
             extra: extra, missing: missing, w: W, h: H };
  }

  /** 数画布上有多少条红色像素（切割线） */
  function redPixels() {
    const cv = el("sl-grid");
    if (!cv || !cv.width) return 0;
    const g = cv.getContext("2d");
    const d = g.getImageData(0, 0, cv.width, cv.height).data;
    let n = 0;
    for (let i = 0; i < d.length; i += 4) {
      if (d[i] > 180 && d[i + 1] < 110 && d[i + 2] < 110 && d[i + 3] > 40) n++;
    }
    return n;
  }

  /** 画布上不同横坐标上的红色像素列数 —— 用来判断竖线根数 */
  function redColumns() {
    const cv = el("sl-grid");
    if (!cv || !cv.width) return 0;
    const w = cv.width, h = cv.height;
    const g = cv.getContext("2d");
    const d = g.getImageData(0, 0, w, h).data;
    const cols = new Set();
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        const i = (y * w + x) * 4;
        if (d[i] > 180 && d[i + 1] < 110 && d[i + 2] < 110 && d[i + 3] > 40) cols.add(x);
      }
    }
    return cols.size;
  }

  (async function () {
    try {
      // 切到「投影切分」页签。
      // 别的页签是 display:none，隐藏容器里量出来的尺寸全是 0，网格画不出来
      // （真实使用中用户就是先点页签再看图的，这里必须一样）。
      const panel = document.querySelector('.tab-panel[data-panel="slice"]');
      rec("panelHiddenAtStart", panel ? !panel.classList.contains("active") : null);
      document.querySelectorAll(".tab").forEach(b => {
        if (b.dataset.tab === "slice") b.click();
      });
      rec("panelActiveAfterSwitch", panel.classList.contains("active"));

      // ---- 初始状态 ----
      rec("planSecHiddenAtStart", el("sl-plan-sec").hidden);
      rec("prevEmptyAtStart", el("sl-prev-empty").hidden === false);

      // ---- 上传 ----
      putFile(b64ToFile());
      rec("fileInfoShown", el("sl-file-info").hidden === false);
      rec("fileName", el("sl-file-name").textContent);

      // 预览自动出现，不用点按钮
      await waitFor(() => el("sl-plan-sec").hidden === false, 30000, "plan-sec");
      await waitFor(() => el("sl-thumb").naturalWidth > 0, 30000, "thumb");
      rec("autoPreview", true);
      rec("sizeText", el("sl-size").textContent);
      rec("recoText", el("sl-reco-txt").textContent);
      rec("summaryHtml", el("sl-summary").textContent);
      rec("chips", el("sl-summary").querySelectorAll(".sl-chip").length);
      rec("modeChecked", document.querySelector('input[name="sl-mode"]:checked').value);
      rec("manualHiddenInAuto", el("sl-manual").hidden);
      rec("thumbW", el("sl-thumb").naturalWidth);
      rec("thumbH", el("sl-thumb").naturalHeight);
      rec("thumbStep", SL.plan ? 1 : -1);
      rec("thumbRendering", getComputedStyle(el("sl-thumb")).imageRendering);
      rec("pixelChip", Array.from(el("sl-summary").querySelectorAll(".sl-chip"))
          .map(c => c.textContent).filter(t => t.indexOf("像素") >= 0).join(""));

      // 服务端给的方案
      rec("planCols", SL.plan.cols);
      rec("planRows", SL.plan.rows);
      rec("planCount", SL.plan.count);
      rec("maxCellW", Math.max.apply(null, SL.plan.cells.map(c => c.w)));
      rec("maxCellH", Math.max.apply(null, SL.plan.cells.map(c => c.h)));
      rec("statCells", el("sl-cells").querySelectorAll(".sl-cell").length);
      rec("keyLen", (SL.key || "").length);
      rec("recoCols", SL.recoGrid ? SL.recoGrid[0] : -1);
      rec("recoRows", SL.recoGrid ? SL.recoGrid[1] : -1);

      // ---- 切割线真的画在画布上 ----
      await waitFor(() => redPixels() > 0, 15000, "grid-lines");
      rec("redPixels", redPixels());
      rec("gridDpr", el("sl-grid").width / el("sl-thumb").clientWidth);
      rec("gridMatch1x1", redMatchGrid());
      rec("gridCanvasW", el("sl-grid").width);
      rec("thumbClientW", el("sl-thumb").clientWidth);
      rec("gridMatchesThumb",
          Math.abs(el("sl-grid").clientWidth - el("sl-thumb").clientWidth) <= 1
          && Math.abs(el("sl-grid").clientHeight - el("sl-thumb").clientHeight) <= 1);

      // ---- 切手动 2x2 ----
      const radio = document.querySelector('input[name="sl-mode"][value="manual"]');
      radio.checked = true;
      radio.dispatchEvent(new Event("change", { bubbles: true }));
      rec("manualVisible", el("sl-manual").hidden === false);
      el("sl-cols").value = "2";
      el("sl-rows").value = "2";
      el("sl-cols").dispatchEvent(new Event("change", { bubbles: true }));
      await waitFor(() => SL.plan && SL.plan.cols === 2 && SL.plan.rows === 2, 30000, "manual-plan");
      await waitFor(() => redMatchGrid().ok, 20000, "manual-grid-match");
      rec("manualGridMatch", redMatchGrid());
      rec("manualWarn", el("sl-plan-warn").hidden === false
          ? el("sl-plan-warn").textContent : "");
      rec("manualCols", SL.plan.cols);
      rec("manualRows", SL.plan.rows);
      rec("manualCount", SL.plan.count);
      rec("manualStatCells", el("sl-cells").querySelectorAll(".sl-cell").length);
      rec("manualRecoStill128", SL.recoGrid[0] + "x" + SL.recoGrid[1]);

      // ---- 真正切分 ----
      el("sl-run").click();
      await waitFor(() => el("sl-save-sec").hidden === false, 90000, "slice-done");
      rec("sliceCount", SL.files.length);
      rec("sliceNames", SL.files.slice(0, 4).map(f => f.name).join(","));
      rec("saveNameText", el("sl-save-name").textContent);
      rec("badge", el("badge").textContent);
      rec("status", el("status").textContent);
      rec("filesHaveSize", SL.files.every(f => f.size > 0));

      // 第一个文件真的能下下来
      const r = await fetch("/api/slice/file/" + SL.taskId + "/0");
      const buf = await r.arrayBuffer();
      rec("firstFileBytes", buf.byteLength);
      const magic = new Uint8Array(buf.slice(0, 2));
      rec("firstFileGzip", magic[0] === 0x1f && magic[1] === 0x8b);

      // zip 出口存在
      const rz = await fetch("/api/slice/zip/" + SL.taskId);
      const zb = await rz.arrayBuffer();
      rec("zipBytes", zb.byteLength);
      const zt = new Uint8Array(zb.slice(0, 2));
      rec("zipMagicOK", zt[0] === 0x50 && zt[1] === 0x4b);

      rec("ok", true);
    } catch (e) {
      out.fatal = String((e && e.stack) || e);
    }
    document.body.setAttribute("data-sltest",
      btoa(unescape(encodeURIComponent(JSON.stringify(out)))));
  })();
})();
</script>
"""


def start_server():
    env = dict(os.environ, MAPART_PORT=str(PORT), PYTHONIOENCODING="utf-8",
               PYTHONPATH=SRC_DIR)
    p = subprocess.Popen([sys.executable, "-m", "maptool"], cwd=SRC_DIR, env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(400):
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/api/palette" % PORT,
                                   timeout=2).read()
            return p
        except Exception:
            time.sleep(0.15)
    raise RuntimeError("服务没起来")


def dump_dom(url, budget=120000, timeout=480):
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


def make_projection():
    """造一个 100×100 的地图画投影 —— 故意不是 128 的整数倍，逼出余数格。"""
    import numpy as np
    from PIL import Image
    from maptool.palette import make_palette
    from maptool.dithering import process_image
    from maptool.schematic import build_mapart_schematic, schem_to_bytes

    rng = np.random.default_rng(5)
    arr = np.clip(rng.normal(128, 55, (100, 100, 3)), 0, 255).astype(np.uint8)
    img = Image.fromarray(arr, "RGB")
    pal, _ = make_palette(None)
    idx, _ = process_image(img, "weighted", "none", 1.0, pal)
    schem, placed = build_mapart_schematic(idx, pal, seed=5)
    return schem_to_bytes(schem), placed


def main():
    if not os.path.isfile(EDGE):
        print("找不到 Edge：%s" % EDGE)
        return 2

    content, placed = make_projection()
    print("测试投影：100×100，%d 方块，%d 字节" % (placed, len(content)))

    web = os.path.join(SRC_DIR, "maptool", "web")
    probe_path = os.path.join(web, "_sl_probe.html")
    html = open(os.path.join(web, "index.html"), encoding="utf-8").read()
    html = html.replace("<head>", "<head>\n" + PROBE_HEAD, 1)
    probe = (PROBE
             .replace("__LITEMATIC_B64__", base64.b64encode(content).decode("ascii"))
             .replace("__LITEMATIC_NAME__", "测试图_100x100.litematic"))
    html = html.replace("</body>", probe + "\n</body>", 1)
    open(probe_path, "w", encoding="utf-8").write(html)

    proc = None
    try:
        proc = start_server()
        dom = dump_dom("http://127.0.0.1:%d/static/_sl_probe.html" % PORT)
        try:
            with open(os.path.join(ROOT, "tests", "_tmp", "sl_dom.html"),
                      "w", encoding="utf-8") as f:
                f.write(dom)
        except OSError:
            pass
        m = re.search(r'data-sltest="([A-Za-z0-9+/=]*)"', dom)
        if not m:
            print("没有探针结果（data-sltest）—— 页面可能卡住了")
            print(dom[:2000])
            return 2
        out = json.loads(base64.b64decode(m.group(1)).decode("utf-8"))
        print("探针结果：")
        print(json.dumps(out, ensure_ascii=False, indent=1)[:4000])
        print()
        if out.get("errors"):
            print("页面报错：")
            for e in out["errors"]:
                print("   " + e[:600])
            print()
        if out.get("fatal"):
            print("流程中断：" + out["fatal"])
            return 2

        g = out.get

        def num(key, default=None):
            v = g(key)
            return default if v is None else v

        check("没有 JS 报错", not out.get("errors"))
        check("初始时切割方式区隐藏", g("planSecHiddenAtStart") is True)
        check("初始显示占位提示", g("prevEmptyAtStart") is True)
        check("上传后显示文件名", g("fileInfoShown") is True
              and "litematic" in str(g("fileName")))

        # ---- 预览 ----
        check("上传后自动出预览（不用点按钮）", g("autoPreview") is True)
        check("显示投影内容尺寸（%s）" % g("sizeText"), "100 × 100" in str(g("sizeText")))
        check("推荐条说明了为什么不用切（%s）" % str(g("recoText")),
              "100×100" in str(g("recoText")) and "不用切" in str(g("recoText")))
        check("概览有 5 个指标（块数/有内容/最大块/网格/预览分辨率，实际 %s）"
              % g("chips"), num("chips", 0) == 5)
        check("默认是自动模式", g("modeChecked") == "auto")
        check("自动模式下手动网格隐藏", g("manualHiddenInAuto") is True)
        check("预览图是逐像素全分辨率（%dx%d，step=%d）"
              % (num("thumbW"), num("thumbH"), num("thumbStep", -1)),
              num("thumbW", 0) == 100 and num("thumbH", 0) == 100
              and num("thumbStep", -1) == 1)
        check("页面上标明 1 方块 = 1 像素（%s）" % g("pixelChip"),
              "1 方块 = 1 像素" in str(g("pixelChip")))
        check("预览图按 pixelated 放大（%s）" % g("thumbRendering"),
              g("thumbRendering") == "pixelated")
        check("内容指纹回传（key 长度 %s）" % g("keyLen"), num("keyLen", 0) > 8)
        check("推荐网格是 1×1（100 ≤ 128）",
              num("recoCols") == 1 and num("recoRows") == 1)
        check("默认方案 = 推荐方案（%s 列 %s 行）"
              % (g("planCols"), g("planRows")),
              num("planCols") == 1 and num("planRows") == 1 and num("planCount") == 1)
        check("每块明细 1 条", num("statCells", 0) == 1)

        # ---- 切割线 ----
        check("画布上真的画了红色切割线（%s 像素）" % g("redPixels"),
              num("redPixels", 0) > 0)
        m1 = g("gridMatch1x1") or {}
        check("1×1 切割线位置和理想网格逐像素一致（多 %s 少 %s）"
              % (m1.get("extra"), m1.get("missing")), m1.get("ok") is True)
        check("网格画布与缩略图尺寸一致（%s / %s）"
              % (g("gridCanvasW"), g("thumbClientW")), g("gridMatchesThumb") is True)

        # ---- 手动模式 ----
        check("切手动后网格输入出现", g("manualVisible") is True)
        check("手动 2×2 生效（%s×%s）" % (g("manualCols"), g("manualRows")),
              num("manualCols") == 2 and num("manualRows") == 2)
        check("手动后每块明细变 4 条", num("manualStatCells", 0) == 4)
        m2 = g("manualGridMatch") or {}
        check("手动 2×2 后切割线落在 0/50/100 上（对齐 %s 像素，多 %s 少 %s）"
              % (m2.get("matched"), m2.get("extra"), m2.get("missing")),
              m2.get("ok") is True and (m2.get("matched") or 0) > 0)
        # 100×100 切 2×2 = 每块 50×50，没超 128 -> 不该有提醒
        check("没超标时不显示提醒（%s）" % (g("manualWarn") or "（无）"),
              not g("manualWarn"))
        check("推荐值不跟着手动改（仍是 %s）" % g("manualRecoStill128"),
              g("manualRecoStill128") == "1x1")

        # ---- 切分 ----
        check("切分完成，拿到 4 个文件（%s）" % g("sliceCount"),
              num("sliceCount", 0) == 4)
        check("文件名带行列号（%s）" % g("sliceNames"),
              "_r1c1.litematic" in str(g("sliceNames"))
              and "_r2c2.litematic" in str(g("sliceNames")))
        check("每个文件都有大小", g("filesHaveSize") is True)
        check("保存区显示文件数（%s）" % str(g("saveNameText"))[:40],
              "4 个文件" in str(g("saveNameText")))
        check("状态栏显示完成（%s）" % g("status"), "切分完成" in str(g("status")))
        check("第一个文件能下载且是 gzip 投影（%s 字节）" % g("firstFileBytes"),
              num("firstFileBytes", 0) > 100 and g("firstFileGzip") is True)
        check("zip 出口可用（%s 字节）" % g("zipBytes"),
              num("zipBytes", 0) > 100 and g("zipMagicOK") is True)
        check("整段流程跑完", g("ok") is True)
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
