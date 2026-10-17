"""建卡会话存储。

NoneBot 的 ``reject`` 循环依赖内存中的临时响应器，静置超过
``BotConfig.session_expire_timeout``（默认 60 秒）就会销毁。建卡流程却常常
跨分钟甚至跨天，所以会话状态额外落盘到插件私有目录：

``data/plugins/跑团/sessions/<user_id>.json``

落盘数据只用于「玩家再次发出指令时接着走」，过期清理也由本模块负责，
不依赖 ``Matcher.state``。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from ATRI.exceptions import str_traceback
from ATRI.log import log

from .creation import CreationSession

__all__ = [
    "SESSIONS_DIRNAME",
    "SESSION_TTL",
    "delete_session",
    "load_session",
    "purge_expired",
    "save_session",
    "session_path",
    "sessions_dir",
]

#: 会话目录名
SESSIONS_DIRNAME = "sessions"

#: 默认保留时长（秒）：一天未继续则视为放弃建卡
SESSION_TTL = 60 * 60 * 24


def sessions_dir() -> Path:
    """取会话目录（不存在则创建）。"""
    from ... import plugin

    path = plugin.get_path() / SESSIONS_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def session_path(user_id: str) -> Path:
    """某玩家的建卡会话文件路径。"""
    safe = "".join(ch for ch in str(user_id) if ch.isalnum() or ch in "-_") or "unknown"
    return sessions_dir() / f"{safe}.json"


def save_session(session: CreationSession) -> Path:
    """写入建卡会话（原子替换，避免写一半被读到）。"""
    path = session_path(session.user_id)
    data = session.to_dict()
    data["_saved_at"] = time.time()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def load_session(user_id: str, *, ttl: float = SESSION_TTL) -> CreationSession | None:
    """读取建卡会话；不存在、过期或损坏返回 ``None``。

    过期文件顺手删掉，避免目录无限增长。
    """
    path = session_path(user_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # 损坏文件不应让整条流程崩掉
        log.warning(f"读取建卡会话失败：{path}\n{str_traceback(e)}")
        return None
    if ttl > 0 and time.time() - float(data.get("_saved_at") or 0) > ttl:
        delete_session(user_id)
        return None
    try:
        return CreationSession.from_dict(data)
    except Exception as e:
        log.warning(f"解析建卡会话失败：{path}\n{str_traceback(e)}")
        return None


def delete_session(user_id: str) -> bool:
    """删除建卡会话，返回是否确实删掉了文件。"""
    path = session_path(user_id)
    if path.is_file():
        path.unlink()
        return True
    return False


def list_sessions() -> list[str]:
    """已存在建卡会话的 user_id 列表（不含过期判断）。"""
    return [path.stem for path in sessions_dir().glob("*.json")]


def purge_expired(*, ttl: float = SESSION_TTL) -> int:
    """清理全部过期会话，返回清理数量。"""
    if ttl <= 0:
        return 0
    deadline = time.time() - ttl
    removed = 0
    for path in sessions_dir().glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            saved_at = float(data.get("_saved_at") or 0)
        except Exception:
            saved_at = 0.0
        if saved_at < deadline:
            try:
                path.unlink()
                removed += 1
            except OSError as e:
                log.warning(f"清理建卡会话失败：{path}\n{str_traceback(e)}")
    return removed
