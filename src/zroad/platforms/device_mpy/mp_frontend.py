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
        while not self.queue:
            time.sleep_ms(20)
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
        path = ROOT + "/zroad/data/" + name
        with open(path, "r") as f:
            return ujson.loads(f.read())

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
        from .device_app import main
        platform = MPPlatform()
        main(platform)
    except Exception:
        # 异常信息输出到 USB REPL（Thonny/串口可看），5 秒后重启回 Shell
        import sys
        sys.print_exception(sys.exc_info()[1])
        time.sleep(5)
        machine.reset()
