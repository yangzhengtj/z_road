"""combat.py —— 单场战斗的纯规则状态机（M4）。

一场战斗分两段（严格按实体说明书顺序）：

  1. 近战前行动（action）：可选一次【远程攻击】（1 弹药掷 2 普通骰，仅击杀面生效），
     或在近战开始前【逃跑】（付 2 汽油、弃牌不得分），也可以直接进入近战；
  2. 近战（melee）：每名幸存者掷 1 颗骰；屍群按 level 把等量普通骰替换为强化骰
     （持有“校车”时强化骰降级为普通骰）；一批骰子同时掷出，逐面结算：
        空白        —— 无事
        击杀        —— 杀 1 丧尸
        额外击杀    —— 杀 1，可再付 1 药剂多杀 1
        伺机而动    —— 可付 1 药剂杀 1，否则躲避（无事）
        咬伤        —— 可付 1 药剂救人，否则损失 1 名幸存者
        死亡(强化)  —— 立即损失 1 名幸存者，药剂不可救
     一批结算完：丧尸清空→胜；幸存者清空→败；否则回到 action 再打一批。

设计要点：
  - 本类只做规则，不 print、不询问；需要玩家决定的地方由引擎/前端先取“机会列表”，
    再把决策（在哪些骰面上花药剂）传回来；
  - 全部可变状态放在 self.state（dict）里，它就是 GameState.pending_combat，
    因此战斗中途存档/读档天然支持，无需额外序列化代码；
  - 兼容 MicroPython 子集：普通类、普通 dict/list，无第三方依赖。
"""

from .constants import (DICE_NORMAL, DICE_ENHANCED, NORMAL_FACE_NAMES,
                        ENHANCED_FACE_NAMES, FACE_HOLD, FACE_KILL,
                        FACE_EXTRA_KILL, FACE_BITE, FACE_DEATH, ITEM_BUS)
from .model import EngineError

# 战斗内部小阶段
STAGE_ACTION = "action"            # 等待选择 远程/逃跑/近战
STAGE_MELEE_DECISION = "melee_decision"  # 一批近战骰已掷出，等待药剂决策
STAGE_DONE = "done"                # 战斗已结束（胜/负/逃跑）

# 战斗结果
RESULT_WON = "won"
RESULT_LOST = "lost"
RESULT_FLED = "fled"

# 近战骰面上可花药剂的“机会类型”
OPP_WAIT = "wait"    # 伺机而动：付药剂→杀 1
OPP_EXTRA = "extra"  # 额外击杀：付药剂→多杀 1
OPP_BITE = "bite"    # 咬伤：付药剂→救下队友


def new_combat_state(card_id, zombies_count, zombies_level, mod_kinds=None):
    """根据战斗牌信息创建一份可序列化的战斗状态 dict。

    mod_kinds: effects 解析出的战斗修正类型列表（字符串），在此映射为开关。
    """
    mod_kinds = list(mod_kinds or [])
    state = {
        "card_id": card_id,
        "zombies_count": zombies_count,   # 初始丧尸数（统计/回看用）
        "zombies_left": zombies_count,    # 剩余丧尸数（战斗中变化）
        "zombies_level": zombies_level,   # 屍群等级 0/1/2/3，决定强化骰数量
        "mods": mod_kinds,
        "ranged_bite_adds_zombie": "mod_ranged_bite_adds" in mod_kinds,
        "no_meds": "mod_no_meds" in mod_kinds,
        "no_flee": "mod_no_flee" in mod_kinds,
        "ranged_used": False,              # 远程攻击每场仅一次
        "combat_stage": STAGE_ACTION,
        "last_roll": None,                 # 最近一批近战骰（等待决策时非空）
        "result": None,                    # won/lost/fled
    }
    return state


class Combat(object):
    """单场战斗状态机。绑定玩家、战斗状态 dict、随机源与局内统计。"""

    def __init__(self, player, state, rng, stats, combat_cfg):
        self.player = player
        self.state = state
        self.rng = rng
        self.stats = stats
        self.cfg = combat_cfg
        # 远程/逃跑的花费与骰数从 config 取，不写死
        ra = combat_cfg["ranged_attack"]
        self._ammo_per_burst = ra["ammo_per_burst"]
        self._dice_per_burst = ra["dice_per_burst"]
        self._ranged_kill_names = tuple(ra["kill_face_names"])
        self._flee_gas = combat_cfg["flee"]["gas_cost"]

    # ---------- 工具 ----------
    @property
    def stage(self):
        return self.state["combat_stage"]

    @property
    def zombies_left(self):
        return self.state["zombies_left"]

    def _bus_held(self):
        """持有校车：变异（强化）丧尸按普通丧尸处理。"""
        return self.player.has_item(ITEM_BUS)

    def _record_dice(self, dice_kind, faces):
        for face in faces:
            self.stats["dice_faces"][dice_kind][face] += 1

    def _spend(self, key, amount):
        self.player.resources.apply_delta({key: -amount})
        self.stats["resources_spent"][key] += amount

    # ---------- 1. 近战前行动 ----------
    def available_actions(self):
        """返回当前可选行动列表（前端据此渲染菜单；不可用的不出现）。"""
        if self.stage != STAGE_ACTION:
            return []
        actions = []
        ra_cfg = self.cfg["ranged_attack"]
        if not self.state["ranged_used"] and self.player.resources.ammo >= 1:
            # 最多能打几个 burst（每个 burst 耗 1 弹药、掷 2 骰）
            max_burst = self.player.resources.ammo // self._ammo_per_burst
            actions.append({"action": "ranged", "max_burst": max_burst,
                            "dice_per_burst": self._dice_per_burst})
        if not self.state["no_flee"] and self.player.resources.gas >= self._flee_gas:
            actions.append({"action": "flee", "gas_cost": self._flee_gas})
        actions.append({"action": "melee"})
        return actions

    def do_ranged(self, burst_count):
        """执行远程攻击：burst_count 为支付弹药数（每 1 弹药掷 2 普通骰）。"""
        if self.stage != STAGE_ACTION:
            raise EngineError("当前不是近战前行动阶段，不能远程攻击")
        if self.state["ranged_used"]:
            raise EngineError("每场战斗只能远程攻击一次")
        if burst_count < 1 or burst_count > self.player.resources.ammo:
            raise EngineError("非法弹药消耗数：%s" % burst_count)

        self._spend("ammo", burst_count * self._ammo_per_burst)
        self.state["ranged_used"] = True

        dice_num = burst_count * self._dice_per_burst
        faces = self.rng.roll_many(dice_num)
        self._record_dice(DICE_NORMAL, faces)

        kills = 0
        added = 0
        for face in faces:
            name = NORMAL_FACE_NAMES[face]
            if name in self._ranged_kill_names:
                kills += 1
            # 远程的咬伤面本来忽略；只有红字修正要求“咬伤则丧尸+1”时才计数
            if name == FACE_BITE and self.state["ranged_bite_adds_zombie"]:
                added += 1
        kills = min(kills, self.state["zombies_left"])
        self.state["zombies_left"] += added - kills
        self.stats["zombies_killed"] += kills

        result = {"action": "ranged", "faces": list(faces), "kills": kills,
                  "zombies_added": added, "zombies_left": self.state["zombies_left"]}
        if self.state["zombies_left"] <= 0:
            self._finish(RESULT_WON)
            result["result"] = RESULT_WON
        return result

    def do_flee(self):
        """近战开始前逃跑：付汽油、弃牌不得分。"""
        if self.stage != STAGE_ACTION:
            raise EngineError("当前阶段不能逃跑")
        if self.state["no_flee"]:
            raise EngineError("本场战斗不能逃跑")
        if self.player.resources.gas < self._flee_gas:
            raise EngineError("汽油不足，无法逃跑")
        self._spend("gas", self._flee_gas)
        self.stats["fled"] += 1
        self._finish(RESULT_FLED)
        return {"action": "flee", "gas_cost": self._flee_gas,
                "result": RESULT_FLED}

    # ---------- 2. 近战 ----------
    def _melee_dice_composition(self):
        """计算本批近战骰：(强化骰个数, 普通骰个数)，总数=幸存者人数。"""
        total = self.player.survivors
        if total <= 0:
            raise EngineError("已无幸存者，无法近战")
        enhanced = 0 if self._bus_held() else min(self.state["zombies_level"], total)
        return enhanced, total - enhanced

    def roll_melee(self):
        """掷出一批近战骰并生成“药剂机会列表”，等待玩家决策。"""
        if self.stage != STAGE_ACTION:
            raise EngineError("上一批近战骰尚未结算")
        enhanced_num, normal_num = self._melee_dice_composition()
        entries = []
        idx = 0
        # 先强化骰后普通骰（顺序只影响展示，不影响结果）
        for _ in range(enhanced_num):
            face = self.rng.d6()
            entries.append(self._make_entry(idx, DICE_ENHANCED, face))
            idx += 1
        enhanced_faces = [e["face"] for e in entries]
        normal_faces = self.rng.roll_many(normal_num)
        self._record_dice(DICE_ENHANCED, enhanced_faces)
        self._record_dice(DICE_NORMAL, normal_faces)
        for face in normal_faces:
            entries.append(self._make_entry(idx, DICE_NORMAL, face))
            idx += 1

        self.state["last_roll"] = entries
        self.state["combat_stage"] = STAGE_MELEE_DECISION
        return self.melee_view()

    def _make_entry(self, idx, dice_kind, face):
        """把一颗骰整理成带结算信息的条目。"""
        name = (ENHANCED_FACE_NAMES if dice_kind == DICE_ENHANCED
                else NORMAL_FACE_NAMES)[face]
        opportunity = None
        if name == FACE_HOLD:
            opportunity = OPP_WAIT
        elif name == FACE_EXTRA_KILL:
            opportunity = OPP_EXTRA
        elif name == FACE_BITE:
            opportunity = OPP_BITE
        # 击杀/空白自动处理；死亡强制损人，都不给决策机会
        return {"idx": idx, "kind": dice_kind, "face": face, "name": name,
                "opportunity": opportunity, "use_meds": False}

    def melee_view(self):
        """给前端展示用：本批骰面 + 可花药剂的机会（no_meds 时机会不可用）。"""
        entries = self.state["last_roll"]
        opportunities = []
        for entry in entries:
            if entry["opportunity"] is None:
                continue
            opportunities.append({
                "idx": entry["idx"], "opportunity": entry["opportunity"],
                "face_name": entry["name"], "meds_cost": 1,
                "available": not self.state["no_meds"]
                and self.player.resources.meds >= 1,
            })
        return {"entries": entries, "opportunities": opportunities,
                "no_meds": self.state["no_meds"],
                "zombies_left": self.state["zombies_left"],
                "survivors": self.player.survivors}

    def resolve_melee(self, use_meds_indices=None):
        """按玩家在哪些骰面上花药剂的决策，结算整批近战骰。

        use_meds_indices: 要花药剂的骰面 idx 列表（必须都是机会骰）。
        返回结算结果，result 为 won/lost/ongoing。
        """
        if self.stage != STAGE_MELEE_DECISION:
            raise EngineError("还没有待结算的近战骰")
        use_meds_indices = set(use_meds_indices or [])
        entries = self.state["last_roll"]
        opp_indices = {e["idx"] for e in entries if e["opportunity"] is not None}
        if not use_meds_indices.issubset(opp_indices):
            raise EngineError("存在不能花药剂的骰面：%s" % use_meds_indices)
        if self.state["no_meds"] and use_meds_indices:
            raise EngineError("本场战斗不能使用药剂")
        if len(use_meds_indices) > self.player.resources.meds:
            raise EngineError("药剂不足以在 %d 个骰面上使用"
                              % len(use_meds_indices))

        kills = 0
        losses = 0
        for entry in entries:
            use_meds = entry["idx"] in use_meds_indices
            entry["use_meds"] = use_meds
            name = entry["name"]
            if name == FACE_KILL:
                kills += 1
            elif name == FACE_EXTRA_KILL:
                kills += 1
                if use_meds:
                    kills += 1
            elif name == FACE_HOLD and use_meds:
                kills += 1
            elif name == FACE_BITE and not use_meds:
                losses += 1
            elif name == FACE_DEATH:
                losses += 1

        # 药剂一次性扣除
        if use_meds_indices:
            self._spend("meds", len(use_meds_indices))
        # 击杀数不能超过剩余丧尸
        kills = min(kills, self.state["zombies_left"])
        self.state["zombies_left"] -= kills
        self.stats["zombies_killed"] += kills
        actual_loss = self.player.lose_survivors(losses)
        self.stats["survivors_lost"] += actual_loss

        outcome = {"action": "melee", "entries": list(entries), "kills": kills,
                   "survivors_lost": actual_loss,
                   "zombies_left": self.state["zombies_left"],
                   "survivors": self.player.survivors}
        self.state["last_roll"] = None

        if self.state["zombies_left"] <= 0:
            self._finish(RESULT_WON)
            outcome["result"] = RESULT_WON
        elif self.player.is_eliminated():
            self._finish(RESULT_LOST)
            outcome["result"] = RESULT_LOST
        else:
            self.state["combat_stage"] = STAGE_ACTION
            outcome["result"] = "ongoing"
        return outcome

    def _finish(self, result):
        self.state["combat_stage"] = STAGE_DONE
        self.state["result"] = result
