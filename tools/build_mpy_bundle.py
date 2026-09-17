#!/usr/bin/env python3
"""build_mpy_bundle.py —— 打包 Cardputer ADV 的 microSD 卡文件（阶段 2）。

产物目录 dist_cardputer/ 直接整体拷到 FAT32 microSD 卡根目录即可：

    dist_cardputer/
      main.py                 ← 游戏入口（Shell 里 run('/sd/zroad/main.py')）
      zroad/                  ← 游戏包（core 零依赖核心 + data + 设备前端）
        core/...
        data/cards.json ...
        platforms/device_mpy/...（含点阵字库 font_data.py）
      saves/                  ← 存档目录（首次运行也会自动创建）

桌面 Rich 前端（platforms/desktop_rich、headless）不拷：它们依赖 rich，
设备端永远不会 import；少拷可以省卡空间、也避免混淆。

用法（仓库根目录）：
    .venv/bin/python tools/build_mpy_bundle.py
然后把 dist_cardputer/ 里的 main.py 和 zroad/ 复制到 microSD 的
/sd/zroad/ 目录下（即卡根建 zroad 文件夹，main.py 放里面）。
"""

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "zroad"
OUT = ROOT / "dist_cardputer"
CARD_APP_DIR = OUT / "zroad"

# 只打包设备需要的子树：核心规则、数据、设备前端
PACKAGE_DIRS = ("core", "data", "platforms")
PACKAGE_ROOT_FILES = ("__init__.py",)
# platforms 下只带设备前端，桌面 Rich 端依赖第三方库，不进设备
DEVICE_SUBDIR = "device_mpy"

MAIN_PY = """\
# main.py —— 末路求生·文字版 Cardputer 入口（由 build_mpy_bundle.py 生成）
# 在 MicroPython Shell 中执行：run('/sd/zroad/main.py')
import sys

sys.path.insert(0, "/sd/zroad")
from zroad.platforms.device_mpy.mp_frontend import boot

boot()
"""


def ignore_pycache(dir_path, names):
    return [n for n in names if n == "__pycache__" or n.endswith(".pyc")]


def copy_tree(src, dst):
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=ignore_pycache)


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    # 1) 包根文件
    pkg = CARD_APP_DIR
    pkg.mkdir(parents=True)
    for name in PACKAGE_ROOT_FILES:
        shutil.copy2(SRC / name, pkg / name)

    # 2) core 与 data 整目录
    for name in ("core", "data"):
        copy_tree(SRC / name, pkg / name)

    # 3) platforms 只带 device_mpy
    plat_dst = pkg / "platforms"
    plat_dst.mkdir()
    (plat_dst / "__init__.py").write_text(
        '"""平台前端包。"""\n', encoding="utf-8")
    copy_tree(SRC / "platforms" / DEVICE_SUBDIR,
              plat_dst / DEVICE_SUBDIR)

    # 4) 入口 main.py 与存档目录
    (OUT / "main.py").write_text(MAIN_PY, encoding="utf-8")
    (OUT / "saves").mkdir()

    # 5) 统计输出
    py_files = list(pkg.rglob("*.py"))
    data_files = list((pkg / "data").glob("*.json"))
    total_bytes = sum(p.stat().st_size for p in pkg.rglob("*") if p.is_file())
    print("已生成 Cardputer 卡包：%s" % OUT)
    print("  Python 文件 %d 个，数据 %d 个，合计 %.1f KB"
          % (len(py_files), len(data_files), total_bytes / 1024))
    print("拷卡方式：把 dist_cardputer/ 内的 main.py、zroad/、saves/")
    print("          放到 microSD 卡的 zroad/ 目录（/sd/zroad/）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
