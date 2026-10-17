"""角色卡存储。

每名玩家一份 JSON 存放在插件私有目录（``data/plugins/跑团/cards/``），
不引入数据库表，便于手工查看与迁移。
"""

from __future__ import annotations

import json
from pathlib import Path

from ATRI.exceptions import str_traceback
from ATRI.log import log

from .card import DndCharacter

__all__ = ["CARDS_DIRNAME", "delete_card", "list_cards", "load_card", "save_card"]

CARDS_DIRNAME = "cards"


def cards_dir() -> Path:
    """取角色卡目录（不存在则创建）。"""
    from ... import plugin

    path = plugin.get_path() / CARDS_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def card_path(user_id: str) -> Path:
    """某玩家的角色卡文件路径。"""
    safe = "".join(ch for ch in str(user_id) if ch.isalnum() or ch in "-_") or "unknown"
    return cards_dir() / f"{safe}.json"


def save_card(character: DndCharacter) -> Path:
    """写入角色卡。"""
    character.touch()
    path = card_path(character.user_id)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(character.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)
    return path


def load_card(user_id: str) -> DndCharacter | None:
    """读取角色卡；不存在或损坏返回 ``None``。"""
    path = card_path(user_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # 损坏文件不应让整条流程崩掉
        log.warning(f"读取角色卡失败：{path}\n{str_traceback(e)}")
        return None
    try:
        return DndCharacter.from_dict(data)
    except Exception as e:
        log.warning(f"解析角色卡失败：{path}\n{str_traceback(e)}")
        return None


def delete_card(user_id: str) -> bool:
    """删除角色卡，返回是否确实删掉了文件。"""
    path = card_path(user_id)
    if path.is_file():
        path.unlink()
        return True
    return False


def list_cards() -> list[str]:
    """已建卡玩家的 user_id 列表。"""
    return [p.stem for p in sorted(cards_dir().glob("*.json"))]
