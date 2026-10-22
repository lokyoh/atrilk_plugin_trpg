"""地理层的常量：**逻辑种类、层级、物理目录、归属规则**。

为什么单独拆一个文件：这些常量被三处用到——

- :mod:`world_store`（索引读写、落盘路径）
- :mod:`presets`（装预设时按物理目录扫文件）
- ``scripts/organize_preset_dirs.py``（按归属重排目录）

前两个在插件包里，能正常 import；第三个是**独立跑的工具脚本**，
import 整个包会拉起 nonebot（要驱动、要配置），背上了整个运行时。
所以这里做成**零依赖**的模块：谁都能直接 import。

术语：

- **逻辑种类**（kind）：据点 / 野外地点 / 地下城是三种，生成模板与下游用法
  本来就不一样，合成一个再靠子类型分派，只是把区别藏进 meta 里。
- **物理目录**（dir）：落盘时用的目录名。三种地点共用 ``place/``——
  它们是同一层的东西，放在三个平行的目录里只是把分类重复了一遍。
  真正的区分在正文与 meta（``meta.site_kind``）里，不在目录名上。
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "DOC_KINDS",
    "DOC_KIND_LABELS",
    "KIND_DIR",
    "KIND_LEVEL",
    "LEVEL_KINDS",
    "SITE_KINDS",
    "doc_owner",
    "kind_dir",
]


#: 文档种类。世界是五层：**世界 → 王国/地区 → 区域（省）→ 地点 → 场景**。
#:
#: ``event`` 不属于地理层级，它是挂在存档上的剧本卡。
DOC_KINDS: tuple[str, ...] = (
    "world",
    "realm",
    "province",
    "city",
    "place",
    "dungeon",
    "scene",
    "event",
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
    "event": "事件",
}

#: 第 4 层的三个种类（地点）
SITE_KINDS: tuple[str, ...] = ("city", "place", "dungeon")

#: **逻辑种类 → 物理目录**。
#:
#: 据点、野外地点、地下城都落在 ``place/`` 下——它们是同一层的东西。
#: 其余种类的物理目录与逻辑种类同名。
KIND_DIR: dict[str, str] = {
    "city": "place",
    "dungeon": "place",
}

#: 文档种类 -> 地理层级（世界 = 1）。``event`` 不在地理层级里，记为 0。
KIND_LEVEL: dict[str, int] = {
    "world": 1,
    "realm": 2,
    "province": 3,
    "city": 4,
    "place": 4,
    "dungeon": 4,
    "scene": 5,
    "event": 0,
}

#: 层级 -> 该层全部种类
LEVEL_KINDS: dict[int, tuple[str, ...]] = {
    1: ("world",),
    2: ("realm",),
    3: ("province",),
    4: SITE_KINDS,
    5: ("scene",),
}


def kind_dir(kind: str) -> str:
    """某个逻辑种类落在哪个**物理目录**下。

    未知种类按原样返回（不抛错）——这个模块是零依赖的，
    校验交给调用方（:mod:`world_store` 会先查 :data:`DOC_KINDS`）。
    """
    kind = str(kind or "").strip().lower()
    return KIND_DIR.get(kind, kind)


def doc_owner(kind: str, meta: dict[str, Any] | None) -> str:
    """一份文档该放在哪个**归属目录**下（空 = 该种类的根目录）。

    落盘结构 ``docs/<物理目录>/<归属>/<文档名>.md``：省挂在王国下面、
    地点挂在省下面、场景与事件挂在地点下面。

    规则只认 ``meta`` 里本来就有的字段——``parent``（上级）与 ``site``
    （所属地点），不额外引入新概念。
    """
    kind = str(kind or "").strip().lower()
    meta = meta or {}
    if kind in ("world", "realm"):
        return ""
    if kind in ("province", *SITE_KINDS):
        return str(meta.get("parent") or "")
    if kind == "scene":
        return str(meta.get("site") or "")
    if kind == "event":
        return str(meta.get("site") or "")
    return ""
