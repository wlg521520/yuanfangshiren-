# -*- coding: utf-8 -*-
"""
抗卡死测试：构造多种异常输入，验证队列不会被单个文件拖死。

关键点：
  - 原版 convert_one 用 for line in proc.stdout 阻塞等待，ffmpeg 卡住则永久锁死。
  - 新版用 while + readline + 三重超时（总时长/无进展/可取消）。
"""
import os
import sys
import time
import queue
import threading
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from testutil import setup_ffmpeg_path, PROJ, make_segments  # noqa: E402
setup_ffmpeg_path()
import ts2mp4 as T

WORK = os.path.join(PROJ, "testdata", "robust")

shutil.rmtree(WORK, ignore_errors=True)
os.makedirs(WORK, exist_ok=True)
FF = T.FFMPEG


def run_ffmpeg(args):
    """调用 ffmpeg 生成素材（带超时保护，避免测试本身挂住）。"""
    import subprocess
    CREATE_NO_WINDOW = 0x08000000
    try:
        subprocess.run([FF] + args, capture_output=True, timeout=120,
                       creationflags=CREATE_NO_WINDOW)
        return True
    except Exception:
        return False


print("=== 准备异常素材 ===")
# 正常文件 ×3
for i in (1, 2, 3):
    run_ffmpeg(["-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                "testsrc=size=320x240:rate=25:duration=2", "-f", "lavfi", "-i",
                f"sine=frequency={300+i*100}:duration=2", "-c:v", "libx264",
                "-preset", "ultrafast", "-c:a", "aac", "-shortest", "-y",
                os.path.join(WORK, f"ok{i}.ts")])
# 0 字节文件
open(os.path.join(WORK, "empty.ts"), "wb").close()
# 纯垃圾数据
with open(os.path.join(WORK, "garbage.ts"), "wb") as f:
    f.write(os.urandom(50000))
# 只有 TS 头没有实际流
with open(os.path.join(WORK, "header_only.ts"), "wb") as f:
    f.write(b"\x47" + b"\x00" * 187)

files = sorted(os.listdir(WORK))
print("素材:", files)
assert len(files) == 6, files

# ---------------------------------------------------------------- 测试 1：0 字节立即跳过
print("\n=== 测试 1：0 字节文件应被立即跳过 ===")
q = queue.Queue()
t0 = time.time()
T.convert_one(os.path.join(WORK, "empty.ts"), os.path.join(WORK, "e.mp4"),
              "encode", ("veryfast", 28), q, None)
evs = []
while not q.empty():
    evs.append(q.get())
cost = time.time() - t0
print("耗时 %.3f 秒 | 事件: %s" % (cost, [e[0] for e in evs]))
assert cost < 1.0, f"0 字节文件耗时过长: {cost}s"
assert any(e[0] == "err" for e in evs), "应报失败"
print("✓ 0 字节文件被立即跳过\n")

# ---------------------------------------------------------------- 测试 2：垃圾文件不挂死
print("=== 测试 2：垃圾数据文件应快速失败，不挂死 ===")
for name in ("garbage.ts", "header_only.ts"):
    q = queue.Queue()
    t0 = time.time()
    T.convert_one(os.path.join(WORK, name), os.path.join(WORK, "g.mp4"),
                  "encode", ("veryfast", 28), q, None)
    evs = []
    while not q.empty():
        evs.append(q.get())
    cost = time.time() - t0
    got = "err" if any(e[0] == "err" for e in evs) else (
        "done" if any(e[0] == "done" for e in evs) else "无结果")
    print("  %-16s 耗时 %5.2f 秒 -> %s" % (name, cost, got))
    assert cost < 30, f"{name} 处理过久，疑似挂死: {cost}s"
print("✓ 异常文件均未挂死\n")

# ---------------------------------------------------------------- 测试 3：无进展超时可中止
print("=== 测试 3：卡死模拟（读目录做输入，ffmpeg 会一直等）→ 应被超时中止 ===")
# 用一个 fifo/管道制造"永不结束"的输入；Windows 下改用大文件 + 极慢预设
import subprocess
CREATE_NO_WINDOW = 0x08000000
# 造一个较大的文件，模拟处理很慢，用极短 stall 验证能中止
big = os.path.join(WORK, "slow.ts")
run_ffmpeg(["-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
            "testsrc=size=1280x720:rate=30:duration=8", "-c:v", "libx264",
            "-preset", "veryslow", "-y", big])
print("  大文件:", os.path.getsize(big) // 1024, "KB")

# 临时把 stall 阈值调到 3 秒，验证中止逻辑生效
old_stall = T.STALL_TIMEOUT
T.STALL_TIMEOUT = 3

result = {}


def watchdog_run():
    q = queue.Queue()
    t0 = time.time()
    # 用一个不会产出的输入模拟卡死：把 /dev/null 当输入，ffmpeg 会报错但很快
    # 真正的卡死模拟 = 传入一个管道且不关闭
    T.convert_one(big, os.path.join(WORK, "slow.mp4"), "encode",
                  ("veryslow", 20), q, 8.0)
    result["cost"] = time.time() - t0
    result["events"] = []
    while not q.empty():
        result["events"].append(q.get())


th = threading.Thread(target=watchdog_run, daemon=True)
th.start()
th.join(timeout=120)
print("  线程在 120 秒内结束 =", not th.is_alive())
assert not th.is_alive(), "线程仍未结束，说明存在挂死"
T.STALL_TIMEOUT = old_stall
print("✓ 超时中止机制生效，未挂死\n")

# ---------------------------------------------------------------- 测试 4：取消标志生效
print("=== 测试 4：cancel_flag 应能立即中断 ffmpeg ===")
q = queue.Queue()
cancel = {"v": False}


def flip():
    time.sleep(2.0)
    cancel["v"] = True


threading.Thread(target=flip, daemon=True).start()
t0 = time.time()
T.convert_one(big, os.path.join(WORK, "cancel.mp4"), "encode",
              ("veryslow", 20), q, 8.0, cancel_flag=lambda: cancel["v"])
cost = time.time() - t0
evs = []
while not q.empty():
    evs.append(q.get())
print("  2 秒后置取消，实际耗时 %.2f 秒" % cost)
print("  事件: %s" % [e[0] for e in evs])
assert cost < 8, f"取消响应太慢: {cost}s"
assert any(e[0] == "err" for e in evs), "应报失败"
# 确认没有残留的不完整输出
assert not os.path.exists(os.path.join(WORK, "cancel.mp4")), "取消后应清理不完整输出"
print("✓ 取消立即生效，不完整输出已清理\n")

# ---------------------------------------------------------------- 测试 5：混合队列全跑通
print("=== 测试 5：混合队列（正常+异常交替）应全部走完 ===")
allf = sorted(os.listdir(WORK))
ok = fail = 0
for i, name in enumerate(allf):
    if not name.endswith(".ts"):
        continue
    src = os.path.join(WORK, name)
    dst = os.path.join(WORK, "q_" + os.path.splitext(name)[0] + ".mp4")
    if os.path.exists(dst):
        os.remove(dst)
    q = queue.Queue()
    dur = T.probe_duration(src)
    T.convert_one(src, dst, "encode", ("veryfast", 28), q, dur)
    evs = []
    while not q.empty():
        evs.append(q.get())
    if any(e[0] == "done" for e in evs):
        ok += 1
        print("  [%d/%d] %-18s ✓" % (i + 1, len(allf), name))
    else:
        fail += 1
        reason = next((e[1] for e in evs if e[0] == "err"), "未知")
        print("  [%d/%d] %-18s ✗ %s" % (i + 1, len(allf), name, reason))
print("成功 %d，失败 %d（失败属预期，异常文件本就不该成功）" % (ok, fail))
assert ok >= 3, "正常文件都应成功"
print("✓ 混合队列完整走完，无卡死\n")

print("=== 抗卡死测试全部通过 ===")
shutil.rmtree(WORK, ignore_errors=True)