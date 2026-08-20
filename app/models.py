from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass
class Window:
    id: str
    label: str
    kind: str  # session | weekly | monthly | model | credits
    percent_remaining: Optional[float] = None
    percent_used: Optional[float] = None
    resets_at: Optional[str] = None
    remaining_label: str = ""
    extra: str = ""
    hours_until_reset: Optional[float] = None
    urgency: Optional[float] = None
    use_first: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ModelRow:
    name: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: Optional[float] = None
    extra: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Provider:
    id: str
    label: str
    plan: str = ""
    status: str = "unavailable"  # fresh | auth_required | error | unavailable
    error: str = ""
    remedy: str = ""
    official_url: str = ""
    windows: list[Window] = field(default_factory=list)
    models: list[ModelRow] = field(default_factory=list)
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "plan": self.plan,
            "status": self.status,
            "error": self.error,
            "remedy": self.remedy,
            "officialUrl": self.official_url,
            "windows": [w.to_dict() for w in self.windows],
            "models": [m.to_dict() for m in self.models],
            "source": self.source,
        }


OFFICIAL = {
    "cursor": "https://cursor.com/dashboard/spending",
    "codex": "https://chatgpt.com/codex",
    "grok": "https://grok.com/",
    "antigravity": "https://antigravity.google/docs/cli/commands/usage",
}
