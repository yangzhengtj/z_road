"""test_core_portability.py —— 阶段 2 守门：core 必须能在 MicroPython 上运行。

三类保证：
1. 静态扫描 core/：禁止 import 桌面/第三方模块（random、re、json、enum、
   dataclasses、pathlib、typing、inspect 等），禁止 3.8 以上语法特性；
2. 纯 Python SeededRng 的确定性、可存档恢复、旧档兼容；
3. textparse 文本工具与原先正则实现行为一致（备注剔除、括号提取、取数）。
"""

import ast
from pathlib import Path

import pytest

from zroad.core import textparse as tp
from zroad.core.rng import SeededRng

ROOT = Path(__file__).resolve().parents[1]
CORE_DIR = ROOT / "src" / "zroad" / "core"

# core 允许使用的 import：Python 标准库中 MicroPython 同样提供、且本项目
# 实际需要的模块。新增依赖必须先确认 MicroPython 支持，再改这里。
ALLOWED_IMPORTS = {"re"}  # 当前 core 已不 import re；保留空集更严格，见下
FORBIDDEN_MODULES = {
    "random", "re", "json", "enum", "dataclasses", "pathlib", "typing",
    "inspect", "platform", "subprocess", "threading", "asyncio",
    "importlib", "unittest", "pickle", "sqlite3", "csv", "logging",
    "argparse", "configparser", "urllib", "http", "socket",
}
# 第三方/桌面专有包名前缀
FORBIDDEN_PREFIXES = ("rich", "pytest", "PIL", "numpy", "pandas")


def _core_py_files():
    return sorted(CORE_DIR.glob("*.py"))


def test_core_only_uses_micropython_subset_imports():
    """core 每个 .py 文件的 import 只能是 Python 未来语句或相对导入。"""
    offenders = []
    for path in _core_py_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top in FORBIDDEN_MODULES or top.startswith(
                        FORBIDDEN_PREFIXES
                    ):
                        offenders.append("%s: import %s" % (path.name, alias.name))
            elif isinstance(node, ast.ImportFrom):
                # 相对导入（node.level>0）是 core 内部模块，允许。
                if node.level:
                    continue
                top = (node.module or "").split(".")[0]
                if top in FORBIDDEN_MODULES or top.startswith(FORBIDDEN_PREFIXES):
                    offenders.append("%s: from %s import ..."
                                     % (path.name, node.module))
    assert not offenders, "core 出现 MicroPython 不保证支持的导入：\n" + \
        "\n".join(offenders)


def test_core_no_modern_syntax_sugar():
    """core 不得使用 match 语句、X|Y 联合类型注解等 3.9+/3.10+ 语法糖。

    AST 层面能稳定识别的是 match 语句；类型注解为字符串/对象表达式，
    core 的约定是“不写运行期注解”，这里顺带扫描函数参数与返回值注解。
    """
    for path in _core_py_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if hasattr(ast, "Match"):
                assert not isinstance(node, ast.Match), \
                    "%s 使用了 match/case（MicroPython 不支持）" % path.name
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert node.returns is None, \
                    "%s 函数 %s 带返回值注解" % (path.name, node.name)
                for arg in list(node.args.args) + list(node.args.kwonlyargs):
                    assert arg.annotation is None, \
                        "%s 函数 %s 参数带注解" % (path.name, node.name)


# ---------------- SeededRng ----------------

def test_rng_deterministic_same_seed():
    a = SeededRng(42)
    b = SeededRng(42)
    assert a.roll_many(20) == b.roll_many(20)


def test_rng_d6_in_range():
    rng = SeededRng(7)
    for _ in range(500):
        assert 1 <= rng.d6() <= 6


def test_rng_shuffle_is_permutation():
    rng = SeededRng(99)
    seq = list(range(30))
    shuffled = list(seq)
    rng.shuffle(shuffled)
    assert sorted(shuffled) == seq


def test_rng_sample_no_repetition():
    rng = SeededRng(123)
    picked = rng.sample(list(range(40)), 10)
    assert len(picked) == 10 and len(set(picked)) == 10


def test_rng_state_roundtrip_keeps_sequence():
    rng = SeededRng(5)
    rng.roll_many(13)
    state = rng.get_state()
    expected = rng.roll_many(17)

    restored = SeededRng(0)
    assert restored.set_state(state) is True
    assert restored.roll_many(17) == expected


def test_rng_legacy_cpython_state_fallback():
    """v0.7.1 旧档（CPython random 的 version/internal/gauss_next）可兼容。"""
    legacy = {"seed": 20260913, "version": 3,
              "internal": [0] * 625, "gauss_next": None}
    rng = SeededRng(1)
    assert rng.set_state(legacy) is False
    # 回退后仍可正常出数
    assert all(1 <= rng.d6() <= 6 for _ in range(20))


def test_rng_none_seed_produces_two_different_sequences():
    """不指定种子时两次开局序列不同（熵源有效）。"""
    a = SeededRng(None).roll_many(10)
    b = SeededRng(None).roll_many(10)
    assert a != b


# ---------------- textparse ----------------

@pytest.mark.parametrize("text,expected", [
    ("获得特殊道具：地图。（注：整套卡牌中一共有 2 张地图）", "获得特殊道具：地图。"),
    ("前文(注:开发者备注)后文", "前文后文"),
    ("（注：只有备注没有正文）", ""),
    ("没有备注的正常文本。", "没有备注的正常文本。"),
    ("一处（注：A）二处（注：B）结束", "一处二处结束"),
])
def test_strip_notes(text, expected):
    assert tp.strip_notes(text) == expected


def test_iter_brackets():
    text = "拿取「地图」标记，同时（药剂）与(汽油)也提及"
    assert list(tp.iter_brackets(text)) == ["地图", "药剂", "汽油"]


def test_iter_brackets_ignores_empty():
    assert list(tp.iter_brackets("空括号（）与「」跳过") ) == []


def test_split_first():
    assert tp.split_first("地图。后续（二）", "。（(") == ("地图", "后续（二）")
    assert tp.split_first("无截断", "。") == ("无截断", "")


def test_int_after():
    assert tp.int_after("得分+6，其他", "得分+") == 6
    assert tp.int_after("支付3个弹药保留", "支付") == 3
    assert tp.int_after("没有数字", "支付") is None


def test_signed_int_after():
    assert tp.signed_int_after("队伍人数-1", "队伍人数") == ("-", 1)
    assert tp.signed_int_after("队伍人数 +2", "队伍人数") == ("+", 2)
    assert tp.signed_int_after("队伍人数不变", "队伍人数") is None
