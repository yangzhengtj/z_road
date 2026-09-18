# device_mpy —— M5Stack Cardputer ADV 前端（v0.8.1）

在 BeanpieChen《MicroPython Shell for Cardputer ADV》固件（MicroPython
v1.26.1，内置 dev 帧缓冲/键盘驱动）上运行的设备前端。规则核心 `core/`
零改动复用，本目录只写设备相关代码：

| 文件 | 职责 |
| --- | --- |
| `screen.py` | 30 半角列 × 8 行逻辑字符屏（紧凑 array 存储、汉字占两列、自动折行、色块、缺字收集），渲染零堆分配；**中文一律走 UTF-8 bytes 整数码点流（`iter_codes`/`row_bytes`），禁止 `s[i]`/`for ch in s`（会永久驻留 qstr，见模块头注释）** |
| `font_data.py` + `font.bin` | 点阵字库（自动生成，勿手改）：ASCII 8×16、CJK/符号 16×16；字形在 font.bin 中按字 32 字节存放，按需 seek+readinto 单缓冲复用，不常驻 |
| `card_catalog.py` | 分阶段数据目录：迷你卡分阶段常驻（开机只载阶段 I），完整卡按 JSONL 行号流式升级/降级，2KB 储备块 |
| `microjson.py` | 零依赖流式 JSON 解析（32B 块 readinto、按行定位、UTF-8 透传），避开 ujson 整包解析峰值 |
| `stores.py` | microSD JSON 存档/历史最佳（分块流式写，格式与桌面端一致） |
| `device_app.py` | 小屏对局全流程（分页、菜单、数字输入、选路、战斗、药剂多选、轮末、终局、读档） |
| `mp_frontend.py` | 真机适配：dev.fb 帧缓冲、TCA8418 键盘事件、shell.hide 接管、暗红错误屏、退出 reset |
| `sim.py` | CPython ANSI 模拟器：终端可直接玩，脚本按键/录屏供自动化测试 |

## 构建卡包

```bash
pip install mpy-cross==1.26.1.post2         # 首次：版本必须与固件 v1.26.1 一致
.venv/bin/python tools/build_mpy_font.py     # 改过界面文案后重跑补字
.venv/bin/python tools/build_mpy_bundle.py   # 生成 dist_cardputer/（.mpy 预编译）
```

`build_mpy_bundle.py` 会用 mpy-cross 把包内所有 .py 预编译成 .mpy
字节码（仅 `main.py` 保留源码，Shell 的 run() 以 exec(open()) 执行它）。
设备内存有限，现场编译 font_data.py 等大文件会 MemoryError，因此**卡上
只放 .mpy，不要把 src 的 .py 散文件直接拷上去**。

把 `dist_cardputer/` 内的 `main.py`、`zroad/`、`saves/` 放到 **MBR +
FAT32** microSD 的 `zroad/` 目录（即 `/sd/zroad/`；大容量 SDXC 可只建
一个 4GB 的 FAT32 MBR 分区，exFAT 不被识别），Shell 中执行
`run('/sd/zroad/main.py')` 启动。详见《玩家使用指南》§2.5。

## 电脑端模拟器（无需硬件）

```bash
.venv/bin/python -m zroad.platforms.device_mpy.sim
```

整局自动化测试见 `tests/test_device_app.py`（脚本按键驱动简单/困难
整局、缺字守门、字库全覆盖）与 `tests/test_microjson.py`（流式解析
与标准 json 逐行等价）。

## 真机内存模拟与限堆回归（tools/mpy_hw_sim/）

真机无 PSRAM，导入全部模块后仅剩约 57KB。`tools/mpy_hw_sim/` 按固件
v1.2 真实接口做桩，用 brew 版 MicroPython v1.19.1（mpy v6.1 卡包）
在电脑上模拟真机，改代码后必须先在此跑绿再拷卡：

```bash
.venv/bin/python tools/mpy_hw_sim/build_hw_sim.py        # 构建 v6.1 模拟卡包（含整局+errtest）
/opt/homebrew/bin/micropython -X heapsize=195K tools/mpy_hw_sim/hw_runner.py
/opt/homebrew/bin/micropython -X heapsize=190K tools/mpy_hw_sim/hw_runner.py errtest
/opt/homebrew/bin/micropython -X heapsize=195K tools/mpy_hw_sim/restore_probe.py save
/opt/homebrew/bin/micropython -X heapsize=195K tools/mpy_hw_sim/restore_probe.py load
```

195K 堆导入后空闲约 56.5KB，与真机 57.3KB 等价；restore_probe 的
save/load 必须分两个进程跑（真机读档也是全新开机），且每次 load 前
重新 save 生成中途档（上一局打完会把 auto 覆盖为终局档，终局档在读
档菜单中置灰，会造成假失败）。

