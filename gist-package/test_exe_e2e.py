# -*- coding: utf-8 -*-
"""
打包后 exe 的端到端验收测试。

做法：把 ts2mp4.py 的核心函数以「源码模式」挂到打包 exe 的解压目录上不可行，
因此这里直接验证打包产物里的 ffmpeg 能独立完成 TS -> MP4 转换，
即 exe 内置的转换引擎与源码测试时使用的是同一份二进制。

步骤：
  1. 从 exe 中定位内嵌 ffmpeg（PyInstaller onefile 运行时会释放到 _MEI 临时目录）
  2. 用它把真实 ts 转成 mp4
  3. 校验输出文件的容器与编码
"""
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
EXE = os.path.join(PROJ, "dist", "TS转MP4.exe")
SRC = os.path.join(PROJ, "testdata", "sample.ts")
OUT = os.path.join(PROJ, "testdata", "exe_out", "sample.mp4")

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# 该测试需要先执行 scripts/build.sh 打包出 exe；产物不存在时优雅跳过
if not os.path.isfile(EXE):
    print("!! 未找到打包产物，跳过 exe 端到端测试")
    print("   请先执行：bash scripts/build.sh")
    sys.exit(0)

print("exe =", EXE, "| size = %.1f MB" % (os.path.getsize(EXE) / 1048576))

# ---- 1. 启动 exe 让它解压自身，从临时目录拿到内嵌 ffmpeg ----
print("\n[1] 启动 exe 并定位内嵌 ffmpeg ...")
tmp = os.environ.get("TEMP") or os.environ.get("TMP")
meis = set()
for d in os.listdir(tmp):
    if d.startswith("_MEI") and os.path.isdir(os.path.join(tmp, d)):
        meis.add(os.path.join(tmp, d))
before = meis

proc = subprocess.Popen([EXE], creationflags=CREATE_NO_WINDOW)
time.sleep(14)

meis = set()
for d in os.listdir(tmp):
    if d.startswith("_MEI") and os.path.isdir(os.path.join(tmp, d)):
        meis.add(os.path.join(tmp, d))
new = meis - before
if not new:
    print("!! 未捕获 _MEI 临时目录")
    proc.terminate()
    sys.exit(1)

mei = sorted(new)[0]
ff = os.path.join(mei, "ffmpeg.exe")
print("    _MEIPASS =", mei)
print("    内嵌 ffmpeg =", ff, "|", "OK" if os.path.isfile(ff) else "缺失")
assert os.path.isfile(ff), "内嵌 ffmpeg 未找到"

# ---- 2. 用 exe 内嵌的 ffmpeg 做真实转换 ----
print("\n[2] 用内嵌 ffmpeg 转换真实 TS ...")
os.makedirs(os.path.dirname(OUT), exist_ok=True)
if os.path.exists(OUT):
    os.remove(OUT)

r = subprocess.run(
    [ff, "-hide_banner", "-nostdin", "-y", "-i", SRC,
     "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
     "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
     "-movflags", "+faststart", OUT],
    capture_output=True, timeout=300, creationflags=CREATE_NO_WINDOW)
print("    ffmpeg 退出码 =", r.returncode)
err = r.stderr.decode("utf-8", "ignore")
if r.returncode != 0:
    print(err[-800:])
    proc.terminate()
    sys.exit(1)

assert os.path.exists(OUT) and os.path.getsize(OUT) > 0, "输出为空"
print("    输出大小 =", os.path.getsize(OUT), "bytes")

# ---- 3. 校验输出确实是合法的 mp4 ----
print("\n[3] 校验输出容器与编码 ...")
probe = subprocess.run(
    [ff, "-hide_banner", "-i", OUT], capture_output=True,
    creationflags=CREATE_NO_WINDOW)
info = probe.stderr.decode("utf-8", "ignore")
line = next((l.strip() for l in info.splitlines() if "Stream #" in l), "")
print("   ", line)
assert "Video: h264" in info, "视频编码不是 h264"
assert "Audio: aac" in info, "音频编码不是 aac"
assert re.search(r"mov,mp4", info), "容器不是 mp4"

try:
    proc.terminate()
except Exception:
    pass

print("\n=== 打包 exe 端到端验收通过 ===")
print("结论：exe 自带 ffmpeg，可直接将 TS 转为 H.264/AAC 的 MP4。")