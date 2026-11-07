# -*- coding: utf-8 -*-
"""合并功能测试：验证自然排序、无损拼接、回退重编码。"""
import os
import sys
import queue
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from testutil import setup_ffmpeg_path, PROJ, make_segments  # noqa: E402
setup_ffmpeg_path()
import ts2mp4 as T

SEG = os.path.join(PROJ, "testdata", "seg")
# 素材缺失时自动生成（保证克隆后开箱即测）
if not os.path.isdir(SEG) or not [f for f in os.listdir(SEG) if f.endswith(".ts")]:
    os.makedirs(SEG, exist_ok=True)
    make_segments(SEG, count=4, duration=3)


# ---------- 1. 自然排序 ----------
files = [os.path.join(SEG, f) for f in os.listdir(SEG) if f.endswith(".ts")]
raw_sorted = sorted(files)                                   # 字符串排序
nat_sorted = sorted(files, key=T.natural_key)               # 自然排序
print("原始顺序      :", [os.path.basename(f) for f in files])
print("字符串排序    :", [os.path.basename(f) for f in raw_sorted])
print("自然排序      :", [os.path.basename(f) for f in nat_sorted])
assert [os.path.basename(f) for f in nat_sorted] == ["ch1.ts", "ch2.ts", "ch3.ts", "ch10.ts"], "自然排序错误"
print("✓ 自然排序正确（ch10 排在 ch3 之后）\n")

# ---------- 2. 流信息探测 ----------
info = T.probe_stream_info(files[0])
print("流信息        :", info)
assert info and info["vcodec"] == "h264", "流探测失败"
print("✓ 流信息探测正确\n")

# ---------- 3. 兼容性判断 ----------
infos = [T.probe_stream_info(f) for f in files]
print("编码是否一致  :", T.streams_compatible(infos))
assert T.streams_compatible(infos)
print("✓ 兼容性判断正确\n")

# ---------- 4. 无损合并 ----------
out_dir = os.path.join(PROJ, "testdata", "merged")
shutil.rmtree(out_dir, ignore_errors=True)
os.makedirs(out_dir, exist_ok=True)
mp4s = []
for f in nat_sorted:
    dst = os.path.join(out_dir, os.path.splitext(os.path.basename(f))[0] + ".mp4")
    q = queue.Queue()
    T.convert_one(f, dst, "encode", ("veryfast", 28), q, T.probe_duration(f))
    evs = []
    while not q.empty():
        evs.append(q.get())
    assert any(e[0] == "done" for e in evs), f"{f} 转换失败: {[e for e in evs if e[0]=='err']}"
    mp4s.append(dst)
print("已转换 %d 个 mp4" % len(mp4s))

final = os.path.join(out_dir, "合并结果.mp4")
q = queue.Queue()
T.merge_files(mp4s, final, q, total_dur=12.0, delete_parts=False)
evs = []
while not q.empty():
    evs.append(q.get())
for e in evs:
    if e[0] == "mlog":
        print("  [log]", e[1])
    elif e[0] == "mdone":
        print("  [done]", e[1], "%.1f MB" % (e[2] / 1048576))
    elif e[0] == "merr":
        print("  [ERR]", e[1])
assert any(e[0] == "mdone" for e in evs), "合并失败"
assert os.path.exists(final), "合并文件不存在"
print("✓ 无损合并成功\n")

# ---------- 5. 校验合并结果时长 ----------
d = T.probe_duration(final)
print("合并后时长 = %.2f 秒（4 段 x 3 秒 ≈ 12 秒）" % d)
assert d and 11.0 < d < 13.5, f"合并后时长异常: {d}"
print("✓ 时长正确，各片段都已拼入\n")

# ---------- 6. 参数不一致时回退重编码 ----------
odd = os.path.join(out_dir, "odd.ts")
FF = T.FFMPEG
subprocess_out = os.popen(
    '"%s" -hide_banner -loglevel error -f lavfi -i "color=c=red:size=640x480:rate=30:duration=3" '
    '-f lavfi -i "sine=frequency=800:duration=3" -c:v libx264 -preset ultrafast -c:a aac -shortest -y "%s"'
    % (FF, odd)).read()
print("生成了不同分辨率的片段:", os.path.exists(odd))
mixed = mp4s[:2] + [odd]
infos2 = [T.probe_stream_info(f) for f in mixed]
compatible = T.streams_compatible(infos2)
print("混合分辨率时编码一致?", compatible, "-> 将走重编码路径")
final2 = os.path.join(out_dir, "混合合并.mp4")
q2 = queue.Queue()
T.merge_files(mixed, final2, q2, total_dur=9.0, delete_parts=False)
evs2 = []
while not q2.empty():
    evs2.append(q2.get())
for e in evs2:
    if e[0] in ("mlog", "mdone", "merr"):
        print("  [%s]" % e[0], e[1])
assert any(e[0] == "mdone" for e in evs2), "混合合并失败"
assert os.path.exists(final2)
print("✓ 参数不一致时回退重编码成功\n")

# ---------- 7. 删除中间文件 ----------
q3 = queue.Queue()
before = len([f for f in os.listdir(out_dir) if f.endswith(".mp4")])
T.merge_files(mp4s, os.path.join(out_dir, "清理测试.mp4"), q3, 12.0, delete_parts=True)
while not q3.empty():
    q3.get()
after = len([f for f in os.listdir(out_dir) if f.endswith(".mp4")])
print(f"清理前 mp4 数={before} 清理后={after}")
assert after < before, "中间文件未被清理"
print("✓ 中间文件清理正常")

print("\n=== 合并功能全部测试通过 ===")
shutil.rmtree(out_dir, ignore_errors=True)