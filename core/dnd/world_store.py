"""全局共享世界文档库（多世界）。

入场流程（``.agents/new_plusin_lktrpg.md`` 的 DM Agent 入场流程）生成的世界 / 剧情 /
区域 / 城镇 / 地点 / 地下城 / 场景文档，统一放在这里。库支持**多个世界**：
每个世界有自己独立的文档库与索引，多份存档（:mod:`.save_store`）可以共用
同一个世界；同一个世界里可以有**多个故事**（``story`` 文档）。

落盘形态为「markdown 文件 + JSON 索引 + 世界注册表」：

.. code-block:: text

    data/plugins/跑团/world/
        worlds.json                     # 世界注册表（名称 / 一句话简介 / 主题）
        worlds/<world_key>/
            index.json                  # 该世界的结构化索引（相邻关系、文档标题等）
            docs/<kind>/<key>.md        # 每个文档一个 markdown 文件

``kind`` 取 :data:`DOC_KINDS`；每个世界的主文档（世界地图）键固定为 ``main``，
故事文档键由 :func:`new_story_id` 生成。
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ATRI.exceptions import str_traceback
from ATRI.log import log

__all__ = [
    "DOC_KINDS",
    "DOC_KIND_LABELS",
    "MAIN_DOC_KEY",
    "REGISTRY_FILENAME",
    "WORLDS_DIRNAME",
    "WORLD_DIRNAME",
    "StoryInfo",
    "WorldDoc",
    "WorldInfo",
    "clean_name",
    "delete_doc",
    "doc_path",
    "docs_dir",
    "get_story",
    "get_world",
    "get_world_map",
    "index_path",
    "list_docs",
    "list_stories",
    "list_worlds",
    "load_doc",
    "load_index",
    "new_story_id",
    "new_world_id",
    "read_doc",
    "registry_path",
    "save_doc",
    "set_world_map",
    "story_text",
    "upsert_world",
    "world_dir",
    "world_root",
    "worlds_dir",
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
WORLDS_DIRNAME = "worlds"
REGISTRY_FILENAME = "worlds.json"
#: 每个世界的主文档（世界地图）固定键
MAIN_DOC_KEY = "main"


# --------------------------------------------------------------------------- #
# 路径
# --------------------------------------------------------------------------- #


def world_root() -> Path:
    """世界库根目录（不存在则创建）。"""
    from ... import plugin

    path = plugin.get_path() / WORLD_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def worlds_dir() -> Path:
    """全部世界的总目录（不存在则创建）。"""
    path = world_root() / WORLDS_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


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


def world_dir(world_key: str) -> Path:
    """某个世界的目录（不存在则创建）。"""
    path = worlds_dir() / _safe_key(world_key)
    path.mkdir(parents=True, exist_ok=True)
    return path


def docs_dir(world_key: str) -> Path:
    """某个世界的文档总目录（``worlds/<键>/docs/``）。"""
    path = world_dir(world_key) / "docs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def index_path(world_key: str) -> Path:
    """某个世界的索引文件路径。"""
    return world_dir(world_key) / "index.json"


def registry_path() -> Path:
    """世界注册表路径。"""
    return world_root() / REGISTRY_FILENAME


def doc_path(world_key: str, kind: str, key: str) -> Path:
    """某个世界里一份文档的文件路径。"""
    kind = _check_kind(kind)
    directory = docs_dir(world_key) / kind
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{_safe_key(key)}.md"


# --------------------------------------------------------------------------- #
# 世界注册表：哪个世界存在、叫什么、一句话简介是什么
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class WorldInfo:
    """一个世界的注册信息。"""

    key: str
    title: str = ""
    intro: str = ""
    theme: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "intro": self.intro,
            "theme": self.theme,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorldInfo:
        return cls(
            key=str(data.get("key", "")),
            title=str(data.get("title", "") or ""),
            intro=str(data.get("intro", "") or ""),
            theme=str(data.get("theme", "") or ""),
            created_at=float(data.get("created_at", 0.0) or 0.0),
            updated_at=float(data.get("updated_at", 0.0) or 0.0),
        )


def _empty_registry() -> dict[str, Any]:
    return {"version": 1, "worlds": []}


def _load_registry() -> dict[str, Any]:
    """读取世界注册表；不存在或损坏时返回空注册表。"""
    path = registry_path()
    if not path.is_file():
        return _empty_registry()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning(f"读取世界注册表失败：{path}（{str_traceback(e)}）")
        return _empty_registry()
    if not isinstance(data, dict):
        return _empty_registry()
    worlds = data.get("worlds")
    registry = _empty_registry()
    if isinstance(worlds, list):
        registry["worlds"] = [dict(w) for w in worlds if isinstance(w, dict)]
    return registry


def _write_registry(data: dict[str, Any]) -> None:
    path = registry_path()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def list_worlds() -> list[WorldInfo]:
    """全部世界（按创建顺序；名称 / 简介已剥掉首尾包裹符）。"""
    worlds = [WorldInfo.from_dict(w) for w in _load_registry()["worlds"]]
    for world in worlds:
        world.title = clean_name(world.title)
        world.intro = clean_name(world.intro)
    return worlds


def get_world(world_key: str) -> WorldInfo | None:
    """按键取某个世界；不存在返回 ``None``。"""
    key = _safe_key(world_key)
    for world in list_worlds():
        if world.key == key:
            return world
    return None


def upsert_world(
    world_key: str,
    *,
    title: str = "",
    intro: str = "",
    theme: str = "",
) -> WorldInfo:
    """新建或更新世界注册信息；留空的字段保持原值。"""
    key = _safe_key(world_key)
    data = _load_registry()
    now = time.time()
    found: dict[str, Any] | None = None
    for item in data["worlds"]:
        if str(item.get("key", "")) == key:
            found = item
            break
    if found is None:
        found = WorldInfo(key=key, created_at=now).to_dict()
        data["worlds"].append(found)
    if title:
        found["title"] = clean_name(title)
    if intro:
        found["intro"] = clean_name(intro)
    if theme:
        found["theme"] = str(theme)
    found["updated_at"] = now
    _write_registry(data)
    return WorldInfo.from_dict(found)


def new_world_id() -> str:
    """生成一个不冲突的世界键，形如 ``world_20261019_a1b2``。"""
    stamp = time.strftime("%Y%m%d")
    existing = {w.key for w in list_worlds()}
    for _ in range(20):
        candidate = f"world_{stamp}_{uuid.uuid4().hex[:4]}"
        if candidate not in existing and not (worlds_dir() / candidate).exists():
            return candidate
    return f"world_{stamp}_{uuid.uuid4().hex[:8]}"


# --------------------------------------------------------------------------- #
# 单个世界的索引
# --------------------------------------------------------------------------- #


def _empty_index() -> dict[str, Any]:
    return {"regions": [], "adjacency": {}, "docs": {k: {} for k in DOC_KINDS}}


def load_index(world_key: str) -> dict[str, Any]:
    """读取某个世界的索引；不存在或损坏时返回空索引。"""
    path = index_path(world_key)
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


def _write_index(world_key: str, index: dict[str, Any]) -> None:
    path = index_path(world_key)
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
    world_key: str,
    kind: str,
    key: str,
    content: str,
    *,
    title: str = "",
    meta: dict[str, Any] | None = None,
) -> Path:
    """在某个世界里写入（或覆盖）一份文档，并更新该世界的索引。"""
    kind = _check_kind(kind)
    key = _safe_key(key)
    path = doc_path(world_key, kind, key)
    path.write_text(str(content or ""), encoding="utf-8")

    index = load_index(world_key)
    entry = dict(meta or {})
    entry["title"] = title or key
    entry["updated_at"] = time.time()
    index["docs"][kind][key] = entry
    _write_index(world_key, index)
    return path


def load_doc(world_key: str, kind: str, key: str) -> str | None:
    """读取某个世界的文档正文；不存在返回 ``None``。"""
    path = doc_path(world_key, kind, key)
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError as e:
        log.warning(f"读取世界文档失败：{path}（{e}）")
        return None


def read_doc(world_key: str, kind: str, key: str) -> WorldDoc | None:
    """读取某个世界的文档（含标题与元信息）；不存在返回 ``None``。"""
    content = load_doc(world_key, kind, key)
    if content is None:
        return None
    kind = _check_kind(kind)
    entry = load_index(world_key)["docs"][kind].get(_safe_key(key), {})
    return WorldDoc(
        kind=kind,
        key=_safe_key(key),
        title=str(entry.get("title", "")),
        content=content,
        meta={k: v for k, v in entry.items() if k not in {"title", "updated_at"}},
        updated_at=float(entry.get("updated_at", 0.0) or 0.0),
    )


def delete_doc(world_key: str, kind: str, key: str) -> bool:
    """删除某个世界里的文档并从索引移除；文档不存在返回 ``False``。"""
    kind = _check_kind(kind)
    safe = _safe_key(key)
    path = doc_path(world_key, kind, safe)
    existed = path.is_file()
    if existed:
        path.unlink()
    index = load_index(world_key)
    index["docs"][kind].pop(safe, None)
    _write_index(world_key, index)
    return existed


def list_docs(world_key: str, kind: str | None = None) -> dict[str, list[str]]:
    """列出某个世界的文档名。``kind`` 为空时返回全部种类的列表。"""
    index = load_index(world_key)
    if kind is not None:
        kind = _check_kind(kind)
        return {kind: sorted(index["docs"][kind])}
    return {k: sorted(index["docs"][k]) for k in DOC_KINDS}


# --------------------------------------------------------------------------- #
# 世界内故事：多个 story 文档，键 + 名称 + 一句话简介
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class StoryInfo:
    """世界内一个故事的注册信息（来自该世界索引的 ``story`` 条目）。"""

    key: str
    title: str = ""
    intro: str = ""


def list_stories(world_key: str) -> list[StoryInfo]:
    """某个世界里的全部故事（按创建顺序；标题已剥掉首尾包裹符）。"""
    index = load_index(world_key)
    return [
        StoryInfo(
            key=str(key),
            title=clean_name(str(entry.get("title") or key)),
            intro=clean_name(str(entry.get("intro") or "")),
        )
        for key, entry in index["docs"]["story"].items()
    ]


#: 名称首尾常见的包裹符号（Markdown 强调、书名号、引号等）
_WRAP_CHARS = "*_`《》「」『』“”\"'<>"


def clean_name(value: str) -> str:
    """剥掉名称首尾的包裹符号并去空白；比较与展示前统一用它归一。"""
    text = str(value or "").strip()
    while len(text) >= 2 and text[0] in _WRAP_CHARS and text[-1] in _WRAP_CHARS:
        inner = text[1:-1].strip()
        if not inner:
            break
        text = inner
    return text


def get_story(world_key: str, ref: str) -> StoryInfo | None:
    """按键或标题取某个世界里的故事；找不到返回 ``None``。

    匹配前会剥掉查询词与标题首尾的包裹符号（如 ``**名字**``），因此索引里
    已存的带强调符标题也能用纯文本找到。
    """
    ref = clean_name(ref)
    if not ref:
        return None
    for story in list_stories(world_key):
        if ref in (story.key, story.title):
            return story
    return None


def new_story_id(world_key: str) -> str:
    """在世界里生成一个不冲突的故事键，形如 ``story_20261019_a1b2``。"""
    stamp = time.strftime("%Y%m%d")
    existing = set(load_index(world_key)["docs"]["story"])
    for _ in range(20):
        candidate = f"story_{stamp}_{uuid.uuid4().hex[:4]}"
        if candidate not in existing:
            return candidate
    return f"story_{stamp}_{uuid.uuid4().hex[:8]}"


def story_text(world_key: str, story_key: str = "") -> str:
    """取某个世界的故事正文：先按给定键，再退回 ``main`` / 世界里的第一个故事。

    旧存档只有默认世界里的 ``story/main``；新存档记录着自己的故事键。
    """
    for key in (str(story_key or "").strip(), MAIN_DOC_KEY):
        if key:
            text = load_doc(world_key, "story", key)
            if text:
                return text
    for story in list_stories(world_key):
        text = load_doc(world_key, "story", story.key)
        if text:
            return text
    return ""


# --------------------------------------------------------------------------- #
# 世界地图结构（相邻关系，按世界隔离）
# --------------------------------------------------------------------------- #


def set_world_map(
    world_key: str, regions: list[str], adjacency: dict[str, list[str]]
) -> None:
    """写入某个世界的地区列表与陆地相邻关系（来自世界地图生成 agent）。"""
    index = load_index(world_key)
    index["regions"] = [str(r) for r in regions]
    index["adjacency"] = {
        str(k): [str(v) for v in (vs or [])] for k, vs in (adjacency or {}).items()
    }
    _write_index(world_key, index)


def get_world_map(world_key: str) -> tuple[list[str], dict[str, list[str]]]:
    """读取某个世界的地区列表与相邻关系；未生成时返回空。"""
    index = load_index(world_key)
    return list(index["regions"]), dict(index["adjacency"])
