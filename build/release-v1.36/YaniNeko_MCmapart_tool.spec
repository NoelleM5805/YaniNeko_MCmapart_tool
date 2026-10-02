# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('D:/地图画工具/src/maptool/web', 'web'), ('D:/地图画工具/src/maptool/data/block_icons.png', 'data'), ('D:/地图画工具/src/maptool/data/minecraft_blocks_mapcolor.json', 'data'), ('D:/地图画工具/src/maptool/native/maptool_native.dll', 'native')]
binaries = []
hiddenimports = ['maptool.data.blockdata', 'litemapy', 'nbtlib', 'numpy', 'anyio', 'sniffio', 'h11', 'click', 'multipart', 'python_multipart']
tmp_ret = collect_all('fastapi')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('uvicorn')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('starlette')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('pydantic')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['D:/地图画工具/src/run_maptool.py'],
    pathex=['D:/地图画工具/src'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='YaniNeko_MCmapart_tool',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='YaniNeko_MCmapart_tool',
)
