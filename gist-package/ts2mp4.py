# -*- coding: utf-8 -*-
"""
TS -> MP4 / MKV 批量转换与合并工具

特性：
  - 图形界面，支持把 ts 文件/文件夹直接拖进窗口
  - 内置 ffmpeg，无需用户自行安装
  - 批量转换、进度条、实时速度与剩余时间
  - 三种流程：先合并 TS 再转码 / 先逐个转再合并 / 仅转换
  - 输出 MP4（兼容性好）或 MKV（无 4GB 限制、容错强）
  - 支持加密 HLS（m3u8 + AES-128）直接解密转码
  - 三重超时保护，异常文件不拖垮整批任务

许可证：GPL-3.0
"""

import os
import sys
import json
import shutil
import subprocess
import threading
import queue
import time
import re

# ---------------------------------------------------------------- 路径处理
# PyInstaller onefile 会把解压出来的临时目录放在 sys._MEIPASS
if getattr(sys, "frozen", False):
    BASE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    APP_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    APP_DIR = BASE_DIR


def _find_bin(name):
    """在打包目录中定位 ffmpeg / ffprobe 可执行文件。"""
    exe = name + ".exe"
    for folder in (BASE_DIR, APP_DIR, os.path.join(BASE_DIR, "bin"), os.path.join(APP_DIR, "bin")):
        p = os.path.join(folder, exe)
        if os.path.isfile(p):
            return p
    found = shutil.which(name)
    return found


FFMPEG = _find_bin("ffmpeg")
FFPROBE = _find_bin("ffprobe")

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

VIDEO_EXTS = {".ts", ".m2ts", ".mts", ".mpg", ".mpeg", ".mp4", ".m4v", ".mov", ".vob", ".flv", ".wmv", ".avi", ".mkv", ".webm", ".rmvb"}
TS_EXTS = {".ts", ".m2ts", ".mts"}
M3U8_EXTS = {".m3u8"}

# 转码参数：兼容性优先
ENCODE_ARGS = [
    "-c:v", "libx264",
    "-preset", "medium",
    "-crf", "22",
    "-pix_fmt", "yuv420p",
    "-c:a", "aac",
    "-b:a", "192k",
    "-ac", "2",
    "-movflags", "+faststart",
]


def probe_duration(path):
    """取视频时长（秒）。优先用 ffprobe；没有 ffprobe 时退回 ffmpeg 解析。失败返回 None。"""
    extra = ["-allowed_extensions", "ALL"] if str(path).lower().endswith(".m3u8") else []
    if FFPROBE:
        try:
            out = subprocess.run(
                [FFPROBE, "-v", "error", "-show_entries", "format=duration",
                 "-of", "json", path],
                capture_output=True, timeout=60,
                creationflags=CREATE_NO_WINDOW,
            )
            data = json.loads(out.stdout.decode("utf-8", "ignore") or "{}")
            dur = data.get("format", {}).get("duration")
            if dur:
                return float(dur)
        except Exception:
            pass
    # 回退：ffmpeg 解析输入信息
    if FFMPEG:
        try:
            out = subprocess.run(
                [FFMPEG, "-hide_banner"] + extra + ["-i", path],
                capture_output=True, timeout=90,
                creationflags=CREATE_NO_WINDOW,
            )
            txt = out.stderr.decode("utf-8", "ignore")
            m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", txt)
            if m:
                return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
        except Exception:
            pass
    return None


_TIME_RE = re.compile(r"time=\s*(\d+):(\d+):(\d+\.?\d*)")
_SPEED_RE = re.compile(r"speed=\s*([\d.]+x)")
_OUT_TIME_RE = re.compile(r"out_time_ms=\s*(\d+)")


def parse_progress(line):
    """从 ffmpeg stderr 行中解析 (已处理秒数, 速度字符串)。"""
    m = _TIME_RE.search(line)
    if not m:
        return None
    h, mnt, s = m.group(1), m.group(2), m.group(3)
    secs = int(h) * 3600 + int(mnt) * 60 + float(s)
    sp = _SPEED_RE.search(line)
    return secs, (sp.group(1) if sp else "")


# ---------------------------------------------------------------- 输出格式
# MKV 相比 MP4 的优势：无 4GB 单文件上限、容错强、可容纳更多轨道。
# TS 转 MKV 通常直接 -c copy（换容器）即可，画质无损且速度极快。
OUTPUT_FORMATS = {
    "mp4": {
        "ext": ".mp4",
        "label": "MP4（兼容性最好，手机电视通吃）",
        "faststart": ["-movflags", "+faststart"],
    },
    "mkv": {
        "ext": ".mkv",
        "label": "MKV（不限 4GB、容错强、速度快）",
        "faststart": [],          # MKV 无需 faststart
    },
}
DEFAULT_FORMAT = "mp4"


def fmt_ext(fmt=None):
    """取输出扩展名（含点）。"""
    return OUTPUT_FORMATS.get(fmt or DEFAULT_FORMAT, OUTPUT_FORMATS["mp4"])["ext"]


def fmt_faststart(fmt=None):
    """取该格式的封装优化参数。"""
    return OUTPUT_FORMATS.get(fmt or DEFAULT_FORMAT, OUTPUT_FORMATS["mp4"])["faststart"]


def with_ext(name, fmt=None):
    """把用户输入的输出名补全/替换为正确扩展名。"""
    ext = fmt_ext(fmt)
    base = name
    for e in (".mp4", ".mkv", ".ts", ".m2ts"):
        if base.lower().endswith(e):
            base = base[: -len(e)]
            break
    return base + ext


def build_cmd(src, dst, mode, quality, overwrite=True, input_extra=None,
              out_fmt=None):
    ext = fmt_ext(out_fmt)
    faststart = fmt_faststart(out_fmt)
    cmd = [FFMPEG, "-hide_banner", "-nostdin", "-y" if overwrite else "-n"]
    if input_extra:                      # 加密 HLS 需要 -allowed_extensions ALL
        cmd += list(input_extra)
    cmd += ["-i", src]
    if mode == "copy":
        # 仅换封装，不重编码（秒级完成）
        if ext == ".mp4":
            cmd += ["-c", "copy", "-bsf:a", "aac_adtstoasc"]
        else:
            # MKV 可直接承载 TS 里的 H.264/AAC，无需 bitstream filter
            cmd += ["-c", "copy"]
        cmd += faststart
    else:
        preset, crf = quality
        cmd += ["-c:v", "libx264", "-preset", preset, "-crf", str(crf),
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                "-ac", "2"] + faststart
    cmd += ["-progress", "pipe:1", "-loglevel", "error", dst]
    return cmd


# ---------------------------------------------------------------- 超时保护
# ffmpeg 遇到损坏文件、正在写入的文件、网络盘卡顿等异常时可能永不退出。
# 若直接 for line in proc.stdout 阻塞等待，整个队列会被永久锁死。
STALL_TIMEOUT = 90           # 连续 90 秒无任何进度输出 -> 判定卡死
TOTAL_TIMEOUT_BASE = 3600# 单文件最长 1 小时
MAX_ATTEMPTS = 2             # 单文件最多重试次数


def _terminate(proc):
    """尽最大努力结束 ffmpeg 进程。"""
    if proc.poll() is not None:
        return
    try:
        proc.kill()
    except Exception:
        pass
    try:
        proc.wait(timeout=8)
    except Exception:
        pass


def convert_one(src, dst, mode, quality, msg_queue, dur=None,
                cancel_flag=None, attempt=1, input_extra=None, out_fmt=None):
    """
    转换单个文件。

    三重保护（防止个别异常文件拖死整个队列）：
      1. 总时长上限：按时长估算，最低 1 小时
      2. 无进展超时：连续 STALL_TIMEOUT 秒无任何输出即判定卡死
      3. 支持外部中断：用户点「停止」时立即终止当前 ffmpeg

    事件：
      ('prog', 已处理秒, 百分比, 剩余秒)
      ('done', 文件名, 字节数) / ('err', 原因) / ('log', 输出行)
    """
    if os.path.abspath(src) == os.path.abspath(dst):
        msg_queue.put(("err", f"{os.path.basename(src)}：源文件与输出文件同名，已跳过"))
        return
    if os.path.exists(dst):
        try:
            os.remove(dst)
        except OSError as e:
            msg_queue.put(("err", f"无法覆盖 {os.path.basename(dst)}：{e}"))
            return

    # 0 字节文件直接跳过，不交给 ffmpeg 磨蹭
    try:
        if os.path.getsize(src) == 0:
            msg_queue.put(("err", f"{os.path.basename(src)}：文件为 0 字节，已跳过"))
            return
    except OSError as e:
        msg_queue.put(("err", f"{os.path.basename(src)}：无法读取（{e}）"))
        return

    # 注：不使用 ffmpeg 的 -timeout 选项（ffmpeg 7.x 已移除该全局选项，
    # 传入会导致 "Option timeout not found" 使所有文件失败）。
    # 超时完全由下面 while 循环的三重保护负责。
    cmd = build_cmd(src, dst, mode, quality, input_extra=input_extra, out_fmt=out_fmt)

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=CREATE_NO_WINDOW, bufsize=1,
            universal_newlines=True, encoding="utf-8", errors="ignore",
        )
    except Exception as e:
        msg_queue.put(("err", f"启动 ffmpeg 失败：{e}"))
        return

    # 总时长上限：按时长 40 倍估算留足余量；时长未知则用基准值
    total_limit = max(TOTAL_TIMEOUT_BASE, int(dur * 40)) if dur else TOTAL_TIMEOUT_BASE
    stall_limit = STALL_TIMEOUT if dur else STALL_TIMEOUT * 2

    last_pct, last_eta = -1, -1.0
    t_begin = time.time()
    t_last_out = time.time()      # 最后一次收到输出的时刻
    aborted = None

    # 关键：ffmpeg 的 stdout 必须由独立线程读取。
    # 若在主循环里直接 proc.stdout.readline()，当管道暂无数据时该调用会阻塞，
    # 导致上面的超时检查永远执行不到 —— 这正是"转十几个后卡死"的第二个成因。
    # （Windows 的 select 不支持管道文件对象，无法用 select 替代。）
    line_q = queue.Queue(maxsize=1000)

    def _reader():
        try:
            for ln in proc.stdout:
                try:
                    line_q.put_nowait(ln)
                except queue.Full:
                    pass          # 队列满则丢弃，主循环只关心进度/错误
        except Exception:
            pass
        finally:
            line_q.put(("__EOF__", None))

    reader = threading.Thread(target=_reader, daemon=True)
    reader.start()

    while True:
        # 用户主动停止
        if cancel_flag is not None and cancel_flag():
            _terminate(proc)
            aborted = "已取消"
            break
        # 总时长超限
        if time.time() - t_begin > total_limit:
            _terminate(proc)
            aborted = f"超过最长处理时间（{int(total_limit // 60)} 分钟）"
            break
        # 无进展：ffmpeg 存活但不出任何输出 = 卡死
        if time.time() - t_last_out > stall_limit:
            _terminate(proc)
            aborted = f"无响应超过 {int(stall_limit)} 秒，已跳过（文件可能损坏或被占用）"
            break
        # 进程已退出但读线程还没投 EOF（极少见）：退出循环
        if proc.poll() is not None:
            break

        try:
            line = line_q.get(timeout=0.3)
        except queue.Empty:
            continue

        if line == ("__EOF__", None):
            break
        if not isinstance(line, str):
            continue
        line = line.strip()
        if not line:
            continue

        t_last_out = time.time()
        line = line.strip()
        if not line:
            continue

        if line.startswith("out_time_ms="):
            m = _OUT_TIME_RE.search(line)
            if m:
                secs = int(m.group(1)) / 1_000_000.0
                pct = int(secs / dur * 100) if dur else 0
                pct = max(0, min(99, pct))
                now = time.time()
                if pct != last_pct or (now - last_eta) > 0.4:
                    last_pct = pct
                    avg = secs / max(now - t_begin, 0.001)
                    eta = max(0.0, (dur - secs) / avg) if dur else 0.0
                    last_eta = now
                    msg_queue.put(("prog", secs, pct, eta))
        elif line.startswith(("progress=", "frame=", "fps=", "bitrate=",
                              "total_size=", "stream_", "drop_frames=",
                              "dup_frames=", "speed=")):
            continue
        elif ("error" in line.lower() or "invalid" in line.lower()
              or "corrupt" in line.lower() or "moov" in line.lower()):
            msg_queue.put(("log", line))

    if aborted is None:
        try:
            proc.wait(timeout=10)
        except Exception:
            _terminate(proc)

    # 失败/aborted 一律清掉不完整输出，避免合并时混入坏文件
    if aborted or proc.returncode != 0:
        if os.path.exists(dst):
            try:
                os.remove(dst)
            except OSError:
                pass

    if aborted:
        msg_queue.put(("err", f"{os.path.basename(src)}：{aborted}"))
        return

    if proc.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst) > 0:
        msg_queue.put(("done", os.path.basename(dst), os.path.getsize(dst)))
    else:
        msg_queue.put(("err", f"{os.path.basename(src)}：转换失败（退出码 {proc.returncode}）"))



def merge_ts_lossless(files, out_ts, msg_queue, total_dur=None):
    """
    把多个 TS 片段无损拼成一个 TS（-c copy，不重编码，几秒内完成）。

    TS 是流式格式，只要各片段编码参数一致即可直接字节级拼接，
    这是最快也最保质的做法。失败时返回 False，由调用方回退到重编码。

    事件：('tsprog', 秒, 百分比, 剩余秒) / ('tsdone', 字节数) / ('tserr', 原因)
    """
    files = [f for f in files if os.path.isfile(f) and os.path.getsize(f) > 0]
    if not files:
        msg_queue.put(("tserr", "没有可合并的 TS 文件"))
        return False

    # 预检：逐个探测能否正常解码。concat demuxer 一遇到无法解析的片段就整体失败，
    # 导致「一个坏文件毁掉整批」。这里先剔除坏文件，保证好片段都能合并进来。
    good = []
    dropped = []
    for f in files:
        if probe_stream_info(f) is None:
            dropped.append(os.path.basename(f))
        else:
            good.append(f)
    if dropped:
        msg_queue.put(("tslog",
                       "已跳过 " + str(len(dropped)) + " 个无法解析的文件："
                       + "、".join(dropped[:5])
                       + ("…" if len(dropped) > 5 else "")))
    if not good:
        msg_queue.put(("tserr", "所有文件都无法解析，无法合并"))
        return False
    if len(good) != len(files):
        msg_queue.put(("tslog", f"使用 {len(good)}/{len(files)} 个有效文件进行拼接"))
    files = good

    os.makedirs(os.path.dirname(os.path.abspath(out_ts)) or ".", exist_ok=True)
    list_path = os.path.join(os.path.dirname(os.path.abspath(out_ts)),
                             "_ts_concat_list.txt")
    concat_demuxer_list(files, list_path)

    cmd = [FFMPEG, "-hide_banner", "-nostdin", "-y",
           "-f", "concat", "-safe", "0", "-i", list_path,
           "-c", "copy",
           "-progress", "pipe:1", "-loglevel", "error", out_ts]

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=CREATE_NO_WINDOW, bufsize=1,
            universal_newlines=True, encoding="utf-8", errors="ignore",
        )
    except Exception as e:
        msg_queue.put(("tserr", f"启动合并进程失败：{e}"))
        return False

    line_q = queue.Queue(maxsize=2000)

    def _reader():
        try:
            for ln in proc.stdout:
                try:
                    line_q.put_nowait(ln)
                except queue.Full:
                    pass
        except Exception:
            pass
        finally:
            line_q.put(("__EOF__", None))

    threading.Thread(target=_reader, daemon=True).start()

    t_begin = time.time()
    t_last = time.time()
    stall_limit = 120
    total_limit = max(TOTAL_TIMEOUT_BASE, int(total_dur * 10)) if total_dur else TOTAL_TIMEOUT_BASE
    aborted = None
    last_pct = -1

    while True:
        if time.time() - t_begin > total_limit:
            _terminate(proc)
            aborted = "合并超过最长处理时间"
            break
        if time.time() - t_last > stall_limit:
            _terminate(proc)
            aborted = f"合并无响应超过 {stall_limit} 秒"
            break
        if proc.poll() is not None:
            break
        try:
            line = line_q.get(timeout=0.3)
        except queue.Empty:
            continue
        if line == ("__EOF__", None):
            break
        if not isinstance(line, str):
            continue
        t_last = time.time()
        line = line.strip()
        if line.startswith("out_time_ms="):
            m = _OUT_TIME_RE.search(line)
            if m:
                secs = int(m.group(1)) / 1_000_000.0
                pct = int(secs / total_dur * 100) if total_dur else 0
                pct = max(0, min(99, pct))
                if pct != last_pct:
                    last_pct = pct
                    avg = secs / max(time.time() - t_begin, 0.001)
                    eta = max(0.0, (total_dur - secs) / avg) if total_dur else 0.0
                    msg_queue.put(("tsprog", secs, pct, eta))
        elif ("error" in line.lower() or "invalid" in line.lower()):
            msg_queue.put(("tslog", line))

    if aborted is None:
        try:
            proc.wait(timeout=10)
        except Exception:
            _terminate(proc)

    try:
        os.remove(list_path)
    except OSError:
        pass

    if aborted:
        if os.path.exists(out_ts):
            try:
                os.remove(out_ts)
            except OSError:
                pass
        msg_queue.put(("tserr", aborted))
        return False

    if proc.returncode == 0 and os.path.exists(out_ts) and os.path.getsize(out_ts) > 0:
        msg_queue.put(("tsdone", os.path.getsize(out_ts)))
        return True

    if os.path.exists(out_ts):
        try:
            os.remove(out_ts)
        except OSError:
            pass
    msg_queue.put(("tserr", f"无损拼接失败（退出码 {proc.returncode}）"))
    return False


# ---------------------------------------------------------------- 加密 HLS
def find_local_playlist(folder):
    """在文件夹里找本地 m3u8 清单（优先 local/original 之类）。"""
    if not os.path.isdir(folder):
        return None
    cands = [f for f in os.listdir(folder) if f.lower().endswith(".m3u8")]
    if not cands:
        return None
    # 优先不含"original"（通常是已下载好的本地清单）
    cands.sort(key=lambda f: ("original" in f.lower(), len(f)))
    return os.path.join(folder, cands[0])


def folder_is_encrypted_hls(folder):
    """快速判断一个文件夹是否是加密 HLS 下载目录。"""
    pl = find_local_playlist(folder)
    return bool(pl and playlist_is_encrypted(pl))


def playlist_is_encrypted(m3u8_path):
    """判断清单是否声明了 AES 加密。"""
    try:
        with open(m3u8_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if line.startswith("#EXT-X-KEY"):
                    if "AES-128" in line or "SAMPLE-AES" in line:
                        return True
    except OSError:
        return False
    return False


def rewrite_local_playlist(m3u8_path, out_path):
    """
    生成一份可本地使用的清单：
      - 密钥 URI 改成本地 enc.key（若存在）
      - 片段行改成本地相对文件名
    返回 (是否改写成功, 清单里声明的密钥IV, 片段总时长)
    """
    folder = os.path.dirname(os.path.abspath(m3u8_path))
    key_in_folder = None
    for f in os.listdir(folder):
        if f.lower().endswith((".key", ".bin")) or f.lower() == "enc.key":
            key_in_folder = os.path.join(folder, f)
            break

    total = 0.0
    iv = None
    ok = True
    try:
        with open(m3u8_path, "r", encoding="utf-8", errors="ignore") as src, \
             open(out_path, "w", encoding="utf-8", newline="\n") as dst:
            for raw in src:
                line = raw.strip()
                if not line:
                    continue
                if line.startswith("#EXT-X-KEY"):
                    m = re.search(r"IV\s*=\s*(0x[0-9a-fA-F]+)", line)
                    if m:
                        iv = m.group(1)
                    if key_in_folder:
                        # 只保留 METHOD 与 IV，URI 指向本地密钥
                        method = "AES-128"
                        if "SAMPLE-AES" in line:
                            method = "SAMPLE-AES"
                        if iv:
                            dst.write(f'#EXT-X-KEY:METHOD={method},URI="{os.path.basename(key_in_folder)}",IV={iv}\n')
                        else:
                            dst.write(f'#EXT-X-KEY:METHOD={method},URI="{os.path.basename(key_in_folder)}"\n')
                        continue
                if line.startswith("#EXTINF:"):
                    m = re.match(r"#EXTINF:\s*([\d.]+)", line)
                    if m:
                        try:
                            total += float(m.group(1))
                        except ValueError:
                            pass
                    dst.write(line + "\n")
                    continue
                if line.startswith("#"):
                    dst.write(line + "\n")
                    continue
                # 普通行 = 片段地址，改成本地文件名
                name = os.path.basename(line.split("?")[0])
                local = os.path.join(folder, name)
                if not os.path.isfile(local):
                    ok = False
                dst.write(name + "\n")
    except OSError:
        return False, None, 0.0
    return ok, iv, total


def build_hls_input_args(m3u8_path):
    """
    为加密 HLS 构造 ffmpeg 输入参数。

    返回 (extra_args, playlist_path_to_use, total_dur)；
    若该文件夹不是加密 HLS，返回 (None, None, 0)。
    """
    if not m3u8_path or not os.path.isfile(m3u8_path):
        return None, None, 0.0
    if not playlist_is_encrypted(m3u8_path):
        return None, None, 0.0

    folder = os.path.dirname(os.path.abspath(m3u8_path))
    fixed = os.path.join(folder, "_hs_local.m3u8")
    ok, iv, total = rewrite_local_playlist(m3u8_path, fixed)
    if not ok:
        # 片段对不上，仍尝试（可能部分片段命名不同）
        pass
    # allowed_extensions ALL：允许读取 enc.key 这类非媒体扩展名
    return ["-allowed_extensions", "ALL"], fixed, total


class _DoneEarly(Exception):
    """内部信号：某条特殊流程已处理完，跳过后续常规分支。"""


# MP4 容器单文件上限约 4GB，超出会导致播放器打不开。
# 超过阈值时按目标时长切成多段（默认 100 分钟一段）。
SPLIT_SIZE_LIMIT = 3900 * 1024 * 1024      # 3.8GB 留余量
DEFAULT_SPLIT_MINUTES = 100


def plan_split(size, dur, max_bytes=SPLIT_SIZE_LIMIT, target_minutes=DEFAULT_SPLIT_MINUTES):
    """
    根据文件大小与总时长计算分段方案，返回 (每段秒数, 段数)。

    原则：每段体积必须落回 max_bytes 以内 —— 段时长由码率反推得出，
    不能直接用固定分钟数，否则大文件切完每段仍然超限。
    """
    if size <= max_bytes or dur <= 0:
        return None, 0
    bytes_per_sec = size / float(dur)
    want_bytes = max_bytes * 0.85          # 留 15% 余量给封装开销
    seg_sec = want_bytes / max(bytes_per_sec, 1.0)
    seg_sec = max(60.0, seg_sec)                    # 每段至少 1 分钟
    seg_sec = min(seg_sec, target_minutes * 60.0)   # 不超过目标时长
    n = int(dur // seg_sec) + 1
    return seg_sec, n


def split_mp4(src, out_base, max_bytes=SPLIT_SIZE_LIMIT,
              target_minutes=DEFAULT_SPLIT_MINUTES, msg_queue=None,
              out_fmt=None):
    """
    把超大文件按计算出的段长切成多段（-c copy 无损，不重编码）。

    注意：MKV 没有 4GB 单文件限制（可到 2^63），因此 MKV 直接返回 [] 不做切分。
    返回 [段文件路径...]；若无需切分则返回 []。
    """
    ext = fmt_ext(out_fmt)
    # MKV 无 4GB 上限，无需切分
    if ext != ".mp4":
        return []
    try:
        size = os.path.getsize(src)
    except OSError:
        return []
    if size <= max_bytes:
        return []

    dur = probe_duration(src) or 0.0
    seg_sec, n = plan_split(size, dur, max_bytes, target_minutes)
    if not seg_sec or n < 2:
        return []

    est = size / n / 1073741824.0
    if msg_queue:
        msg_queue.put(("slog",
                       f"文件 {size / 1073741824:.2f}GB 超过 MP4 单文件上限（4GB），"
                       f"自动切分为 {n} 段（每段约 {int(seg_sec // 60)} 分钟 / 约 {est:.2f}GB）"))

    parts = []
    for i in range(n):
        start = i * seg_sec
        dur_i = seg_sec
        if i == n - 1:
            dur_i = dur - start
        if dur_i < 1.0:
            break
        dst = f"{out_base}_part{i + 1:02d}.mp4"
        cmd = [FFMPEG, "-hide_banner", "-nostdin", "-y",
               "-ss", str(start), "-t", str(dur_i), "-i", src,
               "-c", "copy", "-avoid_negative_ts", "make_zero",
               "-movflags", "+faststart", dst]
        try:
            rc = subprocess.run(cmd, capture_output=True,
                                timeout=3600, creationflags=CREATE_NO_WINDOW).returncode
        except Exception:
            rc = -1
        if rc == 0 and os.path.exists(dst) and os.path.getsize(dst) > 0:
            parts.append(dst)
            if msg_queue:
                msg_queue.put(("spart", os.path.basename(dst), os.path.getsize(dst)))
        else:
            if msg_queue:
                msg_queue.put(("slog", f"第 {i + 1} 段切分失败，已跳过"))
    return parts



def collect_inputs(paths, recursive=True):
    """把传入的文件/文件夹路径展开成待转换文件列表。"""
    out = []
    for p in paths:
        if os.path.isdir(p):
            if recursive:
                for root, _dirs, files in os.walk(p):
                    for f in files:
                        if os.path.splitext(f)[1].lower() in VIDEO_EXTS:
                            out.append(os.path.join(root, f))
            else:
                for f in os.listdir(p):
                    fp = os.path.join(p, f)
                    if os.path.isfile(fp) and os.path.splitext(f)[1].lower() in VIDEO_EXTS:
                        out.append(fp)
        elif os.path.isfile(p) and os.path.splitext(p)[1].lower() in VIDEO_EXTS:
            out.append(p)
    # 去重，保持顺序
    seen, uniq = set(), []
    for f in out:
        k = os.path.normcase(os.path.abspath(f))
        if k not in seen:
            seen.add(k)
            uniq.append(f)
    return uniq


# ---------------------------------------------------------------- 合并
_NAT_RE = re.compile(r"(\d+)")


def natural_key(path):
    """自然排序：让 ch2.ts 排在 ch10.ts 前面（纯字符串排序会排反）。"""
    name = os.path.basename(path)
    parts = _NAT_RE.split(name)
    key = []
    for p in parts:
        if p.isdigit():
            key.append((1, int(p), ""))
        else:
            key.append((0, 0, p.lower()))
    return key


def probe_stream_info(path):
    """读取 (视频编码, 宽, 高, 音频编码, 采样率, 声道)。失败返回 None。"""
    if not FFMPEG:
        return None
    # 加密 HLS 清单需要 -allowed_extensions ALL 才能读取 enc.key
    extra = ["-allowed_extensions", "ALL"] if path.lower().endswith(".m3u8") else []
    try:
        out = subprocess.run(
            [FFMPEG, "-hide_banner"] + extra + ["-i", path],
            capture_output=True, timeout=90,
            creationflags=CREATE_NO_WINDOW,
        )
        txt = out.stderr.decode("utf-8", "ignore")
    except Exception:
        return None
    vcodec = acodec = None
    width = height = None
    sr = ch = None
    m = re.search(r"Stream #\d+:\d+.*?Video:\s*([\w0-9]+).*?(\d{2,5})x(\d{2,5})", txt, re.S)
    if m:
        vcodec, width, height = m.group(1), int(m.group(2)), int(m.group(3))
    m = re.search(r"Stream #\d+:\d+.*?Audio:\s*([\w0-9]+).*?(\d+)\s*Hz.*?(mono|stereo|\d+ channels)", txt, re.S)
    if m:
        acodec = m.group(1)
        sr = int(m.group(2))
        ch = 1 if m.group(3) == "mono" else (2 if m.group(3) == "stereo" else m.group(3))
    if not vcodec:
        return None
    return {"vcodec": vcodec, "w": width, "h": height,
            "acodec": acodec, "sr": sr, "ch": ch}


def streams_compatible(infos):
    """
    判断一批视频能否用无损拼接（-c copy）。

    要求：视频编码、宽高、像素格式、帧率相关的编码参数一致，且音频编码/采样率/声道一致。
    分辨率或采样率不一致时，copy 会导致画面尺寸/音质错乱，必须走重编码。
    """
    if not infos or any(i is None for i in infos):
        return False
    first = infos[0]
    for i in infos[1:]:
        if i["vcodec"] != first["vcodec"]:
            return False
        if i.get("w") != first.get("w") or i.get("h") != first.get("h"):
            return False
        if i.get("acodec") != first.get("acodec"):
            return False
        if i.get("sr") != first.get("sr"):
            return False
        if i.get("ch") != first.get("ch"):
            return False
    return True


def concat_demuxer_list(files, list_path):
    """写 ffmpeg concat demuxer 清单。"""
    with open(list_path, "w", encoding="utf-8") as f:
        for p in files:
            # concat 清单要求单引号包裹，内部单引号用 '\'' 转义
            esc = os.path.abspath(p).replace("\\", "/").replace("'", "'\\''")
            f.write(f"file '{esc}'\n")


def merge_files(files, out_path, msg_queue, total_dur=None, delete_parts=False,
                out_fmt=None):
    """
    把多个视频合并成一个文件（mp4 / mkv）。

    策略：
      1. 优先 ffmpeg concat demuxer + -c copy（秒级完成，无损）
      2. copy 失败则退回 concat filter 重编码（参数不一致时必需）
    """
    ext = fmt_ext(out_fmt)
    faststart = fmt_faststart(out_fmt)
    files = [f for f in files if os.path.isfile(f) and os.path.getsize(f) > 0]
    if not files:
        msg_queue.put(("merr", "没有可合并的文件"))
        return False
    if len(files) == 1:
        # 只有一个文件：换封装转成目标格式（仍需走 ffmpeg 才能换容器）
        cmd = build_cmd(files[0], out_path, "copy", ("medium", 22), out_fmt=out_fmt)
        cmd = [c for c in cmd if not c.startswith("pipe:1")
               and c not in ("-progress",)]
        try:
            rc = subprocess.run(cmd, capture_output=True, timeout=3600,
                                creationflags=CREATE_NO_WINDOW).returncode
        except Exception:
            rc = -1
        if rc == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            msg_queue.put(("mdone", os.path.basename(out_path), os.path.getsize(out_path)))
            return True
        msg_queue.put(("merr", "单文件换封装失败"))
        return False

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    list_path = os.path.join(os.path.dirname(os.path.abspath(out_path)),
                             "_concat_list.txt")
    concat_demuxer_list(files, list_path)

    infos = [probe_stream_info(f) for f in files]
    can_copy = streams_compatible(infos)
    msg_queue.put(("mlog", f"检测到 {len(files)} 个文件，"
                           + ("编码一致，尝试无损拼接" if can_copy else "编码/参数不一致，将重新编码合并")))

    def _run(cmd, dur):
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=CREATE_NO_WINDOW, bufsize=1,
            universal_newlines=True, encoding="utf-8", errors="ignore",
        )
        t0 = time.time()
        for line in proc.stdout:
            line = line.strip()
            if line.startswith("out_time_ms="):
                m = _OUT_TIME_RE.search(line)
                if m:
                    secs = int(m.group(1)) / 1_000_000.0
                    pct = int(secs / dur * 100) if dur else 0
                    pct = max(0, min(99, pct))
                    avg = secs / max(time.time() - t0, 0.001)
                    eta = max(0.0, (dur - secs) / avg) if dur else 0.0
                    msg_queue.put(("mprog", secs, pct, eta))
            elif ("error" in line.lower() or "invalid" in line.lower()) \
                    and not line.startswith(("progress=", "bitrate=", "total_size=")):
                msg_queue.put(("mlog", line))
        proc.wait()
        return proc.returncode

    ok = False
    # ---- 方案 1：concat demuxer 无损拼接 ----
    if can_copy:
        cmd = [FFMPEG, "-hide_banner", "-nostdin", "-y",
               "-f", "concat", "-safe", "0", "-i", list_path,
               "-c", "copy"] + faststart + [
               "-progress", "pipe:1", "-loglevel", "error", out_path]
        msg_queue.put(("mlog", "方式一：无损拼接（-c copy）"))
        rc = _run(cmd, total_dur)
        ok = (rc == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0)
        if not ok and os.path.exists(out_path):
            try:
                os.remove(out_path)
            except OSError:
                pass

    # ---- 方案 2：重编码合并 ----
    if not ok:
        msg_queue.put(("mlog", "方式二：重新编码合并（参数对齐中）"))
        cmd = [FFMPEG, "-hide_banner", "-nostdin", "-y",
               "-f", "concat", "-safe", "0", "-i", list_path]
        for i, f in enumerate(files):
            cmd += ["-i", f]
        # concat filter：所有输入统一缩放/重采样到第一段的参数后串联
        n = len(files)
        base_info = infos[0] if infos and infos[0] else {"w": None, "h": None}
        bw, bh = base_info.get("w"), base_info.get("h")
        chains = []
        for i in range(n):
            if bw and bh:
                # 强制偶数边长，H.264 yuv420p 要求
                vscale = f"scale={bw - bw % 2}:{bh - bh % 2}"
            else:
                vscale = "scale=trunc(iw/2)*2:trunc(ih/2)*2"
            chains.append(
                f"[{i}:v:0]{vscale},setsar=1,fps=25[v{i}];"
                f"[{i}:a:0]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]"
            )
        labels = "".join(f"[v{i}][a{i}]" for i in range(n))
        filt = ";".join(chains) + f";{labels}concat=n={n}:v=1:a=1[outv][outa]"
        cmd += ["-filter_complex", filt,
                "-map", "[outv]", "-map", "[outa]",
                "-c:v", "libx264", "-preset", "medium", "-crf", "22",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k"] + faststart + [
                "-progress", "pipe:1", "-loglevel", "error", out_path]
        rc = _run(cmd, total_dur)
        ok = (rc == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0)

    try:
        os.remove(list_path)
    except OSError:
        pass

    if delete_parts and ok:
        for f in files:
            try:
                os.remove(f)
            except OSError:
                pass
        msg_queue.put(("mlog", "已清理中间文件"))

    if ok:
        msg_queue.put(("mdone", os.path.basename(out_path), os.path.getsize(out_path)))
    else:
        msg_queue.put(("merr", "合并失败"))
    return ok


def parse_dnd_paths(data):
    """把拖拽得到的字符串解析成路径列表（支持双引号包裹）。"""
    paths = []
    for m in re.finditer(r'"([^"]+)"|(\S+)', data):
        s = m.group(1) or m.group(2)
        if s:
            paths.append(s)
    return paths


# ---------------------------------------------------------------- GUI
def run_gui(initial_paths):
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    from tkinter.scrolledtext import ScrolledText

    root = tk.Tk()
    root.title("TS 转 MP4 批量工具")
    root.minsize(800, 720)
    # 初始高度取屏幕可用高度的 88%，但不低于 660，避免底部按钮被挤出可视区
    try:
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
    except Exception:
        sw, sh = 1280, 800
    init_h = max(720, min(int(sh * 0.88), 980))
    init_w = min(940, max(820, sw - 120))
    root.geometry(f"{init_w}x{init_h}")
    try:
        root.iconbitmap(default="")
    except Exception:
        pass

    state = {
        "mode": "encode",        # encode | copy
        "quality": ("medium", 22),
        "running": False,
        "cancel": False,
        "queue": queue.Queue(),
    }
    settings = {"quality": ("medium", 22), "out_mode": "same"}

    # ---------- 样式 ----------
    style = ttk.Style()
    try:
        style.theme_use("vista")
    except Exception:
        pass

    pad = dict(padx=10, pady=6)

    # ---------- 顶部：拖拽区 ----------
    drop_frame = tk.LabelFrame(root, text="① 把 ts 文件或文件夹拖到这里", font=("Microsoft YaHei UI", 9), padx=8, pady=8)
    drop_frame.pack(fill="x", **pad)
    drop_label = tk.Label(drop_frame, text="\n拖 拽 区\n\n或点击下方按钮添加", height=4,
                          bg="#f5f7fa", fg="#5b6b7c", relief="groove",
                          font=("Microsoft YaHei UI", 11), cursor="hand2")
    drop_label.pack(fill="x", expand=True)
    HAS_DND = False

    def on_click_add():
        files = filedialog.askopenfilenames(
            title="选择视频文件",
            filetypes=[("视频文件", "*.ts *.m2ts *.mts *.mpg *.mpeg *.mp4 *.vob *.flv *.mkv *.avi"),
                       ("TS 文件", "*.ts *.m2ts *.mts"), ("所有文件", "*.*")])
        if files:
            add_paths(list(files))

    def on_click_folder():
        d = filedialog.askdirectory(title="选择文件夹（批量转换其中所有视频）")
        if d:
            add_paths([d])

    btn_row = tk.Frame(root)
    btn_row.pack(fill="x", padx=10)
    ttk.Button(btn_row, text="添加文件", command=on_click_add).pack(side="left", padx=(0, 6))
    ttk.Button(btn_row, text="添加文件夹", command=on_click_folder).pack(side="left", padx=6)
    ttk.Button(btn_row, text="移除选中", command=lambda: tree.delete(*tree.selection())).pack(side="left", padx=6)
    ttk.Button(btn_row, text="清空列表", command=lambda: (not state["running"]) and tree.delete(*tree.get_children())).pack(side="left", padx=6)
    # 主转换按钮放在顶部按钮行末尾：位置固定，永远不会被挤出可视区
    top_start_btn = ttk.Button(btn_row, text="▶ 开始转换", command=lambda: None)
    top_start_btn.pack(side="right", padx=6)
    try:
        style.configure("Go.TButton", font=("Microsoft YaHei UI", 10, "bold"))
        top_start_btn.configure(style="Go.TButton")
    except Exception:
        pass

    # ---------- 文件列表 ----------
    list_frame = tk.LabelFrame(root, text="② 待转换列表", font=("Microsoft YaHei UI", 9), padx=6, pady=6)
    list_frame.pack(fill="both", expand=True, padx=10, pady=(6, 4))
    cols = ("name", "size", "path")
    tree = ttk.Treeview(list_frame, columns=cols, show="headings", height=8)
    tree.heading("name", text="文件名")
    tree.heading("size", text="大小")
    tree.heading("path", text="完整路径")
    tree.column("name", width=240)
    tree.column("size", width=90, anchor="e")
    tree.column("path", width=460)
    vsb = ttk.Scrollbar(list_frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=vsb.set)
    tree.pack(side="left", fill="both", expand=True)
    vsb.pack(side="right", fill="y")

    # 状态栏文本变量（add_paths 等回调会提前用到）
    status = tk.StringVar(value="就绪")

    def add_paths(paths):
        if state["running"]:
            messagebox.showinfo("提示", "正在转换中，请先停止后再添加文件。")
            return
        found = collect_inputs(paths)
        if not found:
            messagebox.showwarning("未找到视频", "拖入的路径中没有可转换的视频文件（支持 ts/mpg/mp4 等）。")
            return
        existing = {tree.item(i, "values")[2] for i in tree.get_children()}
        added = 0
        for f in found:
            absf = os.path.abspath(f)
            if absf not in existing:
                tree.insert("", "end", values=(
                    os.path.basename(f),
                    str(round(os.path.getsize(f) / 1048576, 1)) + " MB",
                    absf))
                added += 1
        status.set(f"已添加 {added} 个文件，队列共 {len(tree.get_children())} 个")

    def on_drop(event):
        try:
            paths = parse_dnd_paths(event.data)
        except Exception:
            paths = []
        add_paths(paths)

    try:
        drop_label.drop_target_register("DND_Files")
        drop_label.dnd_bind("<<Drop>>", on_drop)
        HAS_DND = True
    except Exception:
        HAS_DND = False

    # ---------- 选项 ----------
    opt_frame = tk.LabelFrame(root, text="③ 转换选项", font=("Microsoft YaHei UI", 9), padx=8, pady=8)
    opt_frame.pack(fill="x", padx=10, pady=4)

    tk.Label(opt_frame, text="输出位置：").grid(row=0, column=0, sticky="w", pady=3)
    out_var = tk.StringVar(value="与源文件同目录")
    out_combo = ttk.Combobox(opt_frame, textvariable=out_var, state="readonly",
                             values=("与源文件同目录", "统一输出到指定文件夹"), width=24)
    out_combo.grid(row=0, column=1, sticky="w")
    tk.Button(opt_frame, text="浏览…", command=lambda: _pick_outdir()).grid(row=0, column=2, padx=6, sticky="w")

    out_dir_var = {"val": tk.StringVar(value="")}

    def _pick_outdir():
        d = filedialog.askdirectory(title="选择输出文件夹")
        if d:
            out_dir_var["val"].set(d)
            out_var.set("统一输出到指定文件夹")

    tk.Label(opt_frame, text="编码质量：").grid(row=1, column=0, sticky="w", pady=3)
    q_var = tk.StringVar(value="中（推荐）")
    q_combo = ttk.Combobox(opt_frame, textvariable=q_var, state="readonly",
                           values=("高（慢·体积小）", "中（推荐）", "低（快·体积大）", "极速（预设 veryfast）"), width=24)
    q_combo.grid(row=1, column=1, sticky="w")

    mode_var = tk.StringVar(value="encode")
    tk.Label(opt_frame, text="转换方式：").grid(row=2, column=0, sticky="w", pady=3)
    modes = ttk.Frame(opt_frame)
    modes.grid(row=2, column=1, columnspan=3, sticky="w")
    ttk.Radiobutton(modes, text="重新编码（通用，H.264+AAC）", variable=mode_var, value="encode").pack(side="left")
    ttk.Radiobutton(modes, text="极速换封装（不重编码）", variable=mode_var, value="copy").pack(side="left", padx=(14, 0))

    # ---------- 输出格式 ----------
    tk.Label(opt_frame, text="输出格式：").grid(row=3, column=0, sticky="w", pady=3)
    fmt_row = ttk.Frame(opt_frame)
    fmt_row.grid(row=3, column=1, columnspan=3, sticky="w")
    outfmt_var = tk.StringVar(value="mp4")
    ttk.Radiobutton(fmt_row, text="MP4（手机电视通吃）", variable=outfmt_var,
                    value="mp4").pack(side="left")
    ttk.Radiobutton(fmt_row, text="MKV（不限 4GB·容错强·推荐长片）",
                    variable=outfmt_var, value="mkv").pack(side="left", padx=(14, 0))

    # ---------- 处理流程 ----------
    tk.Label(opt_frame, text="处理流程：").grid(row=4, column=0, sticky="w", pady=3)
    flow_frame = ttk.Frame(opt_frame)
    flow_frame.grid(row=4, column=1, columnspan=3, sticky="w")

    flow_var = tk.StringVar(value="merge_first")
    ttk.Radiobutton(flow_frame, text="先合并 TS → 再转 MP4（推荐·只编码一次）",
                    variable=flow_var, value="merge_first").pack(side="left")
    ttk.Radiobutton(flow_frame, text="先逐个转 MP4 → 再合并",
                    variable=flow_var, value="convert_first").pack(side="left", padx=(14, 0))
    ttk.Radiobutton(flow_frame, text="仅转换，不合并",
                    variable=flow_var, value="convert_only").pack(side="left", padx=(14, 0))

    # ---------- 合并设置 ----------
    tk.Label(opt_frame, text="合并设置：").grid(row=5, column=0, sticky="w", pady=3)
    merge_row = ttk.Frame(opt_frame)
    merge_row.grid(row=5, column=1, columnspan=3, sticky="w")

    merge_name_var = tk.StringVar(value="合并结果")
    tk.Label(merge_row, text="输出名：").pack(side="left")
    ttk.Entry(merge_row, textvariable=merge_name_var, width=16).pack(side="left", padx=(2, 10))

    order_var = tk.StringVar(value="name")
    tk.Label(merge_row, text="顺序：").pack(side="left")
    ttk.Combobox(merge_row, textvariable=order_var, state="readonly", width=12,
                 values=("文件名 1,2,3", "修改时间", "列表顺序")).pack(side="left", padx=(2, 10))

    cleanup_var = tk.BooleanVar(value=True)
    ttk.Checkbutton(merge_row, text="删除中间文件", variable=cleanup_var).pack(side="left", padx=(0, 8))

    keep_ts_var = tk.BooleanVar(value=False)
    ttk.Checkbutton(merge_row, text="保留合并的 TS", variable=keep_ts_var).pack(side="left")

    QUALITY_MAP = {"高（慢·体积小）": ("slow", 20), "中（推荐）": ("medium", 22),
                   "低（快·体积大）": ("veryfast", 26), "极速（预设 veryfast）": ("veryfast", 30)}

    # ---------- 日志 ----------
    log_frame = tk.LabelFrame(root, text="运行日志", font=("Microsoft YaHei UI", 9), padx=6, pady=6)
    # 关键：expand=False，让出多余空间给上面的列表区，保证底部按钮始终有空间
    log_frame.pack(fill="x", expand=False, padx=10, pady=(4, 6))
    log_box = ScrolledText(log_frame, height=4, font=("Consolas", 9), state="disabled")
    log_box.pack(fill="both", expand=True)

    def log(msg):
        log_box.config(state="normal")
        log_box.insert("end", msg + "\n")
        log_box.see("end")
        log_box.config(state="disabled")

    # ---------- 进度与控制 ----------
    # 进度条放进列表区下方，保证底部的「开始转换」按钮始终可见
    ctrl = tk.Frame(list_frame)
    ctrl.pack(fill="x", pady=(6, 0))
    pbar = ttk.Progressbar(ctrl, mode="determinate", maximum=100)
    pbar.pack(side="left", fill="x", expand=True, padx=(0, 10))
    status.set("就绪" + ("" if HAS_DND else "（拖拽不可用，请用按钮添加）"))
    ttk.Label(ctrl, textvariable=status).pack(side="right")

    # ---------- 底部按钮 ----------
    btn_frame = tk.Frame(root)
    btn_frame.pack(side="bottom", fill="x", padx=10, pady=(0, 10))

    start_btn = ttk.Button(btn_frame, text="▶ 开始转换", command=lambda: None)
    start_btn.pack(side="left", padx=(0, 8))
    ttk.Button(btn_frame, text="打开输出目录", command=lambda: None).pack(side="left", padx=8)
    out_btn = btn_frame.winfo_children()[1]

    def open_out_dir():
        first = tree.get_children()
        if first:
            d = os.path.dirname(tree.item(first[0], "values")[2])
            if os.path.isdir(d):
                os.startfile(d)
        else:
            os.startfile(APP_DIR)

    out_btn.config(command=open_out_dir)

    def refresh_buttons():
        if state["running"]:
            start_btn.config(text="■ 停止", command=stop_convert)
            top_start_btn.config(text="■ 停止", command=stop_convert)
            drop_label.config(text="转换中…", fg="#8a97a6")
        else:
            start_btn.config(text="▶ 开始转换", command=start_convert)
            top_start_btn.config(text="▶ 开始转换", command=start_convert)
            drop_label.config(text="\n拖 拽 区\n\n或点击下方按钮添加", fg="#5b6b7c")

    def _resolve_out(src):
        ext = fmt_ext(settings.get("out_fmt"))
        if out_var.get() == "统一输出到指定文件夹" and out_dir_var["val"].get().strip():
            return os.path.join(out_dir_var["val"].get().strip(),
                                os.path.splitext(os.path.basename(src))[0] + ext)
        return os.path.splitext(src)[0] + ext

    def _order_files(paths, mode):
        """按指定方式排序待处理文件。"""
        if mode == "time":
            return sorted(paths, key=lambda p: os.path.getmtime(p))
        if mode == "list":
            return list(paths)
        return sorted(paths, key=natural_key)

    def start_convert():
        items = tree.get_children()
        if not items:
            messagebox.showinfo("提示", "请先添加要转换的文件。")
            return
        if not FFMPEG:
            messagebox.showerror("缺少 ffmpeg", "未找到 ffmpeg 组件，程序安装可能不完整。")
            return

        flow = flow_var.get()
        if flow != "convert_only" and len(items) < 2:
            if not messagebox.askyesno("只有 1 个文件",
                                       "当前只有 1 个文件，合并没有意义。\n是否仍要按选中流程处理？"):
                return

        state["running"] = True
        state["cancel"] = False
        pbar.config(value=0)
        settings["quality"] = QUALITY_MAP.get(q_var.get(), ("medium", 22))
        settings["out_mode"] = out_var.get()
        settings["flow"] = flow
        settings["merge_name"] = merge_name_var.get().strip() or "合并结果"
        settings["order"] = order_var.get()
        settings["cleanup"] = cleanup_var.get()
        settings["keep_ts"] = keep_ts_var.get()
        settings["out_fmt"] = outfmt_var.get()
        refresh_buttons()

        fmt = settings["out_fmt"]
        ext = fmt_ext(fmt)
        flow_txt = {"merge_first": f"先合并 TS → 再转 {ext[1:].upper()}",
                    "convert_first": f"先逐个转 {ext[1:].upper()} → 再合并",
                    "convert_only": "仅转换，不合并"}[flow]
        log("=" * 46)
        log(f"输出格式：{ext[1:].upper()}" + ("（不受 4GB 限制）" if fmt == "mkv" else ""))
        log(f"处理流程：{flow_txt}")
        log(f"队列：{len(items)} 个文件")

        # 取出路径并按所选方式排序
        paths = []
        for iid in items:
            vals = tree.item(iid, "values")
            if len(vals) >= 3:
                paths.append(vals[2])
        if not paths:
            messagebox.showwarning("提示", "没有可处理的文件。")
            state["running"] = False
            refresh_buttons()
            return
        ordered = _order_files(paths, settings["order"])
        log(f"排序方式：{settings['order']}")
        for i, p in enumerate(ordered[:30], 1):
            log(f"    {i:>2}. {os.path.basename(p)}")
        if len(ordered) > 30:
            log(f"    … 其余 {len(ordered) - 30} 个略")
        if flow != "convert_only":
            log(f"最终输出：{with_ext(settings['merge_name'], settings.get('out_fmt'))}")

        threading.Thread(target=worker, args=(ordered,), daemon=True).start()

    def stop_convert():
        state["cancel"] = True
        log("已请求停止，当前步骤结束后停止…")

    def _drain_until_terminal(tag_done=True, tag_err=True, max_idle=480):
        """
        消费队列事件直到收到 done/err，返回 (是否成功, 说明文本)。

        注意：绝不能用进度百分比作为退出条件。
        convert_one 发出的进度上限是 99（100% 留给完成态），
        若用 file_pct >= 100 退出，会在 done 事件到达前就返回，
        把成功的转换误判为失败。必须只认 done/err 事件。
        """
        t0 = time.time()
        last_touch = time.time()
        while True:
            if state["cancel"]:
                return False, "已取消"
            try:
                ev = state["queue"].get(timeout=0.3)
            except queue.Empty:
                # 长时间既无事件也无 done/err 才判定异常
                if time.time() - last_touch > max_idle:
                    return False, "长时间无响应"
                continue
            last_touch = time.time()
            kind = ev[0]
            if kind == "prog":
                a, c = ev[2], ev[3]
                pbar.config(value=a)
                left = f"{int(c // 60):02d}:{int(c % 60):02d}" if c > 0 else "--:--"
                status.set(f"转换中 {a}%  |剩余 {left}")
            elif kind == "done":
                log(f"    ✓ 完成（{ev[2] / 1048576:.1f} MB）")
                return True, ev[2]
            elif kind == "err":
                log(f"    ✗ {ev[1]}")
                return False, ev[1]
            elif kind == "log":
                log(f"    · {ev[1]}")

    def _drain_merge(tag, idle=600):
        """消费 ts* / m* 事件直到完成，返回 (是否成功, 说明)。"""
        t0 = time.time()
        while True:
            if state["cancel"]:
                return False, "已取消"
            try:
                ev = state["queue"].get(timeout=0.2)
            except queue.Empty:
                if time.time() - t0 > idle:
                    return False, "合并阶段长时间无响应"
                continue
            k = ev[0]
            if k in ("tsprog", "mprog"):
                a, c = ev[2], ev[3]
                pbar.config(value=a)
                left = f"{int(c // 60):02d}:{int(c % 60):02d}" if c > 0 else "--:--"
                label = "拼接中" if k == "tsprog" else "合并中"
                status.set(f"{label} {a}%  |剩余 {left}")
            elif k in ("tslog", "mlog"):
                log(f"    {ev[1]}")
            elif k in ("tsdone", "mdone"):
                log(f"    ★ {ev[1]}（{ev[2] / 1048576:.1f} MB）")
                return True, ev[2]
            elif k in ("tserr", "merr"):
                log(f"    ✗ {ev[1]}")
                return False, ev[1]

    def _target_dir(sample):
        if out_var.get() == "统一输出到指定文件夹" and out_dir_var["val"].get().strip():
            return out_dir_var["val"].get().strip()
        return os.path.dirname(sample)

    def worker(paths):
        t_start = time.time()
        flow = settings["flow"]
        ok = fail = 0
        final_path = None
        merged_path = None
        tmp_ts = None

        try:
            # ============ 加密 HLS 特殊处理 ============
            # m3u8 下载的剧集：TS 分片是 AES-128 加密的，无法单独解码。
            # 此时以「本地清单」为单一输入，交给 ffmpeg 边解密边转码，
            # 完全不需要先做 TS 合并（也无法合并）。
            hls_folder = os.path.dirname(os.path.abspath(paths[0]))
            hls_playlist = find_local_playlist(hls_folder)
            hls_extra, hls_fixed, hls_total = (None, None, 0.0)
            if hls_playlist and playlist_is_encrypted(hls_playlist):
                hls_extra, hls_fixed, hls_total = build_hls_input_args(hls_playlist)

            if hls_fixed:
                log("\n检测到加密 HLS（m3u8 + AES-128），改为直接解密转码")
                log(f"    清单：{os.path.basename(hls_fixed)}")
                log(f"    影片总时长约 {int(hls_total // 60)} 分{hls_total % 60:.0f} 秒")
                log("    说明：加密片段无法单独解码，将由 ffmpeg 边解密边转码，耗时较长")
                tdir_h = _target_dir(paths[0])
                name_h = settings["merge_name"]
                final_path = os.path.join(
                    tdir_h, with_ext(name_h, settings.get("out_fmt")))
                convert_one(hls_fixed, final_path, mode_var.get(),
                            settings["quality"], state["queue"],
                            hls_total, input_extra=hls_extra,
                            out_fmt=settings.get("out_fmt"))
                good, info = _drain_until_terminal(max_idle=max(600, int(hls_total * 3)))
                if not good:
                    fail += 1
                else:
                    ok += 1
                final_path = final_path if os.path.exists(final_path) else None

                # 超过 4GB 的 MP4 大多播放器打不开，自动切段
                if final_path and os.path.exists(final_path):
                    sq = queue.Queue()
                    parts = split_mp4(final_path,
                                       os.path.splitext(final_path)[0],
                                       msg_queue=sq,
                                       out_fmt=settings.get("out_fmt"))
                    part_logs = []
                    while not sq.empty():
                        pe = sq.get()
                        if pe[0] == "slog":
                            part_logs.append("    " + pe[1])
                            log("    " + pe[1])
                        elif pe[0] == "spart":
                            part_logs.append(f"    ★ {pe[1]}（{pe[2] / 1073741824:.2f} GB）")
                    if parts:
                        try:
                            os.remove(final_path)
                        except OSError:
                            pass
                        log(f"    已切分为 {len(parts)} 个文件，原始大文件已删除")
                        final_path = parts[0]

                raise _DoneEarly()      # 加密模式不走下面的常规流程

            # ============ 流程 A：先合并 TS，再转 MP4 ============
            if flow == "merge_first":
                # 先剔除无法解析的坏文件：concat demuxer 遇到坏片段会整体失败，
                # 不能让一个损坏文件毁掉整批合并。
                usable = []
                broken = []
                for p in paths:
                    if os.path.getsize(p) == 0 or probe_stream_info(p) is None:
                        broken.append(os.path.basename(p))
                    else:
                        usable.append(p)
                if broken:
                    log(f"跳过 {len(broken)} 个损坏/空文件："
                        + "、".join(broken[:5]) + ("…" if len(broken) > 5 else ""))
                if not usable:
                    raise RuntimeError("所有文件都无法解析，无法合并")
                if len(usable) != len(paths):
                    log(f"将使用 {len(usable)}/{len(paths)} 个有效文件")

                total_dur = 0.0
                for p in usable:
                    d = probe_duration(p)
                    if d:
                        total_dur += d
                log(f"\n[阶段 1/2] 合并 {len(usable)} 个 TS（按 "
                    f"{settings['order']}，无损拼接不重编码）…")
                log(f"    合并后总时长约 {int(total_dur // 60)} 分"
                    f"{total_dur % 60:.0f} 秒")

                tdir = _target_dir(usable[0])
                name = settings["merge_name"]
                tmp_ts = os.path.join(tdir, f"_tmp_merged_{name}.ts")
                if not merge_ts_lossless(usable, tmp_ts, state["queue"], total_dur):
                    log("    ! 无损拼接失败，改为逐段解码后重编码拼接…")
                    tmp_ts = os.path.join(tdir, f"_tmp_merged_{name}.ts")
                    fallback = tmp_ts.replace(".ts", ".mp4")
                    merge_files(usable, fallback, state["queue"], total_dur,
                                delete_parts=False,
                                out_fmt=settings.get("out_fmt"))
                    good_m, _info = _drain_merge("m")
                    if not good_m:
                        raise RuntimeError("TS 合并失败")
                    tmp_ts = fallback

                if state["cancel"]:
                    raise RuntimeError("已取消")

                ext = fmt_ext(settings.get("out_fmt"))
                log(f"\n[阶段 2/2] 转换为 {ext[1:].upper()}"
                    f"（{'重新编码' if mode_var.get() == 'encode' else '极速换封装'}）…")
                merged_path = os.path.join(tdir, with_ext(name, settings.get("out_fmt")))
                dur = probe_duration(tmp_ts) or total_dur
                convert_one(tmp_ts, merged_path, mode_var.get(),
                            settings["quality"], state["queue"], dur,
                            out_fmt=settings.get("out_fmt"))
                good, info = _drain_until_terminal()
                if not good:
                    fail += 1
                else:
                    ok += 1
                final_path = merged_path if os.path.exists(merged_path) else None

            # ============ 流程 B：先逐个转 MP4，再合并 ============
            elif flow == "convert_first":
                produced = []
                n = len(paths)
                log(f"\n[阶段 1/2] 逐个转换 {n} 个文件…")
                for idx, path in enumerate(paths):
                    if state["cancel"]:
                        log("■ 已停止。")
                        break
                    name = os.path.basename(path)
                    dst = _resolve_out(path)
                    log(f"[{idx + 1}/{n}] {name}")
                    if not os.path.exists(dst):
                        os.makedirs(os.path.dirname(dst), exist_ok=True)

                    attempt = 1
                    while True:
                        dur = probe_duration(path)
                        convert_one(path, dst, mode_var.get(), settings["quality"],
                                    state["queue"], dur,
                                    cancel_flag=lambda: state["cancel"],
                                    attempt=attempt,
                                    out_fmt=settings.get("out_fmt"))
                        pbar.config(value=(idx * 100 + 99) / n)
                        good, info = _drain_until_terminal()
                        if good:
                            ok += 1
                            produced.append(dst)
                            break
                        if ("退出码" in str(info)) and attempt < MAX_ATTEMPTS \
                                and not state["cancel"]:
                            attempt += 1
                            log(f"    ↻ {info}，重试中…")
                            continue
                        fail += 1
                        break

                if state["cancel"]:
                    return
                if len(produced) < 2:
                    if produced:
                        final_path = produced[0]
                    return

                log(f"\n[阶段 2/2] 合并 {len(produced)} 个 MP4…")
                # 只合并真实存在且非空的产物，避免坏文件拖垮整批
                valid = [p for p in produced
                         if os.path.isfile(p) and os.path.getsize(p) > 0]
                if len(valid) != len(produced):
                    log(f"    跳过 {len(produced) - len(valid)} 个无效产物")
                if len(valid) < 2:
                    if valid:
                        final_path = valid[0]
                        log("有效文件不足 2 个，跳过合并")
                    else:
                        log("没有可合并的有效文件")
                    return
                final_path = os.path.join(
                    _target_dir(paths[0]),
                    with_ext(settings["merge_name"], settings.get("out_fmt")))
                # 注意顺序：必须先启动合并（入队 m* 事件），再消费
                merge_files(valid, final_path, state["queue"], None,
                            delete_parts=settings["cleanup"],
                            out_fmt=settings.get("out_fmt"))
                good, info = _drain_merge("m")
                if not good:
                    fail += 1

            # ============ 流程 C：仅转换，不合并 ============
            else:
                n = len(paths)
                log(f"\n逐个转换 {n} 个文件（不合并）…")
                for idx, path in enumerate(paths):
                    if state["cancel"]:
                        log("■ 已停止。")
                        break
                    log(f"[{idx + 1}/{n}] {os.path.basename(path)}")
                    dst = _resolve_out(path)
                    if not os.path.exists(dst):
                        os.makedirs(os.path.dirname(dst), exist_ok=True)
                    attempt = 1
                    while True:
                        dur = probe_duration(path)
                        convert_one(path, dst, mode_var.get(), settings["quality"],
                                    state["queue"], dur,
                                    cancel_flag=lambda: state["cancel"],
                                    attempt=attempt,
                                    out_fmt=settings.get("out_fmt"))
                        pbar.config(value=(idx * 100 + 99) / n)
                        good, info = _drain_until_terminal()
                        if good:
                            ok += 1
                            break
                        if ("退出码" in str(info)) and attempt < MAX_ATTEMPTS \
                                and not state["cancel"]:
                            attempt += 1
                            log(f"    ↻ {info}，重试中…")
                            continue
                        fail += 1
                        break

        except _DoneEarly:
            pass          # 加密 HLS 分支已完成，走 finally 收尾
        except Exception as e:
            log(f"✗ 处理异常：{e}")
            fail += 1
        finally:
            # 超大 MP4 自动切段（MP4 单文件上限约 4GB）
            if final_path and os.path.exists(final_path) and not state["cancel"]:
                try:
                    if os.path.getsize(final_path) > SPLIT_SIZE_LIMIT:
                        sq = queue.Queue()
                        parts = split_mp4(
                            final_path,
                            os.path.splitext(final_path)[0],
                            msg_queue=sq,
                            out_fmt=settings.get("out_fmt"))
                        while not sq.empty():
                            pe = sq.get()
                            if pe[0] == "slog":
                                log("    " + pe[1])
                            elif pe[0] == "spart":
                                log(f"    ★ {pe[1]}（{pe[2] / 1073741824:.2f} GB）")
                        if parts:
                            try:
                                os.remove(final_path)
                            except OSError:
                                pass
                            log(f"    已切分为 {len(parts)} 个文件，原始大文件已删除")
                            final_path = parts[0]
                except Exception:
                    pass

            # 清理中间 TS
            if tmp_ts and os.path.exists(tmp_ts):
                if settings.get("keep_ts"):
                    final_ts = os.path.splitext(final_path or merged_path or "")[0] + ".ts"
                    try:
                        if final_ts != tmp_ts and os.path.dirname(final_ts):
                            shutil.move(tmp_ts, final_ts)
                            log(f"★ 保留合并后的 TS：{os.path.basename(final_ts)}")
                    except Exception:
                        pass
                else:
                    try:
                        os.remove(tmp_ts)
                    except OSError:
                        pass

            pbar.config(value=100)
            spent = time.time() - t_start
            log("-" * 46)
            log(f"结束：成功 {ok}，失败 {fail}，用时 {int(spent // 60)} 分{int(spent % 60)} 秒")
            if final_path and os.path.exists(final_path):
                log(f"★ 最终成品：{final_path}")
            state["running"] = False
            root.after(0, refresh_buttons)
            if fail == 0:
                root.after(0, lambda: status.set("全部完成"))
            else:
                root.after(0, lambda: status.set(f"完成，{fail} 个失败"))

    refresh_buttons()

    # 兼容：命令行传入文件（拖到 exe 图标上）
    if initial_paths:
        root.after(300, lambda: add_paths(initial_paths))

    root.mainloop()


def main():
    if not FFMPEG and not getattr(sys, "frozen", False):
        print("警告：未找到 ffmpeg，打包后会内置。")
    argv = sys.argv[1:]
    if argv:
        files = collect_inputs(argv)
        if files:
            run_gui(files)
            return
        # 无文件参数 → 打开空白界面
    run_gui([])


if __name__ == "__main__":
    main()