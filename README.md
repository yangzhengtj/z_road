# 末路求生 · 文字版（Z Road）

一款末世公路生存题材的**纯文字**单机游戏：规则框架灵感来源于桌游 **Hit Z Road（亡命之途）**，全部卡牌场景文本为个人原创改编，不含任何原作美术素材。

## 开发目标与范围

- **阶段 1（v0.7.1 已收官）**：Mac/Windows/Linux 终端单人 SOLO 版。Python + Rich 纯文字界面，支持存档/读档、简单/困难难度、分阶段局后统计（资源收支/骰面分布/对局回看）、历史最佳与评级；提供三平台免安装单文件与 pipx 安装。
- **阶段 2（当前，v0.8.1）**：移植到 M5Stack Cardputer ADV（MicroPython v1.26.1，BeanpieChen Shell 固件）。同一套零依赖规则核心 + 全新设备前端（30×8 字符屏、自带点阵中文字库、microSD 存档），电脑端提供 ANSI 模拟器与脚本整局测试；拷卡即用。
- **阶段 3**：两人同设备热座对战（移除竞拍，开局双方掷 D6 比大小定先手、平局重掷，之后每轮轮换先手）。
- **明确不做**：3–4 人模式、联网对战、任何图片/图形渲染。

## 下载试玩（无需 Python）

推送 `v*` 标签后，GitHub Actions 自动构建三平台单文件并发布到 **Releases**：

- Mac：`zroad-vX-macos-arm64`（M 系列）/ `zroad-vX-macos-x86_64`（Intel）
- Windows：`zroad-vX-windows-x86_64.exe`
- Linux：`zroad-vX-linux-x86_64`

首次运行的安全提示绕过方式见《玩家使用指南》2.1 节。熟悉 Python 的玩家也可用
`pipx install git+https://github.com/yangzhengtj/z_road.git` 安装。

## 技术栈

- **规则核心**：纯 Python，仅使用 MicroPython 兼容子集（零第三方依赖、无 random/re/enum/dataclasses、不做 I/O），数据驱动——卡牌、骰子、规则常量全部 JSON 化；RNG 为 core 自带纯 Python MT19937，桌面与设备同一序列。
- **Mac/PC 前端**：CPython + Rich。
- **设备前端**：MicroPython（`src/zroad/platforms/device_mpy/`，逻辑字符屏 + 点阵字库 + dev.fb/TCA8418 适配；`sim.py` 为电脑端 ANSI 模拟器）。
- **分发**：桌面端 PyInstaller 单文件（`packaging/zroad.spec`）+ GitHub Actions 三平台矩阵（`.github/workflows/release.yml`，打 tag 即发布）；Cardputer 用 `tools/build_mpy_bundle.py` 生成 microSD 卡包。
- **测试**：无头前端 + pytest 规则回归 + 设备端脚本整局与缺字守门（当前 143 项全部通过）。

## 目录结构

```
z_road/
├── src/zroad/
│   ├── data/                 # 数据层（随包发布）：cards/dice/config 三个 JSON
│   ├── core/                 # 可移植规则核心（MicroPython 子集，零第三方依赖）
│   ├── platforms/
│   │   ├── desktop_rich/     # Mac/PC Rich 终端前端（阶段 1，含 paths 打包路径适配）
│   │   └── device_mpy/       # Cardputer MicroPython 前端（阶段 2）
│   └── headless/             # 无头前端，供自动化测试
├── tools/md_to_json.py       # 由 Markdown 源表重新生成卡牌/骰面 JSON（仅用标准库）
├── packaging/                # PyInstaller spec 与打包依赖
├── .github/workflows/        # release.yml：tag 推送后三平台构建并发布 Release
├── tests/                    # pytest 测试
├── docs/开发任务规划.md       # 任务拆解、架构设计、进度与版本记录（先读它）
└── 参考资料/                  # 卡牌.md、骰子.md 源表（人工维护）与历史 xls，不随程序打包
```

## 开发环境准备（macOS）

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

### 常用命令

```bash
# 修改 参考资料/ 下的卡牌.md、骰子.md 后，重新生成数据层
python3 tools/md_to_json.py

# 运行测试
python3 -m pytest

# 安装并启动游戏（可编辑安装，注册 zroad 命令）
pip install -e ".[desktop]"
zroad            # 或：python -m zroad
```

存档位置：源码开发模式写在仓库根 `saves/`（已忽略入库）；pipx 安装版与免安装单文件写在用户目录 `~/.zroad/saves/`（Windows 为 `C:\Users\<用户>\.zroad\saves\`）；均可用环境变量 `ZROAD_SAVE_DIR` 改位置。

### 打包单文件（可选，本地验证用）

三平台正式产物由 GitHub Actions 构建；想在本机构建当前系统的单文件：

```bash
pip install -r packaging/requirements-build.txt
pyinstaller packaging/zroad.spec --noconfirm --clean
# 产物在 dist/zroad（Windows 为 dist\zroad.exe）
```

## 文档索引

- `docs/开发任务规划.md`：**主文档**，含规则口径、分层架构、MicroPython 可移植规范、任务分解与版本记录，每次开发完成后更新。
- `src/zroad/data/README.md`：数据层结构与再生成说明。
- `docs/玩家使用指南.md`：安装（免安装/pipx/源码/Cardputer 拷卡）、启动、操作与存档说明（v0.8.0 起含掌机章节 §2.5）。

## 资料来源与版权声明

- 本项目是**非官方、非商业的粉丝学习作品**，与桌游 Hit Z Road 的版权方 SPACE Cowboys / Asmodee 及繁体中文版发行方栢龙玩具**没有任何关联、授权或代言关系**。
- "Hit Z Road""亡命之途"等名称的权利归各自所有者，本仓库仅在说明规则灵感来源时作**描述性引用**，游戏自身使用《末路求生·文字版》这一名称，不主张任何相关商标权利。
- 游戏规则、玩法机制本身不受著作权保护；本项目**未复制**原作说明书文字、卡面插画、盒面美术、照片、Logo 或排版，全部场景文本为个人原创写作。
- 规则框架参考桌游《Hit Z Road / 亡命之途》（设计：Martin Wallace，2016，SPACE Cowboys / Asmodee；繁体中文版：栢龙玩具），在此向原作者与发行方致谢。
- 本仓库**仅用于个人学习与非商业开发**，不收费、不接受赞助或打赏。如权利方认为本项目内容存在不妥，请联系，我会在核实后第一时间下架或修改。

## 协作约定

- 默认分支：`main`。
- 提交信息格式：`类型: 中文说明`，类型包括 feat（新功能）、fix（修复）、data（卡牌/骰子数据）、test（测试）、docs（文档）、chore（杂项）。
- 开发协作方负责 `git add` / `git commit`，仓库所有者手动执行 `git push`。
