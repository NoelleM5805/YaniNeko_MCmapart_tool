# 架构说明

解耦前后的对照，以及各部分为什么这么切。

---

## 一、解耦前后

| | 解耦前 | 解耦后 |
| --- | --- | --- |
| 后端 | `mapart_toolkit_server.py` 单文件 **2189 行 / 77 KB** | `src/maptool/` **15 个模块 + 包初始化**，最大 599 行（`webapp.py`） |
| 前端 | `index.html` 单文件 **3793 行 / 144 KB**（CSS + JS 全内联） | `web/index.html`（544 行）+ `css/style.css`（1482 行）+ `js/*.js` **13 个文件** |
| 数据 | 与源码混在同一目录 | `src/maptool/data/`（生成物，独立） |
| 开发脚本 | 和源码混在一起 | `tools/` |
| 发行版 | `maptool/` 与源码平级 | `dist/maptool/`，唯一输出目录 |
| 旧版 | 无处安放 | `legacy/` 留档 |

**行为零变化**：后端 18 项 API 等价性测试、前端 23 项拆分验证全部通过。
细节见 README 的「怎么验证没改坏」。

---

## 二、后端模块分层

依赖是**单向的**，自上而下，不存在循环：

```
  config          常量、端口(MAPART_PORT)、资源目录定位
    │
  colorspace      sRGB → CIE Lab
    │
  native          C++ 抖动核心的 ctypes 绑定（可选，缺了就退回纯 Python）
    │
  palette         方块表加载 → 按颜色分组 → 每次请求的运行时调色板 Palette
    │
  matching        6 种颜色匹配算法（欧氏/加权/redmean/CIE76/CIE94/CIEDE2000）
                  + 批量匹配 match_batch/dist_batch
    │
  dithering       误差扩散内核 + 有序抖动 + 整图转换 process_image
    │
  ├─ repair       局部噪点修正（套索修补 + 画笔覆盖），依赖 matching.dist_batch
  ├─ adjustments  12 项图像调整（曝光/对比/饱和/亮度/高光/暗部/色温/色调/锐化/清晰/色散/暗角）
  ├─ imageops     缩放、留边、拼贴、比例推荐
    │
  schematic       .litematic 构建、同色方块分配、字节化、文件名清洗
    │
  ├─ slicing      投影按 N×M 网格切块
  ├─ lichen       发光地衣面属性批量替换
    │
  ├─ keepalive    页面保活（SSE 长连接 + 无页面自动退出）
  ├─ tasks        任务表、日志、结果图缓存
    │
  webapp          FastAPI 路由（19 条）
    │
  runtime         Windows 下的控制台 / 异常兜底
    │
  __main__        命令行入口 main()
```

`src/run_maptool.py` 只是把 `src/` 放上 `sys.path` 再调 `main()`，
这样源码运行和 exe 运行走同一段代码。

### 每个模块负责什么

| 模块 | 关键内容 |
| --- | --- |
| `config` | `HOST` / `PORT` / `TASKS` / `KEEP_TASKS` / `PREVIEW_MAX_SIDE` / `DEFAULT_SEED`；`get_base_dir()` 找「含 `web/index.html` 的资源根」，`get_resource_path()` 依次找 `<root>/`、`<root>/web/`、`<root>/data/` |
| `colorspace` | `_srgb_to_linear`、`rgb_to_lab` |
| `palette` | `_load_blockdata()` 从 `maptool.data.blockdata` 读方块表；`_build_palette_groups()` 按颜色值分组并用 `_color_sort_key()` 排序（灰阶在前、彩色按色相）；`Palette.__slots__` 里预摊平 RGB/Lab 分量给向量化匹配用；`make_palette()` 按前端提交的方块顺序建调色板（组内顺序 = 前端顺序，所以「置顶」才有效） |
| `matching` | `_make_dist_tables()` 预计算可分离的距离表（float64，和原来的 int 通道 + float64 算术一致）；CIE 系列用 numpy 整批算；`_dist_block()` 是一个像素块到整个调色板的距离矩阵，`match_batch()`（取 argmin）和 `dist_batch()`（要距离本身，修正功能用）都走它，保证「选哪个」和「差多少」同一套公式。DLL 可用时 `match_batch` 优先走 C++ |
| `dithering` | `DIFFUSION_KERNELS` / `DITHER_LABELS` / `_make_bayer()`；`_diffuse_native()` 走 C++（内层循环 + 匹配都在 DLL 里），`_diffuse_flat()` 是纯 Python 回退（扁平 list + 2 格 padding + 预计算 tap 偏移、不做边界判断）；`process_image()` 是整图入口 |
| `native` | `maptool_native.dll` 的 ctypes 绑定。`mode_for(algo)` 只对**通过全空间等价性验证**的算法返回模式号，其余返回 None 自动退回 Python；`VERIFIED` 白名单的注释里记着验证结论。`PaletteHandle` 会持有传进去的 numpy 数组，否则 ctypes 传完指针数组就被回收，C++ 那边读到野内存 |
| `repair` | 局部噪点修正。`parse_repair()` 解析并夹取前端参数；`apply_repair()` 在 `idx` 上改，套索先算「主色 D + 当前色 C」的 2 色迷你调色板用 `dist_batch` 判受害者（`dD ≤ dC`）再按强度阈值决定改哪些，画笔最后直接覆盖；`render_highlight()` 画洋红高亮；坐标一律归一化，预览和成品套用同一块区域 |
| `adjustments` | `ADJUST_KEYS` 12 项 + `ADJUST_UNIPOLAR`（锐化/清晰/暗角只有正方向）；`parse_adjust()` 解析并夹取范围；调整在**成品尺寸**上做，保证预览和生成一致 |
| `imageops` | `flatten_image()`（透明图铺背景）、`fit_image()`（stretch/contain/cover）、`recommend_ratios()` 推荐地图比例 |
| `schematic` | `pick_block_names()` 同色多选时按 `random`/`cycle`/`first` 分配；`count_block_usage()` 统计；`build_mapart_schematic()` 用 `BlockState(id, **props)` 写方块状态；`schem_to_bytes()`、`safe_stem()` / `safe_litematic_name()` |
| `slicing` | `split_boundaries()` + `do_slice()`，按 Y 轴范围切、输出多个独立 `.litematic` |
| `lichen` | `do_glow_lichen()` 与目标方块 ID（`minecraft:glow_lichen`）比对后批量改面属性 |
| `keepalive` | `KEEPALIVE` 状态 + `_keepalive_watchdog()`；`KEEPALIVE_GRACE=15s`、`KEEPALIVE_TICK=2s`，靠 `armed` 防止没人连过就退出 |
| `tasks` | `IMAGE_CACHE`（`MAX_IMAGE_CACHE=12`，LRU）、`add_log()` / `create_task()` / `finish_task()`，任务只留最近 `KEEP_TASKS=15` 个 |
| `webapp` | `app` 与全部路由；`/` 读 `web/index.html`，`/static/{path}` 只服务 `web/` 下的文件（拼出来的绝对路径必须仍在 `web/` 内，挡 `../` 越界） |
| `runtime` | `_NullWriter`、`stdout_is_tty()`、`_setup_console()`、`_install_excepthook()`、`safe_pause()` |
| `__main__` | `main()`：打启动横幅 → 自检 `web/index.html` → 试绑端口（失败弹 MessageBox）→ 起 uvicorn 线程 + 保活看门狗 → 开浏览器 → 主循环 |

---

## 三、前端拆分

13 个 `<script>` 按顺序加载，**共享同一个全局作用域**（故意不用 ES module，
这样双击 `index.html` 也能跑）。加载顺序就是原来单文件里的顶层语句执行顺序。

```
00-util.js       $ / 徽标 / 拖放 / 保存 / 任务轮询       ← 所有模块的公共依赖
10-modal.js      预览图放大模态框（滚轮缩放 / 拖动）
20-config.js     配置持久化 + 主题
30-adjust.js     图片调整 12 项
40-palette.js    方块选择面板（按颜色分组 / 预设 / 置顶）
50-usage.js      底部方块用量统计
60-settings.js   设置面板 + 表单值 ⇄ 配置
70-mapart.js     地图画生成
80-slice.js      投影切分
85-lichen.js     Glow Lichen
90-init.js       初始化：读配置、把配置贴到界面上
92-focus.js      自由预览（专注模式）
99-start.js      启动：进入专注模式、拉调色板、连保活
```

### 为什么 20-config 排在 70-mapart 前面

原文件里 `mapart`（2289-2616）在 `config`（2617-2688）**之前**。
拆开后把 config 提前，是因为 `60-settings.js` 的顶层语句
`bindSwitch("set-icons", "icons", ...)` 会在**加载时**就读 `CFG[key]`，
而 `const CFG` 是块级绑定（TDZ），声明前读会直接报错。
提前之后只增加可用性，不减少，所以是安全的。

其余顺序完全照原来的行号来。`web_split_check.py` 的第 2 项检查会扫
「顶层语句引用了后面文件才声明的名字」，专门盯这类问题。

### 缩进

原来是嵌在 `<script>` 里的，整体多一层 8 空格缩进。拆出来时去掉这 8 空格，
但**行首落在字符串 / 模板字符串 / 块注释内部的行不动** ——
模板字符串里的空白是内容的一部分。

这个去缩进是可验证无损的：`tools/split_frontend.py` 里把去掉的空格补回去，
必须和原文逐字一致，否则直接报错退出。

> 踩过的坑：扫描器一开始把「处理换行」放在「处理行注释」之前，导致
> `//` 注释永远不出栈、一直吞到文件结尾，于是**没有任何一行被保护**，
> 模板字符串里的缩进被改掉了。往返验证发现不了这个（补回空格当然能还原），
> 是 token 级比对抓出来的。

---

## 三·五、局部噪点修正为什么这么做

「受害者方块」的定义是这个功能的核心，改过一版：

- ❌ **一开始写成「区域内 `idx != 主色` 的像素」** —— 太松。
  在强度 4（阈值 1.0）下只有 705/928 被改回，剩下的 223 个「改不动」，
  测试直接失败。原因是那 223 个像素用**当前颜色**其实比主色更准
  （渐变该有的层次），把它们也算「受害者」本身就是错的。
- ✅ **改成「`dD ≤ dC` 的像素」** —— 也就是「用主色表示不比用当前色差」。
  于是强度 4 的「改回数量 == 受害者数量」天然成立，按 S 高亮的就是这一批，
  而且修完之后区域里剩下的非主色像素全都是「当前色确实更准」的那些。

这个定义还有个好处：**强度是可验证的递进关系**。测试里直接断言
「强度 2 改的像素集合是强度 4 的子集」和「2 ≤ 3 ≤ 4 单调不减」，
不靠感觉。

坐标一律用**归一化坐标**（0~1，相对真实图像）。预览可能被 LANCZOS 缩小过，
生成是全尺寸，用归一化坐标才能保证两处圈到/涂到的是同一块地方。
测试里有一条专门验这个：同一份选区在 1 倍和 2 倍尺寸下，改回的比例要接近。

前端的修正画布**不包 wrapper**，而是绝对定位盖在 `<img>` 上面。
一开始包了一层 `.rp-wrap`（`display:inline-block; max-width:100%`），
和 `img` 上的 `max-width:100%` 形成循环依赖，图片显示尺寸被压到 49×49；
而且画布在图片完成布局**之前**测量，尺寸变成 1×1，之后所有归一化坐标全乱
（第一个浏览器测试就是这么抓出来的）。现在改成：绝对定位 + `load` 事件 +
`ResizeObserver` 兜底，尺寸退化时直接拒绝记录坐标。

> 另一个只有实测才能发现的坑：预览接口一开始改完 `idx` 忘了按新 `idx`
> 重新渲染 `rgb`，于是「修正生效了、但页面上看不出任何变化」。
> 单元测试（直接调 `apply_repair`）完全发现不了，是浏览器测试读预览图像素抓出来的。

---

## 四、资源定位（源码运行 / exe 运行统一）

资源根目录固定是「含 `web/index.html` 的那个目录」，候选顺序：

1. PyInstaller 解包目录 `sys._MEIPASS`
2. 本包所在目录（`src/maptool/`）—— 源码直接跑
3. 可执行文件所在目录 —— 绿色版把 `web/` 和 `data/` 放在 exe 旁边
4. 当前工作目录

```
<root>/web/    前端：index.html · css/style.css · js/*.js
<root>/data/   block_icons.png · minecraft_blocks_mapcolor.json
```

`maptool.data.blockdata` 是**普通模块**（`from .data import blockdata`），
不进资源目录 —— 打包时由 PyInstaller 当模块打进去，不需要 `--hidden-import` 之外的照顾。

---

## 五、几个设计取舍

**重复方块取哪条**：同名不同色的条目取数据文件里**靠后**的那条。

**玻璃被排除**：`EXCLUDED_CATS = ("glass",)`。玻璃是半透明的，当方块画出来
颜色对不上。

**需要固定方块状态的方块**：在 `gen_blockdata.py` 的 `BLOCK_PROPS` 里配，
构建投影时用 `BlockState(id, **props)` 写进去。

| 方块 | 状态 |
| --- | --- |
| 发光地衣 `glow_lichen` | `down=true` |
| 轻质测重压力板 `light_weighted_pressure_plate` | `power=0` |
| 铁活板门 `iron_trapdoor` | `facing=north,half=bottom,open=false,powered=false,waterlogged=false` |

`chest` 没有方块贴图（是实体模型），退回纯色块。

**贴图集版本化**：`/api/icons.png` 的地址上挂 `?v=<mtime>-<size>`。
重新生成贴图集后每个方块所处的格子都会变，如果浏览器还拿着旧图 + 新坐标
就会整体错位；内容一变地址就变，缓存自然失效。前端加载时还会自检一次
贴图集尺寸，对不上就提示强刷。

**保活用 SSE 不用定时 ping**：浏览器会把后台标签页的定时器限流到每分钟一次，
容易误判；长连接在标签页关闭瞬间就断开，最可靠。

**输出精度**：优化后的匹配算法与原实现在 287/288 组合上逐像素一致。
唯一差异是纯灰阶渐变 + `weighted` + `sierra_lite` 这一组合 ——
误差扩散本身是混沌的，浮点精度差异会级联放大。
真实照片类图像完全一致，这个取舍是已知且接受的。

---

## 六、Windows 下的坑（都已处理）

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `RuntimeError: lost sys.stdin` | `pythonw` / `--windowed` 下没有 stdin，而代码只捕获了 `EOFError` | `safe_pause()`：检查 `sys.stdin is None` / `closed` / `isatty()`，并捕获 `EOFError`/`KeyboardInterrupt`/`RuntimeError`/`OSError`/`ValueError` |
| `ValueError: Unable to configure formatter 'default'` | uvicorn 里 `sys.stdout.isatty()` 在无控制台时是 `None` | `_NullWriter` 顶上 + 显式传 `use_colors=stdout_is_tty()` |
| `UnicodeEncodeError: 'gbk' codec can't encode '\u2713'` | `✓ ✗ ⚠` 不在 GBK 里 | 控制台输出改用 `√ × !`，并 `reconfigure(errors="replace")` |
| `换端口启动.bat` 不生效 | 写成 UTF-8 时 cmd 按 GBK 读，尾字节把换行吃掉了 | **必须存成 GBK（代码页 936）** |

---

## 七、性能

| 位置 | 原来 | 现在 |
| --- | --- | --- |
| 颜色匹配 | 每次调用 10.6 µs | 可分离 float64 距离表；CIE 系列 numpy 向量化 |
| 误差扩散 | 纯 Python 逐像素、每步做边界判断 | `_diffuse_flat()`：扁平 list + 2 格 padding + 预计算 tap 偏移，不做边界判断 |

关键约束：距离表必须用 **float64**，且通道保持整数。
最初写成 float32 时，288 个颜色组合里有 3 个和原实现不一致 ——
原实现是「整数通道 + float64 算术」，中途降精度会改变舍入结果。
