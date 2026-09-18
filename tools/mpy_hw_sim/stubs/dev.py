"""dev.py —— BeanpieChen《MicroPython Shell for Cardputer ADV》v1.2 的
固件 dev 模块桩（仅用于电脑端真机模拟，见 tools/mpy_hw_sim）。

常量与函数名严格对齐固件 modules/dev.py（tag v1.2），不实现真实光栅化，
只统计调用，用来验证游戏前端是否按固件真实接口编程。
"""

# ---- 颜色（RGB565，桩里给互不相同的值即可）----
DPC_X = 0x0000
DPC_W = 0xFFFF
DPC_R = 0xF800
DPC_G = 0x07E0
DPC_B = 0x001F
DPC_C = 0x07FF
DPC_Y = 0xFFE0
DPC_M = 0xF81F
DPC_GR = 0x8410
DPC_DR = 0x7800
DPC_DG = 0x03E0
DPC_DB = 0x000F
DPC_DC = 0x03EF
DPC_DM = 0x780F
DPC_OR = 0xFD20
DPC_PU = 0xC1F7
DPC_DOR = 0x6200
DPC_DPU = 0x3007

# ---- 键盘事件 ----
KBE_DOWN = 0
KBE_UP = 1
KBE_INPUT = 2

# ---- 扫描码（KBE_DOWN 的 d0）----
KSC_BS = 65
KSC_ENTER = 67

# ---- FN 虚拟键（KBE_DOWN 的 d1）----
KV_ESC = 101
KV_DEL = 102
KV_UP = 103
KV_DOWN = 104
KV_LEFT = 105
KV_RIGHT = 106

# ---- 调用统计（测试断言用；固件里没有这些变量）----
_kbls = []
fill_color = None
frect_calls = 0
rect_calls = 0
nonblack_calls = 0
present_calls = 0
texts = []


def reset_stats():
    global frect_calls, rect_calls, nonblack_calls, present_calls, texts
    global fill_color
    frect_calls = 0
    rect_calls = 0
    nonblack_calls = 0
    present_calls = 0
    texts = []
    fill_color = None


def add_kbl(listener):
    _kbls.append(listener)


def remove_kbl(listener):
    _kbls.remove(listener)


def fb_fill(color):
    global fill_color
    fill_color = color


def fb_frect(x, y, w, h, color):
    global frect_calls, nonblack_calls
    frect_calls += 1
    if color != DPC_X:
        nonblack_calls += 1


def fb_rect(x, y, w, h, color):
    global rect_calls
    rect_calls += 1


def fb_text(*args):
    # 固件签名 fb_text(x, y, s, fg, bg)（透传给 st7789 FB.text）
    texts.append(args)


def fb_present():
    global present_calls
    present_calls += 1


# 模拟器专用：真机上 fb_frect/fb_rect 是 C 函数，调用不产生 Python
# 帧对象、不搅动堆；纯 Python 桩若逐段调用，每屏上千次帧分配会让
# 模拟器比真机碎片化严重得多。draw_cells 在该标志下改为整屏只做一次
# 统计回传（见 MPDriver.draw_cells），以贴近真机的堆行为。
FB_NOOP_BLIT = True


def add_blit_stats(frect_n, nonblack_n, rect_n=0):
    """一屏的光栅化调用一次性记账（模拟器用，真机无此函数）。"""
    global frect_calls, nonblack_calls, rect_calls
    frect_calls += frect_n
    nonblack_calls += nonblack_n
    rect_calls += rect_n


def batt_uv():
    return 3.9
