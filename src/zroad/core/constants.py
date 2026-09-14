"""constants.py —— 全局稳定标识（字符串常量）。

为什么不用 enum.Enum：MicroPython 对标准库 enum 支持不完整，为保证同一份核心
代码将来能直接跑在 Cardputer 上，统一用“全大写字符串常量”代替枚举。

约定：代码里一切资源、骰面、阶段、卡牌明暗等“取值有限的离散标识”都定义在这里，
禁止在业务代码里散落裸字符串，避免拼写错误且便于全局检索。
"""

# ---- 三种资源（键名与 data/config.json、cards.json 保持一致） ----
RESOURCE_AMMO = "ammo"   # 弹药（实体书：彈藥）
RESOURCE_GAS = "gas"     # 汽油（实体书：汽油）
RESOURCE_MEDS = "meds"   # 药剂（实体书：腎上腺素，用户改编版称 X 药剂）
RESOURCE_KEYS = (RESOURCE_AMMO, RESOURCE_GAS, RESOURCE_MEDS)

# ---- 三个阶段 ----
STAGE_I = 1
STAGE_II = 2
STAGE_III = 3

# ---- 卡牌面的明暗（单人规划阶段） ----
FACE_UP = "up"
FACE_DOWN = "down"

# ---- 两种骰子（键名与 dice.json 保持一致） ----
DICE_NORMAL = "normal"      # 普通骰（实体书：黑色骰）
DICE_ENHANCED = "enhanced"  # 强化骰（实体书：红色屍群骰）

# ---- 骰面名称（与 dice.json 的 name 字段、config.json 的 face_costs 对应） ----
FACE_BLANK = "空白"
FACE_HOLD = "伺机而动"
FACE_KILL = "击杀"
FACE_EXTRA_KILL = "额外击杀"
FACE_BITE = "咬伤"
FACE_DEATH = "死亡"

# ---- 骰面 → 名称映射（与 data/dice.json 严格一致，由测试守护） ----
# 普通骰（黑）：1 空白 / 2 伺机而动 / 3、4 击杀 / 5 额外击杀 / 6 咬伤
NORMAL_FACE_NAMES = {1: FACE_BLANK, 2: FACE_HOLD, 3: FACE_KILL,
                     4: FACE_KILL, 5: FACE_EXTRA_KILL, 6: FACE_BITE}
# 强化骰（红，屍群骰）：1 咬伤 / 2 伺机而动 / 3、4 击杀 / 5 额外击杀 / 6 死亡
ENHANCED_FACE_NAMES = {1: FACE_BITE, 2: FACE_HOLD, 3: FACE_KILL,
                       4: FACE_KILL, 5: FACE_EXTRA_KILL, 6: FACE_DEATH}

# ---- 事件原文中的固定前缀 ----
EVENT_NONE = "无"
EVENT_PREFIX = "事件效果："

# ---- 六种自设特殊道具（名称与卡牌事件文本完全一致） ----
ITEM_SNIPER = "狙击枪"
ITEM_MAP = "地图"
ITEM_BUS = "校车"
ITEM_GAS = "毒气"
ITEM_WOUND = "受伤"
# 原“幸存者”标记自 v0.6.1 起改名“狂热者”（I-8 获得、III-9 养护、III-17 引爆）
ITEM_ZEALOT = "狂热者"
SPECIAL_ITEMS = (ITEM_SNIPER, ITEM_MAP, ITEM_BUS,
                 ITEM_GAS, ITEM_WOUND, ITEM_ZEALOT)

# ---- 对局模式 ----
MODE_SOLO = "solo"   # 单人 SOLO（阶段 1）
MODE_DUEL = "duel"   # 两人热座（阶段 3）

# ---- 引擎状态机的阶段（GameState.phase 取值） ----
PHASE_INIT = "init"            # 已建局、尚未开始第一轮
PHASE_PLANNING = "planning"    # 规划：三条路径已摆出，等待玩家选路
PHASE_ENCOUNTER = "encounter"  # 遭遇：逐张结算所选路径的两张卡（M3 起填充结算）
PHASE_ROUND_END = "round_end"  # 一轮两张卡结算完毕、尚未进入下一轮
PHASE_FINISHED = "finished"    # 8 轮全部结束（计分在 M5 接入）
