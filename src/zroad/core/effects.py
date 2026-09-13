"""effects.py —— 卡牌事件的解析与立即结算（M3 落地）。

两张“面孔”：
  1. parse_effect(card)：纯函数，把卡牌的 event_raw 中文文本解析成结构化“效果指令”dict；
     60 张卡必须全部命中已知类型，解析不了直接报错（测试 test_effects_full_coverage
     会守住这条线，防止新增卡牌时静默漏处理）。
  2. resolve_immediate(spec, player, rng, stats, decision)：把“立即生效”的效果结算到
     玩家身上（改人数/资源/道具/额外分、需要时掷骰）；战斗修正类效果不在此结算，
     由引擎挂到 pending_combat 上交给 M4 的战斗模块。

设计约束同 core 其它模块：普通 dict、无第三方、无 I/O、兼容 MicroPython。
效果指令 spec 的统一形状：{"kind": 类型, "timing": "immediate"/"combat", ...参数}
"""

import re

from .constants import (EVENT_NONE, EVENT_PREFIX, DICE_NORMAL,
                        ITEM_SNIPER, ITEM_MAP, ITEM_BUS, ITEM_GAS, ITEM_WOUND,
                        ITEM_SURVIVOR)
from .model import EngineError

# ---- 效果类型常量（同样不用 enum） ----
KIND_NONE = "none"                       # 无事件
KIND_GAIN_SURVIVORS = "gain_survivors"   # 队伍人数 +N
KIND_GAIN_ITEM = "gain_item"             # 获得特殊道具
KIND_LOSE_RESOURCE_CHOICE = "lose_resource_choice"  # 三选一资源 -N
KIND_ITEM_MAP_SCORE = "item_map_score"   # 有地图：+分并消耗
KIND_ITEM_BUS_NOTE = "item_bus_note"     # 有校车：获得被动能力（无即时变化）
KIND_ITEM_SNIPER_PASS = "item_sniper_pass"  # 有狙击枪安全通过，否则 -N 人
KIND_ITEM_WOUND = "item_wound"           # 有“受伤”：-1 并消耗
KIND_ITEM_GAS = "item_gas"               # 有“毒气”：-1 并消耗
KIND_ITEM_SURVIVOR_UPKEEP = "item_survivor_upkeep"  # 付弹药保留“幸存者”，否则弃
KIND_PAY_GAS_OR_LOSE = "pay_gas_or_lose"  # 付汽油否则按缺口损人
KIND_RUSSIAN_ROULETTE = "russian_roulette"  # 按人数掷骰，每个咬伤面 -1
KIND_RESCUE_SHOT = "rescue_shot"         # 掷 1 骰，不在解救面则 -1
# ---- 战斗修正类（timing=combat，M4 使用） ----
KIND_MOD_RANGED_BITE_ADDS = "mod_ranged_bite_adds"  # 远程掷出咬伤则丧尸+1
KIND_MOD_NO_MEDS = "mod_no_meds"         # 本场不能用药剂
KIND_MOD_NO_FLEE = "mod_no_flee"         # 本场不能逃跑
KIND_MOD_SURVIVOR_NUKE = "mod_survivor_nuke"  # 有“幸存者”则全屏秒杀、跳过战斗

TIMING_IMMEDIATE = "immediate"
TIMING_COMBAT = "combat"

# 需要玩家做决策的效果类型（前端据此弹出对应询问）
DECISION_CHOOSE_RESOURCE = "choose_resource"
DECISION_PAY_GAS = "pay_gas"
DECISION_SURVIVOR_UPKEEP = "survivor_upkeep"


# ============================ 1. 解析 ============================
def _spec(kind, timing, **params):
    """构造效果指令 dict。"""
    result = {"kind": kind, "timing": timing}
    result.update(params)
    return result


def _extract_item(text):
    """从“特殊道具（地图）”这类文本中取出括号内道具名。"""
    match = re.search(r"[（(](.+?)[）)]", text)
    return match.group(1) if match else None


def parse_effect(card):
    """把一张卡牌的事件文本解析为效果指令。

    规则按“特异 → 通用”排序：先匹配战斗修正与道具条件句，最后才落到通用句式，
    避免关键词互相误命中。
    """
    text = (card.get("event_raw") or "").strip()
    if text.startswith(EVENT_PREFIX):
        text = text[len(EVENT_PREFIX):].strip()

    # 0) 无事件
    if text == EVENT_NONE:
        return _spec(KIND_NONE, TIMING_IMMEDIATE)

    # 1) 战斗修正类（红字效果，影响本场战斗，M4 消费）
    if "丧尸全部死亡" in text or "不再进行战斗" in text:
        return _spec(KIND_MOD_SURVIVOR_NUKE, TIMING_COMBAT, item=ITEM_SURVIVOR)
    if "不能使用药剂" in text:
        return _spec(KIND_MOD_NO_MEDS, TIMING_COMBAT)
    if "不能逃跑" in text:
        return _spec(KIND_MOD_NO_FLEE, TIMING_COMBAT)
    if "远距离攻击" in text:
        return _spec(KIND_MOD_RANGED_BITE_ADDS, TIMING_COMBAT)

    # 2) 获得道具 / 人数
    if text.startswith("获得特殊道具"):
        item = _extract_item(text)
        if item is None:
            # 形如“获得特殊道具：狙击枪”（冒号后直接是名字）
            item = text.split("：", 1)[-1].strip()
        return _spec(KIND_GAIN_ITEM, TIMING_IMMEDIATE, item=item)
    if "队伍人数+1" in text:
        return _spec(KIND_GAIN_SURVIVORS, TIMING_IMMEDIATE, amount=1)

    # 3) 六种道具的条件触发句
    if "特殊道具（地图）" in text:
        score = 6
        match = re.search(r"得分\+(\d+)", text)
        if match:
            score = int(match.group(1))
        return _spec(KIND_ITEM_MAP_SCORE, TIMING_IMMEDIATE, item=ITEM_MAP, score=score)
    if "特殊道具（校车）" in text:
        return _spec(KIND_ITEM_BUS_NOTE, TIMING_IMMEDIATE, item=ITEM_BUS)
    if "特殊道具（狙击枪）" in text:
        return _spec(KIND_ITEM_SNIPER_PASS, TIMING_IMMEDIATE,
                     item=ITEM_SNIPER, lose_if_missing=2)
    if "特殊道具（受伤）" in text:
        return _spec(KIND_ITEM_WOUND, TIMING_IMMEDIATE, item=ITEM_WOUND, lose=1)
    if "特殊道具（毒气）" in text:
        return _spec(KIND_ITEM_GAS, TIMING_IMMEDIATE, item=ITEM_GAS, lose=1)
    if "继续持有" in text or "支付3个弹药" in text:
        return _spec(KIND_ITEM_SURVIVOR_UPKEEP, TIMING_IMMEDIATE,
                     item=ITEM_SURVIVOR, ammo=3)

    # 4) 资源三选一损失
    if "三选一" in text and "数量-1" in text:
        return _spec(KIND_LOSE_RESOURCE_CHOICE, TIMING_IMMEDIATE, amount=1)

    # 5) 付汽油否则损人（II-7/III-3 付1；II-12/III-4 付2，每缺1损1）
    if "汽油" in text and "队伍人数-1" in text:
        match = re.search(r"支付(\d+)个汽油", text)
        required = int(match.group(1)) if match else 1
        return _spec(KIND_PAY_GAS_OR_LOSE, TIMING_IMMEDIATE,
                     gas=required, lose_per_missing=1)

    # 6) 俄罗斯轮盘（II-9）：按当前人数掷普通骰，每个 6 点咬伤损 1 人
    if "按照队伍人数投掷等量" in text:
        return _spec(KIND_RUSSIAN_ROULETTE, TIMING_IMMEDIATE,
                     dice=DICE_NORMAL, bite_face=6, lose_per=1)

    # 7) 解救射击（III-13/14/15）：掷 1 普通骰，3/4/5 解救成功，否则损 1 人
    if "投一个普通骰子" in text and "不是3、4、5" in text:
        return _spec(KIND_RESCUE_SHOT, TIMING_IMMEDIATE,
                     dice=DICE_NORMAL, save_faces=(3, 4, 5), lose=1)

    # 8) 兜底：未识别文本直接报错，绝不静默忽略
    raise EngineError("无法解析卡牌事件：%s｜文本：%s" % (card.get("id"), text))


def parse_all(cards):
    """批量解析，返回 {卡牌id: spec}。"""
    return {card["id"]: parse_effect(card) for card in cards}


def decision_needed(spec, player):
    """判断某立即效果是否需要玩家先做决策；需要则返回决策描述 dict，否则 None。"""
    kind = spec["kind"]
    if kind == KIND_LOSE_RESOURCE_CHOICE:
        available = [key for key in ("ammo", "gas", "meds")
                     if player.resources.get(key) > 0]
        # 三种资源都为 0 时无东西可丢，无需决策（结算时自动无事发生）
        if not available:
            return None
        return {"type": DECISION_CHOOSE_RESOURCE,
                "amount": spec["amount"], "available": available}
    if kind == KIND_PAY_GAS_OR_LOSE:
        return {"type": DECISION_PAY_GAS, "required": spec["gas"],
                "available_gas": player.resources.gas}
    if kind == KIND_ITEM_SURVIVOR_UPKEEP and player.has_item(spec["item"]):
        return {"type": DECISION_SURVIVOR_UPKEEP, "ammo": spec["ammo"],
                "can_keep": player.resources.ammo >= spec["ammo"]}
    return None


# ============================ 2. 立即结算 ============================
def _result():
    """一份空白结算结果（记录本次效果造成的所有变化，供日志与统计）。"""
    return {"logs": [], "survivors_delta": 0, "resources_delta": {},
            "item_gained": None, "item_consumed": None, "bonus_score": 0,
            "rolls": []}


def _record_dice(stats, dice_kind, faces):
    """把一次掷出的若干骰面记入统计 stats.dice_faces。"""
    if stats is None:
        return
    for face in faces:
        stats["dice_faces"][dice_kind][face] += 1


def _lose_players(player, count, result, stats):
    """实际扣减幸存者并同步统计与结果。"""
    actual = player.lose_survivors(count)
    result["survivors_delta"] -= actual
    if stats is not None:
        stats["survivors_lost"] += actual
    return actual


def _spend(player, key, amount, stats):
    """扣资源并记支出统计。"""
    player.resources.apply_delta({key: -amount})
    if stats is not None:
        stats["resources_spent"][key] += amount


def resolve_immediate(spec, player, rng, stats=None, decision=None):
    """结算一个 timing=immediate 的效果，返回结果 dict，并就地修改 player。

    参数:
        spec: parse_effect 的结果；
        player: PlayerState；
        rng: SeededRng（只有掷骰类效果使用）；
        stats: GameState.stats，可为 None（单元测试单用）；
        decision: 玩家决策，形状由 decision_needed 的描述决定。
    """
    result = _result()
    kind = spec["kind"]

    if kind == KIND_NONE:
        return result

    if kind == KIND_GAIN_SURVIVORS:
        player.survivors += spec["amount"]
        result["survivors_delta"] = spec["amount"]
        result["logs"].append("队伍人数 +%d" % spec["amount"])
        return result

    if kind == KIND_GAIN_ITEM:
        player.gain_item(spec["item"])
        result["item_gained"] = spec["item"]
        result["logs"].append("获得特殊道具：%s" % spec["item"])
        return result

    if kind == KIND_LOSE_RESOURCE_CHOICE:
        needed = decision_needed(spec, player)
        if needed is None:
            result["logs"].append("三种资源均为 0，无可损失")
            return result
        if not decision or "resource" not in decision:
            raise EngineError("该效果需要选择损失哪种资源")
        key = decision["resource"]
        if key not in needed["available"]:
            raise EngineError("不能选择已无库存的资源：%s" % key)
        _spend(player, key, spec["amount"], stats)
        result["resources_delta"][key] = -spec["amount"]
        result["logs"].append("损失 %d 个所选资源" % spec["amount"])
        return result

    if kind == KIND_ITEM_MAP_SCORE:
        if player.has_item(spec["item"]):
            player.consume_item(spec["item"])
            player.bonus_score += spec["score"]
            result["item_consumed"] = spec["item"]
            result["bonus_score"] = spec["score"]
            result["logs"].append("地图确认路线，得分 +%d 并消耗地图" % spec["score"])
        else:
            result["logs"].append("没有地图，正常通过")
        return result

    if kind == KIND_ITEM_BUS_NOTE:
        held = player.has_item(spec["item"])
        result["logs"].append("校车可加固冲撞，变异丧尸按普通处理"
                              if held else "没有校车，无事发生")
        return result

    if kind == KIND_ITEM_SNIPER_PASS:
        if player.has_item(spec["item"]):
            player.consume_item(spec["item"])
            result["item_consumed"] = spec["item"]
            result["logs"].append("狙击枪引爆油罐车，安全通过并消耗狙击枪")
        else:
            lost = _lose_players(player, spec["lose_if_missing"], result, stats)
            result["logs"].append("没有狙击枪硬闯，损失 %d 人" % lost)
        return result

    if kind in (KIND_ITEM_WOUND, KIND_ITEM_GAS):
        if player.has_item(spec["item"]):
            player.consume_item(spec["item"])
            lost = _lose_players(player, spec["lose"], result, stats)
            result["item_consumed"] = spec["item"]
            result["logs"].append("触发道具【%s】，损失 %d 人并消耗该道具"
                                  % (spec["item"], lost))
        else:
            result["logs"].append("没有道具【%s】，无事发生" % spec["item"])
        return result

    if kind == KIND_ITEM_SURVIVOR_UPKEEP:
        if not player.has_item(spec["item"]):
            return result
        keep = bool(decision and decision.get("keep"))
        if keep:
            if player.resources.ammo < spec["ammo"]:
                raise EngineError("弹药不足 %d，无法继续持有该伙伴" % spec["ammo"])
            _spend(player, "ammo", spec["ammo"], stats)
            result["resources_delta"]["ammo"] = -spec["ammo"]
            result["logs"].append("支付 %d 弹药，伙伴继续同行" % spec["ammo"])
        else:
            player.consume_item(spec["item"])
            result["item_consumed"] = spec["item"]
            result["logs"].append("未支付弹药，伙伴离队（消耗道具）")
        return result

    if kind == KIND_PAY_GAS_OR_LOSE:
        required = spec["gas"]
        if not decision or "gas_pay" not in decision:
            raise EngineError("该效果需要决定支付多少汽油")
        pay = int(decision["gas_pay"])
        if pay < 0 or pay > min(required, player.resources.gas):
            raise EngineError("非法汽油支付数：%d（库存 %d，需要 %d）"
                              % (pay, player.resources.gas, required))
        if pay > 0:
            _spend(player, "gas", pay, stats)
            result["resources_delta"]["gas"] = -pay
        missing = required - pay
        if missing > 0:
            lost = _lose_players(player,
                                 missing * spec["lose_per_missing"], result, stats)
            result["logs"].append("支付 %d 汽油、缺口 %d，损失 %d 人"
                                  % (pay, missing, lost))
        else:
            result["logs"].append("足额支付 %d 汽油，安全通过" % required)
        return result

    if kind == KIND_RUSSIAN_ROULETTE:
        dice_kind = spec["dice"]
        faces = rng.roll_many(player.survivors)
        _record_dice(stats, dice_kind, faces)
        result["rolls"] = list(faces)
        bites = sum(1 for f in faces if f == spec["bite_face"])
        if bites:
            lost = _lose_players(player, bites * spec["lose_per"], result, stats)
            result["logs"].append("轮盘掷骰 %s，咬伤 %d 次，损失 %d 人"
                                  % (faces, bites, lost))
        else:
            result["logs"].append("轮盘掷骰 %s，无人受伤" % faces)
        return result

    if kind == KIND_RESCUE_SHOT:
        face = rng.d6()
        _record_dice(stats, spec["dice"], [face])
        result["rolls"] = [face]
        if face in spec["save_faces"]:
            result["logs"].append("解救射击掷出 %d，成功救下队友" % face)
        else:
            lost = _lose_players(player, spec["lose"], result, stats)
            result["logs"].append("解救射击掷出 %d，失败，损失 %d 人" % (face, lost))
        return result

    raise EngineError("resolve_immediate 收到非即时效果或未知类型：%s" % kind)
