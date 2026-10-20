"""内置预设世界：把 ``presets/`` 下的目录**原样装进世界库**（零模型调用）。

模型现写一个世界要一两分钟、还要花钱；赶时间或者只想直接开跑时，可以装一份预设：
世界地图、起始区域、几个场景与几条故事线都是预先写好的，装完即可 ``/跑团.开局`` 选它。

预设目录与世界库里的一个世界目录**完全同构**（``presets/<名字>/`` 就是 ``worlds/<world_key>/``）::

    presets/default_world/
        index.json                  # 该世界的结构化索引（相邻关系、文档标题等）
        docs/<kind>/<key>.md        # 每个文档一个 markdown 文件

安装 = 按 ``index.json`` 把 ``docs/`` 下的文档**原样重放**进世界库，走的
:func:`~...dnd.world_store.save_doc` / :func:`~...dnd.world_store.set_world_map` /
:func:`~...dnd.world_store.upsert_world` 与生成 agent 落库时是同一条代码路径，
所以装完的世界和「现生成」的没有任何区别（``ensure_scene`` / 复盘 / 开局列表都不用区分）。

格式一致，反向也成立：把 ``worlds/<world_key>/`` 整个目录拷进 ``presets/`` 就是一个新预设。

约定：``index.json`` 里写了的文档，磁盘上就必须有对应文件——缺文件会直接报错，
宁可装不上，也不要装出半个世界。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ATRI.exceptions import str_traceback
from ATRI.log import log

from .world_store import (
    DOC_KINDS,
    MAIN_DOC_KEY,
    list_worlds,
    load_doc,
    new_world_id,
    save_doc,
    set_world_map,
    upsert_world,
)

__all__ = [
    "DEFAULT_PRESET",
    "PRESET_ROOT",
    "ensure_preset",
    "install_preset",
    "list_presets",
    "load_preset_index",
]

#: 预设目录在插件根目录下（``atrilk_plugin_trpg/presets``）
PRESET_ROOT = Path(__file__).resolve().parents[2] / "presets"

#: 默认装的预设名
DEFAULT_PRESET = "default_world"

#: 预设里世界地图文档的键（与世界库一致：主文档固定为 ``main``）
_PRESET_WORLD_DOC = MAIN_DOC_KEY

#: 预设装进世界库时注册的主题标记
PRESET_THEME = "预设"


def list_presets() -> list[str]:
    """可用的预设名（有 ``index.json`` 才算）。"""
    if not PRESET_ROOT.is_dir():
        return []
    return sorted(
        path.name for path in PRESET_ROOT.iterdir() if (path / "index.json").is_file()
    )


def _preset_dir(name: str) -> Path:
    directory = PRESET_ROOT / str(name or DEFAULT_PRESET)
    if not (directory / "index.json").is_file():
        available = "、".join(list_presets()) or "（无）"
        raise FileNotFoundError(f"没有这个预设世界：{name}。可用：{available}")
    return directory


def load_preset_index(name: str = DEFAULT_PRESET) -> dict[str, Any]:
    """读预设的结构化索引（地区 / 邻接 / 文档清单），格式与世界库 ``index.json`` 相同。"""
    return _read_preset_index(_preset_dir(name))


def _read_preset_index(directory: Path) -> dict[str, Any]:
    """读预设目录下的 ``index.json``；结构坏了直接抛错（安装是显式操作，不静默容错）。"""
    path = directory / "index.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"预设索引读取失败：{path}（{e}）") from e
    if not isinstance(data, dict):
        raise ValueError(f"预设索引格式不对（应为 JSON 对象）：{path}")
    return data


def _collect_docs(
    directory: Path, index: dict[str, Any]
) -> list[tuple[str, str, dict, Path]]:
    """汇总索引里的全部文档条目；任何一个文件缺失都直接报错（列全部缺失，不逐个炸）。

    先校验再安装，避免「装了一半才发现缺文件」。
    """
    docs = index.get("docs")
    if not isinstance(docs, dict):
        raise ValueError("预设索引里没有 docs 段")
    entries: list[tuple[str, str, dict, Path]] = []
    missing: list[str] = []
    for kind in DOC_KINDS:
        section = docs.get(kind)
        if not isinstance(section, dict):
            continue
        for key, entry in section.items():
            path = directory / "docs" / kind / f"{key}.md"
            if not path.is_file():
                missing.append(str(path))
                continue
            entries.append(
                (kind, str(key), dict(entry) if isinstance(entry, dict) else {}, path)
            )
    if missing:
        raise FileNotFoundError(
            "预设文件缺失：\n" + "\n".join(f"· {item}" for item in missing)
        )
    return entries


def _preset_profile(directory: Path) -> tuple[str, str]:
    """从预设的世界地图文档里解析（世界名称, 一句话简介）；缺失为空串。"""
    path = directory / "docs" / "world" / f"{_PRESET_WORLD_DOC}.md"
    if not path.is_file():
        return "", ""
    # 延迟导入：presets 只在「开局 预设」时用到，不该拖累插件导入链
    from ..agents.dnd.gen_world import parse_world_profile

    return parse_world_profile(path.read_text(encoding="utf-8"))


def _seed_bible(key: str, index: dict[str, Any]) -> None:
    """装完库后播种世界圣经：世界文档与场景里的实体。

    全部是程序提取（地区 / 地标 / 国都 / 特殊地区、场景在场者），
    零模型成本——装完的预设世界和「现生成」的一样，开局即有圣经可用。

    章节卡不再这里播种：它是**游玩过程中**织出来才落库的，
    开局时世界库里一章都没有。
    """
    from ..agents.dnd.gen_scene import parse_scene_npcs
    from .world_bible import entities_from_json, register_entities, seed_from_world

    seed_from_world(key)
    register_entities(
        key,
        entities_from_json(load_doc(key, "world", MAIN_DOC_KEY) or ""),
        source="world",
    )
    docs = index.get("docs") or {}
    for scene_key in docs.get("scene") or {}:
        text = load_doc(key, "scene", str(scene_key)) or ""
        register_entities(key, parse_scene_npcs(text), source=f"scene:{scene_key}")


def install_preset(name: str = DEFAULT_PRESET, *, world_key: str = "") -> str:
    """把预设世界装进世界库，返回 ``world_key``。

    - 按 ``index.json`` 把 ``docs/`` 下的文档原样写进世界库（与生成 agent 同一条落库路径），
      所以下游（``ensure_scene`` / 复盘 / 开局列表）不用区分「预设」还是「现生成」；
    - 世界名称 / 一句话简介从世界地图文档里解析（与生成端同一套解析），写进世界注册表；
    - 已存在同 ``world_key`` 的同名文档会被覆盖（可重复安装，便于改预设后重装）；
    - 装完即播种世界圣经（地区 / 地标 / 人物 / 势力全部程序提取），开局就能用；
    - 不写存档、不建局——装完还要走正常的 ``/跑团.开局`` 选中它。
    """
    directory = _preset_dir(name)
    index = _read_preset_index(directory)
    key = world_key or new_world_id()

    count = 0
    for kind, doc_key, entry, path in _collect_docs(directory, index):
        meta = {k: v for k, v in entry.items() if k not in {"title", "updated_at"}}
        save_doc(
            key,
            kind,
            doc_key,
            path.read_text(encoding="utf-8"),
            title=str(entry.get("title") or doc_key),
            meta=meta or None,
        )
        count += 1

    set_world_map(
        key,
        [str(region) for region in index.get("regions", [])],
        {
            str(region): [str(neighbor) for neighbor in neighbors]
            for region, neighbors in (index.get("adjacency") or {}).items()
        },
    )

    title, intro = _preset_profile(directory)
    upsert_world(key, title=title or name, intro=intro, theme=PRESET_THEME)

    try:
        _seed_bible(key, index)
    except Exception as e:  # 圣经是增强项：播种失败不影响安装结果
        log.warning(f"预设世界「{name}」圣经播种失败：{str_traceback(e)}")

    log.info(f"预设世界「{name}」已安装：{count} 篇文档 → {key}")
    return key


def ensure_preset(name: str = DEFAULT_PRESET) -> str:
    """保证这个预设世界**已经装过**，返回它的世界键（幂等）。

    与 :func:`install_preset` 的差别只有一个：**已经装过就直接复用**。
    这是给「一条命令开局」用的——``/跑团.开局 无尽`` 反复发，
    不该每次都往世界列表里塞一个一模一样的灰烬海。

    识别方式：世界注册表里 ``theme`` 等于 :data:`PRESET_THEME`、
    且标题与预设解析出来的世界名一致。标题来自同一份文档，
    所以这条判断与「装的是同一个预设」等价。
    """
    title, _intro = _preset_profile(_preset_dir(name))
    if title:
        for world in list_worlds():
            if world.theme == PRESET_THEME and world.title == title:
                return world.key
    return install_preset(name)
