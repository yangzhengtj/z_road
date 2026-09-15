"""statistics.py —— 局后统计汇总（M6）。

设计原则与 core 其它模块一致：
  * 纯函数、零第三方依赖、不修改传入状态，方便整体移植到 Cardputer；
  * 只做“把 GameState.stats 整理成可展示结构”的工作，不重新记账
    （记账发生在 engine/combat/effects，保证口径唯一）。

对外主入口是 :func:`summarize`，返回一个纯 dict（可直接 JSON 序列化），
桌面端 render 层据此画表，测试也直接断言这个结构。
"""

from .constants import (RESOURCE_KEYS, RES_NAMES_CN, DICE_KINDS,
                        NORMAL_FACE_NAMES, ENHANCED_FACE_NAMES)

# 三个阶段的固定顺序（stats.by_stage 的键是字符串）
STAGE_IDS = ("1", "2", "3")

# 每种骰子各骰面对应的中文名表
DICE_FACE_NAMES = {"normal": NORMAL_FACE_NAMES,
                   "enhanced": ENHANCED_FACE_NAMES}


def _empty_stage_row():
    return {"zombies_killed": 0, "survivors_lost": 0,
            "combats": 0, "fled": 0}


def overview(stats):
    """总览：整场的战斗/击杀/损失/逃跑。"""
    return {
        "combats": stats.get("combats", 0),
        "zombies_killed": stats.get("zombies_killed", 0),
        "survivors_lost": stats.get("survivors_lost", 0),
        "fled": stats.get("fled", 0),
    }


def by_stage(stats):
    """分阶段统计：返回 [{stage, combats, zombies_killed, survivors_lost, fled}]，
    并附 total 行（等于 overview 对应字段，用于交叉核对）。"""
    buckets = stats.get("by_stage", {})
    rows = []
    total = _empty_stage_row()
    for stage_id in STAGE_IDS:
        bucket = buckets.get(stage_id, _empty_stage_row())
        row = {"stage": int(stage_id)}
        for key in total:
            value = int(bucket.get(key, 0))
            row[key] = value
            total[key] += value
        rows.append(row)
    total_row = {"stage": 0}
    total_row.update(total)
    return {"rows": rows, "total": total_row}


def resource_ledger(stats, initial=None, final=None):
    """资源收支账：每种资源的获得/支出/净变化。

    initial/final 给定时再附 closing 校验值（终局持有 = 初始 + 获得 - 支出），
    便于测试核对“每一笔资源变动都记了账”。
    """
    gained = stats.get("resources_gained", {})
    spent = stats.get("resources_spent", {})
    rows = []
    for key in RESOURCE_KEYS:
        g = int(gained.get(key, 0))
        s = int(spent.get(key, 0))
        row = {"key": key, "name": RES_NAMES_CN[key],
               "gained": g, "spent": s, "net": g - s}
        if initial is not None and final is not None:
            row["initial"] = int(getattr(initial, key, 0))
            row["final"] = int(getattr(final, key, 0))
            # 账面恒等式：终局 == 初始 + 净变化
            row["balanced"] = row["final"] == row["initial"] + row["net"]
        rows.append(row)
    return rows


def dice_distribution(stats):
    """骰面分布：普通骰/强化骰各骰面出现次数与掷骰总数。"""
    faces = stats.get("dice_faces", {})
    result = {}
    for kind in DICE_KINDS:
        bucket = faces.get(kind, {})
        name_table = DICE_FACE_NAMES[kind]
        face_rows = []
        total = 0
        for face in range(1, 7):
            count = int(bucket.get(face, 0))
            total += count
            face_rows.append({"face": face, "name": name_table[face],
                              "count": count})
        result[kind] = {"faces": face_rows, "total": total}
    result["total"] = sum(result[k]["total"] for k in DICE_KINDS)
    return result


def replay_rounds(state, catalog):
    """对局回看：把 round_log 的每轮选路补成可展示结构。

    每轮：轮次、阶段、选中路径、选中的两张卡（id + 场景）、弃掉的四张卡 id。
    """
    rounds = []
    for entry in state.round_log:
        chosen = []
        for card_id in entry.get("chosen_cards", []):
            card = catalog.get(card_id)
            chosen.append({"id": card_id,
                           "scene": card["scene"] if card else "（未知卡牌）"})
        rounds.append({
            "round": entry["round"],
            "stage": entry["stage"],
            "chosen_path": entry["chosen_path"],
            "chosen_cards": chosen,
            "discarded_cards": list(entry.get("discarded_cards", [])),
        })
    return rounds


def summarize(state, catalog, initial_resources=None):
    """把一整局的状态汇总成局后统计结构（纯 dict，不修改 state）。

    参数:
        state: GameState；
        catalog: {card_id: card} 卡牌目录，回看时取场景文本；
        initial_resources: 开局 Resources（可选），用于资源账面恒等式校验。
    """
    stats = state.stats
    final_resources = state.player.resources if state.player else None
    return {
        "overview": overview(stats),
        "by_stage": by_stage(stats),
        "resource_ledger": resource_ledger(
            stats, initial=initial_resources, final=final_resources),
        "dice": dice_distribution(stats),
        "rounds": replay_rounds(state, catalog),
    }
