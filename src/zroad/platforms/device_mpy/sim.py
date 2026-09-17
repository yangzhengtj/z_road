#!/usr/bin/env python3
"""sim.py —— Cardputer 界面的电脑端模拟器（CPython，ANSI 终端）。

用途：
  1. 不烧设备即可在 Mac 终端体验 Cardputer 版界面（30×8 字符网格）；
  2. 自动化测试：ScriptedKeys 按脚本/回调喂键，SimDriver 记录每一屏，
     可以在没有硬件的情况下跑完整整局并校验“没有缺字、没有流程死锁”。

交互运行（仓库根目录）：
    .venv/bin/python -m zroad.platforms.device_mpy.sim
按键映射：字母/数字直接输入；回车确认；q 之外的返回用 ESC；
    方向键翻页/移动；Backspace 删除。
"""

import json
import os
import sys
import tempfile

from .screen import Screen, COLS, ROWS, BLACK, WHITE, RED, GREEN, YELLOW, \
    CYAN, MAGENTA, GRAY, BLUE, DARK_GREEN
from .stores import JsonFileStore
from . import device_app

# 逻辑颜色 → ANSI（前景色, 背景色）
_ANSI_FG = {
    BLACK: 30, WHITE: 97, RED: 91, GREEN: 92, YELLOW: 93,
    CYAN: 96, MAGENTA: 95, GRAY: 90, BLUE: 94, DARK_GREEN: 32,
}
_ANSI_BG = {
    BLACK: 40, WHITE: 107, RED: 41, GREEN: 42, YELLOW: 43,
    CYAN: 46, MAGENTA: 45, GRAY: 100, BLUE: 44, DARK_GREEN: 42,
}


class SimDriver(object):
    """把逻辑屏幕渲染成 ANSI 文本；同时记录每一屏供测试断言。"""

    def __init__(self, record=False):
        self.record = record
        self.shots = []
        self.missing = set()

    def begin_screen(self, screen):
        self._current_screen = screen

    def _render_text(self, screen):
        """从逻辑屏幕生成 8 行带颜色的字符矩阵（含色块背景）。"""
        # 每格的背景色默认屏幕底色，色块矩形按覆盖关系刷背景
        bg_grid = [[screen.default_bg] * COLS for _ in range(ROWS)]
        for filled, x, y, w, h, color in screen.rects:
            if not filled:
                continue
            for row in range(ROWS):
                cy = row * 16 + 8
                if y <= cy < y + h:
                    for col in range(COLS):
                        cx = col * 8 + 4
                        if x <= cx < x + w:
                            bg_grid[row][col] = color
        lines = []
        for row in range(ROWS):
            chunks = []
            for col in range(COLS):
                ch, fg, cell_bg, cont = screen.cells[row][col]
                if cont:
                    continue
                # 文字自带底色（如标题栏上的字）优先，否则用色块背景
                bg = cell_bg if cell_bg != screen.default_bg \
                    else bg_grid[row][col]
                display = ch if ch not in ("", " ") else " "
                chunks.append("\x1b[%d;%dm%s" % (
                    _ANSI_FG[fg], _ANSI_BG[bg], display))
                if ord(display) > 0x2E80:  # 汉字占两列，补一个背景格
                    chunks.append("\x1b[%d;%dm " % (_ANSI_FG[fg], _ANSI_BG[bg]))
            lines.append("".join(chunks))
        return lines

    def fill_rect(self, x, y, w, h, color):
        pass

    def stroke_rect(self, x, y, w, h, color):
        pass

    def present(self):
        screen = self._current_screen
        if self.record:
            plain = "\n".join(screen.row_text(r) for r in range(ROWS))
            self.shots.append(plain)
            self.missing |= set(screen.missing)
        else:
            sys.stdout.write("\x1b[?25l\x1b[2J\x1b[H")
            for line in self._render_text(screen):
                sys.stdout.write(line + "\x1b[0m\n")
            sys.stdout.write("\x1b[0m")
            sys.stdout.flush()

    def bind(self, screen):
        self._current_screen = screen


class SimKeys(object):
    """按键来源：scripted=回调/队列（测试），否则读终端方向键。"""

    def __init__(self, scripted=None):
        self.scripted = scripted
        self._queue = []

    def get_key(self):
        if self.scripted is not None:
            return self.scripted()
        return self._read_terminal()

    def push(self, token):
        self._queue.append(token)

    def _read_terminal(self):
        if self._queue:
            return self._queue.pop(0)
        ch = os.read(self._fd, 1)
        if ch == b"\r" or ch == b"\n":
            return "ENTER"
        if ch == b"\x1b":
            seq = os.read(self._fd, 2)
            mapping = {b"[A": "UP", b"[B": "DOWN", b"[C": "RIGHT",
                       b"[D": "LEFT"}
            return mapping.get(seq, "ESC")
        if ch == b"\x7f" or ch == b"\x08":
            return "BS"
        text = ch.decode("utf-8", errors="ignore")
        return text.lower() if text else "ENTER"

    def setup_terminal(self):
        import termios
        import tty
        self._fd = sys.stdin.fileno()
        self._old = termios.tcgetattr(self._fd)
        tty.setraw(self._fd)

    def restore_terminal(self):
        import termios
        termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old)
        sys.stdout.write("\x1b[?25h\x1b[0m\n")


class SimPlatform(object):
    """电脑端平台：数据读仓库 data，存档写临时目录，退出抛 SystemExit。"""

    def __init__(self, keys, driver, save_root=None):
        self.keys = keys
        self.get_key = keys.get_key
        self.driver = driver
        data_dir = os.path.join(os.path.dirname(__file__),
                                "..", "..", "data")
        self.data_dir = os.path.abspath(data_dir)
        save_root = save_root or os.path.join(tempfile.gettempdir(),
                                              "zroad_sim")
        self.store = JsonFileStore(save_root, json, os,
                                   now_func=lambda: "2026-09-17 12:00:00")

    def load_json(self, name):
        with open(os.path.join(self.data_dir, name), "r",
                  encoding="utf-8") as f:
            return json.load(f)

    def exit(self):
        raise SystemExit(0)


def run_interactive():
    """终端交互模式入口。"""
    driver = SimDriver(record=False)
    keys = SimKeys()
    platform = SimPlatform(keys, driver)
    app = device_app.DeviceApp(platform)
    keys.setup_terminal()
    try:
        app.run()
    finally:
        keys.restore_terminal()


if __name__ == "__main__":
    run_interactive()
