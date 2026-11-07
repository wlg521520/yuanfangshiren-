#!/usr/bin/env bash
#
# 直接以源码方式运行（免打包，便于开发调试）
#
#   bash scripts/run.sh              正常启动
#   bash scripts/run.sh 视频.ts ...  带参数启动（等价于拖到 exe 图标）
#
# 前置：先执行 bash scripts/setup.sh
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

VENV_PY="$PROJ/.venv/Scripts/python.exe"
[ -f "$VENV_PY" ] || VENV_PY="$PROJ/.venv/bin/python"

if [ ! -f "$VENV_PY" ]; then
  echo "!! 未找到虚拟环境，请先执行：bash scripts/setup.sh"; exit 1
fi

FF_BIN="$PROJ/third_party/ffmpeg/bin"
export PATH="$FF_BIN:$PATH"

exec "$VENV_PY" "$PROJ/ts2mp4.py" "$@"
