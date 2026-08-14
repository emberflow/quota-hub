from __future__ import annotations

import json
import os
import subprocess
from typing import Any


def _gh() -> list[str]:
    if os.name == "nt":
        return ["cmd", "/c", "gh"]
    return ["gh"]


def _run(args: list[str], timeout: int = 40) -> str:
    proc = subprocess.run(
        _gh() + args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(err or f"gh exit {proc.returncode}")
    if not proc.stdout:
        raise RuntimeError("gh 无输出")
    return proc.stdout


def list_repos(limit: int = 80) -> dict[str, Any]:
    try:
        raw = _run(
            [
                "repo",
                "list",
                "--limit",
                str(limit),
                "--json",
                "nameWithOwner,description,updatedAt,isPrivate,url,defaultBranchRef",
            ]
        )
        repos = json.loads(raw)
        return {"ok": True, "repos": repos}
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "remedy": "运行 gh auth login",
            "repos": [],
        }


def repo_tree(full_name: str, ref: str | None = None) -> dict[str, Any]:
    if "/" not in full_name:
        return {"ok": False, "error": "仓库名应为 owner/name", "entries": []}
    sha = ref or "HEAD"
    try:
        raw = _run(
            [
                "api",
                f"repos/{full_name}/git/trees/{sha}?recursive=1",
            ],
            timeout=60,
        )
        data = json.loads(raw)
        tree = data.get("tree") or []
        entries = []
        for item in tree[:4000]:
            path = item.get("path") or ""
            if not path:
                continue
            entries.append(
                {
                    "path": path,
                    "type": item.get("type"),
                    "size": item.get("size") or 0,
                }
            )
        return {
            "ok": True,
            "truncated": bool(data.get("truncated")) or len(tree) > 4000,
            "sha": data.get("sha"),
            "entries": entries,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "entries": []}
