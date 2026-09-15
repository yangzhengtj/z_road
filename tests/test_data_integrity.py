"""test_data_integrity.py —— 数据层完整性与规则口径锚点测试。

这些测试把“规则口径”固化成可执行断言：一旦源表或 config 被误改、与约定不符，
测试立即失败，避免错误数据流入引擎。
"""

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


@pytest.fixture(scope="module")
def cards():
    return json.loads((DATA / "cards.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def dice():
    return json.loads((DATA / "dice.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def config():
    return json.loads((DATA / "config.json").read_text(encoding="utf-8"))


def test_card_counts_by_stage(cards):
    """牌池 60 张：I 16、II 22、III 22。"""
    counts = {1: 0, 2: 0, 3: 0}
    for card in cards:
        counts[card["stage"]] += 1
    assert counts == {1: 16, 2: 22, 3: 22}
    assert len(cards) == 60


def test_card_ids_unique_and_ordered(cards):
    """id 全局唯一；每阶段 order_in_stage 从 1 连续编号，且 id 前缀与 stage 一致。"""
    ids = [c["id"] for c in cards]
    assert len(ids) == len(set(ids))
    roman = {1: "I", 2: "II", 3: "III"}
    by_stage = {1: [], 2: [], 3: []}
    for card in cards:
        by_stage[card["stage"]].append(card)
    for stage, group in by_stage.items():
        orders = sorted(c["order_in_stage"] for c in group)
        assert orders == list(range(1, len(group) + 1))
        for card in group:
            assert card["id"].startswith(roman[stage] + "-")


def test_fields_non_negative(cards):
    """资源、丧尸数/等级、得分都不允许负数。"""
    for card in cards:
        assert card["scavenge"]["ammo"] >= 0
        assert card["scavenge"]["gas"] >= 0
        assert card["scavenge"]["meds"] >= 0
        assert card["zombies"]["count"] >= 0
        assert card["zombies"]["level"] >= 0
        assert card["score"] >= 0


def test_stage_content_anchors(cards):
    """内容统计锚点（源表当前版本）：战斗牌数/丧尸总数/资源产出/得分合计。"""
    anchors = {
        1: {"battles": 4, "zombies": 15, "ammo": 17, "gas": 8, "meds": 8, "score": 10},
        2: {"battles": 10, "zombies": 50, "ammo": 14, "gas": 9, "meds": 11, "score": 12},
        3: {"battles": 19, "zombies": 95, "ammo": 7, "gas": 1, "meds": 12, "score": 9},
    }
    for stage, expect in anchors.items():
        group = [c for c in cards if c["stage"] == stage]
        actual = {
            "battles": sum(1 for c in group if c["zombies"]["count"] > 0),
            "zombies": sum(c["zombies"]["count"] for c in group),
            "ammo": sum(c["scavenge"]["ammo"] for c in group),
            "gas": sum(c["scavenge"]["gas"] for c in group),
            "meds": sum(c["scavenge"]["meds"] for c in group),
            "score": sum(c["score"] for c in group),
        }
        assert actual == expect, "阶段 %s 锚点不符：%s != %s" % (stage, actual, expect)


def test_dice_faces(dice):
    """两种骰子各 6 面，面值 1..6，骰面名称集合符合规则。"""
    for kind in ("normal", "enhanced"):
        faces = sorted(f["face"] for f in dice[kind])
        assert faces == [1, 2, 3, 4, 5, 6]
    normal_names = {f["name"] for f in dice["normal"]}
    enhanced_names = {f["name"] for f in dice["enhanced"]}
    assert normal_names == {"空白", "咬伤", "伺机而动", "击杀", "额外击杀"}
    # 强化骰没有“空白”，但有“死亡”
    assert enhanced_names == {"咬伤", "死亡", "伺机而动", "击杀", "额外击杀"}


def test_config_setup_arithmetic(config):
    """登场数 = 牌池 - 剔除；每阶段登场数 = 该阶段轮数 × 每轮 6 张。"""
    setup = config["setup"]
    pools = setup["stage_pool_sizes"]
    removed = setup["remove_per_stage"]
    active = setup["active_deck_sizes"]
    assert [p - removed for p in pools] == active
    per_round = setup["solo_cards_per_round"]
    assert [r * per_round for r in setup["stage_rounds"]] == active
    assert sum(setup["stage_rounds"]) == setup["total_rounds"]


def test_config_solo_paths(config):
    """两档难度的三路径明暗都为 暗暗/明暗/明明，奖惩数值符合规则定义。"""
    solo = config["solo_paths"]
    assert solo["default_difficulty"] == "easy"
    assert set(solo["difficulties"]) == {"easy", "hard"}
    for diff in ("easy", "hard"):
        paths = solo["difficulties"][diff]["paths"]
        assert len(paths) == 3
        assert [(p["first_card"], p["second_card"]) for p in paths] == \
            [("down", "down"), ("up", "down"), ("up", "up")]
    easy = solo["difficulties"]["easy"]["paths"]
    hard = solo["difficulties"]["hard"]["paths"]
    # 简单（原规则）：路径1 +2，路径2 无，路径3 -2
    assert [p["bonus_any_resources"] for p in easy] == [2, 0, 0]
    assert [p["cost_any_resources"] for p in easy] == [0, 0, 2]
    # 困难：路径1 无奖惩，路径2 -1，路径3 -2
    assert [p["bonus_any_resources"] for p in hard] == [0, 0, 0]
    assert [p["cost_any_resources"] for p in hard] == [0, 1, 2]


def test_config_rating_bands_cover_all_scores(config):
    """评级区间从 0 起连续覆盖、无重叠无缺口。"""
    bands = config["scoring"]["ratings"]
    assert bands[0]["min"] == 0
    for prev, cur in zip(bands, bands[1:]):
        assert prev["max"] + 1 == cur["min"]
