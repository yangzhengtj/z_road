"""scoring.py —— 单人终局计分与评级（M5）。

计分口径（实体说明书第 12 页 + config.scoring）：
    总分 = 赢得卡牌的卡面得分之和
         + 事件给予的额外分（bonus_score，如 II-4 地图 +6）
         + 完整套装数 × 每套分值
    一套 = 1 名幸存者 + 1 汽油 + 1 弹药 + 1 药剂（四种各取 1，套数取最小值）。
评级按总分落入 config.scoring.ratings 的区间，得到中文称号。

纯函数、零依赖、兼容 MicroPython。
"""


def compute_sets(player, set_cfg):
    """计算能凑出多少套完整补给（幸存者与三种资源各 1 为一套）。"""
    per = set_cfg["survivor"], set_cfg["gas"], set_cfg["ammo"], set_cfg["meds"]
    # 各资源能支撑的套数 = 持有量 // 每套需求量，最后取最小值
    survivor_sets = player.survivors // per[0]
    gas_sets = player.resources.gas // per[1]
    ammo_sets = player.resources.ammo // per[2]
    meds_sets = player.resources.meds // per[3]
    return min(survivor_sets, gas_sets, ammo_sets, meds_sets)


def compute_score(player, catalog, scoring_cfg):
    """计算终局分数明细。

    返回 dict：
        card_score   赢得卡牌的卡面分合计
        bonus_score  事件额外分（直接取自 player.bonus_score）
        sets         完整套装数
        set_score    套装分合计
        total        总分
        won_count    赢得卡牌数
    """
    card_score = 0
    for card_id in player.won_card_ids:
        card = catalog.get(card_id)
        if card is not None:
            card_score += card.get("score", 0)
    sets = compute_sets(player, scoring_cfg["complete_set"])
    set_score = sets * scoring_cfg["complete_set"]["points"]
    bonus = player.bonus_score
    return {
        "card_score": card_score,
        "bonus_score": bonus,
        "sets": sets,
        "set_score": set_score,
        "total": card_score + bonus + set_score,
        "won_count": len(player.won_card_ids),
    }


def rating_for(total, scoring_cfg):
    """按总分返回评级 dict（label + 命中的区间）；未匹配到则返回 None。"""
    for band in scoring_cfg["ratings"]:
        low = band["min"]
        high = band["max"]
        if total >= low and (high is None or total <= high):
            return {"label": band["label"], "min": low, "max": high}
    return None


def final_report(player, catalog, scoring_cfg):
    """一次性给出分数明细 + 评级（终局界面直接使用）。"""
    detail = compute_score(player, catalog, scoring_cfg)
    detail["rating"] = rating_for(detail["total"], scoring_cfg)
    detail["eliminated"] = player.is_eliminated()
    return detail
