# -*- coding: utf-8 -*-
"""
一键发布到 GitHub。

用法：
    # 发布为 Gist（公开）
    python scripts/publish.py --token <TOKEN> --gist

    # 发布为 Gist（私密）
    python scripts/publish.py --token <TOKEN> --gist --private

    # 创建仓库并推送（完整功能：Releases / Issues / CI）
    python scripts/publish.py --token <TOKEN> --repo --repo-name ts2mp4

    # 只检查环境，不实际发布
    python scripts/publish.py --token <TOKEN> --gist --dry-run

Token 获取：
    GitHub → Settings → Developer settings → Personal access tokens
    → Fine-grained tokens → Generate new token
      - Gist 模式：勾选 "Gists" 权限
      - Repo 模式：勾选 "Repository administration" + "Contents" 读写

也可以用环境变量：export GITHUB_TOKEN=xxx
"""
import argparse
import base64
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request

API = "https://api.github.com"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 需要发布的文件（相对仓库根）。sample.ts 是二进制，单独处理。
TEXT_FILES = [
    "README.md", "LICENSE", "CHANGELOG.md", "CONTRIBUTING.md",
    "requirements.txt", ".gitignore", ".gitattributes",
    "src/ts2mp4.py", "src/testutil.py",
    "src/test_core.py", "src/test_merge.py", "src/test_robust.py",
    "src/test_flow.py", "src/test_mkv.py", "src/test_bulk.py",
    "src/test_gui_flow.py", "src/test_exe_e2e.py",
    "scripts/setup.sh", "scripts/build.sh", "scripts/run.sh",
    "docs/技术说明.md",
    ".github/workflows/build.yml",
]
BINARY_FILES = ["testdata/sample.ts"]

# Gist 是扁平结构，文件名不能带目录分隔符，这里做映射
GIST_NAME_MAP = {
    "src/ts2mp4.py": "ts2mp4.py",
    "src/testutil.py": "testutil.py",
    "src/test_core.py": "test_core.py",
    "src/test_merge.py": "test_merge.py",
    "src/test_robust.py": "test_robust.py",
    "src/test_flow.py": "test_flow.py",
    "src/test_mkv.py": "test_mkv.py",
    "src/test_bulk.py": "test_bulk.py",
    "src/test_gui_flow.py": "test_gui_flow.py",
    "src/test_exe_e2e.py": "test_exe_e2e.py",
    "scripts/setup.sh": "setup.sh",
    "scripts/build.sh": "build.sh",
    "scripts/run.sh": "run.sh",
    "docs/技术说明.md": "技术说明.md",
    ".github/workflows/build.yml": "build.yml",
}


def die(msg, code=1):
    print("!! " + msg, file=sys.stderr)
    sys.exit(code)


def check_network():
    """检查 GitHub API 可达性，不通时给出可操作的提示。"""
    print("==> 检查 GitHub 连通性 ...")
    try:
        req = urllib.request.Request(API + "/rate_limit",
                                     headers={"User-Agent": "ts2mp4-publisher"})
        with urllib.request.urlopen(req, timeout=20) as r:
            r.read(1)
        print("    OK：api.github.com 可访问")
        return True
    except urllib.error.HTTPError as e:
        print(f"    HTTP {e.code}（网络可达，可能是代理拦截了部分域名）")
        return True
    except Exception as e:
        print(f"    无法连接：{e}")
        print("")
        print("    GitHub 域名可能��被网络/代理拦截。排查方式：")
        print("      curl -I https://api.github.com")
        print("      若返回 000，说明需配置代理或换网络环境。")
        print("      Windows 也可设置：set HTTPS_PROXY=http://127.0.0.1:端口")
        return False


def api(token, method, path, payload=None):
    """调用 GitHub REST API。"""
    url = API + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "ts2mp4-publisher")
    if data:
        req.add_header("Content-Type", "application/json")
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=60, context=ctx) as r:
        raw = r.read()
        return json.loads(raw.decode("utf-8")) if raw else {}


def collect_files():
    """读取待发布文件，返回 [(相对路径, 文本内容)] 与 [(相对路径, base64内容)]。"""
    texts, binaries = [], []
    for rel in TEXT_FILES:
        p = os.path.join(ROOT, rel)
        if not os.path.isfile(p):
            print(f"    跳过缺失文件：{rel}")
            continue
        with open(p, "r", encoding="utf-8") as f:
            texts.append((rel, f.read()))
    for rel in BINARY_FILES:
        p = os.path.join(ROOT, rel)
        if not os.path.isfile(p):
            continue
        with open(p, "rb") as f:
            binaries.append((rel, base64.b64encode(f.read()).decode("ascii")))
    return texts, binaries


def do_gist(token, public, dry_run):
    texts, binaries = collect_files()
    print(f"==> 准备发布 {len(texts)} 个文本文件 + {len(binaries)} 个二进制文件")

    if dry_run:
        print("\n--- 将要上传的文件 ---")
        for rel, _ in texts:
            print(f"    {GIST_NAME_MAP.get(rel, rel)}")
        for rel, _ in binaries:
            print(f"    {os.path.basename(rel)}  (二进制)")
        return 0

    files = {}
    for rel, content in texts:
        files[GIST_NAME_MAP.get(rel, rel)] = {"content": content}
    for rel, b64 in binaries:
        files[os.path.basename(rel)] = {"content": b64}

    payload = {
        "description": "TS 转 MP4/MKV 批量工具：合并录制片段并转码，支持加密 HLS、"
                       "自然排序、超 4GB 自动分段。Windows 桌面工具，GPL-3.0。",
        "public": bool(public),
        "files": files,
    }
    print("==> 创建 Gist ...")
    res = api(token, "POST", "/gists", payload)
    url = res.get("html_url", "")
    print(f"✓ 已创建：{url}")
    if not public:
        print("  （私密 Gist，只有你自己可见）")
    print()
    print("  下载全部文件：")
    print(f"    git clone {res.get('git_pull_url', '')}")
    return 0


def do_repo(token, repo_name, description, dry_run):
    texts, binaries = collect_files()
    print(f"==> 准备创建仓库 {repo_name}（{len(texts) + len(binaries)} 个文件）")

    if dry_run:
        print("\n--- 将要提交的文件 ---")
        for rel, _ in texts:
            print(f"    {rel}")
        for rel, _ in binaries:
            print(f"    {rel}")
        return 0

    print("==> 创建仓库 ...")
    try:
        res = api(token, "POST", "/user/repos", {
            "name": repo_name,
            "description": description,
            "private": False,
            "auto_init": False,
        })
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")
        if e.code == 422:
            die(f"仓库 {repo_name} 已存在。\n   {body[:300]}")
        die(f"创建仓库失败 HTTP {e.code}：{body[:300]}")

    owner = res["owner"]["login"]
    default_branch = res.get("default_branch", "main")
    print(f"✓ 已创建：{res['html_url']}")

    print("==> 上传文件 ...")
    # 逐个提交（Git Data API）：每次一个 commit，便于失败重试
    sha = None
    all_items = [(rel, c) for rel, c in texts] + [(rel, c) for rel, c in binaries]
    for idx, (rel, content) in enumerate(all_items, 1):
        is_bin = rel in BINARY_FILES
        payload = {
            "message": f"添加 {rel}",
            "content": content,
            "branch": default_branch,
        }
        if is_bin:
            payload["encoding"] = "base64"
        try:
            api(token, "PUT", f"/repos/{owner}/{repo_name}/contents/{rel}", payload)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "ignore")
            die(f"上传 {rel} 失败 HTTP {e.code}：{body[:200]}")
        print(f"    [{idx}/{len(all_items)}] {rel}")

    print()
    print(f"✓ 完成：{res['html_url']}")
    print("  后续可在该仓库添加 Release 上传 exe 成品：")
    print(f"    gh release create v1.2 {os.path.join(ROOT, 'dist', 'TS转MP4.exe')}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="发布 TS 转 MP4/MKV 工具到 GitHub")
    ap.add_argument("--token", help="GitHub PAT（也可用环境变量 GITHUB_TOKEN）")
    ap.add_argument("--gist", action="store_true", help="发布为 Gist")
    ap.add_argument("--repo", action="store_true", help="创建 GitHub 仓库")
    ap.add_argument("--repo-name", default="ts2mp4", help="仓库名（--repo 时有效）")
    ap.add_argument("--private", action="store_true", help="Gist 设为私密")
    ap.add_argument("--dry-run", action="store_true", help="只检查不发布")
    args = ap.parse_args()

    token = args.token or os.environ.get("GITHUB_TOKEN")
    if not token:
        die("缺少 token。用 --token <TOKEN> 或设置环境变量 GITHUB_TOKEN")

    if not args.gist and not args.repo:
        die("请指定 --gist 或 --repo")

    print("=" * 52)
    print("TS 转 MP4/MKV 工具 —— 发布到 GitHub")
    print("=" * 52)

    if not args.dry_run and not check_network():
        die("网络不通，无法发布。请先解决代理/网络问题。")

    if args.gist:
        return do_gist(token, not args.private, args.dry_run)
    return do_repo(token, args.repo_name,
                   "Windows 桌面工具：批量转换并合并 TS 录制片段，"
                   "支持加密 HLS、MP4/MKV 输出、超 4GB 自动分段。", args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
