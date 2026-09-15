# -*- mode: python ; coding: utf-8 -*-
"""zroad.spec —— PyInstaller 单文件打包配置（Mac / Windows / Linux 通用）。

在仓库根目录执行：
    pyinstaller packaging/zroad.spec --noconfirm --clean
产物：
    dist/zroad            （macOS / Linux，无扩展名的可执行文件）
    dist\\zroad.exe       （Windows）

要点：
  * 入口是 src/zroad/__main__.py（等价 python -m zroad）；
  * 三个数据 JSON 通过 datas 打进包内 zroad/data/，
    运行时由 desktop_rich/paths.py 经 sys._MEIPASS 读取；
  * 存档不打包、也不写临时目录：paths.py 在 frozen 状态下把存档放到
    用户目录 ~/.zroad/saves/（Windows 为 C:\\Users\\<用户>\\.zroad\\saves）。

注意：PyInstaller 不能交叉编译——Windows 包必须在 Windows 上构建、
Mac 包在 macOS 上构建。三平台产物由 .github/workflows/release.yml 自动构建。
"""

from pathlib import Path

# SPECPATH 是 PyInstaller 执行 spec 时注入的变量，指向 spec 所在目录（packaging/）
REPO = Path(SPECPATH).resolve().parent
SRC = REPO / "src"
DATA_DIR = SRC / "zroad" / "data"


a = Analysis(
    [str(SRC / "zroad" / "__main__.py")],
    pathex=[str(SRC)],
    binaries=[],
    # (数据源目录, 包内目标目录)：与 paths.data_dir() 的 frozen 分支对应
    datas=[(str(DATA_DIR), "zroad/data")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 开发期工具无需进入发行包
    excludes=["pytest", "pip", "setuptools"],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="zroad",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,  # 终端程序（无图形窗口）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
