# YaniNeko_MCmapart_tool · 地图画工具箱

> **YaniNeko_MCmapart_tool** — a local web tool for Minecraft map-art: convert images to 2D
> map-art `.litematic` schematics (live preview, dithering, local noise repair,
> global undo), slice schematics to map-sized pieces, and batch-edit Glow Lichen
> faces. Runs entirely on `127.0.0.1`. Chinese UI.

纯AI编程
把 Minecraft 地图画相关的三件事打包成一个**本地网页工具**：
上传图片就能实时预览、点击放大、导出投影文件。

| 功能 | 说明 |
| --- | --- |
| 🎨 地图画生成 | 图片 → 2D 地图画 `.litematic`。调参实时预览、点击放大、方块用量统计、导出的文件名默认跟图片同名 |
| 🩹 局部噪点修正 | 锁一种「受害者方块」→ 圈选区 → 按按钮才改。只动锁定那一种颜色，选区里其它内容不碰 |
| ✂️ 投影切分 | 上传 `.litematic` 后**自动按投影尺寸推荐**切法（每块 ≤128×128 = 一张地图），带逐像素切割预览；输出多个独立投影文件（不打包成 zip） |
| 🧹 投影噪点修改 | 直接上传 `.litematic`，在投影俯视图上使用降噪/填充/还原/画笔，导出修改后的投影文件 |
| 🌿 Glow Lichen | 批量修改发光地衣的六面朝向属性 |
| ↩️ 全局撤回 | 最多 20 步，覆盖噪点修正 / 方块选择 / 图片调整 / 算法与尺寸

地图画默认输出：**地面朝向（XZ）· 厚度 1 · 无底板**。
服务只监听 `127.0.0.1`，不上局域网；页面全关掉后进程会自己退出。

---

## 局部噪点修正怎么用

抖动算法为了还原渐变会**故意**在局部掺入别的颜色。大部分时候这正是想要的噪点感，
但大片纯色、人脸、文字这些地方，你可能希望把某一种**被抖动掺进来的杂色**清掉。

**这个功能区只在打开「隐藏原图（放大预览）」时出现。** 关掉时左边栏不显示它，
预览图上的编辑覆盖层也会摘掉，点击预览图恢复成原来的「点击放大」。

进去之后是三步，刻意做成「选完再圈、点了才改」：

**① 选受害者方块** —— 要清掉的那一种颜色

- 点「🎯 从预览图拾取」，再在预览图上点一下
- 或者点下面的色块列表（列表里的颜色都是游戏内可用的）

色块列表点一下作用于**当前工具**，小标题会跟着变：

| 当前工具 | 点色块 | <kbd>Shift</kbd>+点 |
| --- | --- | --- |
| 套索 | 设为受害者方块 | 设为笔刷颜色 |
| 画笔 | 设为笔刷颜色 | 设为受害者方块 |

**② 圈出选区** —— 在预览图上拖拽圈一片区域

圈完只会显示**洋红色的受害者预览**（选区内已经是被锁定颜色的那些方块），
**不会立刻修改**。选区跟随预览图的缩放和平移，相对位置始终不变。

做完几步之后，已执行过的选区轮廓会一直留在预览图上，挡视线。
点「👁 隐藏选区」可以把它们收起来（**只影响显示，修改结果一个字节都不动**），
再点一下恢复；隐藏状态下如果又去拖拽，会自动显示回来，免得拖了半天什么都看不见。
「取消当前选区」只清掉还没执行的那一个。

**③ 点「✅ 执行降噪」** —— 只有点下去才真正改

| 按钮 | 作用 |
| --- | --- |
| ✅ 执行降噪 | 选区内、且**正好是锁定颜色**的方块，换成邻域里出现次数最多的颜色 |
| 🎨 填充 | 把整个选区填成锁定颜色 |
| ↩ 还原 | 把选区恢复成**不做抖动**时最近的颜色（这一片直接关掉抖动） |
| 画笔 | 按住涂抹，方块**立刻**变成笔刷颜色；松开后点「应用画笔修改」才提交并可撤回 |

### 画笔：像素画逻辑 + 实时

**实时**：落笔的瞬间本地就把方块画上去了，拖动过程中每经过一格立刻上色，
**整笔期间不打扰服务端**（预览图不会闪、不会有延迟）。松开鼠标只暂存笔迹，
点「应用画笔修改」才把暂存笔迹提交成操作序列，这时服务端才按同一串操作
重放出权威结果和用量统计。

**像素画逻辑**（不是「笔触」）：

- 落点**吸附到方块格子**（`floor(x * W)`），只有整格，没有半格
- 笔头是 **N×N 的方块**，硬边、不抗锯齿 —— 不是圆形笔头盖出来的柔和笔触
  （`tests/repair_check.py` 里断言「5 格 = 25 个方块，不是圆形的 21 个」）
- 相邻落点之间用 **Bresenham 整数直线**补齐，快拖也不会断成虚线
  （跨 10 格 = 11 个方块，横竖斜都验过）
- 已画过的画笔**不在覆盖层上留任何轮廓** —— 画上去的像素本身就是结果，
  再叠一圈虚线只是碍事（测试里构造「只有画笔操作」的文档，覆盖层必须是空的）

鼠标停在预览图上时会有一个跟着格子的**笔头方框**，让你看清笔头多大。
那是光标，不是笔触，松开鼠标不会留下痕迹。

**「修复强度」= 邻域半径**（1~8 → 半径 1~4）：判定「周围谁最多」时看多大一圈。

- 分散的单点杂色，半径 1 就全清掉了
- 成团的杂色，半径越小只有外圈会被吃掉，里面还留着；半径够大才整块清掉
- 半径越大越干净，但也更容易抹掉细小纹样

**关键点：只动锁定的那一种方块。** 选区里的线条、渐变、边界一概不碰 ——
不是「把整片刷成一个颜色」，那样会把画面压平。这条有测试守着
（`tests/repair_check.py` 里断言「越界改了 0 个」「细节一个没动」）。

### 专注模式下的鼠标操作

| 操作 | 作用 |
| --- | --- |
| **右键拖动** | 平移预览图 |
| 左键拖动 | 套索 / 画笔（当前工具） |
| 滚轮 | 以光标为中心缩放 |
| 双击 | 视图复位 |

左键留给工具，所以右键才是拖图；右键的系统菜单在预览区已被屏蔽。

### 配色调参（左边栏）

专注模式会把主区藏起来，而「颜色识别 / 抖动算法 / 抖动比例」都在主区里，
所以在左边栏镜像了一份，边看边调不用来回切页签。改哪边都行，两边是双向同步的。


修改结果用**归一化坐标**记录成一串操作，服务端每次都从抖动结果重新重放这一串，
所以预览和最终生成的投影套用的是同一串修改。

### 撤回

侧边栏「撤回」区 + <kbd>Ctrl</kbd>+<kbd>Z</kbd>（重做 <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>Z</kbd> / <kbd>Ctrl</kbd>+<kbd>Y</kbd>）。

**全局**的：一次状态快照包含所有会影响成品的东西 ——
噪点修正的操作序列、方块选择、图片调整、算法/抖动/尺寸表单、同色分配策略。
最多保存 **20** 步，超出就把最旧的一条挤掉（那份状态回不去，但当前结果不受影响）。

撤回之后再做新动作，会丢弃「重做」分支（和常见编辑器一致）。
滑杆只在**松手（change）**时记一步，拖动过程中不会刷出一堆空步骤。

---

## 投影切分怎么用

上传一个 `.litematic`，右侧立刻出现**切割预览**和推荐切法，不用先猜 N×M。

**推荐是怎么算的**：一张地图在游戏里就是 128×128 方块，所以推荐的目标尺寸
就是 128 —— 切出来的每一块正好能贴一张地图。

```
列数 = ceil(宽 / 128)     行数 = ceil(高 / 128)     然后均匀分
```

均匀分意味着每块通常略小于 128（2000 格宽切 16 列 = 每列 125）。尺寸不到
128 的投影会直接告诉你「不用切」。

| 操作 | 说明 |
| --- | --- |
| 自动（默认） | 按上面的规则推荐，列出每块尺寸和方块数 |
| 手动指定网格 | 填列数 × 行数。**写了就是最终结果**，哪怕每块超过 128 也只提醒、不改刀数 |
| 每块最大边长 | 默认 128；调小就会推荐更多刀（64 → 2000 格宽切 32 列） |

手动填的数超过「每块最大边长」时会在下面给一条提醒（含最少要几列几行），
但**不会**偷偷替你改 —— 切地图画常常是按自己地图的编号来切的。

**切割预览**：预览图是**逐像素全分辨率**渲染的 —— 一个方块 = 一个像素，
不做下采样（下采样会把 1 格宽的边、文字笔画整个跳过去，预览就和成品对不上）。
图上的红线上就是刀口的位置；鼠标移到「每块明细」里的任意一块，图上对应那块
会高亮，并显示行列号和尺寸。明细列表里空块会置灰（空块**不会**产出文件）。

**保存**：

- 支持目录选择器的浏览器：**边下载边写盘**（并发拉取，不再一个一个等），
  内存里不留整份
- 不支持的：回退成逐个下载，另外给了「打包成 zip 下载」的出口 ——
  Chrome 会拦「一次下很多文件」，64 个投影逐个下容易被拦掉

上传的字节只在预览时传一次：真正切分时前端只回传一个内容指纹（key），
不再把文件重传一遍。

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
│     ├─ repair.py           局部噪点修正（锁定目标 + 邻域多数替换 + 填充/还原/画笔）
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
│  └─ 使用说明.txt
│
├─ tests/                    ← 回归测试
│  ├─ regression_check.py    后端：新旧 API 等价性（13 项）
│  ├─ web_split_check.py     前端：拆分等价性 + 实测渲染（30 项）
│  ├─ slice_check.py         投影切分：逐方块映射 / 序列化字节等价（40 项）
│  ├─ slice_web_check.py     投影切分：浏览器端实测（32 项）
│  ├─ native_match_equiv.py  C++/numpy 匹配器：全 1677 万种颜色逐位比对
│  ├─ dither_native_equiv.py C++/Python 抖动：端到端逐像素比对（900 例）
│  ├─ repair_check.py        局部噪点修正：功能与不变量（55 项）
│  ├─ repair_web_check.py    局部噪点修正 + 全局撤回：浏览器端实测（36 项）
│  ├─ page_load_check.py     页面加载自检：抓 JS 报错 + 开关修正区的行为
│  ├─ bench_dither.py        抖动/匹配性能基准
│  ├─ exe_check.py           发行版：exe 与旧版等价性（需先打包）
│  ├─ slice_byte_noise.py    证明切分结果的字节数本身就会抖动（时间戳）
│  ├─ litematic_determinism.py  证明 .litematic 里 TimeCreated 每次都不同
│  └─ _tmp/                  测试临时产物（自动生成，可随时删）
│
├─ docs/
│  └─ ARCHITECTURE.md        架构与数据流说明
│
├─ dist/                     ← 发行版（打出来的东西，可以整个拷给别人）
│  ├─ YaniNeko_MCmapart_tool/
│  │  ├─ YaniNeko_MCmapart_tool.exe
│  │  └─ 使用说明.txt
│  └─ YaniNeko_MCmapart_tool_v1.37.zip
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

## 怎么打包发行版

```bat
python tools\build.py            :: 产出 dist\YaniNeko_MCmapart_tool\ + 同名 zip
python tools\build.py --check    :: 只做打包前检查
python tools\build.py --no-zip   :: 不生成 zip
```

版本号写在 `src/maptool/__init__.py` 的 `__version__` 里，打包时自动带进
程序名、使用说明和 zip 文件名。

产出：

```
dist/
├─ YaniNeko_MCmapart_tool/                 ← 发行版目录，可以整个拷给别人
│  ├─ YaniNeko_MCmapart_tool.exe
│  └─ 使用说明.txt
└─ YaniNeko_MCmapart_tool_v1.37.zip        ← 上面那个目录打的包
```

多文件（onedir）版：`python tools\build_onedir.py` 会额外产出
`dist\YaniNeko_MCmapart_tool_onedir\` 和 `YaniNeko_MCmapart_tool_v<版本>_onedir.zip`，
启动更快（不用每次解包到临时目录），但文件多、要整个目录一起发。

打包脚本会做的事：检查资源是否齐全、**关掉正在运行的旧 exe**（不然文件被占用
没法覆盖）、跑 PyInstaller（日志只显示 WARNING/ERROR，不然满屏 INFO 看着像失败）、
从 `packaging/` 复制说明文件并替换程序名与版本号。

`dist/` 下**别的发行版不会被删**，只有同名的那一个目录会重建。

> zip 是顺手提供的方便件，不是必须的。刚写完的 34 MB 文件有时会被
> Windows Defender / 搜索索引器占住几秒到几分钟，删不掉也改不了名。
> 遇到这种情况脚本会自动写到 `<名字>_<版本>_new.zip` 并给出提示，
> **不会让整次打包失败** —— 真正要交付的是那个目录。
> 等占用方松手后删掉旧 zip 再重跑一次就能回到正常文件名。

打完之后用 `python tests\exe_check.py` 验证：它会拿旧版单文件版当基准跑一遍
完整请求，另外确认前端资源、C++ 核心、版本号、越界防护和端口自动切换。

---

## 怎么跑

### 方式一：直接用发行版（推荐给使用者）

```
dist\YaniNeko_MCmapart_tool\YaniNeko_MCmapart_tool.exe
```

双击即可，浏览器会自动打开 `http://127.0.0.1:8765`。
详细说明见 `dist/YaniNeko_MCmapart_tool/使用说明.txt`。

### 方式二：从源码跑（推荐给改代码）

```bat
pip install fastapi uvicorn litemapy pillow numpy python-multipart
python src\run_maptool.py
```

端口被占用时不需要手动操作：程序会从默认端口开始自动向后找空闲端口。
也可以仍然用环境变量指定起始端口：

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
python tools\build.py            # 产出 dist\YaniNeko_MCmapart_tool\YaniNeko_MCmapart_tool.exe
python tools\build.py --check    # 只做打包前检查，不打包
```

---

## 怎么验证没改坏

两个回归测试都是「拿解耦前的单文件版当基准，逐项比对」：

```bat
python tests\regression_check.py     # 后端：同一套请求打两个服务，比对响应
python tests\web_split_check.py      # 前端：token 级比对 + 无头浏览器渲染比对
python tests\slice_check.py          # 投影切分：逐方块映射 + 序列化字节等价
python tests\slice_web_check.py      # 投影切分：浏览器端完整流程
python tests\native_match_equiv.py   # C++ 匹配器：全 1677 万种颜色逐位比对
python tests\dither_native_equiv.py  # C++ 抖动：900 例端到端逐像素比对
python tests\repair_check.py         # 局部噪点修正：功能与不变量
python tests\repair_web_check.py     # 局部噪点修正 + 全局撤回：浏览器端实测
python tests\page_load_check.py      # 页面加载自检
python tests\bench_dither.py 384     # 性能基准
python tests\exe_check.py            # 发行版（需要先打包）
```

`regression_check.py` 会同时起旧版和新版两个服务，跑一遍上传 / 预览 / 生成 /
下载 / 切分 / 地衣处理，比对返回的 JSON、PNG、`.litematic` 内容。
`.litematic` 里带 `TimeCreated` / `TimeModified` 时间戳，实测同一进程连做两次
就会差几个字节（`tests/slice_byte_noise.py` 里有实测数据），所以比对前会先把
时间戳归零、再比解压后的内容。

`exe_check.py` 用的是**完全相同的那套请求**（直接 `import regression_check` 复用），
只是把「新版」换成 `dist\YaniNeko_MCmapart_tool\YaniNeko_MCmapart_tool.exe`，另外顺带检查打进去的前端资源
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

**端口被占用**：默认 8765。程序启动时会自动检测，并从 8766 开始
向后寻找一个空闲端口，不需要手动换端口；实际地址以启动窗口打印的
「监听地址」为准。如果连续多个端口都无法绑定，可以先关掉旧的实例。

**图标整体错位**：浏览器缓存了旧的贴图集。`/api/icons.png` 的地址上挂着内容
指纹（mtime + size），正常情况下会自动失效重取；还不行就 Ctrl+F5。
页面加载时也会自检贴图集尺寸，对不上会在状态栏里提示。

**关了网页程序还在跑**：页面全关后要等约 15 秒才退出（容忍刷新和短暂重连）。
不想让它自动退出，就去「设置」里关掉「关闭页面后自动退出」。

---

## 许可证

本项目使用 [MIT License](LICENSE) 开源，欢迎自由使用、修改与分发。
参与开发请先看 [CONTRIBUTING.md](CONTRIBUTING.md)，日常维护见 [docs/MAINTENANCE.md](docs/MAINTENANCE.md)。
