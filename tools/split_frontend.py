# -*- coding: utf-8 -*-
"""
把单文件 index.html 拆成 index.html + css/style.css + js/*.js
================================================================

这是一次性的迁移脚本，留着是为了说明拆分口径（哪个文件对应原来的哪些行），
以及给 tests/web_split_check.py 提供同一份区间表做验证。

要求：拆出来的代码必须和原来逐字等价。

做法：
  1. 用一个小的 JS 扫描器标出「行首位于字符串 / 模板字符串 / 块注释内部」的行，
     这些行绝对不能动缩进（模板字符串里的空白是内容的一部分）。
  2. 其余行去掉行首 8 个空格（原来是嵌在 <script> 里，多一层缩进）。
  3. 反向验证：把去掉的缩进补回去，必须和原文逐字一致，否则直接报错退出。

用法：
    python tools/split_frontend.py            # 从 legacy 单文件版拆
    python tools/split_frontend.py --check    # 只做验证，不写文件
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "src", "maptool", "web")


def _find_source():
    """原始单文件版 index.html：归档到 legacy/ 之后也能找到。"""
    for rel in (("legacy", "MCmapart_tool", "index.html"),
                ("MCmapart_tool", "index.html")):
        p = os.path.join(ROOT, *rel)
        if os.path.isfile(p):
            return p
    return os.path.join(ROOT, "legacy", "MCmapart_tool", "index.html")


SRC = _find_source()

# 行号是原始单文件 index.html 里的 1-based 区间（含两端）。
# 顺序 = 新的 <script> 加载顺序 = 顶层语句执行顺序。
JS_SECTIONS = [
    ("00-util.js", [(2019, 2146)], "通用工具：$ / 徽标 / 拖放 / 保存 / 任务轮询"),
    ("10-modal.js", [(2147, 2288)], "预览图放大模态框（滚轮缩放 / 拖动）"),
    ("20-config.js", [(2617, 2688)], "配置持久化 + 主题（黑夜模式）"),
    ("30-adjust.js", [(2689, 2738)], "图片调整 12 项"),
    ("40-palette.js", [(2739, 3158)], "方块选择面板（按颜色分组 / 预设 / 置顶）"),
    ("50-usage.js", [(3159, 3216)], "底部方块用量统计"),
    ("60-settings.js", [(3217, 3295), (3296, 3328)], "设置面板 + 表单值 ⇄ 配置"),
    ("70-mapart.js", [(2289, 2616)], "地图画生成：上传 / 实时预览 / 生成 / 保存"),
    ("80-slice.js", [(3329, 3450)], "投影切分"),
    ("85-lichen.js", [(3451, 3565)], "Glow Lichen 面属性批量修改"),
    ("90-init.js", [(3566, 3592)], "初始化：读配置、把配置贴到界面上"),
    ("92-focus.js", [(3593, 3785)], "自由预览（专注模式）"),
    ("99-start.js", [(3786, 3789)], "启动：进入专注模式、拉调色板、连保活"),
]

# 原始文件里各块的边界（1-based，含两端）
HEAD = (1, 7)
STYLE = (9, 1493)
BODY = (1497, 2017)

INDENT = 8


def protected_lines(text):
    """
    返回「行首处在字符串/模板/块注释里」的行号集合（0 开始）。

    扫描器状态栈：CODE 普通代码 · SQ/DQ 引号串 · TPL 模板串 · LC 行注释 · BC 块注释
    注意：换行必须先让 LC 出栈，否则行注释会一直吞到文件结尾。
    """
    prot = set()
    stack = ["CODE"]
    i, n = 0, len(text)
    line = 0
    at_line_start = True
    while i < n:
        st = stack[-1]
        c = text[i]
        nxt = text[i + 1] if i + 1 < n else ""

        if c == "\n":
            if st == "LC":
                stack.pop()
            line += 1
            at_line_start = True
            i += 1
            continue

        if at_line_start:
            if stack[-1] in ("SQ", "DQ", "TPL", "BC"):
                prot.add(line)
            at_line_start = False

        if st == "LC":
            i += 1
            continue
        if st == "BC":
            if c == "*" and nxt == "/":
                stack.pop()
                i += 2
                continue
            i += 1
            continue
        if st in ("SQ", "DQ"):
            if c == "\\":
                i += 2
                continue
            if (st == "SQ" and c == "'") or (st == "DQ" and c == '"'):
                stack.pop()
            i += 1
            continue
        if st == "TPL":
            if c == "\\":
                i += 2
                continue
            if c == "`":
                stack.pop()
                i += 1
                continue
            if c == "$" and nxt == "{":
                stack.append("CODE")
                i += 2
                continue
            i += 1
            continue

        if c == "/" and nxt == "/":
            stack.append("LC"); i += 2; continue
        if c == "/" and nxt == "*":
            stack.append("BC"); i += 2; continue
        if c == "'":
            stack.append("SQ"); i += 1; continue
        if c == '"':
            stack.append("DQ"); i += 1; continue
        if c == "`":
            stack.append("TPL"); i += 1; continue
        if c == "}" and len(stack) > 1 and stack[-2] == "TPL":
            stack.pop(); i += 1; continue
        i += 1
    return prot


def dedent_text(text, indent=INDENT, protect=True):
    """去掉行首缩进，返回 (新文本, 补回缩进用的 [(行号, 空格数)])。"""
    lines = text.split("\n")
    prot = protected_lines(text) if protect else set()
    out, restored = [], []
    for idx, ln in enumerate(lines):
        if idx in prot:
            out.append(ln)
            continue
        k = 0
        while k < indent and k < len(ln) and ln[k] == " ":
            k += 1
        if k:
            restored.append((idx, k))
        out.append(ln[k:])
    return "\n".join(out), restored


def span_text(lines, spans):
    return "\n".join("\n".join(lines[a - 1:b]) for a, b in spans)


def load_original():
    raw = io.open(SRC, encoding="utf-8").read()
    lines = raw.split("\n")
    assert lines[0] == "<!DOCTYPE html>", lines[0]
    assert lines[STYLE[0] - 2].strip() == "<style>", lines[STYLE[0] - 2]
    assert lines[STYLE[1]].strip() == "</style>", lines[STYLE[1]]
    assert lines[BODY[0] - 1].strip() == "<body>", lines[BODY[0] - 1]
    return lines


def build():
    """返回 (css 文本, {js 文件名: 正文}, html 文本)。"""
    lines = load_original()

    css_src = "\n".join(lines[STYLE[0] - 1:STYLE[1]])
    css, _ = dedent_text(css_src, protect=False)

    js = {}
    for name, spans, _desc in JS_SECTIONS:
        for a, b in spans:
            orig = "\n".join(lines[a - 1:b])
            body, restored = dedent_text(orig)
            back = body.split("\n")
            for idx, k in restored:
                back[idx] = " " * k + back[idx]
            if "\n".join(back) != orig:
                raise SystemExit("!! 缩进往返不一致: %s %d-%d" % (name, a, b))
        js[name] = span_text(lines, spans).strip("\n")

    head = "\n".join(lines[HEAD[0] - 1:HEAD[1]])
    body_html = "\n".join(lines[BODY[0] - 1:BODY[1]])
    scripts = "\n".join('    <script src="/static/js/%s"></script>' % n
                        for n, _s, _d in JS_SECTIONS)
    html = (
        head + "\n"
        + '    <link rel="stylesheet" href="/static/css/style.css">\n'
        + "</head>\n\n"
        + body_html + "\n"
        + "\n<!-- 前端脚本按顺序加载，共享同一个全局作用域（不用 ES module，本地打开也能跑） -->\n"
        + scripts + "\n"
        + "</body>\n\n</html>\n"
    )
    return css, js, html


def main():
    check_only = "--check" in sys.argv
    if not os.path.isfile(SRC):
        raise SystemExit("找不到原始单文件：%s" % SRC)
    css, js, html = build()
    if check_only:
        print("缩进往返验证：通过（未写文件）")
        return
    os.makedirs(os.path.join(WEB, "css"), exist_ok=True)
    os.makedirs(os.path.join(WEB, "js"), exist_ok=True)

    with io.open(os.path.join(WEB, "css", "style.css"), "w", encoding="utf-8",
                 newline="\n") as f:
        f.write("/* 地图画工具箱 - 样式\n"
                "   从 index.html 的 <style> 块拆出，选择器和声明原样保留。 */\n\n"
                + css.strip("\n") + "\n")

    for name, _spans, desc in JS_SECTIONS:
        with io.open(os.path.join(WEB, "js", name), "w", encoding="utf-8",
                     newline="\n") as f:
            f.write("// %s\n// %s\n\n" % (name.replace(".js", ""), desc))
            f.write(js[name] + "\n")

    with io.open(os.path.join(WEB, "index.html"), "w", encoding="utf-8",
                 newline="\n") as f:
        f.write(html)

    print("缩进往返验证：通过")
    print("CSS 行数 :", len(css.split("\n")))
    print("HTML 行数:", len(html.split("\n")))
    print("JS 文件  :", len(js), "合计", sum(len(v.split("\n")) for v in js.values()), "行")
    for name, _s, desc in JS_SECTIONS:
        print("   %-16s %4d 行  %s" % (name, len(js[name].split("\n")), desc))


if __name__ == "__main__":
    main()
