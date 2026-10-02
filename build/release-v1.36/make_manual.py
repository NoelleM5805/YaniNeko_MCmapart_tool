# -*- coding: utf-8 -*-
import io
src = r"D:\地图画工具\packaging\使用说明.txt"
dst = r"D:\YaniNeko_MCmapart_tool\version\YaniNeko_MCmapart_tool-v1.36\使用说明.txt"
text = io.open(src, encoding="utf-8").read()
text = text.replace("Pictomapart", "YaniNeko_MCmapart_tool")
text = text.replace("__VERSION__", "1.36")
old = (
    "        单文件程序，双击即可运行（首次启动要解包，约 2~8 秒）。\r\n"
    "        同目录另外还有：\r\n"
    "            使用说明.txt        就是本文件\r\n"
)
new = (
    "        多文件程序，双击即可运行；请保持 _internal 文件夹与主程序在同一目录。\r\n"
    "        同目录另外还有：\r\n"
    "            _internal\\          运行库与资源文件夹（不能删除、不能改名）\r\n"
    "            使用说明.txt        就是本文件\r\n"
)
if old in text:
    text = text.replace(old, new)
else:
    text = text.replace(
        "        单文件程序，双击即可运行（首次启动要解包，约 2~8 秒）。",
        "        多文件程序，双击即可运行；请保持 _internal 文件夹与主程序在同一目录。")
    text = text.replace(
        "        同目录另外还有：\r\n            使用说明.txt        就是本文件\r\n",
        "        同目录另外还有：\r\n            _internal\\          运行库与资源文件夹（不能删除、不能改名）\r\n            使用说明.txt        就是本文件\r\n")
io.open(dst, "w", encoding="utf-8", newline="").write(text)
print(dst)