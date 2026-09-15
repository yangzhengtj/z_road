"""paths.py —— 桌面端“数据目录 / 存档目录”解析（v0.7.1 打包适配）。

同一份代码要在三种安装形态下运行，文件位置各不相同，本模块把差异收敛到一处：

1. 开发模式（仓库里 `pip install -e .` 或直接 PYTHONPATH=src 运行）：
   - 数据：仓库内 ``src/zroad/data/``（随源码走，改 JSON 立即生效）；
   - 存档：仓库根 ``saves/``（沿用开发期习惯，不污染用户目录）。
2. pipx / pip 正式安装（包被复制进 site-packages）：
   - 数据：包内 ``zroad/data/``（由 pyproject 的 package-data 随包安装）；
   - 存档：用户目录 ``~/.zroad/saves/``（虚拟环境可能被升级/删除，
     存档绝不能放在包目录里）。
3. PyInstaller 单文件（sys.frozen=True，每次运行解压到临时目录、退出即删）：
   - 数据：PyInstaller 解压目录下的 ``zroad/data/``（spec 里 --add-data 打入）；
   - 存档：用户目录 ``~/.zroad/saves/``（临时目录退出即删，绝不能写这里）。

环境变量 ``ZROAD_SAVE_DIR`` 始终拥有最高优先级，测试与高级用户可覆盖存档位置。
本模块只依赖标准库；core 层不使用它（core 不做文件 I/O）。
"""

import os
import sys
from pathlib import Path

# 包根：本文件位于 src/zroad/platforms/desktop_rich/paths.py，上溯 2 级
PACKAGE_ROOT = Path(__file__).resolve().parents[2]
# 仓库根：src/zroad 再上溯 1 级（仅开发模式下该目录里有 pyproject.toml）
REPO_ROOT = PACKAGE_ROOT.parents[1]

# 安装版/打包版的用户存档目录名
_HOME_SAVE_DIRNAME = ".zroad"


def is_frozen():
    """是否运行在 PyInstaller 打包后的环境中。"""
    return bool(getattr(sys, "frozen", False))


def data_dir():
    """返回游戏数据目录（cards/dice/config 三个 JSON 所在处）。

    打包后 PyInstaller 把数据解压到 sys._MEIPASS；其余形态数据都在包内。
    """
    if is_frozen():
        # spec 中 datas 目标目录指定为 zroad/data
        return Path(sys._MEIPASS) / "zroad" / "data"
    return PACKAGE_ROOT / "data"


def data_file(filename):
    """数据目录下某个文件的完整路径。"""
    return data_dir() / filename


def _is_dev_checkout():
    """判断当前是否为仓库开发模式（包文件仍在仓库 src/ 下）。"""
    return (REPO_ROOT / "pyproject.toml").exists()


def save_dir():
    """返回存档目录（自动档/手动槽/历史最佳 records.json）。

    优先级：环境变量 ZROAD_SAVE_DIR > 开发模式仓库 saves/ > 用户目录 ~/.zroad/saves。
    目录不在这里创建（由 SaveStore/RecordsStore 按需 mkdir），保持纯路径函数。
    """
    env_dir = os.environ.get("ZROAD_SAVE_DIR")
    if env_dir:
        return Path(env_dir)
    if not is_frozen() and _is_dev_checkout():
        return REPO_ROOT / "saves"
    return Path.home() / _HOME_SAVE_DIRNAME / "saves"
