# data 数据层说明

本目录是游戏运行时唯一的内容来源，**引擎代码不写死任何卡牌、骰面或规则数值**。

## 文件清单

| 文件 | 来源 | 内容 |
|---|---|---|
| `cards.json` | 由 `tools/md_to_json.py` 从 `参考资料/卡牌.md` 生成 | 60 张卡牌 |
| `dice.json` | 由 `tools/md_to_json.py` 从 `参考资料/骰子.md` 生成 | 普通骰、强化骰各 6 面 |
| `config.json` | 人工维护（依据实体说明书） | setup、单人路径、战斗、计分、评级、存档、双人模式等固定规则常量 |

## 内容维护流程（重要）

人只编辑 **Markdown 源表**（`参考资料/卡牌.md`、`参考资料/骰子.md`，纯文本、git 可直接看差异），
改完在仓库根目录重新生成 JSON：

```bash
python3 tools/md_to_json.py
```

`tests/test_source_sync.py` 会校验 JSON 与 md 源表一致，忘记重新生成时测试会失败。
（历史上曾使用 xls 源表，`参考资料/亡命之途卡牌及骰子内容.xls` 仅作存档。）

## cards.json 单条结构

```json
{
  "id": "II-14",
  "stage": 2,
  "order_in_stage": 14,
  "scene": "场景描述文本",
  "scavenge": {"ammo": 0, "gas": 0, "meds": 1},
  "event_raw": "事件原文；'无' 表示没有事件",
  "zombies": {"count": 6, "level": 0},
  "score": 2
}
```

- `stage`：1/2/3，对应 I/II/III 阶段；
- `scavenge`：拾荒步骤获得的资源数（ammo 弹药 / gas 汽油 / meds 药剂）；
- `zombies.level`：0 = 普通战斗（普通骰）；1/2/3 = 屍群力量，近战时替换对应数量的强化骰；
- `event_raw` 目前只做忠实转存；把它解析成可执行效果是 `core/effects.py`（M3）的工作。

## dice.json 结构

```json
{"normal": [{"face": 1, "name": "空白", "desc": "……"}], "enhanced": [ ... ]}
```

## 兼容性约定

- JSON 必须是 UTF-8、无 BOM；键名保持英文稳定标识，面向玩家的中文文案放在值里；
- 结构变更时同步提升 `config.json` 的 `schema_version`，并更新 `tests/` 中的数据完整性测试。
