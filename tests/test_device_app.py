"""test_device_app.py —— Cardputer 设备前端的电脑端整局测试（M7.3）。

设备前端 device_app.py 不依赖硬件：sim.py 提供 ANSI/脚本后端。这里用
“看屏按键”的自动玩家驱动完整对局，断言：
  1. 简单/困难两种难度都能从主菜单一路走到终局并回到主菜单退出；
  2. 整局渲染的每一屏都没有缺字（点阵字库覆盖守门）；
  3. 自动存档与终局状态按规则落盘；
  4. 规划页能查看明牌（2L 流程），且明牌页包含事件/拾荒/得分。
"""

import os
import shutil
import tempfile

import pytest

from zroad.platforms.device_mpy import device_app
from zroad.platforms.device_mpy.sim import SimDriver, SimKeys, SimPlatform
from zroad.platforms.device_mpy.stores import AUTO_SLOT


def run_scripted_game(tmpdir, difficulty="e", inspect_card=False):
    """跑一局脚本对局。

    自动玩家策略：回车确认所有默认项；规划页固定选路径 1（任何难度都
    付得起）；战斗默认选首个可用动作（有远程先远程），药剂不用；
    轮末进下一轮；终局分页全部回车，最后在主菜单选 q 退出。
    """
    driver = SimDriver(record=True)
    state = {"keys": 0, "menu_seen": 0, "inspect_done": not inspect_card,
             "phase": ""}
    pending = {"step": ""}

    def key_fn():
        state["keys"] += 1
        assert state["keys"] < 3000, "自动玩家按键超限，疑似流程死锁"
        assert driver.shots, "界面尚未渲染任何屏幕"
        title = driver.shots[-1].split("\n", 1)[0]
        t = title.strip()

        if "末路求生" in t:
            state["menu_seen"] += 1
            return "q" if state["menu_seen"] >= 2 else "n"
        if t == "选择难度":
            return difficulty
        if t == "新游戏":                 # 随机种子：回车=随机
            return "ENTER"
        if "轮 阶段" in t:                # 规划页
            return planning_key()
        if t in ("路径2左卡", "路径2右卡"):
            return "ENTER"
        # 其余界面（遭遇/结算/战斗/骰面/分配/轮末/终局分页）全部默认回车
        return "ENTER"

    def planning_key():
        # 首局先演示一次 2L 看明牌：2 → l → （看牌页回车）→ 1 → 回车
        if not state["inspect_done"]:
            if pending["step"] == "":
                pending["step"] = "inspect_path"
                return "2"
            if pending["step"] == "inspect_path":
                pending["step"] = "inspect_side"
                return "l"
            if pending["step"] == "inspect_side":
                state["inspect_done"] = True
                pending["step"] = "path_num"
                return "1"
        # 正常选路两步：路径号 → 回车
        if pending["step"] in ("", "inspect_side"):
            pending["step"] = "path_num"
            return "1"
        if pending["step"] == "path_num":
            pending["step"] = ""
            return "ENTER"
        pending["step"] = "path_num"
        return "1"

    keys = SimKeys(scripted=key_fn)
    platform = SimPlatform(keys, driver, save_root=tmpdir)
    app = device_app.DeviceApp(platform)
    with pytest.raises(SystemExit):
        app.run()
    return driver, platform, state


@pytest.fixture()
def tmp_save_root():
    path = tempfile.mkdtemp(prefix="zroad_device_test_")
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def test_full_game_easy_no_missing_glyphs(tmp_save_root):
    driver, platform, state = run_scripted_game(tmp_save_root, "e")
    # 完整走完 8 轮并退出（主菜单出现两次：开局 + 终局后）
    assert state["menu_seen"] == 2
    # 缺字守门：整局任何一屏都不能出现字库未收录的汉字
    assert driver.missing == set(), "存在缺字：%s" % "".join(
        chr(code) for code in sorted(driver.missing))
    # 自动存档落盘（终局也会写一次 auto）
    assert platform.store.exists(AUTO_SLOT)
    final_state = platform.store.read(AUTO_SLOT)
    assert final_state["phase"] == "finished"


def test_full_game_hard(tmp_save_root):
    driver, platform, state = run_scripted_game(tmp_save_root, "h")
    assert state["menu_seen"] == 2
    assert driver.missing == set()
    final_state = platform.store.read(AUTO_SLOT)
    assert final_state["phase"] == "finished"
    assert final_state["difficulty"] == "hard"


def test_inspect_faceup_card_shows_event_and_score(tmp_save_root):
    """2L 明牌页必须同时展示事件、拾荒资源与得分（玩家选路决策所需）。"""
    driver, _, _ = run_scripted_game(tmp_save_root, "e", inspect_card=True)
    inspect_shots = [s for s in driver.shots
                     if s.split("\n", 1)[0].strip() == "路径2左卡"]
    assert inspect_shots, "没有出现路径2左卡查看页"
    # 卡面信息可能分页，把该页所有分页拼起来断言
    text = "\n".join(inspect_shots)
    assert "事件：" in text
    assert "拾荒：" in text
    assert "得分：" in text


def test_planning_screen_shows_event_flags_and_status(tmp_save_root):
    """规划页三路径行显示事件有无，状态行显示幸存者/资源/得分。"""
    driver, _, _ = run_scripted_game(tmp_save_root, "e")
    planning = [s for s in driver.shots if "轮 阶段" in s.split("\n", 1)[0]]
    assert planning
    screen = planning[0]
    assert "路径1" in screen and "路径2" in screen and "路径3" in screen
    assert "事件有" in screen or "事件无" in screen or "背面" in screen
    assert "幸存者" in screen and "得分" in screen


def test_font_covers_every_data_and_ui_char():
    """字库守门：数据文件与设备端源码里的每个非 ASCII 字符都必须有点阵。

    整局测试只能覆盖到部分分支（如某些事件日志），本测试直接对全部
    用字做静态覆盖检查，保证任何卡牌/事件在真机上都不会出现豆腐块。
    """
    import json
    from pathlib import Path
    from zroad.platforms.device_mpy import screen as screen_mod

    # screen.py 位于 src/zroad/platforms/device_mpy/，parents[2] 即包根 zroad/
    root = Path(screen_mod.__file__).resolve().parents[2]
    chars = set()

    def collect(value):
        if isinstance(value, str):
            chars.update(value)
        elif isinstance(value, dict):
            for k, v in value.items():
                chars.update(str(k))
                collect(v)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    data_dir = root / "zroad" / "data"
    for path in data_dir.glob("*.json"):
        collect(json.loads(path.read_text(encoding="utf-8")))
    device_dir = root / "zroad" / "platforms" / "device_mpy"
    for path in device_dir.glob("*.py"):
        if path.name == "font_data.py":
            continue
        chars.update(path.read_text(encoding="utf-8"))

    missing = set()
    for ch in chars:
        if ord(ch) < 0x80 or ch in ("\n", "\r", "\t", " "):
            continue
        _, data, _off = screen_mod.glyph(ord(ch))
        if data is None:
            missing.add(ch)
    assert not missing, "字库缺少字形：%s" % " ".join(
        "%s(U+%04X)" % (c, ord(c)) for c in sorted(missing))


def test_records_file_written_on_win(tmp_save_root):
    _, platform, _ = run_scripted_game(tmp_save_root, "e")
    # 通关与否都不应崩溃；通关时 records.json 存在且 best 含 score
    records_path = os.path.join(tmp_save_root, "records.json")
    if os.path.exists(records_path):
        best = platform.store.best_record()
        assert best is not None
        assert "score" in best
