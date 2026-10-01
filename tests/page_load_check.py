# -*- coding: utf-8 -*-
"""
页面加载自检：把浏览器控制台的报错抓出来。

注入探针收集 window.onerror / unhandledrejection / console.error，
等页面稳定后把结果写进 data-pageerrors。会顺带确认关键脚本都执行了。

用法（工作区根目录）：
    python tests/page_load_check.py
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
PORT = 8862

PROBE_HEAD = r"""
<script>
(function () {
  window.__errs = [];
  window.addEventListener("error", function (e) {
    window.__errs.push("error: " + (e.message || "") +
      " @ " + (e.filename || "?") + ":" + (e.lineno || 0));
  });
  window.addEventListener("unhandledrejection", function (e) {
    window.__errs.push("rejection: " + (e.reason && (e.reason.stack || e.reason.message) || e.reason));
  });
  var ce = console.error, cw = console.warn;
  console.error = function () { window.__errs.push("console.error: " + Array.from(arguments).join(" ")); ce.apply(console, arguments); };
  console.warn = function () { window.__errs.push("console.warn: " + Array.from(arguments).join(" ")); cw.apply(console, arguments); };
  // SSE 保活会让 --virtual-time-budget 一直等，打桩掉
  window.EventSource = function () {
    return { close: function () {}, addEventListener: function () {},
             set onmessage(v) {}, set onerror(v) {}, set onopen(v) {} };
  };
})();
</script>
"""

PROBE_BODY = r"""
<script>
(function () {
  function el(id) { return document.getElementById(id); }
  var out = { errors: window.__errs.slice(), checks: {} };
  function has(name, fn) {
    try { out.checks[name] = fn(); } catch (e) { out.checks[name] = "EXC: " + e.message; }
  }
  has("RP", function () { return typeof RP === "object" && !!RP; });
  has("UNDO", function () { return typeof UNDO === "object" && !!(UNDO && UNDO.max === 20); });
  has("rpSetOpen", function () { return typeof rpSetOpen === "function"; });
  has("rpSpread", function () { return typeof rpSpread === "function"; });
  has("undoPush", function () { return typeof undoPush === "function"; });
  has("undoStep", function () { return typeof undoStep === "function"; });
  has("applySoloPreview", function () { return typeof applySoloPreview === "function"; });
  has("focusApply", function () { return typeof focusApply === "function"; });
  has("rpSecHiddenAtStart", function () { return el("rp-sec") ? el("rp-sec").hidden : "missing"; });
  has("rpExecDisabled", function () { return el("rp-exec") ? el("rp-exec").disabled : "missing"; });
  has("undoBtnDisabled", function () { return el("rp-undo") ? el("rp-undo").disabled : "missing"; });
  has("chipsAfterPalette", function () {
    return document.querySelectorAll("#rp-pal .rp-chip").length; });
  has("canvasExists", function () { return !!document.querySelector(".rp-canvas"); });
  has("status", function () { return el("status").textContent; });
  has("undoState", function () { return el("undo-state").textContent; });

  // 打开「隐藏原图」-> 修正区应该出现、画布应该没有（还没上传图片）
  try { el("set-solo-preview").click(); } catch (e) { out.errors.push("click: " + e.message); }

  setTimeout(function () {
    has("rpSecVisibleAfterOpen", function () { return el("rp-sec") && !el("rp-sec").hidden; });
    has("focusMode", function () { return document.body.classList.contains("focus-mode"); });
    // 关掉 -> 应该重新隐藏
    try { el("set-solo-preview").click(); } catch (e) { out.errors.push("click2: " + e.message); }
    setTimeout(function () {
      has("rpSecHiddenAfterClose", function () { return el("rp-sec") && el("rp-sec").hidden; });
      has("focusModeOff", function () { return !document.body.classList.contains("focus-mode"); });
      out.errors = out.errors.concat(window.__errs);
      document.documentElement.setAttribute(
        "data-pageerrors", btoa(unescape(encodeURIComponent(JSON.stringify(out)))));
    }, 250);
  }, 250);
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


def dump_dom(url, budget=12000, timeout=180):
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
    web = os.path.join(SRC_DIR, "maptool", "web")
    probe_path = os.path.join(web, "_pl_probe.html")
    html = open(os.path.join(web, "index.html"), encoding="utf-8").read()
    html = html.replace("<head>", "<head>\n" + PROBE_HEAD, 1)
    html = html.replace("</body>", PROBE_BODY + "\n</body>", 1)
    open(probe_path, "w", encoding="utf-8").write(html)

    proc = None
    rc = 0
    try:
        proc = start_server()
        dom = dump_dom("http://127.0.0.1:%d/static/_pl_probe.html" % PORT)
        m = re.search(r'data-pageerrors="([A-Za-z0-9+/=]*)"', dom)
        if not m:
            print("探针没跑完（data-pageerrors 缺失）")
            print(dom[:1200])
            return 2
        out = json.loads(base64.b64decode(m.group(1)).decode("utf-8"))
        print("页面检查：")
        for k, v in out["checks"].items():
            print("   %-26s %s" % (k, v))
        print()
        errs = [e for e in out["errors"] if "favicon" not in e.lower()]
        if errs:
            print("!! 页面报错 %d 条：" % len(errs))
            for e in errs[:25]:
                print("   " + e[:400])
            rc = 1
        else:
            print("√ 没有任何 JS 报错 / 警告")
        c = out["checks"]
        if c.get("RP") is not True or c.get("UNDO") is not True:
            print("!! RP / UNDO 没定义好")
            rc = 1
        for k in ("rpSecHiddenAtStart", "rpSecVisibleAfterOpen", "rpSecHiddenAfterClose"):
            if c.get(k) is not True:
                print("!! %s = %s" % (k, c.get(k)))
                rc = 1
    finally:
        if proc:
            proc.kill()
        try:
            os.unlink(probe_path)
        except OSError:
            pass
    return rc


if __name__ == "__main__":
    sys.exit(main())
