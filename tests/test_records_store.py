"""test_records_store.py —— M6：跨对局历史最佳 records.json。"""

from zroad.platforms.desktop_rich.records_store import RecordsStore


def test_first_win_sets_best(tmp_path):
    store = RecordsStore(save_dir=str(tmp_path))
    assert store.best() is None
    updated, best = store.record_result(12, rating_label="不错",
                                        difficulty="easy")
    assert updated is True and best["score"] == 12
    # 落盘后重新实例化也能读到
    assert RecordsStore(save_dir=str(tmp_path)).best()["score"] == 12


def test_higher_score_replaces_lower(tmp_path):
    store = RecordsStore(save_dir=str(tmp_path))
    store.record_result(10, difficulty="easy")
    updated, best = store.record_result(18, rating_label="非常好",
                                        difficulty="hard")
    assert updated is True and best["score"] == 18 and best["difficulty"] == "hard"


def test_lower_or_equal_score_keeps_best(tmp_path):
    store = RecordsStore(save_dir=str(tmp_path))
    store.record_result(18, difficulty="easy")
    updated, best = store.record_result(9, difficulty="easy")
    assert updated is False and best["score"] == 18
    # 并列也不覆盖（保留最早达成）
    updated2, best2 = store.record_result(18, difficulty="hard")
    assert updated2 is False and best2["difficulty"] == "easy"


def test_eliminated_run_never_sets_best(tmp_path):
    store = RecordsStore(save_dir=str(tmp_path))
    updated, best = store.record_result(4, eliminated=True)
    assert updated is False and best is None
    # 先有纪录后全灭，也不覆盖
    store.record_result(15)
    updated2, best2 = store.record_result(20, eliminated=True)
    assert updated2 is False and best2["score"] == 15


def test_corrupted_file_resets_gracefully(tmp_path):
    store = RecordsStore(save_dir=str(tmp_path))
    store.path.write_text("不是合法 JSON", encoding="utf-8")
    assert store.best() is None  # 损坏文件不炸游戏，按无纪录处理
    updated, best = store.record_result(7)
    assert updated and best["score"] == 7
