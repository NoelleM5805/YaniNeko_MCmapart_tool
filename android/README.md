# 安卓端工程（Chaquopy + WebView）

把 **YaniNeko_MCmapart_tool** 打包成安卓原生 App：进程内用 **Chaquopy** 跑 Python 后端
（FastAPI），前端用系统 **WebView** 加载本地服务 `http://127.0.0.1:<port>`。整机在手机上运行。

> 详细方案见 [`../docs/ANDROID_PLAN.md`](../docs/ANDROID_PLAN.md)。

## 目录结构

```
android/
├─ settings.gradle.kts / build.gradle / gradle.properties
├─ gradle/wrapper/gradle-wrapper.properties
├─ prepare.py                 ← 把后端源码复制进工程（构建前跑一次）
└─ app/
   ├─ build.gradle            ← Chaquopy 插件 + Python 依赖
   └─ src/main/
      ├─ AndroidManifest.xml
      ├─ java/com/yanineko/maptool/MainActivity.java   ← WebView + JS 桥接 + 生命周期
      ├─ python/main.py        ← 安卓宿主入口（设 MAPART_PLATFORM，起服务）
      ├─ python/maptool/       ← 由 prepare.py 生成（gitignore，不提交）
      └─ res/values/strings.xml
```

## 构建步骤

### 0. 环境要求

- **Android Studio**(含 SDK Platform 35、Build-Tools 34/35、`arm64-v8a` NDK;SDK 默认在 `%LOCALAPPDATA%\Android\Sdk`)
- **JDK 17**(`C:\Program Files\Java\jdk-17`;注意 Android Studio 自带的 JBR 是 JDK 25,与 AGP 8.5.2 不兼容)
- Gradle 8.7(见下「Gradle」)
- 首次构建需联网(拉 AGP 8.5.2、Chaquopy 16.0.0、以及 numpy/fastapi 等 Python 轮子)

### 1. 复制后端源码

在工作区根目录执行：

```bat
python android\prepare.py
```

这会把 `src/maptool/`（含 `web/`、`data/`，排除 `.dll`/`.pyc`/`__pycache__`）
复制到 `android/app/src/main/python/maptool/`。

### 2. 生成 Gradle wrapper（若目录里没有 `gradlew`）

本仓库只提交了 `gradle-wrapper.properties`，未提交二进制的 `gradle-wrapper.jar`。
用 Android Studio 打开 `android/` 会自动补全；或用本机 gradle：

```bat
cd android
gradle wrapper --gradle-version 8.7
```

### 3. 构建 / 安装

```bat
cd android
gradlew assembleDebug          :: 调试 APK
gradlew installDebug           :: 装到已连接的手机/模拟器
gradlew bundleRelease          :: 上架用 AAB（需先配签名）
```

## 关键设计

- **后端复用**：`src/maptool` 原样打包，`webapp.py`、全部算法模块不动。
- **平台分支**：`main.py` 设 `MAPART_PLATFORM=android`，于是 `__main__.start_server()`
  自动不开浏览器、不起「关页面自动退出」看门狗；生命周期交给 `MainActivity`。
- **资源定位**：`web/`、`data/` 都在 `maptool` 包内，后端 `get_base_dir()` 靠 `__file__`
  直接找到，无需额外解压。若以后改用 assets，`config.py` 也支持
  `MAPART_RESOURCE_ROOT` 环境变量注入。
- **文件上传**：`<input type="file">` 在 WebView 里**必须**由 `WebChromeClient.onShowFileChooser`
  + `onActivityResult` 接管，否则点了毫无反应（前端是「点上传区 → `input.click()`」触发）。
  已实现：`accept="image/*"` 走系统照片选择器，`.litematic` 走 DocumentsUI（MIME 拿不到时
  回退 `*/*`）。
- **下载**：
  - 单文件（地图画 / 地衣）→ 前端 `saveBlob` 走 `window.AndroidBridge.saveFile(name, base64)`，
    宿主写 `MediaStore.Downloads`（系统「下载」目录）。
  - 真实 URL（切分的「打包成 zip」）→ WebView `DownloadListener` 交给系统 `DownloadManager`。
  - 切分的「保存全部投影文件」（多文件 blob 下载）在安卓上暂未桥接，请先用「打包成 zip」。
- **移动端交互**：前端新增 `web/css/mobile.css`（响应式）+ `web/js/98-mobile.js`
  （双指捏合缩放、撤回/重做悬浮按钮、下载桥接），仅由 `window.AndroidBridge` /
  `window.__ANDROID__` 触发，桌面行为零变化。
  > `__ANDROID__` 由 `WebViewClient.onPageFinished` 注入（页面导航会重置 window，
  > 不能在 `loadUrl` 之前注入）；`AndroidBridge` 不受导航影响，是更可靠的标记。

## 注意事项 / 待办

- **C++ 抖动核心**：安卓上暂用纯 Python 回退（结果逐像素一致，只是慢）。加速需用 NDK 把
  `dither_core.cpp` 交叉编译成 `libmaptool_native.so`（arm64-v8a），`native/__init__.py`
  已支持按裸名加载 `.so`。
- **版本号**：`app/build.gradle` 里的 `versionName` 需与 `src/maptool/__init__.py` 的
  `__version__` 保持一致。
- **minSdk 29**：为用 `MediaStore.Downloads` 简化保存；如需支持更老设备，请回退到
  `WRITE_EXTERNAL_STORAGE` + 直接写公共目录。
- **真机验证**：M1（原生壳跑通）之后需在真机验证上传→生成→保存、切分→zip 下载、
  双指捏合、撤回/重做等交互。

## 本机构建实测（2026-04，已出 APK）

本机（Windows + JDK 17 + Gradle 8.7）已成功构建出 `app/build/outputs/apk/debug/app-debug.apk`
（约 38.6 MB，`arm64-v8a`）。踩过的坑与对应处理（都已写进本工程）：

| 坑 | 处理 |
| --- | --- |
| 项目路径含中文 `D:\地图画工具` | `gradle.properties` 里 `android.overridePathCheck=true` |
| Gradle 官方源被墙 | 用腾讯镜像下 `gradle-8.7-bin.zip`，解压后用其 `bin\gradle.bat`（`JAVA_HOME` 指向 JDK 17） |
| pip 访问 pypi/chaquo.com 报 SSL 证书校验失败（Steam++ 代理 MITM） | `app/build.gradle` 里 `pip { options "--trusted-host", ... }` |
| fastapi 0.142 强制 pydantic v2 → pydantic-core（Rust）无安卓轮子 | 退回 `fastapi==0.115.6` + `pydantic==1.10.15`（纯 Python，本后端不用 pydantic 模型） |
| pydantic 1.10 在 Python 3.12.4+ 崩溃（`ForwardRef._evaluate` 多了 `recursive_guard`） | Chaquopy `version = "3.11"`（pydantic 1.10 在 3.11 原生兼容） |
| **切换 Python 版本后**打包的仍是旧版原生扩展（cp312 轮子） | **先 `gradle clean` 再重编**，否则 `requirements-*.imy` 里的 numpy/PIL 原生库还是旧 ABI |
| `kotlin-stdlib` 1.8.22 与传递依赖的 `jdk7/jdk8` 1.6.21 重复类 | `configurations.configureEach` 强制统一到 1.8.22 |

构建命令（本机）：

```bat
set JAVA_HOME=C:\Program Files\Java\jdk-17
set ANDROID_HOME=%LOCALAPPDATA%\Android\Sdk
D:\gradle-8.7\bin\gradle.bat assembleDebug
```

安装到真机（arm64，需开 USB 调试）：

```bat
%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe install -r app\build\outputs\apk\debug\app-debug.apk
```

> 注意：当前 APK 只编了 `arm64-v8a`（真机主流）。本机 SDK 里的模拟器系统镜像是
> x86_64，跑这个 arm64 APK 需要 ARM 转译、较慢；如要用 x86_64 模拟器，请在
> `app/build.gradle` 的 `abiFilters` 里加 `"x86_64"` 后重编。

## 模拟器实测（MuMu 12 · Android 15 · arm64 转译）

已用 **MuMu Player 12**（adb `127.0.0.1:16384`）实机验证通过：

- 后端在进程内正常启动，`√ 服务器就绪：http://127.0.0.1:8765`
- `GET /api/palette` → 200（290 方块，与桌面一致）
- 上传图片 → 预览（加权 RGB + Floyd 抖动）→ 生成 `.litematic` 全链路 200
- `native.available=False`（C++ 核心退回纯 Python，符合预期）

> MuMu 的 ARM 转译会缓存原生库；换 ABI/Python 版本后若报 `dlopen libpython3.X.so not found`，
> 先卸载重装、必要时重启模拟器清除转译缓存。
