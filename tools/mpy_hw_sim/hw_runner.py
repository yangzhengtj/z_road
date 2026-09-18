"""hw_runner.py —— 在电脑端 MicroPython 解释器里跑“真机固件模拟”。

由 build_hw_sim.py 准备好 bundle（.mpy 卡包）后执行：

    micropython tools/mpy_hw_sim/hw_runner.py          # 自动整局
    micropython tools/mpy_hw_sim/hw_runner.py errtest  # 异常上屏

stubs/ 按 BeanpieChen v1.2 固件的 dev/shell/machine/time 接口做桩，
bundle/ 是与真机卡包同构的 .mpy 产物，因此这里能抓到：
  · 对固件接口的错误调用（属性名/参数个数）；
  · MicroPython 语法/内建差异（ujson.load、uio、gc 等）；
  · font.bin 按需读取、.mpy 相对导入、SD 目录布局问题；
  · 启动异常是否真的能画到屏幕上（不再黑屏吞错）。
"""

import os
import sys

# MicroPython 的 os 没有 os.path 子模块，手动取脚本所在目录
_argv0 = sys.argv[0]
HERE = _argv0.rsplit("/", 1)[0] if "/" in _argv0 else os.getcwd()
STUBS = HERE + "/stubs"
BUNDLE = HERE + "/bundle"
for path in (STUBS, BUNDLE):
    if path not in sys.path:
        sys.path.insert(0, path)

import dev            # noqa: E402  桩：固件显示/键盘
import machine        # noqa: E402  桩：reset 抛 machine.Reset
import time as faketime            # noqa: E402  桩：sleep_ms 即喂键节拍
import uos            # noqa: E402

import zroad.platforms.device_mpy.mp_frontend as fe   # noqa: E402
from zroad.platforms.device_mpy import device_app     # noqa: E402

FAILURES = []


def check(condition, message):
    if condition:
        print("PASS:", message)
    else:
        FAILURES.append(message)
        print("FAIL:", message)


class RecDriver(object):
    """包在 MPDriver 外：截获逻辑屏幕用于自动玩家决策与断言。"""

    def __init__(self, real):
        self.real = real
        self.screen = None
        # 注意：不能累积每一帧的标题字符串，几百帧会吃掉十几 KB 堆，
        # 模拟的是真机（真机驱动不留存帧），只保留当前标题与计数。
        self.last_title = b""
        self.menu_count = 0
        self.planning_count = 0
        self.present_count = 0

    def begin_screen(self, screen):
        self.screen = screen

    def fill_rect(self, x, y, w, h, color):
        self.real.fill_rect(x, y, w, h, color)

    def stroke_rect(self, x, y, w, h, color):
        self.real.stroke_rect(x, y, w, h, color)

    # 标题识别用 UTF-8 bytes 常量：row_bytes 不产生单字符 qstr，
    # 避免限堆测试中每帧 chr() 把整屏汉字驻留成永久 qstr 干扰内存结论。
    TITLE_MENU = "末路求生".encode("utf-8")
    TITLE_PLANNING = "轮 阶段".encode("utf-8")

    def present(self):
        self.present_count += 1
        if self.screen is not None:
            title = self.screen.row_bytes(0)
            self.last_title = title
            if title.startswith(self.TITLE_MENU):
                self.menu_count += 1
            elif self.TITLE_PLANNING in title:
                self.planning_count += 1
            if self.screen.missing:
                check(False, "出现缺字：" + self.screen.missing_text())
        self.real.present()


def run_full_game():
    # ROOT 指向 bundle（其布局与 /sd/zroad 一致：zroad/data、saves）
    fe.ROOT = BUNDLE
    try:
        uos.mkdir(BUNDLE + "/saves")
    except OSError:
        pass

    platform = fe.MPPlatform()
    rec = RecDriver(platform.driver)
    platform.driver = rec
    app = device_app.DeviceApp(platform)

    state = {"keys": 0, "menu_seen": 0, "inspect_done": False}
    pending = {"step": "", "meds_title": b"", "meds_keys": []}
    T_MEDS = "近战骰面".encode("utf-8")
    T_COMBAT = "战斗".encode("utf-8")
    T_RANGED = "远程攻击".encode("utf-8")

    def decide():
        """保守生存策略的自动玩家：战斗只打近战、所有机会骰都用药，
        保证整局稳定存活到 8 轮（内存测试需要覆盖完整后期流程；
        团灭提前结束会让限堆结论偏乐观）。"""
        state["keys"] += 1
        if state["keys"] > 4000:
            raise RuntimeError("自动玩家按键超限，疑似死循环")
        check(rec.present_count > 0,
              "第 %d 键之前界面至少渲染过一屏" % state["keys"])
        title = rec.last_title
        if title.startswith(rec.TITLE_MENU):
            state["menu_seen"] += 1
            return "q" if state["menu_seen"] >= 2 else "n"
        if title == "选择难度".encode("utf-8"):
            return "e"
        if title == "新游戏".encode("utf-8"):
            return "ENTER"
        if rec.TITLE_PLANNING in title:
            return planning_key()
        if title in ("路径2左卡".encode("utf-8"),
                     "路径2右卡".encode("utf-8")):
            return "ENTER"
        if title == T_COMBAT:
            # 近战：远程会消耗弹药且对内存覆盖无帮助，近战流程分支更多
            return "m"
        if title == T_RANGED:
            # 弹药数量询问：默认 1
            return "ENTER"
        if title == T_MEDS:
            return meds_key(rec)
        pending["meds_title"] = title
        return "ENTER"

    def planning_key():
        # 首局演示 2L 看明牌：2 → l → （看牌页）→ 1 → 回车
        if not state["inspect_done"]:
            if pending["step"] == "":
                pending["step"] = "inspect_path"
                return "2"
            if pending["step"] == "inspect_path":
                pending["step"] = "inspect_side"
                return "l"
            if pending["step"] == "inspect_side":
                state["inspect_done"] = True
                pending["step"] = "path_num"
                return "1"
        if pending["step"] in ("", "inspect_side"):
            pending["step"] = "path_num"
            return "1"
        if pending["step"] == "path_num":
            pending["step"] = ""
            return "ENTER"
        pending["step"] = "path_num"
        return "1"

    B_HAO = "号骰：".encode("utf-8")
    B_SHORT = "药剂不足".encode("utf-8")
    B_PICKED = "已选".encode("utf-8")
    B_SLASH = "/".encode("utf-8")

    def meds_key(rec):
        """药剂屏自动选择：每次近战骰最多选 1 个可用机会骰（选多了
        药剂不够引擎会报错），翻页找，找不到就回车确认。"""
        scr = rec.screen
        found_digit = None
        picked = False
        page_total = 1
        for r in range(1, 8):
            row = scr.row_bytes(r)
            idx = row.find(B_HAO)
            if idx >= 0:
                if row.find(B_PICKED) >= 0:
                    picked = True
                elif row.find(B_SHORT) < 0:
                    # 行首附近的 ASCII 数字即骰号
                    for b in row[:idx]:
                        if 0x31 <= b <= 0x39:
                            found_digit = chr(b)
                            break
            slash = row.find(B_SLASH)
            if slash >= 0:
                # 底栏 “当前页/总页数”
                n = 0
                k = slash + 1
                while k < len(row) and 0x30 <= row[k] <= 0x39:
                    n = n * 10 + (row[k] - 0x30)
                    k += 1
                if n:
                    page_total = n
        # 已经选中过一个机会骰：本屏直接确认（每次近战最多用 1 药）
        if picked:
            pending["meds_picked"] = False
            return "ENTER"
        if found_digit is not None:
            # 选中后下一帧再回车（由“已选”分支处理）
            pending["meds_picked"] = True
            return found_digit
        if pending.get("meds_picked"):
            pending["meds_picked"] = False
            return "ENTER"
        # 本页没有可用机会骰：若还有下一页则翻页，否则确认
        cur = 1
        if scr is not None:
            f7 = scr.row_bytes(7)
            sl = f7.find(B_SLASH)
            if sl >= 0:
                k = sl - 1
                while k >= 0 and 0x30 <= f7[k] <= 0x39:
                    cur = f7[k] - 0x30
                    k -= 1
        if cur < page_total:
            return "DOWN"
        return "ENTER"

    def pump():
        keys = platform.keys
        if not keys.queue:
            keys.queue.append(decide())

    faketime.pump = pump
    reset_seen = False
    try:
        app.run()
    except machine.Reset:
        reset_seen = True
    faketime.pump = None

    check(reset_seen, "退出游戏调用 machine.reset() 回 Shell")
    check(state["menu_seen"] == 2, "主菜单出现两次（开局+终局后），实际 %d"
          % state["menu_seen"])
    check(rec.planning_count >= 8,
          "8 轮规划页全部出现，实际 %d 屏" % rec.planning_count)
    check(rec.present_count > 30, "整局 present 刷屏 %d 次"
          % rec.present_count)
    check(dev.nonblack_calls > 200, "非黑像素矩形绘制 %d 次（文字确实上屏）"
          % dev.nonblack_calls)
    check(dev.frect_calls > 200, "fb_frect 总调用 %d 次" % dev.frect_calls)

    # 存档落盘
    save_files = uos.listdir(BUNDLE + "/saves")
    check(any(f.startswith("auto") for f in save_files),
          "自动存档写入 saves/：%s" % ",".join(save_files))

    # ---- 键盘事件解码（固件 on_kbe → 游戏 token）----
    k = platform.keys
    k.queue = []
    k.on_kbe(dev.KBE_INPUT, ord("N"), None)
    check(k.get_key() == "n", "KBE_INPUT 大写 N 转小写 n")
    k.on_kbe(dev.KBE_INPUT, ord("l"), None)
    check(k.get_key() == "l", "KBE_INPUT 小写 l 直通")
    k.on_kbe(dev.KBE_DOWN, dev.KSC_ENTER, None)
    check(k.get_key() == "ENTER", "KSC_ENTER → ENTER")
    k.on_kbe(dev.KBE_DOWN, dev.KSC_BS, None)
    check(k.get_key() == "BS", "KSC_BS → BS")
    k.on_kbe(dev.KBE_DOWN, 0, dev.KV_UP)
    check(k.get_key() == "UP", "FN 虚拟键 KV_UP → UP")
    k.on_kbe(dev.KBE_DOWN, 0, dev.KV_ESC)
    check(k.get_key() == "ESC", "FN 虚拟键 KV_ESC → ESC")


def run_error_screen():
    # 准备一个空 ROOT（有 saves 目录但没有数据文件），逼 DeviceApp 启动失败
    root = HERE + "/empty_root"
    try:
        uos.mkdir(root)
    except OSError:
        pass
    try:
        uos.mkdir(root + "/saves")
    except OSError:
        pass
    fe.ROOT = root
    dev.reset_stats()

    reset_seen = False
    try:
        fe.boot()
    except machine.Reset:
        reset_seen = True

    check(reset_seen, "启动失败后最终 machine.reset()")
    check(dev.fill_color == dev.DPC_DR, "错误屏暗红底色已填充")
    check(dev.present_calls >= 1, "错误屏调用 fb_present 上屏")
    check(dev.nonblack_calls > 50, "错误文字用 fb_frect 画出（%d 段）"
          % dev.nonblack_calls)


if __name__ == "__main__":
    # 整局模拟与错误屏测试必须在各自独立的解释器进程里跑：错误屏模拟的
    # 是“开机即失败”的场景，若放在整局之后的同一进程里，堆已被整局
    # 消耗，错误屏分配失败不代表真机开机时也会失败。
    if len(sys.argv) > 1 and sys.argv[1] == "errtest":
        run_error_screen()
    else:
        run_full_game()
    if FAILURES:
        print("\n==== %d 项失败 ====" % len(FAILURES))
        for item in FAILURES:
            print(" -", item)
        sys.exit(1)
    print("\n==== 真机模拟全部通过 ====")
