"""ports.py —— 核心层与“外部世界”之间的抽象端口（接口定义，无实现）。

核心层（engine/combat/...）不直接 print、input、画图、存文件，而是调用这里定义
的端口方法。不同平台各自实现这些方法：

    DesktopRichPort   —— Mac 上用 Rich 渲染彩色终端界面；
    HeadlessPort      —— 测试时按脚本自动应答、收集输出；
    DevicePort        —— 阶段 2 在 Cardputer 上用帧缓冲绘制文字。

这样“游戏怎么玩”（core）与“游戏长什么样、怎么操作”（platforms）彻底解耦，
移植设备时核心一行不改。
"""


class UserInterfacePort:
    """表现层必须实现的接口（Python 无接口关键字，用类充当接口契约）。

    方法命名分两类：
      show_*：核心 → 玩家，只负责呈现，不返回值；
      ask_* ：核心 → 玩家，需要玩家决策，返回玩家选择。
    """

    # ---------- 呈现类 ----------
    def show_scene_title(self, round_no, stage, path_no=None):
        """显示当前轮次/阶段标题，例如“第 3 轮 · 阶段 II”。"""
        raise NotImplementedError

    def show_panel(self, title, lines):
        """显示一个信息面板（资源、牌面、战斗状态等）。"""
        raise NotImplementedError

    def show_log(self, text):
        """追加一条事件日志（拾荒、事件、掷骰结果、伤亡等）。"""
        raise NotImplementedError

    def show_card(self, card, revealed):
        """显示一张卡；revealed=False 时只显示卡背（未知）。"""
        raise NotImplementedError

    # ---------- 询问类 ----------
    def ask_choice(self, prompt, options):
        """让玩家在若干选项中选择一个。

        参数:
            prompt: 问题文本；
            options: 选项列表，元素为 (key, label)，key 是程序标识，label 是显示文本。
        返回:
            玩家选中项的 key。
        """
        raise NotImplementedError

    def ask_confirm(self, prompt, default=False):
        """是/否确认，返回 bool。"""
        raise NotImplementedError

    def ask_spend_resources(self, prompt, total, available):
        """让玩家分配支付若干“任意资源”（例如路径三付 2 个任意资源）。

        参数:
            total: 需要支付的总数；
            available: 当前三种资源余量 dict。
        返回:
            支付构成 dict，如 {"ammo": 1, "gas": 1, "meds": 0}。
        """
        raise NotImplementedError


class SavePort:
    """存档端口：把整局状态序列化保存 / 读回（实现随阶段 1）。

    核心只负责提供可 JSON 序列化的状态 dict；落盘位置由平台决定
    （Mac 写文件，Cardputer 写 Flash/microSD）。
    """

    def save(self, state_dict):
        raise NotImplementedError

    def load(self):
        """返回最近一次存档 dict；无存档返回 None。"""
        raise NotImplementedError
