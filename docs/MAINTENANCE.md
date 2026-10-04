# 长期维护指南

> Pictomapart · 地图画工具箱 —— 维护手册
>
> 适用范围：接手这个仓库、做例行维护、准备发新版时的操作手册。
> 配套阅读：`README.md`（功能与用法）、`ARCHITECTURE.md`（架构与设计取舍）。

---

## 0. 一页速览

| 想做什么 | 命令（在仓库根目录执行） |
| --- | --- |
| 快速健康自检 | `python tools\build.py --check` |
| 自检图标/方块数据 | `python tools\check_icons.py` |
| 自检 C++ 工具链 | `python tools\build_native.py --check` |
| 后端回归（不依赖浏览器/旧版） | `python tests\repair_check.py` 和 `python tests\slice_check.py` |
| 完整回归 | 见 §3「完整回归」 |
| 从源码跑 | `python src\run_maptool.py` |
| 打包发行版 | `python tools\build.py` |
| 改版本号 | 改 `src/maptool/__init__.py` 的 `__version__` |

---

## 1. 项目是什么、怎么跑

一个**本地网页工具**（后端 FastAPI + 前端原生 JS，服务只监听 `127.0.0.1`），
把 Minecraft 地图画相关的三件事打包在一起：图片转 `.litematic`、局部噪点修正、
投影切分，外加 Glow Lichen 面属性批量修改和全局撤回。

- **源码运行**：`pip install fastapi uvicorn litemapy pillow numpy python-multipart` 后
  `python src\run_maptool.py`。端口被占会自动向后找空闲端口（可用 `MAPART_PORT` 指定起始端口）。
- **打包运行**：`python tools\build.py` 产出 `dist\YaniNeko_MCmapart_tool\`，双击里面的 exe 即可。

行为契约（改了会破坏用户的既有预期，改动前先看 `README.md` 对应章节）：

- 地图画默认输出：地面朝向（XZ）· 厚度 1 · 无底板。
- 只监听 `127.0.0.1`，不上局域网。
- 页面全关后约 15 秒进程自动退出（可在「设置」里关闭）。

---

## 2. 目录结构与「什么能删、什么不能删」

```
地图画工具/
├─ src/                 源码（Python 后端 + web 前端）
│  ├─ run_maptool.py    打包/运行入口
│  └─ maptool/          后端包（模块职责见 ARCHITECTURE.md）
│     ├─ data/          方块表 + 图标贴图集（生成物，但运行时依赖）
│     ├─ native/        C++ 抖动核心（dither_core.cpp + 编译出的 DLL）
│     └─ web/           前端（index.html + css + js，按编号顺序加载）
├─ tools/               开发脚本（不参与运行）
├─ tests/               回归测试
├─ docs/                架构说明 + 本维护指南
├─ packaging/           发行版说明文件源（使用说明.txt）
├─ legacy/              历史版本留档 + 回归测试的「旧版基准」
├─ build/               打包中间产物（PyInstaller 每次都会先清掉重建）
├─ dist/                发行版输出（整个可以拷给别人）
└─ .gitignore
```

**可随时删除、会被重新生成（已经进 .gitignore）：**

| 目录/文件 | 由谁重新生成 |
| --- | --- |
| `build/` | `tools\build.py`（PyInstaller 中间产物） |
| `dist/` | `tools\build.py`（发行版输出） |
| `__pycache__/`、`*.pyc` | Python 运行时 |
| `tests\_tmp/` | 各测试脚本（测试临时产物） |
| `tools\_build_cache/` | `tools\gen_blockdata.py`（贴图下载缓存） |
| `*.log` | 构建日志 |

**必须保留（删了会出事）：**

| 目录/文件 | 为什么 |
| --- | --- |
| `src/maptool/data/blockdata.py`、`block_icons.png` | 运行时直接读取；是**生成物，不要手改**，改它要走 §5 的流程 |
| `src/maptool/native/maptool_native.dll` | 可选的 C++ 核心；跟踪它让新 clone 不装 MinGW 也开箱即用。重编走 §6 |
| `legacy/` | 历史留档 + `regression_check.py` / `exe_check.py` 的「旧版单文件基准」 |
| `packaging/使用说明.txt` | 打包时复制进发行版并替换程序名/版本号 |

---

## 3. 例行维护清单

### 3.1 健康自检（改完代码先跑这三条，都是只读、秒级）

```bat
python tools\build.py --check          :: 资源齐全 + 包可导入 + 路由/原生核状态
python tools\check_icons.py            :: 方块表 + 图标贴图集逐格比对
python tools\build_native.py --check   :: 编译器/源码/DLL 状态
```

三条都应**退出码 0**。`build.py --check` 末尾会打印
`NN routes; native on/off`——`native off` 不是错误，只是说明没有 DLL、
会退回纯 Python 实现（结果一样、慢一些）。

### 3.2 后端回归（快，不依赖浏览器/旧版）

```bat
python tests\repair_check.py     :: 局部噪点修正：功能与不变量（55 项）
python tests\slice_check.py      :: 投影切分：逐方块映射 + 字节等价（40 项，稍慢）
```

### 3.3 完整回归（改到后端 API / 前端 / 打包时跑）

```bat
python tests\regression_check.py     # 后端：新旧两服务逐项比对（13 项）
python tests\web_split_check.py      # 前端：拆分等价 + 无头浏览器渲染（30 项）
python tests\slice_web_check.py      # 投影切分：浏览器端完整流程（32 项）
python tests\native_match_equiv.py   # C++ 匹配器：全 1677 万颜色逐位比对（较慢）
python tests\dither_native_equiv.py  # C++ 抖动：900 例端到端逐像素比对
python tests\repair_web_check.py     # 局部噪点修正 + 撤回：浏览器端实测（36 项）
python tests\page_load_check.py      # 页面加载自检
```

说明：

- `regression_check.py` 会同时起**旧版（legacy）和新版**两个服务来比对；`.litematic` 带
  `TimeCreated`/`TimeModified` 时间戳，比对前会先把时间戳归零再比解压内容。
- 各测试「项数」只是粗略参考，会随用例增长而漂移，**以实际运行为准**（本机当前
  repair 55 项、slice 40 项）。
- `*_web_*.py` 需要无头 Edge 浏览器；其中凡是用 `--virtual-time-budget` 的测试，
  必须在 `<head>` 里就把 `EventSource` 打桩，否则 SSE 保活长连接会让它永远等下去。
- 改动过 C++ 核心（`dither_core.cpp`）后，`native_match_equiv.py` 和
  `dither_native_equiv.py` 是**必须**跑的——它们是「C++ 与 Python 结果逐像素一致」的证明。

### 3.4 清理垃圾（随手做，不影响构建/运行）

```bat
git clean -ndX          :: 先看 .gitignore 里会清掉什么（-n 只预览）
git clean -fdX          :: 真清（只清被忽略的可再生物，不碰 tracked 文件）
```

或者手动删：`build\`、`dist\`、各 `__pycache__\`、`tests\_tmp\`。

---

## 4. 发布流程

版本号是唯一真相来源：`src/maptool/__init__.py` 的 `__version__`。
打包脚本会把它带进程序名、`使用说明.txt` 和 zip 文件名。

```bat
1. 改版本号       编辑 src\maptool\__init__.py 的 __version__（如 "1.36"）
2. 跑完整回归     python tests\regression_check.py 等（见 §3.3）
3. 打包前检查     python tools\build.py --check
4. 打包           python tools\build.py            :: 产出 dist\YaniNeko_MCmapart_tool\ + zip
5. 验证发行版     python tests\exe_check.py        :: 拿旧版当基准跑一遍 + 查资源/越界/端口
```

注意：

- `build.py` 打包前会**自动结束同名的旧 exe 进程**（文件被占用没法覆盖）。
- `dist\` 下别的发行版不会被删，只有同名那一个目录会重建。
- zip 是「顺手方便件」，不是必须交付物：刚写完的大文件常被 Windows Defender /
  搜索索引器占住几秒，删不掉也改不了名，脚本会自动写成 `<名字>_<版本>_new.zip`
  并只警告不失败。等占用方松手再重跑一次就回到正常文件名。
- 交付物是那个**目录**，zip 只是打包形式。

---

## 5. 更新方块表 / 图标贴图集

方块表和贴图集是**生成物，不要手改**。更新 Minecraft 版本、或官方方块/贴图变化时：

```bat
python tools\gen_blockdata.py    :: 从 minecraft_blocks_mapcolor.json + 官方 zh_cn.json + 官方贴图重新生成
python tools\check_icons.py      :: 自检：坐标无重复无越界、逐格贴图对得上
```

- 贴图缺省会从 `raw.githubusercontent.com` 按需下载并缓存到 `tools\_build_cache\`。
- 可用 `--textures <目录>` 指定本地贴图目录，或 `--offline` 只用本地缓存。
- 贴图集内容一变，`/api/icons.png` 的地址上挂着 `?v=<mtime>-<size>` 指纹会自动失效；
  用户端若仍错位是浏览器缓存，Ctrl+F5 即可。

改方块表 / 贴图集之后，**必须**重跑 §3.2 的 `slice_check.py` 和 §3.3 里相关回归，
因为方块数量/颜色组数变化会牵动调色板、匹配、统计等一连串下游。

---

## 6. 重编 C++ 抖动核心

`src/maptool/native/dither_core.cpp` → `maptool_native.dll`（`ctypes` 调用，可选）。

```bat
python tools\build_native.py           :: 编译（源码没变会跳过）
python tools\build_native.py --force   :: 强制重编
python tools\build_native.py --check   :: 只检查工具链
```

- 需要 MinGW-w64 的 `g++`（本机在 `D:\mingw64\bin`，脚本也会找常见安装位置）。
- **编译参数必须带 `-static`**：这个 g++ 是 posix 线程模型，不带会依赖
  `libwinpthread-1.dll`，加载时报 "Could not find module ... or its dependencies"。
- 为什么是「普通 C DLL + ctypes」而不是 Python 扩展，见 `README.md`「C++ 抖动核心」。
- 改完源码后：跑 `build_native.py --force`，再跑 §3.3 里两个 native 等价测试。

---

## 7. 环境与依赖要求

| 项 | 要求 |
| --- | --- |
| Python | 3.13 / 3.14（本机 3.14） |
| numpy | **≥ 2.1**（1.26.x 不支持 Py3.13+，网传 MINGW 构建会 `import` 直接崩溃） |
| 运行依赖 | fastapi、uvicorn、litemapy、pillow、numpy、python-multipart |
| 打包依赖 | pyinstaller |
| 可选工具链 | MinGW-w64 g++（只用于重编 C++ 核心） |

检查：`python -c "import numpy; print(numpy.__version__)"`

---

## 8. 已知坑（Windows 特定）

| 现象 | 原因 / 处理 |
| --- | --- |
| `UnicodeEncodeError: 'gbk' codec can't encode '\u2713'` | 中文 Windows 默认代码页是 GBK，`✓ ✗ ⚠` 不在 GBK 里。运行时有 `runtime._setup_console()` 兜底；开发脚本（如 `check_icons.py`）也已加 `errors="replace"` 兜底。**新写的脚本若要 print 这些符号，记得做同样的兜底。** |
| 刚打包完删不掉/改不了名 | Windows Defender / 搜索索引器占住新写的大文件，稍等重试即可（`build.py` 已带重试与换名兜底） |
| `RuntimeError: lost sys.stdin` | `--windowed` 打包后无控制台；`safe_pause()` 已兜底，别再用裸 `input()` |
| uvicorn 报 `Unable to configure formatter 'default'` | 无控制台时 `sys.stdout.isatty()` 是 None；`_NullWriter` + `use_colors=stdout_is_tty()` 已处理 |
| 端口被占 | 自动从 8765 向后找空闲端口（上限 64 个），无需手动换 |
| 图标整体错位 | 浏览器缓存旧贴图集；地址带内容指纹会自动失效，仍错就 Ctrl+F5 |

---

## 9. Git 卫生与仓库瘦身

### 9.1 哪些该提交、哪些不该

- **提交**：`src/`、`tools/`、`tests/`、`docs/`、`packaging/`、`.gitignore`、`legacy/`。
- **不提交**（已进 `.gitignore`）：`build/`、`dist/`、`__pycache__/`、`*.pyc`、
  `tests/_tmp/`、`tools/_build_cache/`、`*.log`。
- **边界情况**：`src/maptool/data/`（生成物但运行时依赖）和
  `src/maptool/native/maptool_native.dll`（可选编译产物）**保持跟踪**，见 §2。

### 9.2 历史里已经提交了 build 产物，仓库很大

早期没有 `.gitignore`，把整份 PyInstaller 产物（含 `build/release-v1.3x/**/_internal/**`
上千个小文件、几十 MB 的 exe/zip）提交进了历史，导致 `.git` 目录膨胀到数百 MB。

- **已做（本次维护）**：新增 `.gitignore` 并 `git rm -r --cached` 掉这些可再生物，
  让**未来**不再增长。这只是停止跟踪，**不会**让 `.git` 历史变小。
- **想真正瘦身**（可选、谨慎）：用 `git filter-repo` 从历史中剔除这些路径后
  强制推送。这属于**改写历史**，会改所有提交的 SHA，多人协作或有外部 fork 时
  必须先协调。单人仓库可参考：
  ```bat
  git filter-repo --invert-paths --path build --path dist --path-glob '**/__pycache__/**'
  ```
  执行前先备份（`git clone --mirror`），并 `git filter-repo` 需要单独安装。

### 9.3 提交习惯

仓库现有提交信息是**日期式短信息**（如 `10.3.1029`）。维护提交建议延续可读的
简短信息，一次只做一类事（修 bug / 改文档 / 升版本），方便回溯。

---

## 10. 故障排查速查表

| 症状 | 先查 |
| --- | --- |
| `python src\run_maptool.py` 起不来 | 依赖装全了没（§7）；numpy 版本（≥2.1） |
| 打包报 `缺少 XX 文件` | `tools\build.py --check` 看哪一项缺；`src/maptool/web/js/` 是否被误删 |
| 打包报 `删不掉 ...` / PyInstaller 覆盖失败 | 关掉正在运行的旧 exe；等 Defender 扫描完重试 |
| `import maptool` 失败 | 从仓库根目录 `python src\run_maptool.py` 跑，别直接 `python src\maptool\__main__.py`（相对导入会失败） |
| 切分/匹配回归红了 | 先重跑 §3.1 三条自检；再单独重跑红的那条，确认是不是环境（浏览器/旧版基准）问题 |
| C++ 核心加载失败（`native off`） | `tools\build_native.py --check`；确认 DLL 在 `src/maptool/native/` 下 |

---

## 11. 维护检查清单（可直接复制）

### 发布前（每次发版）

- [ ] `__version__` 已更新
- [ ] `python tools\build.py --check` 退出码 0
- [ ] `python tools\check_icons.py` 退出码 0
- [ ] 后端回归 `repair_check.py` + `slice_check.py` 全绿
- [ ] 涉及后端/前端/打包时跑完 §3.3 完整回归
- [ ] `python tools\build.py` 打包成功，`dist\YaniNeko_MCmapart_tool\` 产出完整
- [ ] `python tests\exe_check.py` 通过
- [ ] README / ARCHITECTURE / MAINTENANCE 三份文档与现状一致

### 周期性（每季度或大改之后）

- [ ] 跑一遍 §3.3 完整回归（尤其两个 native 等价测试，较慢但重要）
- [ ] `git status` 干净，`.gitignore` 仍覆盖所有新出现的可再生物
- [ ] `git clean -ndX` 预览一下可清理的垃圾
- [ ] 检查依赖是否有更新影响行为（尤其 numpy / pillow / fastapi 大版本）
- [ ] 核对 `__version__` 与最近交付的发行版一致
