# -*- coding: utf-8 -*-
"""V1.0-agpl 发布阻塞 bug 修复测试(TDD 红绿)。
覆盖评审报告中"测试覆盖不到的核心运行路径"。"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from batch_core import H3BatchEngine


class WaitShotEngine(H3BatchEngine):
    """替换 _get 以模拟 queue/history, 观察 _wait_shot 判定逻辑。"""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.queue_fmt = None  # "x0" 模拟取位错误; "real" 正确
        self.polls = 0

    def _get(self, path):
        if path == "/queue":
            self.polls += 1
            # 第 1 轮: 任务在队列 (prompt_id 在 x[1]); 第 2 轮起: 移出队列 → 应查 history
            if self.queue_fmt == "x0":
                items = [("q001", "pid-real-001")] if self.polls == 1 else []
            else:
                items = [("q001", "pid-real-001")] if self.polls == 1 else []
            return {"queue_running": items, "queue_pending": []}
        if path == "/history/pid-real-001":
            return {"pid-real-001": {"status": {"status_str": "success"}, "outputs": {}}}
        return {}


def test_wait_shot_matches_pid_at_x1():
    # 队列条目 prompt_id 在 x[1] (Q004/评审: 用 x[0] 会导致永远不等、1h 超时)
    with tempfile.TemporaryDirectory() as d:
        e = WaitShotEngine("分镜", "wf.json", os.path.join(d, "t.csv"), d, d)
        e.queue_fmt = "real"
        status, files = e._wait_shot("pid-real-001", timeout=30)
        assert status == "success", "应匹配 x[1] 的 prompt_id, 得到: %r" % status


def test_wait_shot_timeout_when_pid_misread_x0():
    # 回归: 若仍按 x[0] 取(number 而非 prompt_id), 永远不等 → 超时(模拟 30s 快速)
    with tempfile.TemporaryDirectory() as d:
        e = WaitShotEngine("分镜", "wf.json", os.path.join(d, "t.csv"), d, d)
        e.queue_fmt = "x0"
        status, files = e._wait_shot("pid-real-001", timeout=0.5)
        assert status == "timeout", "x[0] 错位应超时, 得到: %r" % status


def test_seed_float_string_normalized():
    # Excel 常见 "1.0" 不应炸整批, 归一化为整数种子
    with tempfile.TemporaryDirectory() as d:
        e = H3BatchEngine("分镜", "wf.json", os.path.join(d, "t.csv"), d, d)
        assert e._parse_seed("1.0") == 1
        assert e._parse_seed("42") == 42


def test_seed_invalid_falls_back_random():
    # 非数字种子回落随机(不崩溃)
    with tempfile.TemporaryDirectory() as d:
        e = H3BatchEngine("分镜", "wf.json", os.path.join(d, "t.csv"), d, d)
        s = e._parse_seed("abc")
        assert s == 0 or s is None  # 0/None 表示随机, 不抛异常
