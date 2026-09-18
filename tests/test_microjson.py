"""设备端流式 JSON 解析器 microjson 与标准 json 的等价性测试。

microjson 跑在 MicroPython 上（无 ujson 大对象解析的内存峰值），
CPython 下用标准库逐文件、逐行、逐转义核对，确保语义一致。
"""
import io
import json

from zroad.platforms.device_mpy import microjson

DATA_FILES = [
    "cards_1.jsonl",
    "cards_2.jsonl",
    "cards_3.jsonl",
    "config.json",
    "dice.json",
]


def _data_path(name):
    import zroad
    from pathlib import Path
    return Path(zroad.__file__).parent / "data" / name


def test_jsonl_files_loads_and_stream_equivalent():
    """JSONL：整段 loads、流式顺序解析、skip_to_line 随机定位都等价。"""
    total = 0
    for name in DATA_FILES[:3]:
        raw = _data_path(name).read_bytes()
        expected = [json.loads(line) for line in
                    raw.decode("utf-8").splitlines() if line.strip()]
        # 逐对象整段解析
        for exp in expected:
            assert exp == microjson.loads(
                json.dumps(exp, ensure_ascii=False))
        # 流式顺序解析
        stream = microjson.stream_from(io.BytesIO(raw))
        got = []
        while True:
            value = microjson.parse_next(stream)
            if value is None:
                break
            got.append(value)
        assert got == expected
        # 按行号定位
        for idx, exp in enumerate(expected, start=1):
            stream = microjson.stream_from(io.BytesIO(raw))
            assert stream.skip_to_line(idx)
            assert microjson.parse_next(stream) == exp
        total += len(expected)
    assert total == 60  # 三阶段 16+22+22


def test_plain_json_files_equivalent():
    for name in DATA_FILES[3:]:
        raw = _data_path(name).read_bytes()
        expected = json.loads(raw.decode("utf-8"))
        assert expected == microjson.loads(raw)
        stream = microjson.stream_from(io.BytesIO(raw))
        assert microjson.parse_next(stream) == expected


def test_escapes_and_types():
    cases = [
        '{"a":"\\"\\\\\\/\\b\\f\\n\\r\\t","b":-3,"c":1.5,'
        '"d":true,"e":false,"f":null,"g":[1,2,3],'
        '"h":{"x":[{},[]]}}',
        '{"emoji":"\\uD83D\\uDE00","u":"\\u4e2d"}',
        '{"z": 0}',
    ]
    for text in cases:
        expected = json.loads(text)
        assert expected == microjson.loads(text)
        assert expected == microjson.loads(text.encode("utf-8"))
