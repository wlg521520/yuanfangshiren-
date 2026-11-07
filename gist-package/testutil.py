# -*- coding: utf-8 -*-
"""
测试公共辅助：统一解析 ffmpeg 与测试素材目录。

不同仓库布局下 ffmpeg 可能位于：
  - third_party/ffmpeg/bin   （scripts/setup.sh 下载的默认位置）
  - build_deps/ffmpeg/bin    （旧版布局，兼容保留）
  - 系统 PATH                （已全局安装 ffmpeg）
"""
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# 兼容两种目录布局：
#   仓库版  src/testutil.py   -> 项目根在上一级
#   Gist 版 testutil.py（扁平）-> 项目根就是本级
PROJ = os.path.dirname(HERE) if os.path.basename(HERE) == "src" else HERE


def setup_ffmpeg_path():
    """把 ffmpeg 目录加入 PATH，返回找到的可执行文件路径（找不到返回 None）。"""
    candidates = [
        os.environ.get("TS2MP4_FFMPEG_DIR", ""),
        os.path.join(PROJ, "third_party", "ffmpeg", "bin"),
        os.path.join(PROJ, "build_deps", "ffmpeg", "bin"),
        os.path.join(HERE, "build_deps", "ffmpeg", "bin"),
    ]
    for d in candidates:
        if d and os.path.isdir(d):
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
    exe = shutil.which("ffmpeg")
    if not exe:
        print("!! 未找到 ffmpeg。")
        print("   请执行：bash scripts/setup.sh")
        print("   或设置环境变量 TS2MP4_FFMPEG_DIR 指向 ffmpeg 所在目录，")
        print("   或把 ffmpeg 装到系统 PATH 中。")
        sys.exit(1)
    return exe


FFMPEG = setup_ffmpeg_path()
TESTDATA = os.path.join(PROJ, "testdata")


def ensure_testdata():
    os.makedirs(TESTDATA, exist_ok=True)
    return TESTDATA


def import_app():
    """导入主模块（主文件与测试同目录）。"""
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import ts2mp4
    return ts2mp4


def make_ts(path, duration=3, color="blue", freq=440, size="320x240", rate=25):
    """用 ffmpeg 生成一段测试用 TS（纯色画面 + 正弦音）。"""
    import subprocess
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    cmd = [
        FFMPEG, "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=c={color}:size={size}:rate={rate}:duration={duration}",
        "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={duration}",
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
        "-shortest", "-y", path,
    ]
    flags = 0x08000000 if os.name == "nt" else 0
    subprocess.run(cmd, capture_output=True, timeout=300, creationflags=flags)
    return os.path.exists(path)


def make_segments(folder, count=4, duration=3):
    """生成用于合并测试的分片：ch1/ch2/ch3/ch10（红绿蓝黄）。"""
    colors = {1: "red", 2: "green", 3: "blue", 10: "yellow"}
    names = list(colors.keys())[:count] if count <= 4 else list(range(1, count + 1))
    out = []
    for n in names:
        c = colors.get(n, "blue")
        p = os.path.join(folder, f"ch{n}.ts")
        make_ts(p, duration=duration, color=c, freq=300 + n * 100)
        out.append(p)
    return out
