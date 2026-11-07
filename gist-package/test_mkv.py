# -*- coding: utf-8 -*-
"""MKV 输出功能测试。"""
import os
import sys
import time
import queue
import shutil
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from testutil import setup_ffmpeg_path, PROJ, make_segments  # noqa: E402
setup_ffmpeg_path()
import ts2mp4 as T

CN = 0x08000000
SEG = os.path.join(PROJ, "testdata", "mkvseg")
# 素材缺失时自动生成（保证克隆后开箱即测）
if not os.path.isdir(SEG) or not [f for f in os.listdir(SEG) if f.endswith(".ts")]:
    os.makedirs(SEG, exist_ok=True)
    make_segments(SEG, count=4, duration=2)

OUT = os.path.join(PROJ, "testdata", "mkvout")
shutil.rmtree(SEG, ignore_errors=True)
shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(SEG, exist_ok=True)
os.makedirs(OUT, exist_ok=True)

# ---------- 1. 格式工具 ----------
print("=== 1. 格式工具 ===")
print("  fmt_ext('mp4') =", T.fmt_ext("mp4"))
print("  fmt_ext('mkv') =", T.fmt_ext("mkv"))
print("  faststart mp4 =", T.fmt_faststart("mp4"))
print("  faststart mkv =", T.fmt_faststart("mkv"))
assert T.fmt_ext("mp4") == ".mp4"
assert T.fmt_ext("mkv") == ".mkv"
assert T.fmt_faststart("mkv") == []
print("  with_ext('结果')     =", T.with_ext("结果", "mkv"))
print("  with_ext('结果.mp4') =", T.with_ext("结果.mp4", "mkv"))
assert T.with_ext("结果", "mkv") == "结果.mkv"
assert T.with_ext("结果.mp4", "mkv") == "结果.mkv"
assert T.with_ext("结果", "mp4") == "结果.mp4"
print("✓ 格式工具正确\n")

# ---------- 2. 命令构造 ----------
print("=== 2. 命令构造 ===")
c_copy_mp4 = T.build_cmd("a.ts", "b.mp4", "copy", ("medium", 22), out_fmt="mp4")
c_copy_mkv = T.build_cmd("a.ts", "b.mkv", "copy", ("medium", 22), out_fmt="mkv")
c_enc_mkv = T.build_cmd("a.ts", "b.mkv", "encode", ("veryfast", 23), out_fmt="mkv")
print("  mkv copy     :", " ".join(c_copy_mkv[:9]), "...")
print("  mkv encode   :", " ".join(c_enc_mkv[:9]), "...")
assert "-bsf:a" in c_copy_mp4, "mp4 copy 需要 aac_adtstoasc"
assert "-bsf:a" not in c_copy_mkv, "mkv 不需要 bitstream filter"
assert "-movflags" not in c_copy_mkv and "-movflags" not in c_enc_mkv, "mkv 无需 faststart"
assert "libx264" in c_enc_mkv
print("✓ 命令构造正确\n")

# ---------- 3. 生成素材 ----------
print("=== 3. 生成 4 段素材（红绿蓝黄，各2秒）===")
for i, c in [(1, "red"), (2, "green"), (3, "blue"), (10, "yellow")]:
    subprocess.run(
        [T.FFMPEG, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
         f"color=c={c}:size=320x240:rate=25:duration=2", "-f", "lavfi", "-i",
         "sine=frequency=440:duration=2", "-c:v", "libx264", "-preset", "ultrafast",
         "-c:a", "aac", "-shortest", "-y", os.path.join(SEG, f"ch{i}.ts")],
        capture_output=True, timeout=180, creationflags=CN)
files = sorted([os.path.join(SEG, f) for f in os.listdir(SEG) if f.endswith(".ts")],
               key=T.natural_key)
print("  素材:", [os.path.basename(f) for f in files])

# ---------- 4. TS 直接转 MKV（copy，最快路径）----------
print("\n=== 4. TS → MKV 极速换封装 ===")
src = files[0]
dst = os.path.join(OUT, "single.mkv")
q = queue.Queue()
t0 = time.time()
T.convert_one(src, dst, "copy", ("medium", 22), q, T.probe_duration(src), out_fmt="mkv")
cost = time.time() - t0
evs = []
while not q.empty():
    evs.append(q.get())
done = any(e[0] == "done" for e in evs)
err = next((e[1] for e in evs if e[0] == "err"), "")
assert done, f"转 MKV 失败: {err}"
print(f"  ✓ {cost:.2f} 秒 -> {os.path.getsize(dst)} bytes")
info = T.probe_stream_info(dst)
print("  流信息:", info)
assert info["vcodec"] == "h264" and info["acodec"] == "aac"
# 确认容器是 matroska
raw = subprocess.run([T.FFMPEG, "-hide_banner", "-i", dst], capture_output=True,
                     creationflags=CN).stderr.decode("utf-8", "ignore")
assert "matroska" in raw.lower(), "容器不是 MKV"
print("  容器: matroska ✓\n")

# ---------- 5. 合并成 MKV ----------
print("=== 5. 合并多个 TS → 单个 MKV ===")
final = os.path.join(OUT, "合并结果.mkv")
q = queue.Queue()
tot = sum(T.probe_duration(f) or 0 for f in files)
T.merge_files(files, final, q, tot, delete_parts=False, out_fmt="mkv")
evs = []
while not q.empty():
    evs.append(q.get())
for e in evs:
    if e[0] in ("mlog", "mdone", "merr"):
        print(f"  [{e[0]}] {e[1]}")
assert any(e[0] == "mdone" for e in evs), "合并 MKV 失败"
d = T.probe_duration(final)
print(f"  合并后时长 {d:.2f} 秒（期望 {tot:.0f}）")
assert d and abs(d - tot) < 1.0, "合并后时长异常"
print("✓ 合并 MKV 正确\n")

# ---------- 6. 抽帧验证顺序 ----------
print("=== 6. 抽帧验证顺序（红→绿→蓝→黄）===")
names = ["红ch1", "绿ch2", "蓝ch3", "黄ch10"]
ch_rgb = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]
dist = lambda a, b: sum((x - y) ** 2 for x, y in zip(a, b))
ok_all = True
for i, nm in enumerate(names):
    t = i * 2.0 + 1.0
    r = subprocess.run([T.FFMPEG, "-hide_banner", "-loglevel", "error",
                        "-ss", str(t - 0.4), "-i", final, "-frames:v", "1",
                        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "1x1", "-"],
                       capture_output=True, timeout=60, creationflags=CN)
    got = tuple(r.stdout[:3])
    best = min(range(4), key=lambda k: dist(got, ch_rgb[k]))
    print(f"  t={t:.1f}s 期望{nm} 实得{got} -> {names[best]} {'✓' if names[best]==nm else '✗'}")
    if names[best] != nm:
        ok_all = False
assert ok_all, "MKV 合并顺序错误"
print("✓ 顺序正确\n")

# ---------- 7. MKV 不触发 4GB 分段 ----------
print("=== 7. MKV 不做 4GB 分段 ===")
parts = T.split_mp4(final, os.path.join(OUT, "nosplit"), msg_queue=queue.Queue(),
                    out_fmt="mkv")
print("  split_mp4(mkv) 返回:", parts)
assert parts == [], "MKV 不应切分"
print("✓ MKV 跳过分段（无 4GB 限制）\n")

# ---------- 8. MP4 仍会分段（回归） ----------
print("=== 8. 回归：MP4 仍保留分段能力 ===")
big = os.path.join(OUT, "big.mp4")
seg_sec, n = T.plan_split(4 * 1024 ** 3, 3600)
print(f"  4GB/60分钟 -> 每段 {seg_sec/60:.0f} 分钟，共 {n} 段")
assert n >= 2
assert T.split_mp4(big, os.path.join(OUT, "x"), out_fmt="mp4") == []  # 文件不存在时返回空
print("✓ MP4 分段逻辑未受影响\n")

print("=== MKV 功能全部测试通过 ===")
shutil.rmtree(SEG, ignore_errors=True)
shutil.rmtree(OUT, ignore_errors=True)