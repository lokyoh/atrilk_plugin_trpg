"""全局共享世界文档库（多世界）。

入场流程（``.agents/new_plusin_lktrpg.md`` 的 DM Agent 入场流程）生成的世界 / 区域 /
城镇 / 地点 / 地下城 / 场景文档，统一放在这里。库支持**多个世界**：
每个世界有自己独立的文档库与索引，多份存档（:mod:`.save_store`）可以共用
同一个世界。

库里的文档分两层：**世界骨架**（world / area / city / place / dungeon / scene）
在任何存档之间共享，换一局、换一条路线都不变；**章节卡**（``chapter``）是剧本层，
织出来就落在这个世界里，所以织过的章可以复玩。

落盘形态为「markdown 文件 + JSON 索引 + 世界注册表」：

.. code-block:: text

    data/plugins/跑团/world/
        worlds.json                     # 世界注册表（名称 / 一句话简介 / 主题）
        worlds/<world_key>/
            index.json                  # 该世界的结构化索引（相邻关系、文档标题等）
            docs/<kind>/<key>.md        # 每个文档一个 markdown 文件

``kind`` 取 :data:`DOC_KINDS`；每个世界的主文档（世界地图）键固定为 ``main``，
章节卡键由 :func:`new_chapter_id` 生成（并复用卡自身的 id，所以重复保存幂等）。
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
    "KIND_LEVEL",
    "LEVEL_KINDS",
    "MAIN_DOC_KEY",
    "REGISTRY_FILENAME",
    "SITE_KINDS",
    "WORLDS_DIRNAME",
    "WORLD_DIRNAME",
    "ChapterRefInfo",
    "WorldDoc",
    "WorldInfo",
    "add_provinces",
    "clean_name",
    "delete_doc",
    "doc_path",
    "docs_dir",
    "find_at_level",
    "get_provinces",
    "get_world",
    "get_world_map",
    "index_path",
    "kinds_at",
    "level_of",
    "list_chapters",
    "list_docs",
    "list_worlds",
    "load_doc",
    "load_index",
    "names_at_level",
    "new_chapter_id",
    "new_world_id",
    "province_index",
    "read_at_level",
    "read_doc",
    "registry_path",
    "save_doc",
    "set_provinces",
    "set_world_map",
    "upsert_world",
    "world_dir",
    "world_root",
    "worlds_dir",
]

#: 文档种类。世界是五层：**世界 → 王国/地区 → 区域（省）→ 地点 → 场景**。
#:
#: 第 4 层刻意**保持三个种类**而不是合成一个 ``site``：据点（有人住、有买卖、
#: 有管事的人）、野外地点（地形与遭遇）、地下城（多房间的探索空间）三者的生成模板与
#: 下游用法本来就不一样，合成一个再靠子类型分派，只是把区别藏进 meta 里。
#:
#: ``chapter`` 不属于地理层级，它是挂在存档上的剧本卡。
DOC_KINDS: tuple[str, ...] = (
    "world",
    "realm",
    "province",
    "city",
    "place",
    "dungeon",
    "scene",
    "chapter",
)

#: 文档种类 -> 中文名
DOC_KIND_LABELS: dict[str, str] = {
    "world": "世界",
    "realm": "王国/地区",
    "province": "区域",
    "city": "据点",
    "place": "野外地点",
    "dungeon": "地下城",
    "scene": "场景",
    "chapter": "章节",
}

#: 第 4 层的三个种类（地点）
SITE_KINDS: tuple[str, ...] = ("city", "place", "dungeon")

#: 文档种类 -> 地理层级（世界 = 1）。``chapter`` 不在地理层级里，记为 0。
KIND_LEVEL: dict[str, int] = {
    "world": 1,
    "realm": 2,
    "province": 3,
    "city": 4,
    "place": 4,
    "dungeon": 4,
    "scene": 5,
    "chapter": 0,
}

#: 层级 -> 该层全部种类
LEVEL_KINDS: dict[int, tuple[str, ...]] = {
    1: ("world",),
    2: ("realm",),
    3: ("province",),
    4: SITE_KINDS,
    5: ("scene",),
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
    return {
        "regions": [],
        "adjacency": {},
        "provinces": {},
        "docs": {k: {} for k in DOC_KINDS},
    }


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
        provinces = data.get("provinces")
        if isinstance(provinces, dict):
            base["provinces"] = {
                str(k): [str(v) for v in (vs or [])] for k, vs in provinces.items()
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
# 层级：世界 → 王国/地区 → 区域 → 地点 → 场景
# --------------------------------------------------------------------------- #


def level_of(kind: str) -> int:
    """某个文档种类属第几层（世界 = 1；``chapter`` 不在地理层级里，返回 0）。"""
    return KIND_LEVEL.get(str(kind or "").strip().lower(), 0)


def kinds_at(level: int) -> tuple[str, ...]:
    """该层的全部种类，**新种类在前**（读的时候按这个顺序试）。"""
    return LEVEL_KINDS.get(int(level), ())


def find_at_level(
    world_key: str, level: int, name: str
) -> tuple[str, dict[str, Any]] | None:
    """在某一层按名字找文档，返回 ``(种类, meta)``；找不到返回 ``None``。

    第 4 层有三个种类（据点 / 野外地点 / 地下城），只凭名字不知道是哪一个，
    所以这一层要**逐个种类试**；其余层各只有一个种类。
    只查索引，不读正文；要正文用 :func:`read_at_level`。
    """
    key = _safe_key(name)
    index = load_index(world_key)
    for kind in kinds_at(level):
        entry = index["docs"].get(kind, {}).get(key)
        if entry is not None:
            return kind, dict(entry)
    return None


def read_at_level(world_key: str, level: int, name: str) -> WorldDoc | None:
    """在某一层按名字读文档（含正文）；找不到返回 ``None``。"""
    found = find_at_level(world_key, level, name)
    if found is None:
        return None
    kind, _meta = found
    return read_doc(world_key, kind, name)


def names_at_level(world_key: str, level: int) -> list[str]:
    """该层全部文档名（第 4 层三个种类合并、去重）。"""
    index = load_index(world_key)
    seen: list[str] = []
    for kind in kinds_at(level):
        for name in sorted(index["docs"].get(kind, {})):
            if name not in seen:
                seen.append(name)
    return seen


def set_provinces(world_key: str, provinces: dict[str, list[str]]) -> None:
    """写入「王国/地区 → 区域（省）」的归属表。

    与 :func:`set_world_map` 分开，因为两者回答不同的问题：

    - ``regions`` 是**政治单位**（谁说了算），不是旅行单位；
    - ``provinces`` 是**旅行单位**——**相邻两个省之间约一天**，同一个省里
      各处地点之间**几小时**（见 :func:`~...dnd.travel.travel_span`）。

    所以 ``adjacency`` 记的是**省与省**的相邻关系，不是王国与王国。王国之间的关系
    属于政治（写在 realm 文档的「外交」一节），不该混进旅行动词。
    """
    index = load_index(world_key)
    index["provinces"] = {
        str(k): [str(v) for v in (vs or [])] for k, vs in (provinces or {}).items()
    }
    _write_index(world_key, index)


def get_provinces(world_key: str) -> dict[str, list[str]]:
    """读取「王国/地区 → 区域」归属表；还没分过省的世界返回空表。

    空表不是错误：`ensure_realm` 落一篇王国文档时会把它的区域划分写进来，
    在那之前这个表就是空的。
    """
    index = load_index(world_key)
    return {str(k): list(v) for k, v in (index.get("provinces") or {}).items()}


def province_index(world_key: str) -> dict[str, str]:
    """「地名 → 它属于哪个省」的映射，供旅行时间换算用。

    三种来源合成：

    - 省自己映射到自己（``盐仓区 → 盐仓区``）；
    - 地点文档的 ``meta.parent``（``盐仓码头 → 盐仓区``）；
    - 场景文档的 ``meta.site``（``盐仓码头夜景 → 盐仓码头``，再由上一条接上去）。

    **王国不在表里**：它不是旅行单位，从王国出发得先说清去哪个省。
    取不到的（比如没归到省的地点）就不在表里——那样旅行时间会算成「不知道」，
    而不是给出一个凭空猜的数字。
    """
    places: dict[str, str] = {}
    for names in get_provinces(world_key).values():
        for province in names or []:
            places.setdefault(str(province), str(province))
    index = load_index(world_key)
    for kind in SITE_KINDS:
        for name, meta in (index["docs"].get(kind) or {}).items():
            parent = str((meta or {}).get("parent") or "")
            if parent:
                places[str(name)] = parent
    for name, meta in (index["docs"].get("scene") or {}).items():
        site = str((meta or {}).get("site") or "")
        if site and site in places:
            places[str(name)] = places[site]
    return places


def add_provinces(world_key: str, realm: str, names: list[str]) -> None:
    """给某个王国补上若干个区域名（去重保序，可重复调用）。"""
    realm = clean_name(str(realm or ""))
    if not realm:
        return
    table = get_provinces(world_key)
    current = table.get(realm, [])
    for name in names or []:
        text = clean_name(str(name or ""))
        if text and text not in current:
            current.append(text)
    table[realm] = current
    set_provinces(world_key, table)


# --------------------------------------------------------------------------- #
# 名称归一
# --------------------------------------------------------------------------- #

#: 名称首尾常见的包裹符号（Markdown 强调、书名号、引号等）
_WRAP_CHARS = "*_`《》「」『』“”\"'<>"


def clean_name(value: str) -> str:
    """剥掉名称首尾的包裹符号并去空白；比较与展示前统一用它归一。

    索引里存下的标题常带``**`` 之类的强调符（模型生成时很容易带），
    玩家按纯文本查世界时也得能查到，所以查找与展示前都先归一。
    """
    text = str(value or "").strip()
    while len(text) >= 2 and text[0] in _WRAP_CHARS and text[-1] in _WRAP_CHARS:
        inner = text[1:-1].strip()
        if not inner:
            break
        text = inner
    return text


# --------------------------------------------------------------------------- #
# 章节卡（剧本层，织出来就落在世界里）
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class ChapterRefInfo:
    """世界内一张章节卡的注册信息（来自该世界索引的 ``chapter`` 条目）。"""

    key: str
    title: str = ""
    arc: str = ""
    #: 织出这张卡的难度档与章节序号（写进索引元信息，方便按难度挑卡）
    chapter_no: int = 0
    target_cr: str = ""
    updated_at: float = 0.0

    @property
    def arc_label(self) -> str:
        from .chapter import ARC_LABELS

        return ARC_LABELS.get(self.arc, self.arc)


def list_chapters(world_key: str) -> list[ChapterRefInfo]:
    """某个世界里的全部章节卡（按更新时间倒序；最新织的在前）。"""
    index = load_index(world_key)
    items: list[ChapterRefInfo] = []
    for key, entry in index["docs"]["chapter"].items():
        items.append(
            ChapterRefInfo(
                key=str(key),
                title=clean_name(str(entry.get("title") or key)),
                arc=str(entry.get("arc") or ""),
                chapter_no=int(entry.get("chapter_no") or 0),
                target_cr=str(entry.get("target_cr") or ""),
                updated_at=float(entry.get("updated_at") or 0.0),
            )
        )
    items.sort(key=lambda c: c.updated_at, reverse=True)
    return items


def new_chapter_id(world_key: str, prefix: str = "ch") -> str:
    """在世界里生成一个不冲突的章节键，形如 ``ch1_20261021_a1b2``。

    带章节序号前缀是为了让人一眼看出这是第几章的卡，也方便按序排。
    """
    stamp = time.strftime("%Ym%d")
    tag = _safe_key(prefix) or "ch"
    existing = set(load_index(world_key)["docs"]["chapter"])
    for _ in range(20):
        candidate = f"{tag}_{stamp}_{uuid.uuid4().hex[:4]}"
        if candidate not in existing:
            return candidate
    return f"{tag}_{stamp}_{uuid.uuid4().hex[:8]}"


# --------------------------------------------------------------------------- #
# 世界地图结构（相邻关系，按世界隔离）
# --------------------------------------------------------------------------- #


def set_world_map(
    world_key: str, regions: list[str], adjacency: dict[str, list[str]]
) -> None:
    """写入某个世界的**王国**列表与**省与省**的相邻关系。

    ``regions`` 是王国名（政治单位）；``adjacency`` 的键与值都是**省名**——
    相邻即「走一天」。两条相邻的省可以同属一个王国，也可以分属两国。
    """
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
