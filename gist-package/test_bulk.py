# -*- coding: utf-8 -*-
"""批量队列测试：验证'转十几个卡住'的缺陷已修复。

构造 24 个文件：20 个正常 + 4 个异常（0 字节 / 垃圾数据 / 截断 TS），
要求队列完整走完且不卡死。
"""
import os
import sys
import time
import queue
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from testutil import setup_ffmpeg_path, PROJ, make_ts  # noqa: E402
setup_ffmpeg_path()
import ts2mp4 as T

BULK = os.path.join(PROJ, "testdata", "bulk")
OUT = os.path.join(PROJ, "testdata", "bulk_out")

# ---------- 准备素材 ----------
shutil.rmtree(BULK, ignore_errors=True)
os.makedirs(BULK, exist_ok=True)
shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(OUT, exist_ok=True)

NORMAL = 20
for i in range(1, NORMAL + 1):
    make_ts(os.path.join(BULK, f"seg{i}.ts"), duration=1, size="160x120", rate=15,
            freq=200 + i * 10)
# 4 个异常文件：0 字节 / 垃圾数据 / 截断头
open(os.path.join(BULK, "zero.ts"), "wb").close()
with open(os.path.join(BULK, "junk1.ts"), "wb") as f:
    f.write(os.urandom(30000))
with open(os.path.join(BULK, "junk2.ts"), "wb") as f:
    f.write(os.urandom(20000))
with open(os.path.join(BULK, "trunc.ts"), "wb") as f:
    f.write(b"\x47\x40\x00\x10")

files = T.collect_inputs([BULK])
print("队列文件数 =", len(files))
assert len(files) == NORMAL + 4, f"应为 {NORMAL + 4} 个文件，实际 {len(files)}"

# ---------- 逐个处理 ----------
t_begin = time.time()
ok = fail = 0
for i, f in enumerate(files, 1):
    dst = os.path.join(OUT, os.path.splitext(os.path.basename(f))[0] + ".mp4")
    q = queue.Queue()
    dur = T.probe_duration(f)
    t0 = time.time()
    T.convert_one(f, dst, "encode", ("veryfast", 28), q, dur)
    cost = time.time() - t0
    evs = []
    while not q.empty():
        evs.append(q.get())
    done = any(e[0] == "done" for e in evs)
    if done:
        ok += 1
    else:
        fail += 1
        reason = next((e[1] for e in evs if e[0] == "err"), "?")
    print("  [%2d/%2d] %-12s %5.2fs  %s" % (
        i, len(files), os.path.basename(f), cost,
        "✓" if done else "✗ " + reason[:38]))
    # 单文件超过 60 秒即视为卡死
    assert cost < 60, f"第 {i} 个文件耗时 {cost:.1f} 秒，疑似卡死"

total = time.time() - t_begin
print("\n总耗时 %.1f 秒 | 成功 %d | 失败 %d" % (total, ok, fail))
print("队列完整走完，未卡死 ✓" if ok == NORMAL else "异常：成功数不符")
assert ok == NORMAL, f"应恰好 {NORMAL} 个正常文件成功，实际 {ok}"
print(f"=== 批量队列测试通过（{len(files)} 文件全部处理完毕，无卡死）===")

shutil.rmtree(OUT, ignore_errors=True)
shutil.rmtree(BULK, ignore_errors=True)