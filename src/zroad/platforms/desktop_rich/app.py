"""app.py —— Mac 终端 Rich 前端主程序（M5：首个可完整游玩的版本）。

调用关系（依赖方向永远单向：app → core，core 不知道 Rich 的存在）：

    主菜单（新游戏/继续/读档/退出）
      → GameApp.play 主循环，按引擎状态机分发：
          planning   do_planning   展示三路径、收集资源分配、选路
          encounter  do_encounter  逐张卡：拾荒/事件（含抉择）/战斗
          round_end  do_round_end  自动存档 + 可选手动存档，进入下一轮
          finished   show_final    计分、评级、局后统计

运行方式：
    python -m zroad                                    # 安装后（pyproject 脚本 zroad）
    python -m zroad.platforms.desktop_rich.app        # 直接运行
"""

import json
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich import box
from rich.table import Table

from zroad.core.engine import Engine
from zroad.core.model import EngineError
from zroad.core import effects as fx
from zroad.core.constants import NORMAL_FACE_NAMES, RESOURCE_KEYS
from .save_store import (SaveStore, AUTO_SLOT, MANUAL_SLOTS, SLOT_LABELS)
from . import render

# 仓库根与数据目录（本文件位于 src/zroad/platforms/desktop_rich/，上溯 4 级）
REPO_ROOT = Path(__file__).resolve().parents[4]
DATA_DIR = REPO_ROOT / "data"


def load_game_data():
    """读取卡牌与规则配置（桌面层做文件 I/O，core 不碰文件）。"""
    cards = json.loads((DATA_DIR / "cards.json").read_text(encoding="utf-8"))
    config = json.loads((DATA_DIR / "config.json").read_text(encoding="utf-8"))
    return cards, config


class GameApp(object):
    """终端应用控制器：持有 Console、存档仓库与静态数据。"""

    def __init__(self):
        self.console = Console()
        self.store = SaveStore()
        self.cards, self.config = load_game_data()
        self.catalog = {c["id"]: c for c in self.cards}
        self.total_rounds = self.config["setup"]["total_rounds"]

    # ---------- 通用输入辅助 ----------
    def pause(self, prompt="按回车继续…"):
        self.console.input("[dim]%s[/dim]" % prompt)

    def ask_menu(self, prompt, options):
        """数字/字母菜单。options: [(key, label, 是否可用)]，返回 key。"""
        valid = {}
        self.console.print()
        for key, label, enabled in options:
            valid[key] = enabled
            if enabled:
                self.console.print("  [bold][%s][/bold] %s" % (key, label))
            else:
                self.console.print("  [dim red][%s] %s（不可用）[/dim red]"
                                   % (key, label))
        keys = [k for k, _, ok in options if ok]
        while True:
            choice = Prompt.ask(prompt, choices=keys, show_choices=False)
            if valid.get(choice):
                return choice
            self.console.print("[red]该选项当前不可用[/red]")

    def ask_distribution(self, total, player, paying):
        """让玩家把 total 个“任意资源”分配到三种资源上。

        paying=True 时是支付（每种不能超过持有量）；False 时是领取（无上限）。
        返回 {"ammo":a,"gas":g,"meds":m}，三者之和恰为 total。
        """
        verb = "支付" if paying else "领取"
        while True:
            dist = {}
            remain = total
            for i, key in enumerate(RESOURCE_KEYS):
                upper = (min(remain, player.resources.get(key))
                         if paying else remain)
                is_last = i == len(RESOURCE_KEYS) - 1
                # 最后一种自动补齐；已无可分配额度时自动记 0，不再追问
                if is_last or remain == 0 or (paying and upper == 0):
                    amount = 0 if (remain == 0 or (paying and upper == 0)) else remain
                    if amount:
                        self.console.print("  %s自动分配 %s %d 个"
                                           % (render.RES_NAMES[key], verb, amount))
                else:
                    amount = self._ask_int(
                        "%s分配几个给%s（0-%d，剩余 %d）"
                        % (verb, render.RES_NAMES[key], upper, remain),
                        0, upper)
                dist[key] = amount
                remain -= amount
            if sum(dist.values()) == total:
                return dist
            self.console.print("[red]分配总数必须为 %d，请重来[/red]" % total)

    @staticmethod
    def _ask_int(prompt, low, high, default=None):
        while True:
            raw = Prompt.ask(prompt, default=str(default) if default is not None else None)
            if raw is None or str(raw).strip() == "":
                self.console.print("请输入数字")
                continue
            try:
                value = int(raw)
            except (TypeError, ValueError):
                self.console.print("请输入数字")
                continue
            if low <= value <= high:
                return value
            print("请输入 %d 到 %d 之间的数" % (low, high))

    # ---------- 主菜单 ----------
    def run(self):
        self.console.print(Panel.fit("[bold]亡命之途 · 文字版[/bold]  v0.6",
                                     border_style="magenta"))
        while True:
            try:
                auto_ok = self.store.exists(AUTO_SLOT)
                choice = self.ask_menu("主菜单", [
                    ("n", "新游戏", True),
                    ("c", "继续自动存档", auto_ok),
                    ("l", "读取存档", True),
                    ("q", "退出", True),
                ])
                if choice == "n":
                    self.new_game()
                elif choice == "c":
                    self.load_slot(AUTO_SLOT)
                elif choice == "l":
                    self.load_menu()
                else:
                    self.console.print("再会，路上小心。")
                    return
            except _ReturnToMenu:
                self.console.print("[dim]已保存并返回主菜单[/dim]")
                continue

    def new_game(self):
        raw = Prompt.ask("输入随机种子（整数，可复现；直接回车为随机）",
                         default="")
        seed = None
        if raw.strip():
            try:
                seed = int(raw.strip())
            except ValueError:
                self.console.print("[yellow]种子不是整数，改用随机[/yellow]")
        engine = Engine.new_solo(self.cards, self.config, seed=seed)
        self.play(engine)

    def load_menu(self):
        previews = self.store.list_all()
        options = []
        for p in previews:
            if p is None:
                continue
            finished = p["phase"] == "finished"
            label = "%s ｜ 第%d轮 ｜ %d人 ｜ 已赢%d张 ｜ %s%s" % (
                p["label"], p["round_no"], p["survivors"], p["won_count"],
                p["saved_at"], "（已结算）" if finished else "")
            options.append((p["slot"], label, not finished))
        options.append(("b", "返回主菜单", True))
        choice = self.ask_menu("选择存档", options)
        if choice != "b":
            self.load_slot(choice)

    def load_slot(self, slot):
        state = self.store.read(slot)
        engine = Engine.restore(self.cards, self.config, state)
        self.console.print("[green]已读取 %s[/green]" % SLOT_LABELS.get(slot, slot))
        self.play(engine)

    # ---------- 对局主循环 ----------
    def play(self, engine):
        while not engine.is_finished():
            if engine.state.phase == "planning":
                self.do_planning(engine)
            elif engine.state.phase == "encounter":
                self.do_encounter(engine)
            elif engine.state.phase == "round_end":
                self.do_round_end(engine)
        self.show_final(engine)

    # ---------- 规划选路 ----------
    def do_planning(self, engine):
        player = engine.state.player
        self.console.clear()
        self.console.print(render.status_panel(
            player, engine.state.round_no, engine.current_stage(),
            self.total_rounds))
        options = engine.state.current_options
        flags = {opt.index: engine.path_is_affordable(opt) for opt in options}
        self.console.print(render.path_options_table(options, self.catalog, flags))
        choice = self.ask_menu("选择路径", [
            ("1", "路径一（全暗，选前 +2 任意资源）", True),
            ("2", "路径二（左明右暗，正常）", True),
            ("3", "路径三（全明，选前 -2 任意资源）", flags.get(3, True)),
        ])
        path_index = int(choice)
        option = engine._find_option(path_index)
        dist = None
        if option.bonus > 0:
            self.console.print("[green]路径一奖励：领取 2 个任意资源[/green]")
            dist = self.ask_distribution(option.bonus, player, paying=False)
        elif option.cost > 0:
            self.console.print("[yellow]路径三代价：支付 2 个任意资源[/yellow]")
            dist = self.ask_distribution(option.cost, player, paying=True)
        engine.choose_path(path_index, dist)

    # ---------- 遭遇卡 ----------
    def make_decision(self, needed, player):
        """把引擎要求的玩家决策翻译成终端问答。"""
        if needed is None:
            return None
        dtype = needed["type"]
        if dtype == fx.DECISION_CHOOSE_RESOURCE:
            options = [(key, render.RES_NAMES[key], True)
                       for key in needed["available"]]
            key = self.ask_menu("选择损失哪种资源", options)
            return {"resource": key}
        if dtype == fx.DECISION_PAY_GAS:
            hi = min(needed["required"], needed["available_gas"])
            self.console.print("需要 %d 汽油，持有 %d"
                               % (needed["required"], needed["available_gas"]))
            pay = self._ask_int("支付多少汽油（每少 1 损失 1 人）", 0, hi,
                                default=hi)
            return {"gas_pay": pay}
        if dtype == fx.DECISION_SURVIVOR_UPKEEP:
            if needed["can_keep"]:
                keep = Prompt.ask("支付 %d 弹药让伙伴继续同行？(y/n)"
                                  % needed["ammo"], choices=["y", "n"],
                                  default="y")
                return {"keep": keep == "y"}
            self.console.print("[yellow]弹药不足，伙伴离队[/yellow]")
            return {"keep": False}
        return None

    def do_encounter(self, engine):
        card_id = engine.current_encounter_card()
        card = self.catalog[card_id]
        player = engine.state.player
        self.console.clear()
        self.console.print(render.status_panel(
            player, engine.state.round_no, engine.current_stage(),
            self.total_rounds))
        self.console.print(render.card_panel(card, title="遭遇卡 %s" % card_id))
        self.pause("查看卡面后按回车结算…")

        needed = engine.peek_card_decision()
        decision = self.make_decision(needed, player)
        outcome = engine.begin_card_resolution(decision)

        if outcome["scavenge"]:
            gain = "、".join("%s+%d" % (render.RES_NAMES[k], v)
                             for k, v in outcome["scavenge"].items())
            self.console.print("[green]拾荒：%s[/green]" % gain)
        effect = outcome.get("effect")
        if effect and effect.get("logs"):
            for line in effect["logs"]:
                self.console.print("[yellow]事件：%s[/yellow]" % line)

        if outcome.get("combat"):
            self.do_combat(engine)
        if engine.state.player.is_eliminated():
            return
        self.pause()

    # ---------- 战斗 ----------
    def do_combat(self, engine):
        pending = engine.state.pending_combat
        card_id = pending["card_id"]
        self.console.print(Panel(
            "遭遇丧尸 %d 只（剩余 %d）｜屍群等级 %d%s%s%s"
            % (pending["zombies_count"], pending["zombies_left"],
               pending["zombies_level"],
               "｜禁药剂" if pending["no_meds"] else "",
               "｜禁逃跑" if pending["no_flee"] else "",
               "｜远程咬伤会增敌" if pending["ranged_bite_adds_zombie"] else ""),
            title="战斗", border_style="red"))

        while engine.state.pending_combat is not None:
            actions = {a["action"]: a for a in engine.combat_actions()}
            menu = [("m", "近战（每人 1 骰）", "melee" in actions)]
            menu.insert(0, ("r", "远程攻击（耗弹药）", "ranged" in actions))
            menu.append(("f", "逃跑（耗 2 汽油、弃牌）", "flee" in actions))
            choice = self.ask_menu("选择行动", menu)

            if choice == "r":
                info = actions["ranged"]
                burst = self._ask_int(
                    "用几个弹药（每个掷 2 颗普通骰，最多 %d）"
                    % info["max_burst"], 1, info["max_burst"], default=1)
                result = engine.combat_ranged(burst)
                names = "、".join(NORMAL_FACE_NAMES[f] for f in result["faces"])
                self.console.print("远程骰面：%s ｜ 击杀 %d%s"
                                   % (names, result["kills"],
                                      ("｜咬伤增敌 %d" % result["zombies_added"])
                                      if result["zombies_added"] else ""))
                if result.get("result") == "won":
                    self._combat_won(card_id)
                    return
            elif choice == "f":
                engine.combat_flee()
                self.console.print("[dim]你支付汽油逃离，本牌不得分[/dim]")
                return
            else:
                view = engine.combat_roll_melee()
                self.console.print(render.dice_results_table(view["entries"]))
                idxs = self._choose_meds(view)
                result = engine.combat_resolve_melee(idxs)
                self.console.print("本批：击杀 %d，损失 %d 人，剩余丧尸 %d，剩余 %d 人"
                                   % (result["kills"], result["survivors_lost"],
                                      result["zombies_left"], result["survivors"]))
                if result["result"] == "won":
                    self._combat_won(card_id)
                    return
                if result["result"] == "lost":
                    self.console.print("[bold red]全队覆没……[/bold red]")
                    return

    def _choose_meds(self, view):
        """让玩家在哪些机会骰上花药剂，返回 entry idx 列表。"""
        opportunities = [o for o in view["opportunities"]]
        if not opportunities:
            return []
        if view["no_meds"]:
            self.console.print("[red]本场不能使用药剂[/red]")
            return []
        lines = {o["idx"]: "%d号骰 %s（%s）"
                 % (o["idx"] + 1, o["face_name"],
                    "可用药剂" if o["available"] else "药剂不足")
                 for o in opportunities}
        for o in opportunities:
            if o["available"]:
                self.console.print("  %s" % lines[o["idx"]])
            else:
                self.console.print("  [dim red]%s[/dim red]" % lines[o["idx"]])
        raw = Prompt.ask("输入要使用药剂的骰号（逗号分隔，直接回车不用）",
                         default="")
        idxs = []
        for token in raw.replace("，", ",").split(","):
            token = token.strip()
            if not token:
                continue
            try:
                local = int(token) - 1
            except ValueError:
                continue
            if 0 <= local < len(view["entries"]):
                idxs.append(view["entries"][local]["idx"])
        # 只保留真正是机会的骰面；药剂是否够用由引擎强校验并提示
        valid_ids = {o["idx"] for o in opportunities if o["available"]}
        return [i for i in idxs if i in valid_ids]

    def _combat_won(self, card_id):
        self.console.print("[bold green]战斗胜利，赢得卡牌 %s！[/bold green]"
                           % card_id)

    # ---------- 轮末 ----------
    def do_round_end(self, engine):
        path = self.store.write(AUTO_SLOT, engine)
        self.console.print("[dim]已自动存档 → %s[/dim]" % path.name)
        choice = self.ask_menu("轮末", [
            ("c", "进入下一轮", True),
            ("s", "保存到手动槽", True),
            ("q", "保存并退回主菜单", True),
        ])
        if choice == "s":
            slot = self.ask_menu("选择手动槽",
                                 [(s, SLOT_LABELS[s], True) for s in MANUAL_SLOTS])
            if self.store.exists(slot):
                ok = Prompt.ask("该槽已有存档，覆盖？(y/n)", choices=["y", "n"],
                                default="y")
                if ok != "y":
                    return self.do_round_end(engine)
            self.store.write(slot, engine)
            self.console.print("[green]已保存到 %s[/green]" % SLOT_LABELS[slot])
            return self.do_round_end(engine)
        if choice == "q":
            # 直接抛控制信号回到主菜单：用异常最简洁
            raise _ReturnToMenu()
        engine.close_round()

    # ---------- 终局 ----------
    def show_final(self, engine):
        self.console.clear()
        report = engine.final_report()
        player = engine.state.player
        if report["eliminated"]:
            self.console.print(Panel("[bold red]队伍全灭，旅程在此终结……[/bold red]",
                                     border_style="red"))
        else:
            self.console.print(Panel("[bold green]你抵达了终点！[/bold green]",
                                     border_style="green"))
        self.console.print(render.score_report_table(report))
        stats = engine.state.stats
        stat_table = Table(box=box.SIMPLE)
        stat_table.add_column("局后统计", style="cyan")
        stat_table.add_column("数值", justify="right")
        stat_table.add_row("战斗场次", str(stats["combats"]))
        stat_table.add_row("击杀丧尸", str(stats["zombies_killed"]))
        stat_table.add_row("损失幸存者", str(stats["survivors_lost"]))
        stat_table.add_row("逃跑次数", str(stats["fled"]))
        self.console.print(stat_table)
        self.store.write(AUTO_SLOT, engine)  # 终局也落盘，便于回看
        self.pause("按回车回到主菜单…")


class _ReturnToMenu(Exception):
    """内部信号：玩家在轮末选择退回主菜单。"""


def main():
    app = GameApp()
    try:
        app.run()
    except KeyboardInterrupt:
        app.console.print("\n已退出。")
    except EngineError as exc:
        app.console.print("[bold red]规则错误：%s[/bold red]" % exc)


if __name__ == "__main__":
    main()
