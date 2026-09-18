"""machine.py —— 固件 machine 模块桩（真机模拟用，见 tools/mpy_hw_sim）。"""


class Reset(Exception):
    """模拟 machine.reset()：测试里抛出而不是真重启。"""


class RTC(object):
    def datetime(self, *args):
        # 年,月,日,星期,时,分,秒,百分秒
        return (2026, 9, 18, 4, 12, 0, 0, 0)


def reset():
    raise Reset()
