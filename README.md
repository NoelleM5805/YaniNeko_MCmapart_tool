# Pictomapart · 地图画工具箱

把 Minecraft 地图画相关的三件事打包成一个**本地网页工具**：
上传图片就能实时预览、点击放大、导出投影文件。

| 功能 | 说明 |
| --- | --- |
| 🎨 地图画生成 | 图片 → 2D 地图画 `.litematic`。调参实时预览、点击放大、方块用量统计、导出的文件名默认跟图片同名 |
| 🩹 局部噪点修正 | 套索圈一片区域、按 `S` 自动找出「受害者方块」、用 2~4 级强度把它们改回主色；也可以拿画笔逐块涂掉 |
| ✂️ 投影切分 | 把一个 `.litematic` 按 N×M 切成多个独立投影文件（不打包成 zip） |
| 🌿 Glow Lichen | 批量修改发光地衣的六面朝向属性 |

地图画默认输出：**地面朝向（XZ）· 厚度 1 · 无底板**。
服务只监听 `127.0.0.1`，不上局域网；页面全关掉后进程会自己退出。

---

## 局部噪点修正怎么用

抖动算法为了还原渐变会**故意**在局部掺入别的颜色。大部分时候这正是想要的噪点感，
但大片纯色、人脸、文字这些地方，你可能希望恢复成「本来想要的主色调」。

侧边栏 →「局部噪点修正」：

**套索（区域级）**

1. 在预览图上**拖拽**圈出一片区域
2. 按 <kbd>S</kbd> —— 自动识别并高亮该区域里的**受害者方块**
   （洋红色那些：本来该是主色、却被抖动掺成了别的颜色的方块）
3. 拖「修补强度」滑块选 2~4 级
4. 点「应用修正」

**画笔（像素级）**

- 按住涂抹，把被污染的方块覆盖成笔刷颜色
- **按住 <kbd>Alt</kbd> 点击**可以直接从预览图上取色（会吸附到最近的调色板颜色）
- 也可以从下面的色块列表里挑一个游戏内可用的颜色
- 笔刷半径按**成品方块数**算，所以预览被缩小过、生成是全尺寸，两处涂到的是同一块地方

快捷键：<kbd>S</kbd> 识别受害者 · <kbd>B</kbd> 画笔 · <kbd>L</kbd> 套索 · <kbd>Esc</kbd> 取消取色

**强度是什么意思**

区域内的主色 D 取出现次数最多的那个调色板颜色。对区域内每个「当前颜色 C ≠ D」的像素，
比较它到 D 的距离 `dD` 和到 C 的距离 `dC`：

| 强度 | 判定条件 | 效果 |
| --- | --- | --- |
| 2 | `dD ≤ 0.50 × dC` | 只改主色明显更合适的（近至少 2 倍），杂色感保留最多 |
| 3 | `dD ≤ 0.75 × dC` | 改主色更合适的 |
| 4 | `dD ≤ 1.00 × dC` | 改掉**全部受害者**（主色不比当前色差的都改） |

「受害者」= `dD ≤ dC`，和强度无关；按 S 高亮出来的就是这一批，强度 4 会全部修掉。
所以强度 4 之后区域里剩下的非主色像素，都是「用当前颜色确实比主色更准」的那些 ——
**渐变该有的层次会保留**。无脑把整片刷成主色会把渐变也压平，那是「涂掉」不是「修正」。

修正的坐标用归一化坐标保存，所以预览（可能被缩小过）和最终生成的投影套用的是同一块区域。

---

## C++ 抖动核心

误差扩散的内层循环和颜色匹配在 `src/maptool/native/dither_core.cpp` 里，
编译成 `maptool_native.dll` 由 `ctypes` 调用。**这是可选的** ——
DLL 不在就自动退回纯 Python 实现，结果逐像素一致，只是慢一些。

```bat
python tools\build_native.py     :: 需要 MinGW-w64 的 g++（本机在 D:\mingw64\bin）
python tools\build_native.py --check
```

实测（384×384 = 14.7 万像素，59 色调色板）：

| 算法 + 抖动 | 纯 Python | C++ | 加速 |
| --- | --- | --- | --- |
| 加权 RGB + Floyd-Steinberg | 0.328 s | 0.016 s | **20×** |
| 加权 RGB + Atkinson | 0.446 s | 0.019 s | **23×** |
| Redmean + Floyd-Steinberg | 1.124 s | 0.022 s | **51×** |
| CIE94 + Floyd-Steinberg | 0.617 s | 0.024 s | **26×** |
| CIEDE2000 + Floyd-Steinberg | 5.344 s | 0.494 s | **10.8×** |
| 加权 RGB + 无抖动 | 0.107 s | 0.016 s | 6.7× |

**为什么保证结果一样**：不是「应该一致」，是证明了。
`tests/native_match_equiv.py` 把 **256×256×256 = 1677 万种量化颜色**的匹配结果
和 numpy 版逐个比一遍 —— 误差扩散每步的匹配输入必然是量化颜色，
所以全空间一致就等于任意输入下一致。6 种算法 × 2 个调色板全部 0 处不同。
再加上 `tests/dither_native_equiv.py` 的 900 例端到端逐像素比对（6 算法 × 10 抖动 × 3 强度 × 5 张图）。

> 为什么是「普通 C DLL + ctypes」而不是 Python 扩展：本机 CPython 是 MSVC 编译的，
> 而编译器只有 MinGW-w64 g++。MinGW 编 CPython 扩展要跨 CRT（msvcrt vs ucrt），
> `PyObject*` 过边界容易出问题；导出纯 C ABI 用 ctypes 调就没有 ABI 风险，
> 也不需要 Python 头文件。
>
> 编译参数里必须带 `-static`：这个 g++ 是 posix 线程模型，不加的话 DLL 会依赖
> `libwinpthread-1.dll`，ctypes 加载时报 "Could not find module ... or its dependencies"。

---

## 目录结构

```
地图画工具/
├─ src/                      ← 源码
│  ├─ run_maptool.py         打包/运行入口（python src/run_maptool.py）
│  └─ maptool/               后端 Python 包（见 docs/ARCHITECTURE.md）
│     ├─ config.py           常量、端口、资源目录定位
│     ├─ palette.py          方块表加载 + 按颜色分组 + 运行时调色板
│     ├─ matching.py         6 种颜色匹配算法
│     ├─ dithering.py        误差扩散 / 有序抖动
│     ├─ repair.py           局部噪点修正（套索修补 + 画笔覆盖）
│     ├─ adjustments.py      12 项图像调整
│     ├─ schematic.py        .litematic 构建与方块分配
│     ├─ slicing.py          投影切分
│     ├─ lichen.py           发光地衣面属性
│     ├─ webapp.py           FastAPI 路由
│     ├─ native/             C++ 抖动核心（dither_core.cpp + 编译出的 DLL）
│     ├─ data/               生成的数据（blockdata.py、block_icons.png）
│     └─ web/                前端（index.html + css + js）
│
├─ tools/                    ← 开发脚本（不参与运行）
│  ├─ gen_blockdata.py       重新生成 data/blockdata.py 与图标贴图集
│  ├─ check_icons.py         图标贴图集自检（逐格比对像素）
│  ├─ split_frontend.py      历史上把单文件前端拆开的迁移脚本（留档）
│  ├─ build_native.py        编译 C++ 抖动核心（g++ -> maptool_native.dll）
│  └─ build.py               打包成单文件 exe
│
├─ packaging/                ← 发行版里的说明文件（源）
│  ├─ 使用说明.txt
│  └─ 换端口启动.bat          GBK 编码，cmd 才能正确读中文
│
├─ tests/                    ← 回归测试
│  ├─ regression_check.py    后端：新旧 API 等价性（13 项）
│  ├─ web_split_check.py     前端：拆分等价性 + 实测渲染（30 项）
│  ├─ native_match_equiv.py  C++/numpy 匹配器：全 1677 万种颜色逐位比对
│  ├─ dither_native_equiv.py C++/Python 抖动：端到端逐像素比对（900 例）
│  ├─ repair_check.py        局部噪点修正：功能与不变量（28 项）
│  ├─ repair_web_check.py    局部噪点修正：浏览器端实测（20 项）
│  ├─ bench_dither.py        抖动/匹配性能基准
│  ├─ exe_check.py           发行版：exe 与旧版等价性（需先打包）
│  ├─ slice_byte_noise.py    证明切分结果的字节数本身就会抖动（时间戳）
│  ├─ litematic_determinism.py  证明 .litematic 里 TimeCreated 每次都不同
│  └─ _tmp/                  测试临时产物（自动生成，可随时删）
│
├─ docs/
│  └─ ARCHITECTURE.md        架构与数据流说明
│
├─ dist/maptool/             ← 发行版（打包产物，可整个拷给别人）
│  ├─ Pictomapart.exe
│  ├─ 使用说明.txt
│  └─ 换端口启动.bat
│
└─ legacy/                   ← 历史版本留档，不参与构建
   ├─ MCmapart_tool/         解耦前的单文件版（后端 + 前端 + 开发脚本）
   ├─ maptool-v1/            解耦前的发行版（exe + zip）
   └─ 尼古地图画工具/          更早的版本
```

**源码和发行版是分开的**：`src/`、`tools/`、`tests/`、`docs/`、`packaging/` 是源码侧，
`dist/` 是唯一的发行版输出目录，里面的东西全部由 `tools/build.py` 生成，可以随时删掉重建。
`legacy/` 只是留档，删掉也不影响构建和运行（只影响那三个对比测试）。

---

## 怎么跑

### 方式一：直接用发行版（推荐给使用者）

```
dist\maptool\Pictomapart.exe
```

双击即可，浏览器会自动打开 `http://127.0.0.1:8765`。
详细说明见 `dist/maptool/使用说明.txt`。

### 方式二：从源码跑（推荐给改代码）

```bat
pip install fastapi uvicorn litemapy pillow numpy python-multipart
python src\run_maptool.py
```

端口被占用时可以换一个：

```bat
set MAPART_PORT=8899
python src\run_maptool.py
```

> **numpy 必须 >= 2.1**（Python 3.13 / 3.14 尤其重要）。
> numpy 1.26.x 官方不支持 Python 3.13+，网上流传的 MINGW-W64 构建会在
> `import numpy` 时直接 ACCESS_VIOLATION 崩溃。
> 检查：`python -c "import numpy; print(numpy.__version__)"`

---

## 怎么打包

```bat
pip install pyinstaller
python tools\build.py            # 产出 dist\maptool\Pictomapart.exe
python tools\build.py --check    # 只做打包前检查，不打包
```

---

## 怎么验证没改坏

两个回归测试都是「拿解耦前的单文件版当基准，逐项比对」：

```bat
python tests\regression_check.py     # 后端：同一套请求打两个服务，比对响应
python tests\web_split_check.py      # 前端：token 级比对 + 无头浏览器渲染比对
python tests\native_match_equiv.py   # C++ 匹配器：全 1677 万种颜色逐位比对
python tests\dither_native_equiv.py  # C++ 抖动：900 例端到端逐像素比对
python tests\repair_check.py         # 局部噪点修正：功能与不变量
python tests\repair_web_check.py     # 局部噪点修正：浏览器端实测
python tests\bench_dither.py 384     # 性能基准
python tests\exe_check.py            # 发行版（需要先打包）
```

`regression_check.py` 会同时起旧版和新版两个服务，跑一遍上传 / 预览 / 生成 /
下载 / 切分 / 地衣处理，比对返回的 JSON、PNG、`.litematic` 内容。
`.litematic` 里带 `TimeCreated` / `TimeModified` 时间戳，实测同一进程连做两次
就会差几个字节（`tests/slice_byte_noise.py` 里有实测数据），所以比对前会先把
时间戳归零、再比解压后的内容。

`exe_check.py` 用的是**完全相同的那套请求**（直接 `import regression_check` 复用），
只是把「新版」换成 `dist\maptool\Pictomapart.exe`，另外顺带检查打进去的前端资源
能不能取到、`/static/` 的越界读取有没有挡住。

`web_split_check.py` 做三件事：

1. **token 级等价**：拆出来的每个 `js/*.js`，token 流必须和它对应的原始行区间
   完全一致（字符串和模板字符串整体原样比对）。`style.css`、body 结构同理。
   拆分之后又**有意**加了功能的文件（`40-palette.js`、`70-mapart.js`）改成
   「原有 token 流必须是新文件的子序列」—— 只许加，不许删改原有代码。
2. **跨文件提升检查**：原来所有代码在一个 `<script>` 里，函数声明会整体提升；
   拆成多个 `<script>` 之后不再跨文件提升，所以扫一遍「顶层语句引用了后面文件
   才声明的名字」这种会在加载时直接报错的写法。
3. **实际渲染**：两个服务各起一份页面，用无头 Edge 导出最终 DOM 逐字比对。
   页面会开一条 SSE 保活长连接（会让 `--virtual-time-budget` 永远等下去），
   所以中间加了一层代理把 `/api/keepalive` 直接 204 掉。

比 DOM 时会先摘掉**有意新增**的部分（「局部噪点修正」面板、修正画布）和
**必然变化**的部分（调色板列表的内容、图标格子坐标、方块总数、贴图集行数），
这些都有各自的测试负责；剩下的必须逐字一致。

`repair_web_check.py` 是真的在浏览器里跑一遍：造图上传 → 套索拖拽 → 按 S →
调强度 → 画笔涂抹 → 清除，每一步都从渲染出来的预览图里读像素来验证。
注入探针时**必须在 `<head>` 里就把 `EventSource` 打桩**：
`keepaliveConnect()` 在 `99-start.js` 里就跑掉了，等页面底部再打桩已经晚了，
已经打开的 SSE 会让 `--virtual-time-budget` 永远等下去。

---

## 重新生成方块数据

方块表（`src/maptool/data/blockdata.py`）和图标贴图集
（`src/maptool/data/block_icons.png`）都是生成的，**不要手改**：

```bat
python tools\gen_blockdata.py     # 从 minecraft_blocks_mapcolor.json + 官方 zh_cn.json + 官方贴图 重新生成
python tools\check_icons.py       # 自检：坐标无重复无越界、每格贴图都对得上
```

贴图缺省会从 `raw.githubusercontent.com` 按需下载并缓存在 `tools/_build_cache/`，
也可以 `--textures <目录>` 指定本地贴图目录，或 `--offline` 只用本地缓存。

---

## 端口 / 常见问题

**端口被占用**：默认 8765。两种办法

```bat
netstat -ano | findstr :8765
taskkill /PID <PID> /F
```

或者双击 `换端口启动.bat`（改用 8899）。

**图标整体错位**：浏览器缓存了旧的贴图集。`/api/icons.png` 的地址上挂着内容
指纹（mtime + size），正常情况下会自动失效重取；还不行就 Ctrl+F5。
页面加载时也会自检贴图集尺寸，对不上会在状态栏里提示。

**关了网页程序还在跑**：页面全关后要等约 15 秒才退出（容忍刷新和短暂重连）。
不想让它自动退出，就去「设置」里关掉「关闭页面后自动退出」。
