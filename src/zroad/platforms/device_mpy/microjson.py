"""microjson.py —— 设备端低碎片 JSON 解析器（只服务卡牌 JSONL）。

为什么不用 ujson.loads：一张完整卡牌约 1KB，ujson 一次性解析会在堆上
同时申请 dict 表、几十个字符串等大量对象；而“先 readline 读出整行再
解析”还需要一个约等于行长（最长近 1KB）的连续缓冲区。游戏中后期堆被
常驻对象切碎后，常常“空闲近 10KB 却分不出 700B 连续块”。

本模块是**流式**递归下降解析器：
  * 直接从文件对象按 32 字节小块读取（块缓冲固定 32B，可复用），
    连“整行缓冲区”都不需要；跳行时按块找换行符，同样零大分配；
  * 字符串用 bytearray 逐字节累加（源文本是 UTF-8，中文原样复制，
    不产生中间字符串），读完一次性 decode 成最终字符串；
  * 每解析完一个对象成员/数组元素主动 gc 一次，让 dict、bytearray
    扩容产生的旧块立即归还，把单次最大连续分配压到约 300~400B；
  * 不使用 split/strip/正则等复制整段文本的操作。

支持的 JSON 子集覆盖本项目工具链（Python json.dumps，ensure_ascii
=False）的全部输出：object、array、string（含全部标准转义和
\\uXXXX）、整数、小数、true/false/null。非法输入抛 ValueError，
数据文件由构建工具生成、有测试守门。

另外保留 loads(text) 入口（小文件/测试用），内部把文本包成字节流。
"""
import gc

# 每次从文件读取的块大小（固定的小连续分配，可被所有卡牌复用）
CHUNK = 32


class _Stream(object):
    """带 1 字节回退的字节块读取器。"""

    def __init__(self, fp=None, data=None):
        self.fp = fp
        self.b = data        # loads() 模式下直接用一段 bytes
        self.bi = 0
        self.bn = 0
        self.buf = bytearray(CHUNK)
        self.pushed = -1    # 回退的 1 个字节（-1 表示无）

    def get(self):
        """读 1 字节，文件结束返回 -1。"""
        if self.pushed >= 0:
            c = self.pushed
            self.pushed = -1
            return c
        if self.fp is not None:
            if self.bi >= self.bn:
                n = self.fp.readinto(self.buf)
                if n is None or n <= 0:
                    return -1
                self.bi = 0
                self.bn = n
            c = self.buf[self.bi]
            self.bi += 1
            return c
        if self.bi >= len(self.b):
            return -1
        c = self.b[self.bi]
        self.bi += 1
        return c

    def unget(self, c):
        """退回 1 字节（同一时刻最多 1 个）。"""
        self.pushed = c

    def skip_to_line(self, target):
        """定位到第 target 行（1 起）的行首；文件不足该行返回 False。

        全程只经过 32B 块缓冲，不构造任何整行对象；定位后本流可直接
        交给 _Parser 继续解析（块内多读的字节由位置索引保留）。
        """
        if target <= 1:
            return True
        need = target - 1   # 需要越过的换行符数量
        while need > 0:
            c = self.get()
            if c < 0:
                return False
            if c == 0x0A:
                need -= 1
        return True


def _encode_utf8(cp, buf):
    """把一个 Unicode 码点追加到 bytearray（UTF-8 编码）。"""
    if cp < 0x80:
        buf.append(cp)
    elif cp < 0x800:
        buf.append(0xC0 | (cp >> 6))
        buf.append(0x80 | (cp & 0x3F))
    elif cp < 0x10000:
        buf.append(0xE0 | (cp >> 12))
        buf.append(0x80 | ((cp >> 6) & 0x3F))
        buf.append(0x80 | (cp & 0x3F))
    else:
        buf.append(0xF0 | (cp >> 18))
        buf.append(0x80 | ((cp >> 12) & 0x3F))
        buf.append(0x80 | ((cp >> 6) & 0x3F))
        buf.append(0x80 | (cp & 0x3F))


class _Parser(object):
    def __init__(self, stream):
        self.s = stream

    def error(self, msg):
        raise ValueError("microjson: %s" % msg)

    def skip_ws(self):
        s = self.s
        while True:
            c = s.get()
            if c < 0:
                return
            if c > 0x20:
                s.unget(c)
                return

    def parse_value(self):
        self.skip_ws()
        c = self.s.get()
        if c < 0:
            self.error("unexpected end")
        if c == 0x7B:      # '{'
            return self.parse_object()
        if c == 0x5B:      # '['
            return self.parse_array()
        if c == 0x22:      # '"'
            return self.parse_string()
        if c == 0x74:      # true
            return self.parse_literal(b"rue", True)
        if c == 0x66:      # false
            return self.parse_literal(b"alse", False)
        if c == 0x6E:      # null
            return self.parse_literal(b"ull", None)
        if c == 0x2D or 0x30 <= c <= 0x39:   # '-' or digit
            return self.parse_number(c)
        self.error("unexpected char %d" % c)

    def parse_literal(self, rest, value):
        s = self.s
        for expected in rest:
            c = s.get()
            if c != expected:
                self.error("bad literal")
        return value

    def parse_number(self, first):
        s = self.s
        buf = bytearray()
        buf.append(first)
        is_float = False
        while True:
            c = s.get()
            if 0x30 <= c <= 0x39:
                buf.append(c)
            elif c == 0x2E or c == 0x65 or c == 0x45 or c == 0x2B \
                    or c == 0x2D:
                is_float = True
                buf.append(c)
            else:
                s.unget(c)
                break
        token = bytes(buf).decode("ascii")
        if is_float:
            return float(token)
        return int(token)

    def parse_string(self):
        # 入口时开引号已被 parse_value 消费
        s = self.s
        out = bytearray()
        while True:
            c = s.get()
            if c < 0:
                self.error("unterminated string")
            if c == 0x22:          # 闭引号
                return out.decode("utf-8")
            if c == 0x5C:          # 反斜杠转义
                e = s.get()
                if e == 0x22:
                    out.append(0x22)
                elif e == 0x5C:
                    out.append(0x5C)
                elif e == 0x2F:
                    out.append(0x2F)
                elif e == 0x62:
                    out.append(0x08)
                elif e == 0x66:
                    out.append(0x0C)
                elif e == 0x6E:
                    out.append(0x0A)
                elif e == 0x72:
                    out.append(0x0D)
                elif e == 0x74:
                    out.append(0x09)
                elif e == 0x75:    # \uXXXX
                    cp = self._hex4()
                    # 代理对（emoji 等）；本游戏文本用不到，但保持完整
                    if 0xD800 <= cp <= 0xDBFF:
                        c1 = s.get()
                        c2 = s.get()
                        if c1 == 0x5C and c2 == 0x75:
                            lo = self._hex4()
                            cp = 0x10000 + ((cp - 0xD800) << 10) \
                                + (lo - 0xDC00)
                        else:
                            self.error("bad surrogate pair")
                    _encode_utf8(cp, out)
                else:
                    self.error("bad escape char")
            else:
                # UTF-8 原始字节（含中文）原样透传。
                # bytearray 扩容会留下旧块：每攒满 64 字节主动回收一次，
                # 把长字符串解析中途的最大连续分配压到约 128B。
                if (len(out) & 0x3F) == 0:
                    gc.collect()
                out.append(c)

    def _hex4(self):
        # 入口时 'u' 已被消费，接下来恰为 4 个十六进制位
        s = self.s
        cp = 0
        for _ in range(4):
            c = s.get()
            if 0x30 <= c <= 0x39:
                d = c - 0x30
            elif 0x41 <= c <= 0x46:
                d = c - 0x41 + 10
            elif 0x61 <= c <= 0x66:
                d = c - 0x61 + 10
            else:
                self.error("bad hex")
            cp = (cp << 4) | d
        return cp

    def parse_object(self):
        obj = {}
        s = self.s
        self.skip_ws()
        c = s.get()
        if c == 0x7D:
            return obj
        if c != 0x22:
            self.error("expected key string")
        while True:
            key = self.parse_string()
            self.skip_ws()
            if s.get() != 0x3A:
                self.error("expected ':'")
            obj[key] = self.parse_value()
            self.skip_ws()
            c = s.get()
            if c == 0x7D:
                # 对象闭合：回收整个对象构建期间的扩容旧块
                gc.collect()
                return obj
            if c != 0x2C:
                self.error("expected ',' or '}'")
            self.skip_ws()
            if s.get() != 0x22:
                self.error("expected key string")

    def parse_array(self):
        arr = []
        s = self.s
        self.skip_ws()
        c = s.get()
        if c == 0x5D:
            return arr
        if c < 0:
            self.error("unterminated array")
        s.unget(c)
        while True:
            arr.append(self.parse_value())
            self.skip_ws()
            c = s.get()
            if c == 0x5D:
                gc.collect()
                return arr
            if c != 0x2C:
                self.error("expected ',' or ']'")


def _parse_stream(stream):
    p = _Parser(stream)
    value = p.parse_value()
    return value


def stream_from(fp):
    """在二进制文件对象上建立流式读取器。"""
    return _Stream(fp=fp)


def parse_next(stream):
    """从流的当前位置解析一个 JSON 值（跳过前导空白）。

    返回 None 表示文件已结束。
    """
    # 先探测是否还有非空白字节
    p = _Parser(stream)
    p.skip_ws()
    c = stream.get()
    if c < 0:
        return None
    stream.unget(c)
    return p.parse_value()


def loadf(fp):
    """从二进制文件对象流式解析一个 JSON 值（设备端主入口）。"""
    return _parse_stream(_Stream(fp=fp))


def loads(text):
    """解析 JSON 文本/字节串（小文件与测试用）。"""
    if isinstance(text, str):
        data = text.encode("utf-8")
    else:
        data = text
    return _parse_stream(_Stream(data=data))



