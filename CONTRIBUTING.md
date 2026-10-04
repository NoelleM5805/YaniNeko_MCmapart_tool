# 贡献指南

感谢你愿意参与 **YaniNeko_MCmapart_tool · 地图画工具箱** 的开发。这份指南帮你把本地环境
跑起来、跑通测试，并提交符合本项目习惯的改动。

## 目录

- [环境准备](#环境准备)
- [快速开始](#快速开始)
- [项目结构](#项目结构)
- [测试](#测试)
- [代码习惯](#代码习惯)
- [提交与 PR](#提交与-pr)

## 环境准备

| 依赖 | 说明 |
| --- | --- |
| Python 3.13 / 3.14 | 依赖 numpy ≥ 2.1（1.26.x 在 3.13+ 上 `import` 即崩溃） |
| MinGW-w64 g++（可选） | 只用于重编 C++ 抖动核心；不装也能跑，会自动退回纯 Python |
| Microsoft Edge（可选） | 浏览器端测试需要，默认找 `C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe` |

安装依赖：

```bat
pip install -r requirements.txt
```

打包发行版还需要：

```bat
pip install -r requirements-dev.txt
```

## 快速开始

```bat
python src\run_maptool.py
```

浏览器会自动打开 `http://127.0.0.1:8765`（端口被占会自动向后找空闲端口）。

源码入口只有一个：`src\run_maptool.py`。不要直接 `python src\maptool\__main__.py`
（相对导入会失败）。

## 项目结构

| 路径 | 说明 |
| --- | --- |
| `src/maptool/` | 后端 Python 包，模块自下而上单向依赖、无循环（职责见 `docs/ARCHITECTURE.md`） |
| `src/maptool/web/` | 前端（原生 JS，按 `NN-name.js` 编号顺序加载，共享全局作用域，非 ES module） |
| `src/maptool/data/` | 方块表与图标贴图集（**生成物，不要手改**） |
| `tools/` | 开发脚本（生成数据、打包、编译 C++ 核心） |
| `tests/` | 回归测试（纯脚本，直接 `python tests\xxx.py`，不用 pytest） |
| `legacy/` | 历史版本留档 + 回归测试的「旧版基准」 |
| `docs/` | 架构说明 + 长期维护指南 |
| `packaging/` | 发行版说明文件源 |

## 测试

改完代码先跑三条**只读自检**（都应退出码 0）：

```bat
python tools\build.py --check
python tools\check_icons.py
python tools\build_native.py --check
```

**后端回归**（快，不依赖浏览器 / 旧版）：

```bat
python tests\repair_check.py
python tests\slice_check.py
```

**完整回归**（改了后端 API / 前端 / 打包 / C++ 核心时跑，详见 README「怎么验证没改坏」）：

```bat
python tests\regression_check.py      # 需要 legacy 旧版基准
python tests\web_split_check.py       # 需要无头 Edge
python tests\slice_web_check.py       # 需要无头 Edge
python tests\native_match_equiv.py    # 全 1677 万颜色逐位比对（较慢）
python tests\dither_native_equiv.py   # 900 例端到端逐像素比对
python tests\repair_web_check.py      # 需要无头 Edge
python tests\page_load_check.py       # 需要无头 Edge
```

说明：

- 浏览器测试用无头 Edge 的 `--dump-dom` 驱动，**不用 selenium**。
- `native_match_equiv.py` / `dither_native_equiv.py` 是「C++ 与纯 Python 结果
  逐像素一致」的证明，改 C++ 核心后**必须**跑。

## 代码习惯

- **注释和文档用中文**，和现有代码保持一致。
- 后端模块**自下而上单向依赖**，不要引入循环依赖（顺序见 `docs/ARCHITECTURE.md` 第二节）。
- 前端拆成多个 `<script>` 共享全局作用域，**不是 ES module**；加载顺序就是
  `NN-name.js` 的编号顺序，加新文件要接在后面，并保证不跨文件引用「后面才声明的
  名字」（`web_split_check.py` 有专门检查）。
- 生成物（`data/blockdata.py`、`block_icons.png`、`native/maptool_native.dll`）
  不要手改，分别用 `tools/gen_blockdata.py` / `tools/build_native.py` 重新生成。
- 控制台输出里 GBK 编不出的符号（`✓ ✗ ⚠`）要么改用 `√ × !`，要么给 stdout 加
  `errors="replace"` 兜底（参考 `src/maptool/runtime.py` 和 `tools/check_icons.py`）。

## 提交与 PR

- 一次提交只做一类事（修 bug / 改文档 / 升版本）。
- 提交信息简短、说明动机；仓库历史是日期式短信息，不强求一致，但可读性更好。
- 发 PR 前：跑完相关测试、确认 `git status` 干净、别把 `build/`、`dist/`、
  `__pycache__/`、`tests/_tmp/` 提交进来（它们已在 `.gitignore` 里）。
- 涉及功能变化时，同步更新 `README.md` / `docs/ARCHITECTURE.md` / `docs/MAINTENANCE.md`。

更多例行维护操作（发版、更新方块表、重编 C++、仓库瘦身）见 `docs/MAINTENANCE.md`。
