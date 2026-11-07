# 贡献指南

感谢你愿意参与改进这个项目。

## 报告问题

提交 issue 时请附上：

- 系统版本（Windows 10 / 11，64 位）
- 软件版本（Release 页面）
- **运行日志的完整截图或文本**（界面右下角「运行日志」框）
- 复现步骤
- 若涉及具体文件，请说明文件类型（普通 TS / 加密 HLS / 其他）

## 提交代码

1. Fork 后新建分支：`git checkout -b fix/xxx`
2. 改动尽量小而聚焦，一个 PR 只做一件事
3. 提交前跑一遍测试：

   ```bash
   export TS2MP4_FFMPEG_DIR=$PWD/third_party/ffmpeg/bin
   for t in test_core test_merge test_robust test_flow test_mkv test_bulk; do
     .venv/Scripts/python.exe "src/$t.py"
   done
   ```

4. 提交信息用中文或英文均可，说明**改了什么**与**为什么**

## 代码约定

- 保持单文件结构（`src/ts2mp4.py`），这是本项目的刻意选择 —— 便于单文件分发
- 新增功能请同时补充对应测试脚本
- 涉及 ffmpeg 调用时，务必保留**超时保护**（见 `docs/技术说明.md` 的「三重超时保护」）
- 不要引入除 `pyinstaller`、`tkinterdnd2` 之外的第三方依赖
- 注释说明**为什么**这么做，而非复述代码

## 关键约束（改动时务必留意）

这几处是踩过坑总结出来的，改动时容易破坏：

| 约束 | 原因 |
|---|---|
| 消费端只认 `done`/`err` 事件判定成败 | 用进度百分比退出会把成功任务误记为失败 |
| ffmpeg stdout 必须由**读线程**读取 | 主线程直接读会在管道空时阻塞，导致整个队列锁死 |
| 合并前用 `probe_stream_info` 预检过滤坏文件 | concat demuxer 遇坏片段会整体失败 |
| 判定能否 `-c copy` 必须比对宽高/采样率/声道 | 只比编码名会漏掉分辨率差异，导致画面尺寸错乱 |
| 分段段长须由码率反推 | 用固定分钟数会让每段仍然超出 4GB |
| MKV 不要加 `-bsf:a aac_adtstoasc` | 那是 MP4 专用 filter |

## 许可

提交代码即表示你同意你的贡献以 **GPL-3.0** 许可发布。
