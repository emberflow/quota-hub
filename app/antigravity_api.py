from __future__ import annotations

import base64
import ctypes
import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .models import ModelRow, OFFICIAL, Provider, Window
from .timefmt import annotate_window, parse_ts

# Public installed-app OAuth client shipped inside `agy` (same as agy-cli-usage).
# Per-user identity is the local refresh token, not this pair.
_OAUTH_CLIENT_ID = "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com"
_OAUTH_CLIENT_SECRET = "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"
_B64_PREFIX = "go-keyring-base64:"
_CRED_TARGET = "gemini:antigravity"
_HOSTS = ("cloudcode-pa.googleapis.com", "daily-cloudcode-pa.googleapis.com")
_META = {
    "ideType": "ANTIGRAVITY",
    "platform": "PLATFORM_UNSPECIFIED",
    "pluginType": "GEMINI",
}


class CREDENTIAL(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.c_void_p),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


def _npx() -> list[str]:
    if os.name == "nt":
        return ["cmd", "/c", "npx", "-y"]
    return ["npx", "-y"]


def _as_percent(value) -> float | None:
    """Cloud Code remainingFraction is 0–1; some CLIs already emit 0–100."""
    if value is None:
        return None
    n = float(value)
    if 0 <= n <= 1.5:
        return round(n * 100.0, 2)
    return round(n, 2)


def _parse_expiry(value) -> Optional[datetime]:
    if not value:
        return None
    s = str(value).strip().replace("Z", "+00:00")
    s = re.sub(r"(\.\d{6})\d+", r"\1", s)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _expired(expiry) -> bool:
    dt = _parse_expiry(expiry)
    if not dt:
        return False
    return dt.timestamp() <= datetime.now(timezone.utc).timestamp() + 60


def _read_credman(target: str) -> bytes:
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    adv.CredReadW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.POINTER(CREDENTIAL)),
    ]
    adv.CredReadW.restype = wintypes.BOOL
    adv.CredFree.argtypes = [ctypes.c_void_p]
    ptr = ctypes.POINTER(CREDENTIAL)()
    if not adv.CredReadW(target, 1, 0, ctypes.byref(ptr)):
        raise RuntimeError(f"CredRead {target} failed {ctypes.get_last_error()}")
    try:
        cred = ptr.contents
        if not cred.CredentialBlobSize or not cred.CredentialBlob:
            raise RuntimeError("empty credential blob")
        return ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
    finally:
        adv.CredFree(ptr)


def _decode_secret(raw: bytes) -> dict:
    text = raw.decode("utf-8", "replace").strip("\x00").strip()
    if not (text.startswith(_B64_PREFIX) or text.startswith("{")):
        text = raw.decode("utf-16le", "replace").strip("\x00").strip()
    if text.startswith(_B64_PREFIX):
        text = base64.b64decode(text[len(_B64_PREFIX) :]).decode("utf-8")
    obj = json.loads(text)
    token = obj.get("token") if isinstance(obj.get("token"), dict) else obj
    if not isinstance(token, dict) or not token.get("access_token"):
        raise RuntimeError("agy credential has no access_token")
    return token


def _token_files() -> list[Path]:
    override = os.environ.get("AGY_OAUTH_TOKEN_FILE")
    home = Path.home()
    paths = []
    if override:
        paths.append(Path(override))
    paths.extend(
        [
            home / ".gemini" / "antigravity-cli" / "antigravity-oauth-token",
            home / ".gemini" / "oauth_creds.json",
        ]
    )
    return paths


def _read_local_token() -> dict:
    if os.name == "nt":
        try:
            return _decode_secret(_read_credman(_CRED_TARGET))
        except Exception:
            pass
    last = "no agy credential"
    for path in _token_files():
        try:
            if path.exists():
                return _decode_secret(path.read_bytes())
        except Exception as exc:
            last = str(exc)
    raise RuntimeError(last)


def _refresh_access_token(refresh_token: str) -> str:
    body = urllib.parse.urlencode(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": _OAUTH_CLIENT_ID,
            "client_secret": _OAUTH_CLIENT_SECRET,
        }
    ).encode()
    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        payload = json.loads(resp.read().decode("utf-8", "ignore"))
    token = payload.get("access_token")
    if not token:
        raise RuntimeError("Google token refresh 没有 access_token")
    return token


def _access_token() -> str:
    cred = _read_local_token()
    token = cred.get("access_token")
    if token and not _expired(cred.get("expiry")):
        return token
    refresh = cred.get("refresh_token")
    if not refresh:
        if token:
            return token
        raise RuntimeError("agy 登录已过期且没有 refresh_token")
    return _refresh_access_token(refresh)


def _post(host: str, token: str, method: str, body: dict) -> dict:
    req = urllib.request.Request(
        f"https://{host}/v1internal:{method}",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "antigravity",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            return json.loads(resp.read().decode("utf-8", "ignore") or "{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "ignore")[:180]
        raise RuntimeError(f"HTTP {exc.code} {method}: {detail}") from exc


def _cloudcode(token: str) -> tuple[dict, dict]:
    last = "Cloud Code 无响应"
    for host in _HOSTS:
        try:
            lca = _post(host, token, "loadCodeAssist", {"metadata": _META})
            summary = _post(host, token, "retrieveUserQuotaSummary", {})
            return lca, summary
        except Exception as exc:
            last = str(exc)
            if "401" in last or "403" in last:
                raise
    raise RuntimeError(last)


def _window_from_bucket(wid: str, label: str, kind: str, bucket: dict, extra: str) -> Window:
    left = _as_percent(bucket.get("remainingFraction"))
    meta = annotate_window(left, parse_ts(bucket.get("resetTime")))
    return Window(
        id=wid,
        label=label,
        kind=kind,
        percent_remaining=left,
        percent_used=None if left is None else round(100 - left, 2),
        extra=extra,
        **meta,
    )


def _apply_summary(p: Provider, lca: dict, summary: dict) -> Provider:
    paid = lca.get("paidTier") if isinstance(lca.get("paidTier"), dict) else {}
    current = lca.get("currentTier") if isinstance(lca.get("currentTier"), dict) else {}
    p.plan = str(paid.get("name") or current.get("name") or current.get("id") or "")
    p.source = "agy Cloud Code retrieveUserQuotaSummary"
    windows: list[Window] = []
    models: list[ModelRow] = []
    for group in summary.get("groups") or []:
        if not isinstance(group, dict):
            continue
        name = str(group.get("displayName") or "Models")
        desc = str(group.get("description") or "")
        extra = desc.replace("Models within this group:", "").strip()
        is_gemini = "gemini" in name.lower()
        for bucket in group.get("buckets") or []:
            if not isinstance(bucket, dict):
                continue
            window = str(bucket.get("window") or "")
            bid = str(bucket.get("bucketId") or "")
            kind = "weekly" if window == "weekly" or "week" in bid else "session"
            if is_gemini:
                wid = "gemini" if kind == "weekly" else "gemini-5h"
                label = "Gemini 周额度" if kind == "weekly" else "Gemini 5 小时"
            else:
                wid = "other" if kind == "weekly" else "other-5h"
                label = "其它模型周额度" if kind == "weekly" else "其它模型 5 小时"
            windows.append(_window_from_bucket(wid, label, kind, bucket, extra))
        if extra:
            models.append(ModelRow(name=name, extra=extra))
    p.windows = windows
    p.models = models
    if windows:
        p.status = "fresh"
        p.error = ""
        p.remedy = ""
    else:
        p.status = "unavailable"
        p.error = "已连上但没有额度分组"
        p.remedy = "在 agy 里打开 /usage 对照"
    return p


def _from_cloudcode(p: Provider) -> Provider:
    token = _access_token()
    try:
        lca, summary = _cloudcode(token)
    except Exception as exc:
        if "401" in str(exc) or "403" in str(exc):
            cred = _read_local_token()
            refresh = cred.get("refresh_token")
            if not refresh:
                raise
            token = _refresh_access_token(refresh)
            lca, summary = _cloudcode(token)
        else:
            raise
    return _apply_summary(p, lca, summary)


def _as_model_list(raw: Any) -> list[dict]:
    if isinstance(raw, list):
        return [m for m in raw if isinstance(m, dict)]
    if isinstance(raw, dict):
        out = []
        for k, v in raw.items():
            if isinstance(v, dict):
                row = dict(v)
                row.setdefault("modelId", k)
                out.append(row)
        return out
    return []


def _extract_models(payload: Any) -> tuple[str, list[dict]]:
    plan = ""
    models: list[dict] = []
    if isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict):
                continue
            snap = item.get("snapshot") if isinstance(item.get("snapshot"), dict) else item
            if not isinstance(snap, dict):
                continue
            plan = str(snap.get("planType") or snap.get("plan") or snap.get("tier") or plan)
            raw = snap.get("models") or snap.get("quota") or snap.get("groups") or []
            chunk = _as_model_list(raw)
            if chunk:
                models.extend(chunk)
            elif "remainingPercentage" in snap or "quotaInfo" in snap or "label" in snap:
                models.append(snap)
        return plan, models
    if isinstance(payload, dict):
        plan = str(payload.get("planType") or payload.get("plan") or payload.get("tier") or "")
        raw = payload.get("models") or payload.get("quota") or []
        if isinstance(payload.get("accounts"), list) and payload["accounts"]:
            first = payload["accounts"][0]
            if isinstance(first, dict):
                plan = str(first.get("planType") or plan)
                nested = first.get("snapshot") if isinstance(first.get("snapshot"), dict) else first
                raw = nested.get("models") or nested.get("quota") or raw
        if payload.get("groups"):
            return plan, []
        return plan, _as_model_list(raw)
    return "", []


def _model_quota(m: dict) -> tuple[float | None, Any]:
    qi = m.get("quotaInfo") if isinstance(m.get("quotaInfo"), dict) else {}
    raw = m.get("remainingPercentage")
    if raw is None:
        raw = m.get("remainingFraction")
    if raw is None:
        raw = qi.get("remainingFraction")
    if raw is None:
        raw = qi.get("remainingPercentage")
    left = _as_percent(raw)
    if left is None and (m.get("isExhausted") or qi.get("isExhausted")):
        left = 0.0
    resets = m.get("resetTime") or m.get("resetsAt") or qi.get("resetTime") or qi.get("resetsAt")
    return left, resets


def _from_agy_snapshot(p: Provider, payload: dict) -> Provider:
    """agy-cli-usage --json Snapshot."""
    p.plan = str(payload.get("tier") or p.plan)
    p.source = "agy-cli-usage"
    windows: list[Window] = []
    models: list[ModelRow] = []
    for group in payload.get("groups") or []:
        if not isinstance(group, dict):
            continue
        name = str(group.get("name") or "Models")
        extra = str(group.get("models") or "")
        is_gemini = "gemini" in name.lower()
        for bucket in group.get("buckets") or []:
            if not isinstance(bucket, dict):
                continue
            kind_raw = str(bucket.get("kind") or "")
            kind = "weekly" if kind_raw == "weekly" else "session"
            left = _as_percent(bucket.get("remainingFraction"))
            if left is None and bucket.get("available"):
                left = 100.0
            resets = bucket.get("resetAt")
            if not resets and bucket.get("resetsInSeconds") is not None:
                secs = float(bucket["resetsInSeconds"])
                resets = datetime.now(timezone.utc).timestamp() + secs
            meta = annotate_window(left, parse_ts(resets))
            if is_gemini:
                wid = "gemini" if kind == "weekly" else "gemini-5h"
                label = "Gemini 周额度" if kind == "weekly" else "Gemini 5 小时"
            else:
                wid = "other" if kind == "weekly" else "other-5h"
                label = "其它模型周额度" if kind == "weekly" else "其它模型 5 小时"
            windows.append(
                Window(
                    id=wid,
                    label=label,
                    kind=kind,
                    percent_remaining=left,
                    percent_used=None if left is None else round(100 - left, 2),
                    extra=extra,
                    **meta,
                )
            )
        if extra:
            models.append(ModelRow(name=name, extra=extra))
    p.windows = windows
    p.models = models
    p.status = "fresh" if windows else "unavailable"
    return p


def _from_legacy_models(p: Provider, payload: Any) -> Provider:
    plan, models = _extract_models(payload)
    p.plan = plan or p.plan
    windows: list[Window] = []
    model_rows: list[ModelRow] = []
    gemini_left = None
    other_left = None
    gemini_reset = None
    other_reset = None
    for m in models:
        if m.get("isAutocompleteOnly"):
            continue
        name = str(m.get("label") or m.get("modelId") or m.get("name") or m.get("displayName") or "?")
        mid = str(m.get("modelId") or name).lower()
        left, resets = _model_quota(m)
        model_rows.append(ModelRow(name=name, extra="" if left is None else f"剩余 {left:.0f}%"))
        is_gemini = "gemini" in mid or "gemini" in name.lower()
        if is_gemini:
            if gemini_left is None or (left is not None and left < gemini_left):
                gemini_left = left
                gemini_reset = resets
        else:
            if other_left is None or (left is not None and left < other_left):
                other_left = left
                other_reset = resets
    if gemini_left is not None:
        meta = annotate_window(gemini_left, parse_ts(gemini_reset))
        windows.append(
            Window(
                id="gemini",
                label="Gemini 池",
                kind="session",
                percent_remaining=gemini_left,
                percent_used=round(100 - gemini_left, 2),
                extra="Flash / Pro 共用",
                **meta,
            )
        )
    if other_left is not None:
        meta = annotate_window(other_left, parse_ts(other_reset))
        windows.append(
            Window(
                id="other",
                label="非 Google 模型池",
                kind="session",
                percent_remaining=other_left,
                percent_used=round(100 - other_left, 2),
                extra="Claude / GPT 等",
                **meta,
            )
        )
    p.windows = windows
    p.models = model_rows[:30]
    p.status = "fresh" if windows or model_rows else "unavailable"
    return p


def _run_json(cmd: list[str]) -> Any:
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
    )
    text = (proc.stdout or "").strip()
    start = text.find("{")
    start_arr = text.find("[")
    if start < 0 and start_arr < 0:
        err = (proc.stderr or text or "无输出").strip()
        raise RuntimeError(err.splitlines()[-1][:240] if err else f"exit {proc.returncode}")
    if start >= 0 and (start_arr < 0 or start < start_arr):
        return json.loads(text[start:])
    return json.loads(text[start_arr:])


def _from_cli(p: Provider) -> Provider:
    last = ""
    for args in (
        ["agy-cli-usage", "--json", "--source", "api"],
        ["antigravity-usage", "--json"],
    ):
        try:
            payload = _run_json(_npx() + args)
        except Exception as exc:
            last = str(exc)
            continue
        if isinstance(payload, dict) and payload.get("groups"):
            return _from_agy_snapshot(p, payload)
        result = _from_legacy_models(p, payload)
        if result.windows or result.models:
            result.source = args[0]
            return result
        last = "CLI 已连上但没有额度字段"
    raise RuntimeError(last or "没有可用的 Antigravity CLI")


def collect_antigravity() -> Provider:
    p = Provider(
        id="antigravity",
        label="Google AI Pro · Antigravity",
        official_url=OFFICIAL["antigravity"],
        source="agy",
    )
    errors: list[str] = []
    try:
        return _from_cloudcode(p)
    except Exception as exc:
        errors.append(str(exc))
    try:
        return _from_cli(p)
    except Exception as exc:
        errors.append(str(exc))
    p.status = "auth_required"
    p.error = errors[-1][:240] if errors else "读不到 Antigravity 登录"
    p.remedy = "确认本机已登录 agy / Antigravity IDE（看板只读 Windows 凭据 gemini:antigravity）"
    return p
