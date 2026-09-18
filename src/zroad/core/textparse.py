"""textparse.py —— 卡牌事件文本解析所需的极简文本工具（零依赖、MicroPython 兼容）。

core/effects.py 原本用标准库 re 处理事件结算说明，但 MicroPython 的正则模块
（ure）是 CPython re 的子集（没有 finditer，非贪婪匹配支持也不稳定）。这些
文本模式都很简单，用纯字符串扫描即可实现，行为更可控，也彻底消除平台差异。

所有函数只处理 str，不 import 任何第三方或桌面专有模块。
"""

# 括号配对（开括号 → 对应的闭括号）
_OPEN_BRACKETS = "「（("
_CLOSE_BRACKETS = "」）)"
# 备注/句读截断用的闭括号集合（中文句号、两种闭括号）
_TAIL_STOPS = "。（("


def strip_notes(text):
    """剔除开发者备注，如“（注：整套卡牌中一共有 2 张地图）”。

    规则：从“（注：”“(注：”“（注:”“(注:”起，删除到其后出现的第一个
    闭括号（）或 )）为止；文本中可能出现多处，逐处剔除，其余内容原样保留。
    """
    result = text
    while True:
        start = _find_any(result, ("（注：", "（注:", "(注：", "(注:"))
        if start < 0:
            return result
        close = _find_any(result, ("）", ")"), start + 1)
        if close < 0:
            # 没有闭括号：把备注起始处之后的内容一并去掉。
            return result[:start]
        result = result[:start] + result[close + 1:]


def iter_brackets(text):
    """按出现顺序生成所有“「…」/（…）/(…)”括号中的内容（已去首尾空白）。

    与原正则 [「（(](.+?)[」）)] 行为一致：取最近的闭括号，内容非空才产出。
    """
    index = 0
    length = len(text)
    while index < length:
        open_pos = _find_any_char(text, _OPEN_BRACKETS, index)
        if open_pos < 0:
            return
        close_pos = _find_any_char(text, _CLOSE_BRACKETS, open_pos + 1)
        if close_pos < 0:
            return
        inner = text[open_pos + 1:close_pos].strip()
        if inner:
            yield inner
        index = close_pos + 1


def split_first(text, stop_chars):
    """在 text 中寻找 stop_chars 里任意字符第一次出现的位置，切成两段。

    未找到时第二段为空字符串。等价于 re.split(r"[字符集]", text, 1)。
    """
    pos = _find_any_char(text, stop_chars, 0)
    if pos < 0:
        return text, ""
    return text[:pos], text[pos + 1:]


def int_after(text, marker):
    """寻找 marker 子串，返回紧随其后的第一段连续数字对应的 int；找不到返回 None。

    例如 int_after("得分+6", "得分+") == 6。
    """
    pos = _find_any(text, (marker,))
    if pos < 0:
        return None
    return _read_int(text, pos + len(marker))


def signed_int_after(text, marker):
    """寻找 marker，跳过空白后读取“+N / -N”，返回 (符号, 数值)。

    符号为 "+" 或 "-"；找不到合法的“符号+数字”时返回 None。
    例如 signed_int_after("队伍人数 -2", "队伍人数") == ("-", 2)。
    """
    pos = _find_any(text, (marker,))
    if pos < 0:
        return None
    index = pos + len(marker)
    code_at = _code_at(text, index)
    while code_at == 32:   # ' '
        index += 1
        code_at = _code_at(text, index)
    if code_at < 0 or code_at not in (43, 45):   # '+' '-'
        return None
    sign = "+" if code_at == 43 else "-"
    number = _read_int(text, index + 1)
    if number is None:
        return None
    return sign, number


# ---------------- 内部小工具 ----------------
#
# 【MicroPython 内存红线】本模块扫描的是中文事件文本，绝不能用
# text[i] / for ch in text 逐字取字符：MicroPython 对 unicode 串
# 取下标/迭代会把每个首次出现的字符经 mp_obj_new_str_via_qstr
# 驻留成永久 qstr（qstr 池只增不减、扩容块要求连续），整局游戏
# 累计上千个不同汉字后必然 MemoryError。统一先 encode 成临时
# UTF-8 bytes（可被 GC 回收），之后全程只处理整数码点。

def _next_code(data, i):
    """从 bytes 的字节位置 i 读一个 UTF-8 字符，返回 (码点, 字节长度)。"""
    x = data[i]
    if x < 0x80:
        return x, 1
    if x < 0xE0:
        return ((x & 0x1F) << 6) | (data[i + 1] & 0x3F), 2
    if x < 0xF0:
        return (((x & 0x0F) << 12)
                | ((data[i + 1] & 0x3F) << 6)
                | (data[i + 2] & 0x3F)), 3
    return ((((x & 0x07) << 18)
             | ((data[i + 1] & 0x3F) << 12)
             | ((data[i + 2] & 0x3F) << 6)
             | (data[i + 3] & 0x3F))), 4


def _byte_offset(data, char_index):
    """把字符下标换算成 UTF-8 字节偏移（续字节 10xxxxxx 不计字符）。"""
    n = len(data)
    i = 0
    ci = 0
    while i < n and ci < char_index:
        x = data[i]
        if x < 0x80:
            i += 1
        elif x < 0xE0:
            i += 2
        elif x < 0xF0:
            i += 3
        else:
            i += 4
        ci += 1
    return i


def _char_index(data, byte_pos):
    """字节偏移 → 字符下标：字节位置前的 UTF-8 续字节数不计字符。"""
    count = 0
    for j in range(byte_pos):
        if (data[j] & 0xC0) == 0x80:
            count += 1
    return byte_pos - count


def _code_at(text, char_index):
    """取 text 第 char_index 个字符的整数码点；越界返回 -1（不产生
    单字符 str，避免 qstr 驻留）。"""
    n_chars = len(text)
    if char_index < 0 or char_index >= n_chars:
        return -1
    data = text.encode("utf-8")
    i = _byte_offset(data, char_index)
    code, _ = _next_code(data, i)
    return code


def _read_int(text, start):
    """从字符下标 start 起读取连续 ASCII 数字；没有数字返回 None。"""
    data = text.encode("utf-8")
    i = _byte_offset(data, start)
    n = len(data)
    begin = i
    while i < n and 48 <= data[i] <= 57:   # '0'..'9'
        i += 1
    if i == begin:
        return None
    # 数字是 ASCII，字节切片与字符切片等价
    return int(data[begin:i].decode("utf-8"))


def _find_any(text, markers, start=0):
    """返回 markers 中任一子串最早出现的“字符位置”；都没有返回 -1。

    设备端注意两点：
      1. 不用 unicode 的 text.find(marker, start)：MicroPython 对双字节
         中文串的查找会申请约等于文本长度的临时缓冲（长事件文本可达
         1.2KB），碎片化小堆上会成为压垮内存的最后一根稻草；
      2. 不逐字取 text[i]：会把汉字驻留成永久 qstr（见模块头说明）。
    改为在 UTF-8 bytes 上用 bytes.find（C 层 memcmp，不产生大缓冲，
    UTF-8 自同步不会跨字符误匹配），再把字节偏移换算回字符下标。
    """
    data = text.encode("utf-8")
    start_byte = _byte_offset(data, start)
    best = -1
    for marker in markers:
        mb = marker.encode("utf-8")
        # UTF-8 自同步：续字节都是 10xxxxxx，而首字节不是，子串匹配
        # 不可能从字符中间开始，无需再做边界校验（旧实现误判“匹配点
        # 前一个字节是续字节”为非法，实际上每个汉字末尾本来就是续字节，
        # 会把所有中文匹配错误跳过）。
        pos = data.find(mb, start_byte)
        if pos >= 0 and (best < 0 or pos < best):
            best = pos
    if best < 0:
        return -1
    return _char_index(data, best)


def _find_any_char(text, chars, start=0):
    """返回 chars 中任一字符最早出现的“字符位置”；都没有返回 -1。

    在 UTF-8 bytes 上逐码点扫描（原因同 _find_any：既避开 MicroPython
    长中文串 find 的临时缓冲，也避开逐字取字符的 qstr 永久驻留）。
    """
    wanted = set()
    cdata = chars.encode("utf-8")
    j = 0
    while j < len(cdata):
        code, step = _next_code(cdata, j)
        wanted.add(code)
        j += step
    data = text.encode("utf-8")
    n = len(data)
    i = 0
    ci = 0
    while i < n:
        code, step = _next_code(data, i)
        if ci >= start and code in wanted:
            return ci
        i += step
        ci += 1
    return -1
