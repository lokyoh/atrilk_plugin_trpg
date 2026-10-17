"""通用的场景与事件记录。

规则体系无关：世界 / 国家 / 地点 / 场景都只是一份带事件流的数据对象，
DND 侧在此之上补充专属字段。
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

__all__ = ["SceneEvent", "TrpgScene"]


@dataclass
class SceneEvent:
    """场景内发生的一件事（玩家行动、规则判定、AI 描述……）。"""

    kind: str
    content: str
    actor: str = ""
    ts: float = field(default_factory=time.time)

    def line(self) -> str:
        who = f"{self.actor}：" if self.actor else ""
        return f"[{self.kind}] {who}{self.content}"


@dataclass
class TrpgScene:
    """一个场景（回合单位的容器）。"""

    name: str
    place: str = ""
    summary: str = ""
    events: list[SceneEvent] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)

    def add_event(self, kind: str, content: str, actor: str = "") -> SceneEvent:
        event = SceneEvent(kind=kind, content=content, actor=actor)
        self.events.append(event)
        return event

    def recent(self, limit: int = 10) -> list[SceneEvent]:
        """最近的 ``limit`` 条事件（按时间正序）。"""
        if limit <= 0:
            return []
        return self.events[-limit:]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
