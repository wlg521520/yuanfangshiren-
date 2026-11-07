# -*- coding: utf-8 -*-
"""新流程测试：先合并 TS（按 1,2,3 顺序）→ 再一次性转 MP4。"""
import os
import sys
import time
import queue
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from testutil import setup_ffmpeg_path, PROJ, make_segments  # noqa: E402
setup_ffmpeg_path()
import ts2mp4 as T

SEG = os.path.join(PROJ, "testdata", "seg2")
# 素材缺失时自动生成（保证克隆后开箱即测）
if not os.path.isdir(SEG) or not [f for f in os.listdir(SEG) if f.endswith(".ts")]:
    os.makedirs(SEG, exist_ok=True)
    make_segments(SEG, count=4, duration=2)

OUT = os.path.join(PROJ, "testdata", "flow_out")
shutil.rmtree(SEG, ignore_errors=True)
shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(SEG, exist_ok=True)
FF = T.FFMPEG
import subprocess
CN = 0x08000000


def gen(dst, dur, color):
    subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"color=c={color}:size=320x240:rate=25:duration={dur}",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={dur}",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                    "-shortest", "-y", dst], capture_output=True, timeout=180,
                   creationflags=CN)


# 用 3 种颜色区分三个片段，合并后可抽帧验证顺序
print("=== 准备 3 个 TS 片段（红/绿/蓝，各 2 秒）===")
gen(os.path.join(SEG, "ch1.ts"), 2, "red")
gen(os.path.join(SEG, "ch2.ts"), 2, "green")
gen(os.path.join(SEG, "ch3.ts"), 2, "blue")
for f in sorted(os.listdir(SEG)):
    print("  ", f, os.path.getsize(os.path.join(SEG, f)) // 1024, "KB")

# ---------- 1. 排序验证 ----------
files = [os.path.join(SEG, f) for f in os.listdir(SEG) if f.endswith(".ts")]
ordered = sorted(files, key=T.natural_key)
print("\n自然排序结果:", [os.path.basename(f) for f in ordered])
assert [os.path.basename(f) for f in ordered] == ["ch1.ts", "ch2.ts", "ch3.ts"]

# 加一个两位数文件，验证 ch10 排在 ch3 之后
gen(os.path.join(SEG, "ch10.ts"), 2, "yellow")
files = [os.path.join(SEG, f) for f in os.listdir(SEG) if f.endswith(".ts")]
ordered = sorted(files, key=T.natural_key)
print("含 ch10 后:", [os.path.basename(f) for f in ordered])
assert [os.path.basename(f) for f in ordered] == ["ch1.ts", "ch2.ts", "ch3.ts", "ch10.ts"]
print("✓ 顺序正确（1,2,3,10 而非 1,10,2,3）\n")

# ---------- 2. 无损合并 TS ----------
os.makedirs(OUT, exist_ok=True)
merged_ts = os.path.join(OUT, "merged.ts")
total_dur = sum(T.probe_duration(f) or 0 for f in ordered)
print("=== 阶段1：无损合并 TS（总时长 %.1f 秒）===" % total_dur)
q = queue.Queue()
t0 = time.time()
ok = T.merge_ts_lossless(ordered, merged_ts, q, total_dur)
cost = time.time() - t0
evs = []
while not q.empty():
    evs.append(q.get())
for e in evs:
    if e[0] in ("tsdone", "tserr"):
        print("  [%s] %s" % (e[0], e[1]))
assert ok, "无损合并 TS 失败"
print("  合并耗时 %.2f 秒 -> %d KB\n" % (cost, os.path.getsize(merged_ts) // 1024))

d = T.probe_duration(merged_ts)
print("合并后 TS 时长 = %.2f 秒（期望 %.0f）" % (d, total_dur))
assert abs(d - total_dur) < 1.5, "合并后时长异常"
print("✓ TS 合并正确\n")

# ---------- 3. 合并后的 TS 转 MP4 ----------
print("=== 阶段2：合并后的 TS → MP4 ===")
final = os.path.join(OUT, "合并结果.mp4")
q = queue.Queue()
t0 = time.time()
T.convert_one(merged_ts, final, "encode", ("veryfast", 26), q, d)
cost = time.time() - t0
evs = []
while not q.empty():
    evs.append(q.get())
done = any(e[0] == "done" for e in evs)
err = next((e[1] for e in evs if e[0] == "err"), "")
assert done, f"转 MP4 失败: {err}"
print("  ✓ 完成 %.2f 秒 -> %d KB\n" % (cost, os.path.getsize(final) // 1024))

d2 = T.probe_duration(final)
print("最终 MP4 时长 = %.2f 秒（期望 %.0f）" % (d2, total_dur))
assert abs(d2 - total_dur) < 1.5, "最终时长异常"
info = T.probe_stream_info(final)
print("  流信息:", info)
assert info["vcodec"] == "h264" and info["acodec"] == "aac"
print("✓ MP4 编码正确\n")

# ---------- 4. 抽帧验证顺序（核心验证）----------
print("=== 抽帧验证合并顺序（红→绿→蓝→黄）===")
# 每段 2 秒：ch1=[0,2) ch2=[2,4) ch3=[4,6) ch10=[6,8)
# 取各段正中偏一点，避开边界
expected = [(1.0, (255, 0, 0), "红 ch1"), (3.0, (0, 255, 0), "绿 ch2"),
            (5.0, (0, 0, 255), "蓝 ch3"), (7.0, (255, 255, 0), "黄 ch10")]
ok_all = True
for t, rgb, label in expected:
    # 取该时间点附近一帧的中位颜色，避免边界/GOP 影响
    r = subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-ss", str(t - 0.5),
                        "-i", final, "-frames:v", "1", "-f", "rawvideo",
                        "-pix_fmt", "rgb24", "-s", "1x1", "-"],
                       capture_output=True, timeout=60, creationflags=CN)
    got = tuple(r.stdout[:3])
    # 判断"属于哪个片段"：看哪个通道占主导
    ch_names = ["红 ch1", "绿 ch2", "蓝 ch3", "黄 ch10"]
    ch_rgb = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]
    def dist(a, b):
        return sum((x - y) ** 2 for x, y in zip(a, b))
    best = min(range(4), key=lambda i: dist(got, ch_rgb[i]))
    hit = ch_names[best] == label
    print("  t=%4.1fs 期望%s  实得%s  最近=%s  %s"
          % (t, label, got, ch_names[best], "✓" if hit else "✗"))
    if not hit:
        ok_all = False
assert ok_all, "合并顺序错误！"
print("✓ 4 个片段顺序完全正确\n")

print("=== 新流程全部测试通过 ===")
print("结论：先按 1/2/3/10 顺序无损合并 TS，再一次性转 MP4 —— 画面顺序正确，只编码一次。")
shutil.rmtree(OUT, ignore_errors=True)