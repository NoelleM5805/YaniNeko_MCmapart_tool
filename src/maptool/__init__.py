# -*- coding: utf-8 -*-
"""
Pictomapart —— 地图画工具箱
=============================

把 Minecraft 地图画相关的三件事打包成一个本地 Web 服务：

  1. 图片转 2D 地图画（.litematic）—— 调参实时预览、点击放大、用量统计
  2. 投影切分（.litematic → 分块 zip）
  3. Glow Lichen 面属性批量修改

模块划分（自下而上，不出现循环依赖）::

    config       常量、端口、资源目录定位
    colorspace   sRGB → CIE Lab
    palette      方块表加载、按颜色分组、每次请求的运行时调色板
    matching     6 种颜色匹配算法 + 批量匹配
    dithering    误差扩散 / 有序抖动 + 整图转换
    adjustments  12 项图像调整（曝光、对比、色散……）
    imageops     缩放、留边、拼贴、比例推荐
    schematic    .litematic 构建、方块分配、字节化、文件名清洗
    slicing      投影按网格切块
    lichen       发光地衣面属性批量替换
    keepalive    页面保活（SSE 长连接 + 无页面自动退出）
    tasks        任务表、日志、结果图缓存
    webapp       FastAPI 路由
    runtime      Windows 下的控制台 / 异常兜底
    __main__     命令行入口，main() 在这里

源码布局::

    src/maptool/          本包
    src/maptool/data/     生成的数据（blockdata.py / block_icons.png）
    src/maptool/web/      前端（index.html / css / js）
    tools/                开发脚本（生成数据、打包）
    tests/                回归测试
    dist/                 发行版
"""

__version__ = "1.1.0"
__all__ = ["__version__"]
