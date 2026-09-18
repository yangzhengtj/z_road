"""deck.py —— 牌库构建、发牌与单人三路径组装（M2 落地）。

对应实体说明书的两个环节：
  【游戏设置】三个阶段牌堆分别洗牌、各随机剔除 4 张（本局不会登场），
             登场数量 12 / 18 / 18，三阶段依次叠放、跨阶段绝不混洗；
  【规划阶段】单人每轮从“当前阶段牌库顶”摸 6 张，按顺序两两组成 3 条路径，
             并按 config.solo_paths 决定每张卡正面朝上还是朝下。

本模块同样遵守 MicroPython 子集：只用普通类/函数与基础容器，不做 I/O。
"""

from .constants import STAGE_I, STAGE_II, STAGE_III, FACE_UP
from .model import PathOption, EngineError

# 三个阶段的固定遍历顺序（I → II → III）
STAGE_ORDER = (STAGE_I, STAGE_II, STAGE_III)


def group_cards_by_stage(catalog):
    """把卡牌目录（60 张）按阶段分组，返回 {1:[card...], 2:[...], 3:[...]}。

    catalog 可以是 list（cards.json 原样）或 {id: card} dict；
    组内按 order_in_stage 排序，保证分组结果与洗牌前的初始顺序确定。
    """
    if isinstance(catalog, dict):
        cards = list(catalog.values())
    else:
        cards = list(catalog)
    groups = {STAGE_I: [], STAGE_II: [], STAGE_III: []}
    for card in cards:
        groups[card["stage"]].append(card)
    for stage in STAGE_ORDER:
        groups[stage].sort(key=lambda c: c["order_in_stage"])
    return groups


def build_stage_decks(catalog, rng, setup_config):
    """设置阶段：构建三阶段登场牌库。

    参数:
        catalog: 60 张卡的 list/dict；
        rng: SeededRng 实例（一切随机都经它，保证同种子可复现）；
        setup_config: config.json 的 "setup" 段。
    返回:
        (active, removed)：两者都是 {阶段: [卡牌id...]}
        active  —— 登场牌库，列表头即牌库顶（发牌从头部取）；
        removed —— 被随机剔除的 4 张/阶段，仅供记录。

    实现说明：对每阶段牌池整体洗牌后，前 4 张剔除、其余登场。
    “洗牌后切前 4 张”与“随机无放回抽 4 张剔除”在概率上等价。
    """
    groups = group_cards_by_stage(catalog)
    pool_sizes = setup_config["stage_pool_sizes"]            # [16, 22, 22]

    active = {}
    removed = {}
    for offset, stage in enumerate(STAGE_ORDER):
        active[stage], removed[stage] = build_one_stage_deck(
            groups[stage], stage, offset, rng, setup_config,
            expected_pool_size=pool_sizes[offset])
    return active, removed


def build_one_stage_deck(cards, stage, offset, rng, setup_config,
                         expected_pool_size=None):
    """单阶段洗牌剔除：返回 (active_ids, removed_ids)。

    参数:
        cards: 该阶段牌池的卡牌列表（顺序不限，内部按 order_in_stage 排序）；
        stage: 阶段常量 1/2/3；
        offset: 阶段在 STAGE_ORDER 中的下标（0/1/2），用于读 config 数组；
        rng: SeededRng 实例；
        setup_config: config["setup"]；
        expected_pool_size: 期望牌池数量（None 时不校验，供分阶段懒加载）。

    设备端内存小，需要逐阶段加载、洗牌、卸载时可直接调用本函数，
    与 build_stage_decks 对同一阶段使用同一段随机序列，结果完全一致。
    """
    pool_sizes = setup_config["stage_pool_sizes"]
    remove_n = setup_config["remove_per_stage"]              # 4
    expected_active = setup_config["active_deck_sizes"]      # [12, 18, 18]

    ordered = sorted(cards, key=lambda c: c["order_in_stage"])
    pool = [card["id"] for card in ordered]
    if expected_pool_size is not None and len(pool) != expected_pool_size:
        raise EngineError(
            "阶段 %s 牌池数量 %d 与 config.stage_pool_sizes[%d]=%d 不一致"
            % (stage, len(pool), offset, expected_pool_size))
    elif expected_pool_size is None and len(pool) != pool_sizes[offset]:
        raise EngineError(
            "阶段 %s 牌池数量 %d 与 config.stage_pool_sizes[%d]=%d 不一致"
            % (stage, len(pool), offset, pool_sizes[offset]))
    rng.shuffle(pool)  # 原地洗牌
    removed = pool[:remove_n]
    active = pool[remove_n:]
    if len(active) != expected_active[offset]:
        raise EngineError(
            "阶段 %s 登场数量应为 %d，实为 %d"
            % (stage, expected_active[offset], len(active)))
    return active, removed


def build_one_stage_deck_from_ids(pool_ids, stage, offset, rng,
                                  setup_config):
    """与 build_one_stage_deck 相同的洗牌剔除，但输入直接是 id 列表。

    设备端内存小，建库时不解析卡牌正文，只按 id 洗牌（id 列表约 1KB），
    正文留到对局中逐张懒加载。
    """
    pool_sizes = setup_config["stage_pool_sizes"]
    remove_n = setup_config["remove_per_stage"]
    expected_active = setup_config["active_deck_sizes"]
    if len(pool_ids) != pool_sizes[offset]:
        raise EngineError(
            "阶段 %s 牌池数量 %d 与 config.stage_pool_sizes[%d]=%d 不一致"
            % (stage, len(pool_ids), offset, pool_sizes[offset]))
    pool = list(pool_ids)
    rng.shuffle(pool)
    removed = pool[:remove_n]
    active = pool[remove_n:]
    if len(active) != expected_active[offset]:
        raise EngineError(
            "阶段 %s 登场数量应为 %d，实为 %d"
            % (stage, expected_active[offset], len(active)))
    return active, removed


def draw_from_stage(stage_deck, count):
    """从某阶段牌库顶（列表头）取 count 张，返回取出的 id 列表；牌库原地缩短。"""
    if len(stage_deck) < count:
        raise EngineError("牌库剩余 %d 张，不足以摸 %d 张"
                          % (len(stage_deck), count))
    drawn = stage_deck[:count]
    # 用切片删除头部，等价于连续 pop(0) count 次但只做一次列表移动
    del stage_deck[:count]
    return drawn


def make_solo_options(drawn_ids, solo_config):
    """把一轮摸到的 6 张牌组装成单人三条路径。

    参数:
        drawn_ids: 长度 6 的卡牌 id 列表，顺序即摸牌顺序；
        solo_config: config.json 的 "solo_paths" 段（含每条路径明暗与奖惩）。
    返回:
        [PathOption×3]，路径一/二/三按 config 声明顺序。

    发位约定（与实体游戏一致）：每两张组成一条路径，
    即 [0,1]→路径一，[2,3]→路径二，[4,5]→路径三。
    """
    cards_per_path = solo_config.get("cards_per_path", 2)
    path_cfgs = solo_config["paths"]
    if len(drawn_ids) != cards_per_path * len(path_cfgs):
        raise EngineError("单人一轮应摸 %d 张，实得 %d 张"
                          % (cards_per_path * len(path_cfgs), len(drawn_ids)))

    options = []
    cursor = 0
    for path_cfg in path_cfgs:
        pair = drawn_ids[cursor:cursor + cards_per_path]
        cursor += cards_per_path
        # config 里写 "up"/"down"，转成布尔面明标志
        face_up = (path_cfg["first_card"] == FACE_UP,
                   path_cfg["second_card"] == FACE_UP)
        options.append(PathOption(
            index=path_cfg["index"],
            card_ids=pair,
            face_up=face_up,
            bonus=path_cfg.get("bonus_any_resources", 0),
            cost=path_cfg.get("cost_any_resources", 0),
        ))
    return options


def stage_of_round(round_no, stage_rounds):
    """由轮次号推算所处阶段。stage_rounds=[2,3,3]：
    第 1-2 轮 → 阶段 1；第 3-5 轮 → 阶段 2；第 6-8 轮 → 阶段 3。
    超出总轮次时返回 None。
    """
    cumulative = 0
    for offset, rounds in enumerate(stage_rounds):
        cumulative += rounds
        if round_no <= cumulative:
            return STAGE_ORDER[offset]
    return None
