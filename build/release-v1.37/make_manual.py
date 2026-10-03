# -*- coding: utf-8 -*-
import io
src = r"D:\地图画工具\packaging\使用说明.txt"
dst = r"D:\YaniNeko_MCmapart_tool\version\YaniNeko_MCmapart_tool-v1.37\使用说明.txt"
text = io.open(src, encoding="utf-8").read()
text = text.replace("Pictomapart", "YaniNeko_MCmapart_tool")
text = text.replace("__VERSION__", "1.37")
text = text.replace(
    "        单文件程序，双击即可运行（首次启动要解包，约 2~8 秒）。",
    "        多文件程序，双击即可运行；请保持 _internal 文件夹与主程序在同一目录。")
for nl in ("\r\n", "\n"):
    needle = "        同目录另外还有：" + nl + "            使用说明.txt"
    repl = "        同目录另外还有：" + nl + "            _internal\\          运行库与资源文件夹（不能删除、不能改名）" + nl + "            使用说明.txt"
    if needle in text:
        text = text.replace(needle, repl)
        break
io.open(dst, "w", encoding="utf-8", newline="").write(text)
print("manual written")