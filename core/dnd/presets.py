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

**版本**：``index.json`` 的 ``version`` 是这份预设的版本号（六位日期 ``YYMMDD``）。
改了 ``presets/`` 下任何文件，**提交前**跑一次 ``scripts/bump_preset_version.py``——
它会检查 ``presets/`` 是否有改动，有就把版本号提到当天。版本号一变，
下次开局 :func:`ensure_preset` 就会按新版就地重装一遍，用户不需要手动清理旧世界。
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
    kind_dir,
    list_worlds,
    load_doc,
    new_world_id,
    save_doc,
    set_provinces,
    set_world_map,
    upsert_world,
)

__all__ = [
    "DEFAULT_PRESET",
    "PRESET_ROOT",
    "PRESET_THEME",
    "VERSION_FORMAT",
    "VERSION_KEY",
    "ensure_preset",
    "install_preset",
    "list_presets",
    "load_preset_index",
    "preset_version",
    "today_version",
]

#: 预设目录在插件根目录下（``atrilk_plugin_trpg/presets``）
PRESET_ROOT = Path(__file__).resolve().parents[2] / "presets"

#: 默认装的预设名
DEFAULT_PRESET = "default_world"

#: 预设里世界地图文档的键（与世界库一致：主文档固定为 ``main``）
_PRESET_WORLD_DOC = MAIN_DOC_KEY

#: 预设装进世界库时注册的主题标记
PRESET_THEME = "预设"

#: 预设索引里版本号那个键
VERSION_KEY = "version"

#: 版本号的写法：**六位日期** ``YYMMDD``（如 ``261024`` = 2026-10-24）。
#: 不取 1.0 / 1.1 那套：预设改的是内容不是接口，日期本身就是「第几版」，
#: 还能顺带看出最后改动是哪天。改了 ``presets/`` 下任何文件，
#: 提交前用 ``scripts/bump_preset_version.py`` 把它提到当天——
#: 版本号一变，下次开局就会按新版重装一遍。
VERSION_FORMAT = "%y%m%d"


def preset_version(name: str = DEFAULT_PRESET) -> str:
    """预设的版本号（``presets/<name>/index.json`` 的 ``version``）；没有为空串。"""
    return str(_read_preset_index(_preset_dir(name)).get(VERSION_KEY, "") or "")


def today_version() -> str:
    """今天的版本号（六位日期）。"""
    import time

    return time.strftime(VERSION_FORMAT)


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

    **子目录只是给人看的**（按归属分：``docs/scene/盐仓区/盐仓码头.md``），
    程序不靠目录找文件——这里对每一类先建一张「文档名 → 路径」的表，
    递归扫一遍就够，索引里仍然是平铺的键。

    **按物理目录扫，不按逻辑种类扫**：据点、野外地点、地下城三类都落在
    ``docs/place/`` 下（见 :data:`~...dnd.world_store.KIND_DIR`），
    所以扫 ``place/`` 时要把三类都收进来。
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
        base = directory / "docs" / kind_dir(kind)
        # **按种类各建一张表**：据点、野外地点、地下城三种共用
        # ``docs/place/``，共用一张表会让同名的文件互相顶掉
        # （索引里三种是分开的三段，键可以重名）。
        table: dict[str, Path] = {}
        if base.is_dir():
            for path in base.rglob("*.md"):
                table.setdefault(path.stem, path)
        for key, entry in section.items():
            path = table.get(str(key))
            if path is None or not path.is_file():
                missing.append(str(base / f"{key}.md"))
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
    # 延迟导入：presets 只在「装预设世界」时用到，不该拖累插件导入链
    from ..agents.dnd.gen_world import parse_world_profile

    return parse_world_profile(path.read_text(encoding="utf-8"))


def _seed_bible(key: str, index: dict[str, Any]) -> None:
    """装完库后播种世界圣经：五层里**有文档的每一层**都登记进去。

    全部是程序提取（王国 / 区域 / 据点 / 野外地点 / 地下城、地标、国都、场景在场者），
    零模型成本——装完的预设世界和「现生成」的一样，开局即有圣经可用。

    登记成 ``place`` 而不是给层级各开一个种类：圣经是**名字登记簿**，
    层级在 ``index.json`` 的 ``provinces`` 与文档 ``meta.parent`` 里。

    事件卡不在这里播种：它是**游玩过程中**织出来才落库的，开局时一章都没有。
    """
    from ..agents.dnd.gen_scene import parse_scene_npcs
    from .world_bible import (
        Entity,
        entities_from_json,
        register_entities,
        seed_from_world,
    )
    from .world_store import SITE_KINDS

    seed_from_world(key)
    register_entities(
        key,
        entities_from_json(load_doc(key, "world", MAIN_DOC_KEY) or ""),
        source="world",
    )
    docs = index.get("docs") or {}
    level_notes = {
        "realm": "王国/地区",
        "province": "区域",
        **dict.fromkeys(SITE_KINDS, ""),
    }
    for kind, note in level_notes.items():
        for name in docs.get(kind) or {}:
            register_entities(
                key,
                [
                    Entity(
                        name=str(name), kind="place", note=note, source=f"preset:{kind}"
                    )
                ],
                source="preset",
            )
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

    set_provinces(key, dict(index.get("provinces") or {}))
    set_world_map(
        key,
        [str(region) for region in index.get("regions", [])],
        {
            str(region): [str(neighbor) for neighbor in neighbors]
            for region, neighbors in (index.get("adjacency") or {}).items()
        },
    )

    title, intro = _preset_profile(directory)
    version = preset_version(name)
    upsert_world(
        key,
        title=title or name,
        intro=intro,
        theme=PRESET_THEME,
        preset_version=version,
    )

    try:
        _seed_bible(key, index)
    except Exception as e:  # 圣经是增强项：播种失败不影响安装结果
        log.warning(f"预设世界「{name}」圣经播种失败：{str_traceback(e)}")

    log.info(
        f"预设世界「{name}」已安装：{count} 篇文档 → {key}"
        + (f"（版本 {version}）" if version else "")
    )
    return key


def ensure_preset(name: str = DEFAULT_PRESET) -> str:
    """保证这个预设世界**已经装过、且是最新版**，返回它的世界键（幂等）。

    与 :func:`install_preset` 的差别：**已经装过就直接复用**。
    这是给「开局」用的——``/跑团.开局`` 反复发，不该每次都往世界列表里
    塞一个一模一样的阿塞尔提姆。

    识别方式：世界注册表里 ``theme`` 等于 :data:`PRESET_THEME`、
    且标题与预设解析出来的世界名一致。标题来自同一份文档，
    所以这条判断与「装的是同一个预设」等价。

    **版本不一致就重装**：预设改过之后（``index.json`` 的 ``version`` 变了），
    库里那一份是旧的，就地用同一个 ``world_key`` 覆盖一遍——
    而不是另建一个世界：另建会让世界列表里出现两个同名世界，
    已经开过的存档也还指着旧的那份。存档那边不用管：文档是按键读的，
    换内容不换键，跑过的局照样接着跑。
    """
    title, _intro = _preset_profile(_preset_dir(name))
    version = preset_version(name)
    if title:
        for world in list_worlds():
            if world.theme == PRESET_THEME and world.title == title:
                if version and world.preset_version != version:
                    log.info(
                        f"预设世界「{title}」有新版本"
                        f"（{world.preset_version or '无'} → {version}），就地重装"
                    )
                    return install_preset(name, world_key=world.key)
                return world.key
    return install_preset(name)
