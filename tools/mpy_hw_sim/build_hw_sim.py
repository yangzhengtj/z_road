#!/usr/bin/env python3
"""build_hw_sim.py —— 构建“真机固件模拟”用的 .mpy 卡包并运行。

背景：本机用 Homebrew 的 MicroPython unix 移植版（v1.19.1，mpy v6.1）
做真机模拟，而真机固件是 v1.26.1（mpy v6.3）。因此这里把 src 下的
设备端代码用 mpy-cross 交叉编译为 v6.1 字节码放进 bundle/，仅用于
本机模拟；真机卡包仍由 tools/build_mpy_bundle.py 产出 v6.3。

用法：
    .venv/bin/python tools/mpy_hw_sim/build_hw_sim.py
随后自动调用 micropython 运行 hw_runner.py。
"""

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SRC = os.path.join(ROOT, "src", "zroad")
HERE = os.path.dirname(os.path.abspath(__file__))
BUNDLE = os.path.join(HERE, "bundle")
MPY_CROSS = os.path.join(ROOT, ".venv", "bin", "mpy-cross")
MICROPYTHON = shutil.which("micropython") or "/opt/homebrew/bin/micropython"

# 只编译设备端会用到的模块：core 全包 + device_mpy（排除 sim）
EXCLUDE_FILES = {"sim.py"}
EXCLUDE_DIRS = {"desktop_rich", "__pycache__"}


def compile_tree():
    count = 0
    for dirpath, dirnames, filenames in os.walk(SRC):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for name in filenames:
            if not name.endswith(".py") or name in EXCLUDE_FILES:
                continue
            src_file = os.path.join(dirpath, name)
            rel = os.path.relpath(src_file, os.path.join(ROOT, "src"))
            out_file = os.path.join(BUNDLE, rel[:-3] + ".mpy")
            os.makedirs(os.path.dirname(out_file), exist_ok=True)
            subprocess.run(
                [MPY_CROSS, "-b", "6.1", "-o", out_file, src_file],
                check=True)
            count += 1
    return count


def copy_data():
    # 与真机卡包保持一致：阶段拆分卡 + 精简 config，不带整包 cards.json
    tools_dir = os.path.join(ROOT, "tools")
    sys.path.insert(0, tools_dir)
    from split_cards import main as split_cards_main
    split_cards_main()
    data_src = os.path.join(SRC, "data")
    data_dst = os.path.join(BUNDLE, "zroad", "data")
    if os.path.exists(data_dst):
        shutil.rmtree(data_dst)
    os.makedirs(data_dst)
    for name in ("cards_1.jsonl", "cards_2.jsonl", "cards_3.jsonl",
                 "dice.json"):
        shutil.copy2(os.path.join(data_src, name),
                     os.path.join(data_dst, name))
    # 精简 config（复用卡包构建脚本逻辑）
    from build_mpy_bundle import slim_config
    slim_config(os.path.join(data_src, "config.json"),
                os.path.join(data_dst, "config.json"))
    # CJK 点阵字库
    shutil.copy2(
        os.path.join(SRC, "platforms", "device_mpy", "font.bin"),
        os.path.join(BUNDLE, "zroad", "platforms", "device_mpy",
                     "font.bin"))
    # 存档目录（与真机卡包一致）
    saves = os.path.join(BUNDLE, "saves")
    os.makedirs(saves, exist_ok=True)


def main():
    if os.path.exists(BUNDLE):
        shutil.rmtree(BUNDLE)
    os.makedirs(BUNDLE)
    count = compile_tree()
    copy_data()
    print("编译 %d 个 .mpy（bytecode v6.1，仅供本机模拟）" % count)
    runner = os.path.join(HERE, "hw_runner.py")
    # 两个场景各自独立进程（见 hw_runner.py 入口说明）
    result = subprocess.run([MICROPYTHON, runner])
    if result.returncode == 0:
        result = subprocess.run([MICROPYTHON, runner, "errtest"])
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
