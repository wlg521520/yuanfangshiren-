# -*- coding: utf-8 -*-
"""核心转换逻辑的无界面测试：验证 build_cmd / probe_duration / convert_one 全链路。"""
import os
import sys
import queue
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from testutil import setup_ffmpeg_path, PROJ  # noqa: E402
setup_ffmpeg_path()
import ts2mp4 as T

SRC = os.path.join(PROJ, "testdata", "sample.ts")
OUT = os.path.join(PROJ, "testdata", "out", "sample.mp4")

print("FFMPEG =", T.FFMPEG)
print("FFPROBE =", T.FFPROBE)
assert T.FFMPEG, "ffmpeg 未找到"

# 1) 时长探测
dur = T.probe_duration(SRC)
print("duration =", dur)
assert dur and 5.0 < dur < 7.0, f"时长异常: {dur}"

# 2) 命令构造
cmd = T.build_cmd(SRC, OUT, "encode", ("medium", 22))
print("cmd =", " ".join(cmd))
assert "libx264" in cmd and "aac" in cmd

# 3) 实际转换 + 进度事件
os.makedirs(os.path.dirname(OUT), exist_ok=True)
if os.path.exists(OUT):
    os.remove(OUT)

q = queue.Queue()
T.convert_one(SRC, OUT, "encode", ("veryfast", 26), q, dur)

events = []
while not q.empty():
    events.append(q.get())

progs = [e for e in events if e[0] == "prog"]
dones = [e for e in events if e[0] == "done"]
errs = [e for e in events if e[0] == "err"]
print(f"事件统计: prog={len(progs)} done={len(dones)} err={len(errs)}")
if progs:
    print("最后进度:", progs[-1][1:])
assert dones, f"转换失败: {errs}"
assert os.path.exists(OUT) and os.path.getsize(OUT) > 0, "输出文件为空"
print("输出大小 =", os.path.getsize(OUT), "bytes")

# 4) copy 模式（换封装）
OUT2 = os.path.join(PROJ, "testdata", "out", "sample_copy.mp4")
q2 = queue.Queue()
T.convert_one(SRC, OUT2, "copy", ("medium", 22), q2, dur)
ev2 = []
while not q2.empty():
    ev2.append(q2.get())
assert any(e[0] == "done" for e in ev2), f"copy 模式失败: {[e for e in ev2 if e[0]=='err']}"
print("copy 模式输出 =", os.path.getsize(OUT2), "bytes")

# 5) 批量收集
found = T.collect_inputs([os.path.join(PROJ, "testdata")])
print("collect_inputs 找到:", [os.path.basename(f) for f in found])
assert found, "批量收集失败"

print("\n=== 全部核心测试通过 ===")
shutil.rmtree(os.path.join(PROJ, "testdata", "out"), ignore_errors=True)