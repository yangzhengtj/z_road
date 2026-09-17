"""zroad —— 末路求生·文字版（规则框架灵感来源于桌游 Hit Z Road）。

包结构：
    core/      可移植规则核心（MicroPython 兼容子集，零第三方依赖、无 I/O）
    platforms/ 各平台前端（desktop_rich / device_mpy）
    headless/  无头前端，供自动化测试与脚本对局
"""

__version__ = "0.8.0"
