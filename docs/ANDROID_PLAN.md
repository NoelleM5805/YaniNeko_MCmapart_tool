# 安卓端适配方案

> 分支：`android`（已从 `main` 创建）。目标：把 **YaniNeko_MCmapart_tool** 打包成安卓原生 App，**整机在手机上运行**（不是手机浏览器远程访问桌面端）。

---

## 0. 结论速览

- **路线**：用 **Chaquopy** 把现有 Python 后端（FastAPI + numpy + litemapy + nbtlib + pillow）嵌入安卓 App，前端用**系统 WebView** 加载本地服务 `http://127.0.0.1:<port>`。
- **复用**：后端算法（抖动/匹配/修正/切分/地衣）、`webapp.py` 全部路由、前端 `HTML/CSS/JS` **原样复用**。
- **改动**：后端入口做平台分支、C++ 抖动核心交叉编译成 `.so`、前端加触屏手势与响应式、文件下载改走系统下载/分享。
- **分四步**：M1 原生壳跑通 → M2 移动端交互与下载 → M3 原生核心加速 → M4 打包发布。

---

## 1. 目标与架构

### 1.1 现状

```
桌面端
┌───────────────────────────────┐
│ Python 进程 (127.0.0.1:8765)  │
│  FastAPI (webapp.py)          │
│   ├─ palette / matching / dithering / repair / …   │
│   ├─ native/maptool_native.dll  (可选 C++ 加速)    │
│   └─ web/  (index.html + css + js)                 │
└──────────────▲────────────────┘
               │ 桌面浏览器打开 http://127.0.0.1:8765
```

### 1.2 目标（安卓原生 App）

```
Android App (单进程)
┌─────────────────────────────────────────────┐
│ Kotlin/Java 壳                               │
│  ├─ WebView  ──────────┐ 加载本地 URL        │
│  └─ 生命周期控制 ───────┼──► 启动/停止服务     │
└────────────────────────┼────────────────────┘
                         ▼
┌─────────────────────────────────────────────┐
│ Chaquopy 内嵌 CPython（同一进程的后台线程）  │
│  FastAPI 监听 127.0.0.1:<port>              │
│   ├─ 后端模块（复用，无改动）                 │
│   ├─ native/libmaptool_native.so（ARM64 交叉编译）│
│   └─ web/ 前端（打包进 APK，复用）           │
└─────────────────────────────────────────────┘
```

要点：**前端一行不改也能跑**，因为它是标准网页；安卓 WebView 访问本机 `127.0.0.1` 不需要局域网，也基本不破坏「只在本地」的安全边界。

---

## 2. 技术选型

| 方案 | 说明 | 结论 |
| --- | --- | --- |
| **Chaquopy**（推荐） | Gradle 插件，CPython 与 Java/Kotlin 同进程；`pip` 装 numpy/fastapi 等，自带 Android 轮子仓库；WebView + 后台 Python 线程是官方典型用法 | ✅ 选用 |
| python-for-android（Kivy/buildozer） | 纯 Python 打包，numpy 用 recipe 编译；WebView 要靠 pyjnius 手写桥接，胶水多、编译慢 | 备选 |
| BeeWare/Briefcase | Toga UI 需重写前端（本方案要保留网页前端），Android 后端底层其实也走 Chaquopy | 不适合 |
| Termux | 不是「App 交付」，用户体验差 | 不适合 |

**选 Chaquopy 的理由**：

1. 后端依赖几乎全是**纯 Python**（fastapi、uvicorn、litemapy、nbtlib、python-multipart），只有 **numpy / pillow** 是原生扩展——这两个在 Chaquopy 都有现成 Android 轮子。
2. 前端是**标准网页**，直接塞 WebView，改动面最小、复用最多、回归测试还能照跑。
3. Java↔Python 可双向调用，能干净地做「WebView 下载 → Kotlin 写系统下载目录」「日志 → logcat」这类桥接。

---

## 3. 现状盘点（复用 vs 改动）

| 模块 | 处理 | 说明 |
| --- | --- | --- |
| `palette / matching / dithering / repair / adjustments / imageops / schematic / slicing / lichen / colorspace / tasks` | ✅ 原样复用 | 纯 Python + numpy，与平台无关 |
| `webapp.py`（22 条路由） | ✅ 原样复用 | FastAPI 路由、静态资源、上传下载逻辑都不变 |
| `web/index.html` + `css/*` + `js/*` | ⚠️ 部分改动 | 加触屏手势、响应式、下载桥接；业务逻辑不变 |
| `config.py` | ⚠️ 小改 | `get_base_dir()` 增加安卓资源根注入 |
| `__main__.py` | 🔧 平台分支 | 去掉 `webbrowser.open`、主循环/键盘中断、Windows 弹窗，改成「启动服务→返回 URL」 |
| `runtime.py` | ⚠️ 小改 | 无控制台兜底已有；日志重定向到 logcat |
| `keepalive.py` | 🔧 平台分支 | 安卓下禁用 `os._exit` 看门狗，改由 App 生命周期控制 |
| `native/dither_core.cpp` + `.dll` | 🔧 交叉编译 | `.dll`(x86_64 PE) → `libmaptool_native.so`(arm64-v8a)，ctypes 加载路径适配；首版可退回纯 Python |

---

## 4. 后端适配改动（逐项）

### 4.1 入口与平台分支

当前 `__main__.py` 的 `main()` 是「桌面一次性入口」：打印横幅 → 自检资源 → 找端口 → 起 uvicorn 线程 → `webbrowser.open(url)` → `while True: sleep(1)` 主循环。安卓上要做平台分支：

- **新增 `start_server()`（供宿主调用）**：起 uvicorn 线程 + 找端口 + 返回 `"http://127.0.0.1:<port>"`，**不** `webbrowser.open`、**不**进 `while True` 死循环、**不** `safe_pause()`。
- **移除/隔离 Windows 专属**：`__main__.py` 里的 `ctypes.windll.user32.MessageBoxW` 已有 `sys.platform.startswith("win")` 保护，安全；`webbrowser` 调用在安卓分支直接跳过。
- **`runtime.py`**：`_setup_console()` / `_install_excepthook()` 已做无控制台兜底，能直接复用；安卓上把 `sys.stdout/stderr` 接到 `android.util.Log`（Chaquopy 可配），异常栈打到 logcat。
- **`keepalive.py`**：桌面版「页面全关 → `os._exit(0)`」在安卓会直接杀进程。安卓分支**不启动 `_keepalive_watchdog`**，服务生命周期交给 Activity：`onCreate` 起服务、`onDestroy`（或进程被杀）自然结束。SSE 保活长连接本身保留（前端 `99-start.js` 会连，不影响）。

### 4.2 资源根注入

`config.get_base_dir()` 现在依次找 `sys._MEIPASS` / 包目录 / `sys.executable` 目录 / cwd。安卓上没有 `_MEIPASS`，打包进 APK 的 `web/`、`data/`（`block_icons.png`、`minecraft_blocks_mapcolor.json`）需要能被找到：

- **方案**：宿主启动时把 assets 里的 `web/`、`data/` 解压（或直接引用）到 `context.filesDir`，并通过环境变量把该目录传给 Python，例如 `MAPART_RESOURCE_ROOT=<filesDir>`。
- **改法**：`_root_candidates()` 开头加入 `os.environ.get("MAPART_RESOURCE_ROOT")`。这样 `get_base_dir()` / `web_index_path()` 无需大改。
- `maptool.data.blockdata` 是**普通 Python 模块**（`from .data import blockdata`），随 Chaquopy 打包即可，不占资源目录。

### 4.3 C++ 抖动核心

- 现状：`native/maptool_native.dll` 是 Windows PE + x86_64，由 MinGW g++ 编译，`-static` 消掉 `libwinpthread` 依赖。
- 目标：用 **Android NDK 的 clang** 交叉编译 `dither_core.cpp` → `libmaptool_native.so`（`-fPIC -shared`，目标 `aarch64-linux-android`）。
- 加载：`native/__init__.py` 里 ctypes 加载 `.dll` 改成按平台选择 `libmaptool_native.so`（用 `ctypes.CDLL("libmaptool_native.so")`，或打包后按绝对路径加载）。
- **首版可接受纯 Python 回退**：`native/__init__.py` 已有「DLL 不在 → 退纯 Python」逻辑，结果**逐像素一致**（`tests/native_match_equiv.py` 已证），只是慢。代价：CIEDE2000 + 抖动在手机上会更慢（桌面 5.3s，手机可能 15~30s）。M3 再补 `.so`。

### 4.4 下载 / 保存（后端配合）

后端路由 `/api/download/{tid}`、`/api/slice/file/{tid}/{index}`、`/api/slice/zip/{tid}` **不变**。真正要动的是**前端把数据交给系统**的方式（见 5.4）。后端无需感知安卓。

---

## 5. 前端移动端适配

现有交互大量面向桌面鼠标/键盘，需逐一映射（见 README「专注模式下的鼠标操作」与 `92-focus.js`、`88-repair.js`）。

### 5.1 触屏手势映射

| 桌面操作 | 移动端替代 | 备注 |
| --- | --- | --- |
| 右键拖动 = 平移 | 单指拖动 = 平移；或专门「抓手」工具 | 触屏没有右键，需决定单指语义（见下） |
| 左键拖动 = 套索/画笔 | 单指拖动 = 当前工具 | 与「平移」冲突，需用工具页签区分 |
| 滚轮缩放 | 双指捏合缩放 | pointer events 已可收到多点，需按双指间距算缩放 |
| 双击复位 | 双击复位 | 保留 |
| 悬停笔头方框 | 落笔前无法悬停 | 可去掉，或长按进入「预览笔头」 |
| Ctrl+Z / Ctrl+Shift+Z / Ctrl+Y | 屏幕上的撤回/重做按钮 | 键盘快捷键手机上不存在 |
| Shift+点色块 | 长按色块 或 双选开关 | 色块列表「设受害者/设笔刷」需要触屏入口 |

**单指语义的取舍**：桌面「左键=工具、右键=平移」在触屏只剩一根手指。建议：

- 预览区放**工具切换**（平移/套索/画笔 三态按钮），当前工具决定单指行为；
- 或者更符合移动端习惯：**单指平移、双指缩放，工具用「笔」模式后单指落笔**（类似画板 App）。此点需在 M2 用真机手感定案。

### 5.2 响应式布局

桌面是「左侧参数栏 + 右侧预览」双栏；手机竖屏要改成：

- 窄屏下主区与参数栏切**页签/抽屉**（预览页 + 参数页），或用底部 Tab。
- 预览图 `max-width` / 布局盒需按视口重排；`image-rendering: pixelated` 保留。
- 纯 CSS 媒体查询 + 少量 JS 控制页签即可，业务 DOM 结构不变（`web_split_check.py` 只校验拆分等价与加载，需确认新增样式/脚本不破坏 token 子序列约束——建议**新增独立文件** `js/9x-mobile.js` 和 `css/mobile.css`，不改原有 token 流）。

### 5.3 撤回 / 重做触屏入口

`89-undo.js` 现有侧边栏按钮 + 键盘快捷键。专注模式下侧边栏可能被藏起，移动端需：

- 预览区常驻悬浮的**撤回/重做按钮**（图标按钮，调用现有 `undo()`/`redo()` 即可，逻辑复用）。

### 5.4 下载 / 保存桥接（重点）

现状（`00-util.js` 的 `saveBlob`、`80-slice.js` 的保存）：

- 优先 `showSaveFilePicker` / `showDirectoryPicker`（**安卓 WebView 不支持**，会自然跳过）；
- 回退 `<a download>` + blob URL——**安卓 WebView 默认没有 `DownloadListener`，blob 下载不会落盘**。

**改动**：

1. 宿主通过 `addJavascriptInterface` 注入 `window.AndroidBridge.saveFile(name, base64)`（写入 `MediaStore.Downloads`，或先写应用私有目录再走 `FileProvider` + 系统分享）。
2. 前端 `saveBlob()` 在**检测到 `window.__ANDROID__`** 时优先把 Blob 读成 base64/ArrayBuffer 交给 bridge；否则维持现有桌面分支。
3. zip 这类**真实 http URL**（`/api/slice/zip/{tid}`）可交给 WebView 的 `DownloadListener` 兜底。
4. 前端通过宿主注入 `window.__ANDROID__ = true` 识别运行环境，桌面行为零变化。

### 5.5 文件上传

已有 `<input type="file">` + 拖放。手机上：点上传 → 系统相册/文件管理器选图，可用；拖放在触屏无意义，保留不影响。

---

## 6. 构建与打包

### 6.1 工程结构（建议）

```
android/                       ← 新增：安卓工程（源码侧，与 dist/ 区分）
├─ app/
│  ├─ build.gradle(.kts)       ← 引入 Chaquopy 插件与 Python 依赖
│  ├─ src/main/
│  │  ├─ kotlin|java/          ← MainActivity + WebView + JS bridge + 生命周期
│  │  ├─ python/               ← 后端 maptool 包（或引用 src/maptool）
│  │  ├─ assets/               ← web/ + data/（打包进 APK）
│  │  └─ AndroidManifest.xml
└─ settings.gradle
```

### 6.2 关键配置

- **Chaquopy**：`pip { install "numpy>=2.1" "fastapi" "uvicorn" "litemapy" "nbtlib" "pillow" "python-multipart" }`；ABI 选 `arm64-v8a`（主流，可按需加 `armeabi-v7a`）。
- **权限**：`INTERNET`（加载本地 URL + 将来局域网预留）；写公共下载目录用 `MediaStore`（Android 10+ 无需 `WRITE_EXTERNAL_STORAGE`，低版本按需声明）。
- **明文 HTTP**：`127.0.0.1` 的明文需在 Manifest 设 `android:usesCleartextTraffic="true"`，或用 Network Security Config 仅放行 loopback。
- **资源注入**：启动时把 `assets/` 下的 `web/`、`data/` 解压到 `filesDir`，并 `System.setenv("MAPART_RESOURCE_ROOT", ...)`（Chaquopy 允许在 Python 启动前设环境变量）。

### 6.3 打包

- `gradlew assembleDebug` → 调试 APK；`bundleRelease` + 签名 → 上架用 AAB。
- 图标、应用名、版本号：与 `maptool/__init__.py` 的 `__version__` 对齐，构建脚本可自动带进 Manifest。

---

## 7. 测试与验证

| 项 | 做法 |
| --- | --- |
| 后端算法回归 | 现有 `tests/regression_check.py`、`slice_check.py`、`repair_check.py` 等**纯后端**测试，在安卓 Python 环境（或 PC 上带 `MAPART_RESOURCE_ROOT` 的模拟）照跑 |
| 前端渲染 | `tests/web_split_check.py` 在桌面无头浏览器跑；新增的 `mobile.css/js` 需确认不破坏「原 token 流为子序列」约束 |
| 原生核心等价 | `tests/native_match_equiv.py`（1677 万色逐位比对）在真机/模拟器跑一遍，证明 `.so` 与 numpy 版一致 |
| 真机清单 | 上传图片→生成→下载投影；切分→多文件保存到「下载」；噪点修正套索/画笔触屏操作；双指缩放/单指平移；撤回重做；地衣批量修改 |

---

## 8. 里程碑

| 阶段 | 内容 | 验收 |
| --- | --- | --- |
| **M0（本次）** | 建 `android` 分支 + 本方案文档 | 方案评审通过 |
| **M1 原生壳** | Chaquopy 工程 + WebView 加载本地 FastAPI；后端平台分支（入口/资源根/日志/保活）；纯 Python 抖动回退 | 手机能打开界面、上传图片、生成并预览地图画 |
| **M2 移动端交互** | 触屏手势、响应式布局、撤回重做按钮、下载桥接到系统下载/分享 | 真机跑通「生成 + 切分 + 保存」完整流程 |
| **M3 性能** | NDK 交叉编译 `dither_core.cpp` → `libmaptool_native.so`，真机等价性测试 | CIEDE2000 等算法性能达标、结果逐像素一致 |
| **M4 发布** | 签名、图标、权限最小化、AAB 打包 | 可安装、可交付 |

---

## 9. 风险与对策

| 风险 | 影响 | 对策 |
| --- | --- | --- |
| numpy/pillow 的 Android 轮子与桌面版本（3.13/3.14、numpy≥2.1）不完全一致 | 数值/结果可能微差 | 以 Chaquopy 支持矩阵就近选 Python 版本；纯 Python 模块（litemapy/nbtlib）无 ABI 问题；用 `native_match_equiv.py` 守护逐像素一致性 |
| uvicorn 事件循环在安卓 | 启动失败/性能 | 明确用纯 asyncio 事件循环，禁用 uvloop/httptools（原生）；ChaquoPy 进程内起线程已验证可行 |
| C++ 核心交叉编译 | `.so` 加载失败 | 首版纯 Python 回退兜底；NDK clang 编纯 C ABI（`extern "C"`）无跨 CRT 问题；真机跑等价性测试 |
| WebView 下载不落盘 | 用户拿不到文件 | JS bridge 写 `MediaStore.Downloads` + `DownloadListener` 兜底；提供「分享」出口 |
| SSE 保活 + `os._exit` 看门狗 | 误杀进程 | 安卓禁用看门狗，生命周期交给 Activity |
| 手机内存/性能（大图、任务缓存） | OOM / 卡顿 | 评估 `PREVIEW_MAX_SIDE=1024`、任务/图片缓存上限；必要时安卓下调默认值 |
| 明文 HTTP 被 WebView 拦截 | 页面打不开 | `usesCleartextTraffic` 或 Network Security Config 放行 loopback |
| `web_split_check.py` 的 token 子序列约束 | 新增前端代码被判定破坏 | 新增功能放独立 `mobile.css/js`，不改原有 token 流；必要时把「允许新增」白名单扩展 |
