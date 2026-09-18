"""screen.py —— 设备端“字符网格屏幕”模型（MicroPython / CPython 模拟器共用）。

Cardputer ADV 屏幕 240×135，字号 16px 高：
  · 半角字符 8×16，一屏 30 列 × 8 行；
  · 汉字/全角字符 16×16，占两个半角列，一屏 15 字 × 8 行。

本模块只维护“逻辑屏幕”（字符格子 + 色块矩形），不碰具体硬件：
  · mp_backend 把逻辑屏幕光栅化到 dev.fb（帧缓冲）；
  · sim_backend 在电脑终端用 ANSI 渲染同一模型，供调试与自动化测试。
这样界面逻辑只写一遍，且测试可以直接断言屏幕上的文字内容。
"""

import gc

from . import font_data

# 紧凑格子存储用 array（真机无 PSRAM，240 个小 list 要占约 17KB，
# 换成定长数组后只占约 1.2KB）。CPython 与 MicroPython 都有 array 模块。
from array import array

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


def iter_codes(text):
    """把 str 按 UTF-8 解成整数码点流（生成器）。

    【为什么不能直接 for ch in text / text[i]】MicroPython 对 unicode
    字符串“取下标字符”和“迭代取字符”都会走 mp_obj_new_str_via_qstr：
    每个第一次出现的字符会被驻留成永久 qstr（qstr 池只增不减、扩容块
    必须连续）。一局游戏界面会遇到上千个不同汉字，qstr 池扩容到
    1280B 连续块时，碎片化小堆已无法满足，表现为 text() 里申请
    1280B MemoryError（实测：同串 s[21]/s[23] 已驻留则成功，首次
    遇到的新字 s[22] 必失败；切片 s[22:23] 不走 qstr 则成功）。

    正确做法：先 encode 成临时 UTF-8 bytes（可被 GC 回收），之后
    全程只处理小整数，绝不产生单字符 str。ASCII 1 字节、常用汉字
    3 字节，emoji 等 4 字节也兼容。
    """
    data = text.encode("utf-8")
    i = 0
    n = len(data)
    while i < n:
        x = data[i]
        if x < 0x80:
            yield x
            i += 1
        elif x < 0xE0:
            yield ((x & 0x1F) << 6) | (data[i + 1] & 0x3F)
            i += 2
        elif x < 0xF0:
            yield (((x & 0x0F) << 12)
                   | ((data[i + 1] & 0x3F) << 6)
                   | (data[i + 2] & 0x3F))
            i += 3
        else:
            yield (((x & 0x07) << 18)
                   | ((data[i + 1] & 0x3F) << 12)
                   | ((data[i + 2] & 0x3F) << 6)
                   | (data[i + 3] & 0x3F))
            i += 4


def char_width(code):
    """汉字/全角字符占 2 个半角列，其余占 1。入参为整数码点。

    用字库码点表二分判定，不用 str.find：MicroPython 对长中文字符串的
    find 会申请约 1.3KB 临时缓冲，碎片化小堆上每帧调用会直接内存失败。
    """
    if font_data.cjk_index(code) >= 0:
        return 2
    # 字库未收录的 CJK 标点/全角字符也按全角排版，避免界面错位
    if 0x2E80 <= code <= 0x9FFF or 0xFF00 <= code <= 0xFFEF:
        return 2
    return 1


def glyph(code):
    """按码点（整数）返回 (字宽, 点阵 bytes, 起始偏移)；缺字返回三个 None。

    入参直接用整数码点而不是字符，避免每帧上百次 chr() 短命字符串。
    ASCII 每字 16 字节（16 行×1 字节），CJK 每字 32 字节（16 行×2 字节），
    每个字节 MSB 在左，1=前景像素。

    ASCII 直接返回内联大 bytes 与偏移量、不做切片：每帧要画上百个字符，
    切片会产生大量短命 bytes，在无 PSRAM 的设备上造成严重堆碎片。
    CJK 点阵由 font_data 的小缓存返回（已缓存的固定 bytes，可复用）。
    """
    if font_data.ASCII_FIRST <= code <= 126:
        index = code - font_data.ASCII_FIRST
        return CELL_W, font_data.ASCII_DATA, index * CELL_H
    pos = font_data.cjk_index(code)
    if pos >= 0:
        # CJK 点阵不内联（设备内存小），从同目录 font.bin 按需读取
        return font_data.CJK_W, font_data.cjk_glyph(pos), 0
    return None, None, None


def wrap_text(text, width=COLS):
    """按半角列宽把长文本切成多行（中文按字断开，不丢字符）。

    换行符 \\n 强制断行；超宽的连续 ASCII 也允许在任意位置断开（界面文案
    以中文为主，不做英文单词边界保护）。
    """
    # 不能用 text.split("\n")：MicroPython 的 split 会把每个分段复制成
    # 新字符串，长事件文本约 600 个汉字 ≈ 1.2KB，碎片化小堆上会直接
    # MemoryError。这里按 UTF-8 码点流扫描（绝不逐字取下标，避免
    # MicroPython 把每个新汉字驻留成永久 qstr，见 iter_codes 说明），
    # 只对最终的短行（≤30 列）做切片，切片不产生 qstr。
    lines = []
    line_start = 0
    used = 0
    i = 0
    for code in iter_codes(text):
        if code == 10:   # '\n'
            lines.append(text[line_start:i])
            i += 1
            line_start = i
            used = 0
            continue
        w = char_width(code)
        if used + w > width:
            lines.append(text[line_start:i])
            line_start = i
            used = 0
        used += w
        i += 1
    lines.append(text[line_start:])
    return lines


class Screen(object):
    """逻辑屏幕：cells 存字符格，rects 存色块（标题栏/底栏/边框）。"""

    def __init__(self):
        n = ROWS * COLS
        # 四个定长数组各描述一格：字符码点（'H' 可容纳本游戏所有 CJK）、
        # 前景色、背景色、是否汉字右半占位。数组只分配一次，clear() 原地
        # 重置：设备内存小，避免每帧新建几百个小对象造成堆碎片。
        # 注意 MicroPython 的 array 不支持 * 与 list.extend，
        # 用定长 bytes 直接构造（'H' 为小端 2 字节，空格=0x20 0x00）。
        self.chars = array("H", b"\x20\x00" * n)
        # MicroPython 的 bytearray 不支持 * int，用 bytes 乘开再包一层
        self.fgs = bytearray(bytes([WHITE]) * n)
        self.bgs = bytearray(n)   # 初始全 0 = BLACK
        self.cont = bytearray(n)
        self.rects = []
        self.missing = set()   # 渲染时遇到的缺字集合（测试用来守门）
        self.default_bg = BLACK

    def _idx(self, row, col):
        return row * COLS + col

    def clear(self, bg=BLACK):
        space = ord(" ")
        for i in range(ROWS * COLS):
            self.chars[i] = space
            self.fgs[i] = WHITE
            self.bgs[i] = bg
            self.cont[i] = 0
        self.rects = []
        self.missing.clear()
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

    def put_char(self, col, row, code, fg=WHITE, bg=BLACK):
        """在半角列 col、行 row 写一个字符（code 为整数码点）；越界裁剪。"""
        if row < 0 or row >= ROWS:
            return
        if code == 10:   # '\n'
            return
        width = char_width(code)
        if col < 0 or col + width > COLS:
            return
        i = self._idx(row, col)
        self.chars[i] = code
        self.fgs[i] = fg
        self.bgs[i] = bg
        self.cont[i] = 0
        if width == 2:
            # 右半格只负责继承底色，不再画字形（码点 0 = 右半占位）
            j = i + 1
            self.chars[j] = 0
            self.fgs[j] = fg
            self.bgs[j] = bg
            self.cont[j] = 1

    def text(self, col, row, s, fg=WHITE, bg=BLACK):
        """从 (col,row) 起写一行字符串（不自动换行，超长裁剪）。

        只走码点流，不逐字取 str 下标，避免 MicroPython 驻留 qstr
        （详见 iter_codes 的说明）。
        """
        c = col
        for code in iter_codes(s):
            if code == 10:   # '\n'
                row += 1
                c = col
                continue
            self.put_char(c, row, code, fg, bg)
            c += char_width(code)

    def text_wrapped(self, col, row, s, fg=WHITE, bg=BLACK,
                     max_rows=ROWS):
        """自动折行写多行，返回实际占用的行数。"""
        width = COLS - col
        gc.collect()   # 折行会产生若干短命字符串，先腾连续块
        lines = wrap_text(s, width)
        for i, line in enumerate(lines[:max_rows]):
            self.text(col, row + i, line, fg, bg)
        return min(len(lines), max_rows)

    def row_text(self, row):
        """读出某行显示的文字（测试/自动操作判断界面用）。"""
        out = ""
        base = row * COLS
        for col in range(COLS):
            i = base + col
            if not self.cont[i]:
                code = self.chars[i]
                if code:
                    out += chr(code)
                else:
                    out += " "
        return out

    def all_text(self):
        return "\n".join(self.row_text(r) for r in range(ROWS))

    def row_bytes(self, row):
        """返回某行去掉首尾空格后的 UTF-8 bytes（右半占位格跳过）。

        给模拟器/自动测试做标题识别用：直接比较码点并手工编码成
        bytes，全程不产生单字符 str，避免在 MicroPython 限堆测试里
        因 chr()/取字符把整屏汉字逐个驻留成 qstr（见 iter_codes）。
        """
        base = row * COLS
        left = 0
        while left < COLS:
            i = base + left
            if not self.cont[i] and self.chars[i] not in (0, 32):
                break
            left += 1
        right = COLS - 1
        while right >= left:
            i = base + right
            if not self.cont[i] and self.chars[i] not in (0, 32):
                break
            right -= 1
        out = bytearray()
        for col in range(left, right + 1):
            i = base + col
            if self.cont[i]:
                continue
            code = self.chars[i]
            if code == 0:
                continue
            if code < 0x80:
                out.append(code)
            elif code < 0x800:
                out.append(0xC0 | (code >> 6))
                out.append(0x80 | (code & 0x3F))
            elif code < 0x10000:
                out.append(0xE0 | (code >> 12))
                out.append(0x80 | ((code >> 6) & 0x3F))
                out.append(0x80 | (code & 0x3F))
            else:
                out.append(0xF0 | (code >> 18))
                out.append(0x80 | ((code >> 12) & 0x3F))
                out.append(0x80 | ((code >> 6) & 0x3F))
                out.append(0x80 | (code & 0x3F))
        return bytes(out)

    def missing_text(self):
        """把缺字码点集合转成可打印字符串（仅报错/测试时调用，
        设备正常渲染路径不走这里）。"""
        return "".join(chr(code) for code in sorted(self.missing))

    # ---------- 光栅化 ----------

    def render(self, driver):
        """把逻辑屏幕交给具体后端画出来（碎片堆上带一次 gc 重试）。"""
        try:
            self._render_once(driver)
        except MemoryError:
            # 极端碎片化时本屏绘制中途可能连几百字节都分不出：
            # 再回收一次重试一屏；仍失败则上抛，由设备 boot 的错误屏接管。
            gc.collect()
            self._render_once(driver)

    def _render_once(self, driver):
        """实际绘制一屏。

        关键内存策略：整屏底色由驱动一次清屏（真机上是一次 C 调用），
        不再为每个空格/每个字形铺底色矩形——旧实现每帧要往 rects 里
        追加数百个 Python 元组（约 20-30KB 短命对象），是碎片化的
        头号来源。rects 里只保留标题栏/底栏/边框等少数装饰矩形。
        """
        # 上一屏的换行临时字符串、点阵缓存等先回收，尽量腾出连续块，
        # 避免本屏绘制中途 MemoryError（GC 不压缩，只能靠及时回收）。
        gc.collect()
        if hasattr(driver, "begin_screen"):
            driver.begin_screen(self)
        # 1) 整屏一次清成默认底色（覆盖上一屏所有像素残留）
        if hasattr(driver, "clear_screen"):
            driver.clear_screen(self.default_bg)
        # 2) 装饰矩形（标题栏/底栏/边框，数量很少）
        for filled, x, y, w, h, color in self.rects:
            if filled:
                driver.fill_rect(x, y, w, h, color)
            else:
                driver.stroke_rect(x, y, w, h, color)
        # 3) 缺字登记（纯整数/数组遍历，几乎零分配）
        for row in range(ROWS):
            base = row * COLS
            for col in range(COLS):
                i = base + col
                if self.cont[i]:
                    continue
                code = self.chars[i]
                if code and code != ord(" "):
                    width, data, offset = glyph(code)
                    if data is None:
                        # 只存整数码点：chr(code) 在 MicroPython 下同样
                        # 会产生新 qstr，缺字集合每帧登记，不能污染 qstr 池。
                        self.missing.add(code)
        # 4) 整屏字形一次性交给驱动绘制（设备端在单个函数内循环，
        #    避免每格一次 Python 函数帧造成的堆搅动；见 MPDriver.draw_cells）
        if hasattr(driver, "draw_cells"):
            driver.draw_cells(self)
        else:
            self._draw_cells_fallback(driver)
        driver.present()

    def _draw_cells_fallback(self, driver):
        """不支持 draw_cells 的后端（旧测试桩）逐格绘制。"""
        for row in range(ROWS):
            base = row * COLS
            for col in range(COLS):
                i = base + col
                if self.cont[i]:
                    continue
                code = self.chars[i]
                if code == 0 or code == ord(" "):
                    continue
                fg = self.fgs[i]
                width, data, offset = glyph(code)
                if data is None:
                    driver.stroke_rect(col * CELL_W, row * CELL_H,
                                       CELL_W - 1, CELL_H - 1, fg)
                    continue
                blit_glyph(driver, col * CELL_W, row * CELL_H,
                           width, data, offset, fg)


def blit_glyph(driver, x, y, width, data, offset, fg):
    """把 1-bit 点阵的前景像素画到后端（底色由整屏清屏统一处理）。

    按“连续像素段”调用 fill_rect，而不是逐像素画点，在设备上快得多
    （一个汉字通常只需几十次矩形调用而非 256 次画点）。
    offset 为该字点阵在 data 中的字节起始（ASCII 共用一整块内联 bytes）。
    """
    bytes_per_row = width // 8
    for row in range(CELL_H):
        base = offset + row * bytes_per_row
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
