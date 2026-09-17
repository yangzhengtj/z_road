# device_mpy —— M5Stack Cardputer ADV 前端（v0.8.0）

在 BeanpieChen《MicroPython Shell for Cardputer ADV》固件（MicroPython
v1.26.1，内置 dev 帧缓冲/键盘驱动）上运行的设备前端。规则核心 `core/`
零改动复用，本目录只写设备相关代码：

| 文件 | 职责 |
| --- | --- |
| `screen.py` | 30 半角列 × 8 行逻辑字符屏（汉字占两列、自动折行、色块、缺字收集），光栅化只依赖后端 `fill_rect/stroke_rect/present` |
| `font_data.py` | 点阵字库（自动生成，勿手改）：ASCII 8×16、CJK/符号 16×16 |
| `stores.py` | microSD JSON 存档/历史最佳（ujson、uos 注入，格式与桌面端一致） |
| `device_app.py` | 小屏对局全流程（分页、菜单、数字输入、选路、战斗、药剂多选、轮末、终局） |
| `mp_frontend.py` | 真机适配：dev.fb 帧缓冲、TCA8418 键盘事件、shell.hide 接管、退出 reset |
| `sim.py` | CPython ANSI 模拟器：终端可直接玩，脚本按键/录屏供自动化测试 |

## 构建卡包

```bash
.venv/bin/python tools/build_mpy_font.py     # 改过界面文案后重跑补字
.venv/bin/python tools/build_mpy_bundle.py   # 生成 dist_cardputer/
```

把 `dist_cardputer/` 内的 `main.py`、`zroad/`、`saves/` 放到 FAT32
microSD 的 `zroad/` 目录（即 `/sd/zroad/`），Shell 中执行
`run('/sd/zroad/main.py')` 启动。详见《玩家使用指南》§2.5。

## 电脑端模拟器（无需硬件）

```bash
.venv/bin/python -m zroad.platforms.device_mpy.sim
```

整局自动化测试见 `tests/test_device_app.py`（脚本按键驱动简单/困难
整局、缺字守门、字库全覆盖）。

