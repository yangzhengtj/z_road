"""test_app_smoke.py —— M5：Rich 终端 App 的整局驱动冒烟（不依赖真人键盘）。

做法：实例化真实 GameApp，把“向人提问”的入口（菜单、数字输入、回车暂停）
替换成自动应答，输出重定向到内存，然后完整跑 8 轮，
覆盖选路/遭遇/战斗药剂抉择/轮末自动存档/终局渲染的每一条 App 分支。
v0.6.1：选路改为自由输入（1/2/3 或 2L/3R 查看明牌），由假 Prompt 应答。
"""

import io
import json
from pathlib import Path

import pytest
from rich.console import Console

from zroad.core.engine import Engine
from zroad.platforms.desktop_rich.app import GameApp
from zroad.platforms.desktop_rich import app as appmod
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
    # 菜单一律选第一个可用项（战斗优先远程、否则近战、进入下一轮、损失第一种资源等）
    a.ask_menu = lambda prompt, options: [k for k, _, ok in options if ok][0]
    return a


def _auto_int(prompt, low, high, default=None):
    return default if default is not None else low


def _scripted_prompt(path_choice):
    """造一个按提示文本分流的假 Prompt.ask。"""
    def fake_ask(prompt="", *a, **k):
        text = str(prompt)
        if "选路径" in text:
            return path_choice
        if "药剂的骰号" in text:
            return ""          # 近战不主动花药剂
        return "y"             # 其余 y/n 确认默认肯定
    return staticmethod(fake_ask)


@pytest.mark.parametrize("seed", [1, 5, 9])
def test_app_plays_full_game(app, seed, monkeypatch):
    monkeypatch.setattr(app, "_ask_int", _auto_int)
    monkeypatch.setattr(appmod.Prompt, "ask", _scripted_prompt("2"))
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
    monkeypatch.setattr(appmod.Prompt, "ask", _scripted_prompt("1"))
    engine = Engine.new_solo(app.cards, app.config, seed=1)
    before = engine.state.player.resources.ammo
    app.do_planning(engine)
    assert engine.state.phase == "encounter"
    assert engine.state.player.resources.ammo == before + 2


def test_app_inspect_face_up_card(app, monkeypatch):
    """选路时输入 2L/3R 可查看明牌详情，之后仍能正常选路。"""
    answers = iter(["2L", "3R", "2"])  # 先看两张明牌，再选路径二
    monkeypatch.setattr(app, "_ask_int", _auto_int)
    monkeypatch.setattr(appmod.Prompt, "ask",
                        staticmethod(lambda *a, **k: next(answers)))
    engine = Engine.new_solo(app.cards, app.config, seed=1)
    app.do_planning(engine)
    out = app.console.file.getvalue()
    assert "卡牌详情" in out and engine.state.phase == "encounter"


def test_app_hard_difficulty_path2_costs_one(app, monkeypatch):
    """困难难度：路径2 选前支付 1 个任意资源（分配全落到最后一种资源药剂）。"""
    monkeypatch.setattr(app, "_ask_int", _auto_int)  # 每类先问 0，最后一种自动补齐 1
    monkeypatch.setattr(appmod.Prompt, "ask", _scripted_prompt("2"))
    engine = Engine.new_solo(app.cards, app.config, seed=1, difficulty="hard")
    before = engine.state.player.resources.meds
    app.do_planning(engine)
    assert engine.state.phase == "encounter"
    assert engine.state.player.resources.meds == before - 1
    assert "难度困难" in app.console.file.getvalue()


def test_bus_downgrade_shown_on_panels(app):
    """校车+车辆铠甲两件套遭遇屍群时，面板明示强化骰全降级；单件不提示。"""
    from rich.console import Console
    from zroad.platforms.desktop_rich import render
    from zroad.core.model import PlayerState, Resources
    from zroad.core.constants import ITEM_BUS, ITEM_VEHICLE_ARMOR
    card = next(c for c in app.cards if c["zombies"]["level"] == 1)
    # 单件校车：不出现降级提示
    out1 = io.StringIO()
    Console(file=out1, width=100).print(
        render.card_panel(card, armored_bus_held=False))
    assert "降级" not in out1.getvalue()
    # 两件套齐：出现降级提示
    out2 = io.StringIO()
    Console(file=out2, width=100).print(
        render.card_panel(card, armored_bus_held=True))
    assert "校车+车辆铠甲：全降级为普通骰" in out2.getvalue()
    player = PlayerState(survivors=5, resources=Resources(4, 4, 4))
    player.gain_item(ITEM_BUS)
    player.gain_item(ITEM_VEHICLE_ARMOR)
    combo = (player.has_item(ITEM_BUS)
             and player.has_item(ITEM_VEHICLE_ARMOR))
    assert combo is True
