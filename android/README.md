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

### 0. 环境要求（本机无法构建，需在装了以下工具的机器上执行）

- **Android Studio**（含 Android SDK Platform 35、Build-Tools、`arm64-v8a` NDK）
- JDK 17
- 首次构建需联网（拉 Gradle 8.7、AGP 8.5.2、Chaquopy 16.0.0，以及 numpy/fastapi 等 Python 轮子）

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
- **下载**：
  - 单文件（地图画 / 地衣）→ 前端 `saveBlob` 走 `window.AndroidBridge.saveFile(name, base64)`，
    宿主写 `MediaStore.Downloads`（系统「下载」目录）。
  - 真实 URL（切分的「打包成 zip」）→ WebView `DownloadListener` 交给系统 `DownloadManager`。
  - 切分的「保存全部投影文件」（多文件 blob 下载）在安卓上暂未桥接，请先用「打包成 zip」。
- **移动端交互**：前端新增 `web/css/mobile.css`（响应式）+ `web/js/98-mobile.js`
  （双指捏合缩放、撤回/重做悬浮按钮、下载桥接），仅由 `window.__ANDROID__` 触发，
  桌面行为零变化。

## 注意事项 / 待办

- **C++ 抖动核心**：安卓上暂用纯 Python 回退（结果逐像素一致，只是慢）。加速需用 NDK 把
  `dither_core.cpp` 交叉编译成 `libmaptool_native.so`（arm64-v8a），`native/__init__.py`
  已支持按裸名加载 `.so`。
- **版本号**：`app/build.gradle` 里的 `versionName` 需与 `src/maptool/__init__.py` 的
  `__version__` 保持一致。
- **Python 版本**：Chaquopy 的 `version = "3.12"` 以 Chaquopy 支持矩阵为准；桌面用 3.13/3.14，
  若 Chaquopy 已支持更高版本可对齐。
- **minSdk 29**：为用 `MediaStore.Downloads` 简化保存；如需支持更老设备，请回退到
  `WRITE_EXTERNAL_STORAGE` + 直接写公共目录。
- **真机验证**：M1（原生壳跑通）之后需在真机验证上传→生成→保存、切分→zip 下载、
  双指捏合、撤回/重做等交互。
