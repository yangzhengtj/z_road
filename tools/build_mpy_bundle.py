#!/usr/bin/env python3
"""build_mpy_bundle.py —— 打包 Cardputer ADV 的 microSD 卡文件（阶段 2）。

产物目录 dist_cardputer/ 直接整体拷到 FAT32 microSD 卡根目录即可：

    dist_cardputer/
      main.py                 ← 游戏入口（Shell 里 run('/sd/zroad/main.py')）
      zroad/                  ← 游戏包（core 零依赖核心 + data + 设备前端）
        core/...              （*.mpy 预编译字节码，见下）
        data/cards.json ...
        platforms/device_mpy/...（含点阵字库 font_data.mpy）
      saves/                  ← 存档目录（首次运行也会自动创建）

为什么要预编译成 .mpy：
  设备内存有限，现场 import 一个 154KB 的 font_data.py 时，MicroPython
  一边编译一边分配内存，会抛 MemoryError（表现为导入中断、随后出现
  AttributeError: 'module' object has no attribute ...）。
  本工具用与固件同版本（MicroPython v1.26.1）的 mpy-cross 把包内所有
  .py 预编译为 .mpy 字节码，设备直接加载、不再现场编译，大幅降低导入
  峰值内存。main.py 必须保持源码（Shell 的 run() 用 exec(open()) 执行）。

桌面 Rich 前端（platforms/desktop_rich、headless）不拷：它们依赖 rich，
设备端永远不会 import；少拷可以省卡空间、也避免混淆。

用法（仓库根目录）：
    .venv/bin/python tools/build_mpy_bundle.py
然后把 dist_cardputer/ 里的 main.py 和 zroad/ 复制到 microSD 的
/sd/zroad/ 目录下（即卡根建 zroad 文件夹，main.py 放里面）。
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from split_cards import main as split_cards_main  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "zroad"
OUT = ROOT / "dist_cardputer"
CARD_APP_DIR = OUT / "zroad"

# mpy-cross 必须与固件 MicroPython 版本一致（固件为 v1.26.1，mpy v6.3）。
# 优先用项目虚拟环境里 pip 安装的 mpy-cross==1.26.1.post2。
MPY_CROSS_CANDIDATES = (
    ROOT / ".venv" / "bin" / "mpy-cross",
    ROOT / ".venv" / "Scripts" / "mpy-cross.exe",  # Windows 虚拟环境
)

# 只打包设备需要的子树：核心规则、数据、设备前端
PACKAGE_DIRS = ("core", "data", "platforms")
PACKAGE_ROOT_FILES = ("__init__.py",)
# platforms 下只带设备前端，桌面 Rich 端依赖第三方库，不进设备
DEVICE_SUBDIR = "device_mpy"

MAIN_PY = """\
# main.py —— 末路求生·文字版 Cardputer 入口（由 build_mpy_bundle.py 生成）
# 在 MicroPython Shell 中执行：run('/sd/zroad/main.py')
import gc
import sys

sys.path.insert(0, "/sd/zroad")
gc.collect()
from zroad.platforms.device_mpy.mp_frontend import boot

boot()
"""


def find_mpy_cross():
    """定位与固件匹配的 mpy-cross，并核对版本。"""
    binary = None
    for cand in MPY_CROSS_CANDIDATES:
        if cand.exists():
            binary = str(cand)
            break
    if binary is None:
        found = shutil.which("mpy-cross")
        if found:
            binary = found
    if binary is None:
        raise SystemExit(
            "未找到 mpy-cross。请先安装（与固件同版本）：\n"
            "  .venv/bin/pip install mpy-cross==1.26.1.post2"
        )
    out = subprocess.run([binary, "--version"], capture_output=True,
                         text=True, check=False)
    version_line = (out.stdout + out.stderr).strip()
    print("使用 %s（%s）" % (binary, version_line))
    if "v1.26" not in version_line:
        raise SystemExit(
            "mpy-cross 版本与固件不匹配：固件为 MicroPython v1.26.1，\n"
            "当前是 %r。请安装：.venv/bin/pip install mpy-cross==1.26.1.post2"
            % version_line
        )
    return binary


def ignore_pycache(dir_path, names):
    return [n for n in names if n == "__pycache__" or n.endswith(".pyc")]


def slim_config(src, dst):
    """生成设备专用 config.json：删除注释长文本与未实现的双人模式段。

    注释（comment/note）只给开发者看，duel（两人热座）阶段 2 不实现；
    这些中文字符串在小内存设备上白白常驻十几 KB。
    """
    with open(src, encoding="utf-8") as f:
        cfg = json.load(f)

    def strip(node):
        if isinstance(node, dict):
            for key in list(node.keys()):
                if key in ("comment", "note"):
                    del node[key]
                else:
                    strip(node[key])
        elif isinstance(node, list):
            for item in node:
                strip(item)

    cfg.pop("duel", None)
    strip(cfg)
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, separators=(",", ":"))


def copy_tree(src, dst):
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=ignore_pycache)


def compile_tree(mpy_cross, pkg_dir):
    """把 pkg_dir 下所有 .py 原地编译为 .mpy，并删除 .py 源文件。

    设备 import 时会优先找到同名 .mpy；只保留 .mpy 也能避免设备错误地
    现场编译 .py（.mpy 与 .py 并存时不同端口的查找顺序并不总一致）。
    """
    py_files = sorted(pkg_dir.rglob("*.py"))
    failed = []
    for py in py_files:
        mpy = py.with_suffix(".mpy")
        proc = subprocess.run([mpy_cross, "-o", str(mpy), str(py)],
                              capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            failed.append((py, proc.stderr or proc.stdout))
            continue
        py.unlink()
    if failed:
        for py, err in failed:
            print("编译失败：%s\n%s" % (py, err), file=sys.stderr)
        raise SystemExit("有 %d 个文件 mpy-cross 编译失败" % len(failed))
    return len(py_files)


def main():
    mpy_cross = find_mpy_cross()

    # 0) 由 cards.json 重新生成按阶段拆分的 cards_1/2/3.json（设备懒加载）
    split_cards_main()

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    # 1) 包根文件
    pkg = CARD_APP_DIR
    pkg.mkdir(parents=True)
    for name in PACKAGE_ROOT_FILES:
        shutil.copy2(SRC / name, pkg / name)

    # 2) core 整目录；data 只带设备需要的文件
    copy_tree(SRC / "core", pkg / "core")
    data_dst = pkg / "data"
    data_dst.mkdir(parents=True)
    for name in ("cards_1.jsonl", "cards_2.jsonl", "cards_3.jsonl",
                 "dice.json"):
        shutil.copy2(SRC / "data" / name, data_dst / name)
    # 设备专用精简 config（删注释/双人段），整包 cards.json 不进设备
    slim_config(SRC / "data" / "config.json", data_dst / "config.json")

    # 3) platforms 只带 device_mpy
    plat_dst = pkg / "platforms"
    plat_dst.mkdir()
    (plat_dst / "__init__.py").write_text(
        '"""平台前端包。"""\n', encoding="utf-8")
    copy_tree(SRC / "platforms" / DEVICE_SUBDIR,
              plat_dst / DEVICE_SUBDIR)

    # 4) 包内所有 .py 预编译为 .mpy（main.py 除外，见模块说明）
    n_compiled = compile_tree(mpy_cross, pkg)

    # sim.mpy 是电脑端模拟器（依赖 termios），设备永远不会 import，删掉省空间
    sim_mpy = pkg / "platforms" / DEVICE_SUBDIR / "sim.mpy"
    if sim_mpy.exists():
        sim_mpy.unlink()

    # 5) 入口 main.py（保持源码）与存档目录
    (OUT / "main.py").write_text(MAIN_PY, encoding="utf-8")
    (OUT / "saves").mkdir()

    # 6) 统计输出
    mpy_files = list(pkg.rglob("*.mpy"))
    data_files = list((pkg / "data").glob("*.json"))
    total_bytes = sum(p.stat().st_size for p in pkg.rglob("*") if p.is_file())
    print("已生成 Cardputer 卡包：%s" % OUT)
    print("  预编译 .mpy %d 个（源 .py %d 个），数据 %d 个，合计 %.1f KB"
          % (len(mpy_files), n_compiled, len(data_files),
             total_bytes / 1024))
    print("拷卡方式：把 dist_cardputer/ 内的 main.py、zroad/、saves/")
    print("          放到 microSD 卡的 zroad/ 目录（/sd/zroad/）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
