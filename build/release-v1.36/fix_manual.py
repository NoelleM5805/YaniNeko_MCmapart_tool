# -*- coding: utf-8 -*-
import io
p = r"D:\YaniNeko_MCmapart_tool\version\YaniNeko_MCmapart_tool-v1.36\使用说明.txt"
text = io.open(p, encoding="utf-8").read()
text = text.replace("        同目录另外还有：\r\n            使用说明.txt",
                    "        同目录另外还有：\r\n            _internal\\          运行库与资源文件夹（不能删除、不能改名）\r\n            使用说明.txt")
text = text.replace("        同目录另外还有：\n            使用说明.txt",
                    "        同目录另外还有：\n            _internal\\          运行库与资源文件夹（不能删除、不能改名）\n            使用说明.txt")
io.open(p, "w", encoding="utf-8", newline="").write(text)
print("ok")