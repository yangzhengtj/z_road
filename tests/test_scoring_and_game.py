"""test_scoring_and_game.py —— M5：计分评级、整局闭环、存档仓库测试。"""

import json
from pathlib import Path

import pytest

from zroad.core.model import PlayerState, Resources
from zroad.core import scoring
from zroad.headless.scripted import play_full_game
from zroad.platforms.desktop_rich.save_store import SaveStore, AUTO_SLOT

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def cards():
    return json.loads((ROOT / "src" / "zroad" / "data" / "cards.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def config():
    return json.loads((ROOT / "src" / "zroad" / "data" / "config.json").read_text(encoding="utf-8"))


# ---------- 套装 ----------
def test_compute_sets(config):
    cfg = config["scoring"]
    # 5 人、弹药3、汽油2、药剂10 → 受汽油限制只能凑 2 套
    p = PlayerState(survivors=5, resources=Resources(3, 2, 10))
    assert scoring.compute_sets(p, cfg["complete_set"]) == 2
    p2 = PlayerState(survivors=0, resources=Resources(9, 9, 9))
    assert scoring.compute_sets(p2, cfg["complete_set"]) == 0


# ---------- 总分构成 ----------
def test_score_breakdown(cards, config):
    catalog = {c["id"]: c for c in cards}
    p = PlayerState(survivors=3, resources=Resources(3, 3, 3))
    # I-1 得分1、I-13 得分1（以数据实际为准，动态求和避免写死）
    chosen = ["I-1", "I-13"]
    p.won_card_ids = chosen
    p.bonus_score = 6
    report = scoring.final_report(p, catalog, config["scoring"])
    expected_card = sum(catalog[c]["score"] for c in chosen)
    assert report["card_score"] == expected_card
    assert report["bonus_score"] == 6
    assert report["sets"] == 3  # 3 人、三资源各 3
    assert report["total"] == expected_card + 6 + 3
    assert report["rating"] is not None


# ---------- 评级边界 ----------
@pytest.mark.parametrize("total,label", [
    (0, "神父加百列"), (5, "神父加百列"),
    (6, "还好"), (10, "还好"),
    (11, "不错"), (15, "不错"),
    (16, "非常好"), (18, "非常好"),
    (19, "叫我达瑞"), (21, "叫我达瑞"),
    (22, "幸运儿或天才"), (99, "幸运儿或天才"),
])
def test_rating_boundaries(config, total, label):
    assert scoring.rating_for(total, config["scoring"])["label"] == label


# ---------- 整局闭环（多种子） ----------
@pytest.mark.parametrize("seed", [0, 1, 2, 3])
def test_full_game_finishes_with_report(cards, config, seed):
    engine = play_full_game(cards, config, seed=seed)
    assert engine.is_finished()
    report = engine.final_report()
    # 总分恒等式
    assert report["total"] == (report["card_score"] + report["bonus_score"]
                               + report["set_score"])
    assert report["rating"] is not None
    # 8 轮内必然结束（通关打完第 8 轮，或中途全灭提前结束）
    assert 1 <= engine.state.round_no <= 8
    assert report["won_count"] == len(engine.state.player.won_card_ids)
    assert report["won_count"] <= 16


# ---------- 存档仓库 ----------
def test_save_store_roundtrip(cards, config, tmp_path):
    engine = play_full_game(cards, config, seed=11)
    store = SaveStore(save_dir=tmp_path)
    path = store.write(AUTO_SLOT, engine)
    assert path.exists()
    preview = store.preview(AUTO_SLOT)
    assert preview["round_no"] == 8 and preview["phase"] == "finished"
    state = store.read(AUTO_SLOT)
    assert state["round_no"] == 8
    # 三个手动槽初始为空
    assert store.preview("manual_1") is None
