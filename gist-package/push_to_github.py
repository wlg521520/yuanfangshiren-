# -*- coding: utf-8 -*-
"""
把本项目推送到 GitHub 仓库 wlg521520/yuanfangshiren-。

需要 GitHub Personal Access Token（PAT）：
  GitHub → Settings → Developer settings → Personal access tokens
  → Fine-grained tokens → Generate new token
    - Repository access : 只勾选 yuanfangshiren- （或 All repositories）
    - Permissions       : Contents → Read and write
  token 只需勾 Contents 读写即可。

用法：
    python scripts/push_to_github.py --token <TOKEN>
    export GITHUB_TOKEN=xxx && python scripts/push_to_github.py
    python scripts/push_to_github.py --token <TOKEN> --dry-run
"""
import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OWNER = "wlg521520"
REPO = "yuanfangshiren-"
BRANCH = "main"

# 仓库中已存在、需要先删除再重建的文件（GitHub 自动生成的模板）
STALE = [".gitignore", "LICENSE", "README.md"]

FILES = [
    "README.md", "LICENSE", "CHANGELOG.md", "CONTRIBUTING.md",
    "requirements.txt", ".gitignore", ".gitattributes",
    "src/ts2mp4.py", "src/testutil.py",
    "src/test_core.py", "src/test_merge.py", "src/test_robust.py",
    "src/test_flow.py", "src/test_mkv.py", "src/test_bulk.py",
    "src/test_gui_flow.py", "src/test_exe_e2e.py",
    "scripts/setup.sh", "scripts/build.sh", "scripts/run.sh",
    "scripts/push_to_github.py", "scripts/publish.py",
    "docs/技术说明.md",
    ".github/workflows/build.yml",
]


def die(msg, code=1):
    print("!! " + msg, file=sys.stderr)
    sys.exit(code)


def api(token, method, path, payload=None, raw=False):
    url = API + path
    data = None
    if payload is not None:
        data = payload if raw else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "ts2mp4-publisher")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            body = r.read()
            return json.loads(body.decode("utf-8")) if body else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:400]
        die(f"{method} {path} → HTTP {e.code}\n   {detail}")


def get_sha(token, path):
    """取文件 blob 的 sha（不存在返回 None）。"""
    try:
        res = api(token, "GET", f"/repos/{OWNER}/{REPO}/contents/{path}")
        return res.get("sha")
    except SystemExit:
        return None


def main():
    ap = argparse.ArgumentParser(description="推送项目到 wlg521520/yuanfangshiren-")
    ap.add_argument("--token", help="GitHub PAT（也可用环境变量 GITHUB_TOKEN）")
    ap.add_argument("--dry-run", action="store_true", help="只检查不推送")
    args = ap.parse_args()

    token = args.token or os.environ.get("GITHUB_TOKEN")
    if not token:
        die("缺少 token。用 --token <TOKEN> 或设置环境变量 GITHUB_TOKEN")

    print("=" * 52)
    print("推送到 GitHub: " + OWNER + "/" + REPO)
    print("=" * 52)

    # 1. 验证身份与权限
    print("==> 验证 token ...")
    me = api(token, "GET", "/user")
    login = me.get("login", "?")
    print(f"    账号：{login}")
    if login.lower() != OWNER.lower():
        print(f"    ! 提示：token 属于 {login}，与目标仓库所有者 {OWNER} 不同")

    repo = api(token, "GET", f"/repos/{OWNER}/{REPO}")
    print(f"    仓库：{repo.get('full_name')}（{'私有' if repo.get('private') else '公开'}）"
          f"，默认分支 {repo.get('default_branch')}")

    missing = [f for f in FILES if not os.path.isfile(os.path.join(ROOT, f))]
    if missing:
        die("本地缺少文件：" + ", ".join(missing))

    if args.dry_run:
        print("\n--- 将要推送的文件 ---")
        for f in FILES:
            print("    " + f)
        print(f"\n共 {len(FILES)} 个文件（另有 testdata/sample.ts 需 git push 才能带二进制）")
        return 0

    # 2. 删除仓库自动生成的模板文件（内容将被项目文件取代）
    print("==> 清理仓库自动生成的模板文件 ...")
    for path in STALE:
        sha = get_sha(token, path)
        if not sha:
            print(f"    跳过 {path}（不存在）")
            continue
        try:
            api(token, "DELETE", f"/repos/{OWNER}/{REPO}/contents/{path}",
                {"message": f"移除 GitHub 自动生成的 {path}", "sha": sha, "branch": BRANCH})
            print(f"    已删除 {path}")
        except SystemExit:
            print(f"    ! 删除 {path} 失败，继续")

    # 3. 推送全部文件
    print(f"==> 推送 {len(FILES)} 个文件 ...")
    # 逐个提交：每个文件一个 commit，便于定位失败文件
    for i, rel in enumerate(FILES, 1):
        with open(os.path.join(ROOT, rel), "r", encoding="utf-8") as f:
            content = f.read()
        sha = get_sha(token, rel)
        payload = {
            "message": f"添加 {rel}",
            "content": content,
            "branch": BRANCH,
        }
        if sha:
            payload["sha"] = sha
        api(token, "PUT", f"/repos/{OWNER}/{REPO}/contents/{rel}", payload)
        print(f"    [{i}/{len(FILES)}] {rel}")

    print()
    print("✓ 完成")
    print("  仓库地址：https://github.com/" + OWNER + "/" + REPO)
    print()
    print("  剩余步骤（二进制测试样例需 git 推送，或直接跳过）：")
    print("    git clone https://github.com/" + OWNER + "/" + REPO + ".git")
    print("    cd " + REPO)
    print("    cp <本地>/ts2mp4-open/testdata/sample.ts testdata/")
    print("    git add testdata/sample.ts && git commit -m '添加测试样例' && git push")
    print()
    print("  上传 exe 成品（Releases）：")
    print("    gh release create v1.2 dist/TS转MP4.exe --title 'v1.2' --notes '见 CHANGELOG'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
