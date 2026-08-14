from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .collect import collect_all
from .github_api import list_repos, repo_tree

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"

app = FastAPI(title="quota-hub", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/quota")
def api_quota():
    return collect_all()


@app.get("/api/github/repos")
def api_repos():
    return list_repos()


@app.get("/api/github/tree")
def api_tree(repo: str = Query(..., min_length=3), ref: str | None = None):
    if "/" not in repo or ".." in repo:
        raise HTTPException(400, "invalid repo")
    return repo_tree(repo, ref)
