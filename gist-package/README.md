# TS 转 MP4 / MKV 批量工具

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-green.svg)]()
![Windows](https://img.shields.io/badge/platform-Windows-0078D4?logo=windows)

一个 Windows 桌面图形工具：把 TS / M2TS 等电视录制片段批量转换并**合并成一个完整视频**，
支持加密 HLS（m3u8 + AES-128）直接解密转码。

单文件 exe，双击即用，**内置 ffmpeg，无需另行安装**。

> 这是 [yuanfangshiren-](https://github.com/wlg521520/yuanfangshiren-) 仓库的第一个项目 ——
> 也就是「自己的第一个开源项目」。

---

## 特点

- **拖拽即用** — 把文件或整个文件夹拖进窗口；也可直接拖到 exe 图标上
- **先合并再转码** — TS 无损拼接后只编码一次，比"逐个转再合并"快得多
- **支持加密 HLS** — 自动识别 m3u8 + AES-128，改写密钥路径后由 ffmpeg 边解密边转码
- **自然排序** — `ch1 → ch2 → ch3 → ch10`，不会把 ch10 排到 ch2 前面
- **输出 MP4 或 MKV** — MKV 无 4GB 限制、容错强，且可只换容器不重编码（极快）
- **超 4GB 自动分段** — MP4 超过 3.8GB 时自动切成多段，无损不重编码
- **三重超时保护** — 单个坏文件或卡死的 ffmpeg 不会拖垮整批任务
- **实时进度** — 总进度、当前进度、剩余时间预估

## 界面

```
┌──────────────────────────────────────────────────────────────┐
│ ① 把 ts 文件或文件夹拖到这里                                  │
├──────────────────────────────────────────────────────────────┤
│ ② 待转换列表            文件名 | 大小 | 完整路径              │
├──────────────────────────────────────────────────────────────┤
│ ③ 转换选项                                                   │
│    输出位置  : 与源文件同目录 / 统一输出到指定文件夹          │
│    编码质量  : 高 / 中 / 低 / 极速                            │
│    转换方式  : 重新编码 / 极速换封装                         │
│    输出格式  : MP4（兼容性好）/ MKV（不限 4GB·容错强）       │
│    处理流程  : 先合并再转 / 先转再合并 / 仅转换               │
│    合并设置  : 输出名 / 顺序 / 清理中间文件 / 保留合并的 TS   │
├──────────────────────────────────────────────────────────────┤
│ 运行日志                                                     │
├──────────────────────────────────────────────────────────────┤
│ ▓▓▓▓▓▓▓░░░░░░░  总进度 45% | 当前 62% | 剩余 03:15        │
│           [▶ 开始转换]  [打开输出目录]                        │
└──────────────────────────────────────────────────────────────┘
```

## 下载

前往 [Releases](https://github.com/<你的账号>/ts2mp4/releases) 下载 `TS转MP4.exe`，
双击运行即可（Windows 10/11，64 位）。

> exe 已内置 ffmpeg 7.1（含 libx264 / AAC），无需安装任何运行库。

## 使用方法

### 基本转换

1. 双击打开 `TS转MP4.exe`
2. 把 ts 文件（或整个文件夹）拖进窗口
3. 选好输出格式与处理流程
4. 点「▶ 开始转换」

### 合并录制片段

录制电视/摄像头往往产生多个分片（`seg_0.ts`、`seg_1.ts`…）。
选择流程 **「先合并 TS → 再转 MP4」**，程序会按文件名自然排序后无损拼接，
再一次性编码成完整视频 —— **只编码一次**，比逐个转再合并快得多。

### 处理加密剧集（m3u8）

下载的 m3u8 视频分片往往是 AES-128 加密的，单个文件无法解码。
把**整个文件夹**（含 `.m3u8` 与 `.key`）拖进窗口，程序会自动：

1. 找到本地清单，把密钥路径改写为本地文件
2. 以清单为输入，由 ffmpeg 边解密边转码

日志会提示 `检测到加密 HLS（m3u8 + AES-128），改为直接解密转码`。

### 长片建议选 MKV

若片子很长（数小时），建议 **输出格式选 MKV + 转换方式选「极速换封装」**：

- MKV 没有 4GB 单文件限制，不用分段
- 换封装不重编码，137 分钟的片子约 **1.4 秒**即可完成（重编码需约 90 分钟）
- 画质零损失

## 从源码构建

### 依赖

- Windows 10/11
- Python 3.9+（安装时勾选 **tcl/tk** 组件与 "Add Python to PATH"）
- Git Bash（脚本基于 bash 编写）

### 步骤

```bash
git clone <仓库地址>
cd ts2mp4

# 1. 准备依赖（自动下载 ffmpeg、创建虚拟环境、装 Python 包）
bash scripts/setup.sh

# 2. 直接以源码运行，调试用
bash scripts/run.sh

# 3. 或打包成单文件 exe
bash scripts/build.sh
#    产物在 dist/TS转MP4.exe
```

`setup.sh` 会自动从 [gyan.dev](https://gyan.dev/ffmpeg/builds/) 下载 ffmpeg 静态版
（含 libx264 与 AAC），解压到 `third_party/ffmpeg/`。

## 项目结构

```
ts2mp4/
├── src/
│   ├── ts2mp4.py           # 主程序（单文件，含 GUI 与全部处理逻辑）
│   └── test_*.py           # 测试脚本
├── scripts/
│   ├── setup.sh            # 一键准备依赖（下载 ffmpeg、建 venv、装包）
│   ├── build.sh            # PyInstaller 打包
│   └── run.sh              # 源码直接运行
├── docs/
│   └── 技术说明.md          # 实现细节与设计取舍
├── requirements.txt
├── LICENSE                 # GPL-3.0
└── README.md
```

## 测试

```bash
bash scripts/run.sh --test      # 或分别执行
.venv/Scripts/python.exe src/test_core.py
.venv/Scripts/python.exe src/test_merge.py
.venv/Scripts/python.exe src/test_robust.py
.venv/Scripts/python.exe src/test_mkv.py
```

测试覆盖：核心转换链路、合并与自然排序、异常文件容错、加密 HLS、MKV 输出、GUI 流程。

## 常见问题

**Q: 提示"所有文件都无法解析，无法合并"**
A: 这些是 AES-128 加密的 HLS 分片，不能单独解码。请确认**整个文件夹**（含 `.m3u8`
与 `.key` 文件）一起拖入，程序会自动走解密路径。

**Q: 转到一半不动了**
A: 旧版本存在阻塞缺陷，现已修复（三重超时保护）。若仍遇到，请附上日志反馈。

**Q: 生成的视频播放器打不开**
A: MP4 超过 4GB 会超出格式上限。程序会自动分段；若想避免，请改用 MKV 输出。

**Q: 合并后顺序不对**
A: 请使用「文件名 1,2,3」排序方式（自然排序）。默认已是此模式。

**Q: 提示缺少 ffmpeg**
A: 运行 `bash scripts/setup.sh` 自动下载；或手动下载 ffmpeg 放入 `third_party/ffmpeg/bin/`。

## 致谢

- [FFmpeg](https://ffmpeg.org/) — 音视频处理核心
- [gyan.dev](https://gyan.dev/ffmpeg/builds/) — Windows 静态构建
- [PyInstaller](https://pyinstaller.org/) — 打包为单文件 exe
- [tkinterdnd2](https://github.com/jsn5/tkinterdnd2) — 界面拖拽支持

## 许可证

本项目采用 **GPL-3.0** 许可证，详见 [LICENSE](LICENSE)。

打包产物内含 FFmpeg（GPL v3 构建），因此分发 exe 时同样适用 GPL-3.0。
