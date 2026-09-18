"""card_catalog.py —— 设备端分阶段卡牌目录（内存优化，Cardputer 无 PSRAM）。

真机内存实测：导入完全部模块并建好界面对象后只剩约 30KB 堆，而一次性
解析 60 张卡的 cards.json 需要约 96KB，必然 MemoryError。本目录把卡牌
拆成三个 JSONL 文件（cards_1/2/3.jsonl，每行一张卡），并做到：

  1. 开局建库只洗 id（id 列表约 1KB），不解析任何卡牌正文；
  2. 进入某阶段时逐行解析，只给登场卡保留“迷你卡”
     （id/stage/scene/score，约 100 字节/张，供战斗阶段标记、
     终局计分与对局回看）；
  3. 引擎/界面用 catalog[card_id] 取卡时，若还是迷你卡，就按 id 即时
     解析对应 JSONL 行升级为完整卡（解析峰值只有一张卡约 2KB）；
     一轮结束后完整卡全部降回迷你卡，任意时刻完整卡不超过桌上的
     6 张（约 10KB）；
  4. 只需要 score/scene/stage 的场合（计分、回看、当前得分）一律用
     catalog.get(card_id)，取到迷你卡即可、不触发升级；
  5. 对引擎而言它仍是一个普通 {id: card} dict（dict 子类），
     Engine.new_solo 通过 prebuilt_decks 接收预先洗好的牌库，
     随机序列与桌面端一次性构建完全一致（同 seed、同 rng 消费顺序）。

注意：MicroPython 的 dict 子类不会回调 __missing__，所以懒加载放在
本类的 __getitem__ 里（catalog[id] 升级），而不是卡牌对象的 __missing__。
"""

import gc

from . import microjson
from ...core import deck as deck_mod

STAGES = (1, 2, 3)
STAGE_PREFIX = {1: "I", 2: "II", 3: "III"}

# 迷你卡保留的字段：战斗重建要 stage，终局计分要 score，回要看 scene
_MINI_FIELDS = ("id", "stage", "scene", "score")


class StageCatalog(dict):
    """按阶段懒加载的卡牌目录，接口与普通 dict 一致。"""

    def __init__(self, platform, config):
        super().__init__()
        self.p = platform
        self.config = config
        self.active = None        # {阶段: [登场 id...]}（引擎会原地摸牌）
        self.removed = None       # {阶段: [剔除 id...]}
        self.loaded_stage = 0     # 当前对局进行到的阶段，0=尚未
        # 已建立迷你卡的阶段集合（分阶段预载，见 preload_stage_minis）
        self.mini_stages = set()
        self._full = {}           # {id: True} 当前为完整卡的 id
        # 升级为完整卡时把原迷你卡对象暂存这里，降级时原样放回：
        # 零分配，避免“重建迷你卡”在堆碎片里再挖坑。
        self._mini_backup = {}
        # 连续内存储备块（见 release_reserve/acquire_reserve）。
        self.RESERVE_SIZE = 2 * 1024
        self._reserve = None

    # ---------- 连续内存储备块的借还 ----------
    #
    # MicroPython 的 GC 不压缩堆：游戏中每屏界面都会产生大量短命小对象，
    # 常驻小对象（统计记录、点阵缓存等）夹在其中，时间一久空闲堆就被
    # 切成碎片，常出现“空闲十几 KB 却分不出 1KB 连续块”。储备块在堆
    # 还整齐时占住一块连续内存，平时持有（不参与碎片分配），只在
    # “升级完整卡（解析峰值约 2KB）/长文本折行/存档序列化”前临时释放，
    # 用完（卡降回迷你卡后）立刻 gc 并重新占回。

    def release_reserve(self):
        """临时释放储备块，供接下来的较大短命分配使用。"""
        if self._reserve is not None:
            self._reserve = None
            gc.collect()

    def acquire_reserve(self):
        """大分配结束后重新占住一块连续内存；堆极度紧张时允许失败。"""
        gc.collect()
        if self._reserve is None:
            try:
                self._reserve = bytearray(self.RESERVE_SIZE)
            except MemoryError:
                self._reserve = None
        gc.collect()

    # ---------- dict 取卡：下标访问时迷你卡即时升级 ----------

    def __getitem__(self, card_id):
        card = super().__getitem__(card_id)
        # 注意：这里不能用 self._full.get(card_id) 判断（MicroPython
        # 旧版本 dict.get 不回调子类钩子，且语义也不如 in 清晰）。
        if card_id not in self._full:
            self._load_full(card_id, card)
        return super().__getitem__(card_id)

    # 注意：不覆盖 get()。catalog.get(id) 用于计分/回看等只读
    # score/scene/stage 的场合，迷你卡已包含这些字段，无需升级，
    # 避免终局时把全部卡牌重新解析成完整卡导致内存爆掉。

    # ---------- 分阶段预载（在任何界面帧之前，堆最整齐时完成） ----------
    #
    # 内存账：60 张迷你卡（dict + 场景串）常驻约 16KB，在无 PSRAM 的
    # 设备上会把后期水位压到危险线。实测改为“只常驻当前阶段 + 已赢得
    # 的卡”：开机只载阶段 I（16 张），阶段切换时先整体降级并释放旧
    # 阶段迷你卡（保留 won_card_ids 里的赢家卡，终局计分/回看要用），
    # 再载新阶段。任意时刻常驻迷你卡 ≤22 张 + 至多 8 张赢家卡，
    # 省下约 8~10KB 常驻堆。

    def preload_stage_minis(self, stage):
        """把某一阶段的卡建成迷你卡（含当局会被剔除的 4 张）。

        必须在该阶段第一次界面渲染之前调用：MicroPython 的 GC 不压缩
        堆，若等界面帧产生过大量短命对象后再建常驻迷你卡，dict/字符串
        会落进回收后的碎片空洞，把最大连续块切碎，游戏中途解析完整卡
        （约 2KB）就会 MemoryError。阶段切换时先释放旧阶段（见
        ensure_stage），净增常驻很小，新迷你卡仍能排在较整齐的堆上。

        场景文本是迷你卡里唯一的“长对象”（最长 131 个汉字）。做法：
        解析时把场景原文追加进连续 blob，按 8 张一批在紧凑循环里切出
        最终字符串，让它们在堆上连续排开，避免钉在临时对象空洞之间。
        """
        if stage in self.mini_stages:
            return
        gc.collect()
        BATCH = 8

        def flush_batch(blob, offsets):
            # 紧凑循环：只分配最终场景字符串，逐个连续落堆。
            # offsets 里直接放迷你卡 dict（不能走 self[id]，那会触发
            # 完整卡升级；嵌套函数里也无法用零参 super()）。
            for mini, start, end in offsets:
                mini["scene"] = bytes(blob[start:end]).decode("utf-8")

        blob = bytearray()
        offsets = []   # (mini dict, start, end)
        path = self.p.data_path("cards_%d.jsonl" % stage)
        with open(path, "rb") as f:
            stream = microjson.stream_from(f)
            while True:
                # 流式解析：不构造整行缓冲，32B 小块读取，碎片堆
                # 上也能逐张解析。
                gc.collect()
                card = microjson.parse_next(stream)
                if card is None:
                    break
                scene = card.get("scene", "")
                start = len(blob)
                # 场景内不会出现 NUL，用 0 作分隔
                blob.extend(scene.encode("utf-8"))
                end = len(blob)
                blob.append(0)
                mini = {"id": card["id"], "stage": card["stage"],
                        "scene": None, "score": card.get("score", 0)}
                offsets.append((mini, start, end))
                super().__setitem__(card["id"], mini)
                del card
                if len(offsets) >= BATCH:
                    gc.collect()
                    flush_batch(blob, offsets)
                    blob = bytearray()
                    offsets = []
                    gc.collect()
        gc.collect()
        flush_batch(blob, offsets)
        del offsets
        del blob
        self.mini_stages.add(stage)
        gc.collect()

    def release_stage_minis(self, stage, keep_ids=()):
        """释放某阶段全部迷你卡（完整卡先降级），keep_ids 里的赢家卡保留。

        终局计分（scoring）只访问玩家赢取的卡，统计回看（statistics）
        在每轮结束时已把场景复制进 round_log，因此其余旧阶段卡可以
        整体删除；被剔除从未登场的卡同样在这里释放。
        """
        prefix = STAGE_PREFIX[stage] + "-"
        keep = keep_ids if isinstance(keep_ids, (set, frozenset, tuple)) \
            else tuple(keep_ids)
        # 先把完整卡降回迷你卡（否则会连同备份一起泄漏）
        self.release_stage_full(stage)
        for card_id in list(self.keys()):
            if card_id.startswith(prefix) and card_id not in keep:
                super().__delitem__(card_id)
                self._mini_backup.pop(card_id, None)
        if stage in self.mini_stages:
            self.mini_stages.remove(stage)
        # 赢家卡仍在，但所属阶段已移出 mini_stages，不影响后续逻辑
        gc.collect()

    # ---------- 开局建库 ----------

    def setup_new(self, rng):
        """新对局：只洗 id，不解析正文；随后载入阶段 I 迷你卡。

        rng 由调用方预先创建并播种（设备端在堆还整齐时就分配好，
        洗牌后同一个 rng 直接交给引擎，无需重放或二次分配）。
        """
        # 建库前先彻底回收：主菜单/选难度等界面帧产生过大量短命对象，
        # 若不回收就分配常驻的牌库 id 与迷你卡，这些常驻对象会落进短命
        # 对象之间的空洞，GC 后整堆被切碎（无压缩 GC），后期连 1KB 的
        # 连续块都分不出来。
        gc.collect()
        # 同一目录实例会被多局复用（主菜单可反复开局），先把上一局可能
        # 残留的完整卡标记/备份清空，并把迷你卡收敛到阶段 I（释放上一局
        # 阶段 II/III 的预载；赢家卡跨局不再保留）。
        self._full = {}
        self._mini_backup = {}
        for st in STAGES:
            if st != 1 and st in self.mini_stages:
                self.release_stage_minis(st)
        setup = self.config["setup"]
        pool_sizes = setup["stage_pool_sizes"]
        active = {}
        removed = {}
        for offset, stage in enumerate(STAGES):
            prefix = STAGE_PREFIX[stage]
            ids = ["%s-%d" % (prefix, n)
                   for n in range(1, pool_sizes[offset] + 1)]
            active[stage], removed[stage] = \
                deck_mod.build_one_stage_deck_from_ids(
                    ids, stage, offset, rng, setup)
        self.active = active
        self.removed = removed
        self.rng = rng   # 交给引擎继续使用（同实例，随机序列天然连续）
        # 阶段 I 迷你卡开机时已在干净堆上预载；若被上一局流程释放过，
        # 这里补载。
        self.loaded_stage = 1
        self.preload_stage_minis(1)
        # 上一局终局时储备块处于释放状态，新局建库后重新占住。
        self.acquire_reserve()

    def setup_restore(self, state):
        """读档：接收存档里的牌库 id；迷你卡开机时已全部预载。"""
        gc.collect()   # 同 setup_new：常驻对象分配前先清空碎片
        self._full = {}
        self._mini_backup = {}
        self.active = state.stage_decks
        self.removed = state.removed_cards
        self.loaded_stage = 1
        # 与 setup_new 相同：把迷你卡收敛到阶段 I 并补载。
        for st in STAGES:
            if st != 1 and st in self.mini_stages:
                self.release_stage_minis(st)
        self.preload_stage_minis(1)
        self.acquire_reserve()

    # ---------- 阶段切换 / 完整卡升降级 ----------

    def ensure_stage(self, stage, won_ids=()):
        """阶段切换点（每轮规划前调用）：释放旧阶段迷你卡（保留赢家卡），
        并预载新阶段的迷你卡。

        won_ids 是玩家截至目前赢取的全部卡牌 id：旧阶段里只有这些卡
        还要留到终局计分与对局回看，其余（含当局被剔除的卡）全部释放。
        """
        if stage is None or stage == self.loaded_stage:
            # 同一阶段内重复调用也要保证迷你卡已载（读档恢复的边界情况）
            if stage is not None and stage not in self.mini_stages:
                self.release_reserve()
                self.preload_stage_minis(stage)
            return
        # 预载新阶段需要约 5KB 的连续空间（解析瞬态 + 新常驻迷你卡），
        # 先释放 2KB 储备块（play() 主循环随后本来就会释放它）。
        self.release_reserve()
        if self.loaded_stage and self.loaded_stage != stage:
            # 离开旧阶段：完整卡降级 + 释放非赢家迷你卡
            self.release_stage_minis(self.loaded_stage, won_ids)
        self.loaded_stage = stage
        self.preload_stage_minis(stage)

    def release_card(self, card_id):
        """单张完整卡结算完后立即降回迷你卡（遭遇结算后调用）。

        完整卡约 1.5~2KB，遭遇流程结束后引擎只保留卡牌 id，卡面内容
        不再需要；立刻降级让同阶段内存峰值从“多张完整卡”降到一张以内。
        """
        if self._full.get(card_id):
            backup = self._mini_backup.pop(card_id, None)
            super().__setitem__(card_id,
                                backup if backup is not None
                                else self._make_mini(super().__getitem__(card_id)))
            del self._full[card_id]
            gc.collect()

    def release_stage_full(self, stage):
        """把某阶段所有完整卡降回迷你卡（阶段切换时调用，控制内存上限）。

        注意不能遍历 active[stage]：引擎摸牌会把该列表 pop 空，已摸到的
        卡（赢取/弃置/检视过）一样占着完整卡内存，必须按 id 前缀把本阶段
        仍处于完整状态的卡全部降级。
        """
        prefix = STAGE_PREFIX[stage] + "-"
        for card_id in list(self._full.keys()):
            if card_id.startswith(prefix):
                backup = self._mini_backup.pop(card_id, None)
                if backup is not None:
                    super().__setitem__(card_id, backup)
                else:
                    full = super().__getitem__(card_id)
                    super().__setitem__(card_id, self._make_mini(full))
                del self._full[card_id]
        gc.collect()

    def _load_full(self, card_id, mini):
        """按 id 序号读取 JSONL 对应行，把迷你卡原地替换为完整卡。

        JSONL 行号即卡 id 的序号（split_cards.py 按 order_in_stage
        排序写出，且已校验 order_in_stage 与序号一致）。
        """
        stage = self._stage_of_id(card_id)
        number = int(card_id.split("-")[1])
        path = self.p.data_path("cards_%d.jsonl" % stage)
        # 释放连续储备块：完整卡解析峰值约 2KB，必须在连续空间里完成。
        # 储备块在卡降回迷你卡（release_card/release_stage_full）后占回。
        self.release_reserve()
        gc.collect()
        try:
            self._read_full_line(path, number, card_id, mini)
        except MemoryError:
            # 碎片堆上解析可能中途失败：回收后重读一次（本函数无副作用，
            # 失败时不会留下半截卡）。
            gc.collect()
            self._read_full_line(path, number, card_id, mini)

    def _read_full_line(self, path, number, card_id, mini):
        """打开 JSONL 定位到目标行并升级为完整卡（_load_full 的可重试部分）。"""
        # 全程流式：32B 小块跳行 + 流式解析，不构造整行缓冲，碎片堆上
        # 单次最大连续分配约为最长字段（300~400B）。
        with open(path, "rb") as f:
            stream = microjson.stream_from(f)
            if not stream.skip_to_line(number):
                raise KeyError(card_id)
            card = microjson.parse_next(stream)
            if card is None or card["id"] != card_id:
                raise KeyError(card_id)
            # 暂存原迷你卡，降级时原样放回（零分配）
            self._mini_backup[card_id] = mini
            super().__setitem__(card_id, card)
            self._full[card_id] = True
            del card
            gc.collect()

    def _stage_of_id(self, card_id):
        prefix = card_id.split("-")[0]
        for stage in STAGES:
            if STAGE_PREFIX[stage] == prefix:
                return stage
        return self.loaded_stage

    def _make_mini(self, card):
        mini = {}
        for key in _MINI_FIELDS:
            if key in card:
                mini[key] = card[key]
        return mini
