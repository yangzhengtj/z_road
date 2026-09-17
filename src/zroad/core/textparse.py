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
    pos = text.find(marker)
    if pos < 0:
        return None
    return _read_int(text, pos + len(marker))


def signed_int_after(text, marker):
    """寻找 marker，跳过空白后读取“+N / -N”，返回 (符号, 数值)。

    符号为 "+" 或 "-"；找不到合法的“符号+数字”时返回 None。
    例如 signed_int_after("队伍人数 -2", "队伍人数") == ("-", 2)。
    """
    pos = text.find(marker)
    if pos < 0:
        return None
    index = pos + len(marker)
    while index < len(text) and text[index] == " ":
        index += 1
    if index >= len(text) or text[index] not in "+-":
        return None
    sign = text[index]
    number = _read_int(text, index + 1)
    if number is None:
        return None
    return sign, number


# ---------------- 内部小工具 ----------------

def _read_int(text, start):
    """从 start 起读取连续 ASCII 数字；没有数字返回 None。"""
    end = start
    while end < len(text) and "0" <= text[end] <= "9":
        end += 1
    if end == start:
        return None
    return int(text[start:end])


def _find_any(text, markers, start=0):
    """返回 markers 中任一子串最早出现的位置；都没有返回 -1。"""
    best = -1
    for marker in markers:
        pos = text.find(marker, start)
        if pos >= 0 and (best < 0 or pos < best):
            best = pos
    return best


def _find_any_char(text, chars, start=0):
    """返回 chars 中任一字符最早出现的位置；都没有返回 -1。"""
    best = -1
    for char in chars:
        pos = text.find(char, start)
        if pos >= 0 and (best < 0 or pos < best):
            best = pos
    return best
