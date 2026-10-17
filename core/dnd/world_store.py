"""全局共享世界文档库。

入场流程（``.agents/new_plusin_lktrpg.md`` 的 DM Agent 入场流程）生成的世界 / 剧情 /
区域 / 城镇 / 地点 / 地下城 / 场景文档，统一放在这里。库是**全局共享**的：多份存档
（:mod:`.save_store`）可以共用同一张世界地图。

落盘形态为「markdown 文件 + JSON 索引」：

.. code-block:: text

    data/plugins/跑团/world/
        index.json                 # 结构化索引（相邻关系、文档标题等）
        docs/<kind>/<key>.md       # 每个文档一个 markdown 文件

``kind`` 取 :data:`DOC_KINDS`；``key`` 是文档名（如地区名「北境」）。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ATRI.exceptions import str_traceback
from ATRI.log import log

__all__ = [
    "DOC_KINDS",
    "DOC_KIND_LABELS",
    "WORLD_DIRNAME",
    "WorldDoc",
    "delete_doc",
    "doc_path",
    "docs_dir",
    "get_world_map",
    "index_path",
    "list_docs",
    "load_doc",
    "load_index",
    "read_doc",
    "save_doc",
    "set_world_map",
    "world_dir",
]

#: 文档种类
DOC_KINDS: tuple[str, ...] = (
    "world",
    "story",
    "area",
    "city",
    "place",
    "dungeon",
    "scene",
)

#: 文档种类 -> 中文名
DOC_KIND_LABELS: dict[str, str] = {
    "world": "世界",
    "story": "剧情",
    "area": "区域",
    "city": "城镇",
    "place": "地点",
    "dungeon": "地下城",
    "scene": "场景",
}

WORLD_DIRNAME = "world"


# --------------------------------------------------------------------------- #
# 路径
# --------------------------------------------------------------------------- #


def world_dir() -> Path:
    """世界库根目录（不存在则创建）。"""
    from ... import plugin

    path = plugin.get_path() / WORLD_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def docs_dir() -> Path:
    """文档总目录（``world/docs/``）。"""
    path = world_dir() / "docs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def index_path() -> Path:
    """索引文件路径。"""
    return world_dir() / "index.json"


def _safe_key(key: str) -> str:
    """把文档名清洗成安全的文件名，保留中文与字母数字。"""
    text = str(key).strip()
    cleaned = "".join(ch if (ch.isalnum() or ch in "-_") else "_" for ch in text)
    cleaned = cleaned.strip("_")
    return cleaned or "untitled"


def _check_kind(kind: str) -> str:
    kind = str(kind).strip().lower()
    if kind not in DOC_KINDS:
        raise ValueError(f"未知文档种类：{kind}（可用：{'、'.join(DOC_KINDS)}）")
    return kind


def doc_path(kind: str, key: str) -> Path:
    """某文档的文件路径。"""
    kind = _check_kind(kind)
    directory = docs_dir() / kind
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{_safe_key(key)}.md"


# --------------------------------------------------------------------------- #
# 索引
# --------------------------------------------------------------------------- #


def _empty_index() -> dict[str, Any]:
    return {"regions": [], "adjacency": {}, "docs": {k: {} for k in DOC_KINDS}}


def load_index() -> dict[str, Any]:
    """读取索引；不存在或损坏时返回空索引。"""
    path = index_path()
    if not path.is_file():
        return _empty_index()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning(f"读取世界库索引失败：{path}（{str_traceback(e)}）")
        return _empty_index()

    base = _empty_index()
    if isinstance(data, dict):
        base["regions"] = list(data.get("regions") or [])
        adjacency = data.get("adjacency")
        if isinstance(adjacency, dict):
            base["adjacency"] = {
                str(k): [str(v) for v in (vs or [])] for k, vs in adjacency.items()
            }
        docs = data.get("docs")
        if isinstance(docs, dict):
            for kind in DOC_KINDS:
                entries = docs.get(kind)
                if isinstance(entries, dict):
                    base["docs"][kind] = {
                        str(k): dict(v) if isinstance(v, dict) else {}
                        for k, v in entries.items()
                    }
    return base


def _write_index(index: dict[str, Any]) -> None:
    path = index_path()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


# --------------------------------------------------------------------------- #
# 文档
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class WorldDoc:
    """一份世界文档。"""

    kind: str
    key: str
    title: str = ""
    content: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    updated_at: float = 0.0


def save_doc(
    kind: str,
    key: str,
    content: str,
    *,
    title: str = "",
    meta: dict[str, Any] | None = None,
) -> Path:
    """写入（或覆盖）一份文档，并更新索引。"""
    kind = _check_kind(kind)
    key = _safe_key(key)
    path = doc_path(kind, key)
    path.write_text(str(content or ""), encoding="utf-8")

    index = load_index()
    entry = dict(meta or {})
    entry["title"] = title or key
    entry["updated_at"] = time.time()
    index["docs"][kind][key] = entry
    _write_index(index)
    return path


def load_doc(kind: str, key: str) -> str | None:
    """读取文档正文；不存在返回 ``None``。"""
    path = doc_path(kind, key)
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError as e:
        log.warning(f"读取世界文档失败：{path}（{e}）")
        return None


def read_doc(kind: str, key: str) -> WorldDoc | None:
    """读取文档（含标题与元信息）；不存在返回 ``None``。"""
    content = load_doc(kind, key)
    if content is None:
        return None
    kind = _check_kind(kind)
    entry = load_index()["docs"][kind].get(_safe_key(key), {})
    return WorldDoc(
        kind=kind,
        key=_safe_key(key),
        title=str(entry.get("title", "")),
        content=content,
        meta={k: v for k, v in entry.items() if k not in {"title", "updated_at"}},
        updated_at=float(entry.get("updated_at", 0.0) or 0.0),
    )


def delete_doc(kind: str, key: str) -> bool:
    """删除文档并从索引移除；文档不存在返回 ``False``。"""
    kind = _check_kind(kind)
    safe = _safe_key(key)
    path = doc_path(kind, safe)
    existed = path.is_file()
    if existed:
        path.unlink()
    index = load_index()
    index["docs"][kind].pop(safe, None)
    _write_index(index)
    return existed


def list_docs(kind: str | None = None) -> dict[str, list[str]]:
    """列出文档名。``kind`` 为空时返回全部种类的列表。"""
    index = load_index()
    if kind is not None:
        kind = _check_kind(kind)
        return {kind: sorted(index["docs"][kind])}
    return {k: sorted(index["docs"][k]) for k in DOC_KINDS}


# --------------------------------------------------------------------------- #
# 世界地图结构（相邻关系）
# --------------------------------------------------------------------------- #


def set_world_map(regions: list[str], adjacency: dict[str, list[str]]) -> None:
    """写入地区列表与陆地相邻关系（来自世界地图生成 agent）。"""
    index = load_index()
    index["regions"] = [str(r) for r in regions]
    index["adjacency"] = {
        str(k): [str(v) for v in (vs or [])] for k, vs in (adjacency or {}).items()
    }
    _write_index(index)


def get_world_map() -> tuple[list[str], dict[str, list[str]]]:
    """读取地区列表与相邻关系；未生成时返回空。"""
    index = load_index()
    return list(index["regions"]), dict(index["adjacency"])
