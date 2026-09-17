"""device_mpy —— M5Stack Cardputer ADV（MicroPython）设备前端。

模块分工：
  screen.py      逻辑字符屏幕（30×8）与点阵字形绘制，设备/模拟器共用；
  font_data.py   tools/build_mpy_font.py 生成的点阵字库（勿手改）；
  stores.py      microSD 上的 JSON 存档/历史最佳；
  device_app.py  完整对局流程与小屏交互（不依赖硬件，可在电脑上测试）；
  mp_frontend.py 真机适配：dev 帧缓冲、TCA8418 键盘、/sd 路径；
  sim.py         电脑端 ANSI 模拟器与自动化测试后端。
"""
