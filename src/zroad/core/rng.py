"""rng.py —— 纯 Python 实现的可复现随机源（桌面 / MicroPython 同一份代码）。

为什么不再使用标准库 random.Random：
    CPython 的 random 模块依赖 C 实现的 MT19937，MicroPython 自带的 random
    模块既没有 Random 类，也没有 getstate/setstate，无法在 Cardputer 上使用，
    更无法保证桌面与设备随机序列一致。
    这里用纯 Python 实现标准 32 位梅森旋转算法（MT19937），它：
      1. 零依赖：只用内置 int/列表，CPython 与 MicroPython 行为完全一致；
      2. 可复现：相同种子产生完全相同的摸牌/掷骰序列，便于测试与复现 bug；
      3. 可存档：状态就是 624 个整数 + 下标，可直接 JSON 序列化，读档后
         后续随机序列与不存档时严格一致；
      4. 可替换：将来设备端若想用硬件真随机，只需在构造时传入一个种子整数。

注意：核心层其他模块一律不直接 import random，只接收本类实例。
"""


def _uint32(value):
    """把整数截断为无符号 32 位。"""
    return value & 0xFFFFFFFF


def random_seed():
    """生成一个不可预测的种子整数（用于不指定种子的新对局）。

    依次尝试：操作系统熵源（CPython 的 os.urandom、ESP32 MicroPython
    同样提供 os.urandom）→ 硬件毫秒计数器 → 固定兜底值。
    """
    try:
        import os
        # 16 字节熵 → 整数；MicroPython 也支持 int.from_bytes。
        return int.from_bytes(os.urandom(16), "little")
    except Exception:
        pass
    try:
        import time
        return int(time.ticks_ms()) & 0xFFFFFFFF
    except Exception:
        return 20260917


class SeededRng:
    """对局随机源。一次对局使用一个实例，便于整局复现。"""

    # MT19937 常量
    _N = 624
    _M = 397
    _MATRIX_A = 0x9908B0DF
    _UPPER_MASK = 0x80000000
    _LOWER_MASK = 0x7FFFFFFF

    def __init__(self, seed=None):
        if seed is None:
            seed = random_seed()
        # 种子统一按 32 位处理，保证 CPython / MicroPython 结果一致。
        self.seed = _uint32(int(seed))
        self._mt = [0] * self._N
        self._index = self._N
        self._init_state(self.seed)

    def _init_state(self, seed):
        """按标准 MT19937 种子填充算法初始化 624 个状态字。"""
        self._mt[0] = seed
        for i in range(1, self._N):
            prev = self._mt[i - 1]
            self._mt[i] = _uint32(
                1812433253 * (prev ^ (prev >> 30)) + i
            )
        self._index = self._N

    def _generate(self):
        """用当前 624 个状态字生成下一批 624 个状态字（twist）。"""
        for i in range(self._N):
            y = (self._mt[i] & self._UPPER_MASK) | (
                self._mt[(i + 1) % self._N] & self._LOWER_MASK
            )
            self._mt[i] = self._mt[(i + self._M) % self._N] ^ (y >> 1)
            if y & 1:
                self._mt[i] = _uint32(self._mt[i] ^ self._MATRIX_A)
        self._index = 0

    def _next_u32(self):
        """返回下一个 32 位无符号随机整数。"""
        if self._index >= self._N:
            self._generate()
        y = self._mt[self._index]
        y ^= y >> 11
        y ^= (y << 7) & 0x9D2C5680
        y ^= (y << 15) & 0xEFC60000
        y ^= y >> 18
        self._index += 1
        return _uint32(y)

    # ---------- 游戏使用的便捷方法 ----------

    def d6(self):
        """掷一颗六面骰，返回 1..6。"""
        return self._next_u32() % 6 + 1

    def roll_many(self, count):
        """一次掷 count 颗六面骰，返回结果列表。"""
        return [self.d6() for _ in range(count)]

    def shuffle(self, seq):
        """原地洗牌（Fisher–Yates，返回 None，与标准库习惯一致）。"""
        for i in range(len(seq) - 1, 0, -1):
            j = self._next_u32() % (i + 1)
            seq[i], seq[j] = seq[j], seq[i]

    def pick(self, seq):
        """从序列中均匀随机取一个元素。"""
        return seq[self._next_u32() % len(seq)]

    def sample(self, seq, count):
        """无放回随机抽取 count 个元素，返回新列表。"""
        indices = list(range(len(seq)))
        self.shuffle(indices)
        return [seq[i] for i in indices[:count]]

    # ---- 随机状态的保存与恢复（为存档/读档服务） ----

    def get_state(self):
        """导出可 JSON 序列化的完整随机状态。"""
        return {"kind": "mt", "seed": self.seed,
                "mt": list(self._mt), "index": self._index}

    def set_state(self, state):
        """按 get_state() 导出的 dict 恢复随机状态。

        遇到 v0.7.1 及更早版本（基于 CPython random）的旧存档时，旧格式
        无法映射到本实现，此时退回为“按旧种子重新播种”：牌堆顺序已保存在
        存档里不受影响，仅此后的掷骰序列重新开始，属可接受的一次性迁移。
        返回 True 表示精确恢复，False 表示走了兼容回退。
        """
        if state.get("kind") == "mt" and len(state.get("mt", ())) == self._N:
            self.seed = _uint32(int(state.get("seed", 0)))
            self._mt = [_uint32(int(x)) for x in state["mt"]]
            self._index = int(state["index"])
            return True
        # 旧格式（CPython random 的 version/internal/gauss_next）。
        old_seed = state.get("seed")
        seed = old_seed if isinstance(old_seed, int) else random_seed()
        self.seed = _uint32(seed)
        self._init_state(self.seed)
        return False
