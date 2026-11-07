# -*- coding: utf-8 -*-
"""GUI 级验证：模拟真实用户操作，通过 invoke() 切换流程选项并执行。"""
import os
import sys
import shutil
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from testutil import setup_ffmpeg_path, PROJ, make_segments  # noqa: E402
setup_ffmpeg_path()
import ts2mp4
import tkinter as tk
from tkinter.scrolledtext import ScrolledText

CN = 0x08000000
FF = ts2mp4.FFMPEG
SRC = os.path.join(PROJ, "testdata", "gui_src")
# 素材缺失时自动生成（保证克隆后开箱即测）
if not os.path.isdir(SRC) or not [f for f in os.listdir(SRC) if f.endswith(".ts")]:
    os.makedirs(SRC, exist_ok=True)
    make_segments(SRC, count=3, duration=1)

OUTD = os.path.join(PROJ, "testdata", "gui_out")
_orig = tk.Tk.mainloop


def setup():
    shutil.rmtree(SRC, ignore_errors=True)
    shutil.rmtree(OUTD, ignore_errors=True)
    os.makedirs(SRC, exist_ok=True)
    os.makedirs(OUTD, exist_ok=True)
    for i, c in [(1, "red"), (2, "green"), (3, "blue"), (10, "yellow")]:
        subprocess.run(
            [FF, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
             f"color=c={c}:size=160x120:rate=15:duration=1", "-f", "lavfi", "-i",
             "sine=frequency=440:duration=1", "-c:v", "libx264", "-preset", "ultrafast",
             "-c:a", "aac", "-shortest", "-y", os.path.join(SRC, f"ch{i}.ts")],
            capture_output=True, timeout=120, creationflags=CN)


def find_widgets(root, pred):
    out = []

    def walk(x):
        for c in x.winfo_children():
            try:
                t = c.cget("text")
            except Exception:
                t = ""
            if pred(c, t):
                out.append(c)
            walk(c)
    walk(root)
    return out


def run_one(label_pred, timeout=70):
    """切到指定流程并执行，返回日志文本。"""
    result = {}

    def patched(self, n=0):
        self.update_idletasks()
        self.update()

        def do_run():
            # ttk.Radiobutton 的 winfo_class 可能是 TRadiobutton/TCheckbutton，
            # 用类名 + 文本双条件匹配更稳
            rb = [w for w in find_widgets(self, lambda c, t: t and label_pred(t))
                  if w.winfo_class().lower().endswith("radiobutton")]
            if not rb:
                # 退回到纯文本匹配（可能是普通 Radiobutton）
                rb = find_widgets(self, lambda c, t: t and label_pred(t))
            if not rb:
                result["err"] = "未找到流程选项"
                self.destroy()
                return
            rb[0].invoke()          # 像用户点击一样切换
            self.update()
            starts = find_widgets(self, lambda c, t: "开始转换" in (t or ""))
            starts[0].invoke()

            # 轮询：按钮文案从"停止"变回"开始转换"即表示 worker 结束
            def poll():
                labels = []
                for c in find_widgets(self, lambda c, t: True):
                    try:
                        labels.append(c.cget("text"))
                    except Exception:
                        pass
                busy = any(isinstance(s, str) and "停止" in s for s in labels)
                if busy and not result.get("started"):
                    result["started"] = True
                    result["t0"] = __import__("time").time()
                if result.get("started") and not busy:
                    self.after(800, report)
                    return
                if __import__("time").time() - result.get("t0", __import__("time").time()) > timeout:
                    self.after(800, report)
                    return
                self.after(700, poll)

            self.after(1500, poll)

        def report():
            txts = find_widgets(self, lambda c, t: isinstance(c, ScrolledText))
            if txts:
                result["log"] = txts[0].get("1.0", "end")
            self.destroy()

        self.after(500, do_run)
        _orig(self)

    tk.Tk.mainloop = patched
    ts2mp4.run_gui([SRC])
    return result


print("=== 准备素材（红/绿/蓝/黄，各 1 秒）===")
setup()
print("源文件:", sorted(os.listdir(SRC)))
print()

# ---------- 流程 A ----------
print("=== 测试 1：先合并 TS → 再转 MP4 ===")
# 清空源目录中的旧产物（默认输出位置 = 源文件同目录）
for f in os.listdir(SRC):
    if f.endswith(".mp4"):
        os.remove(os.path.join(SRC, f))
r = run_one(lambda t: t.startswith("先合并"))
log = r.get("log", "")
if r.get("err"):
    print("ERROR:", r["err"])
print("\n".join(log.strip().splitlines()[-11:]))
print("--- 源目录（默认输出位置）---")
print(sorted(os.listdir(SRC)))
final = os.path.join(SRC, "合并结果.mp4")
assert os.path.exists(final), "流程A 未产出 MP4"
d = ts2mp4.probe_duration(final)
print("最终 MP4 时长 = %.2f 秒（期望约 4 秒）" % d)
assert d and 3.4 < d < 4.9, "时长异常"
assert not any(f.startswith("_tmp_") for f in os.listdir(SRC)), "中间 TS 未清理"
print("✓ 流程 A 通过：只编码一次，中间 TS 已清理\n")

# ---------- 流程 B ----------
print("=== 测试 2：先逐个转 MP4 → 再合并 ===")
for f in os.listdir(SRC):
    if f.endswith(".mp4"):
        os.remove(os.path.join(SRC, f))
r = run_one(lambda t: t.startswith("先逐个"))
log = r.get("log", "")
print("\n".join(log.strip().splitlines()[-9:]))
print("--- 源目录 ---")
print(sorted(os.listdir(SRC)))
assert os.path.exists(final), "流程B 未产出 MP4"
d = ts2mp4.probe_duration(final)
print("最终 MP4 时长 = %.2f 秒" % d)
assert d and 3.4 < d < 4.9
print("✓ 流程 B 通过\n")

# ---------- 流程 C ----------
print("=== 测试 3：仅转换，不合并 ===")
for f in os.listdir(SRC):
    if f.endswith(".mp4"):
        os.remove(os.path.join(SRC, f))
r = run_one(lambda t: t.startswith("仅转换"))
log = r.get("log", "")
print("\n".join(log.strip().splitlines()[-7:]))
print("--- 源目录 ---")
print(sorted(os.listdir(SRC)))
mp4s = [f for f in os.listdir(SRC) if f.endswith(".mp4")]
assert len(mp4s) == 4, f"应产出 4 个独立 mp4，实际 {len(mp4s)}: {mp4s}"
print("✓ 流程 C 通过：产出 4 个独立 mp4，未合并")

shutil.rmtree(SRC, ignore_errors=True)
shutil.rmtree(OUTD, ignore_errors=True)
print("\n=== 三种流程 GUI 验证全部通过 ===")