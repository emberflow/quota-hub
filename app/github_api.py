from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any


_OWNER_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_REPO_RE = re.compile(r"[A-Za-z0-9._-]{1,100}$")
_REF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$")


def valid_repo(full_name: str) -> bool:
    if not isinstance(full_name, str) or full_name.count("/") != 1:
        return False
    owner, name = full_name.split("/", 1)
    return bool(
        _OWNER_RE.fullmatch(owner)
        and _REPO_RE.fullmatch(name)
        and not name.startswith((".", "-"))
        and not name.endswith((".", "-"))
        and ".." not in name
    )


def valid_ref(ref: str) -> bool:
    return bool(
        isinstance(ref, str)
        and _REF_RE.fullmatch(ref)
        and not ref.startswith((".", "/", "-"))
        and not ref.endswith((".", "/"))
        and ".." not in ref
        and "//" not in ref
        and "@{" not in ref
    )


def _gh() -> list[str]:
    if os.name == "nt":
        return ["cmd", "/c", "gh"]
    return ["gh"]


def _run(args: list[str], timeout: int = 40) -> str:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    proc = subprocess.run(
        _gh() + args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=flags,
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
    if not valid_repo(full_name):
        return {"ok": False, "error": "仓库名应为 owner/name", "entries": []}
    sha = (ref or "HEAD").strip() or "HEAD"
    if sha != "HEAD" and not valid_ref(sha):
        return {"ok": False, "error": "invalid ref", "entries": []}
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
