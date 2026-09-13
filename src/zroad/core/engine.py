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
                        MODE_SOLO)
from .rng import SeededRng
from .model import GameState, PlayerState, Resources, EngineError
from . import deck as deck_mod


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

    # ---------- 建局 / 读档 ----------
    @classmethod
    def new_solo(cls, cards, config, seed=None, ui=None):
        """创建并开始一局单人游戏，返回处于第一轮 planning 的引擎。

        参数:
            cards: cards.json 的列表（也兼容 {id:card}）；
            seed: 随机种子，传入整数可复现整局；None 为真随机。
        """
        catalog = cards if isinstance(cards, dict) else {c["id"]: c for c in cards}
        rng = SeededRng(seed)
        state = GameState(mode=MODE_SOLO, seed=seed)

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
        options = deck_mod.make_solo_options(drawn, self.solo_cfg)
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
        """路径三的前置判断：总资源是否够付代价（供前端置灰选项）。"""
        if option.cost <= 0:
            return True
        return self.state.player.resources.total() >= option.cost

    def choose_path(self, path_index, resource_dist=None):
        """玩家在规划阶段选定一条路径。

        参数:
            path_index: 路径序号 1/2/3；
            resource_dist: 涉及“任意资源”时的分配方案，
                选路径一（奖励 2）时必填，如 {"ammo":2}；
                选路径三（代价 2）时必填，如 {"gas":1,"meds":1}；
                路径二传 None。
        返回:
            被选中的 PathOption。
        """
        if self.state.phase != PHASE_PLANNING:
            raise EngineError("当前不是规划阶段，无法选路（phase=%s）"
                              % self.state.phase)
        option = self._find_option(path_index)

        # 1) 路径一：选前获得 N 个任意资源
        if option.bonus > 0:
            gain = self._validate_distribution(resource_dist, option.bonus, paying=False)
            self.state.player.resources.apply_delta(gain)
        # 2) 路径三：选前支付 N 个任意资源，不足时禁止选择（状态保持 planning）
        if option.cost > 0:
            if not self.path_is_affordable(option):
                raise EngineError("资源总数不足 %d，无法选择路径三" % option.cost)
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

    # ---------- 内部 ----------
    def _sync_rng(self):
        """把最新随机状态写回 state，使任何时刻 snapshot 都能无损续局。"""
        self.state.rng_state = self.rng.get_state()
