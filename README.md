# 亡命之途 · 文字版（Z-Road）

基于桌游 **Hit Z Road（亡命之途）** 的规则框架、使用个人整理改编的卡牌内容开发的**纯文字**单机游戏。

## 开发目标与范围

- **阶段 1（当前）**：Mac 终端单人 SOLO 版。Python + Rich 纯文字界面，支持存档/读档、局后统计与评级。
- **阶段 2**：移植到 M5Stack Cardputer ADV（MicroPython 设备前端，规则核心保持同一份可移植代码）。
- **阶段 3**：两人同设备热座对战（移除竞拍，开局双方掷 D6 比大小定先手、平局重掷，之后每轮轮换先手）。
- **明确不做**：3–4 人模式、联网对战、任何图片/图形渲染。

## 技术栈

- **规则核心**：纯 Python，仅使用 MicroPython 兼容子集（零第三方依赖、不做 I/O），数据驱动——卡牌、骰子、规则常量全部 JSON 化。
- **Mac 前端**：CPython + Rich（界面复杂度上升后再评估 Textual）。
- **设备前端**：MicroPython（阶段 2）。
- **测试**：无头前端 + pytest 规则回归（当前 85 项全部通过）。

## 目录结构

```
z_road/
├── data/                     # 数据层：cards/dice/config 三个 JSON（运行时唯一内容来源）
├── tools/md_to_json.py       # 由 Markdown 源表重新生成卡牌/骰面 JSON（仅用标准库）
├── src/zroad/
│   ├── core/                 # 可移植规则核心（MicroPython 子集，零第三方依赖）
│   ├── platforms/
│   │   ├── desktop_rich/     # Mac Rich 终端前端（阶段 1）
│   │   └── device_mpy/       # Cardputer MicroPython 前端（阶段 2）
│   └── headless/             # 无头前端，供自动化测试
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

存档默认写在仓库根 `saves/`（已忽略入库），可用环境变量 `ZROAD_SAVE_DIR` 改位置。

## 文档索引

- `docs/开发任务规划.md`：**主文档**，含规则口径、分层架构、MicroPython 可移植规范、任务分解与版本记录，每次开发完成后更新。
- `data/README.md`：数据层结构与再生成说明。
- `docs/玩家使用指南.md`：安装、启动、操作与存档说明（v0.6.1 起）。

## 资料来源与声明

- 规则框架源自桌游《Hit Z Road / 亡命之途》（设计：Martin Wallace，2016，SPACE Cowboys / Asmodee；繁体中文版：栢龙玩具）。
- 游戏文本与卡牌数据为个人整理与改编，**本仓库仅用于个人学习与非商业开发**。

## 协作约定

- 默认分支：`main`。
- 提交信息格式：`类型: 中文说明`，类型包括 feat（新功能）、fix（修复）、data（卡牌/骰子数据）、test（测试）、docs（文档）、chore（杂项）。
- 开发协作方负责 `git add` / `git commit`，仓库所有者手动执行 `git push`。
