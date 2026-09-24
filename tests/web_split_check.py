# -*- coding: utf-8 -*-
"""
前端解耦验证
============

三项检查：

1. 静态等价（逐文件比对 token）
   拆出来的每个 js/*.js，token 流必须和它对应的原始行区间完全一致。
   token 流一致 = 没有漏代码 / 多代码 / 改代码；只有缩进被忽略，
   字符串和模板字符串整体作为一个 token 原样比对。
   css/style.css 同理，body 结构逐字比对。
   区间表直接取自 tools/split_frontend.py，两边同一份。

2. 跨文件提升
   原来所有代码在一个 <script> 里，函数声明会整体提升；拆成多个 <script>
   之后不再跨文件提升。这里扫出「某个文件的顶层语句引用了后面文件才声明的
   名字」—— 那会在加载时直接 ReferenceError。

3. 实际渲染
   两个服务各起一份页面，用无头 Edge 导出最终 DOM 做对比。
   页面会开一条 SSE 保活长连接，会让 --virtual-time-budget 永远等下去，
   所以中间加了一层代理，把 /api/keepalive 直接 204 掉。

用法（工作区根目录）：
    python tests/web_split_check.py
"""
import http.client
import importlib.util
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "src", "maptool", "web")
JS_DIR = os.path.join(WEB, "js")
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

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
OLD_PORT, NEW_PORT = 8811, 8812
OLD_PROXY, NEW_PROXY = 8821, 8822

# 拆分之后又**有意**加了功能（局部噪点修正）的文件。
# 这些文件要求「原有 token 流是完整子序列」—— 只许加，不许删改原有代码；
# 其余文件仍然要求 token 完全一致。
MODIFIED_JS = {"40-palette.js", "70-mapart.js"}

PASS, FAIL = [], []


def is_subsequence(small, big):
    """small 是不是 big 的子序列（顺序保持一致，中间可以插东西）。"""
    it = iter(big)
    return all(any(x == y for y in it) for x in small)


def _load_splitter():
    p = os.path.join(ROOT, "tools", "split_frontend.py")
    spec = importlib.util.spec_from_file_location("split_frontend", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  [√] " if ok else "  [×] ") + name + (("  " + detail) if detail and not ok else ""))


# =============================================================== token
def js_tokens(src):
    """
    切成 token；字符串/模板字符串整体作为一个 token 原样保留。

    `/` 既可能是除号也可能是正则开头，按「前一个 token 是不是值」来判断：
    值之后是除号，其余是正则。不处理这条会踩到代码里 /[\\\\/:*?"<>|]/ 这类
    正则里的引号，把后面一大段代码误当成字符串。
    """
    VALUE_END = re.compile(r"^([A-Za-z_$][\w$]*|\d[\w.]*|'|\"|`|\)|\]|\})")
    KEYWORDS_NOT_VALUE = set("return typeof case in of new delete void do else".split())
    toks = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c in " \t\r\n":
            i += 1
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if c == "/":
            prev = toks[-1] if toks else ""
            is_div = bool(VALUE_END.match(prev)) and prev not in KEYWORDS_NOT_VALUE
            if not is_div:
                # 正则字面量：吞到未转义的 / 为止，后面的 flags 一起带走
                j = i + 1
                in_class = False
                while j < n:
                    if src[j] == "\\":
                        j += 2
                        continue
                    if src[j] == "[":
                        in_class = True
                    elif src[j] == "]":
                        in_class = False
                    elif src[j] == "/" and not in_class:
                        j += 1
                        while j < n and src[j].isalpha():
                            j += 1
                        break
                    elif src[j] == "\n":
                        break
                    j += 1
                toks.append(src[i:j])
                i = j
                continue
        if c in "'\"`":
            j = i + 1
            while j < n:
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == c:
                    j += 1
                    break
                j += 1
            toks.append(src[i:j])
            i = j
            continue
        m = re.match(r"[A-Za-z_$][\w$]*", src[i:])
        if m:
            toks.append(m.group(0))
            i += len(m.group(0))
            continue
        m = re.match(r"\d[\w.]*", src[i:])
        if m:
            toks.append(m.group(0))
            i += len(m.group(0))
            continue
        toks.append(c)
        i += 1
    return toks


def css_tokens(src):
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    return re.findall(r"[A-Za-z_\-][\w\-]*|#[0-9A-Fa-f]{3,8}|[^\s]", src)


def first_diff(a, b):
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return "第 %d 个：%r != %r" % (i, x, y)
    if len(a) != len(b):
        return "长度 %d != %d" % (len(a), len(b))
    return ""


def static_checks(splitter):
    lines = splitter.load_original()
    for name, spans, _desc in splitter.JS_SECTIONS:
        orig = splitter.span_text(lines, spans)
        path = os.path.join(JS_DIR, name)
        if not os.path.isfile(path):
            check("%s 存在" % name, False, path)
            continue
        body = "\n".join(io.open(path, encoding="utf-8").read().split("\n")[3:])
        ta, tb = js_tokens(orig), js_tokens(body)
        if name in MODIFIED_JS:
            # 拆分之后又**有意**加了功能（局部噪点修正）。这些文件必须是
            # 「原文 token 流作为子序列完整保留」—— 只许加，不许删改原有代码。
            ok = is_subsequence(ta, tb)
            check("%s 保留了原有全部代码（新增 %d 个 token）"
                  % (name, len(tb) - len(ta)), ok,
                  "" if ok else "原有 token 流不是新文件的子序列")
        else:
            check("%s token 一致（%d 个）" % (name, len(ta)), ta == tb, first_diff(ta, tb))

    css_orig = "\n".join(lines[splitter.STYLE[0] - 1:splitter.STYLE[1]])
    css_new = io.open(os.path.join(WEB, "css", "style.css"), encoding="utf-8").read()
    ca, cb = css_tokens(css_orig), css_tokens(css_new.split("*/", 1)[1])
    check("style.css 保留了原有全部样式（%d -> %d 个 token）" % (len(ca), len(cb)),
          is_subsequence(ca, cb), first_diff(ca, cb))

    body_orig = "\n".join(lines[splitter.BODY[0] - 1:splitter.BODY[1]])
    html = io.open(os.path.join(WEB, "index.html"), encoding="utf-8").read()
    bo = [t for t in re.split(r"(\s+)", body_orig) if t.strip()]
    bn = [t for t in re.split(r"(\s+)", html) if t.strip()]
    check("body 结构保留了原有全部标签（%d -> %d 个片段）" % (len(bo), len(bn)),
          is_subsequence(bo, bn), "原有标签序列不是新页面的子序列")


# =============================================================== 提升
DECL_RE = re.compile(r"^(?:async\s+)?function\s+([A-Za-z_$][\w$]*)"
                     r"|^(?:const|let|var|class)\s+([A-Za-z_$][\w$]*)", re.M)
STR_RE = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"|`(?:[^`\\]|\\.)*`")
KEYWORDS = set("""if else for while do return function var let const new typeof
instanceof in of this null true false undefined try catch finally throw switch case
break continue delete void async await yield class extends super""".split())


def top_level_names(src):
    out = set()
    for m in DECL_RE.finditer(src):
        out.add(m.group(1) or m.group(2))
    return out


def hoist_check():
    names = sorted(n for n in os.listdir(JS_DIR) if n.endswith(".js"))
    srcs = {n: io.open(os.path.join(JS_DIR, n), encoding="utf-8").read() for n in names}
    declared = {n: top_level_names(srcs[n]) for n in names}
    later = {}
    for i, n in enumerate(names):
        s = set()
        for m in names[i + 1:]:
            s |= declared[m]
        later[n] = s

    bad = []
    for n in names:
        depth = 0
        for ln in srcs[n].split("\n"):
            s = STR_RE.sub("''", ln)
            s = re.sub(r"//.*$", "", s)
            if depth == 0:
                for m in re.finditer(r"\b([A-Za-z_$][\w$]*)\b", s):
                    nm = m.group(1)
                    if nm in later[n] and nm not in declared[n] and nm not in KEYWORDS:
                        bad.append("%s 顶层引用了 %s" % (n, nm))
            depth += s.count("{") - s.count("}")
    check("没有跨文件提升隐患", not bad, "; ".join(sorted(set(bad))))


# =============================================================== 代理
class Proxy(BaseHTTPRequestHandler):
    """转发请求给真正的服务，但 /api/keepalive 直接 204（掐掉 SSE）。"""

    target = 0
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _forward(self):
        body = None
        if "Content-Length" in self.headers:
            body = self.rfile.read(int(self.headers["Content-Length"]))
        conn = http.client.HTTPConnection("127.0.0.1", self.target, timeout=30)
        hdrs = {k: v for k, v in self.headers.items()
                if k.lower() not in ("host", "connection", "accept-encoding")}
        conn.request(self.command, self.path, body=body, headers=hdrs)
        r = conn.getresponse()
        data = r.read()
        self.send_response(r.status)
        for k, v in r.getheaders():
            if k.lower() in ("transfer-encoding", "connection", "content-length"):
                continue
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        conn.close()

    def do_GET(self):
        if self.path.startswith("/api/keepalive"):
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._forward()

    do_POST = _forward
    do_HEAD = _forward


def start_proxy(target_port, listen_port):
    cls = type("P%d" % listen_port, (Proxy,), {"target": target_port})
    srv = ThreadingHTTPServer(("127.0.0.1", listen_port), cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# =============================================================== 实测
def wait_port(port):
    for _ in range(300):
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/api/palette" % port, timeout=2).read()
            return True
        except Exception:
            time.sleep(0.15)
    return False


def start_servers():
    env_old = dict(os.environ, MAPART_PORT=str(OLD_PORT), PYTHONIOENCODING="utf-8")
    env_new = dict(os.environ, MAPART_PORT=str(NEW_PORT), PYTHONIOENCODING="utf-8",
                   PYTHONPATH=SRC_DIR)
    old = subprocess.Popen([sys.executable, OLD_SCRIPT], cwd=OLD_CWD, env=env_old,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    new = subprocess.Popen([sys.executable, "-m", "maptool"], cwd=SRC_DIR, env=env_new,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not wait_port(OLD_PORT) or not wait_port(NEW_PORT):
        raise RuntimeError("服务没起来")
    return old, new


def dump_dom(url, budget=6000, timeout=150):
    prof = tempfile.mkdtemp(prefix="edgeprof_")
    try:
        cmd = [EDGE, "--headless", "--no-sandbox", "--disable-gpu",
               "--disable-extensions", "--no-first-run", "--disable-sync",
               "--disable-background-networking", "--disable-component-update",
               "--virtual-time-budget=%d" % budget,
               "--user-data-dir=" + prof, "--dump-dom", url]
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return p.stdout.decode("utf-8", "replace")
    finally:
        shutil.rmtree(prof, ignore_errors=True)


def strip_div_by_id(dom, elem_id):
    """
    删掉某个 <div id="..."> 连同它的内容（按 <div> 嵌套深度配对，不用正则硬扛）。

    之所以要删 #pal-list：调色板后来有意新增了砂轮和冰，方块列表的内容和顺序
    必然和旧版不同 —— 那部分由 tests/regression_check.py（带显式白名单）
    和 tools/check_icons.py（逐格比对贴图）负责验证。
    这个测试只管「前端拆分是否忠实」，所以把调色板列表摘掉再比 DOM。
    """
    marker = 'id="%s"' % elem_id
    i = dom.find(marker)
    if i < 0:
        return dom
    # 回退到该属性所在标签的 '<'
    start = dom.rfind("<", 0, i)
    if start < 0:
        return dom
    j = dom.find(">", i)
    if j < 0:
        return dom
    depth = 1
    k = j + 1
    while k < len(dom) and depth > 0:
        nxt_open = dom.find("<div", k)
        nxt_close = dom.find("</div", k)
        if nxt_close < 0:
            break
        if 0 <= nxt_open < nxt_close:
            depth += 1
            k = nxt_open + 4
        else:
            depth -= 1
            k = nxt_close + 5
    end = dom.find(">", k)
    return dom[:start] + dom[end + 1:]


def normalize_dom(dom, strip_palette_list=True):
    if strip_palette_list:
        dom = strip_div_by_id(dom, "pal-list")
    # 「局部噪点修正」是拆分之后有意新增的功能，旧版当然没有；
    # 比对旧/新版一致性的目的在别处，这里把新增的部分摘掉再比。
    # 注意顺序：必须在删注释**之前**摘，否则两个标记注释先被删掉就找不到了。
    dom = re.sub(r"<!--\s*=+\s*局部噪点修正\s*=+\s*-->.*?<!--\s*/局部噪点修正\s*-->",
                 "", dom, flags=re.S)
    dom = re.sub(r"<canvas\b[^>]*>.*?</canvas>", "", dom, flags=re.S | re.I)
    # 说明性注释（旧版单文件没有），比对时忽略
    dom = re.sub(r"<!--.*?-->", "", dom, flags=re.S)
    dom = re.sub(r"<style\b[^>]*>.*?</style>", "", dom, flags=re.S | re.I)
    dom = re.sub(r"<script\b[^>]*>.*?</script>", "", dom, flags=re.S | re.I)
    dom = re.sub(r"<link\b[^>]*>", "", dom, flags=re.I)
    dom = re.sub(r"/api/icons\.png\?v=[\d\-]+", "/api/icons.png?v=X", dom)
    # 图标贴图集的行数由方块数决定，新增 2 个方块后 18 -> 19 行，是预期的变化。
    # 具体数值单独比（见下面 dynamic_check 里的断言）。
    dom = re.sub(r"--icon-rows:\s*\d+", "--icon-rows: X", dom)
    # 方块总数随调色板变化（288 -> 290，新增砂轮 + 冰），单独断言
    dom = re.sub(r"/\d+ 个方块", "/N 个方块", dom)
    # 图标在贴图集里的格子坐标：新增 2 个方块后整表重排，坐标必然变。
    # 坐标的正确性由 tools/check_icons.py 逐格比对像素来保证。
    dom = re.sub(r"calc\(var\(--icon-px\) \* -?\d+\) calc\(var\(--icon-px\) \* -?\d+\)",
                 "ICONPOS", dom)
    return re.sub(r"\s+", " ", dom).strip()


def dynamic_check():
    if not os.path.isfile(EDGE):
        check("无头 Edge 可用", False, EDGE)
        return
    old_proc = new_proc = None
    proxies = []
    try:
        old_proc, new_proc = start_servers()
        proxies.append(start_proxy(OLD_PORT, OLD_PROXY))
        proxies.append(start_proxy(NEW_PORT, NEW_PROXY))
        old_raw = dump_dom("http://127.0.0.1:%d/" % OLD_PROXY)
        new_raw = dump_dom("http://127.0.0.1:%d/" % NEW_PROXY)
        a = normalize_dom(old_raw)
        b = normalize_dom(new_raw)
        check("渲染后的 DOM 完全一致（%d 字符）" % len(a), a == b)

        # 图标贴图集行数在 normalize 里被抹成 X 了，用原始 DOM 单独比一次
        ra = re.search(r"--icon-rows:\s*(\d+)", old_raw)
        rb = re.search(r"--icon-rows:\s*(\d+)", new_raw)
        if ra and rb:
            check("图标贴图集行数按预期增加（%s -> %s）"
                  % (ra.group(1), rb.group(1)),
                  int(rb.group(1)) == int(ra.group(1)) + 1)

        # 方块总数：288 -> 290（新增砂轮 + 冰）
        na = re.search(r"已选 \d+/(\d+) 个方块", old_raw)
        nb = re.search(r"已选 \d+/(\d+) 个方块", new_raw)
        if na and nb:
            check("方块总数按预期增加（%s -> %s）" % (na.group(1), nb.group(1)),
                  int(nb.group(1)) == int(na.group(1)) + 2)

        if a != b:
            n = min(len(a), len(b))
            k = next((i for i in range(n) if a[i] != b[i]), n)
            print("      首个差异位置 %d：" % k)
            print("        旧:", repr(a[max(0, k - 120):k + 200]))
            print("        新:", repr(b[max(0, k - 120):k + 200]))
        else:
            # 这些都在 #pal-list 里（对比时被摘掉了），所以在**原始**新页面 DOM 上查。
            # 方块数 288 -> 290：有意新增砂轮 + 冰。
            for probe, least in (("pgroup", 59), ("pblock", 290),
                                 ("pal-icon", 290)):
                have = new_raw.count(probe)
                check("新页面含 %s × %d" % (probe, least), have >= least,
                      "实际 %d" % have)
            # 不属于调色板列表的，仍在新旧共有的 DOM 上查
            for probe, least in (("face-item", 6), ("pal-alloc", 1),
                                 ("zoom-hint", 2)):
                have = a.count(probe)
                check("渲染结果含 %s × %d" % (probe, least), have >= least,
                      "实际 %d" % have)
            # 有意新增的修正面板（画布要上传图片后才有，见 tests/repair_web_check.py）
            for probe, least in (('id="rp-sec"', 1), ('class="rp-tab', 2),
                                 ('id="rp-level"', 1), ('id="rp-pal"', 1),
                                 ('class="rp-chip', 59)):
                have = new_raw.count(probe)
                check("新页面含 %s × %d" % (probe, least), have >= least,
                      "实际 %d" % have)
    finally:
        for p in (old_proc, new_proc):
            if p:
                p.kill()
        for s in proxies:
            s.shutdown()


def main():
    splitter = _load_splitter()
    print("1) 静态等价（逐文件比对 token）")
    static_checks(splitter)
    print("2) 跨文件提升检查")
    hoist_check()
    print("3) 实际渲染对比")
    dynamic_check()
    print("\n" + "=" * 60)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("  失败：" + f)
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
