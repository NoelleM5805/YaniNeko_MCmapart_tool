# -*- coding: utf-8 -*-
"""
局部噪点修正 + 全局撤回：浏览器端实测
======================================

在真实 DOM 上把整套流程走一遍：

  隐藏原图关闭时
    · 左侧不显示「局部噪点修正」区域
    · 预览图上没有覆盖画布，点击预览图会弹出放大框（和原来一样）

  打开隐藏原图之后
    · 区域出现，预览图上挂上覆盖画布
    · 没选受害者方块时圈选区会被拒绝
    · 选好受害者 -> 圈选区 -> 此时**不做任何修改**（预览图不变）
    · 选区里受害者像素被本地高亮成洋红
    · 点「执行降噪」才真正修改（只改锁定的那种方块）
    · 缩放 / 平移时覆盖层跟着预览图一起变换
    · 撤回 / 重做能回到前后状态，最多 20 步

做法：注入探针把结果写进 data-rptest（base64），再 dump-dom 取出来。
必须在 <head> 里就把 EventSource 打桩，否则 SSE 会让 --virtual-time-budget 永远等。

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
  function el(id) { return document.getElementById(id); }
  function rec(k, v) { out[k] = v; }
  function cvs() {
    // 现在有两张画布：.rp-paint（已画像素）和选区覆盖层。这里要的是覆盖层。
    return document.querySelector("#mp-preview-pal canvas.rp-canvas:not(.rp-paint)");
  }
  function pimg() {
    return document.querySelector("#mp-preview-pal img, #mp-preview-pal canvas#mp-preview-canvas");
  }
  function pw(el) { return (el && (el.naturalWidth || el.width)) || 0; }
  function ph(el) { return (el && (el.naturalHeight || el.height)) || 0; }

  function mouse(target, type, cx, cy, extra) {
    const r = target.getBoundingClientRect();
    target.dispatchEvent(new MouseEvent(type, Object.assign({
      bubbles: true, cancelable: true, clientX: r.left + cx, clientY: r.top + cy,
      view: window,
    }, extra || {})));
  }
  function click(id) { const e = el(id); if (e) e.click(); }

  function waitFor(fn, ms, why) {
    const t0 = Date.now();
    return new Promise((resolve, reject) => {
      (function loop() {
        let v = null;
        try { v = fn(); } catch (e) { v = null; }
        if (v) return resolve(v);
        if (Date.now() - t0 > (ms || 20000)) {
          return reject(new Error("超时: " + (why || "")));
        }
        setTimeout(loop, 50);
      })();
    });
  }

  function settle() {
    return waitFor(() => {
      const box = el("mp-preview-pal");
      return pimg() && !box.classList.contains("updating") && RP.canvas === cvs();
    }, 30000, "settle");
  }

  function previewSrc() {
    const i = pimg();
    if (!i) return "";
    return i.tagName === "CANVAS" ? i.toDataURL("image/png") : i.src;
  }

  function colorAt(nx, ny) {
    const img = pimg();
    const c = document.createElement("canvas");
    c.width = pw(img); c.height = ph(img);
    const g = c.getContext("2d"); g.drawImage(img, 0, 0);
    const d = g.getImageData(Math.floor(nx * c.width), Math.floor(ny * c.height), 1, 1).data;
    return "#" + [d[0], d[1], d[2]].map(v => v.toString(16).padStart(2, "0"))
      .join("").toUpperCase();
  }

  function countColor(hex) {
    const img = pimg();
    const want = [parseInt(hex.slice(1, 3), 16), parseInt(hex.slice(3, 5), 16),
                  parseInt(hex.slice(5, 7), 16)];
    const c = document.createElement("canvas");
    c.width = pw(img); c.height = ph(img);
    const g = c.getContext("2d"); g.drawImage(img, 0, 0);
    const d = g.getImageData(0, 0, c.width, c.height).data;
    let n = 0;
    for (let i = 0; i < d.length; i += 4) {
      if (d[i] === want[0] && d[i + 1] === want[1] && d[i + 2] === want[2]) n++;
    }
    return n;
  }

  /** 统计相对 baseline 被本地画笔改过的像素索引（不再有临时绘制层）。 */
  function paintStat() {
    const pc = RP.pc, base = RP._paintBaseline;
    if (!pc || !base || !pc.pixels) return { n: 0, w: 0, h: 0 };
    let n = 0, x0 = 1e9, y0 = 1e9, x1 = -1, y1 = -1;
    for (let y = 0; y < pc.height; y++) {
      for (let x = 0; x < pc.width; x++) {
        const i = y * pc.width + x;
        if (pc.pixels[i] !== base[i]) {
          n++;
          if (x < x0) x0 = x;
          if (x > x1) x1 = x;
          if (y < y0) y0 = y;
          if (y > y1) y1 = y;
        }
      }
    }
    if (n === 0) return { n: 0, w: 0, h: 0 };
    return { n: n, w: x1 - x0 + 1, h: y1 - y0 + 1 };
  }

  function makeImage() {    const c = document.createElement("canvas");
    c.width = 200; c.height = 150;
    const g = c.getContext("2d");
    g.fillStyle = "#767a7e"; g.fillRect(0, 0, 200, 150);
    const grd = g.createLinearGradient(0, 0, 200, 150);
    grd.addColorStop(0, "#6b7c8a"); grd.addColorStop(1, "#c8a06a");
    g.fillStyle = grd; g.fillRect(0, 0, 200, 150);
    g.fillStyle = "#e63c32"; g.fillRect(20, 110, 60, 18);
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

  /** 在覆盖画布上拖一圈（用归一化坐标，换算到当前可视矩形） */
  function lassoDrag(pts) {
    const c = RP.canvas;
    const r = c.getBoundingClientRect();
    const at = (p) => [p[0] * r.width, p[1] * r.height];
    const a = at(pts[0]);
    mouse(c, "mousedown", a[0], a[1]);
    pts.slice(1).forEach(p => { const q = at(p); mouse(c, "mousemove", q[0], q[1]); });
    window.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
  }

  (async function run() {
    const R = [];
    try {
      // ================= A. 隐藏原图关闭时 =================
      rec("hiddenAtStart", el("rp-sec").hidden);
      rec("noCanvasAtStart", !cvs());

      // 上传 + 开抖动，先把预览跑出来
      el("mp-dither").value = "floyd";
      el("mp-dither").dispatchEvent(new Event("change", { bubbles: true }));
      await upload();
      await waitFor(() => pimg() && pw(pimg()) > 0, 30000, "首张预览");
      await settle();
      rec("hiddenAfterUpload", el("rp-sec").hidden);
      rec("noCanvasAfterUpload", !cvs());
      rec("baseSrc", previewSrc().slice(0, 40));

      // 点预览图应该弹出放大框
      const im0 = pimg();
      mouse(im0, "click", im0.getBoundingClientRect().width / 2,
            im0.getBoundingClientRect().height / 2);
      await waitFor(() => el("mp-modal") && el("mp-modal").hidden === false, 5000,
                    "放大框");
      rec("modalOpensWhenClosed", el("mp-modal").hidden === false);
      click("mp-modal-close");
      await waitFor(() => el("mp-modal").hidden === true, 5000, "关闭放大框");

      // ================= B. 打开隐藏原图 =================
      click("set-solo-preview");
      await waitFor(() => !el("rp-sec").hidden, 5000, "修正区出现");
      rec("visibleAfterOpen", !el("rp-sec").hidden);
      rec("focusOn", document.body.classList.contains("focus-mode"));
      await waitFor(() => cvs() && cvs().offsetWidth > 4, 8000, "覆盖画布");
      rec("canvasAttached", !!cvs());
      const cv = cvs(), im = pimg();
      rec("canvasMatchesLayout",
          Math.abs(cv.offsetWidth - im.offsetWidth) <= 1 &&
          Math.abs(cv.offsetHeight - im.offsetHeight) <= 1);
      rec("chips", document.querySelectorAll("#rp-pal .rp-chip").length);
      rec("execDisabledNoTarget", el("rp-exec").disabled);

      // ================= B2. 算法 / 抖动已固定到左侧栏 =================
      rec("fxVisible", el("fx-sec") && !el("fx-sec").hidden);
      rec("algoOptions", el("mp-algo").options.length);
      rec("ditherOptions", el("mp-dither").options.length);
      rec("algoInit", el("mp-algo").value);
      rec("ditherInit", el("mp-dither").value);

      // 左侧栏现在就是唯一控件，直接改它。
      el("mp-algo").value = "cie94";
      el("mp-algo").dispatchEvent(new Event("change", { bubbles: true }));
      await new Promise(r => setTimeout(r, 150));
      rec("algoChanged", el("mp-algo").value);

      el("mp-dither").value = "atkinson";
      el("mp-dither").dispatchEvent(new Event("change", { bubbles: true }));
      await new Promise(r => setTimeout(r, 150));
      rec("ditherChanged", el("mp-dither").value);

      el("mp-strength").value = "60";
      el("mp-strength").dispatchEvent(new Event("input", { bubbles: true }));
      await new Promise(r => setTimeout(r, 150));
      rec("strengthChanged", el("mp-strength").value);
      rec("strengthLabel", el("mp-strength-val").textContent);

      // 还原，别影响后面的用例
      el("mp-algo").value = "weighted";
      el("mp-algo").dispatchEvent(new Event("change", { bubbles: true }));
      el("mp-dither").value = "floyd";
      el("mp-dither").dispatchEvent(new Event("change", { bubbles: true }));
      el("mp-strength").value = "100";
      el("mp-strength").dispatchEvent(new Event("input", { bubbles: true }));
      el("mp-strength").dispatchEvent(new Event("change", { bubbles: true }));
      await new Promise(r => setTimeout(r, 600));
      await settle();

      // ================= C. 没选受害者 -> 拒绝圈选区 =================
      lassoDrag([[0.1, 0.1], [0.6, 0.12], [0.6, 0.7], [0.1, 0.68]]);
      await new Promise(r => setTimeout(r, 120));
      rec("pendingWithoutTarget", RP.pending ? RP.pending.length : 0);
      rec("statNoTarget", el("rp-stat").textContent.slice(0, 30));

      // ================= D. 选受害者方块 =================
      // 从色块列表里挑一个（Shift 点是笔刷，普通点是受害者）
      const chips = [...document.querySelectorAll("#rp-pal .rp-chip")];
      rec("chipCount", chips.length);
      chips[7].click();
      await new Promise(r => setTimeout(r, 80));
      rec("targetSet", RP.target);
      rec("targetShown", el("rp-target-hex").textContent);

      // 再从预览图上拾取一次（验证拾色通路）
      click("rp-pick-target");
      rec("picking", RP.picking === true);
      const c2 = RP.canvas;
      const r2 = c2.getBoundingClientRect();
      mouse(c2, "mousedown", r2.width * 0.5, r2.height * 0.5);
      await new Promise(r => setTimeout(r, 80));
      rec("targetAfterPick", RP.target);
      rec("pickConsumed", RP.picking === false);

      // 为了让降噪有效果，锁定「预览里出现最多、又不是主色」的那种杂色：
      // 直接把 target 设成调色板里某个颜色，然后圈一片渐变区
      const tgt = (function () {
        // 用预览像素统计：选一个数量中等、在渐变区里成群出现的颜色
        const img = pimg();
        const c = document.createElement("canvas");
        c.width = pw(img); c.height = ph(img);
        const g = c.getContext("2d"); g.drawImage(img, 0, 0);
        const d = g.getImageData(0, 0, c.width, c.height).data;
        const m = new Map();
        // 只看右半边（渐变那一片）
        for (let y = 0; y < c.height; y++) {
          for (let x = (c.width >> 1); x < c.width; x++) {
            const o = (y * c.width + x) * 4;
            const k = (d[o] << 16) | (d[o + 1] << 8) | d[o + 2];
            m.set(k, (m.get(k) || 0) + 1);
          }
        }
        const arr = [...m.entries()].sort((a, b) => b[1] - a[1]);
        // 取第 3 多的，既不是主色也有一定数量
        const k = arr[Math.min(2, arr.length - 1)][0];
        return "#" + k.toString(16).padStart(6, "0").toUpperCase();
      })();
      RP.target = tgt;
      rpSyncUI();
      rec("targetForTest", tgt);
      const countTargetBefore = countColor(tgt);
      rec("countTargetBefore", countTargetBefore);

      // ================= E. 圈选区（只是待执行） =================
      const srcBefore = previewSrc();
      const opsBefore = RP.ops.length;
      lassoDrag([[0.55, 0.35], [0.97, 0.37], [0.96, 0.92], [0.56, 0.90]]);
      await new Promise(r => setTimeout(r, 200));
      rec("pendingPoints", RP.pending ? RP.pending.length : 0);
      rec("opsUnchangedBeforeExec", RP.ops.length === opsBefore);
      rec("previewUnchangedBeforeExec", previewSrc() === srcBefore);
      rec("execEnabled", el("rp-exec").disabled === false);

      // 覆盖层上应该出现洋红高亮
      (function () {
        const cvv = cvs();
        const g = cvv.getContext("2d");
        const d = g.getImageData(0, 0, cvv.width, cvv.height).data;
        let n = 0;
        for (let i = 0; i < d.length; i += 4) {
          if (d[i] > 200 && d[i + 1] < 60 && d[i + 2] > 150 && d[i + 3] > 100) n++;
        }
        rec("highlightPixels", n);
      })();

      // ================= F. 执行降噪 =================
      click("rp-exec");
      await waitFor(() => RP.lastInfo && RP.lastInfo.op_count > 0, 30000, "执行降噪");
      await settle();
      rec("opsAfterExec", RP.ops.length);
      rec("denoised", RP.lastInfo.denoised);
      rec("changedCount", RP.lastInfo.ops[0].changed);
      rec("countTargetAfter", countColor(tgt));
      rec("previewChanged", previewSrc() !== srcBefore);
      // 只改锁定的方块：其它颜色不该变少
      rec("opTarget", RP.lastInfo.ops[0].target);
      rec("statAfterExec", el("rp-stat").textContent.slice(0, 60));

      // ================= F2. 画笔 =================
      document.querySelector('.rp-tab[data-rptool="brush"]').click();
      await new Promise(r => setTimeout(r, 80));
      rec("brushTool", RP.tool);
      rec("brushHexDefault", RP.brushHex);

      // 挑一个和落笔点当前颜色不一样的调色板颜色当笔刷色
      const spot = [0.25, 0.30];
      const spotBefore = colorAt(spot[0], spot[1]);
      const chips2 = [...document.querySelectorAll("#rp-pal .rp-chip")];
      let brushPick = null;
      for (const c of chips2) {
        if (c.dataset.hex.toUpperCase() !== spotBefore) {
          c.dispatchEvent(new MouseEvent("click", { bubbles: true, shiftKey: true }));
          brushPick = c.dataset.hex;
          break;
        }
      }
      rec("brushHexPicked", RP.brushHex);
      rec("brushPick", brushPick);

      el("rp-size").value = "20";
      el("rp-size").dispatchEvent(new Event("input", { bubbles: true }));
      rec("brushSize", RP.brushSize);
      rec("brushRadii", rpBrushRadii());

      // —— 实时性：落笔之后、松开之前，本地就应该已经画上去了 ——
      const srcBeforeBrush = previewSrc();
      RP._paintBaseline = RP.pc.pixels.slice();
      const cb2 = RP.canvas;
      const rb2 = cb2.getBoundingClientRect();
      mouse(cb2, "mousedown", rb2.width * spot[0], rb2.height * spot[1]);
      await new Promise(r => setTimeout(r, 80));
      rec("inkOnMouseDown", paintStat().n);
      rec("opsDuringDrag", RP.ops.length);
      rec("imgUnchangedDuringDrag", previewSrc() === srcBeforeBrush);
      mouse(cb2, "mousemove", rb2.width * spot[0] + 6, rb2.height * spot[1] + 6);
      await new Promise(r => setTimeout(r, 80));
      rec("inkAfterMove", paintStat().n);
      rec("inkGrewWhileDragging", paintStat().n > 0);
      // 松开前服务端一次都没被叫过（这就是「实时」）
      rec("imgStillUnchanged", previewSrc() === srcBeforeBrush);

      window.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
      await new Promise(r => setTimeout(r, 150));
      rec("pendingBrushStrokes", RP.pendingStrokes.length);
      rec("opsAfterMouseUp", RP.ops.length);
      rec("applyDisabledBeforeClick", el("rp-apply-brush").disabled);
      click("rp-apply-brush");
      await new Promise(r => setTimeout(r, 100));
      rec("pendingAfterApply", RP.pendingStrokes.length);
      rec("opsAfterBrush", RP.ops.length);
      rec("lastOpKind", (RP.ops[RP.ops.length - 1] || {}).kind);
      rec("lastOpPts", ((RP.ops[RP.ops.length - 1] || {}).points || []).length);
      rec("lastOpRx", (RP.ops[RP.ops.length - 1] || {}).rx);
      rec("lastOpRy", (RP.ops[RP.ops.length - 1] || {}).ry);

      try {
        await waitFor(() => RP.lastInfo && RP.lastInfo.brush_pixels > 0,
                      30000, "画笔生效");
        await waitFor(() => previewSrc() !== srcBeforeBrush, 30000, "画笔预览刷新");
        await settle();
        rec("brushPixels", RP.lastInfo.brush_pixels);
      } catch (e) {
        rec("brushErr", String((e && e.message) || e));
        rec("brushPixels", 0);
      }
      rec("brushSpotBefore", spotBefore);
      rec("brushSpotAfter", colorAt(spot[0], spot[1]));
      rec("opsAfterBrush", RP.ops.length);
      rec("countAfterBrush", countColor(tgt));
      rec("inkAfterServerRender", paintStat().n);

      // —— 像素画：笔头 1 格 + 单击一下 = 正好一个方块，硬边 ——
      el("rp-size").value = "1";
      el("rp-size").dispatchEvent(new Event("input", { bubbles: true }));
      RP._paintBaseline = RP.pc.pixels.slice();
      const pencilAt = [0.42, 0.42];
      rec("pencilSpotBefore", colorAt(pencilAt[0], pencilAt[1]));
      const cb3 = RP.canvas;
      const rb3 = cb3.getBoundingClientRect();
      mouse(cb3, "mousedown", rb3.width * pencilAt[0], rb3.height * pencilAt[1]);
      await new Promise(r => setTimeout(r, 60));
      const st1 = paintStat();
      rec("pencilInk", st1.n);
      rec("pencilW", st1.w);
      rec("pencilH", st1.h);
      window.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
      await new Promise(r => setTimeout(r, 80));
      click("rp-apply-brush");
      await new Promise(r => setTimeout(r, 700));
      rec("opsAfterPencil", RP.ops.length);
      rec("pencilSpotAfter", colorAt(pencilAt[0], pencilAt[1]));

      // —— 画笔不画笔触轮廓 ——
      // 直接构造一份「只有画笔操作」的文档再重绘，覆盖层上应该什么都不画。
      // （不能靠数蓝色像素：已执行的降噪套索本来就是蓝的。）
      (function () {
        const keep = RP.ops;
        RP.ops = keep.filter(o => o.kind === "brush");
        const keepHover = RP.hover;
        RP.hover = null;
        rpRedraw();
        const cvv = cvs();
        const g = cvv.getContext("2d");
        const d = g.getImageData(0, 0, cvv.width, cvv.height).data;
        let n = 0;
        for (let i = 3; i < d.length; i += 4) if (d[i] > 8) n++;
        rec("brushOnlyOverlayInk", n);
        RP.ops = keep;
        RP.hover = keepHover;
        rpRedraw();
      })();
      rec("opsBeforeOverlayTest", RP.ops.length);
      document.querySelector('.rp-tab[data-rptool="lasso"]').click();
      await new Promise(r => setTimeout(r, 60));

      // ================= G2. 右键拖动 / 左键留给工具 =================
      focusResetTransform();
      await new Promise(r => setTimeout(r, 60));
      const cvg = RP.canvas;
      const rg = cvg.getBoundingClientRect();
      const tx0 = FOCUS.tx, ty0 = FOCUS.ty;

      // 右键拖动应该平移预览图
      mouse(cvg, "mousedown", rg.width * 0.5, rg.height * 0.5, { button: 2 });
      window.dispatchEvent(new MouseEvent("mousemove", {
        bubbles: true, button: 2,
        clientX: rg.left + rg.width * 0.5 + 40,
        clientY: rg.top + rg.height * 0.5 + 25 }));
      window.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, button: 2 }));
      await new Promise(r => setTimeout(r, 60));
      rec("panRightDx", Math.round(FOCUS.tx - tx0));
      rec("panRightDy", Math.round(FOCUS.ty - ty0));
      rec("panRightMoved",
          Math.abs(FOCUS.tx - tx0 - 40) < 3 && Math.abs(FOCUS.ty - ty0 - 25) < 3);
      rec("panOverlayFollows", pimg().style.transform === cvg.style.transform);

      // 左键不应该平移（那是套索 / 画笔的地盘）
      focusResetTransform();
      await new Promise(r => setTimeout(r, 60));
      const tx1 = FOCUS.tx, ty1 = FOCUS.ty;
      const rg2 = cvg.getBoundingClientRect();
      mouse(cvg, "mousedown", rg2.width * 0.4, rg2.height * 0.4, { button: 0 });
      window.dispatchEvent(new MouseEvent("mousemove", {
        bubbles: true, button: 0,
        clientX: rg2.left + rg2.width * 0.4 + 40,
        clientY: rg2.top + rg2.height * 0.4 + 25 }));
      window.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, button: 0 }));
      await new Promise(r => setTimeout(r, 80));
      rec("panLeftDx", Math.round(FOCUS.tx - tx1));
      rec("leftDidNotPan", FOCUS.tx === tx1 && FOCUS.ty === ty1);
      RP.pending = null;
      rpSyncUI();
      focusResetTransform();

      // ================= G. 覆盖层跟随缩放 / 平移 =================
      const imgEl = pimg();
      focusZoomAt(1.6, window.innerWidth / 2, window.innerHeight / 2);
      await new Promise(r => setTimeout(r, 60));
      const tImg = imgEl.style.transform;
      const tCv = cvs().style.transform;
      rec("imgTransform", tImg);
      rec("canvasTransform", tCv);
      rec("transformMatches", tImg === tCv && tImg.indexOf("scale") >= 0);
      focusResetTransform();
      await new Promise(r => setTimeout(r, 60));
      rec("transformMatchesAfterReset",
          pimg().style.transform === cvs().style.transform);
      // 平移
      FOCUS.tx = 40; FOCUS.ty = -25; focusApply();
      await new Promise(r => setTimeout(r, 60));
      rec("transformMatchesAfterPan",
          pimg().style.transform === cvs().style.transform);

      // ================= G2. 隐藏 / 显示选区 =================
      function canvasInk() {
        // 数一下画布上有多少个非透明像素，用来判断轮廓在不在
        const cvv = cvs();
        const g = cvv.getContext("2d");
        const d = g.getImageData(0, 0, cvv.width, cvv.height).data;
        let n = 0;
        for (let i = 3; i < d.length; i += 4) if (d[i] > 8) n++;
        return n;
      }
      rec("inkWithOverlay", canvasInk());
      rec("hideLabelBefore", el("rp-hide").textContent.trim());
      click("rp-hide");
      await new Promise(r => setTimeout(r, 120));
      rec("inkHidden", canvasInk());
      rec("overlayFlagOff", RP.showOverlay === false);
      rec("hideLabelAfter", el("rp-hide").textContent.trim());
      rec("hideBtnOffClass", el("rp-hide").classList.contains("off"));
      rec("opsIntactWhileHidden", RP.ops.length);
      // 隐藏只是不画，修改结果不能变
      rec("countTargetWhileHidden", countColor(tgt));
      // 隐藏状态下再拖一把，应该自动显示回来（否则拖了看不见）
      const cvh = RP.canvas;
      const rh = cvh.getBoundingClientRect();
      mouse(cvh, "mousedown", rh.width * 0.6, rh.height * 0.5);
      await new Promise(r => setTimeout(r, 120));
      rec("autoShownOnDrag", RP.showOverlay === true);
      window.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
      RP.pending = null; rpSyncUI();
      click("rp-hide");                       // 再收起来，验证能反复切换
      await new Promise(r => setTimeout(r, 120));
      rec("inkHiddenAgain", canvasInk());
      click("rp-hide");
      await new Promise(r => setTimeout(r, 120));
      rec("inkShownAgain", canvasInk());
      rec("opsStillIntact", RP.ops.length);

      // ================= H. 撤回 / 重做 =================
      rec("undoStack", UNDO.stack.length);
      rec("undoIndex", UNDO.index);
      const nOps = RP.ops.length;              // 此时是「降噪 + 画笔」两步
      const countBeforeUndo = countColor(tgt);
      const srcDone = previewSrc();
      click("rp-undo");
      await waitFor(() => RP.ops.length === nOps - 1, 30000, "撤回");
      // 注意：RP.ops 是同步改掉的，必须等预览图真的刷新过再读像素
      await waitFor(() => previewSrc() !== srcDone, 30000, "撤回后预览刷新");
      await settle();
      rec("opsBeforeUndo", nOps);
      rec("opsAfterUndo", RP.ops.length);
      rec("countBeforeUndo", countBeforeUndo);
      rec("countAfterUndo", countColor(tgt));
      rec("pencilSpotAfterUndo", colorAt(pencilAt[0], pencilAt[1]));
      rec("undoBtnDisabledNow", el("rp-undo").disabled);

      const srcUndone = previewSrc();
      click("rp-redo");
      await waitFor(() => RP.ops.length === nOps, 30000, "重做");
      await waitFor(() => previewSrc() !== srcUndone, 30000, "重做后预览刷新");
      await settle();
      rec("opsAfterRedo", RP.ops.length);
      rec("countAfterRedo", countColor(tgt));
      rec("pencilSpotAfterRedo", colorAt(pencilAt[0], pencilAt[1]));
      rec("undoStateText", el("undo-state").textContent);

      // 一路撤回到底：操作清空，画面应该回到最初的抖动结果
      let guard = 0;
      while (RP.ops.length > 0 && guard++ < 30) {
        const n0 = RP.ops.length;
        const s0 = previewSrc();
        click("rp-undo");
        await waitFor(() => RP.ops.length === n0 - 1, 30000, "撤回一步");
        await waitFor(() => previewSrc() !== s0, 30000, "撤回后预览刷新");
      }
      await settle();
      rec("opsAfterUndoAll", RP.ops.length);
      rec("countAfterUndoAll", countColor(tgt));
      rec("countOriginal", countTargetBefore);
      click("rp-redo");                        // 恢复一步，后面的用例还要用
      await new Promise(r => setTimeout(r, 500));

      // 连做多次编辑，验证上限 20
      for (let i = 0; i < 25; i++) {
        RP.ops.push({ kind: "fill", hex: RP.target || "#909090",
                      lasso: [[0.01, 0.01], [0.02, 0.01], [0.02, 0.02]] });
        undoPush();
      }
      rec("stackCapped", UNDO.stack.length);
      rec("undoMax", UNDO.max);

      // ================= I. 撤回是「全局」的 =================
      // 先回到干净状态
      RP.ops = []; RP.pending = null; rpSyncUI();
      await new Promise(r => setTimeout(r, 150));

      // (1) 方块选择：点「全不选」再撤回，选择应该回来
      const selBefore = [...PAL.selected].sort();
      rec("selCountBefore", selBefore.length);
      click("pal-none");
      await waitFor(() => PAL.selected.size === 0, 20000, "全不选");
      rec("selCountAfterNone", PAL.selected.size);
      click("rp-undo");
      await waitFor(() => PAL.selected.size === selBefore.length, 20000, "撤回选择");
      rec("selCountAfterUndo", PAL.selected.size);
      rec("selRestored",
          [...PAL.selected].sort().join(",") === selBefore.join(","));

      // (2) 图片调整：改曝光再撤回，数值应该回到原来
      const expBefore = ADJ.exposure;
      const sl = el("adj-exposure");
      sl.value = "60";
      sl.dispatchEvent(new Event("input", { bubbles: true }));
      sl.dispatchEvent(new Event("change", { bubbles: true }));
      await new Promise(r => setTimeout(r, 200));
      rec("expBefore", expBefore);
      rec("expAfterSet", ADJ.exposure);
      click("rp-undo");
      await new Promise(r => setTimeout(r, 500));
      rec("expAfterUndo", ADJ.exposure);
      rec("sliderRestored", el("adj-exposure").value);

      // (3) 撤回之后做新动作，重做分支应被丢弃
      click("rp-undo");
      await new Promise(r => setTimeout(r, 300));
      rec("canRedoBefore", el("rp-redo").disabled === false);
      RP.ops.push({ kind: "revert", lasso: [[0.01, 0.01], [0.02, 0.01], [0.02, 0.02]] });
      undoPush();
      await new Promise(r => setTimeout(r, 100));
      rec("canRedoAfter", el("rp-redo").disabled === false);

      // ================= J. 关掉隐藏原图 =================
      click("set-solo-preview");
      await waitFor(() => el("rp-sec").hidden, 5000, "修正区隐藏");
      rec("hiddenAfterClose", el("rp-sec").hidden);
      rec("canvasRemoved", !cvs());
      const im2 = pimg();
      mouse(im2, "click", im2.getBoundingClientRect().width / 2,
            im2.getBoundingClientRect().height / 2);
      await waitFor(() => el("mp-modal").hidden === false, 5000, "放大框2");
      rec("modalOpensWhenClosedAgain", el("mp-modal").hidden === false);

      rec("ok", true);
    } catch (e) {
      out.errors.push(String((e && e.stack) || e));
      rec("ok", false);
    }
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
            urllib.request.urlopen("http://127.0.0.1:%d/api/palette" % PORT,
                                   timeout=2).read()
            return p
        except Exception:
            time.sleep(0.15)
    raise RuntimeError("服务没起来")


def dump_dom(url, budget=90000, timeout=420):
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
    web = os.path.join(SRC_DIR, "maptool", "web")
    probe_path = os.path.join(web, "_rp_probe.html")
    html = open(os.path.join(web, "index.html"), encoding="utf-8").read()
    html = html.replace("<head>", "<head>\n" + PROBE_HEAD, 1)
    html = html.replace("</body>", PROBE + "\n</body>", 1)
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
            print("没有探针结果（data-rptest）—— 页面可能卡住了")
            print(dom[:1500])
            return 2
        out = json.loads(base64.b64decode(m.group(1)).decode("utf-8"))
        print("探针结果：")
        print(json.dumps(out, ensure_ascii=False, indent=1)[:4200])
        print()
        if out.get("errors"):
            print("页面报错：")
            for e in out["errors"]:
                print("   " + e[:700])
            print()

        g = out.get

        def num(key, default=None):
            """0 是有意义的值，不能用 `or` 兜底（`0 or -1` 会变成 -1）。"""
            v = g(key)
            return default if v is None else v

        check("关闭时修正区隐藏", g("hiddenAtStart") is True
              and g("hiddenAfterUpload") is True)
        check("关闭时预览图上没有覆盖画布", g("noCanvasAtStart") is True
              and g("noCanvasAfterUpload") is True)
        check("关闭时点预览图弹出放大框", g("modalOpensWhenClosed") is True)
        check("打开后修正区出现", g("visibleAfterOpen") is True)
        check("打开后进入专注模式", g("focusOn") is True)
        check("覆盖画布已挂上且与图片布局盒一致",
              g("canvasAttached") is True and g("canvasMatchesLayout") is True)
        check("色块列表已渲染（%s 个）" % g("chips"), (g("chips") or 0) > 10)
        check("左侧栏出现「算法与抖动」", g("fxVisible") is True)
        check("颜色识别 / 抖动算法选项存在（%s / %s）"
              % (g("algoOptions"), g("ditherOptions")),
              num("algoOptions", 0) >= 6 and num("ditherOptions", 0) >= 10)
        check("左侧栏颜色识别可直接修改（%s）" % g("algoChanged"),
              g("algoChanged") == "cie94")
        check("左侧栏抖动算法可直接修改（%s）" % g("ditherChanged"),
              g("ditherChanged") == "atkinson")
        check("左侧栏抖动比例可直接修改（%s / label %s）"
              % (g("strengthChanged"), g("strengthLabel")),
              g("strengthChanged") == "60" and g("strengthLabel") == "60")

        # ---- 右键拖动 / 左键留给工具 ----
        check("右键拖动可以平移预览图（dx=%s dy=%s）"
              % (g("panRightDx"), g("panRightDy")), g("panRightMoved") is True)
        check("平移时覆盖层跟着图片走", g("panOverlayFollows") is True)
        check("左键拖动不平移预览图（dx=%s）" % g("panLeftDx"),
              g("leftDidNotPan") is True)
        check("没选受害者时「执行降噪」不可点", g("execDisabledNoTarget") is True)
        check("没选受害者时圈选区被拒绝（pending=%s）" % g("pendingWithoutTarget"),
              num("pendingWithoutTarget", 0) == 0)
        check("能从色块列表选受害者（%s）" % g("targetSet"), bool(g("targetSet")))
        check("能按住从预览图拾取受害者（%s）" % g("targetAfterPick"),
              bool(g("targetAfterPick")) and g("pickConsumed") is True)
        check("圈完选区后只是待执行，操作序列没变", g("opsUnchangedBeforeExec") is True)
        check("圈完选区后预览图没变（没自动修改）",
              g("previewUnchangedBeforeExec") is True)
        check("执行按钮变为可点", g("execEnabled") is True)
        check("覆盖层上出现洋红受害者高亮（%s 像素）" % g("highlightPixels"),
              num("highlightPixels", 0) > 0)
        check("执行后操作入栈", num("opsAfterExec", -1) == 1)
        check("执行后确实降噪了（%s 个）" % g("denoised"), num("denoised", 0) > 0)
        check("执行后预览图变了", g("previewChanged") is True)
        check("只改了锁定的那种方块（target=%s，改 %s 个）"
              % (g("opTarget"), g("changedCount")),
              g("opTarget") == g("targetForTest") and num("changedCount", 0) > 0)
        check("目标色数量下降（%s -> %s）"
              % (g("countTargetBefore"), g("countTargetAfter")),
              num("countTargetAfter", 1 << 30) < num("countTargetBefore", 0))

        # ---- 画笔 ----
        check("能切到画笔工具", g("brushTool") == "brush")
        check("画笔颜色能选中（%s）" % g("brushHexPicked"), bool(g("brushHexPicked")))
        check("笔刷半径按成品方块数换算（%s）" % g("brushRadii"),
              (g("brushRadii") or {}).get("rx", 0) > 0.01
              and (g("brushRadii") or {}).get("ry", 0) > 0.01)
        check("涂抹产生了一个 brush 操作（kind=%s，%s 个落点，rx=%s ry=%s）"
              % (g("lastOpKind"), g("lastOpPts"), g("lastOpRx"), g("lastOpRy")),
              g("lastOpKind") == "brush" and num("lastOpPts", 0) > 0
              and num("lastOpRx", 0) > 0 and num("lastOpRy", 0) > 0)
        check("服务端确认画笔生效（%s 个方块）" % g("brushPixels"),
              num("brushPixels", 0) > 0, str(g("brushErr")))
        check("落笔处被涂成笔刷色（%s -> %s，笔刷 %s）"
              % (g("brushSpotBefore"), g("brushSpotAfter"), g("brushHexPicked")),
              str(g("brushSpotAfter")).upper() == str(g("brushHexPicked")).upper()
              and str(g("brushSpotBefore")).upper() != str(g("brushHexPicked")).upper())

        # ---- 实时：松开鼠标之前本地就已经画上去了，而且没打扰服务端 ----
        check("落笔瞬间本地就画上去了（%s 个像素）" % g("inkOnMouseDown"),
              num("inkOnMouseDown", 0) > 0)
        check("拖动过程中本地像素在增加（%s -> %s）"
              % (g("inkOnMouseDown"), g("inkAfterMove")),
              num("inkAfterMove", 0) > num("inkOnMouseDown", 0))
        check("拖动过程中还没入栈（%s 步）" % g("opsDuringDrag"),
              num("opsDuringDrag", -1) == num("opsAfterExec", -2))
        check("拖动时预览 canvas 本地重绘（%s / %s）"
              % (g("imgUnchangedDuringDrag"), g("imgStillUnchanged")),
              g("imgUnchangedDuringDrag") is False and g("imgStillUnchanged") is False)
        check("松开鼠标只暂存笔迹，不自动入栈（pending=%s, ops=%s）"
              % (g("pendingBrushStrokes"), g("opsAfterMouseUp")),
              num("pendingBrushStrokes", 0) > 0
              and num("opsAfterMouseUp", -1) == num("opsAfterExec", -2))
        check("暂存后「应用画笔修改」按钮可用", g("applyDisabledBeforeClick") is False)
        check("点应用后才提交并清空暂存（pending=%s, ops=%s）"
              % (g("pendingAfterApply"), g("opsAfterBrush")),
              num("pendingAfterApply", 0) == 0
              and num("opsAfterBrush", -1) > num("opsAfterExec", -2))
        check("松开后服务端结果仍保留画笔像素（%s）" % g("inkAfterServerRender"),
              num("inkAfterServerRender", 0) > 0)

        # ---- 像素画：笔头 1 格 = 一个方块，硬边 ----
        check("笔头 1 格时单击只画 1 个方块（%s 像素，%s×%s）"
              % (g("pencilInk"), g("pencilW"), g("pencilH")),
              num("pencilInk", 0) == 1 and num("pencilW", 0) == 1
              and num("pencilH", 0) == 1)
        check("铅笔点一下确实落在图上（%s -> %s）"
              % (g("pencilSpotBefore"), g("pencilSpotAfter")),
              str(g("pencilSpotAfter")).upper() == str(g("brushHexPicked")).upper()
              and str(g("pencilSpotBefore")).upper() != str(g("pencilSpotAfter")).upper())
        check("第二次涂抹也入了栈（%s）" % g("opsAfterPencil"),
              num("opsAfterPencil", -1) == num("opsAfterBrush", -2) + 1)
        check("画笔在覆盖层上不留任何笔触（只有画笔操作时覆盖层是空的，%s 像素）"
              % g("brushOnlyOverlayInk"), num("brushOnlyOverlayInk", -1) == 0)
        check("缩放后覆盖层与图片 transform 一致（%s）" % g("canvasTransform"),
              g("transformMatches") is True)
        check("复位后仍一致", g("transformMatchesAfterReset") is True)
        check("平移后仍一致", g("transformMatchesAfterPan") is True)
        check("执行后画布上确实有选区轮廓（%s 个像素）" % g("inkWithOverlay"),
              num("inkWithOverlay", 0) > 0)
        check("「隐藏选区」按钮切换文字（%s -> %s）"
              % (g("hideLabelBefore"), g("hideLabelAfter")),
              "隐藏" in str(g("hideLabelBefore"))
              and "显示" in str(g("hideLabelAfter")))
        check("隐藏后画布被清空（%s -> %s）"
              % (g("inkWithOverlay"), g("inkHidden")),
              num("inkHidden", -1) == 0 and num("inkWithOverlay", 0) > 0)
        check("隐藏后按钮有 off 样式", g("hideBtnOffClass") is True)
        check("隐藏只影响显示，操作序列不变（%s）" % g("opsIntactWhileHidden"),
              num("opsIntactWhileHidden", -1) == num("opsBeforeOverlayTest", -2))
        check("隐藏后修改结果不变（目标色仍是 %s）" % g("countTargetWhileHidden"),
              num("countTargetWhileHidden", -1) == num("countAfterBrush", -2))
        check("隐藏状态下再拖会自动显示回来", g("autoShownOnDrag") is True)
        check("可以反复隐藏（%s）" % g("inkHiddenAgain"),
              num("inkHiddenAgain", -1) == 0)
        check("再点一下能恢复显示（%s）" % g("inkShownAgain"),
              num("inkShownAgain", 0) > 0)
        check("反复切换后操作序列仍然不变（%s）" % g("opsStillIntact"),
              num("opsStillIntact", -1) == num("opsBeforeOverlayTest", -2))
        check("撤回一步把操作弹掉（%s -> %s）"
              % (g("opsBeforeUndo"), g("opsAfterUndo")),
              num("opsAfterUndo", -1) == num("opsBeforeUndo", 0) - 1)
        check("撤回最后一次涂抹：那个方块回到原色（%s -> %s）"
              % (g("pencilSpotAfter"), g("pencilSpotAfterUndo")),
              str(g("pencilSpotAfterUndo")).upper() != str(g("brushHexPicked")).upper()
              and str(g("pencilSpotAfterUndo")).upper()
                  == str(g("pencilSpotBefore")).upper())
        check("重做恢复操作（%s）" % g("opsAfterRedo"),
              num("opsAfterRedo", -1) == num("opsBeforeUndo", -2))
        check("重做后那个方块又变回笔刷色（%s）" % g("pencilSpotAfterRedo"),
              str(g("pencilSpotAfterRedo")).upper() == str(g("brushHexPicked")).upper())
        check("一路撤回到底后操作清空（%s）" % g("opsAfterUndoAll"),
              num("opsAfterUndoAll", -1) == 0)
        check("撤回到底后画面回到最初的抖动结果（目标色 %s vs 原始 %s）"
              % (g("countAfterUndoAll"), g("countOriginal")),
              num("countAfterUndoAll", -1) == num("countOriginal", -2))
        check("撤回栈上限是 20（实际 %s）" % g("stackCapped"),
              g("undoMax") == 20 and num("stackCapped", 999) <= 20)
        check("撤回覆盖方块选择（%s -> 0 -> %s）"
              % (g("selCountBefore"), g("selCountAfterUndo")),
              num("selCountAfterNone", -1) == 0
              and num("selCountAfterUndo", -2) == num("selCountBefore", -3)
              and g("selRestored") is True)
        check("撤回覆盖图片调整（曝光 %s -> %s -> %s）"
              % (g("expBefore"), g("expAfterSet"), g("expAfterUndo")),
              num("expAfterSet", 0) == 60 and num("expAfterUndo", -1) == num("expBefore", -2))
        check("撤回后滑杆数值也一起恢复（%s）" % g("sliderRestored"),
              str(g("sliderRestored")) == str(g("expBefore")))
        check("撤回之后做新动作会丢弃重做分支（%s -> %s）"
              % (g("canRedoBefore"), g("canRedoAfter")),
              g("canRedoBefore") is True and g("canRedoAfter") is False)
        check("关闭后修正区隐藏", g("hiddenAfterClose") is True)
        check("关闭后覆盖画布被摘掉", g("canvasRemoved") is True)
        check("关闭后点预览图又能弹出放大框",
              g("modalOpensWhenClosedAgain") is True)
        check("整段流程无异常", g("ok") is True)
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
