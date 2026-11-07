#!/usr/bin/env bash
#
# 打包 TS 转 MP4/MKV 工具（PyInstaller onefile）
#
# 前置：先执行 bash scripts/setup.sh 准备依赖
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

# Git Bash 的路径形如 /c/Users/...，PyInstaller 无法识别，
# 需用 cygpath 转换成 Windows 形式 C:/Users/...
if command -v cygpath >/dev/null 2>&1; then
  WIN_PROJ="$(cygpath -w "$PROJ")"
else
  WIN_PROJ="$PROJ"
fi

FF_BIN="$PROJ/third_party/ffmpeg/bin"
VENV_PY="$PROJ/.venv/Scripts/python.exe"
[ -f "$VENV_PY" ] || VENV_PY="$PROJ/.venv/bin/python"

echo "==> 1/4 校验依赖"
if [ ! -x "$VENV_PY" ] && [ ! -f "$VENV_PY" ]; then
  echo "!! 未找到虚拟环境，请先执行：bash scripts/setup.sh"; exit 1
fi
FF_EXE=""
for f in "$FF_BIN/ffmpeg.exe" "$FF_BIN/ffmpeg"; do
  [ -f "$f" ] && FF_EXE="$f" && break
done
if [ -z "$FF_EXE" ]; then
  echo "!! 未找到 ffmpeg，请先执行：bash scripts/setup.sh"; exit 1
fi
echo "    ffmpeg: $FF_EXE"

echo "==> 2/4 检查打包依赖"
"$VENV_PY" -c "import PyInstaller, tkinterdnd2" 2>/dev/null || {
  echo "!! 缺少 pyinstaller/tkinterdnd2，请执行：bash scripts/setup.sh"; exit 1
}

echo "==> 3/4 运行 PyInstaller"
rm -rf "$PROJ/build" "$PROJ/dist"

# PyInstaller 需要 Windows 形式路径
WIN_FF_EXE="$(cygpath -w "$FF_EXE" 2>/dev/null || echo "$FF_EXE")"
WIN_SRC="$(cygpath -w "$PROJ/ts2mp4.py" 2>/dev/null || echo "$PROJ/ts2mp4.py")"
WIN_DIST="$(cygpath -w "$PROJ/dist" 2>/dev/null || echo "$PROJ/dist")"
WIN_WORK="$(cygpath -w "$PROJ/build" 2>/dev/null || echo "$PROJ/build")"
WIN_SPEC="$(cygpath -w "$PROJ" 2>/dev/null || echo "$PROJ")"

ARGS=(
  --noconfirm --clean --onefile --windowed
  --name "TS转MP4"
  --add-binary "$WIN_FF_EXE;."
  --hidden-import tkinterdnd2
  --collect-all tkinterdnd2
  --distpath "$WIN_DIST"
  --workpath "$WIN_WORK"
  --specpath "$WIN_SPEC"
)

# ffprobe 可选：有则一并打包（用于精确取时长），没有也能靠 ffmpeg 解析
for f in "$FF_BIN/ffprobe.exe" "$FF_BIN/ffprobe"; do
  [ -f "$f" ] && ARGS+=(--add-binary "$(cygpath -w "$f" 2>/dev/null || echo "$f");.")
done

"$VENV_PY" -m PyInstaller "${ARGS[@]}" "$WIN_SRC"

echo "==> 4/4 完成"
ls -lh "$PROJ/dist/"
