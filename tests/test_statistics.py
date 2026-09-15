"""test_statistics.py —— M6：局后统计汇总（分阶段/资源账本/骰面/回看）。"""

import json
from pathlib import Path

import pytest

from zroad.core import statistics as stats_mod
from zroad.core.model import GameState, PlayerState, Resources, MODE_SOLO
from zroad.headless.scripted import play_full_game

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def cards():
    return json.loads((ROOT / "data" / "cards.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def config():
    return json.loads((ROOT / "data" / "config.json").read_text(encoding="utf-8"))


def _finished_engine(cards, config, seed=3, difficulty="easy"):
    return play_full_game(cards, config, seed=seed, difficulty=difficulty)


# ---------- 1. 整局汇总结构与账面恒等式 ----------

def test_summary_structure(cards, config):
    """整局结束后汇总包含全部五个板块，回看恰好 8 轮。"""
    eng = _finished_engine(cards, config)
    summary = eng.statistics_summary()
    assert set(summary) == {"overview", "by_stage", "resource_ledger",
                           "dice", "rounds"}
    assert len(summary["rounds"]) == 8
    # 分阶段三行 + 合计行
    assert [r["stage"] for r in summary["by_stage"]["rows"]] == [1, 2, 3]


def test_resource_ledger_balanced(cards, config):
    """资源账面恒等式：终局持有 = 初始 + 获得 - 支出（三种资源全部成立）。"""
    eng = _finished_engine(cards, config)
    for row in eng.statistics_summary()["resource_ledger"]:
        assert row["balanced"] is True, row
        assert row["final"] == row["initial"] + row["gained"] - row["spent"]


def test_by_stage_totals_match_overview(cards, config):
    """分阶段合计必须等于总览（多粒种子都核对，防止漏记账）。"""
    for seed in (1, 3, 9, 21):
        eng = _finished_engine(cards, config, seed=seed)
        summary = eng.statistics_summary()
        total = summary["by_stage"]["total"]
        ov = summary["overview"]
        for key in ("combats", "zombies_killed", "survivors_lost", "fled"):
            assert total[key] == ov[key], (seed, key)


def test_dice_distribution_counts(cards, config):
    """骰面分布：各骰面之和等于小计，两种骰小计之和等于总计。"""
    eng = _finished_engine(cards, config)
    dice = eng.statistics_summary()["dice"]
    for kind in ("normal", "enhanced"):
        block = dice[kind]
        assert sum(f["count"] for f in block["faces"]) == block["total"]
    assert dice["normal"]["total"] + dice["enhanced"]["total"] == dice["total"]
    # 打过的整局必然掷过骰子
    assert dice["total"] > 0


# ---------- 2. 纯函数单元行为 ----------

def test_overview_and_stage_empty():
    """空统计：总览与分阶段全为 0，不抛异常。"""
    empty = GameState._empty_stats()
    ov = stats_mod.overview(empty)
    assert ov == {"combats": 0, "zombies_killed": 0,
                  "survivors_lost": 0, "fled": 0}
    stage = stats_mod.by_stage(empty)
    assert stage["total"] == {"stage": 0, "zombies_killed": 0,
                              "survivors_lost": 0, "combats": 0, "fled": 0}


def test_resource_ledger_net():
    """手工构造收支，净变化=获得-支出；未给初始/终局时不做 balanced 校验。"""
    state = GameState(mode=MODE_SOLO, seed=1)
    state.stats["resources_gained"] = {"ammo": 5, "gas": 0, "meds": 2}
    state.stats["resources_spent"] = {"ammo": 3, "gas": 1, "meds": 2}
    state.player = PlayerState(survivors=5, resources=Resources(0, 0, 0))
    ledger = stats_mod.resource_ledger(state.stats)
    by_key = {r["key"]: r for r in ledger}
    assert by_key["ammo"]["net"] == 2
    assert by_key["gas"]["net"] == -1
    assert by_key["meds"]["net"] == 0
    assert "balanced" not in by_key["ammo"]


def test_replay_rounds_enriches_cards(cards, config):
    """回看每轮带阶段/路径/两张遭遇卡（id+场景），弃牌 4 张。"""
    eng = _finished_engine(cards, config)
    summary = eng.statistics_summary()
    for r in summary["rounds"]:
        assert r["chosen_path"] in (1, 2, 3)
        assert len(r["chosen_cards"]) == 2
        assert len(r["discarded_cards"]) == 4
        for card in r["chosen_cards"]:
            assert card["scene"]  # 场景文本非空


def test_old_save_stats_forward_compat(cards):
    """旧档没有 by_stage 桶时，from_dict 自动补零，不影响后续记账。"""
    state_dict = {
        "mode": MODE_SOLO, "seed": 1, "phase": "round_end", "round_no": 3,
        "stage_decks": {"1": [], "2": [], "3": []},
        "removed_cards": {"1": [], "2": [], "3": []},
        "player": {"survivors": 5, "resources": {"ammo": 4, "gas": 4, "meds": 4},
                   "special_items": [], "bonus_score": 0, "won_card_ids": []},
        "current_options": [], "encounter_queue": [],
        "pending_combat": None,
        "stats": {"zombies_killed": 1, "survivors_lost": 0, "combats": 1,
                  "fled": 0,
                  "resources_gained": {"ammo": 0, "gas": 0, "meds": 0},
                  "resources_spent": {"ammo": 0, "gas": 0, "meds": 0},
                  "dice_faces": {"normal": {}, "enhanced": {}}},
        "round_log": [],
    }
    state = GameState.from_dict(state_dict)
    assert state.stats["by_stage"]["2"]["combats"] == 0
    # 旧档骰面空桶也能正常汇总
    summary = stats_mod.summarize(state, {c["id"]: c for c in cards})
    assert summary["overview"]["zombies_killed"] == 1
