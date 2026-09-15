"""model.py —— 游戏状态数据结构（M2 落地：资源、玩家、路径选项、整局状态）。

设计约束（务必遵守，关系到阶段 2 能否直接移植到 MicroPython）：
    1. 只用“普通类 + dict/list/int/str”，不用 dataclasses、enum、typing 运行期特性；
    2. 不做任何输入输出（不 print、不读文件），纯内存状态；
    3. 每个状态类都实现 to_dict()/from_dict()：to_dict 的结果必须能直接 json.dump，
       from_dict 是它的逆操作——存档就是把 GameState.to_dict() 写进文件。

为什么状态里只存卡牌 id（如 "II-14"）而不是整张卡牌 dict？
    卡牌内容是静态数据（data/cards.json），整局中不会变化；状态里重复存全文既浪费
    又容易不一致。需要卡牌内容时，用引擎持有的 catalog[id] 查。
"""

from .constants import (RESOURCE_AMMO, RESOURCE_GAS, RESOURCE_MEDS, RESOURCE_KEYS,
                        PHASE_INIT, MODE_SOLO, DIFFICULTY_EASY)


class EngineError(ValueError):
    """规则违反类错误：非法阶段操作、资源不足、选项越界等。

    继承 ValueError 以便测试用 assertRaises(ValueError) 捕获；前端应把消息展示给玩家，
    而不是让程序崩溃。
    """


class Resources:
    """三种资源的容器：弹药 ammo / 汽油 gas / 药剂 meds。"""

    def __init__(self, ammo=0, gas=0, meds=0):
        self.ammo = ammo
        self.gas = gas
        self.meds = meds

    # --- 基本读写 ---
    def get(self, key):
        """按常量键取值，避免业务代码到处写 if/else。"""
        if key == RESOURCE_AMMO:
            return self.ammo
        if key == RESOURCE_GAS:
            return self.gas
        if key == RESOURCE_MEDS:
            return self.meds
        raise EngineError("未知资源类型：%r" % (key,))

    def set(self, key, value):
        if value < 0:
            raise EngineError("资源数量不能为负：%s=%d" % (key, value))
        if key == RESOURCE_AMMO:
            self.ammo = value
        elif key == RESOURCE_GAS:
            self.gas = value
        elif key == RESOURCE_MEDS:
            self.meds = value
        else:
            raise EngineError("未知资源类型：%r" % (key,))

    def total(self):
        """三种资源总数（判断“付 2 个任意资源是否付得起”时用）。"""
        return self.ammo + self.gas + self.meds

    def as_dict(self):
        """返回 {"ammo":..,"gas":..,"meds":..}，用于结算/展示。"""
        return {RESOURCE_AMMO: self.ammo, RESOURCE_GAS: self.gas,
                RESOURCE_MEDS: self.meds}

    # --- 批量增减（“任意资源”的奖惩都走这里，便于校验） ---
    def apply_delta(self, delta):
        """按 delta dict 增减资源，返回变化后的新 dict。

        delta 例：{"ammo": 2} 表示获得 2 弹药；{"gas": -1, "meds": -1} 表示各扣 1。
        扣减后任何一种资源不得为负，否则抛 EngineError（调用方应先判断能否支付）。
        """
        for key in RESOURCE_KEYS:
            amount = delta.get(key, 0)
            new_value = self.get(key) + amount
            if new_value < 0:
                raise EngineError("资源 %s 不足：当前 %d，想扣 %d"
                                  % (key, self.get(key), -amount))
            self.set(key, new_value)
        return self.as_dict()

    def can_pay_distribution(self, payment):
        """检查 payment（如 {"gas":1,"meds":1}）是否在当前库存内。"""
        for key in RESOURCE_KEYS:
            if payment.get(key, 0) > self.get(key):
                return False
        return True

    def copy(self):
        return Resources(self.ammo, self.gas, self.meds)

    # --- 序列化 ---
    def to_dict(self):
        return self.as_dict()

    @classmethod
    def from_dict(cls, data):
        return cls(ammo=data.get(RESOURCE_AMMO, 0),
                   gas=data.get(RESOURCE_GAS, 0),
                   meds=data.get(RESOURCE_MEDS, 0))


class PlayerState:
    """一名玩家的全部私有状态（单人局只有 1 个；双人局有 2 个）。"""

    def __init__(self, survivors=0, resources=None, name="玩家"):
        self.name = name
        # 幸存者人数（含主角）；归零即出局
        self.survivors = survivors
        self.resources = resources if resources is not None else Resources()
        # 已赢得（成功通过）的卡牌 id 列表，终局计分用
        self.won_card_ids = []
        # 持有的特殊道具名（6 种自设道具，可重复持有，如两张地图）
        self.special_items = []
        # 事件直接给的额外分数（如 II-4 地图 +6），终局与卡牌得分相加
        self.bonus_score = 0

    def has_item(self, item_name):
        """是否持有某特殊道具。"""
        return item_name in self.special_items

    def count_item(self, item_name):
        """持有某特殊道具的数量（道具可重复持有，如 2 张地图、2 个毒气）。"""
        return self.special_items.count(item_name)

    def gain_item(self, item_name):
        """获得特殊道具（允许重复获得）。"""
        self.special_items.append(item_name)

    def consume_item(self, item_name):
        """消耗（弃掉）一个指定道具；返回是否成功消耗。"""
        if item_name in self.special_items:
            self.special_items.remove(item_name)
            return True
        return False

    def lose_survivors(self, count):
        """损失 count 名幸存者，返回实际损失数（不会低于 0）。"""
        actual = min(count, self.survivors)
        self.survivors -= actual
        return actual

    def is_eliminated(self):
        """幸存者为 0 即淘汰/失败。"""
        return self.survivors <= 0

    def to_dict(self):
        return {"name": self.name, "survivors": self.survivors,
                "resources": self.resources.to_dict(),
                "won_card_ids": list(self.won_card_ids),
                "special_items": list(self.special_items),
                "bonus_score": self.bonus_score}

    @classmethod
    def from_dict(cls, data):
        player = cls(survivors=data["survivors"],
                     resources=Resources.from_dict(data["resources"]),
                     name=data.get("name", "玩家"))
        player.won_card_ids = list(data.get("won_card_ids", []))
        player.special_items = list(data.get("special_items", []))
        player.bonus_score = data.get("bonus_score", 0)
        return player


class PathOption:
    """规划阶段摆出的“一条路径”：由 2 张卡组成，带明暗与奖惩信息。

    Attributes:
        index: 路径序号 1/2/3（对应单人三路径）；
        card_ids: 两张卡牌 id，顺序即从左到右；
        face_up: 两个 bool，True=正面朝上（玩家可见），False=背面朝下（未知）；
        bonus: 选前奖励的“任意资源”总数（路径一为 2，其余为 0）；
        cost:  选前代价的“任意资源”总数（路径三为 2，其余为 0）。
    """

    def __init__(self, index, card_ids, face_up, bonus=0, cost=0):
        self.index = index
        self.card_ids = list(card_ids)
        self.face_up = tuple(face_up)
        self.bonus = bonus
        self.cost = cost

    def visible_card_id(self, position, catalog):
        """返回某位置（0 左 / 1 右）玩家能看到的卡牌 id；面朝下时返回 None。

        catalog 参数仅用于调用方取内容，本方法不做内容判断，保持数据结构纯粹。
        """
        if self.face_up[position]:
            return self.card_ids[position]
        return None

    def to_dict(self):
        return {"index": self.index, "card_ids": list(self.card_ids),
                "face_up": list(self.face_up), "bonus": self.bonus,
                "cost": self.cost}

    @classmethod
    def from_dict(cls, data):
        return cls(data["index"], data["card_ids"], data["face_up"],
                   bonus=data.get("bonus", 0), cost=data.get("cost", 0))


class GameState:
    """整局局面状态。引擎只修改它，前端只读它来渲染，存档只存它。

    牌库表示：stage_decks = {1: [id...], 2: [...], 3: [...]}，列表头是牌库顶，
    发牌就是 pop(0)。分阶段存放是为了严格保证“跨阶段不混洗”。
    """

    def __init__(self, mode=MODE_SOLO, seed=None, difficulty=None):
        self.mode = mode
        self.seed = seed
        # 单人难度 easy/hard（只影响路径奖惩；旧存档没有该字段时按 easy 处理）
        self.difficulty = difficulty
        self.phase = PHASE_INIT
        # 当前轮次，从 1 开始；尚未开局为 0；全部结束为 total_rounds+1
        self.round_no = 0
        # 各阶段“登场牌库”剩余卡牌 id（已剔除 4 张之后的 12/18/18）
        self.stage_decks = {1: [], 2: [], 3: []}
        # setup 时被随机剔除的卡（仅记录，用于复盘/统计，不参与游戏）
        self.removed_cards = {1: [], 2: [], 3: []}
        # 弃牌堆：没被选中的 4 张/逃跑的牌等
        self.discard_ids = []
        # 玩家（单人 1 个；双人在阶段 3 扩展为列表，此处先单玩家）
        self.player = None
        # 当前轮摆出的三条路径（phase=planning 时有效）
        self.current_options = []
        # 所选路径进入遭遇阶段的两张卡，以及处理到第几张
        self.encounter_queue = []
        self.encounter_index = 0
        # 当前待结算战斗（M3 建立结构，M4 实现战斗过程）；无战斗时为 None
        self.pending_combat = None
        # 累计统计（M3 起记账，M6 做局后统计展示）
        self.stats = self._empty_stats()
        # 随机源状态（SeededRng.get_state() 的结果），保证读档后随机序列无缝衔接
        self.rng_state = None
        # 逐轮流水（每轮选了哪条路、拿到/弃掉哪些牌），供局后统计与回看
        self.round_log = []

    @staticmethod
    def _empty_stage_stats():
        """单个阶段的零值统计（M6 局后统计按阶段拆分用）。"""
        return {"zombies_killed": 0, "survivors_lost": 0,
                "combats": 0, "fled": 0}

    @staticmethod
    def _empty_stats():
        """新建一份零值统计（键名固定，便于序列化与局后汇总）。"""
        return {
            "zombies_killed": 0,        # 累计击杀丧尸
            "survivors_lost": 0,        # 累计损失幸存者
            "combats": 0,               # 发生战斗的场次
            "fled": 0,                  # 逃跑次数
            "resources_gained": {"ammo": 0, "gas": 0, "meds": 0},
            "resources_spent": {"ammo": 0, "gas": 0, "meds": 0},
            # 两种骰子各面值出现次数（事件掷骰与 M4 战斗掷骰都记）
            "dice_faces": {"normal": {1: 0, 2: 0, 3: 0, 4: 0, 5: 0, 6: 0},
                           "enhanced": {1: 0, 2: 0, 3: 0, 4: 0, 5: 0, 6: 0}},
            # 分阶段统计（键为阶段号字符串，JSON 键只能是字符串）
            "by_stage": {"1": GameState._empty_stage_stats(),
                         "2": GameState._empty_stage_stats(),
                         "3": GameState._empty_stage_stats()},
        }

    # --- 序列化（存档的基础；要求输出是纯 JSON 类型） ---
    def to_dict(self):
        return {
            "mode": self.mode,
            "seed": self.seed,
            "difficulty": self.difficulty,
            "phase": self.phase,
            "round_no": self.round_no,
            "stage_decks": {str(k): list(v) for k, v in self.stage_decks.items()},
            "removed_cards": {str(k): list(v) for k, v in self.removed_cards.items()},
            "discard_ids": list(self.discard_ids),
            "player": self.player.to_dict() if self.player else None,
            "current_options": [opt.to_dict() for opt in self.current_options],
            "encounter_queue": list(self.encounter_queue),
            "encounter_index": self.encounter_index,
            "pending_combat": self.pending_combat,
            "stats": self.stats,
            "rng_state": self.rng_state,
            "round_log": list(self.round_log),
        }

    @classmethod
    def from_dict(cls, data):
        state = cls(mode=data["mode"], seed=data.get("seed"))
        state.phase = data["phase"]
        # 旧存档没有难度字段，按简单难度（原规则）兼容
        state.difficulty = data.get("difficulty") or DIFFICULTY_EASY
        state.round_no = data["round_no"]
        # JSON 的键只能是字符串，读回时转回 int 阶段号
        state.stage_decks = {int(k): list(v) for k, v in data["stage_decks"].items()}
        state.removed_cards = {int(k): list(v) for k, v in data["removed_cards"].items()}
        state.discard_ids = list(data.get("discard_ids", []))
        state.player = PlayerState.from_dict(data["player"]) if data.get("player") else None
        state.current_options = [PathOption.from_dict(d) for d in data.get("current_options", [])]
        state.encounter_queue = list(data.get("encounter_queue", []))
        state.encounter_index = data.get("encounter_index", 0)
        state.pending_combat = data.get("pending_combat")
        stats = data.get("stats") or GameState._empty_stats()
        # JSON 对象键只能是字符串，读回时把骰面键 "1".."6" 转回 int
        # （否则读档后再掷骰，用整数骰面索引会 KeyError）
        for kind_faces in stats.get("dice_faces", {}).values():
            for face in list(kind_faces.keys()):
                if isinstance(face, str) and face.isdigit():
                    kind_faces[int(face)] = kind_faces.pop(face)
        # 旧档没有分阶段桶，补齐零值（v0.7.0 起新增，向前兼容）
        stage_buckets = stats.setdefault("by_stage", {})
        for stage_no in ("1", "2", "3"):
            bucket = stage_buckets.setdefault(
                stage_no, GameState._empty_stage_stats())
            for key, zero in GameState._empty_stage_stats().items():
                bucket.setdefault(key, zero)
        state.stats = stats
        state.rng_state = data.get("rng_state")
        state.round_log = list(data.get("round_log", []))
        return state
