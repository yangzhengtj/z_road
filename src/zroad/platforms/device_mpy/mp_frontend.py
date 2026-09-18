"""mp_frontend.py —— Cardputer ADV 真机适配层（仅 MicroPython 运行）。

依赖 BeanpieChen 的 “MicroPython Shell for Cardputer ADV” 固件：
  · dev 模块已初始化 ST7789 帧缓冲（dev.fb / dev.fb_present）与 TCA8418
    键盘监听（dev.add_kbl），microSD 开机自动挂载在 /sd；
  · shell.hide() 会注销 Shell 自己的键盘监听，把整屏和按键让给游戏；
  · 退出游戏调用 machine.reset()，重启后自动回到 Shell。

SD 卡目录布局（由 tools/build_mpy_bundle.py 生成）：
    /sd/zroad/main.py                 ← 在 Shell 里 run('/sd/zroad/main.py')
    /sd/zroad/zroad/...               ← 游戏包（core + data + 设备前端）
    /sd/zroad/saves/*.json            ← 自动/手动存档
    /sd/zroad/records.json            ← 历史最佳
"""

import dev
import machine
import time
import ujson
import uos

# 逻辑颜色 → 固件帧缓冲颜色（RGB565）
_COLORS = {
    0: dev.DPC_X,    # 黑
    1: dev.DPC_W,    # 白
    2: dev.DPC_R,    # 红
    3: dev.DPC_G,    # 绿
    4: dev.DPC_Y,    # 黄
    5: dev.DPC_C,    # 青
    6: dev.DPC_M,    # 品红
    7: dev.DPC_GR,   # 灰
    8: dev.DPC_B,    # 蓝
    9: dev.DPC_DG,   # 暗绿
}

ROOT = "/sd/zroad"


class MPDriver(object):
    """把逻辑屏幕的矩形/呈现调用转给固件 dev.fb。"""

    def clear_screen(self, color):
        """整屏填底色（一次 C 调用，替代逐格铺底）。"""
        dev.fb_fill(_COLORS[color])

    def draw_cells(self, screen):
        """在单个函数内遍历全部字符格并光栅化。

        为什么不沿用“每格回调一次驱动”的写法：真机上每次 Python 层
        函数调用都要在堆上分配帧对象，一屏 240 格 × 若干扫描线段，
        每帧产生上千次短命分配，会把堆打成蜂窝。把循环收进本函数后，
        只有一次 Python 帧，内部全部是对 dev.fb_frect 的 C 调用
        （小整数参数不占堆），整屏绘制近乎零堆分配。
        """
        from .screen import glyph, CELL_W, CELL_H, COLS, ROWS
        frect = dev.fb_frect
        rect = dev.fb_rect
        colors = _COLORS
        chars = screen.chars
        cont = screen.cont
        fgs = screen.fgs
        space = 32
        # 模拟器桩把逐段 C 调用折成一屏一次记账（见 stubs/dev.py）
        noop = getattr(dev, "FB_NOOP_BLIT", False)
        n_frect = 0
        n_nonblack = 0
        n_rect = 0
        for row in range(ROWS):
            y = row * CELL_H
            base = row * COLS
            for col in range(COLS):
                i = base + col
                if cont[i]:
                    continue
                code = chars[i]
                if code == 0 or code == space:
                    continue
                width, data, offset = glyph(code)
                if data is None:
                    # 缺字豆腐块
                    x = col * CELL_W
                    if noop:
                        n_rect += 1
                    else:
                        rect(x, y, CELL_W - 1, CELL_H - 1, colors[fgs[i]])
                    continue
                color = colors[fgs[i]]
                x = col * CELL_W
                bytes_per_row = width >> 3
                for r in range(CELL_H):
                    b = offset + r * bytes_per_row
                    c = 0
                    while c < width:
                        byte = data[b + (c >> 3)]
                        if byte & (0x80 >> (c & 7)):
                            start = c
                            while c < width:
                                byte = data[b + (c >> 3)]
                                if not (byte & (0x80 >> (c & 7))):
                                    break
                                c += 1
                            if noop:
                                n_frect += 1
                                if color != dev.DPC_X:
                                    n_nonblack += 1
                            else:
                                frect(x + start, y + r, c - start, 1, color)
                        else:
                            c += 1
        if noop:
            dev.add_blit_stats(n_frect, n_nonblack, n_rect)

    def fill_rect(self, x, y, w, h, color):
        dev.fb_frect(x, y, w, h, _COLORS[color])

    def stroke_rect(self, x, y, w, h, color):
        dev.fb_rect(x, y, w, h, _COLORS[color])

    def present(self):
        dev.fb_present()


class MPKeys(object):
    """键盘监听者：固件事件 → 游戏内部按键 token，放入队列。

    token 约定（与 sim.py 保持一致）：
      可打印字符（字母统一转小写）、' '、'ENTER'、'ESC'、'BS'、
      'UP'、'DOWN'、'LEFT'、'RIGHT'。
    """

    def __init__(self):
        self.queue = []
        dev.add_kbl(self)

    def on_kbe(self, event, d0, d1):
        if event == dev.KBE_INPUT:
            code = d0
            if 65 <= code <= 90:       # Shift 产生的大写字母转小写
                code += 32
            self.queue.append(chr(code))
        elif event == dev.KBE_DOWN:
            token = None
            if d0 == dev.KSC_ENTER:
                token = "ENTER"
            elif d0 == dev.KSC_BS:
                token = "BS"
            elif d1 == dev.KV_ESC:
                token = "ESC"
            elif d1 == dev.KV_UP:
                token = "UP"
            elif d1 == dev.KV_DOWN:
                token = "DOWN"
            elif d1 == dev.KV_LEFT:
                token = "LEFT"
            elif d1 == dev.KV_RIGHT:
                token = "RIGHT"
            if token is not None:
                self.queue.append(token)

    def get_key(self):
        """阻塞等待一个按键（设备端在此期间进入轻睡眠省电）。"""
        import gc
        ticks = 0
        while not self.queue:
            time.sleep_ms(20)
            ticks += 1
            if ticks >= 100:          # 约每 2 秒回收一次界面临时对象
                gc.collect()
                ticks = 0
        return self.queue.pop(0)


class MPPlatform(object):
    """device_app 需要的平台能力：驱动、按键、数据与存档目录、退出。"""

    def __init__(self):
        import shell
        shell.hide()                 # 注销 Shell 键盘监听，接管屏幕
        dev.fb_fill(dev.DPC_X)
        dev.fb_present()
        self.driver = MPDriver()
        self.keys = MPKeys()
        self.get_key = self.keys.get_key
        from .stores import JsonFileStore
        self.store = JsonFileStore(ROOT, ujson, uos, now_func=self._now)

    def load_json(self, name):
        import gc
        path = ROOT + "/zroad/data/" + name
        gc.collect()              # JSON 解析有峰值内存，读前先回收
        # 用 load(文件流) 而不是 loads(整段字符串)：后者会让“32KB 源文本”
        # 和“解析结果”同时驻留内存，在设备上极易触发 MemoryError。
        with open(path, "r") as f:
            data = ujson.load(f)
        gc.collect()
        return data

    def data_path(self, name):
        """返回数据文件路径（StageCatalog 逐行读取 JSONL 用）。"""
        return ROOT + "/zroad/data/" + name

    def json_loads(self, text):
        # 卡牌 JSONL 走低碎片解析器（见 microjson 模块说明）；
        # 配置/存档等小文件仍可直接用 ujson。
        from . import microjson
        return microjson.loads(text)

    def _now(self):
        try:
            dt = machine.RTC().datetime()
            # 元组顺序：年,月,日,星期,时,分,秒,百分秒
            return "%04d-%02d-%02d %02d:%02d:%02d" % (
                dt[0], dt[1], dt[2], dt[4], dt[5], dt[6])
        except Exception:
            return ""

    def exit(self):
        machine.reset()


def boot():
    """main.py 入口：构造真机平台并启动游戏，异常时回 Shell 便于排查。"""
    try:
        # device_app / font_data 体积较大，导入前先回收一次内存，
        # 降低小内存设备上的导入峰值（.mpy 预编译之外的第二道保险）。
        import gc
        gc.collect()
        from .device_app import main
        gc.collect()
        platform = MPPlatform()
        main(platform)
    except Exception as exc:
        # Shell 已 hide，REPL 不可见，必须把异常直接画到屏幕上。
        # 注意：MicroPython 没有 sys.exc_info()，必须用 as exc 绑定。
        # 错误屏走与游戏相同的 fb_frect 通路 + 自带 ASCII 点阵，
        # 不依赖固件 fb_text 的签名（各固件不一致）。
        import gc
        import sys
        import uio
        gc.collect()                     # 异常本身常是 MemoryError，先回收
        try:
            from . import font_data
            buf = uio.StringIO()
            sys.print_exception(exc, buf)
            text = buf.getvalue()
            dev.fb_fill(dev.DPC_DR)      # 暗红底，提示这是错误屏

            def draw_ascii(x, y, code, color):
                start = (code - 32) * 16
                for row in range(16):
                    bits = font_data.ASCII_DATA[start + row]
                    run_start = -1
                    for col in range(8):
                        on = bits & (0x80 >> col)
                        if on:
                            if run_start < 0:
                                run_start = col
                        elif run_start >= 0:
                            dev.fb_frect(x + run_start, y + row,
                                         col - run_start, 1, color)
                            run_start = -1
                    if run_start >= 0:
                        dev.fb_frect(x + run_start, y + row,
                                     8 - run_start, 1, color)

            # 异常文本只保留可打印 ASCII（异常名/路径/行号都是 ASCII），
            # 非 ASCII 替换为 ?，按 30 列折行，最多 8 行。
            lines = []
            for raw in text.split("\n"):
                row = ""
                for ch in raw:
                    code = ord(ch)
                    row += ch if 32 <= code <= 126 else "?"
                while len(row) > 30:
                    lines.append(row[:30])
                    row = row[30:]
                lines.append(row)
            y = 2
            for line in lines[:8]:
                x = 2
                for ch in line[:30]:
                    draw_ascii(x, y, ord(ch), dev.DPC_W)
                    x += 8
                y += 16
            dev.fb_present()
        except Exception:
            # 最后兜底：哪怕文字画不出来，也要亮红屏表明启动失败
            try:
                dev.fb_fill(dev.DPC_R)
                dev.fb_present()
            except Exception:
                pass
        time.sleep(45)                   # 留足时间拍照
        machine.reset()
