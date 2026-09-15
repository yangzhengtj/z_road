"""test_paths.py —— v0.7.1：数据/存档目录在三种安装形态下的解析。"""

import sys
from pathlib import Path

from zroad.platforms.desktop_rich import paths
from zroad.platforms.desktop_rich.save_store import default_save_dir
from zroad.platforms.desktop_rich.records_store import RecordsStore
from zroad.platforms.desktop_rich.app import load_game_data

ROOT = Path(__file__).resolve().parents[1]


def test_data_dir_contains_all_json():
    d = paths.data_dir()
    for name in ("cards.json", "dice.json", "config.json"):
        assert (d / name).is_file(), name


def test_load_game_data_reads_packaged_data():
    cards, config = load_game_data()
    assert len(cards) == 60
    assert "setup" in config


def test_save_dir_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("ZROAD_SAVE_DIR", str(tmp_path / "custom"))
    assert paths.save_dir() == tmp_path / "custom"
    # SaveStore 与 RecordsStore 都走同一解析结果
    assert default_save_dir() == tmp_path / "custom"


def test_save_dir_dev_checkout_is_repo_saves(monkeypatch):
    monkeypatch.delenv("ZROAD_SAVE_DIR", raising=False)
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    # 测试运行在仓库检出里（pyproject.toml 存在）→ 仓库根 saves/
    assert paths.save_dir() == ROOT / "saves"


def test_save_dir_frozen_uses_home(monkeypatch):
    monkeypatch.delenv("ZROAD_SAVE_DIR", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert paths.save_dir() == Path.home() / ".zroad" / "saves"


def test_save_dir_installed_package_uses_home(monkeypatch, tmp_path):
    """模拟 pip 安装：包不在仓库里（找不到 pyproject.toml）→ 用户目录。"""
    monkeypatch.delenv("ZROAD_SAVE_DIR", raising=False)
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(paths, "REPO_ROOT", tmp_path / "not_a_repo")
    assert paths.save_dir() == Path.home() / ".zroad" / "saves"


def test_records_store_uses_resolved_save_dir(monkeypatch, tmp_path):
    """不指定目录时 RecordsStore 与存档落在同一解析目录（env 覆盖生效）。"""
    monkeypatch.setenv("ZROAD_SAVE_DIR", str(tmp_path / "saves"))
    store = RecordsStore()
    assert store.dir == tmp_path / "saves"
