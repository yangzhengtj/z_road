"""screen.py —— 设备端“字符网格屏幕”模型（MicroPython / CPython 模拟器共用）。

Cardputer ADV 屏幕 240×135，字号 16px 高：
  · 半角字符 8×16，一屏 30 列 × 8 行；
  · 汉字/全角字符 16×16，占两个半角列，一屏 15 字 × 8 行。

本模块只维护“逻辑屏幕”（字符格子 + 色块矩形），不碰具体硬件：
  · mp_backend 把逻辑屏幕光栅化到 dev.fb（帧缓冲）；
  · sim_backend 在电脑终端用 ANSI 渲染同一模型，供调试与自动化测试。
这样界面逻辑只写一遍，且测试可以直接断言屏幕上的文字内容。
"""

from . import font_data

# ---------- 逻辑尺寸 ----------
CELL_W = 8
CELL_H = 16
COLS = 240 // CELL_W      # 30 个半角列
ROWS = 135 // CELL_H      # 8 行

# ---------- 逻辑颜色（后端各自映射到 RGB565 / ANSI） ----------
BLACK = 0
WHITE = 1
RED = 2
GREEN = 3
YELLOW = 4
CYAN = 5
MAGENTA = 6
GRAY = 7
BLUE = 8
DARK_GREEN = 9


def char_width(ch):
    """汉字/全角字符占 2 个半角列，其余占 1。"""
    if font_data.CJK_CHARS.find(ch) >= 0:
        return 2
    code = ord(ch)
    # CJK 标点、全角字符区（字库未收录时也按全角排版，避免错位）
    if 0x2E80 <= code <= 0x9FFF or 0xFF00 <= code <= 0xFFEF:
        return 2
    return 1


def glyph(ch):
    """返回 (字宽, 点阵 bytes)；字库没有该字返回 (None, None)。

    ASCII 每字 16 字节（16 行×1 字节），CJK 每字 32 字节（16 行×2 字节），
    每个字节 MSB 在左，1=前景像素。
    """
    code = ord(ch)
    if font_data.ASCII_FIRST <= code <= 126:
        index = code - font_data.ASCII_FIRST
        start = index * CELL_H
        return CELL_W, font_data.ASCII_DATA[start:start + CELL_H]
    pos = font_data.CJK_CHARS.find(ch)
    if pos >= 0:
        start = pos * font_data.CJK_W * CELL_H // 8
        return font_data.CJK_W, font_data.CJK_DATA[start:start + 32]
    return None, None


def wrap_text(text, width=COLS):
    """按半角列宽把长文本切成多行（中文按字断开，不丢字符）。

    换行符 \\n 强制断行；超宽的连续 ASCII 也允许在任意位置断开（界面文案
    以中文为主，不做英文单词边界保护）。
    """
    lines = []
    for paragraph in text.split("\n"):
        current = ""
        used = 0
        for ch in paragraph:
            w = char_width(ch)
            if used + w > width:
                lines.append(current)
                current = ch
                used = w
            else:
                current += ch
                used += w
        lines.append(current)
    return lines


class Screen(object):
    """逻辑屏幕：cells 存字符格，rects 存色块（标题栏/底栏/边框）。"""

    def __init__(self):
        # 每格是 [字符, 前景色, 背景色, 是否为汉字右半占位]
        self.cells = []
        self.rects = []
        self.missing = set()   # 渲染时遇到的缺字集合（测试用来守门）
        self.default_bg = BLACK
        self.clear()

    def clear(self, bg=BLACK):
        self.cells = [[[" ", WHITE, bg, 0] for _ in range(COLS)]
                      for _ in range(ROWS)]
        self.rects = []
        self.missing = set()
        self.default_bg = bg

    # ---------- 色块 ----------

    def fill_rect(self, x, y, w, h, color):
        """填充矩形（像素坐标）。"""
        self.rects.append((1, x, y, w, h, color))

    def stroke_rect(self, x, y, w, h, color):
        """空心矩形边框（像素坐标）。"""
        self.rects.append((0, x, y, w, h, color))

    def bar(self, row, color):
        """整行反白条（标题栏/底栏），row 为行号 0..7。"""
        self.fill_rect(0, row * CELL_H, COLS * CELL_W, CELL_H, color)

    # ---------- 文字 ----------

    def put_char(self, col, row, ch, fg=WHITE, bg=BLACK):
        """在半角列 col、行 row 写一个字符；越界自动裁剪。"""
        if row < 0 or row >= ROWS:
            return
        if ch == "\n":
            return
        width = char_width(ch)
        if col < 0 or col + width > COLS:
            return
        self.cells[row][col] = [ch, fg, bg, 0]
        if width == 2:
            # 右半格只负责继承底色，不再画字形
            self.cells[row][col + 1] = ["", fg, bg, 1]

    def text(self, col, row, s, fg=WHITE, bg=BLACK):
        """从 (col,row) 起写一行字符串（不自动换行，超长裁剪）。"""
        c = col
        for ch in s:
            if ch == "\n":
                row += 1
                c = col
                continue
            self.put_char(c, row, ch, fg, bg)
            c += char_width(ch)

    def text_wrapped(self, col, row, s, fg=WHITE, bg=BLACK,
                     max_rows=ROWS):
        """自动折行写多行，返回实际占用的行数。"""
        width = COLS - col
        lines = wrap_text(s, width)
        for i, line in enumerate(lines[:max_rows]):
            self.text(col, row + i, line, fg, bg)
        return min(len(lines), max_rows)

    def row_text(self, row):
        """读出某行显示的文字（测试/自动操作判断界面用）。"""
        out = ""
        for cell in self.cells[row]:
            if not cell[3]:
                out += cell[0]
        return out

    def all_text(self):
        return "\n".join(self.row_text(r) for r in range(ROWS))

    # ---------- 光栅化 ----------

    def render(self, driver):
        """把逻辑屏幕交给具体后端画出来。"""
        if hasattr(driver, "begin_screen"):
            driver.begin_screen(self)
        for filled, x, y, w, h, color in self.rects:
            if filled:
                driver.fill_rect(x, y, w, h, color)
            else:
                driver.stroke_rect(x, y, w, h, color)
        for row in range(ROWS):
            for col in range(COLS):
                ch, fg, bg, cont = self.cells[row][col]
                if cont:
                    continue
                if ch == " " or ch == "":
                    # 空格也需要刷底色（覆盖上一屏残留）
                    driver.fill_rect(col * CELL_W, row * CELL_H,
                                     CELL_W, CELL_H, bg)
                    continue
                width, data = glyph(ch)
                if data is None:
                    self.missing.add(ch)
                    # 缺字画一个空心方框（豆腐块）提示
                    driver.stroke_rect(col * CELL_W, row * CELL_H,
                                       CELL_W - 1, CELL_H - 1, fg)
                    continue
                blit_glyph(driver, col * CELL_W, row * CELL_H,
                           width, data, fg, bg)
        driver.present()


def blit_glyph(driver, x, y, width, data, fg, bg):
    """把 1-bit 点阵画到后端：先铺底色矩形，再按扫描线连续段画前景。

    按“连续像素段”调用 fill_rect，而不是逐像素画点，在设备上快得多
    （一个汉字通常只需几十次矩形调用而非 256 次画点）。
    """
    driver.fill_rect(x, y, width, CELL_H, bg)
    bytes_per_row = width // 8
    for row in range(CELL_H):
        base = row * bytes_per_row
        col = 0
        while col < width:
            byte = data[base + (col >> 3)]
            if byte & (0x80 >> (col & 7)):
                start = col
                while col < width:
                    byte = data[base + (col >> 3)]
                    if not (byte & (0x80 >> (col & 7))):
                        break
                    col += 1
                driver.fill_rect(x + start, y + row, col - start, 1, fg)
            else:
                col += 1
