"""device_app.py —— Cardputer 端游戏主逻辑（MicroPython / 模拟器共用）。

结构与桌面端 desktop_rich/app.py 一一对应，但所有交互都为 240×135 小屏
（30 半角列 × 8 行）重新设计：标题栏 + 6 行正文 + 底栏，长内容分页，
菜单支持“直接按快捷键”和“方向键选择 + 回车”两种操作。

本模块不 import 任何硬件相关库：屏幕驱动、按键、文件路径、json 实现都由
Platform 对象注入。mp_frontend.py 注入真机实现，sim.py 注入电脑端模拟实现，
因此整套对局流程可以在 Mac 上自动化测试。
"""

import gc

from .screen import (Screen, COLS, ROWS, BLACK, WHITE, RED, GREEN, YELLOW,
                     CYAN, MAGENTA, GRAY, BLUE, DARK_GREEN, wrap_text)
from .stores import JsonFileStore, AUTO_SLOT, MANUAL_SLOTS, SLOT_LABELS
from .card_catalog import StageCatalog
from . import font_data
from ...core.engine import Engine
from ...core.model import GameState
from ...core import effects as fx
from ...core.constants import NORMAL_FACE_NAMES, RESOURCE_KEYS, ITEM_BUS, \
    ITEM_VEHICLE_ARMOR

VERSION = "v0.8.1"

RES_FULL = {"ammo": "弹药", "gas": "汽油", "meds": "药剂"}
RES_SHORT = {"ammo": "弹", "gas": "油", "meds": "药"}
STAGE_ROMAN = {1: "I", 2: "II", 3: "III"}
CONTENT_ROWS = ROWS - 2   # 标题栏、底栏之外可用 6 行

HELP_LINES = [
    "主菜单：n 新游戏（先选难度）｜c 继续自动档",
    "        l 读取存档｜h 帮助｜q 退出回 Shell",
    "规划阶段：1/2/3 选择对应路径；",
    "  先按路径号再按 L/R 查看明牌，如 2L、3R",
    "遭遇阶段：回车逐张结算（拾荒→事件→战斗）",
    "战斗：r 远程（每场一次、仅近战前）",
    "      f 逃跑（仅近战前）｜m 近战",
    "      近战开始后本场只能继续近战",
    "      药剂机会：数字键切换、回车确认",
    "轮末：c 下一轮（自动存档）｜s 手动存档",
    "      q 存盘回主菜单",
    "翻页：FN+; / FN+.（方向键上下）",
    "规则：每阶段随机剔 4 张；每轮 6 张组 3 路径",
    "      选 1 条路、弃 4 张；校车+车辆铠甲同时持有",
    "      时，屍群强化骰才全部降级为普通骰。",
]


from ...core.rng import SeededRng

# 工作随机源在模块导入期就分配：此刻堆刚由解释器/模块顺序占用、
# 连续块最大，2.5KB 状态数组一定能分到；之后无论新局还是读档都复用
# 这一个实例（reseed/set_state 原地复用数组），运行期不再申请大块。
_WORK_RNG = SeededRng(0)


class ReturnToMenu(Exception):
    """轮末选择“保存并回主菜单”的控制信号。"""


class DeviceUI(object):
    """小屏交互组件：消息分页、菜单、数字输入。"""

    def __init__(self, driver, get_key):
        self.screen = Screen()
        self.driver = driver
        self.get_key = get_key

    # ---------- 基础绘制 ----------

    def _top(self, title, color=CYAN):
        self.screen.bar(0, color)
        self.screen.text(0, 0, title, fg=BLACK, bg=color)

    def _footer(self, text, color=GRAY):
        self.screen.bar(ROWS - 1, color)
        self.screen.text(0, ROWS - 1, text, fg=BLACK, bg=color)

    def _flatten(self, lines):
        """把 str 或 (文本,颜色) 的行列表折行成 (文本,颜色) 平面行。"""
        gc.collect()   # 折行会产生一批短命字符串，先回收腾连续块
        flat = []
        for item in lines:
            if isinstance(item, tuple):
                text, color = item
            else:
                text, color = item, WHITE
            try:
                wrapped_lines = wrap_text(text, COLS)
            except MemoryError:
                # 碎片化极端情况下折行中途失败：再回收一次后重试；
                # 仍失败则退化为“不折行、超长裁剪”，保证界面还能出来。
                gc.collect()
                try:
                    probe = bytearray(1280)
                    del probe
                    wrapped_lines = wrap_text(text, COLS)
                except MemoryError:
                    wrapped_lines = [text[:COLS]]
            for wrapped in wrapped_lines:
                flat.append((wrapped, color))
        return flat

    def message(self, title, lines, footer="回车继续", title_color=CYAN):
        """长文本分页浏览。回车翻到下一页/结束，上下方向键翻页。"""
        flat = self._flatten(lines)
        if not flat:
            flat =[("", WHITE)]
        pages = []
        for i in range(0, len(flat), CONTENT_ROWS):
            pages.append(flat[i:i + CONTENT_ROWS])
        page = 0
        while True:
            self.screen.clear()
            self._top(title, title_color)
            for i, (text, color) in enumerate(pages[page]):
                self.screen.text(0, 1 + i, text, fg=color)
            if len(pages) > 1:
                hint = "%d/%d  ↑↓翻页 回车继续" % (page + 1, len(pages))
            else:
                hint = footer
            self._footer(hint)
            self.screen.render(self.driver)
            key = self.get_key()
            if key == "ENTER":
                if page >= len(pages) - 1:
                    return
                page += 1
            elif key in ("UP",) and page > 0:
                page -= 1
            elif key in ("DOWN",) and page < len(pages) - 1:
                page += 1

    def menu(self, title, options, footer="快捷键或↑↓选择 回车确认",
             title_color=CYAN):
        """选单。options: [(key,label,enabled)]，返回选中的 key。

        超过 6 项自动分页；选项自身的快捷键（数字/字母）直接生效。
        """
        enabled_idx = [i for i, (_, _, ok) in enumerate(options) if ok]
        cursor = enabled_idx[0] if enabled_idx else 0
        scroll = 0
        while True:
            if cursor < scroll:
                scroll = cursor
            if cursor >= scroll + CONTENT_ROWS:
                scroll = cursor - CONTENT_ROWS + 1
            self.screen.clear()
            self._top(title, title_color)
            for row in range(CONTENT_ROWS):
                i = scroll + row
                if i >= len(options):
                    break
                key, label, enabled = options[i]
                prefix = ">" if i == cursor else " "
                color = WHITE if enabled else GRAY
                text = "%s %s（%s）" % (prefix, label, key)
                if not enabled:
                    text += " 不可用"
                self.screen.text(0, 1 + row, text, fg=color)
            page_count = (len(options) + CONTENT_ROWS - 1) // CONTENT_ROWS
            if page_count > 1:
                footer = "%d/%d  ↑↓翻页 回车确认" % (
                    scroll // CONTENT_ROWS + 1, page_count)
            self._footer(footer)
            self.screen.render(self.driver)

            key = self.get_key()
            if key == "UP":
                prev = [i for i in enabled_idx if i < cursor]
                if prev:
                    cursor = prev[-1]
            elif key == "DOWN":
                nxt = [i for i in enabled_idx if i > cursor]
                if nxt:
                    cursor = nxt[0]
            elif key == "ENTER":
                if options[cursor][2]:
                    return options[cursor][0]
            else:
                for i, (opt_key, _, enabled) in enumerate(options):
                    if opt_key == key and enabled:
                        return opt_key

    def ask_int(self, title, prompt, low, high, default):
        """整数输入：数字键录入、FN+BS(DEL) 删除、回车确认（空=默认值）。

        返回整数；按 ESC 取消返回 None。
        """
        value = ""
        while True:
            self.screen.clear()
            self._top(title, MAGENTA)
            self.screen.text_wrapped(0, 1, prompt, fg=YELLOW)
            shown = value if value else ("%d（默认）" % default)
            self.screen.text(0, 4, shown, fg=GREEN)
            self.screen.text(0, 5, "范围 %d-%d" % (low, high), fg=GRAY)
            self._footer("数字输入 DEL删除 回车确认")
            self.screen.render(self.driver)
            key = self.get_key()
            if key == "ENTER":
                number = int(value) if value else default
                if low <= number <= high:
                    return number
            elif key == "ESC":
                return None
            elif key == "BS" or key == "DEL":
                value = value[:-1]
            elif len(key) == 1 and "0" <= key <= "9" and len(value) < 10:
                value += key
                if int(value) > high:
                    value = value[:-1]


class DeviceApp(object):
    """设备端应用控制器，流程与桌面 GameApp 相同。"""

    def __init__(self, platform):
        self.p = platform
        # 随机源在模块导入期就预分配（见模块末尾的 _WORK_RNG）：那时堆
        # 还很整齐，2.5KB 连续状态块一定能分到；新局/读档都复用这一个
        # 实例（reseed/set_state 原地复用数组，运行期不再申请大块）。
        self.work_rng = _WORK_RNG
        self.ui = DeviceUI(platform.driver, platform.get_key)
        # 然后打开字库文件并预热常用汉字的点阵缓存：让文件对象和缓存
        # 槽位也分配在界面帧产生碎片之前，避免游戏中途连 32 字节都分不出。
        font_data.open_bin()
        font_data.warm("末路求生文字版新游戏读取存档退出选择难度简单困难"
                       "第轮阶段幸存者弹油药人得分道具路径左右卡遭遇丧尸战斗")
        gc.collect()
        # 配置与卡牌目录。迷你卡（60 张，约 10KB）刻意在“任何界面帧
        # 渲染之前”就预载完成：此时堆最整齐，常驻对象从底部连续排开，
        # 顶部能保留一大块连续空间。若拖到开局菜单之后再建，dict/字符串
        # 会落进界面帧回收后的碎片空洞，MicroPython 的 GC 不压缩堆，
        # 游戏中途最大连续块会被切碎到 1KB 以下而 MemoryError。
        self.config = platform.load_json("config.json")
        self.catalog = StageCatalog(platform, self.config)
        # 开机只预载阶段 I 迷你卡（16 张）；阶段 II/III 在切换时载、
        # 旧阶段在切换时释放，把常驻迷你卡压到最小（见 card_catalog）。
        self.catalog.preload_stage_minis(1)
        # 终局才用的两个模块也在开机时导入：代码对象同样是常驻内存，
        # 趁堆整齐一次性放好，避免终局时碎片堆里连模块都加载失败。
        from ...core import scoring as _scoring  # noqa: F401
        from ...core import statistics as _statistics  # noqa: F401
        gc.collect()
        self.total_rounds = self.config["setup"]["total_rounds"]
        self.store = platform.store

    # ---------- 文案小工具 ----------

    def _diff_label(self, engine):
        return engine.difficulty_label()

    def _scavenge_text(self, card):
        parts = []
        for key in ("ammo", "gas", "meds"):
            amount = card["scavenge"].get(key, 0)
            if amount > 0:
                parts.append("%s+%d" % (RES_FULL[key], amount))
        return "、".join(parts) if parts else "无"

    def _status_lines(self, engine):
        player = engine.state.player
        title = "第%d/%d轮 阶段%s %s" % (
            engine.state.round_no, self.total_rounds,
            STAGE_ROMAN.get(engine.current_stage(), "?"),
            self._diff_label(engine))
        line1 = "幸存者%d人 弹%d 油%d 药%d" % (
            player.survivors, player.resources.ammo,
            player.resources.gas, player.resources.meds)
        items = "、".join(player.special_items) if player.special_items else "无"
        line2 = "得分%d 道具:%s" % (engine.current_score(), items)
        return title, line1, line2

    def _card_lines(self, card):
        """一张卡的完整信息（遭遇页与选路看牌页共用）。"""
        lines = [("场景：" + card["scene"], WHITE)]
        lines.append(("拾荒：" + self._scavenge_text(card), GREEN))
        event = card.get("event_text", "无")
        lines.append(("事件：" + event, YELLOW if event != "无" else GRAY))
        z = card["zombies"]
        if z["count"] > 0:
            text = "战斗：丧尸%d只" % z["count"]
            if z["level"] > 0:
                text += "（屍群等级%d）" % z["level"]
            lines.append((text, RED))
        else:
            lines.append(("战斗：无", GRAY))
        lines.append(("得分：%d分" % card.get("score", 0), CYAN))
        return lines

    # ---------- 主菜单 ----------

    def run(self):
        while True:
            try:
                auto_ok = self.store.exists(AUTO_SLOT)
                choice = self.ui.menu(
                    "末路求生·文字版 " + VERSION, [
                        ("n", "新游戏", True),
                        ("c", "继续自动存档", auto_ok),
                        ("l", "读取存档", True),
                        ("h", "帮助", True),
                        ("q", "退出", True),
                    ],
                    footer="n新游戏 c继续 l读档 h帮助 q退出",
                    title_color=MAGENTA)
                if choice == "n":
                    self.new_game()
                elif choice == "c":
                    self.load_slot(AUTO_SLOT)
                elif choice == "l":
                    self.load_menu()
                elif choice == "h":
                    self.ui.message("帮助", HELP_LINES, title_color=MAGENTA)
                else:
                    self.ui.message("再会，路上小心", ["正在返回 Shell…"],
                                    footer="回车退出")
                    self.p.exit()
                    return
            except ReturnToMenu:
                continue

    def new_game(self):
        diff = self.ui.menu("选择难度", [
            ("e", "简单：路径1奖2，路径3付2", True),
            ("h", "困难：路径2付1，路径3付2", True),
        ], title_color=MAGENTA)
        difficulty = "easy" if diff == "e" else "hard"
        seed = self.ui.ask_int(
            "新游戏", "随机种子（直接回车=随机）", 0, 9999999999, 0)
        actual_seed = None if seed is None or seed == 0 else seed
        # 迷你卡开机时已预载，这里复用同一个目录实例（第二局也不重建），
        # 只重新洗牌 id。
        catalog = self.catalog
        self.work_rng.reseed(actual_seed)
        catalog.setup_new(self.work_rng)
        gc.collect()
        engine = Engine.new_solo(
            catalog, self.config, seed=actual_seed,
            difficulty=difficulty,
            prebuilt_decks=(catalog.active, catalog.removed),
            rng=self.work_rng)
        self.play(engine)

    def load_menu(self):
        options = []
        slot_by_key = {}
        key_map = {"auto": "a", "manual_1": "1", "manual_2": "2",
                   "manual_3": "3"}
        for preview in self.store.list_all():
            if preview is None:
                continue
            key = key_map[preview["slot"]]
            slot_by_key[key] = preview["slot"]
            finished = preview["phase"] == "finished"
            label = "%s 第%d轮 %d人 赢%d张" % (
                preview["label"], preview["round_no"], preview["survivors"],
                preview["won_count"])
            options.append((key, label, not finished))
        options.append(("b", "返回主菜单", True))
        choice = self.ui.menu("读取存档", options, title_color=BLUE)
        if choice != "b":
            self.load_slot(slot_by_key[choice])

    def load_slot(self, slot):
        state_dict = self.store.read(slot)
        catalog = self.catalog   # 迷你卡开机已预载，复用同一目录实例
        # catalog 需要 GameState 对象（取 stage_decks/removed_cards），
        # Engine.restore 接收原始 dict 自行反序列化，两者各取所需。
        state_obj = GameState.from_dict(state_dict)
        catalog.setup_restore(state_obj)
        # 传入同一个 state_obj：catalog 已借用其中的牌库列表，避免
        # Engine 再反序列化一份导致牌库 id 列表在堆里存两份。
        engine = Engine.restore(catalog, self.config, state_dict,
                                rng=self.work_rng, state_obj=state_obj)
        # play() 在本函数栈帧内执行，局部变量会一直占着堆：存档原始
        # dict（含 rng 回放序列，整局可达数 KB）必须先释放；state_obj
        # 已由 engine 持有，牌库列表由 catalog/engine 共享引用。
        del state_dict
        gc.collect()
        self.play(engine)

    # ---------- 对局主循环 ----------

    def play(self, engine):
        # scoring/statistics 已在 DeviceApp.__init__ 提前导入（代码对象
        # 也是常驻内存，必须在界面帧产生碎片之前放好）。
        while not engine.is_finished():
            # 每个阶段步开始前回收一次：引擎在本步分配的常驻对象
            # （统计、战斗记录、PathOption 等）要尽量落在干净堆上，
            # 不要插进上一屏界面帧的短命对象之间造成碎片。
            gc.collect()
            # 每轮确保只把当前阶段的卡牌留在内存里（StageCatalog 才有此方法）
            catalog = engine.catalog
            if hasattr(catalog, "ensure_stage"):
                catalog.ensure_stage(engine.current_stage(),
                                     engine.state.player.won_card_ids)
            phase = engine.state.phase
            # 阶段工作开始前释放连续储备块：本阶段的完整卡解析、长文本
            # 折行等较大短命分配使用这块连续内存；阶段结束再占回来。
            catalog.release_reserve()
            if phase == "planning":
                self.do_planning(engine)
            elif phase == "encounter":
                self.do_encounter(engine)
            elif phase == "round_end":
                self.do_round_end(engine)
            # 储备块释放后不再尝试占回：GC 不压缩，阶段结束时已分不出
            # 等大的连续块；各阶段大分配点另有 gc 重试兜底。
            catalog._reserve = None
        catalog.release_reserve()
        self.show_final(engine)

    # ---------- 规划选路 ----------

    def do_planning(self, engine):
        """规划选路。

        小屏按键是逐键返回的，桌面端“2L 一次输入”在这里拆成两步：
          先按路径号 1/2/3 → 底栏变为“回车选定 / L看左卡 / R看右卡”；
          回车确认选路，L/R 看该路径明牌，ESC 取消重选，数字键直接改选。
        """
        options = engine.state.current_options
        flags = {opt.index: engine.path_is_affordable(opt)
                 for opt in options}
        pending = 0
        while True:
            title, line1, line2 = self._status_lines(engine)
            self.ui.screen.clear()
            self.ui._top(title)
            self.ui.screen.text(0, 1, line1, fg=WHITE)
            used = self.ui.screen.text_wrapped(0, 2, line2, fg=CYAN)
            path_row = 2 + used   # 道具行可能折两行，路径行随之下移
            for i, opt in enumerate(options):
                tags = []
                for pos in range(2):
                    card_id = opt.visible_card_id(pos, self.catalog)
                    if card_id is None:
                        tags.append("背面")
                    else:
                        card = self.catalog[card_id]
                        tags.append("事件有" if card.get("event_text", "无") != "无"
                                    else "事件无")
                if opt.bonus > 0:
                    effect, color = "奖+%d" % opt.bonus, GREEN
                elif opt.cost > 0:
                    effect, color = "付%d" % opt.cost, YELLOW
                else:
                    effect, color = "无奖惩", GRAY
                if not flags.get(opt.index, True):
                    effect, color = "不足", RED
                prefix = ">" if pending == opt.index else " "
                text = "%s路径%d [%s][%s] %s" % (
                    prefix, opt.index, tags[0], tags[1], effect)
                self.ui.screen.text(0, path_row + i, text, fg=color)
            if pending:
                self.ui._footer("路径%d：回车选定 L左卡 R右卡 ESC重选"
                                % pending)
            else:
                self.ui._footer("1/2/3选路，再按L/R看明牌")
            self.ui.screen.render(self.ui.driver)

            key = self.ui.get_key()
            if key in ("1", "2", "3"):
                pending = int(key)
                continue
            if not pending:
                continue
            if key in ("l", "L"):
                self._inspect_path_card(options, pending, 0)
            elif key in ("r", "R"):
                self._inspect_path_card(options, pending, 1)
            elif key == "ESC":
                pending = 0
            elif key == "ENTER":
                if flags.get(pending, True):
                    path_index = pending
                    break
                self.ui.message("无法选择",
                                ["总资源不足，路径%d 不可选" % pending])
                pending = 0

        option = engine._find_option(path_index)
        dist = None
        if option.bonus > 0:
            dist = self._ask_distribution(option.bonus, engine.state.player,
                                          paying=False)
        elif option.cost > 0:
            dist = self._ask_distribution(option.cost, engine.state.player,
                                          paying=True)
        engine.choose_path(path_index, dist)
        # 规划阶段为看明牌升级过的完整卡，选路后立即全部降级：
        # 接下来遭遇时会按需逐张升级，控制完整卡同时驻留的数量。
        if hasattr(self.catalog, "release_stage_full"):
            self.catalog.release_stage_full(engine.current_stage())

    def _inspect_path_card(self, options, path_index, position):
        option = None
        for candidate in options:
            if candidate.index == path_index:
                option = candidate
                break
        if option is None:
            return
        card_id = option.visible_card_id(position, self.catalog)
        where = "路径%d%s卡" % (path_index, "左" if position == 0 else "右")
        if card_id is None:
            self.ui.message(where, ["该卡背面朝下，选择前无法查看"])
            return
        self.ui.message(where, self._card_lines(self.catalog[card_id]),
                        title_color=BLUE)

    def _ask_distribution(self, total, player, paying):
        """把 total 个任意资源分配到三种资源（设备版逐种数字输入）。"""
        verb = "支付" if paying else "领取"
        dist = {}
        remain = total
        for i, key in enumerate(RESOURCE_KEYS):
            upper = min(remain, player.resources.get(key)) if paying else remain
            is_last = i == len(RESOURCE_KEYS) - 1
            if is_last or remain == 0 or (paying and upper == 0):
                amount = 0 if (remain == 0 or (paying and upper == 0)) else remain
            else:
                amount = self.ui.ask_int(
                    verb + "分配",
                    "%s分配几个给%s（剩余%d）" % (verb, RES_FULL[key], remain),
                    0, upper, upper)
                if amount is None:
                    amount = 0
            dist[key] = amount
            remain -= amount
        return dist

    # ---------- 遭遇 ----------

    def make_decision(self, needed, player):
        if needed is None:
            return None
        dtype = needed["type"]
        if dtype == fx.DECISION_CHOOSE_RESOURCE:
            options = []
            key_map = {}
            for i, key in enumerate(needed["available"], start=1):
                hotkey = str(i)
                key_map[hotkey] = key
                options.append((hotkey, "损失%s" % RES_FULL[key], True))
            hotkey = self.ui.menu("选择损失哪种资源", options, title_color=RED)
            return {"resource": key_map[hotkey]}
        if dtype == fx.DECISION_PAY_GAS:
            hi = min(needed["required"], needed["available_gas"])
            pay = self.ui.ask_int(
                "汽油抉择",
                "需要%d汽油，持有%d；每少1损失1人"
                % (needed["required"], needed["available_gas"]),
                0, hi, hi)
            return {"gas_pay": pay if pay is not None else hi}
        if dtype == fx.DECISION_ZEALOT_UPKEEP:
            if needed["can_keep"]:
                choice = self.ui.menu("狂热者", [
                    ("y", "支付%d弹药，狂热者继续同行" % needed["ammo"], True),
                    ("n", "不支付，狂热者离队", True),
                ], title_color=YELLOW)
                return {"keep": choice == "y"}
            return {"keep": False}
        return None

    def do_encounter(self, engine):
        card_id = engine.current_encounter_card()
        card = self.catalog[card_id]
        player = engine.state.player
        armored = (player.has_item(ITEM_BUS)
                   and player.has_item(ITEM_VEHICLE_ARMOR))
        lines = self._card_lines(card)
        if card["zombies"]["level"] > 0 and armored:
            lines.append(("校车+车辆铠甲：强化骰全降级为普通骰", DARK_GREEN))
        self.ui.message("遭遇卡", lines, footer="回车结算这张卡",
                        title_color=YELLOW)

        needed = engine.peek_card_decision()
        decision = self.make_decision(needed, player)
        outcome = engine.begin_card_resolution(decision)

        messages = []
        if outcome["scavenge"]:
            gain = "、".join("%s+%d" % (RES_FULL[k], v)
                            for k, v in outcome["scavenge"].items())
            messages.append(("拾荒：" + gain, GREEN))
        effect = outcome.get("effect")
        if effect and effect.get("logs"):
            for line in effect["logs"]:
                messages.append(("事件：" + line, YELLOW))
        if outcome.get("combat"):
            # 战斗挂起信息已独立保存在引擎里，战斗过程不再需要卡面，
            # 先把完整卡降级，给战斗中的骰面/菜单文本腾出内存。
            if hasattr(self.catalog, "release_card"):
                self.catalog.release_card(card_id)
            self.do_combat(engine)
        if engine.state.player.is_eliminated():
            return
        if messages:
            self.ui.message("结算", messages)
        else:
            # 无战斗也无文字收益时给一个短暂停顿，保持节奏一致
            self.ui.message("结算", ["（这张卡没有额外结算内容）"])
        # 本卡遭遇流程全部结束，卡面不再需要，立即降回迷你卡
        if hasattr(self.catalog, "release_card"):
            self.catalog.release_card(card_id)

    # ---------- 战斗 ----------

    def _combat_lines(self, engine):
        pending = engine.state.pending_combat
        player = engine.state.player
        lines = [
            ("遭遇丧尸%d只（剩余%d）屍群等级%d" % (
                pending["zombies_count"], pending["zombies_left"],
                pending["zombies_level"]), RED),
            ("弹%d 油%d 药%d 人%d" % (
                player.resources.ammo, player.resources.gas,
                player.resources.meds, player.survivors), WHITE),
        ]
        if (pending["zombies_level"] > 0
                and player.has_item(ITEM_BUS)
                and player.has_item(ITEM_VEHICLE_ARMOR)):
            lines.append(("校车+车辆铠甲：强化骰全降级", DARK_GREEN))
        if pending["no_meds"]:
            lines.append(("本场禁用药剂", RED))
        if pending["no_flee"]:
            lines.append(("本场禁止逃跑", RED))
        if pending["ranged_bite_adds_zombie"]:
            lines.append(("远程咬伤会增加丧尸", YELLOW))
        return lines

    def do_combat(self, engine):
        card_id = engine.state.pending_combat["card_id"]
        while engine.state.pending_combat is not None:
            pending = engine.state.pending_combat
            actions = {a["action"]: a for a in engine.combat_actions()}
            melee_started = pending.get("melee_started", False)

            options = [("r", "远程攻击（1弹2骰）",
                        "ranged" in actions and not melee_started)]
            options.append(("m", "近战（每人1骰）", "melee" in actions))
            options.append(("f", "逃跑（2油弃牌）",
                            "flee" in actions and not melee_started))
            lines = self._combat_lines(engine)
            if melee_started:
                lines.append(("已进入近战：只能继续近战", GRAY))
            # 战斗状态与菜单合在一屏：状态占前几行，菜单从其后开始
            choice = self._inline_menu(lines, "战斗", options)

            if choice == "r":
                info = actions["ranged"]
                burst = self.ui.ask_int(
                    "远程攻击",
                    "用几个弹药（每个掷2颗普通骰，最多%d）"
                    % info["max_burst"], 1, info["max_burst"], 1)
                if burst is None:
                    burst = 1
                result = engine.combat_ranged(burst)
                names = "、".join(NORMAL_FACE_NAMES[f]
                                  for f in result["faces"])
                messages = [("远程骰面：%s" % names, WHITE),
                            ("击杀%d" % result["kills"], GREEN)]
                if result["zombies_added"]:
                    messages.append(("咬伤增敌%d" % result["zombies_added"],
                                     RED))
                self.ui.message("远程结果", messages, title_color=RED)
                if result.get("result") == "won":
                    self.ui.message("战斗胜利",
                                    ["赢得卡牌 %s" % card_id],
                                    title_color=GREEN)
                    return
            elif choice == "f":
                engine.combat_flee()
                self.ui.message("逃跑", ["支付汽油逃离，本牌不得分"])
                return
            else:
                view = engine.combat_roll_melee()
                idxs = self._choose_meds(view)
                result = engine.combat_resolve_melee(idxs)
                messages = [
                    ("本批击杀%d，损失%d人" % (
                        result["kills"], result["survivors_lost"]), WHITE),
                    ("剩余丧尸%d，剩余%d人" % (
                        result["zombies_left"], result["survivors"]), WHITE),
                ]
                if result["result"] == "won":
                    self.ui.message("近战结果", messages, title_color=GREEN)
                    self.ui.message("战斗胜利",
                                    ["赢得卡牌 %s" % card_id],
                                    title_color=GREEN)
                    return
                if result["result"] == "lost":
                    self.ui.message("近战结果", messages + [
                        ("全队覆没……", RED)], title_color=RED)
                    return
                self.ui.message("近战结果", messages)

    def _inline_menu(self, head_lines, title, options):
        """状态行 + 菜单同屏：状态行占上方，剩余行数显示选项。

        选项超过剩余行数时，只显示状态行与“回车打开行动菜单”，
        选择动作放到二级菜单（战斗状态最多 5 行，6 行正文放得下 3 个选项）。
        """
        flat = self.ui._flatten(head_lines)
        # 简化实现：先把状态分页看完，最后一页承载选项。
        per_page = CONTENT_ROWS
        # 保证最后一页至少留 1 行给菜单提示
        head_pages = []
        for i in range(0, max(1, len(flat)), per_page):
            head_pages.append(flat[i:i + per_page])
        # 战斗状态通常 2-5 行，与 3 个选项合计不超过 6 行时直接同屏
        if len(flat) + len(options) <= per_page:
            return self._draw_inline(flat, title, options)
        for page in head_pages[:-1]:
            self.ui.message(title, [t for t, _ in page])
        return self._draw_inline(head_pages[-1], title, options)

    def _draw_inline(self, flat, title, options):
        """在同一屏画状态行 + 选项并处理选择（方向键只在可选项间移动）。"""
        enabled = [j for j, (_, _, ok) in enumerate(options) if ok]
        cursor = enabled[0] if enabled else 0
        while True:
            self.ui.screen.clear()
            self.ui._top(title, RED)
            for i, (text, color) in enumerate(flat):
                self.ui.screen.text(0, 1 + i, text, fg=color)
            base = len(flat)
            for j, (key, label, item_enabled) in enumerate(options):
                row = base + j
                if row >= CONTENT_ROWS:
                    break
                prefix = ">" if cursor == j else " "
                color = WHITE if item_enabled else GRAY
                text = "%s %s（%s）" % (prefix, label, key)
                if not item_enabled:
                    text += " 不可用"
                self.ui.screen.text(0, 1 + row, text, fg=color)
            self.ui._footer("r远程 m近战 f逃跑 回车选定")
            self.ui.screen.render(self.ui.driver)
            key = self.ui.get_key()
            if key == "UP":
                prev = [j for j in enabled if j < cursor]
                if prev:
                    cursor = prev[-1]
            elif key == "DOWN":
                nxt = [j for j in enabled if j > cursor]
                if nxt:
                    cursor = nxt[0]
            elif key == "ENTER":
                if options[cursor][2]:
                    return options[cursor][0]
            else:
                for j, (opt_key, _, item_enabled) in enumerate(options):
                    if opt_key == key and item_enabled:
                        return opt_key

    def _choose_meds(self, view):
        """近战骰后选择在哪些机会骰上用药剂，返回引擎 entry idx 列表。

        骰面行 + 机会提示可能超过 6 行，因此做成可翻页的交互屏：
        数字键 1..5 切换该骰是否用药，上下翻页，回车确认。
        """
        opportunities = view["opportunities"]
        if not opportunities:
            self.ui.message("近战骰面", self._dice_lines(view, set()))
            return []
        if view["no_meds"]:
            self.ui.message("近战骰面",
                            self._dice_lines(view, set())
                            + [("本场不能使用药剂", RED)])
            return []
        available_by_no = {opp["idx"] + 1: opp["available"]
                           for opp in opportunities}
        selected = set()
        page = 0
        while True:
            flat = self.ui._flatten(self._dice_lines(view, selected))
            pages = []
            for i in range(0, len(flat), CONTENT_ROWS):
                pages.append(flat[i:i + CONTENT_ROWS])
            if page >= len(pages):
                page = len(pages) - 1
            self.ui.screen.clear()
            self.ui._top("近战骰面", RED)
            for i, (text, color) in enumerate(pages[page]):
                self.ui.screen.text(0, 1 + i, text, fg=color)
            if len(pages) > 1:
                self.ui._footer("%d/%d 数字切换 ↑↓翻页 回车"
                                % (page + 1, len(pages)))
            else:
                self.ui._footer("数字键切换药剂 回车确认")
            self.ui.screen.render(self.ui.driver)
            key = self.ui.get_key()
            if key == "ENTER":
                break
            if key == "UP" and page > 0:
                page -= 1
            elif key == "DOWN" and page < len(pages) - 1:
                page += 1
            elif len(key) == 1 and "1" <= key <= "9":
                number = int(key)
                if available_by_no.get(number):
                    if number in selected:
                        selected.discard(number)
                    else:
                        selected.add(number)
        return sorted(n - 1 for n in selected)

    def _dice_lines(self, view, selected):
        """骰面行：[*] 表示该骰决定花药剂；机会行注明用途/药剂不足。"""
        opp_text = {"wait": "可付药剂反杀", "extra": "可付药剂多杀1",
                    "bite": "可付药剂救人"}
        kind_name = {"normal": "普", "enhanced": "强"}
        lines = []
        for entry in view["entries"]:
            number = entry["idx"] + 1
            mark = "[*]" if number in selected else "   "
            text = "%s%d %s %d点 %s" % (
                mark, number, kind_name.get(entry["kind"], "?"),
                entry["face"], entry["name"])
            color = RED if entry["kind"] == "enhanced" else WHITE
            lines.append((text, color))
        for opp in view["opportunities"]:
            number = opp["idx"] + 1
            if not opp["available"]:
                lines.append(("%d号骰：药剂不足" % number, GRAY))
            elif number in selected:
                lines.append(("%d号骰：%s（已选）" % (
                    number, opp_text.get(opp["opportunity"], "")), GREEN))
            else:
                lines.append(("%d号骰：%s" % (
                    number, opp_text.get(opp["opportunity"], "")), YELLOW))
        return lines

    # ---------- 轮末 ----------

    def do_round_end(self, engine):
        self.store.write(AUTO_SLOT, engine)
        while True:
            choice = self.ui.menu("轮末（已自动存档）", [
                ("c", "进入下一轮", True),
                ("s", "保存到手动槽", True),
                ("q", "保存并退回主菜单", True),
            ], title_color=DARK_GREEN)
            if choice == "c":
                prev_stage = engine.current_stage()
                engine.close_round()
                # 同阶段相邻两轮：把上一轮摸过的完整卡降回迷你卡，
                # 使内存中完整卡始终不超过桌上的 6 张（跨阶段由
                # play() 循环开头的 ensure_stage 处理）。
                catalog = engine.catalog
                if hasattr(catalog, "release_stage_full") and \
                        engine.current_stage() == prev_stage and \
                        prev_stage is not None:
                    catalog.release_stage_full(prev_stage)
                return
            if choice == "q":
                raise ReturnToMenu()
            slot_choice = self.ui.menu("选择手动槽", [
                ("1", SLOT_LABELS["manual_1"], True),
                ("2", SLOT_LABELS["manual_2"], True),
                ("3", SLOT_LABELS["manual_3"], True),
                ("b", "返回", True),
            ], title_color=BLUE)
            if slot_choice == "b":
                continue
            slot = MANUAL_SLOTS[int(slot_choice) - 1]
            if self.store.exists(slot):
                ok = self.ui.menu("确认覆盖", [
                    ("y", "覆盖该存档", True),
                    ("n", "取消", True),
                ], title_color=RED)
                if ok != "y":
                    continue
            self.store.write(slot, engine)
            self.ui.message("已保存", ["已写入%s" % SLOT_LABELS[slot]])

    # ---------- 终局 ----------

    def show_final(self, engine):
        # 终局不再需要任何完整卡面，先全部降级；迷你卡也只保留玩家赢取
        # 的卡（计分/回看只访问它们），把当前阶段 22 张迷你卡的常驻
        # 内存让给结算文本、统计与终局存档序列化。
        if hasattr(self.catalog, "release_stage_full"):
            won_ids = engine.state.player.won_card_ids
            for stage in (1, 2, 3):
                if hasattr(self.catalog, "release_stage_minis"):
                    self.catalog.release_stage_minis(stage, won_ids)
                else:
                    self.catalog.release_stage_full(stage)
        gc.collect()
        report = engine.final_report()
        summary = engine.statistics_summary()
        lines = []
        if report["eliminated"]:
            lines.append(("队伍全灭，旅程在此终结……", RED))
        else:
            lines.append(("你抵达了终点！", GREEN))
        lines.append(("赢得卡牌%d张" % report["won_count"], WHITE))
        lines.append(("卡牌得分%d" % report["card_score"], WHITE))
        lines.append(("事件额外分%d" % report["bonus_score"], WHITE))
        lines.append(("完整套装%d套，套装分%d" % (
            report["sets"], report["set_score"]), WHITE))
        lines.append(("总分%d" % report["total"], GREEN))
        if report.get("rating"):
            lines.append(("评级：%s" % report["rating"]["label"], MAGENTA))
        self.ui.message("终局结算", lines, title_color=MAGENTA)

        # 历史最佳
        rating_label = report["rating"]["label"] if report.get("rating") else ""
        updated, best = self.store.record_result(
            report["total"], rating_label=rating_label,
            difficulty=engine.state.difficulty,
            eliminated=report["eliminated"], seed=engine.state.seed)
        if best is not None:
            if updated:
                self.ui.message("新纪录",
                                ["历史最佳%d分（%s）" % (
                                    best["score"], best.get("rating_label", ""))],
                                title_color=YELLOW)
            else:
                self.ui.message("历史最佳",
                                ["%d分（%s，%s）" % (
                                    best["score"], best.get("rating_label", ""),
                                    best.get("finished_at", ""))])

        # 分阶段统计
        stat_lines = []
        for row in summary["by_stage"]["rows"]:
            stat_lines.append("阶段%s 战%d 杀%d 损%d 逃%d" % (
                STAGE_ROMAN.get(row["stage"], "?"), row["combats"],
                row["zombies_killed"], row["survivors_lost"], row["fled"]))
        total = summary["by_stage"]["total"]
        stat_lines.append("合计 战%d 杀%d 损%d 逃%d" % (
            total["combats"], total["zombies_killed"],
            total["survivors_lost"], total["fled"]))
        self.ui.message("战斗统计", stat_lines, title_color=BLUE)

        ledger_lines = []
        for row in summary["resource_ledger"]:
            ledger_lines.append("%s 得%d 支%d 净%+d" % (
                row["name"], row["gained"], row["spent"], row["net"]))
        self.ui.message("资源收支", ledger_lines, title_color=BLUE)

        dice = summary["dice"]
        dice_lines = ["共掷%d颗" % dice["total"]]
        for kind in ("normal", "enhanced"):
            block = dice[kind]
            counts = " ".join("%s%d" % (f["name"][:1], f["count"])
                              for f in block["faces"])
            dice_lines.append(("%s骰(%d)：%s" % (
                "普通" if kind == "normal" else "强化", block["total"],
                counts), RED if kind == "enhanced" else WHITE))
        self.ui.message("骰面分布", dice_lines, title_color=BLUE)

        replay_lines = []
        for r in summary["rounds"]:
            ids = "、".join(c["id"] for c in r["chosen_cards"])
            replay_lines.append("%d %s 路径%d %s" % (
                r["round"], STAGE_ROMAN.get(r["stage"], "?"),
                r["chosen_path"], ids))
        self.ui.message("对局回看", replay_lines, title_color=BLUE)

        self.store.write(AUTO_SLOT, engine)
        self.ui.message("旅程结束", ["回车回到主菜单"])


def main(platform):
    """平台入口构造好 Platform 后调用本函数。"""
    app = DeviceApp(platform)
    app.run()
