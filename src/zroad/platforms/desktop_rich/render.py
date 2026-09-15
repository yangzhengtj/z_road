"""render.py —— 桌面端的 Rich 渲染组件（只负责“长什么样”）。

把游戏状态转成 Rich 的 Panel/Table，全部是纯展示函数，不修改任何状态，
方便以后整体替换成 Cardputer 的屏幕绘制（届时重写这一层即可，core 不动）。
"""

from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

# 资源键 → 中文名（界面统一从这里取，避免散落字面量）
RES_NAMES = {"ammo": "弹药", "gas": "汽油", "meds": "药剂"}
STAGE_NAMES = {1: "阶段 I", 2: "阶段 II", 3: "阶段 III"}


def resources_text(player):
    """一行展示三种资源余量。"""
    r = player.resources
    return "弹药 %d ｜ 汽油 %d ｜ 药剂 %d" % (r.ammo, r.gas, r.meds)


def status_panel(player, round_no, stage, total_rounds=8, score=0,
                 difficulty_label=None):
    """顶部状态面板：轮次、人数、资源、道具、当前累计得分、难度。"""
    title_text = "第 %d/%d 轮 · %s" % (round_no, total_rounds,
                                       STAGE_NAMES.get(stage, str(stage)))
    if difficulty_label:
        title_text += " · 难度%s" % difficulty_label
    title = title_text
    items = "、".join(player.special_items) if player.special_items else "无"
    body = Text()
    body.append("幸存者 ", style="bold")
    body.append("%d 人" % player.survivors, style="bold green")
    body.append("    %s" % resources_text(player))
    body.append("\n特殊道具：%s" % items)
    body.append("\n得分：%d" % score, style="bold yellow")
    return Panel(body, title=title, border_style="cyan")


def _scavenge_text(card):
    s = card["scavenge"]
    parts = []
    for key in ("ammo", "gas", "meds"):
        if s.get(key, 0) > 0:
            parts.append("%s+%d" % (RES_NAMES[key], s[key]))
    return "、".join(parts) if parts else "无"


def card_panel(card, title=None):
    """一张遭遇卡的完整信息面板。"""
    table = Table.grid(padding=(0, 2))
    table.add_column(style="dim", justify="right")
    table.add_column()
    table.add_row("场景", card["scene"])
    table.add_row("拾荒", _scavenge_text(card))
    # 遭遇界面展示玩家面事件（event_text）；开发者结算说明不外露
    event = card.get("event_text", "无")
    event = event if event != "无" else "—"
    table.add_row("事件", event)
    z = card["zombies"]
    if z["count"] > 0:
        zombie = "丧尸 %d 只" % z["count"]
        if z["level"] > 0:
            zombie += "（屍群等级 %d：%d 颗强化骰）" % (z["level"], z["level"])
        table.add_row("战斗", zombie)
    else:
        table.add_row("战斗", "无")
    if card.get("score", 0) > 0:
        table.add_row("得分", "%d 分" % card["score"])
    return Panel(table, title=title or card["id"], border_style="yellow")


def path_options_table(options, catalog, affordable_flags=None):
    """规划阶段三条路径一览表（面朝下的卡只显示？？）。"""
    affordable_flags = affordable_flags or {}
    table = Table(box=box.SIMPLE_HEAVY, expand=False)
    table.add_column("路径", justify="center", style="bold cyan")
    table.add_column("左卡")
    table.add_column("右卡")
    table.add_column("选前效果")
    for opt in options:
        cells = []
        for pos in range(2):
            card_id = opt.visible_card_id(pos, catalog)
            if card_id is None:
                cells.append(Text("？？（背面）", style="dim"))
            else:
                card = catalog[card_id]
                # 明牌不显示编号，只显示“事件：有/无”，详情用 2L/3R 命令查看
                if card.get("event_text", "无") == "无":
                    cells.append(Text("事件：无", style="green"))
                else:
                    cells.append(Text("事件：有", style="yellow"))
        # 选前效果按该路径实际奖惩生成（数值随难度变化，不能写死）
        if opt.bonus > 0:
            hint = "奖励：+%d 任意资源" % opt.bonus
        elif opt.cost > 0:
            hint = "代价：-%d 任意资源" % opt.cost
        else:
            hint = "无奖惩"
        if not affordable_flags.get(opt.index, True):
            hint = Text("%s（资源不足，不可选）" % hint, style="bold red")
        table.add_row(str(opt.index), cells[0], cells[1], hint)
    return table


def card_inspect_panel(card, where):
    """选路时用 2L/3R 等命令查看明牌：拾荒资源、事件、战斗、得分。"""
    table = Table.grid(padding=(0, 2))
    table.add_column(style="dim", justify="right")
    table.add_column()
    table.add_row("位置", where)
    table.add_row("拾荒", _scavenge_text(card))
    table.add_row("事件", card.get("event_text", "无"))
    z = card["zombies"]
    if z["count"] > 0:
        zombie = "丧尸 %d 只" % z["count"]
        if z["level"] > 0:
            zombie += "（屍群等级 %d）" % z["level"]
        table.add_row("战斗", zombie)
    else:
        table.add_row("战斗", "无")
    table.add_row("得分", "%d 分" % card.get("score", 0))
    return Panel(table, title="卡牌详情", border_style="bright_blue")


def dice_results_table(entries):
    """近战一批骰子的结果表。"""
    table = Table(box=box.SIMPLE)
    table.add_column("#", justify="center")
    table.add_column("骰种")
    table.add_column("点数", justify="center")
    table.add_column("骰面")
    table.add_column("药剂处理")
    kind_name = {"normal": "普通", "enhanced": "强化"}
    opp_text = {"wait": "可付药剂反杀", "extra": "可付药剂多杀 1",
                "bite": "可付药剂救人"}
    for e in entries:
        meds = ""
        if e["use_meds"]:
            meds = Text("已用药剂", style="bold green")
        elif e["opportunity"]:
            meds = Text(opp_text[e["opportunity"]], style="yellow")
        style = "red" if e["kind"] == "enhanced" else None
        table.add_row(str(e["idx"] + 1), kind_name[e["kind"]],
                      str(e["face"]), e["name"], meds, style=style)
    return table


def score_report_table(report):
    """终局分数明细表。"""
    table = Table(box=box.SIMPLE_HEAVY)
    table.add_column("项目", style="cyan")
    table.add_column("数值", justify="right")
    table.add_row("赢得卡牌", "%d 张" % report["won_count"])
    table.add_row("卡牌得分", "%d" % report["card_score"])
    table.add_row("事件额外分", "%d" % report["bonus_score"])
    table.add_row("完整套装", "%d 套（每套 +1）" % report["sets"])
    table.add_row("套装分", "%d" % report["set_score"])
    total = Text("%d" % report["total"], style="bold green")
    table.add_row(Text("总分", style="bold"), total)
    if report.get("rating"):
        table.add_row("评级",
                      Text(report["rating"]["label"], style="bold magenta"))
    return table
