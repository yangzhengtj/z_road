"""engine.py —— 对局状态机（M2 落地：开局设置、规划选路、轮次/阶段推进）。

引擎是“纯规则”的：它只读写 GameState、只通过 SeededRng 取随机，不 print、不 input。
所有与玩家的交互（例如“路径一奖励的 2 个资源要怎么分配”）都由调用方/前端决定后，
以普通参数传入，因此同一引擎可以接 Rich 终端、无头测试与将来的 Cardputer。

M2 覆盖的主循环（★为本次实现，☆为后续里程碑接入）：
    开局（5 人、资源各 4、分阶段洗牌剔 4）             ★
      → 每轮：摸 6 张组 3 路径（明暗+奖惩）           ★
            → 玩家选路（路径一 +2 任意 / 路径三 -2）  ★
            → 两张中选卡进入遭遇队列                  ★（队列流转，结算在 M3☆）
            → 未选 4 张弃置、每轮恰好消耗 6 张        ★
            → 轮次推进与阶段切换（2/3/3，共 8 轮）    ★
      → 8 轮结束进入 finished（计分评级在 M5☆）
"""

from .constants import (RESOURCE_KEYS, PHASE_INIT, PHASE_PLANNING,
                        PHASE_ENCOUNTER, PHASE_ROUND_END, PHASE_FINISHED,
                        MODE_SOLO, DIFFICULTY_EASY)
from .rng import SeededRng
from .model import GameState, PlayerState, Resources, EngineError
from . import deck as deck_mod
from . import effects as effects_mod
from . import combat as combat_mod
from . import scoring as scoring_mod
from .combat import Combat


class Engine:
    """对局引擎。一次对局对应一个实例。

    构造参数:
        catalog: 卡牌目录 {id: card}（由 cards.json 列表转来，便于按 id 查牌）；
        config: config.json 解析后的 dict；
        state:  已有 GameState（读档续局用）；新开局用 new_solo() 创建；
        rng:    随机源（新开局内部创建，读档时按 state.rng_state 恢复）；
        ui:     可选的 UserInterfacePort，M2 不使用，M3 起用于交互。
    """

    def __init__(self, catalog, config, state, rng, ui=None):
        self.catalog = catalog
        self.config = config
        self.state = state
        self.rng = rng
        self.ui = ui
        self.setup_cfg = config["setup"]
        self.solo_cfg = config["solo_paths"]
        # 当前战斗控制器（Combat 实例）；不在战斗中为 None，读档后惰性重建
        self._combat = None

    @staticmethod
    def _resolve_difficulty(config, difficulty):
        """校验并归一难度键：未指定时取配置默认值，再缺省为简单；非法值直接报错。"""
        solo_cfg = config["solo_paths"]
        diffs = solo_cfg.get("difficulties", {})
        if difficulty is None:
            difficulty = solo_cfg.get("default_difficulty", DIFFICULTY_EASY)
        if difficulty not in diffs:
            raise EngineError("未知难度：%r，可选 %s"
                              % (difficulty, sorted(diffs.keys())))
        return difficulty

    def _solo_paths_view(self):
        """返回当前难度对应的 solo_paths 配置视图（deck 只认里面的 paths）。

        难度只在“摆出路径”时读取一次，奖惩值随后固化进 PathOption，
        因此一局中途切换难度不会影响已经摆出的路径，读档续局也完全一致。
        """
        diffs = self.solo_cfg["difficulties"]
        difficulty = self.state.difficulty or DIFFICULTY_EASY
        view = dict(self.solo_cfg)  # 浅拷贝，保留 cards_per_path 等其它键
        view["paths"] = diffs[difficulty]["paths"]
        return view

    def difficulty_label(self):
        """当前难度的中文名（供界面展示）。"""
        diffs = self.solo_cfg.get("difficulties", {})
        entry = diffs.get(self.state.difficulty or DIFFICULTY_EASY)
        return entry["label"] if entry else str(self.state.difficulty)

    # ---------- 建局 / 读档 ----------
    @classmethod
    def new_solo(cls, cards, config, seed=None, ui=None, difficulty=None):
        """创建并开始一局单人游戏，返回处于第一轮 planning 的引擎。

        参数:
            cards: cards.json 的列表（也兼容 {id:card}）；
            seed: 随机种子，传入整数可复现整局；None 为真随机；
            difficulty: 难度键 "easy"/"hard"，None 时取
                config.solo_paths.default_difficulty（再缺省为简单）。
        """
        catalog = cards if isinstance(cards, dict) else {c["id"]: c for c in cards}
        rng = SeededRng(seed)
        difficulty = cls._resolve_difficulty(config, difficulty)
        state = GameState(mode=MODE_SOLO, seed=seed, difficulty=difficulty)

        # 初始人员与资源（数字来自 config，不在代码里写死）
        init_cfg = config["setup"]["initial_state"]
        survivors = init_cfg["main_survivors"] + init_cfg["followers"]
        state.player = PlayerState(
            survivors=survivors,
            resources=Resources(init_cfg["ammo"], init_cfg["gas"], init_cfg["meds"]))

        # 分阶段洗牌、剔除 4 张，得到登场牌库
        active, removed = deck_mod.build_stage_decks(catalog, rng, config["setup"])
        state.stage_decks = active
        state.removed_cards = removed
        state.rng_state = rng.get_state()

        engine = cls(catalog, config, state, rng, ui)
        engine.start_round()
        return engine

    @classmethod
    def restore(cls, cards, config, state_dict, ui=None):
        """从 GameState.to_dict() 的存档恢复一局，随机序列无缝延续。"""
        catalog = cards if isinstance(cards, dict) else {c["id"]: c for c in cards}
        state = GameState.from_dict(state_dict)
        rng = SeededRng(seed=state.seed)
        if state.rng_state is not None:
            rng.set_state(state.rng_state)
        return cls(catalog, config, state, rng, ui)

    def snapshot(self):
        """导出整局存档 dict（= GameState.to_dict，且同步最新随机状态）。"""
        self.state.rng_state = self.rng.get_state()
        return self.state.to_dict()

    # ---------- 轮次推进 ----------
    def current_stage(self):
        """当前轮所处阶段（1/2/3）。"""
        return deck_mod.stage_of_round(self.state.round_no,
                                       self.setup_cfg["stage_rounds"])

    def start_round(self):
        """进入下一轮：摸 6 张、组三条路径，状态切到 planning。"""
        if self.state.phase not in (PHASE_INIT, PHASE_ROUND_END):
            raise EngineError("只有建局/轮末才能开始新一轮，当前阶段：%s"
                              % self.state.phase)
        self.state.round_no += 1
        stage = self.current_stage()
        if stage is None:
            # 理论上 close_round 会先拦住，这里做双保险
            self.state.phase = PHASE_FINISHED
            return None

        drawn = deck_mod.draw_from_stage(
            self.state.stage_decks[stage],
            self.setup_cfg["solo_cards_per_round"])
        options = deck_mod.make_solo_options(drawn, self._solo_paths_view())
        self.state.current_options = options
        self.state.encounter_queue = []
        self.state.encounter_index = 0
        self.state.phase = PHASE_PLANNING
        return options

    # ---------- 规划选路 ----------
    def _validate_distribution(self, dist, amount, paying):
        """校验“任意资源分配”是否合法，返回规范化后的 dict（补零）。

        amount: 必须恰好分配的总数（奖励 2 或代价 2）；
        paying: True=支付（不得超过库存），False=获得（只看总数）。
        """
        if dist is None:
            dist = {}
        normalized = {}
        total = 0
        for key in RESOURCE_KEYS:
            value = int(dist.get(key, 0))
            if value < 0:
                raise EngineError("资源分配不能为负数")
            normalized[key] = value
            total += value
        for key in dist:
            if key not in RESOURCE_KEYS:
                raise EngineError("未知资源类型：%r" % (key,))
        if total != amount:
            raise EngineError("必须恰好分配 %d 个任意资源，当前合计 %d"
                              % (amount, total))
        if paying and not self.state.player.resources.can_pay_distribution(normalized):
            raise EngineError("资源不足，无法按该分配支付")
        return normalized

    def path_is_affordable(self, option):
        """选路前置判断：总资源是否够付该路径代价（供前端置灰选项）。

        简单难度只有路径三有代价；困难难度路径二（1）、路径三（2）都有代价。
        """
        if option.cost <= 0:
            return True
        return self.state.player.resources.total() >= option.cost

    def choose_path(self, path_index, resource_dist=None):
        """玩家在规划阶段选定一条路径。

        参数:
            path_index: 路径序号 1/2/3；
            resource_dist: 涉及“任意资源”时的分配方案（奖惩数量来自所选路径，
                随难度不同：简单 路径1奖2/路径3付2；困难 路径2付1/路径3付2），
                如 {"ammo":2}；既无奖励也无代价时传 None。
        返回:
            被选中的 PathOption。
        """
        if self.state.phase != PHASE_PLANNING:
            raise EngineError("当前不是规划阶段，无法选路（phase=%s）"
                              % self.state.phase)
        option = self._find_option(path_index)

        # 1) 选前获得 N 个任意资源（简单难度路径一）
        if option.bonus > 0:
            gain = self._validate_distribution(resource_dist, option.bonus, paying=False)
            self.state.player.resources.apply_delta(gain)
        # 2) 选前支付 N 个任意资源，不足时禁止选择（状态保持 planning）
        if option.cost > 0:
            if not self.path_is_affordable(option):
                raise EngineError("资源总数不足 %d，无法选择路径%d"
                                  % (option.cost, option.index))
            payment = self._validate_distribution(resource_dist, option.cost, paying=True)
            cost_delta = {k: -v for k, v in payment.items()}
            self.state.player.resources.apply_delta(cost_delta)

        # 3) 中选的两张进入遭遇队列（M3 逐张结算）；其余四张弃置
        chosen_ids = list(option.card_ids)
        unchosen = []
        for other in self.state.current_options:
            if other.index != option.index:
                unchosen.extend(other.card_ids)
        self.state.discard_ids.extend(unchosen)
        self.state.encounter_queue = chosen_ids
        self.state.encounter_index = 0
        self.state.phase = PHASE_ENCOUNTER

        self.state.round_log.append({
            "round": self.state.round_no,
            "stage": self.current_stage(),
            "chosen_path": option.index,
            "chosen_cards": chosen_ids,
            "discarded_cards": unchosen,
        })
        self._sync_rng()
        return option

    def _find_option(self, path_index):
        for option in self.state.current_options:
            if option.index == path_index:
                return option
        raise EngineError("没有序号为 %r 的路径（可选 1/2/3）" % (path_index,))

    # ---------- 遭遇队列流转（M2 只流转，M3 在此之间插入拾荒/事件/战斗） ----------
    def current_encounter_card(self):
        """返回当前待遭遇的卡牌 id；队列已空返回 None。"""
        if self.state.phase != PHASE_ENCOUNTER:
            return None
        idx = self.state.encounter_index
        if idx >= len(self.state.encounter_queue):
            return None
        return self.state.encounter_queue[idx]

    def consume_encounter_card(self):
        """标记当前遭遇卡已结算，推进到下一张；两张都结束则进入轮末。

        返回推进后的当前卡牌 id（可能为 None，表示本轮遭遇全部结束）。
        """
        if self.state.phase != PHASE_ENCOUNTER:
            raise EngineError("当前不在遭遇阶段")
        self.state.encounter_index += 1
        if self.state.encounter_index >= len(self.state.encounter_queue):
            self.state.phase = PHASE_ROUND_END
            return None
        return self.state.encounter_queue[self.state.encounter_index]

    # ---------- M3：卡牌结算（拾荒 → 事件 → 战斗挂起） ----------
    def _current_card(self):
        """取当前遭遇卡牌的完整数据（catalog 查表）。"""
        card_id = self.current_encounter_card()
        if card_id is None:
            raise EngineError("当前没有待结算的遭遇卡")
        return card_id, self.catalog[card_id]

    def peek_card_decision(self):
        """查看当前卡的立即效果是否需要玩家先决策（不需要返回 None）。

        前端循环用法：先 peek，有决策就询问玩家，再把 decision 传给
        begin_card_resolution。
        """
        _, card = self._current_card()
        spec = effects_mod.parse_effect(card)
        if spec["timing"] == effects_mod.TIMING_IMMEDIATE:
            return effects_mod.decision_needed(spec, self.state.player)
        return None

    def begin_card_resolution(self, decision=None):
        """结算当前遭遇卡的“非战斗部分”：拾荒 + 立即事件。

        返回 outcome：
            - 无战斗的卡：直接赢得并推进队列，outcome["combat"] 为 None；
            - 战斗牌：outcome["combat"] 为挂起的战斗信息 dict，等待 M4 战斗模块
              （M3 测试期可调用 resolve_combat_result 给出结果占位）。
        """
        if self.state.phase != PHASE_ENCOUNTER:
            raise EngineError("当前不在遭遇阶段（phase=%s）" % self.state.phase)
        card_id, card = self._current_card()
        player = self.state.player
        outcome = {"card_id": card_id, "scavenge": None,
                   "effect": None, "combat": None}

        # ① 拾荒：获得卡面全部资源（先结算，事件可能紧接着扣资源）
        scav = card["scavenge"]
        gain = {k: scav.get(k, 0) for k in RESOURCE_KEYS if scav.get(k, 0) > 0}
        if gain:
            player.resources.apply_delta(gain)
            for key, amount in gain.items():
                self.state.stats["resources_gained"][key] += amount
        outcome["scavenge"] = gain

        # ② 事件：解析效果指令
        spec = effects_mod.parse_effect(card)

        # ③ 战斗修正类效果：不立即结算，随战斗挂起（III-17 的道具全屏秒杀除外）
        if spec["timing"] == effects_mod.TIMING_COMBAT:
            if (spec["kind"] == effects_mod.KIND_MOD_ZEALOT_NUKE
                    and player.has_item(spec["item"])):
                # 持有“狂热者”：本场丧尸全部死亡、跳过战斗并消耗道具
                player.consume_item(spec["item"])
                killed = card["zombies"]["count"]
                self.state.stats["zombies_killed"] += killed
                self.state.stats["combats"] += 1
                outcome["effect"] = {"logs": ["狂热者引爆炸药，丧尸全灭，跳过战斗"],
                                     "item_consumed": spec["item"]}
                self._win_card(card_id)
                self._advance_after_resolution(outcome)
                return outcome
            # 立即效果（若有）已把人清零则不再开打，直接结束对局
            if player.is_eliminated():
                self.state.phase = PHASE_FINISHED
                self._sync_rng()
                return outcome
            outcome["combat"] = self._build_pending_combat(card, [spec["kind"]])
            self._sync_rng()
            return outcome

        # 立即效果结算
        effect_result = effects_mod.resolve_immediate(
            spec, player, self.rng, self.state.stats, decision)
        outcome["effect"] = effect_result

        # ④ 是否还有战斗：有丧尸则挂起战斗，且【不推进队列】（等战斗收口）；
        #    事件已导致全灭则不再开打、也不赢得本卡；无战斗则赢得并推进
        if card["zombies"]["count"] > 0:
            if player.is_eliminated():
                self.state.phase = PHASE_FINISHED
                self._sync_rng()
                return outcome
            outcome["combat"] = self._build_pending_combat(card, [])
            self._sync_rng()
            return outcome
        self._win_card(card_id)
        self._check_eliminated()
        self._advance_after_resolution(outcome)
        self._sync_rng()
        return outcome

    def _build_pending_combat(self, card, mod_kinds):
        """把一张战斗牌整理成挂起战斗状态（combat.new_combat_state），并建控制器。"""
        pending = combat_mod.new_combat_state(
            card["id"], card["zombies"]["count"], card["zombies"]["level"],
            mod_kinds)
        self.state.pending_combat = pending
        self.state.stats["combats"] += 1
        self._combat = Combat(self.state.player, pending, self.rng,
                              self.state.stats, self.config["combat"])
        return pending

    # ---------- M4：战斗驱动（前端按顺序调用） ----------
    def combat(self):
        """获取当前战斗控制器；若为读档后首次使用则按 pending_combat 惰性重建。"""
        if self.state.pending_combat is None:
            raise EngineError("当前没有挂起的战斗")
        if self._combat is None:
            self._combat = Combat(self.state.player, self.state.pending_combat,
                                  self.rng, self.state.stats,
                                  self.config["combat"])
        return self._combat

    def combat_actions(self):
        """近战前可选行动（远程/逃跑/近战）。"""
        return self.combat().available_actions()

    def combat_ranged(self, burst_count):
        """远程攻击，返回掷骰结果；若直接清场则自动收口战斗。"""
        result = self.combat().do_ranged(burst_count)
        if result.get("result") == combat_mod.RESULT_WON:
            self._close_combat(combat_mod.RESULT_WON)
        self._sync_rng()
        return result

    def combat_flee(self):
        """逃跑：付汽油、弃牌，收口战斗。"""
        result = self.combat().do_flee()
        self._close_combat(combat_mod.RESULT_FLED)
        self._sync_rng()
        return result

    def combat_roll_melee(self):
        """掷出一批近战骰，返回骰面与药剂机会列表（前端询问后再结算）。"""
        return self.combat().roll_melee()

    def combat_resolve_melee(self, use_meds_indices=None):
        """按药剂决策结算一批近战骰；ongoing 则继续下一批，胜/负自动收口。"""
        outcome = self.combat().resolve_melee(use_meds_indices)
        result = outcome["result"]
        if result in (combat_mod.RESULT_WON, combat_mod.RESULT_LOST):
            self._close_combat(result)
        self._sync_rng()
        return outcome

    def _close_combat(self, result):
        """战斗结束统一收口：赢得卡牌 / 逃跑弃牌 / 全灭失败，并推进遭遇队列。"""
        pending = self.state.pending_combat
        card_id = pending["card_id"]
        outcome = {"card_id": card_id, "combat_result": result}
        if result == combat_mod.RESULT_WON:
            self._win_card(card_id)
        elif result == combat_mod.RESULT_FLED:
            self.state.discard_ids.append(card_id)
        # RESULT_LOST：牌既不赢得也不弃，幸存者已归零，直接结束对局
        self.state.pending_combat = None
        self._combat = None
        if result == combat_mod.RESULT_LOST or self.state.player.is_eliminated():
            self.state.phase = PHASE_FINISHED
            return
        self._advance_after_resolution(outcome)

    def _win_card(self, card_id):
        """赢得（成功通过）一张卡：计入 won_card_ids。"""
        self.state.player.won_card_ids.append(card_id)

    def _check_eliminated(self):
        """幸存者归零：对局立即失败结束。"""
        if self.state.player.is_eliminated():
            self.state.phase = PHASE_FINISHED

    def _advance_after_resolution(self, outcome):
        """一张卡处理完后推进遭遇队列（两张都完则进入轮末）。"""
        if self.state.phase == PHASE_FINISHED:
            return
        next_id = self.consume_encounter_card()
        outcome["next_card"] = next_id
        outcome["phase_after"] = self.state.phase

    def close_round(self):
        """轮末收尾：进入下一轮 planning；若已打完 8 轮则进入 finished。

        返回下一轮的 PathOption 列表；finished 时返回 None。
        """
        if self.state.phase != PHASE_ROUND_END:
            raise EngineError("遭遇尚未结算完，不能结束本轮（phase=%s）"
                              % self.state.phase)
        self.state.current_options = []
        self.state.encounter_queue = []
        self.state.encounter_index = 0
        if self.state.round_no >= self.setup_cfg["total_rounds"]:
            self.state.phase = PHASE_FINISHED
            self._sync_rng()
            return None
        options = self.start_round()
        self._sync_rng()
        return options

    def is_finished(self):
        return self.state.phase == PHASE_FINISHED

    def final_report(self):
        """终局分数明细与评级（仅在 finished 时有意义）。"""
        return scoring_mod.final_report(self.state.player, self.catalog,
                                        self.config["scoring"])

    def current_score(self):
        """对局进行中的累计得分（已赢卡牌分 + 事件额外分；套装分终局才计）。"""
        card_score = 0
        for card_id in self.state.player.won_card_ids:
            card = self.catalog.get(card_id)
            if card is not None:
                card_score += card.get("score", 0)
        return card_score + self.state.player.bonus_score

    # ---------- 内部 ----------
    def _sync_rng(self):
        """把最新随机状态写回 state，使任何时刻 snapshot 都能无损续局。"""
        self.state.rng_state = self.rng.get_state()
