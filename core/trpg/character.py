"""TRPG 角色基类。

只放所有跑团玩法共用的字段与行为；具体规则体系（DND）在自己的角色卡类里继承本类，
补充专属字段，并在需要时覆盖 :meth:`to_dict` / :meth:`from_dict` 以处理枚举与嵌套对象。
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field, fields
from typing import Any

__all__ = ["TrpgCharacter"]


@dataclass
class TrpgCharacter:
    """角色基类。"""

    user_id: str
    name: str = ""
    system: str = ""
    level: int = 1
    notes: str = ""
    tags: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        self.user_id = str(self.user_id)

    def touch(self) -> None:
        """刷新更新时间。"""
        self.updated_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        """导出为可 JSON 序列化的字典。"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]):
        """从字典恢复；忽略未知字段，缺失字段走默认值。"""
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def brief(self) -> str:
        """一行摘要，用于列表展示。"""
        parts = [self.name or f"<{self.user_id}>"]
        if self.system:
            parts.append(f"[{self.system}]")
        parts.append(f"Lv.{self.level}")
        return " ".join(parts)
