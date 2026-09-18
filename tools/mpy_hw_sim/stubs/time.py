"""time.py —— time 模块桩（真机模拟用）。

真机上 MPKeys.get_key() 在等待按键时循环 sleep_ms(20)。模拟环境把
sleep_ms 变成“喂下一个脚本按键”的节拍器，从而驱动整局自动对局。
"""

pump = None          # 由 hw_runner 设置：无按键时调用，返回一个按键 token
sleep_calls = 0


def sleep_ms(ms):
    global sleep_calls
    sleep_calls += 1
    if pump is not None:
        pump()


def sleep(seconds):
    # 错误屏会 sleep(45)，模拟环境不等待
    return
