#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
md_to_json.py —— 把 Markdown 版内容源表转换为程序使用的 JSON 数据。

数据源（用户手工维护，纯文本、可直接用 git 看差异）：
    参考资料/卡牌.md   —— 60 张卡牌的 Markdown 表格
    参考资料/骰子.md   —— 普通骰/强化骰骰面表
生成物（程序只读这些）：
    src/zroad/data/cards.json、src/zroad/data/dice.json
    （v0.7.1 起数据随包发布，放在包目录内，pipx 安装与 PyInstaller 打包都能带上）

本脚本只使用 Python 标准库，无需 pip 安装任何依赖。
在仓库根目录执行：  python3 tools/md_to_json.py
"""

import json
import os
import re
import sys

# 仓库根 = 本脚本所在 tools/ 的上一级（不依赖运行时的当前目录）
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROMAN_STAGE = {"I": 1, "II": 2, "III": 3}
SOURCE_DIR = os.path.join(REPO_ROOT, "参考资料")
# 数据 JSON 随包放在 src/zroad/data/（pyproject package-data 会带上）
OUT_DIR = os.path.join(REPO_ROOT, "src", "zroad", "data")


def parse_md_table(path, expected_cols):
    """读取一个 GFM Markdown 表格，返回数据行（跳过表头与分隔行）。

    每一行是去掉首尾竖线后切出的单元格字符串列表。
    """
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line.startswith("|"):
                continue
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if len(cells) != expected_cols:
                continue
            # 表头行 / |---|---| 分隔行跳过
            if set(cells[0]) <= set("-: ") or cells[0] in ("序号", "骰子数值"):
                continue
            rows.append(cells)
    return rows


def to_int(text, default=0):
    """空串转 default，其余转 int。"""
    text = str(text).strip()
    return default if text == "" else int(text)


def _player_event_text(raw):
    """规范化“事件”列（给玩家看的事件描述）。

    去掉“事件：”前缀；空单元格或“事件：无”统一为“无”。
    """
    text = (raw or "").strip().replace("<br>", "\n")
    if text.startswith("事件："):
        text = text[len("事件："):].strip()
    return text if text and text != "无" else "无"


def convert_cards(md_path):
    """卡牌.md（11 列）→ 卡牌记录列表。"""
    cards = []
    for cells in parse_md_table(md_path, 11):
        # 列：序号 | 阶段 | 场景描述 | 弹药 | 汽油 | 药剂 | 事件（玩家面）
        #     | 事件结算说明（开发面） | 丧尸数量 | 丧尸等级 | 得分
        (_no, card_id, scene, ammo, gas, meds, event_show,
         event_rule, z_count, z_level, score) = cells
        stage_roman, order = card_id.split("-")
        cards.append({
            "id": card_id,
            "stage": ROMAN_STAGE[stage_roman.strip()],
            "order_in_stage": int(order),
            "scene": scene,
            "scavenge": {
                "ammo": to_int(ammo),
                "gas": to_int(gas),
                "meds": to_int(meds),
            },
            # event_text：选路/遭遇时展示给玩家的事件描述（不参与结算）
            "event_text": _player_event_text(event_show),
            # event_raw：事件结算说明，effects.py 据此解析规则
            "event_raw": event_rule.strip() if event_rule.strip() else "无",
            "zombies": {"count": to_int(z_count), "level": to_int(z_level)},
            "score": to_int(score),
        })
    return cards


def convert_dice(md_path):
    """骰子.md（分两个二级标题，每个数据行 3 列）→ 骰面字典。"""
    dice = {"normal": [], "enhanced": []}
    title_map = {"普通骰子": "normal", "普通骰": "normal",
                 "强化骰子": "enhanced", "强化骰": "enhanced"}
    current = None
    face_pattern = re.compile(r"骰子数值为[：:]\s*(\d)")
    with open(md_path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("## "):
                current = title_map.get(line[3:].strip())
                continue
            line = line.strip()
            if current and line.startswith("|") and "骰子数值为" in line:
                cells = [c.strip() for c in line.strip("|").split("|")]
                if len(cells) != 3:
                    continue
                face = int(face_pattern.search(cells[0]).group(1))
                dice[current].append({"face": face, "name": cells[1], "desc": cells[2]})
    for kind, faces in dice.items():
        faces.sort(key=lambda item: item["face"])
        if [f["face"] for f in faces] != [1, 2, 3, 4, 5, 6]:
            raise ValueError("%s 骰面不完整：%s" % (kind, [f["face"] for f in faces]))
    return dice


def main():
    cards_md = os.path.join(SOURCE_DIR, "卡牌.md")
    dice_md = os.path.join(SOURCE_DIR, "骰子.md")
    for path in (cards_md, dice_md):
        if not os.path.exists(path):
            sys.exit("找不到内容源文件：%s" % path)

    cards = convert_cards(cards_md)
    dice = convert_dice(dice_md)
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "cards.json"), "w", encoding="utf-8") as f:
        json.dump(cards, f, ensure_ascii=False, indent=2)
    with open(os.path.join(OUT_DIR, "dice.json"), "w", encoding="utf-8") as f:
        json.dump(dice, f, ensure_ascii=False, indent=2)

    stage_counts = {1: 0, 2: 0, 3: 0}
    for card in cards:
        stage_counts[card["stage"]] += 1
    print("已生成卡牌 %d 张，分阶段：%s" % (len(cards), stage_counts))
    print("已生成骰面：普通骰 %d 面，强化骰 %d 面"
          % (len(dice["normal"]), len(dice["enhanced"])))


if __name__ == "__main__":
    main()
