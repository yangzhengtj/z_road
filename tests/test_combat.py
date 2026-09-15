"""test_combat.py —— M4 战斗系统测试。

用“脚本化随机源”按顺序吐出指定骰面，保证战斗结算逐面可验证，
不依赖真实随机种子；引擎层另测战斗收口与遭遇推进、战斗中途存档。
"""

import json
from pathlib import Path

import pytest

from zroad.core.model import PlayerState, Resources, EngineError
from zroad.core.combat import (Combat, new_combat_state, STAGE_ACTION,
                               STAGE_MELEE_DECISION, RESULT_WON, RESULT_FLED,
                               RESULT_LOST, OPP_BITE)
from zroad.core import constants as C
from zroad.core.engine import Engine

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def cards():
    return json.loads((ROOT / "data" / "cards.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def config():
    return json.loads((ROOT / "data" / "config.json").read_text(encoding="utf-8"))


class ScriptedRng(object):
    """按预设序列吐骰面：d6/roll_many 都从同一个队列取。"""

    def __init__(self, faces):
        self._faces = list(faces)
        self._i = 0

    def d6(self):
        face = self._faces[self._i]
        self._i += 1
        return face

    def roll_many(self, n):
        return [self.d6() for _ in range(n)]


def _combat(survivors=5, zombies=3, level=0, ammo=4, gas=4, meds=4,
            faces=(), mod_kinds=None, items=None, config=None):
    cfg = config if config is not None else json.loads(
        (ROOT / "data" / "config.json").read_text(encoding="utf-8"))
    player = PlayerState(survivors=survivors,
                         resources=Resources(ammo, gas, meds))
    for item in (items or []):
        player.gain_item(item)
    stats = GameState_stats()
    state = new_combat_state("T", zombies, level, mod_kinds)
    return Combat(player, state, ScriptedRng(faces), stats, cfg["combat"]), player, stats


def GameState_stats():
    return {"zombies_killed": 0, "survivors_lost": 0, "combats": 0, "fled": 0,
            "resources_gained": {"ammo": 0, "gas": 0, "meds": 0},
            "resources_spent": {"ammo": 0, "gas": 0, "meds": 0},
            "dice_faces": {"normal": {i: 0 for i in range(1, 7)},
                           "enhanced": {i: 0 for i in range(1, 7)}}}


# ---------- 骰面映射与数据一致 ----------
def test_face_maps_match_dice_json():
    dice = json.loads((ROOT / "data" / "dice.json").read_text(encoding="utf-8"))
    for face in range(1, 7):
        assert C.NORMAL_FACE_NAMES[face] == dice["normal"][face - 1]["name"]
        assert C.ENHANCED_FACE_NAMES[face] == dice["enhanced"][face - 1]["name"]


# ---------- 近战骰组成 ----------
def test_melee_composition_level():
    combat, _, _ = _combat(survivors=5, level=2)
    assert combat._melee_dice_composition() == (2, 3)  # 2 强化 + 3 普通
    combat2, _, _ = _combat(survivors=5, level=3, items=[C.ITEM_BUS])
    assert combat2._melee_dice_composition() == (0, 5)  # 校车：强化降级为普通
    combat3, _, _ = _combat(survivors=2, level=3)
    assert combat3._melee_dice_composition() == (2, 0)  # 总数不超过幸存者


# ---------- 六骰面 × 两骰种逐面结算 ----------
def test_blank_does_nothing():
    combat, player, _ = _combat(zombies=3, faces=[1, 1, 1, 1, 1])
    combat.roll_melee()
    out = combat.resolve_melee()
    assert out["result"] == "ongoing" and out["kills"] == 0
    assert player.survivors == 5


def test_kill_faces_win():
    combat, _, _ = _combat(zombies=3, faces=[3, 4, 3, 1, 1])  # 三个击杀
    combat.roll_melee()
    out = combat.resolve_melee()
    assert out["result"] == RESULT_WON and out["kills"] == 3


def test_extra_kill_with_and_without_meds():
    # 额外击杀：不花药剂杀 1；花药剂再杀 1
    combat, player, _ = _combat(zombies=2, meds=1, faces=[5, 1, 1, 1, 1])
    view = combat.roll_melee()
    idx = [o["idx"] for o in view["opportunities"]
           if o["opportunity"] == "extra"]
    out = combat.resolve_melee(idx)
    assert out["kills"] == 2 and player.resources.meds == 0

    combat2, player2, _ = _combat(zombies=2, faces=[5, 1, 1, 1, 1])
    combat2.roll_melee()
    out2 = combat2.resolve_melee([])
    assert out2["kills"] == 1 and out2["result"] == "ongoing"


def test_hold_face_meds_choice():
    combat, player, _ = _combat(zombies=1, meds=1, faces=[2, 1, 1, 1, 1])
    view = combat.roll_melee()
    idx = [o["idx"] for o in view["opportunities"]]
    out = combat.resolve_melee(idx)  # 付药剂反杀
    assert out["kills"] == 1 and player.resources.meds == 0

    combat2, player2, _ = _combat(zombies=1, faces=[2, 1, 1, 1, 1])
    combat2.roll_melee()
    out2 = combat2.resolve_melee([])  # 躲避
    assert out2["kills"] == 0 and player2.survivors == 5


def test_bite_normal_face():
    # 普通骰 6 = 咬伤：不付药剂损 1 人
    combat, player, _ = _combat(zombies=1, faces=[6, 1, 1, 1, 1])
    view = combat.roll_melee()
    out = combat.resolve_melee([])
    assert player.survivors == 4 and out["survivors_lost"] == 1
    # 付药剂可救
    combat2, player2, _ = _combat(zombies=1, meds=1, faces=[6, 1, 1, 1, 1])
    view2 = combat2.roll_melee()
    bite_idx = [o["idx"] for o in view2["opportunities"]
                if o["opportunity"] == OPP_BITE]
    out2 = combat2.resolve_melee(bite_idx)
    assert player2.survivors == 5 and player2.resources.meds == 0


def test_enhanced_faces():
    # level1：第一颗为强化骰。强化骰 1=咬伤、6=死亡
    combat, player, _ = _combat(zombies=1, level=1,
                                faces=[1, 1, 1, 1, 1])
    view = combat.roll_melee()
    assert view["entries"][0]["kind"] == "enhanced"
    out = combat.resolve_melee([])  # 强化咬伤未救 → 损 1
    assert player.survivors == 4

    combat2, player2, _ = _combat(zombies=1, level=1, meds=4,
                                  faces=[6, 1, 1, 1, 1])  # 强化死亡
    view2 = combat2.roll_melee()
    death = view2["entries"][0]
    assert death["name"] == C.FACE_DEATH and death["opportunity"] is None
    out2 = combat2.resolve_melee([])
    assert player2.survivors == 4 and player2.resources.meds == 4  # 药剂救不了


def test_no_meds_mod():
    combat, player, _ = _combat(zombies=1, meds=4, faces=[6, 1, 1, 1, 1],
                                mod_kinds=["mod_no_meds"])
    view = combat.roll_melee()
    assert view["no_meds"] is True
    bite = [o["idx"] for o in view["opportunities"]]
    with pytest.raises(EngineError):
        combat.resolve_melee(bite)  # 禁止用药
    out = combat.resolve_melee([])
    assert player.survivors == 4 and player.resources.meds == 4


def test_total_loss_is_defeat():
    # 5 颗全是普通咬伤、无药剂 → 全灭 → lost
    combat, player, _ = _combat(survivors=5, zombies=9, meds=0,
                                faces=[6, 6, 6, 6, 6])
    combat.roll_melee()
    out = combat.resolve_melee([])
    assert out["result"] == RESULT_LOST and player.survivors == 0


# ---------- 远程攻击 ----------
def test_ranged_attack_kills():
    combat, player, stats = _combat(zombies=2, ammo=2, faces=[3, 5])
    result = combat.do_ranged(1)  # 1 弹药 2 骰：击杀+额外击杀面各杀 1
    assert result["kills"] == 2 and result["result"] == RESULT_WON
    assert player.resources.ammo == 1 and stats["zombies_killed"] == 2
    with pytest.raises(EngineError):
        combat.do_ranged(1)  # 每场仅一次


def test_ranged_bite_adds_zombie_mod():
    combat, _, _ = _combat(zombies=3, ammo=1, faces=[6, 6],
                           mod_kinds=["mod_ranged_bite_adds"])
    result = combat.do_ranged(1)  # 两个咬伤面：丧尸 +2、0 击杀
    assert result["zombies_added"] == 2 and result["zombies_left"] == 5


def test_ranged_ignores_bite_without_mod():
    combat, _, _ = _combat(zombies=3, ammo=1, faces=[6, 1])
    result = combat.do_ranged(1)
    assert result["zombies_added"] == 0 and result["zombies_left"] == 3


# ---------- 逃跑 ----------
def test_flee_costs_gas():
    combat, player, stats = _combat(zombies=3, gas=3)
    result = combat.do_flee()
    assert result["result"] == RESULT_FLED
    assert player.resources.gas == 1 and stats["fled"] == 1


def test_flee_forbidden_by_mod():
    combat, _, _ = _combat(zombies=3, mod_kinds=["mod_no_flee"])
    actions = [a["action"] for a in combat.available_actions()]
    assert "flee" not in actions
    with pytest.raises(EngineError):
        combat.do_flee()


# ---------- 引擎层：战斗收口推进遭遇、中途存档 ----------
def _force_encounter(engine, card_id):
    engine.state.phase = "encounter"
    engine.state.encounter_queue = [card_id, "I-1"]
    engine.state.encounter_index = 0


def test_engine_combat_win_advances(cards, config):
    engine = Engine.new_solo(cards, config, seed=3)
    _force_encounter(engine, "I-13")  # 4 丧尸，其后还有 I-1
    engine.begin_card_resolution()
    # 给足人，反复近战直到赢
    engine.state.player.survivors = 20
    priority = {"bite": 0, "extra": 1, "wait": 2}
    while engine.state.pending_combat:
        view = engine.combat_roll_melee()
        usable = sorted((o for o in view["opportunities"] if o["available"]),
                        key=lambda o: priority[o["opportunity"]])
        idxs = [o["idx"] for o in usable[:engine.state.player.resources.meds]]
        out = engine.combat_resolve_melee(idxs)
        if out["result"] in ("won", "lost"):
            break
    assert "I-13" in engine.state.player.won_card_ids
    assert engine.current_encounter_card() == "I-1"  # 自动推进到下一张


def test_combat_state_roundtrip(cards, config):
    engine = Engine.new_solo(cards, config, seed=3)
    _force_encounter(engine, "I-14")
    engine.begin_card_resolution()
    engine.combat_roll_melee()  # 停在“等待药剂决策”
    assert engine.state.pending_combat["combat_stage"] == STAGE_MELEE_DECISION

    restored = Engine.restore(cards, config, engine.snapshot())
    assert restored.state.pending_combat["combat_stage"] == STAGE_MELEE_DECISION
    # 控制器惰性重建后可继续结算
    out = restored.combat_resolve_melee([])
    assert out["result"] in ("ongoing", "won", "lost")


def test_dice_stats_survive_json_roundtrip(cards, config):
    """回归：存档 JSON 往返后骰面统计键转回 int，继续掷骰不得 KeyError。"""
    import json
    engine = Engine.new_solo(cards, config, seed=3)
    _force_encounter(engine, "I-13")  # 4 丧尸
    engine.begin_card_resolution()
    engine.combat_ranged(1)  # 先掷一批骰，让 stats.dice_faces 有累计
    assert sum(engine.state.stats["dice_faces"]["normal"].values()) == 2

    # 存档落盘再读回（JSON 会把数字键转成字符串）
    blob = json.loads(json.dumps(engine.snapshot()))
    restored = Engine.restore(cards, config, blob)
    normal = restored.state.stats["dice_faces"]["normal"]
    assert all(isinstance(face, int) for face in normal.keys())
    before = sum(normal.values()) + sum(
        restored.state.stats["dice_faces"]["enhanced"].values())
    # 继续近战掷骰（每名幸存者 1 骰），不再抛 KeyError 且累计正确
    restored.combat_roll_melee()
    rolled = len(restored.state.pending_combat["last_roll"])
    after = sum(restored.state.stats["dice_faces"]["normal"].values()) + sum(
        restored.state.stats["dice_faces"]["enhanced"].values())
    assert rolled == restored.state.player.survivors
    assert after == before + rolled
