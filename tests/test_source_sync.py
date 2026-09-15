"""test_source_sync.py —— 保证 Markdown 源表与生成的 JSON 始终一致。

工作流约定：人只改 参考资料/卡牌.md、参考资料/骰子.md，改完运行
`python3 tools/md_to_json.py` 重新生成 JSON。本测试直接调用转换函数，
一旦忘记重新生成（源表与 JSON 不一致）就会失败。
"""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_converter():
    """以文件路径方式加载 tools/md_to_json.py（tools 不是包）。"""
    spec = importlib.util.spec_from_file_location(
        "md_to_json", ROOT / "tools" / "md_to_json.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def converter():
    return _load_converter()


def test_cards_md_matches_json(converter):
    expected = converter.convert_cards(str(ROOT / "参考资料" / "卡牌.md"))
    actual = json.loads((ROOT / "src" / "zroad" / "data" / "cards.json").read_text(encoding="utf-8"))
    assert expected == actual, "cards.json 与 卡牌.md 不一致，请重新运行 md_to_json.py"


def test_dice_md_matches_json(converter):
    expected = converter.convert_dice(str(ROOT / "参考资料" / "骰子.md"))
    actual = json.loads((ROOT / "src" / "zroad" / "data" / "dice.json").read_text(encoding="utf-8"))
    assert expected == actual, "dice.json 与 骰子.md 不一致，请重新运行 md_to_json.py"
