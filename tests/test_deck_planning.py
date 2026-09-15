"""test_deck_planning.py —— M2：牌库构建、单人三路径、轮次/阶段推进测试。

覆盖验收标准：
  - 固定种子下牌序可复现；
  - setup 每阶段剔 4、登场 12/18/18，跨阶段不混洗；
  - 每轮恰好消耗 6 张，三阶段 2/3/3 轮、共 8 轮后牌库恰好耗尽；
  - 三路径明暗与奖惩正确，路径三资源不足时被禁止；
  - 存档快照（含随机状态）恢复后，后续对局与不存档完全一致。
"""

import copy
import json
from pathlib import Path

import pytest

from zroad.core.constants import (PHASE_PLANNING, PHASE_ENCOUNTER,
                                  PHASE_ROUND_END, PHASE_FINISHED)
from zroad.core.model import EngineError
from zroad.core.engine import Engine
from zroad.core import deck as deck_mod

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def cards():
    return json.loads((ROOT / "src" / "zroad" / "data" / "cards.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def config():
    return json.loads((ROOT / "src" / "zroad" / "data" / "config.json").read_text(encoding="utf-8"))


# ---------- 测试辅助：按固定脚本打完一轮（M2 不结算遭遇，只走队列） ----------
def _resource_dist_for(path_index):
    """路径一奖励拿 2 弹药；路径三代价付 1 汽油+1 药剂；路径二无需分配。"""
    if path_index == 1:
        return {"ammo": 2, "gas": 0, "meds": 0}
    if path_index == 3:
        return {"ammo": 0, "gas": 1, "meds": 1}
    return None


def play_one_round(engine, path_index):
    """选路 → 排空遭遇队列（M3 起这里会插入拾荒/事件/战斗）→ 结束本轮。"""
    engine.choose_path(path_index, _resource_dist_for(path_index))
    assert engine.state.phase == PHASE_ENCOUNTER
    # 两张中选卡依次消费完
    first = engine.current_encounter_card()
    second = engine.consume_encounter_card()
    assert first is not None and second is not None
    assert engine.consume_encounter_card() is None
    assert engine.state.phase == PHASE_ROUND_END
    return engine.close_round()


def scripted_path(round_no):
    """固定选路脚本：1→2→3 循环，保证可复现。"""
    return (round_no - 1) % 3 + 1


# ---------- 1. setup：剔 4、登场 12/18/18，且全集守恒 ----------
def test_build_decks_sizes(cards, config):
    """牌库构建层：三阶段登场 12/18/18、各剔 4。"""
    from zroad.core.rng import SeededRng
    active, removed = deck_mod.build_stage_decks(
        cards, SeededRng(100), config["setup"])
    assert [len(active[s]) for s in (1, 2, 3)] == [12, 18, 18]
    assert [len(removed[s]) for s in (1, 2, 3)] == [4, 4, 4]


def test_setup_remove_four_and_conserve(cards, config):
    """建局即进入第一轮：阶段 I 已摸出 6 张摆路径，牌库剩 6；全集仍守恒为 60。"""
    engine = Engine.new_solo(cards, config, seed=100)
    state = engine.state
    # 阶段 I：牌库剩余 6 + 当前轮摆出 6 = 登场 12；II/III 尚未摸牌
    assert [len(state.stage_decks[s]) for s in (1, 2, 3)] == [6, 18, 18]
    on_table = [cid for opt in state.current_options for cid in opt.card_ids]
    assert len(on_table) == 6
    assert [len(state.removed_cards[s]) for s in (1, 2, 3)] == [4, 4, 4]
    all_ids = set(on_table)
    for stage in (1, 2, 3):
        active = state.stage_decks[stage]
        removed = state.removed_cards[stage]
        assert not (set(active) & set(removed))
        all_ids.update(active)
        all_ids.update(removed)
    assert len(all_ids) == 60  # 一张不多一张不少


def test_no_cross_stage_mixing(cards, config):
    """每个阶段牌库里只能出现本阶段卡牌（id 前缀校验）。"""
    engine = Engine.new_solo(cards, config, seed=7)
    prefix = {1: "I-", 2: "II-", 3: "III-"}
    for stage in (1, 2, 3):
        for card_id in engine.state.stage_decks[stage] + engine.state.removed_cards[stage]:
            assert card_id.startswith(prefix[stage])


# ---------- 2. 可复现性 ----------
def test_same_seed_same_decks(cards, config):
    a = Engine.new_solo(cards, config, seed=2026)
    b = Engine.new_solo(cards, config, seed=2026)
    assert a.state.stage_decks == b.state.stage_decks
    assert a.state.removed_cards == b.state.removed_cards


def test_different_seed_differs(cards, config):
    a = Engine.new_solo(cards, config, seed=1)
    b = Engine.new_solo(cards, config, seed=2)
    # 极小概率两种子洗牌结果完全相同，若真发生可换种子；此处先断言至少一个阶段顺序不同
    assert (a.state.stage_decks != b.state.stage_decks or
            a.state.removed_cards != b.state.removed_cards)


# ---------- 3. 初始状态 ----------
def test_initial_player_state(cards, config):
    engine = Engine.new_solo(cards, config, seed=0)
    p = engine.state.player
    assert p.survivors == 5
    assert (p.resources.ammo, p.resources.gas, p.resources.meds) == (4, 4, 4)
    assert engine.state.round_no == 1
    assert engine.state.phase == PHASE_PLANNING


# ---------- 4. 三路径明暗 ----------
def test_path_visibility(cards, config):
    engine = Engine.new_solo(cards, config, seed=11)
    options = {opt.index: opt for opt in engine.state.current_options}
    assert options[1].face_up == (False, False)
    assert options[2].face_up == (True, False)
    assert options[3].face_up == (True, True)
    assert options[1].bonus == 2 and options[3].cost == 2


# ---------- 5. 选路：2 入队、4 弃置、每轮消耗 6 ----------
def test_choose_path_queue_and_discard(cards, config):
    engine = Engine.new_solo(cards, config, seed=11)
    before = {s: len(engine.state.stage_decks[s]) for s in (1, 2, 3)}
    chosen = engine.choose_path(2)  # 路径二无奖惩
    assert engine.state.encounter_queue == chosen.card_ids
    assert len(engine.state.discard_ids) == 4
    chosen_set = set(chosen.card_ids)
    assert not (chosen_set & set(engine.state.discard_ids))
    # 选路本身不再摸牌（6 张建局时已摆出），牌库余量不变
    assert {s: len(engine.state.stage_decks[s]) for s in (1, 2, 3)} == before
    # 结束本轮、进入第二轮时，才从阶段 I 再摸 6 张
    engine.consume_encounter_card()
    engine.consume_encounter_card()
    engine.close_round()
    assert len(engine.state.stage_decks[1]) == before[1] - 6


# ---------- 6. 路径一奖励 ----------
def test_path1_bonus(cards, config):
    engine = Engine.new_solo(cards, config, seed=11)
    engine.choose_path(1, {"ammo": 2, "gas": 0, "meds": 0})
    assert engine.state.player.resources.ammo == 6  # 4 + 2
    with pytest.raises(EngineError):
        # 合计不是 2 必须拒绝
        engine2 = Engine.new_solo(cards, config, seed=11)
        engine2.choose_path(1, {"ammo": 1})


# ---------- 7. 路径三代价与资源不足封锁 ----------
def test_path3_cost(cards, config):
    engine = Engine.new_solo(cards, config, seed=11)
    engine.choose_path(3, {"ammo": 0, "gas": 1, "meds": 1})
    assert (engine.state.player.resources.gas,
            engine.state.player.resources.meds) == (3, 3)


def test_path3_blocked_when_poor(cards, config):
    engine = Engine.new_solo(cards, config, seed=11)
    # 把资源打到只剩 1（总资源不足 2）
    res = engine.state.player.resources
    res.ammo, res.gas, res.meds = 1, 0, 0
    with pytest.raises(EngineError):
        engine.choose_path(3, {"ammo": 1, "gas": 0, "meds": 0})
    # 被拒绝后仍停留在规划阶段、选项未被消耗
    assert engine.state.phase == PHASE_PLANNING
    assert len(engine.state.discard_ids) == 0


def test_choose_path_wrong_phase(cards, config):
    engine = Engine.new_solo(cards, config, seed=11)
    engine.choose_path(2)
    with pytest.raises(EngineError):
        engine.choose_path(2)  # 已进入遭遇阶段，不能再选


# ---------- 8. 完整 8 轮：2/3/3、每轮 6 张、恰好耗尽 ----------
def test_full_eight_rounds(cards, config):
    engine = Engine.new_solo(cards, config, seed=99)
    stage_rounds_seen = {1: 0, 2: 0, 3: 0}
    for round_no in range(1, 9):
        assert engine.state.phase == PHASE_PLANNING
        stage = engine.current_stage()
        stage_rounds_seen[stage] += 1
        play_one_round(engine, scripted_path(round_no))
    assert stage_rounds_seen == {1: 2, 2: 3, 3: 3}
    assert engine.is_finished()
    assert engine.state.phase == PHASE_FINISHED
    # 登场 48 张全部流向“中选（遭遇）或弃置”
    consumed = len(engine.state.discard_ids) + sum(
        len(entry["chosen_cards"]) for entry in engine.state.round_log)
    assert consumed == 48
    assert all(len(deck) == 0 for deck in engine.state.stage_decks.values())
    assert len(engine.state.round_log) == 8


# ---------- 9. stage_of_round 轮次→阶段映射 ----------
def test_stage_of_round():
    rounds = [2, 3, 3]
    assert [deck_mod.stage_of_round(r, rounds) for r in range(1, 9)] == \
        [1, 1, 2, 2, 2, 3, 3, 3]
    assert deck_mod.stage_of_round(9, rounds) is None


# ---------- 10. 存档快照恢复：后续对局必须严丝合缝 ----------
def test_snapshot_restore_continues_identically(cards, config):
    def play_scripted(engine, start, end):
        for round_no in range(start, end + 1):
            play_one_round(engine, scripted_path(round_no))

    # A：不中断连打 8 轮
    engine_a = Engine.new_solo(cards, config, seed=314)
    play_scripted(engine_a, 1, 8)

    # B：打到第 3 轮末（第 4 轮 planning 前）存档，再恢复继续打
    engine_b = Engine.new_solo(cards, config, seed=314)
    play_scripted(engine_b, 1, 3)
    snapshot = engine_b.snapshot()
    # 快照必须是纯 JSON 可序列化
    json.dumps(snapshot, ensure_ascii=False)
    restored = Engine.restore(cards, config, copy.deepcopy(snapshot))
    assert restored.state.round_no == engine_b.state.round_no
    play_scripted(restored, 4, 8)

    assert restored.snapshot() == engine_a.snapshot()


# ---------- 8. 难度设置（v0.6.4：简单=原规则，困难调整路径奖惩） ----------
def test_default_difficulty_is_easy(cards, config):
    engine = Engine.new_solo(cards, config, seed=1)
    assert engine.state.difficulty == "easy"
    opts = {o.index: o for o in engine.state.current_options}
    assert opts[1].bonus == 2 and opts[2].cost == 0 and opts[3].cost == 2


def test_hard_difficulty_path_effects(cards, config):
    engine = Engine.new_solo(cards, config, seed=1, difficulty="hard")
    opts = {o.index: o for o in engine.state.current_options}
    # 困难：路径1无奖惩、路径2付1、路径3付2
    assert opts[1].bonus == 0 and opts[1].cost == 0
    assert opts[2].cost == 1 and opts[3].cost == 2
    before = engine.state.player.resources.gas
    engine.choose_path(2, {"ammo": 0, "gas": 1, "meds": 0})
    assert engine.state.player.resources.gas == before - 1


def test_hard_difficulty_path1_no_bonus(cards, config):
    engine = Engine.new_solo(cards, config, seed=1, difficulty="hard")
    res = engine.state.player.resources
    before = (res.ammo, res.gas, res.meds)
    engine.choose_path(1)  # 困难路径1无奖惩，也不需要资源分配
    after = (res.ammo, res.gas, res.meds)
    assert before == after


def test_hard_difficulty_path2_blocked_when_empty(cards, config):
    engine = Engine.new_solo(cards, config, seed=1, difficulty="hard")
    res = engine.state.player.resources
    res.ammo, res.gas, res.meds = 0, 0, 0
    assert engine.path_is_affordable(engine._find_option(2)) is False
    with pytest.raises(EngineError):
        engine.choose_path(2, {"gas": 1})
    assert engine.state.phase == PHASE_PLANNING


def test_invalid_difficulty_rejected(cards, config):
    with pytest.raises(EngineError):
        Engine.new_solo(cards, config, difficulty="nightmare")


def test_difficulty_survives_save_and_next_rounds(cards, config):
    engine = Engine.new_solo(cards, config, seed=3, difficulty="hard")
    json.dumps(engine.snapshot(), ensure_ascii=False)  # 难度可序列化
    restored = Engine.restore(cards, config, engine.snapshot())
    assert restored.state.difficulty == "hard"
    # 打完第一轮，后续轮次摆出的仍是困难数值
    restored.choose_path(2, {"ammo": 1, "gas": 0, "meds": 0})
    restored.consume_encounter_card()
    restored.consume_encounter_card()
    restored.close_round()
    opts = {o.index: o for o in restored.state.current_options}
    assert opts[2].cost == 1 and opts[1].bonus == 0
