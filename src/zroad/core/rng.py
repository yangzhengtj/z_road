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

内存实现（设备端）：624 个状态字若用 Python int 列表，在 MicroPython 上
要占约 23KB 且初始化过程会造成严重堆碎片；改用两个 bytearray 分别存
低/高 16 位后，每块只占 624×2≈1.25KB（拆成两块是为了把单次连续分配
需求从 2.5KB 降到 1.25KB，无 PSRAM 设备堆碎片后更容易分到）。
bytearray 是 CPython / MicroPython 共有的零依赖内置类型。
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
        # 用两个 bytearray 分别存 32 位状态字的低 16 位与高 16 位。
        # bytearray(n) 由虚拟机直接分配并清零、没有额外临时 bytes；
        # 而 array("H", bytes(...)) 构造时会同时存在两块 1.25KB，
        # 在碎片化的小堆上更容易 MemoryError。
        self._mt_lo = bytearray(2 * self._N)
        self._mt_hi = bytearray(2 * self._N)
        self._index = self._N
        self.calls = 0          # 已消耗的随机数个数（用于轻量存档）
        self._init_state(self.seed)

    def _mt_get(self, i):
        """读出第 i 个 32 位状态字（lo/hi 各两个字节，小端组合）。"""
        j = i * 2
        return (self._mt_lo[j] | (self._mt_lo[j + 1] << 8)
                | (self._mt_hi[j] << 16) | (self._mt_hi[j + 1] << 24))

    def _mt_set(self, i, value):
        """写入第 i 个 32 位状态字（自动截断为 32 位无符号）。"""
        value = _uint32(value)
        j = i * 2
        self._mt_lo[j] = value & 0xFF
        self._mt_lo[j + 1] = (value >> 8) & 0xFF
        self._mt_hi[j] = (value >> 16) & 0xFF
        self._mt_hi[j + 1] = (value >> 24) & 0xFF

    def reseed(self, seed=None):
        """复用现有数组重新播种（设备端避免重新分配 2.5KB 状态数组）。"""
        if seed is None:
            seed = random_seed()
        self.seed = _uint32(int(seed))
        self._init_state(self.seed)

    def _init_state(self, seed):
        """按标准 MT19937 种子填充算法初始化 624 个状态字。"""
        self._mt_set(0, seed)
        for i in range(1, self._N):
            prev = self._mt_get(i - 1)
            self._mt_set(
                i, 1812433253 * (prev ^ (prev >> 30)) + i
            )
        self._index = self._N
        self.calls = 0

    def _generate(self):
        """用当前 624 个状态字生成下一批 624 个状态字（twist）。"""
        n = self._N
        for i in range(n):
            cur = self._mt_get(i)
            nxt = self._mt_get((i + 1) % n)
            y = (cur & self._UPPER_MASK) | (nxt & self._LOWER_MASK)
            value = self._mt_get((i + self._M) % n) ^ (y >> 1)
            if y & 1:
                value = _uint32(value ^ self._MATRIX_A)
            self._mt_set(i, value)
        self._index = 0

    def _next_u32(self):
        """返回下一个 32 位无符号随机整数。"""
        if self._index >= self._N:
            self._generate()
        y = self._mt_get(self._index)
        y ^= y >> 11
        y ^= (y << 7) & 0x9D2C5680
        y ^= (y << 15) & 0xEFC60000
        y ^= y >> 18
        self._index += 1
        self.calls += 1
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
    #
    # 线格式用“种子 + 已消耗随机数个数”（mt-replay）：MT19937 序列完全由
    # 种子决定，读档后重新播种并空转 calls 次即可精确回到原位置。存档因此
    # 只有几十字节，而不是 624 个状态字（列表约 17KB / hex 约 5KB），
    # 这对无 PSRAM、堆碎片严重的设备至关重要。
    # 同时保留对开发期 mt-hex / mt-list 完整快照格式的读取兼容。

    _HEX_DIGITS = "0123456789abcdef"
    _HEX_NIBBLE = b"0123456789abcdef"

    def _to_hex(self):
        # 逐状态字取小端 4 字节再转十六进制（开发期快照格式，设备不使用）。
        out = bytearray(self._N * 8)
        table = self._HEX_NIBBLE
        for i in range(self._N):
            value = self._mt_get(i)
            base = i * 8
            for shift in range(4):
                byte = (value >> (8 * shift)) & 0xFF
                j = base + shift * 2
                out[j] = table[(byte >> 4) & 0x0F]
                out[j + 1] = table[byte & 0x0F]
        return out.decode("ascii")

    def _from_hex(self, text):
        digits = self._HEX_DIGITS
        for i in range(self._N):
            value = 0
            base = i * 8
            for shift in range(4):
                high = digits.find(text[base + shift * 2])
                low = digits.find(text[base + shift * 2 + 1])
                value |= ((high << 4) | low) << (8 * shift)
            self._mt_set(i, value)

    def get_state(self):
        """导出可 JSON 序列化的随机状态（轻量 replay 格式）。"""
        return {"kind": "mt-replay", "seed": self.seed,
                "calls": self.calls}

    def set_state(self, state):
        """按 get_state() 导出的 dict 恢复随机状态。

        遇到 v0.7.1 及更早版本（基于 CPython random）的旧存档时，旧格式
        无法映射到本实现，此时退回为“按旧种子重新播种”：牌堆顺序已保存在
        存档里不受影响，仅此后的掷骰序列重新开始，属可接受的一次性迁移。
        返回 True 表示精确恢复，False 表示走了兼容回退。
        """
        if state.get("kind") == "mt-replay":
            # 轻量格式：重新播种后空转到相同消耗位置
            self.seed = _uint32(int(state.get("seed", 0)))
            self._init_state(self.seed)
            total = int(state.get("calls", 0))
            for _i in range(total):
                self._next_u32()
            return True
        if state.get("kind") == "mt":
            if state.get("mt_hex"):
                mt_hex = state["mt_hex"]
                if len(mt_hex) != self._N * 8:
                    raise ValueError("mt_hex 长度应为 %d，实为 %d"
                                     % (self._N * 8, len(mt_hex)))
                self.seed = _uint32(int(state.get("seed", 0)))
                self._from_hex(mt_hex)
                self._index = int(state["index"])
                self.calls = int(state.get("calls", 0))
                return True
            if len(state.get("mt", ())) == self._N:
                # 兼容早期测试/旧档的整数列表格式
                self.seed = _uint32(int(state.get("seed", 0)))
                for i, value in enumerate(state["mt"]):
                    self._mt_set(i, int(value))
                self._index = int(state["index"])
                self.calls = int(state.get("calls", 0))
                return True
        # 旧格式（CPython random 的 version/internal/gauss_next）。
        old_seed = state.get("seed")
        seed = old_seed if isinstance(old_seed, int) else random_seed()
        self.seed = _uint32(seed)
        self._init_state(self.seed)
        return False
