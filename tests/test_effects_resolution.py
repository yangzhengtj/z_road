"""test_effects_resolution.py —— M3：事件解析全覆盖与结算测试。

三部分：
  1. 60 张卡的 event_raw 必须全部解析为已知效果类型（显式期望表，新增卡漏网即失败）；
  2. 各立即效果在 PlayerState 上的结算正确（道具链、支付抉择、掷骰事件）；
  3. 引擎层：拾荒入账、战斗牌挂起、III-17 全屏秒杀、整局 8 轮空跑不崩。
"""

import json
from pathlib import Path

import pytest

from zroad.core.model import PlayerState, Resources, GameState, EngineError
from zroad.core.rng import SeededRng
from zroad.core.engine import Engine
from zroad.core import effects as fx
from zroad.core.constants import (PHASE_ENCOUNTER, PHASE_ROUND_END,
                                  PHASE_FINISHED, ITEM_ZEALOT)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def cards():
    return json.loads((ROOT / "data" / "cards.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def config():
    return json.loads((ROOT / "data" / "config.json").read_text(encoding="utf-8"))


# ---------- 1. 60 张卡事件类型全覆盖（显式期望表） ----------
EXPECTED_KIND = {
    # 阶段 I
    "I-1": fx.KIND_NONE, "I-2": fx.KIND_NONE, "I-3": fx.KIND_NONE,
    "I-4": fx.KIND_GAIN_SURVIVORS,
    "I-5": fx.KIND_GAIN_ITEM, "I-6": fx.KIND_GAIN_ITEM, "I-7": fx.KIND_GAIN_ITEM,
    "I-8": fx.KIND_GAIN_ITEM, "I-9": fx.KIND_GAIN_ITEM,
    "I-10": fx.KIND_GAIN_ITEM, "I-11": fx.KIND_GAIN_ITEM,
    "I-12": fx.KIND_LOSE_RESOURCE_CHOICE,
    "I-13": fx.KIND_NONE, "I-14": fx.KIND_NONE, "I-15": fx.KIND_NONE,
    "I-16": fx.KIND_GAIN_ITEM,
    # 阶段 II
    "II-1": fx.KIND_NONE, "II-2": fx.KIND_NONE, "II-3": fx.KIND_NONE,
    "II-4": fx.KIND_ITEM_MAP_SCORE, "II-5": fx.KIND_ITEM_ARMOR,
    "II-6": fx.KIND_LOSE_SURVIVORS,
    "II-7": fx.KIND_PAY_GAS_OR_LOSE, "II-8": fx.KIND_LOSE_RESOURCE_CHOICE,
    "II-9": fx.KIND_RUSSIAN_ROULETTE, "II-10": fx.KIND_GAIN_SURVIVORS,
    "II-11": fx.KIND_ITEM_SNIPER_PASS, "II-12": fx.KIND_PAY_GAS_OR_LOSE,
    "II-13": fx.KIND_NONE, "II-14": fx.KIND_MOD_RANGED_BITE_ADDS,
    "II-15": fx.KIND_ITEM_WOUND,
    "II-16": fx.KIND_NONE, "II-17": fx.KIND_NONE, "II-18": fx.KIND_NONE,
    "II-19": fx.KIND_NONE, "II-20": fx.KIND_NONE,
    "II-21": fx.KIND_MOD_RANGED_BITE_ADDS, "II-22": fx.KIND_NONE,
    # 阶段 III
    "III-1": fx.KIND_NONE, "III-2": fx.KIND_NONE,
    "III-3": fx.KIND_PAY_GAS_OR_LOSE, "III-4": fx.KIND_PAY_GAS_OR_LOSE,
    "III-5": fx.KIND_ITEM_GAS, "III-6": fx.KIND_ITEM_GAS,
    "III-7": fx.KIND_MOD_RANGED_BITE_ADDS, "III-8": fx.KIND_MOD_RANGED_BITE_ADDS,
    "III-9": fx.KIND_ITEM_ZEALOT_UPKEEP,
    "III-10": fx.KIND_MOD_NO_MEDS, "III-11": fx.KIND_MOD_NO_MEDS,
    "III-12": fx.KIND_NONE,
    "III-13": fx.KIND_RESCUE_SHOT, "III-14": fx.KIND_RESCUE_SHOT,
    "III-15": fx.KIND_RESCUE_SHOT, "III-16": fx.KIND_NONE,
    "III-17": fx.KIND_MOD_ZEALOT_NUKE, "III-18": fx.KIND_MOD_RANGED_BITE_ADDS,
    "III-19": fx.KIND_MOD_NO_FLEE, "III-20": fx.KIND_MOD_NO_FLEE,
    "III-21": fx.KIND_NONE, "III-22": fx.KIND_NONE,
}

COMBAT_TIMING_KINDS = {fx.KIND_MOD_RANGED_BITE_ADDS, fx.KIND_MOD_NO_MEDS,
                       fx.KIND_MOD_NO_FLEE, fx.KIND_MOD_ZEALOT_NUKE}


def test_all_cards_effects_covered(cards):
    """每张卡都能解析，且类型与显式期望表一致；数量 60 一张不少。"""
    parsed = fx.parse_all(cards)
    assert len(parsed) == 60
    assert set(parsed.keys()) == set(EXPECTED_KIND.keys())
    for card in cards:
        spec = parsed[card["id"]]
        assert spec["kind"] == EXPECTED_KIND[card["id"]], \
            "%s 解析为 %s，期望 %s" % (card["id"], spec["kind"], EXPECTED_KIND[card["id"]])
        expected_timing = (fx.TIMING_COMBAT if spec["kind"] in COMBAT_TIMING_KINDS
                           else fx.TIMING_IMMEDIATE)
        assert spec["timing"] == expected_timing


def test_gain_item_names_are_special_items(cards):
    """所有“获得道具”卡给出的道具名都在六种特殊道具清单内。"""
    for card in cards:
        spec = fx.parse_effect(card)
        if spec["kind"] == fx.KIND_GAIN_ITEM:
            from zroad.core.constants import SPECIAL_ITEMS
            assert spec["item"] in SPECIAL_ITEMS, card["id"]


# ---------- 2. 立即效果结算单元测试 ----------
def _player(survivors=5, ammo=4, gas=4, meds=4, items=None):
    p = PlayerState(survivors=survivors,
                    resources=Resources(ammo, gas, meds))
    for item in (items or []):
        p.gain_item(item)
    return p


def test_gain_survivors():
    p = _player()
    spec = {"kind": fx.KIND_GAIN_SURVIVORS, "timing": fx.TIMING_IMMEDIATE, "amount": 1}
    fx.resolve_immediate(spec, p, SeededRng(1))
    assert p.survivors == 6


def test_lose_survivors():
    p = _player(survivors=5)
    stats = {"survivors_lost": 0}
    spec = {"kind": fx.KIND_LOSE_SURVIVORS, "timing": fx.TIMING_IMMEDIATE, "amount": 1}
    result = fx.resolve_immediate(spec, p, SeededRng(1), stats)
    assert p.survivors == 4 and result["survivors_delta"] == -1
    assert stats["survivors_lost"] == 1


def test_gain_item():
    p = _player()
    spec = {"kind": fx.KIND_GAIN_ITEM, "timing": fx.TIMING_IMMEDIATE, "item": "地图"}
    fx.resolve_immediate(spec, p, SeededRng(1))
    assert p.has_item("地图")


def test_vehicle_armor_gain_and_combo():
    """II-5 车辆铠甲：无条件拿到；与校车是否同时持有只影响提示文案，标记都保留。"""
    from zroad.core.constants import ITEM_BUS, ITEM_VEHICLE_ARMOR
    # 此前没有校车：铠甲照拿，提示尚未成套
    p1 = _player()
    spec = {"kind": fx.KIND_ITEM_ARMOR, "timing": fx.TIMING_IMMEDIATE,
            "item": ITEM_VEHICLE_ARMOR}
    r1 = fx.resolve_immediate(spec, p1, SeededRng(1))
    assert p1.has_item(ITEM_VEHICLE_ARMOR) and not p1.has_item(ITEM_BUS)
    assert "尚无校车" in "".join(r1["logs"])
    # 此前已有校车：拿到铠甲即两件套成型
    p2 = _player(items=[ITEM_BUS])
    r2 = fx.resolve_immediate(spec, p2, SeededRng(1))
    assert p2.has_item(ITEM_VEHICLE_ARMOR) and p2.has_item(ITEM_BUS)
    assert "普通骰" in "".join(r2["logs"])


def test_lose_resource_choice():
    p = _player(ammo=3, gas=0, meds=0)
    spec = {"kind": fx.KIND_LOSE_RESOURCE_CHOICE, "timing": fx.TIMING_IMMEDIATE, "amount": 1}
    # 未给决策应报错
    with pytest.raises(EngineError):
        fx.resolve_immediate(spec, p, SeededRng(1))
    # 只能选还有库存的资源
    needed = fx.decision_needed(spec, p)
    assert needed["available"] == ["ammo"]
    fx.resolve_immediate(spec, p, SeededRng(1), decision={"resource": "ammo"})
    assert p.resources.ammo == 2
    # 全空时无需决策、自动无事
    p2 = _player(ammo=0, gas=0, meds=0)
    assert fx.decision_needed(spec, p2) is None
    fx.resolve_immediate(spec, p2, SeededRng(1))
    assert p2.resources.total() == 0


def test_map_score():
    with_map = _player(items=["地图"])
    spec = {"kind": fx.KIND_ITEM_MAP_SCORE, "timing": fx.TIMING_IMMEDIATE,
            "item": "地图", "score": 6}
    fx.resolve_immediate(spec, with_map, SeededRng(1))
    assert with_map.bonus_score == 6 and not with_map.has_item("地图")
    without = _player()
    fx.resolve_immediate(spec, without, SeededRng(1))
    assert without.bonus_score == 0


def test_map_score_stacks_per_map():
    """累计 2 张地图时每张 +6（共 +12）并全部消耗。"""
    p = _player(items=["地图", "地图"])
    spec = {"kind": fx.KIND_ITEM_MAP_SCORE, "timing": fx.TIMING_IMMEDIATE,
            "item": "地图", "score": 6}
    result = fx.resolve_immediate(spec, p, SeededRng(1))
    assert p.bonus_score == 12 and p.count_item("地图") == 0
    assert result["bonus_score"] == 12


def test_sniper_pass():
    with_gun = _player(items=["狙击枪"])
    spec = {"kind": fx.KIND_ITEM_SNIPER_PASS, "timing": fx.TIMING_IMMEDIATE,
            "item": "狙击枪", "lose_if_missing": 2}
    fx.resolve_immediate(spec, with_gun, SeededRng(1))
    assert with_gun.survivors == 5 and not with_gun.has_item("狙击枪")
    no_gun = _player()
    fx.resolve_immediate(spec, no_gun, SeededRng(1))
    assert no_gun.survivors == 3  # 缺道具 -2


def test_gas_item_per_marker_not_consumed():
    """方案 B：每持有 1 个毒气标记损 1 人，标记不消耗，III-5/III-6 各自独立触发。"""
    p = _player(items=["毒气", "毒气"])
    spec = {"kind": fx.KIND_ITEM_GAS, "timing": fx.TIMING_IMMEDIATE,
            "item": "毒气", "lose_per": 1}
    fx.resolve_immediate(spec, p, SeededRng(1))
    assert p.survivors == 3 and p.count_item("毒气") == 2  # 扣 2、标记保留
    fx.resolve_immediate(spec, p, SeededRng(1))
    assert p.survivors == 1 and p.count_item("毒气") == 2  # 再次独立扣 2
    # 没有毒气则无事
    clean = _player()
    fx.resolve_immediate(spec, clean, SeededRng(1))
    assert clean.survivors == 5


def test_zealot_upkeep():
    spec = {"kind": fx.KIND_ITEM_ZEALOT_UPKEEP, "timing": fx.TIMING_IMMEDIATE,
            "item": ITEM_ZEALOT, "ammo": 3}
    keep = _player(ammo=4, items=[ITEM_ZEALOT])
    fx.resolve_immediate(spec, keep, SeededRng(1), decision={"keep": True})
    assert keep.has_item(ITEM_ZEALOT) and keep.resources.ammo == 1
    abandon = _player(ammo=4, items=[ITEM_ZEALOT])
    fx.resolve_immediate(spec, abandon, SeededRng(1), decision={"keep": False})
    assert not abandon.has_item(ITEM_ZEALOT) and abandon.resources.ammo == 4
    poor = _player(ammo=2, items=[ITEM_ZEALOT])
    with pytest.raises(EngineError):
        fx.resolve_immediate(spec, poor, SeededRng(1), decision={"keep": True})


def test_pay_gas_or_lose():
    spec = {"kind": fx.KIND_PAY_GAS_OR_LOSE, "timing": fx.TIMING_IMMEDIATE,
            "gas": 2, "lose_per_missing": 1}
    full = _player(gas=3)
    fx.resolve_immediate(spec, full, SeededRng(1), decision={"gas_pay": 2})
    assert full.resources.gas == 1 and full.survivors == 5
    partial = _player(gas=1)
    fx.resolve_immediate(spec, partial, SeededRng(1), decision={"gas_pay": 1})
    assert partial.resources.gas == 0 and partial.survivors == 4  # 缺 1 损 1


def test_russian_roulette_seeded():
    """固定种子下，掷骰结果与损失人数可复算。"""
    seed = 5
    p = _player(survivors=5)
    rng = SeededRng(seed)
    expected_faces = SeededRng(seed).roll_many(5)
    spec = {"kind": fx.KIND_RUSSIAN_ROULETTE, "timing": fx.TIMING_IMMEDIATE,
            "dice": "normal", "bite_face": 6, "lose_per": 1}
    result = fx.resolve_immediate(spec, p, rng)
    assert result["rolls"] == expected_faces
    bites = expected_faces.count(6)
    assert p.survivors == 5 - bites


def test_rescue_shot_branches():
    spec = {"kind": fx.KIND_RESCUE_SHOT, "timing": fx.TIMING_IMMEDIATE,
            "dice": "normal", "save_faces": (3, 4, 5), "lose": 1}
    # 找一个必出 1（解救失败）的种子
    for seed in range(50):
        if SeededRng(seed).d6() == 1:
            p = _player()
            fx.resolve_immediate(spec, p, SeededRng(seed))
            assert p.survivors == 4
            break
    for seed in range(50):
        if SeededRng(seed).d6() == 3:
            p = _player()
            fx.resolve_immediate(spec, p, SeededRng(seed))
            assert p.survivors == 5
            break


# ---------- 3. 引擎层集成 ----------
def _force_encounter(engine, card_id):
    """白盒：把指定卡摆为当前遭遇卡（便于精准测试单卡流程）。"""
    engine.state.phase = PHASE_ENCOUNTER
    engine.state.encounter_queue = [card_id]
    engine.state.encounter_index = 0


def auto_play_combat(engine, use_meds=True):
    """测试辅助：用近战把当前挂起的战斗打完（默认在所有可用机会上花药剂）。"""
    priority = {"bite": 0, "extra": 1, "wait": 2}
    while engine.state.pending_combat is not None:
        view = engine.combat_roll_melee()
        usable = [o for o in view["opportunities"]
                  if use_meds and o["available"]]
        usable.sort(key=lambda o: priority[o["opportunity"]])
        idxs = [o["idx"] for o in usable[:engine.state.player.resources.meds]]
        outcome = engine.combat_resolve_melee(idxs)
        if outcome["result"] in ("won", "lost"):
            return outcome
    return None


def test_engine_scavenge_noncombat(cards, config):
    engine = Engine.new_solo(cards, config, seed=1)
    _force_encounter(engine, "I-3")  # 拾荒 +2 汽油、无事件、无战斗
    gas_before = engine.state.player.resources.gas
    outcome = engine.begin_card_resolution()
    assert outcome["scavenge"] == {"gas": 2}
    assert outcome["combat"] is None
    assert engine.state.player.resources.gas == gas_before + 2
    assert "I-3" in engine.state.player.won_card_ids


def test_engine_combat_card_pending(cards, config):
    engine = Engine.new_solo(cards, config, seed=1)
    _force_encounter(engine, "I-13")  # +1弹药+1药剂，4 丧尸
    outcome = engine.begin_card_resolution()
    assert outcome["combat"]["zombies_count"] == 4
    assert engine.state.pending_combat is not None
    # 通过真实近战打赢
    result = auto_play_combat(engine)
    assert result["result"] == "won"
    assert "I-13" in engine.state.player.won_card_ids
    assert engine.state.stats["zombies_killed"] == 4


def test_engine_combat_mods_attached(cards, config):
    engine = Engine.new_solo(cards, config, seed=1)
    _force_encounter(engine, "III-19")  # 不能逃跑
    outcome = engine.begin_card_resolution()
    assert outcome["combat"]["no_flee"] is True
    action_kinds = [a["action"] for a in engine.combat_actions()]
    assert "flee" not in action_kinds
    auto_play_combat(engine)

    engine2 = Engine.new_solo(cards, config, seed=1)
    _force_encounter(engine2, "III-10")  # 不能用药剂
    outcome2 = engine2.begin_card_resolution()
    assert outcome2["combat"]["no_meds"] is True
    view = engine2.combat_roll_melee()
    assert view["no_meds"] is True
    assert all(not o["available"] for o in view["opportunities"])


def test_engine_zealot_nuke_skips_combat(cards, config):
    engine = Engine.new_solo(cards, config, seed=1)
    engine.state.player.gain_item(ITEM_ZEALOT)
    _force_encounter(engine, "III-17")  # 6 丧尸、level1
    outcome = engine.begin_card_resolution()
    assert outcome["combat"] is None  # 全屏秒杀，战斗跳过
    assert engine.state.pending_combat is None
    assert not engine.state.player.has_item(ITEM_ZEALOT)
    assert engine.state.stats["zombies_killed"] == 6
    assert "III-17" in engine.state.player.won_card_ids


def test_full_game_smoke_with_decisions(cards, config):
    """整局 8 轮脚本空跑：自动应答所有决策、战斗全部打赢，断言流程闭环。"""
    engine = Engine.new_solo(cards, config, seed=2026)
    # 流程冒烟：给足人与资源，避免事件损耗把人清零而中断流程
    p = engine.state.player
    p.survivors = 30
    p.resources.ammo, p.resources.gas, p.resources.meds = 30, 30, 30

    def auto_decision():
        needed = engine.peek_card_decision()
        if needed is None:
            return None
        if needed["type"] == fx.DECISION_CHOOSE_RESOURCE:
            return {"resource": needed["available"][0]}
        if needed["type"] == fx.DECISION_PAY_GAS:
            return {"gas_pay": min(needed["required"], needed["available_gas"])}
        if needed["type"] == fx.DECISION_ZEALOT_UPKEEP:
            return {"keep": needed["can_keep"]}
        return None

    rounds = 0
    while not engine.is_finished():
        rounds += 1
        engine.choose_path(2)  # 始终走路径二，避免路径奖惩干扰
        while engine.state.phase == PHASE_ENCOUNTER:
            outcome = engine.begin_card_resolution(auto_decision())
            if outcome.get("combat"):
                result = auto_play_combat(engine)
                if result["result"] == "lost":
                    break
        assert engine.state.phase == PHASE_ROUND_END
        engine.close_round()
    assert rounds == 8
    assert len(engine.state.player.won_card_ids) == 16  # 每轮 2 张
    assert engine.state.stats["fled"] == 0
