"""rng.py —— 可注入种子的随机数端口（阶段 0 即提供完整实现）。

为什么核心层需要自己的随机数封装：
    1. 可复现：给定相同种子，整局摸牌、掷骰序列完全一致，便于写测试、复现 bug；
    2. 可移植：本实现只用 MicroPython 同样支持的 random.Random，设备端无需改动；
    3. 可替换：将来设备端若想用硬件真随机，只需实现同名方法的另一个类注入引擎。

注意：核心层其他模块一律不直接 import random，只接收本类实例。
"""

import random


class SeededRng:
    """对局随机源。一次对局使用一个实例，便于整局复现。"""

    def __init__(self, seed=None):
        # CPython 的 random.Random 与 MicroPython 的 random 模块接口基本一致；
        # seed=None 时由系统提供熵（等价于真随机开局）。
        self._r = random.Random(seed)
        self.seed = seed

    def d6(self):
        """掷一颗六面骰，返回 1..6。"""
        return self._r.randint(1, 6)

    def roll_many(self, count):
        """一次掷 count 颗六面骰，返回结果列表。"""
        return [self.d6() for _ in range(count)]

    def shuffle(self, seq):
        """原地洗牌（返回 None，与标准库一致）。"""
        self._r.shuffle(seq)

    def pick(self, seq):
        """从序列中均匀随机取一个元素。"""
        return seq[self._r.randrange(len(seq))]

    def sample(self, seq, count):
        """无放回随机抽取 count 个元素，返回新列表。"""
        indices = list(range(len(seq)))
        self._r.shuffle(indices)
        return [seq[i] for i in indices[:count]]

    # ---- 随机状态的保存与恢复（为存档/读档服务） ----
    def get_state(self):
        """导出可 JSON 序列化的完整随机状态。

        标准库 random.Random.getstate() 返回 (版本号, 625 个整数的元组, 高斯缓存)，
        元组不能直接写进 JSON，这里统一转成 list；set_state 负责还原。
        这样存档后再读回，后续摸牌/掷骰序列与不存档时严格一致。
        """
        version, internal, gauss_next = self._r.getstate()
        return {"seed": self.seed, "version": version,
                "internal": list(internal), "gauss_next": gauss_next}

    def set_state(self, state):
        """按 get_state() 导出的 dict 恢复随机状态（与 get_state 互为逆操作）。"""
        self.seed = state["seed"]
        self._r.setstate((state["version"], tuple(state["internal"]),
                          state["gauss_next"]))
