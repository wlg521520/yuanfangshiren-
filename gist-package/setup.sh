#!/usr/bin/env bash
#
# 一键准备构建依赖（Windows / Git Bash / WSL 均可）
#
#   1. 下载 ffmpeg 静态版（gyan.dev，含 libx264 + aac）
#   2. 创建 Python 虚拟环境并安装打包所需依赖
#
# 用法：
#   bash scripts/setup.sh
#
# 依赖下载后的目录：
#   third_party/ffmpeg/bin/ffmpeg.exe
#   .venv/                     （虚拟环境）
#
set -e

# 兼容两种目录布局：
#   仓库版  scripts/xxx.sh  -> 项目根在上一级
#   Gist 版 xxx.sh（扁平）  -> 项目根就是本级
_HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$_HERE/ts2mp4.py" ]; then
  PROJ="$_HERE"
else
  PROJ="$(cd "$_HERE/.." && pwd)"
fi
cd "$PROJ"

FF_DIR="$PROJ/third_party/ffmpeg"
FF_BIN="$FF_DIR/bin"

echo "==> 项目目录: $PROJ"

# ---------------------------------------------------------------- Python
# 可用 PYTHON 环境变量指定解释器，例如：
#   PYTHON="C:/Python312/python.exe" bash scripts/setup.sh
PY="${PYTHON:-}"
if [ -n "$PY" ] && ! command -v "$PY" >/dev/null 2>&1 && [ ! -f "$PY" ]; then
  echo "!! PYTHON 指向的解释器不存在：$PY"; exit 1
fi
if [ -z "$PY" ]; then
  for cand in python python3 py; do
    if command -v "$cand" >/dev/null 2>&1; then
      if "$cand" -c "import sys; sys.exit(0 if sys.version_info>=(3,9) else 1)" 2>/dev/null; then
        PY="$cand"; break
      fi
    fi
  done
fi

if [ -z "$PY" ]; then
  echo "!! 未找到 Python 3.9+，请先安装：https://www.python.org/downloads/"
  echo "   安装时请勾选「Add Python to PATH」"
  exit 1
fi
echo "==> 使用 Python: $PY ($($PY -V 2>&1))"

# tkinter 是标准库但需随安装包提供，Python 3.13 起官方安装包已不再自带，
# 缺失时必须提前报错，否则后面打包才失败、难以定位。
if ! "$PY" -c "import tkinter" >/dev/null 2>&1; then
  echo ""
  echo "!! 当前 Python 缺少 tkinter，无法运行图形界面。"
  echo ""
  echo "   原因：Python 3.13 起官方 Windows 安装包不再捆绑 Tcl/Tk。"
  echo "   解决办法（二选一）："
  echo ""
  echo "   A) 安装 Python 3.12 或更低版本（自带 tkinter，推荐）："
  echo "      https://www.python.org/downloads/release/python-3129/"
  echo "      安装时务必勾选 tcl/tk 组件"
  echo ""
  echo "   B) 已有 Python 3.12+ 时，安装 tcl/tk 后重跑本脚本"
  echo ""
  echo "   已检测到的 Python："
  for c in python python3 py; do
    command -v "$c" >/dev/null 2>&1 && echo "     $c -> $("$c" -V 2>&1) ($("$c" -c 'import tkinter' >/dev/null 2>&1 && echo '含 tkinter' || echo '缺 tkinter'))"
  done
  exit 1
fi
echo "    tkinter: 可用"

# ---------------------------------------------------------------- ffmpeg
if [ -f "$FF_BIN/ffmpeg.exe" ] || [ -f "$FF_BIN/ffmpeg" ]; then
  echo "==> ffmpeg 已存在，跳过下载"
else
echo "==> 下载 ffmpeg（约 80MB，请稍候）..."
mkdir -p "$FF_DIR"
ZIP="$FF_DIR/ffmpeg.zip"

# 主源 GitHub，备用源 gyan.dev（前者被墙或限流时用后者）
FFMPEG_URLS=(
  "https://github.com/GyanD/codexffmpeg/releases/download/7.1/ffmpeg-7.1-essentials_build.zip"
  "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
)

DOWNLOADED=0
for URL in "${FFMPEG_URLS[@]}"; do
  echo "    源：$URL"
  if command -v curl >/dev/null 2>&1; then
    curl -L --retry 3 --retry-delay 2 --connect-timeout 20 -o "$ZIP" "$URL" && DOWNLOADED=1 || DOWNLOADED=0
  elif command -v wget >/dev/null 2>&1; then
    wget -O "$ZIP" "$URL" && DOWNLOADED=1 || DOWNLOADED=0
  else
    echo "!! 需要 curl 或 wget"
    DOWNLOADED=0
  fi
  if [ "$DOWNLOADED" = "1" ] && [ -s "$ZIP" ]; then
    echo "    下载完成"
    break
  fi
  echo "    该源失败，尝试下一个…"
done

if [ "$DOWNLOADED" != "1" ] || [ ! -s "$ZIP" ]; then
  echo ""
  echo "!! ffmpeg 下载失败。请手动下载后解压到：$FF_DIR"
  echo "   下载地址："
  echo "     https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
  echo "   解压后把其中的 ffmpeg.exe 放到 $FF_BIN/ 即可"
  exit 1
fi

  echo "==> 解压..."
  if command -v unzip >/dev/null 2>&1; then
    unzip -q -o "$ZIP" -d "$FF_DIR"
  else
    PYTHON_EXE="$($PY -c 'import sys;print(sys.executable)')"
    "$PYTHON_EXE" -c "
import zipfile,sys,os
z=zipfile.ZipFile(sys.argv[1]); z.extractall(sys.argv[2])
" "$ZIP" "$FF_DIR"
  fi
  rm -f "$ZIP"

  # 解压后目录形如 ffmpeg-7.1-essentials_build/bin/ffmpeg.exe，统一挪到 third_party/ffmpeg/bin
  FOUND="$(find "$FF_DIR" -name "ffmpeg.exe" -o -name "ffmpeg" -type f 2>/dev/null | head -1)"
  if [ -n "$FOUND" ]; then
    SRC_BIN="$(dirname "$FOUND")"
    mkdir -p "$FF_BIN"
    cp -f "$SRC_BIN"/ffmpeg* "$FF_BIN/" 2>/dev/null || true
    rm -rf "$FF_DIR"/ffmpeg-*-build
  fi
  echo "==> ffmpeg 就绪: $FF_BIN"
fi

# ---------------------------------------------------------------- venv
if [ ! -d "$PROJ/.venv" ]; then
  echo "==> 创建虚拟环境 .venv"
  "$PY" -m venv "$PROJ/.venv"
fi
VENV_PY="$PROJ/.venv/Scripts/python.exe"
[ -f "$VENV_PY" ] || VENV_PY="$PROJ/.venv/bin/python"

echo "==> 安装 Python 依赖"
"$VENV_PY" -m pip install --upgrade pip -q
"$VENV_PY" -m pip install -q -r "$PROJ/requirements.txt"

echo ""
echo "==> 准备完成"
echo "   构建：bash build.sh"
echo "   运行：bash run.sh"
