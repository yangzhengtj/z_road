"""shell.py —— BeanpieChen 固件 shell 模块桩（真机模拟用）。"""

hidden = 0
shown = 0


def hide():
    global hidden
    hidden += 1


def show():
    global shown
    shown += 1
