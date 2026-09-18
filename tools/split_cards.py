#!/usr/bin/env python3
"""split_cards.py —— 由 cards.json 生成设备端按阶段拆分的 JSONL 卡牌文件。

Cardputer ADV 没有 PSRAM，一次性解析 60 张卡的 cards.json 需要约 96KB，
设备导入全部模块后只剩约 57KB 堆。因此设备端数据改为：

    data/cards_1.jsonl   阶段 I  16 张，每行一张卡的 JSON
    data/cards_2.jsonl   阶段 II 22 张
    data/cards_3.jsonl   阶段 III 22 张

行号与卡 id 序号一一对应（第 N 行即 id 为 "<阶段前缀>-N" 的卡），
设备端 StageCatalog 据此按 id 直接 seek 到对应行、逐张懒加载，
解析峰值只有一张卡的大小（约 2KB）。

桌面端仍使用 pretty 的 cards.json（权威数据，由 md_to_json.py 从
参考资料/卡牌.md 生成）；本脚本在 md_to_json.py 末尾会被自动调用。
"""

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src", "zroad", "data", "cards.json")

STAGE_PREFIX = {1: "I", 2: "II", 3: "III"}


def main():
    with open(SRC, encoding="utf-8") as f:
        cards = json.load(f)
    total = 0
    for stage in (1, 2, 3):
        group = sorted((c for c in cards if c["stage"] == stage),
                       key=lambda c: c["order_in_stage"])
        out = os.path.join(ROOT, "src", "zroad", "data",
                           "cards_%d.jsonl" % stage)
        with open(out, "w", encoding="utf-8") as f:
            for card in group:
                f.write(json.dumps(card, ensure_ascii=False,
                                   separators=(",", ":")))
                f.write("\n")
        size = os.path.getsize(out)
        print("%s %d 张 %d bytes" % (out, len(group), size))
        total += len(group)
    if total != len(cards):
        raise SystemExit("拆分数量 %d 与 cards.json 总数 %d 不一致"
                         % (total, len(cards)))


if __name__ == "__main__":
    main()
