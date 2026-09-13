"""test_app_smoke.py —— M5：Rich 终端 App 的整局驱动冒烟（不依赖真人键盘）。

做法：实例化真实 GameApp，把“向人提问”的入口（菜单、数字输入、回车暂停）
替换成自动应答，输出重定向到内存，然后完整跑 8 轮，
覆盖选路/遭遇/战斗药剂抉择/轮末自动存档/终局渲染的每一条 App 分支。
"""

import io
import json
from pathlib import Path

import pytest
from rich.console import Console

from zroad.core.engine import Engine
from zroad.platforms.desktop_rich.app import GameApp
from zroad.platforms.desktop_rich.save_store import SaveStore

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def app(tmp_path):
    a = GameApp()
    # 输出全部进内存，存档写临时目录
    a.console = Console(file=io.StringIO(), width=100)
    a.store = SaveStore(save_dir=tmp_path)
    a.console.clear = lambda: None
    a.pause = lambda *x, **k: None
    # 菜单一律选第一个可用项（路径二、近战、进入下一轮、损失第一种资源等）
    a.ask_menu = lambda prompt, options: [k for k, _, ok in options if ok][0]
    return a


def _auto_int(prompt, low, high, default=None):
    return default if default is not None else low


@pytest.mark.parametrize("seed", [1, 5, 9])
def test_app_plays_full_game(app, seed, monkeypatch):
    monkeypatch.setattr(app, "_ask_int", _auto_int)
    # Prompt.ask 只可能出现在 y/n 确认等场景，统一回 y
    import zroad.platforms.desktop_rich.app as appmod
    monkeypatch.setattr(appmod.Prompt, "ask", staticmethod(lambda *a, **k: "y"))
    engine = Engine.new_solo(app.cards, app.config, seed=seed)
    app.play(engine)
    out = app.console.file.getvalue()
    report = engine.final_report()
    assert engine.is_finished()
    assert "总分" in out and report["rating"]["label"] in out
    # 轮末自动档确实落盘
    assert app.store.exists("auto")


def test_app_path1_bonus_distribution(app, monkeypatch):
    """选路径一：奖励 2 个任意资源，全部分给弹药后弹药 +2。"""
    monkeypatch.setattr(app, "_ask_int", lambda *a, **k: 2)  # 第一种资源给满
    engine = Engine.new_solo(app.cards, app.config, seed=1)
    before = engine.state.player.resources.ammo
    # 只走一次规划阶段
    app.do_planning(engine)
    assert engine.state.phase == "encounter"
    assert engine.state.player.resources.ammo == before + 2
