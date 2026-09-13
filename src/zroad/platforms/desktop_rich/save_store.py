"""save_store.py —— Mac 端存档仓库（轮末自动档 + 3 个手动槽）。

存档是一个 JSON 文件，内容 = 固定外壳 + engine.snapshot() 的整局状态：
    {"format": "zroad-save", "format_version": 1,
     "slot": "auto" / "manual_1".., "saved_at": 时间字符串,
     "state": <GameState.to_dict()>}

存档目录默认在仓库根的 saves/（已被 .gitignore 忽略），可用环境变量
ZROAD_SAVE_DIR 覆盖。桌面层允许使用 pathlib / datetime / json，core 不依赖本模块。
"""

import json
import os
from datetime import datetime
from pathlib import Path

AUTO_SLOT = "auto"
MANUAL_SLOTS = ("manual_1", "manual_2", "manual_3")
SLOT_LABELS = {"auto": "自动存档", "manual_1": "手动槽 1",
               "manual_2": "手动槽 2", "manual_3": "手动槽 3"}


def default_save_dir():
    """存档目录：环境变量优先，否则用仓库根下的 saves/。"""
    env_dir = os.environ.get("ZROAD_SAVE_DIR")
    if env_dir:
        return Path(env_dir)
    # 本文件：<仓库>/src/zroad/platforms/desktop_rich/save_store.py → 上溯 4 级到仓库根
    return Path(__file__).resolve().parents[4] / "saves"


class SaveStore(object):
    """负责存档文件的增删查改，不包含任何游戏规则。"""

    def __init__(self, save_dir=None):
        self.dir = Path(save_dir) if save_dir else default_save_dir()
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, slot):
        return self.dir / ("%s.json" % slot)

    def exists(self, slot):
        return self._path(slot).exists()

    def write(self, slot, engine):
        """把引擎当前整局状态写入指定槽位，返回写入的文件路径。"""
        payload = {
            "format": "zroad-save",
            "format_version": 1,
            "slot": slot,
            "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "state": engine.snapshot(),
        }
        path = self._path(slot)
        with path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        return path

    def read(self, slot):
        """读取槽位，返回整局状态 dict（GameState.from_dict 可直接消费）。"""
        with self._path(slot).open("r", encoding="utf-8") as f:
            payload = json.load(f)
        return payload["state"]

    def read_envelope(self, slot):
        """读取完整外壳（含保存时间等元信息）。"""
        with self._path(slot).open("r", encoding="utf-8") as f:
            return json.load(f)

    def delete(self, slot):
        path = self._path(slot)
        if path.exists():
            path.unlink()

    def preview(self, slot):
        """生成槽位摘要（菜单列表用）；无存档返回 None。"""
        if not self.exists(slot):
            return None
        envelope = self.read_envelope(slot)
        state = envelope["state"]
        player = state["player"]
        return {
            "slot": slot,
            "label": SLOT_LABELS.get(slot, slot),
            "saved_at": envelope.get("saved_at", ""),
            "round_no": state.get("round_no", 0),
            "phase": state.get("phase", ""),
            "survivors": player.get("survivors", 0),
            "won_count": len(player.get("won_card_ids", [])),
        }

    def list_all(self):
        """按 [自动, 手动1..3] 顺序返回摘要（不存在的槽也占位，值为 None）。"""
        slots = (AUTO_SLOT,) + MANUAL_SLOTS
        return [self.preview(slot) for slot in slots]
