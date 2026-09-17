"""stores.py —— 设备端存档与历史最佳（microSD 上的 JSON 文件）。

与桌面端 save_store.py / records_store.py 文件格式完全一致，存档理论上
可以在电脑和 Cardputer 之间互相拷贝读取（core 的状态结构是同一份）。

为兼容 MicroPython：
  · json/os 模块由平台入口注入（设备传 ujson、uos，模拟器传标准库）；
  · 不用 pathlib/datetime，时间字符串由平台的 now() 提供（设备 RTC 未校时
    时可能为空字符串，不影响读档）。
"""

AUTO_SLOT = "auto"
MANUAL_SLOTS = ("manual_1", "manual_2", "manual_3")
SLOT_LABELS = {"auto": "自动存档", "manual_1": "手动槽 1",
               "manual_2": "手动槽 2", "manual_3": "手动槽 3"}


class JsonFileStore(object):
    """存档仓库 + 历史最佳，构造时注入平台依赖。

    参数:
        root: 数据根目录（设备为 /sd/zroad，模拟器为临时/仓库目录）；
        json_mod: ujson 或 json；
        os_mod: uos 或 os；
        now_func: 返回 'YYYY-MM-DD HH:MM:SS' 的函数（可为空串）。
    """

    def __init__(self, root, json_mod, os_mod, now_func=None):
        self.root = root
        self.json = json_mod
        self.os = os_mod
        self.now = now_func or (lambda: "")
        self.save_dir = root + "/saves"
        self._mkdir(self.root)
        self._mkdir(self.save_dir)

    def _mkdir(self, path):
        try:
            self.os.mkdir(path)
        except Exception:
            pass  # 已存在即视为成功（MicroPython 无 exist_ok 参数）

    def _path(self, slot):
        return self.save_dir + "/" + slot + ".json"

    def _exists(self, path):
        try:
            self.os.stat(path)
            return True
        except OSError:
            return False

    def _read_json(self, path):
        with open(path, "r") as f:
            return self.json.loads(f.read())

    def _write_json(self, path, obj):
        with open(path, "w") as f:
            f.write(self.json.dumps(obj))

    # ---------------- 存档 ----------------

    def exists(self, slot):
        return self._exists(self._path(slot))

    def write(self, slot, engine):
        payload = {
            "format": "zroad-save",
            "format_version": 1,
            "slot": slot,
            "saved_at": self.now(),
            "state": engine.snapshot(),
        }
        self._write_json(self._path(slot), payload)
        return self._path(slot)

    def read(self, slot):
        return self._read_json(self._path(slot))["state"]

    def preview(self, slot):
        if not self.exists(slot):
            return None
        try:
            envelope = self._read_json(self._path(slot))
        except Exception:
            return None
        state = envelope.get("state", {})
        player = state.get("player", {})
        return {
            "slot": slot,
            "label": SLOT_LABELS.get(slot, slot),
            "saved_at": envelope.get("saved_at", ""),
            "round_no": state.get("round_no", 0),
            "phase": state.get("phase", ""),
            "difficulty": state.get("difficulty", "easy"),
            "survivors": player.get("survivors", 0),
            "won_count": len(player.get("won_card_ids", [])),
        }

    def list_all(self):
        slots = (AUTO_SLOT,) + MANUAL_SLOTS
        return [self.preview(slot) for slot in slots]

    # ---------------- 历史最佳 ----------------

    def _records_path(self):
        return self.root + "/records.json"

    def _load_records(self):
        if not self._exists(self._records_path()):
            return {"format": "zroad-records", "format_version": 1, "best": None}
        try:
            data = self._read_json(self._records_path())
        except Exception:
            return {"format": "zroad-records", "format_version": 1, "best": None}
        if not isinstance(data, dict) or "best" not in data:
            return {"format": "zroad-records", "format_version": 1, "best": None}
        return data

    def best_record(self):
        return self._load_records().get("best")

    def record_result(self, score, rating_label="", difficulty="easy",
                      eliminated=False, seed=None):
        """通关且分数严格更高才刷新；返回 (是否刷新, 最佳 dict)。"""
        data = self._load_records()
        current = data.get("best")
        updated = False
        if not eliminated:
            if current is None or score > current.get("score", -1):
                current = {"score": int(score),
                           "rating_label": rating_label or "",
                           "difficulty": difficulty,
                           "eliminated": False,
                           "seed": seed,
                           "finished_at": self.now()}
                data["best"] = current
                updated = True
        if updated:
            self._write_json(self._records_path(), data)
        return updated, current
