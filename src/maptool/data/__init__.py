# -*- coding: utf-8 -*-
"""
生成数据目录
============

这里的东西都是脚本生成的，不要手改：

    blockdata.py      由 tools/gen_blockdata.py 从
                      minecraft_blocks_mapcolor.json + 官方 zh_cn.json
                      + 官方方块贴图 生成
    block_icons.png   方块贴图拼成的图集（16px 一格）
    minecraft_blocks_mapcolor.json
                      颜色 + 中文名原始数据（改造自工作区里的原始文件）

重新生成::

    python tools/gen_blockdata.py
"""
