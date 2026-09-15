"""records_store.py —— 跨对局历史最佳记录（M6）。

与存档同目录（默认仓库根 saves/，可用 ZROAD_SAVE_DIR 覆盖），单文件
records.json，结构：
    {"format": "zroad-records", "format_version": 1,
     "best": {"score": 22, "rating_label": "...", "difficulty": "easy",
              "eliminated": false, "seed": 7, "finished_at": "2026-09-15 21:00:00"}
     或 best=None（还没有通关记录）}

只记录“通关（未全灭）”的最高分；全队覆没的对局不参与历史最佳。
桌面层模块，允许使用 pathlib/datetime/json；core 不依赖它。
"""

import json
from datetime import datetime
from pathlib import Path

from .save_store import default_save_dir

RECORDS_FILE = "records.json"


class RecordsStore(object):
    """历史最佳的读写，纯文件操作、不含游戏规则。"""

    def __init__(self, save_dir=None):
        self.dir = Path(save_dir) if save_dir else default_save_dir()
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / RECORDS_FILE

    # ---------- 读 ----------
    def _empty(self):
        return {"format": "zroad-records", "format_version": 1, "best": None}

    def load(self):
        """读取全部记录；文件缺失或损坏时返回空结构（不影响游戏）。"""
        if not self.path.exists():
            return self._empty()
        try:
            with self.path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (ValueError, OSError):
            return self._empty()
        if not isinstance(data, dict) or "best" not in data:
            return self._empty()
        return data

    def best(self):
        """返回历史最佳 dict，没有记录时返回 None。"""
        return self.load().get("best")

    # ---------- 写 ----------
    def record_result(self, score, rating_label=None, difficulty="easy",
                      eliminated=False, seed=None):
        """登记一局结果。

        通关且分数严格高于历史最佳才更新（并列不覆盖，保留最早达成）。
        返回 (是否刷新纪录, 最新最佳 dict)。
        """
        data = self.load()
        current = data.get("best")
        updated = False
        # 全灭的失败旅程不进入历史最佳
        if not eliminated:
            if current is None or score > current.get("score", -1):
                current = {"score": int(score),
                           "rating_label": rating_label or "",
                           "difficulty": difficulty,
                           "eliminated": False,
                           "seed": seed,
                           "finished_at": datetime.now().strftime(
                               "%Y-%m-%d %H:%M:%S")}
                data["best"] = current
                updated = True
        if updated:
            with self.path.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
        return updated, current
