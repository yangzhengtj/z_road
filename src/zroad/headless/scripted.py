"""scripted.py —— 无头脚本玩家：不经过任何界面，自动驱动引擎打完一整局。

两个用途：
  1. 自动化测试/平衡模拟：用固定策略把 8 轮全部走完，验证主循环闭环与计分；
  2. 阶段 2 移植 Cardputer 时，它就是“前端如何驱动 core”的最小参考实现。

策略刻意简单（路径二、能付就付、近战药剂优先救咬伤、有余弹先远程），
不追求胜率，只保证每一步都走引擎的公开接口。
"""

from zroad.core import effects as fx

# 药剂使用优先级：咬伤救人 > 额外击杀 > 伺机而动
_MEDS_PRIORITY = {"bite": 0, "extra": 1, "wait": 2}


def scripted_effect_decision(engine):
    """对应 app.make_decision：自动应答立即效果的玩家抉择。"""
    needed = engine.peek_card_decision()
    if needed is None:
        return None
    if needed["type"] == fx.DECISION_CHOOSE_RESOURCE:
        return {"resource": needed["available"][0]}
    if needed["type"] == fx.DECISION_PAY_GAS:
        # 尽量足额支付，不够就付光
        return {"gas_pay": min(needed["required"], needed["available_gas"])}
    if needed["type"] == fx.DECISION_ZEALOT_UPKEEP:
        return {"keep": needed["can_keep"]}
    return None


def scripted_fight(engine, use_ranged=True):
    """用简单策略打完当前挂起的战斗，返回最终结果 won/lost。"""
    final = None
    while engine.state.pending_combat is not None:
        actions = {a["action"]: a for a in engine.combat_actions()}
        if use_ranged and "ranged" in actions and engine.state.player.resources.ammo >= 1:
            burst = min(2, actions["ranged"]["max_burst"])
            result = engine.combat_ranged(burst)
            if result.get("result") == "won":
                return "won"
            continue
        view = engine.combat_roll_melee()
        usable = [o for o in view["opportunities"] if o["available"]]
        usable.sort(key=lambda o: _MEDS_PRIORITY[o["opportunity"]])
        meds = engine.state.player.resources.meds
        idxs = [o["idx"] for o in usable[:meds]]
        result = engine.combat_resolve_melee(idxs)
        if result["result"] in ("won", "lost"):
            final = result["result"]
            break
    return final


def play_full_game(cards, config, seed=None, path_index=2, use_ranged=True):
    """从建局打到 finished，返回引擎实例（终局用 engine.final_report()）。"""
    from zroad.core.engine import Engine
    engine = Engine.new_solo(cards, config, seed=seed)
    while not engine.is_finished():
        engine.choose_path(path_index)
        while engine.state.phase == "encounter":
            decision = scripted_effect_decision(engine)
            outcome = engine.begin_card_resolution(decision)
            if outcome.get("combat"):
                scripted_fight(engine, use_ranged=use_ranged)
        if engine.state.phase == "round_end":
            engine.close_round()
    return engine
