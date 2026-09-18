#!/usr/bin/env python3
"""build_mpy_font.py —— 为 Cardputer 端生成 16px 点阵中文字库（阶段 2 构建工具）。

为什么需要它：
    Cardputer ADV 屏幕 240×135，BeanpieChen 的 MicroPython Shell 固件只内置
    8×16 ASCII 字库，不含中文字形。游戏是纯中文界面，需要自带一份中文字库。
    全量中文字库以 MB 计，设备装不下也没必要——游戏实际用到的汉字只有几百个，
    因此本工具按“实际用字”生成最小子集：
      · ASCII 0x20–0x7E：8×16，每字 16 字节；
      · 中文/全角字符：16×16，每字 32 字节；
    约 800 个汉字 ≈ 26KB，放 microSD 随取随用。

用字来源（自动扫描，保证不漏字）：
  1. src/zroad/data/*.json 的所有字符串（卡牌场景/事件、骰面、配置标签）；
  2. src/zroad/platforms/device_mpy/*.py 里出现的全部中日韩字符（界面文案）；
  3. 固定补充的中文标点集合。

用法（在仓库根目录）：
    .venv/bin/python tools/build_mpy_font.py
    .venv/bin/python tools/build_mpy_font.py --font /System/Library/Fonts/PingFang.ttc

输出：
    src/zroad/platforms/device_mpy/font_data.py
        ASCII 点阵（很小，直接内联）+ CJK 字符索引表 + 按需读取器；
    src/zroad/platforms/device_mpy/font.bin
        CJK 字形裸数据（每字 32 字节，顺序与 CJK_TABLE 码点升序一致）。

设备内存很小，34KB 的 CJK 点阵若内联进 .mpy，导入时一次性载入会触发
MemoryError；因此 CJK 字形放卡上文件，按字符 seek 读取并只缓存最近 64 个
（约 2KB）。模拟器（CPython）走同一套读取逻辑。

依赖：Pillow（pip install pillow，仅构建期需要，不进设备）。
字形 © Apple PingFang，仅在你自己的设备上随游戏个人使用，不分发字体文件本身。
"""

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "src" / "zroad" / "data"
DEVICE_DIR = ROOT / "src" / "zroad" / "platforms" / "device_mpy"
OUTPUT = DEVICE_DIR / "font_data.py"
FONT_BIN = DEVICE_DIR / "font.bin"

FONT_HEIGHT = 16       # 行高 16px：135px 屏幕可显示 8 行
ASCII_W = 8            # 半角字宽：一行 30 个半角字符
CJK_W = 16             # 全角字宽：一行 15 个汉字
ASCII_FIRST = 0x20
ASCII_LAST = 0x7E

# 界面可能用到、但扫描源码/数据不一定覆盖的中文标点与常用符号
EXTRA_CHARS = "：，。、（）「」？！…—·《》①②③④⑤⑥⑦⑧⑨→↑↓←"

CJK_RANGES = (
    (0x00B7, 0x00B7),    # 间隔号·
    (0x1100, 0x11FF),    # 韩文兼容区（一般用不到，成本低）
    (0x2000, 0x21FF),    # 常用标点（…—）与方向箭头（↑↓←→）
    (0x2E80, 0x9FFF),    # CJK 部首、中日韩统一表意文字、标点、假名
    (0xFF00, 0xFFEF),    # 全角拉丁/标点
)


def is_cjk(ch):
    code = ord(ch)
    for lo, hi in CJK_RANGES:
        if lo <= code <= hi:
            return True
    return False


def collect_strings_from_json(value, sink):
    """递归收集 JSON 里的所有字符串字符。"""
    if isinstance(value, str):
        sink.update(value)
    elif isinstance(value, dict):
        for k, v in value.items():
            sink.update(str(k))
            collect_strings_from_json(v, sink)
    elif isinstance(value, list):
        for item in value:
            collect_strings_from_json(item, sink)


def collect_chars():
    chars = set(EXTRA_CHARS)
    # 1) 数据文件
    for path in sorted(DATA_DIR.glob("*.json")):
        collect_strings_from_json(json.loads(path.read_text(encoding="utf-8")),
                                  chars)
    # 2) 全部源码里的 CJK 字符：设备界面文案在 device_mpy，core 里还有
    #    事件结算日志、EngineError 提示等会直接显示给玩家的中文
    #    （font_data.py 自身跳过）
    for path in sorted((ROOT / "src" / "zroad").rglob("*.py")):
        if path.name == "font_data.py":
            continue
        text = path.read_text(encoding="utf-8")
        for ch in text:
            if is_cjk(ch):
                chars.add(ch)
    return chars


def render_glyph(font_path, font_index, cjk_size, ascii_size, ch, width):
    """把单个字符渲染成 width×16 的 1-bit 位图，返回行优先 bytes（MSB 在左）。"""
    size = cjk_size if width == CJK_W else ascii_size
    font = ImageFont.truetype(font_path, size, index=font_index)
    img = Image.new("L", (width, FONT_HEIGHT), 0)
    draw = ImageDraw.Draw(img)
    # 用墨迹实际包围盒居中，避免不同字体上下留白差异
    bbox = draw.textbbox((0, 0), ch, font=font)
    ink_w = bbox[2] - bbox[0]
    ink_h = bbox[3] - bbox[1]
    x = (width - ink_w) // 2 - bbox[0]
    y = (FONT_HEIGHT - ink_h) // 2 - bbox[1]
    draw.text((x, y), ch, fill=255, font=font)
    pixels = img.load()
    rows = []
    for row in range(FONT_HEIGHT):
        line = bytearray((width + 7) // 8)
        for col in range(width):
            if pixels[col, row] > 128:
                line[col >> 3] |= 0x80 >> (col & 7)
        rows.extend(line)
    return bytes(rows)


def b64ish_lines(data, group):
    """把 bytes 切成多行 b'' 片段，每 group 字节，便于阅读与设备解析。"""
    chunks = []
    for i in range(0, len(data), group):
        chunks.append("    b'" + "".join(
            "\\x%02X" % b for b in data[i:i + group]) + "'")
    return "\n".join(chunks)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--font", default="/System/Library/Fonts/PingFang.ttc",
                        help="TTF/TTC 字体路径（默认 macOS 苹方）")
    parser.add_argument("--font-index", type=int, default=5, help="TTC 面索引")
    parser.add_argument("--cjk-size", type=int, default=16)
    parser.add_argument("--ascii-size", type=int, default=14)
    args = parser.parse_args()

    if not Path(args.font).exists():
        print("找不到字体 %s；Linux 可尝试 Noto Sans CJK（--font 指定）"
              % args.font, file=sys.stderr)
        return 1

    chars = collect_chars()
    cjk_chars = sorted(ch for ch in chars if is_cjk(ch))
    missing = []

    # ASCII 32..126 全量
    ascii_data = bytearray()
    for code in range(ASCII_FIRST, ASCII_LAST + 1):
        ascii_data.extend(render_glyph(args.font, args.font_index,
                                       args.cjk_size, args.ascii_size,
                                       chr(code), ASCII_W))

    # CJK 子集
    cjk_data = bytearray()
    for ch in cjk_chars:
        glyph = render_glyph(args.font, args.font_index,
                             args.cjk_size, args.ascii_size, ch, CJK_W)
        # 空白字形说明字体缺字（全 0），报告出来便于人工确认
        if not any(glyph):
            missing.append(ch)
        cjk_data.extend(glyph)

    header = (
        '"""font_data.py —— Cardputer 端 16px 点阵字库（自动生成，请勿手改）。\n'
        '\n'
        '由 tools/build_mpy_font.py 扫描游戏数据与设备端源码的实际用字生成；\n'
        '改动界面文案或卡牌文本后，重新运行该工具即可补字（font.bin 会一并重生成）。\n'
        'ASCII：%d×%d 每字 %d 字节（内联在本文件）；CJK：%d×%d 每字 %d 字节，\n'
        '点阵放在同目录 font.bin，由 cjk_glyph(index) 按需 seek 读取并做小缓存，\n'
        '避免一次性载入几十 KB 导致设备 MemoryError。行扫描 MSB 在左。\n'
        '"""\n\n'
        "HEIGHT = %d\n"
        "ASCII_W = %d\n"
        "CJK_W = %d\n"
        "ASCII_FIRST = %d\n"
        "ASCII_COUNT = %d\n"
        "CJK_COUNT = %d\n"
        "CJK_GLYPH_BYTES = %d\n\n"
    ) % (ASCII_W, FONT_HEIGHT, ASCII_W, CJK_W, FONT_HEIGHT, CJK_W // 2 * 4,
         FONT_HEIGHT, ASCII_W, CJK_W, ASCII_FIRST,
         ASCII_LAST - ASCII_FIRST + 1, len(cjk_chars), CJK_W * FONT_HEIGHT // 8)

    body = []
    body.append("ASCII_DATA = (\n" + b64ish_lines(bytes(ascii_data), 16)
                + "\n)\n")
    # 码点表：按码点升序排列（与 font.bin 顺序一致），每个码点 2 字节
    # 大端。设备端用二分查找定位字形下标，全程零分配；不能用 str.find：
    # MicroPython 对双字节中文字符串的查找会申请约 1.3KB 临时缓冲，
    # 在碎片化小堆上会直接 MemoryError。
    table = bytearray()
    for ch in cjk_chars:
        code = ord(ch)
        table.append((code >> 8) & 0xFF)
        table.append(code & 0xFF)
    body.append("# 字形码点表（大端 uint16，升序，下标即 font.bin 中的字形序号）\n")
    body.append("CJK_TABLE = (\n" + b64ish_lines(bytes(table), 32) + "\n)\n")
    # CJK 点阵按需读取器（MicroPython / CPython 通用，不依赖第三方库）：
    # 单个复用缓冲，零缓存、零运行期分配，避免缓存小块把堆插碎。
    body.append(
        "_bin = None\n"
        "_scratch = bytearray(CJK_GLYPH_BYTES)\n\n\n"
        "def open_bin():\n"
        '    """提前打开 font.bin（开局建界面之前调用，文件对象落在整齐堆）。"""\n'
        "    global _bin\n"
        "    if _bin is None:\n"
        "        if '/' in __file__:\n"
        "            directory = __file__.rsplit('/', 1)[0]\n"
        "        else:\n"
        "            directory = __file__.rsplit('\\\\', 1)[0]\n"
        "        _bin = open(directory + '/font.bin', 'rb')\n"
        "    return _bin\n\n\n"
        "def cjk_index(code):\n"
        '    """按整数码点二分查找字形序号；字库中没有返回 -1（零分配）。"""\n'
        "    lo, hi = 0, CJK_COUNT - 1\n"
        "    while lo <= hi:\n"
        "        mid = (lo + hi) >> 1\n"
        "        j = mid * 2\n"
        "        value = (CJK_TABLE[j] << 8) | CJK_TABLE[j + 1]\n"
        "        if value == code:\n"
        "            return mid\n"
        "        if value < code:\n"
        "            lo = mid + 1\n"
        "        else:\n"
        "            hi = mid - 1\n"
        "    return -1\n\n\n"
        "def warm(text):\n"
        '    """兼容旧调用：字形即读即用，预热只需确保字库文件已打开。"""\n'
        "    open_bin()\n\n\n"
        "def cjk_glyph(index):\n"
        '    """把第 index 个 CJK 字形读进复用缓冲并返回（32 字节，MSB 在左）。\n'
        "\n"
        "    返回的是共享缓冲，每次调用覆盖上次内容，调用方必须当场用完，\n"
        "    不得长期持有引用（draw_cells 逐字绘制，满足该约定）。\n"
        '    """\n'
        "    data_file = open_bin()\n"
        "    data_file.seek(index * CJK_GLYPH_BYTES)\n"
        "    data_file.readinto(_scratch)\n"
        "    return _scratch\n"
    )

    DEVICE_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(header + "\n".join(body), encoding="utf-8")
    FONT_BIN.write_bytes(bytes(cjk_data))

    print("ASCII 字形 %d 个（%d 字节，内联）" %
          (ASCII_LAST - ASCII_FIRST + 1, len(ascii_data)))
    print("CJK 字形 %d 个（%d 字节 → %s）" %
          (len(cjk_chars), len(cjk_data), FONT_BIN.name))
    print("输出：%s（%.1f KB）+ %s（%.1f KB）" %
          (OUTPUT, OUTPUT.stat().st_size / 1024,
           FONT_BIN.name, FONT_BIN.stat().st_size / 1024))
    if missing:
        print("警告：以下字符字体缺字（显示为空白）：%s" % " ".join(missing),
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
