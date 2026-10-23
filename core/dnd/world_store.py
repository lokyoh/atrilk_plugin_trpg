"""全局共享世界文档库（多世界）。

入场流程（``.agents/new_plusin_lktrpg.md`` 的 DM Agent 入场流程）生成的世界 / 区域 /
城镇 / 地点 / 地下城 / 场景文档，统一放在这里。库支持**多个世界**：
每个世界有自己独立的文档库与索引，多份存档（:mod:`.save_store`）可以共用
同一个世界。

库里的文档分两层：**世界骨架**（world / area / city / place / dungeon / scene）
在任何存档之间共享，换一局、换一条路线都不变；**事件卡**（``event``）是剧本层，
织出来就落在这个世界里，所以织过的章可以复玩。

落盘形态为「markdown 文件 + JSON 索引 + 世界注册表」：

.. code-block:: text

    data/plugins/跑团/world/
        worlds.json                     # 世界注册表（名称 / 一句话简介 / 主题）
        worlds/<world_key>/
            index.json                  # 该世界的结构化索引（相邻关系、文档标题等）
            docs/<kind>/<key>.md        # 每个文档一个 markdown 文件

``kind`` 取 :data:`DOC_KINDS`；每个世界的主文档（世界地图）键固定为 ``main``，
事件卡键由 :func:`new_event_id` 生成（并复用卡自身的 id，所以重复保存幂等）。
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
    "KIND_DIR",
    "KIND_LEVEL",
    "LEVEL_KINDS",
    "MAIN_DOC_KEY",
    "REGISTRY_FILENAME",
    "SITE_KINDS",
    "WORLDS_DIRNAME",
    "WORLD_DIRNAME",
    "EventRefInfo",
    "WorldDoc",
    "WorldInfo",
    "add_provinces",
    "clean_name",
    "delete_doc",
    "doc_owner",
    "doc_path",
    "docs_dir",
    "find_at_level",
    "find_doc_path",
    "get_provinces",
    "get_world",
    "get_world_map",
    "index_path",
    "kind_dir",
    "kinds_at",
    "level_of",
    "list_docs",
    "list_events",
    "list_worlds",
    "load_doc",
    "load_index",
    "names_at_level",
    "new_event_id",
    "new_world_id",
    "province_crossing",
    "province_index",
    "read_at_level",
    "read_doc",
    "realm_inhabited",
    "registry_path",
    "save_doc",
    "scene_site_index",
    "set_provinces",
    "set_world_map",
    "upsert_world",
    "world_dir",
    "world_root",
    "worlds_dir",
]

#: 地理层的常量住在 :mod:`.layers`（零依赖，独立脚本也能 import）。
#:
#: - 逻辑种类：第 4 层**保持三个**（据点 / 野外地点 / 地下城）——
#:   三者的生成模板与下游用法本来就不一样，合成一个再靠子类型分派，
#:   只是把区别藏进 meta 里。
#: - 物理目录：三种地点共用 ``place/``——它们是同一层的东西，
#:   放在三个平行的目录里只是把分类重复了一遍。
#:
#: 这里保留同名转发，别的模块照旧 ``from .world_store import DOC_KINDS``。
from .layers import (
    DOC_KIND_LABELS,
    DOC_KINDS,
    KIND_DIR,
    KIND_LEVEL,
    LEVEL_KINDS,
    SITE_KINDS,
)
from .layers import (
    doc_owner as _doc_owner,
)
from .layers import (
    kind_dir as _kind_dir,
)

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


def kind_dir(kind: str) -> str:
    """某个逻辑种类落在哪个**物理目录**下。

    第 4 层的三个种类（据点 / 野外地点 / 地下城）共用 ``place/``——
    它们是同一层的东西，分类的区分在正文与 meta 里，不在目录名上。
    """
    return _kind_dir(_check_kind(kind))


def doc_owner(kind: str, meta: dict[str, Any] | None) -> str:
    """一份文档该放在哪个**归属目录**下（空 = 该种类的根目录）。

    落盘结构 ``docs/<物理目录>/<归属>/<文档名>.md``，与预设样本一致：
    省挂在王国下面、地点挂在省下面、场景与事件挂在地点下面。

    规则只认 ``meta`` 里的 ``parent``（上级）与 ``site``（所属地点）——
    那是索引里本来就有的字段，不额外引入新概念。
    """
    return _doc_owner(_check_kind(kind), meta)


def doc_path(world_key: str, kind: str, key: str, owner: str = "") -> Path:
    """某个世界里一份文档的文件路径。

    :param owner: 归属目录名（按归属分目录时用）。**空 = 平铺在该种类的目录下。**

    落盘结构与预设样本一致：``docs/<物理目录>/<归属>/<文档名>.md``。
    读的时候不靠目录找文件——索引里存的是平铺的键。
    """
    directory = docs_dir(world_key) / kind_dir(kind)
    if owner:
        directory = directory / _safe_key(owner)
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{_safe_key(key)}.md"


def find_doc_path(world_key: str, kind: str, key: str) -> Path | None:
    """找一份文档的实际文件（**按归属找，找不到就全目录扫**）。

    归属会变：地点改名、省调整之后，文件可能还留在旧目录里。
    读的时候先按当前归属算路径，落在那儿就用；不在就扫一遍——
    免得「文件在但读不到」这种���态把文档变成看不见的死档。
    """
    kind = _check_kind(kind)
    safe = _safe_key(key)
    index = load_index(world_key)
    entry = index["docs"][kind].get(safe) or {}
    owner = doc_owner(kind, entry)
    direct = doc_path(world_key, kind, safe, owner)
    if direct.is_file():
        return direct
    base = docs_dir(world_key) / kind_dir(kind)
    if base.is_dir():
        for path in base.rglob(f"{safe}.md"):
            return path
    return None


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
    #: 装的是哪**一版**预设世界（``presets/<name>/index.json`` 的 ``version``）。
    #: 非预设世界为空串。有了它，改过预设之后才知道「已经装的那份是旧的」。
    preset_version: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "intro": self.intro,
            "theme": self.theme,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "preset_version": self.preset_version,
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
            preset_version=str(data.get("preset_version", "") or ""),
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
    preset_version: str = "",
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
    if preset_version:
        found["preset_version"] = str(preset_version)
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
    """在某个世界里写入（或覆盖）一份文档，并更新该世界的索引。

    **按归属落盘**（``docs/<物理目录>/<归属>/<文档名>.md``），
    与预设样本的结构一致——生成器写出来的世界和手工整理的样本长得一样。
    """
    kind = _check_kind(kind)
    key = _safe_key(key)
    entry = dict(meta or {})
    path = doc_path(world_key, kind, key, doc_owner(kind, entry))
    path.write_text(str(content or ""), encoding="utf-8")

    index = load_index(world_key)
    entry["title"] = title or key
    entry["updated_at"] = time.time()
    index["docs"][kind][key] = entry
    _write_index(world_key, index)
    return path


def load_doc(world_key: str, kind: str, key: str) -> str | None:
    """读取某个世界的文档正文；不存在返回 ``None``。"""
    path = find_doc_path(world_key, kind, key)
    if path is None or not path.is_file():
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
    path = find_doc_path(world_key, kind, safe)
    existed = path is not None and path.is_file()
    if existed and path is not None:
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
    """某个文档种类属第几层（世界 = 1；``event`` 不在地理层级里，返回 0）。"""
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


def scene_site_index(world_key: str) -> dict[str, str]:
    """「场景 → 它属于哪个场所」的映射（**场景组**）。

    一个 ``place``（第 4 层）可以挂着好几个 ``scene``（第 5 层）——
    官方模组里一艘船有主甲板、船长室、储藏室，**它们是同一个场所里的房间**。

    判据是场景文档的 ``meta.group``：修道院 / 洞穴 / 船骸 / 天文台这种
    「岛上的分组」不是独立的一层，它们只作为 ``group`` 出现，
    而 ``meta.site`` 仍然指向真正的地点文档名（风骸岛）。
    没写 ``group`` 的老场景回落到 ``meta.site``——那时地点与场景同名，
    落在同一个地点里的场景自然算一组。

    **为什么需要它**：移动系统要分清两种走法——

    - **组内移动**（主甲板 → 船长室）：在同一处地方里挪个位置。
      不该扣世界时间（官方是几分钟），也不该判一次「路上有没有事」；
    - **跨组移动**（船上 → 修道院）：换地方了，该按跨越难度算时间、
      该每跳判一次路上有没有事。

    没有这个区分，玩家在船舱里走一圈会被算成三十二小时 + 八次遭遇判定。

    取不到 ``meta.site`` 的场景不在表里——那时它没有组，
    移动按普通场景处理（不特殊照顾，也不特殊对待）。
    """
    index = load_index(world_key)
    sites: dict[str, str] = {}
    for name, meta in (index["docs"].get("scene") or {}).items():
        site = str((meta or {}).get("group") or (meta or {}).get("site") or "")
        if site:
            sites[str(name)] = site
    return sites


def province_index(world_key: str) -> dict[str, str]:
    """「地名 → 它属于哪个省」的映射，供旅行时间换算用。

    三种来源合成：

    - 省自己映射到自己（``盐仓区 → 盐仓区``）；
    - 地点文档的 ``meta.parent``（``盐仓码头 → 盐仓区``）；
    - 场景文档的 ``meta.province``（直接挂省），或 ``meta.site``
      （指向所属地点，再由上一条接上去）。

    两种场景写法都在用：老世界的场景只写 ``meta.site``；一个场所挂多个
    房间场景时（风骸岛有 26 个），场所本身可能只是**分组标签**而不是文档，
    这时场景直接写 ``meta.province``——否则那一串房间都定位不到省。

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
        province = str((meta or {}).get("province") or "")
        if province in places:
            places[str(name)] = province
            continue
        site = str((meta or {}).get("site") or "")
        if site and site in places:
            places[str(name)] = places[site]
    return places


def province_crossing(world_key: str) -> dict[str, int]:
    """「省 → 跨越难度 1–5」映射，供旅行时间换算用。

    取的是区域文档的 ``meta.crossing``：``gen_province`` 生成区域时一并写好。
    **为什么让模型给而不是程序推断**——地形到难度的映射不唯一，「裂谷」可以是
    3 也可以是 5，取决于有没有栈道、有没有人维护，这需要常识。

    **没有这个字段的旧世界，那一项直接缺席**，:func:`~...dnd.travel.crossing_level`
    按「好走」兜底——于是旧世界的行为与加权之前完全一致，不需要迁移。
    """
    index = load_index(world_key)
    out: dict[str, int] = {}
    for name, meta in (index["docs"].get("province") or {}).items():
        try:
            out[str(name)] = int((meta or {}).get("crossing"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
    return out


def realm_inhabited(world_key: str) -> dict[str, bool]:
    """「王国 → 这里有没有人管事」。没标过的一律按 ``True``。

    无人区不是「穷」，是**没有建制**：没有政体、没有驿站、没有常驻人口。
    它对下游有三条连锁：区域里不产据点、跨越时没补给、**不产流言**（荒野里没有目击者）。
    """
    index = load_index(world_key)
    out: dict[str, bool] = {}
    for name, meta in (index["docs"].get("realm") or {}).items():
        out[str(name)] = bool((meta or {}).get("inhabited", True))
    return out


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
# 事件卡（剧本层，织出来就落在世界里）
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class EventRefInfo:
    """世界内一张事件卡的注册信息（来自该世界索引的 ``event`` 条目）。"""

    key: str
    title: str = ""
    arc: str = ""
    #: 织出这张卡的难度档与事件序号（写进索引元信息，方便按难度挑卡）
    event_no: int = 0
    target_cr: str = ""
    updated_at: float = 0.0

    @property
    def arc_label(self) -> str:
        from .event_progress import ARC_LABELS

        return ARC_LABELS.get(self.arc, self.arc)


def list_events(world_key: str) -> list[EventRefInfo]:
    """某个世界里的全部事件卡（按更新时间倒序；最新织的在前）。"""
    index = load_index(world_key)
    items: list[EventRefInfo] = []
    for key, entry in index["docs"]["event"].items():
        items.append(
            EventRefInfo(
                key=str(key),
                title=clean_name(str(entry.get("title") or key)),
                arc=str(entry.get("arc") or ""),
                event_no=int(entry.get("event_no") or 0),
                target_cr=str(entry.get("target_cr") or ""),
                updated_at=float(entry.get("updated_at") or 0.0),
            )
        )
    items.sort(key=lambda c: c.updated_at, reverse=True)
    return items


def new_event_id(world_key: str, prefix: str = "ch") -> str:
    """在世界里生成一个不冲突的事件键，形如 ``ch1_20261021_a1b2``。

    带事件序号前缀是为了让人一眼看出这是第几章的卡，也方便按序排。
    """
    stamp = time.strftime("%Ym%d")
    tag = _safe_key(prefix) or "ch"
    existing = set(load_index(world_key)["docs"]["event"])
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
