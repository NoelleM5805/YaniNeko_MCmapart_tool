# Pictomapart · 地图画工具箱

把 Minecraft 地图画相关的三件事打包成一个**本地网页工具**：
上传图片就能实时预览、点击放大、导出投影文件。

| 功能 | 说明 |
| --- | --- |
| 🎨 地图画生成 | 图片 → 2D 地图画 `.litematic`。调参实时预览、点击放大、方块用量统计、导出的文件名默认跟图片同名 |
| ✂️ 投影切分 | 把一个 `.litematic` 按 N×M 切成多个独立投影文件（不打包成 zip） |
| 🌿 Glow Lichen | 批量修改发光地衣的六面朝向属性 |

地图画默认输出：**地面朝向（XZ）· 厚度 1 · 无底板**。
服务只监听 `127.0.0.1`，不上局域网；页面全关掉后进程会自己退出。

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
│     ├─ adjustments.py      12 项图像调整
│     ├─ schematic.py        .litematic 构建与方块分配
│     ├─ slicing.py          投影切分
│     ├─ lichen.py           发光地衣面属性
│     ├─ webapp.py           FastAPI 路由
│     ├─ data/               生成的数据（blockdata.py、block_icons.png）
│     └─ web/                前端（index.html + css + js）
│
├─ tools/                    ← 开发脚本（不参与运行）
│  ├─ gen_blockdata.py       重新生成 data/blockdata.py 与图标贴图集
│  ├─ check_icons.py         图标贴图集自检
│  ├─ split_frontend.py      历史上把单文件前端拆开的迁移脚本（留档）
│  └─ build.py               打包成单文件 exe
│
├─ packaging/                ← 发行版里的说明文件（源）
│  ├─ 使用说明.txt
│  └─ 换端口启动.bat          GBK 编码，cmd 才能正确读中文
│
├─ tests/                    ← 回归测试
│  ├─ regression_check.py    后端：新旧 API 等价性（18 项）
│  ├─ web_split_check.py     前端：拆分等价性（23 项）
│  ├─ exe_check.py           发行版：exe 与旧版等价性（24 项）
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
python tests\exe_check.py            # 发行版：把新版换成打好的 exe 再比一遍
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
2. **跨文件提升检查**：原来所有代码在一个 `<script>` 里，函数声明会整体提升；
   拆成多个 `<script>` 之后不再跨文件提升，所以扫一遍「顶层语句引用了后面文件
   才声明的名字」这种会在加载时直接报错的写法。
3. **实际渲染**：两个服务各起一份页面，用无头 Edge 导出最终 DOM 逐字比对。
   页面会开一条 SSE 保活长连接（会让 `--virtual-time-budget` 永远等下去），
   所以中间加了一层代理把 `/api/keepalive` 直接 204 掉。

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
