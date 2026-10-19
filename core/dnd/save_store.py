"""跑团存档。

一份存档 = 一局跑团（自动短 ID）。里面放：

- **角色卡副本**：建卡后复制一份进来，DM 的一切改动只改副本，不动 ``cards/`` 源卡；
- **进度状态**：当前场景、自由进度键值（任务、线索等）；
- **场景与事件日志**：DM 的 ``history`` 来源。

世界 / 剧情 / 地点 / 场景文档不在这里，统一放在**全局共享世界库**
（:mod:`.world_store`），存档只记录引用（``world_key`` / ``current_scene``）。

落盘形态（``markdown 文件 + JSON``）：

.. code-block:: text

    data/plugins/跑团/saves/<save_id>/
        save.json                    # 元信息 + 进度 + 玩家列表
        characters/<user_id>.json    # 角色卡副本
        events.jsonl                 # 事件日志（每行一个 JSON，喂给 DM）
        scenes/<名字>.md             # 本局用到的场景副本（变化追加在文末）
        round.json                   # 本轮等待名单 + 已提交的行动
        story.md                     # 复盘（给人看的剧情流水，满 10 MiB 滚到 story-2.md）
"""

from __future__ import annotations

import json
import re
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ATRI.exceptions import str_traceback
from ATRI.log import log

from .card import DndCharacter
from .names import lk_name

__all__ = [
    "BINDINGS_FILENAME",
    "EVENTS_FILENAME",
    "ROUND_FILENAME",
    "SAVES_DIRNAME",
    "SAVE_FILENAME",
    "SCENES_DIRNAME",
    "SCENE_CHANGE_HEADER",
    "STORY_FILENAME",
    "STORY_ROUND_KEY",
    "STORY_VOLUME_BYTES",
    "RoundState",
    "Save",
    "add_player",
    "append_scene_change",
    "append_story",
    "append_story_ending",
    "bindings_path",
    "character_path",
    "characters_dir",
    "clear_binding",
    "clear_round",
    "copy_scene",
    "create_save",
    "delete_save",
    "events_text",
    "find_character",
    "get_binding",
    "get_character",
    "has_scene",
    "list_bindings",
    "list_saves",
    "list_scenes",
    "load_bindings",
    "load_round",
    "load_save",
    "log_event",
    "new_save_id",
    "next_story_round",
    "read_scene",
    "recent_events",
    "remove_player",
    "round_path",
    "save_dir",
    "save_path",
    "save_round",
    "save_save",
    "saves_dir",
    "scene_path",
    "scenes_dir",
    "set_binding",
    "set_character",
    "story_files",
    "story_path",
    "update_progress",
]

SAVES_DIRNAME = "saves"
SAVE_FILENAME = "save.json"
EVENTS_FILENAME = "events.jsonl"
BINDINGS_FILENAME = "bindings.json"
SCENES_DIRNAME = "scenes"
ROUND_FILENAME = "round.json"

#: 场景副本里「变化记录」小节的小标题
SCENE_CHANGE_HEADER = "## 变化记录"


# --------------------------------------------------------------------------- #
# 路径
# --------------------------------------------------------------------------- #


def saves_dir() -> Path:
    """存档总目录（不存在则创建）。"""
    from ... import plugin

    path = plugin.get_path() / SAVES_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_id(save_id: str) -> str:
    safe = "".join(ch for ch in str(save_id) if ch.isalnum() or ch in "-_")
    return safe or "unknown"


def save_dir(save_id: str) -> Path:
    """某存档目录。"""
    return saves_dir() / _safe_id(save_id)


def save_path(save_id: str) -> Path:
    """某存档的 ``save.json`` 路径。"""
    return save_dir(save_id) / SAVE_FILENAME


def characters_dir(save_id: str) -> Path:
    """某存档的角色卡副本目录（不存在则创建）。"""
    path = save_dir(save_id) / "characters"
    path.mkdir(parents=True, exist_ok=True)
    return path


def character_path(save_id: str, user_id: str) -> Path:
    """某玩家在该存档里的角色卡副本路径。"""
    safe = "".join(ch for ch in str(user_id) if ch.isalnum() or ch in "-_") or "unknown"
    return characters_dir(save_id) / f"{safe}.json"


def event_path(save_id: str) -> Path:
    """某存档的事件日志路径。"""
    return save_dir(save_id) / EVENTS_FILENAME


# --------------------------------------------------------------------------- #
# 存档对象
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class Save:
    """一局跑团的存档（不含角色卡副本正文，副本单独存文件）。"""

    save_id: str
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    #: 引用的世界库键（全局共享，多世界之一）
    world_key: str = "main"
    #: 引用的故事键（该世界世界库内的 story 文档键；旧存档为空）
    story_key: str = ""
    #: 当前场景（世界库中 scene 文档的键）
    current_scene: str = ""
    #: 自由进度（当前时间、任务、线索等）
    progress: dict[str, Any] = field(default_factory=dict)
    #: 参与的玩家 user_id
    player_ids: list[str] = field(default_factory=list)
    #: 开局者 user_id（同一群每人只能开一局，判断依据）
    creator_id: str = ""

    def touch(self) -> None:
        self.updated_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        return {
            "save_id": self.save_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "world_key": self.world_key,
            "story_key": self.story_key,
            "current_scene": self.current_scene,
            "progress": self.progress,
            "player_ids": self.player_ids,
            "creator_id": self.creator_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Save:
        return cls(
            save_id=str(data.get("save_id", "")),
            created_at=float(data.get("created_at", 0.0) or 0.0),
            updated_at=float(data.get("updated_at", 0.0) or 0.0),
            world_key=str(data.get("world_key", "main") or "main"),
            story_key=str(data.get("story_key", "") or ""),
            current_scene=str(data.get("current_scene", "") or ""),
            progress=dict(data.get("progress") or {}),
            player_ids=[str(u) for u in (data.get("player_ids") or [])],
            creator_id=str(data.get("creator_id", "") or ""),
        )


def new_save_id() -> str:
    """生成一个不冲突的短存档 ID，形如 ``save_20261017_a1b2``。"""
    stamp = time.strftime("%Y%m%d")
    for _ in range(20):
        candidate = f"save_{stamp}_{uuid.uuid4().hex[:4]}"
        if not save_path(candidate).exists():
            return candidate
    return f"save_{stamp}_{uuid.uuid4().hex[:8]}"


# --------------------------------------------------------------------------- #
# 读写
# --------------------------------------------------------------------------- #


def save_save(save: Save) -> Path:
    """写入 ``save.json``。

    落盘前先把计费统计的最新累计并回对象：用量是在模型调用过程中边跑边记的，
    而 DM 的功能调用（改卡 / 切场景 / 按需生成）各有自己的存档副本、之后还会
    整份写回；用旧对象直接落盘会把刚记上的用量写回旧值（表现为「DM 的调用没记上」）。
    """
    save.touch()
    try:
        from .billing import sync_billing

        sync_billing(save)
    except Exception as e:  # 计费是附加功能，出问题也不能影响存档本身
        log.warning(f"跑团计费合并失败：{str_traceback(e)}")
    path = save_path(save.save_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(save.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tmp.replace(path)
    return path


def load_save(save_id: str) -> Save | None:
    """读取存档；不存在或损坏返回 ``None``。"""
    path = save_path(save_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning(f"读取存档失败：{path}（{str_traceback(e)}）")
        return None
    if not isinstance(data, dict):
        return None
    save = Save.from_dict(data)
    save.save_id = save.save_id or _safe_id(save_id)
    return save


def list_saves() -> list[Save]:
    """列出全部存档（按更新时间倒序）。"""
    result: list[Save] = []
    for child in saves_dir().iterdir():
        if not child.is_dir():
            continue
        save = load_save(child.name)
        if save is not None:
            result.append(save)
    result.sort(key=lambda s: s.updated_at, reverse=True)
    return result


def create_save(
    *, save_id: str | None = None, player_cards: list[DndCharacter] | None = None
) -> Save:
    """新建一份存档，可选地把若干角色卡的副本放进去。"""
    save = Save(save_id=_safe_id(save_id) if save_id else new_save_id())
    save_save(save)
    for card in player_cards or []:
        add_player(save, card)
    return save


def delete_save(save_id: str) -> bool:
    """删除整份存档（含角色卡副本与事件日志）。"""
    import shutil

    directory = save_dir(save_id)
    if not directory.is_dir():
        return False
    shutil.rmtree(directory, ignore_errors=True)
    try:
        from .billing import forget

        forget(save_id)
    except Exception:  # 存档已经删了，快照清不掉也不影响
        pass
    return True


# --------------------------------------------------------------------------- #
# 角色卡副本
# --------------------------------------------------------------------------- #


def add_player(save: Save, card: DndCharacter) -> DndCharacter:
    """把角色卡**复制**进存档（之后只改副本），返回副本。"""
    copy = DndCharacter.from_dict(card.to_dict())
    copy.user_id = str(card.user_id)
    character_path(save.save_id, copy.user_id).write_text(
        json.dumps(copy.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if copy.user_id not in save.player_ids:
        save.player_ids.append(copy.user_id)
    save_save(save)
    return copy


def get_character(save: Save, user_id: str) -> DndCharacter | None:
    """取存档里某玩家的角色卡副本；没有返回 ``None``。"""
    path = character_path(save.save_id, user_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning(f"读取角色卡副本失败：{path}（{str_traceback(e)}）")
        return None
    if not isinstance(data, dict):
        return None
    return DndCharacter.from_dict(data)


def set_character(save: Save, card: DndCharacter) -> Path:
    """写回存档里某玩家的角色卡副本。"""
    card.touch()
    path = character_path(save.save_id, card.user_id)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(card.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tmp.replace(path)
    save_save(save)
    return path


def remove_player(save: Save, user_id: str) -> bool:
    """把某玩家移出存档；不存在返回 ``False``。"""
    path = character_path(save.save_id, user_id)
    existed = path.is_file()
    if existed:
        path.unlink()
    if str(user_id) in save.player_ids:
        save.player_ids.remove(str(user_id))
        existed = True
    if existed:
        save_save(save)
    return existed


# --------------------------------------------------------------------------- #
# 进度 / 事件
# --------------------------------------------------------------------------- #


def update_progress(save: Save, **values: Any) -> Save:
    """合并更新进度字段并落盘。"""
    save.progress.update({k: v for k, v in values.items() if v is not None})
    save_save(save)
    return save


def log_event(
    save: Save, kind: str, content: str, *, actor: str = "", scene: str = ""
) -> None:
    """追加一条事件（DM 的 ``history`` 来源）。

    :param kind: 事件类型，如 ``action`` / ``narration`` / ``check`` / ``note``
    :param content: 事件正文
    :param actor: 触发者 user_id，可为空
    :param scene: 事件发生的场景键，留空则用存档当前场景
    """
    text = str(content or "").strip()
    if not text:
        return
    event = {
        "time": time.time(),
        "kind": str(kind or "note"),
        "actor": str(actor or ""),
        "scene": str(scene or save.current_scene or ""),
        "content": text,
    }
    path = event_path(save.save_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
    save_save(save)


def recent_events(
    save: Save, limit: int = 10, *, scene: str | None = None
) -> list[dict]:
    """取最近的事件（按时间正序返回最后 ``limit`` 条）。"""
    path = event_path(save.save_id)
    if not path.is_file():
        return []
    events: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                events.append(item)
    except OSError as e:
        log.warning(f"读取事件日志失败：{path}（{e}）")
        return []

    if scene is not None:
        events = [e for e in events if str(e.get("scene", "")) == str(scene)]
    if limit > 0:
        events = events[-limit:]
    return events


def events_text(save: Save, limit: int = 10, *, scene: str | None = None) -> str:
    """把最近事件排版成可直接内联进提示词的文本。"""
    labels = {"action": "行动", "narration": "叙述", "check": "判定", "note": "记录"}
    lines: list[str] = []
    for event in recent_events(save, limit, scene=scene):
        label = labels.get(str(event.get("kind")), str(event.get("kind")))
        actor = str(event.get("actor", ""))
        prefix = f"[{label}]"
        if actor:
            prefix += f"{actor} "
        lines.append(f"{prefix}{event.get('content', '')}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 场景副本：用到的场景复制一份进存档，变化追加在文末
# --------------------------------------------------------------------------- #
#
# 场景是会被玩坏的（门被砸开、NPC 死亡、东西被拿走），所以不能改全局世界库里的原文。
# 某个场景第一次被使用时从世界库复制一份进存档；之后以副本为准，变化在文末的
# 「变化记录」小节里追加，不覆盖原文。


def _file_key(key: str) -> str:
    """把键清洗成安全文件名（与 ``world_store`` 的规则一致，便于同名对应）。"""
    text = str(key).strip()
    cleaned = "".join(ch if (ch.isalnum() or ch in "-_") else "_" for ch in text)
    return cleaned.strip("_") or "untitled"


def scenes_dir(save_id: str) -> Path:
    """某存档的场景副本目录（不存在则创建）。"""
    path = save_dir(save_id) / SCENES_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def scene_path(save_id: str, key: str) -> Path:
    """某存档里某场景副本的路径。"""
    return scenes_dir(save_id) / f"{_file_key(key)}.md"


def list_scenes(save_id: str) -> list[str]:
    """某存档已有的场景副本名。"""
    return sorted(p.stem for p in scenes_dir(save_id).glob("*.md"))


def has_scene(save: Save, key: str) -> bool:
    """该存档里有没有这个场景的副本。"""
    return scene_path(save.save_id, key).is_file()


def read_scene(save: Save, key: str) -> str | None:
    """读取存档里的场景副本；没有返回 ``None``。"""
    path = scene_path(save.save_id, key)
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError as e:
        log.warning(f"读取场景副本失败：{path}（{e}）")
        return None


def copy_scene(save: Save, key: str, content: str) -> Path:
    """把世界库里的一份场景复制进存档（已存在则覆盖）。"""
    path = scene_path(save.save_id, key)
    path.write_text(str(content or ""), encoding="utf-8")
    return path


def append_scene_change(save: Save, key: str, note: str, *, kind: str = "变化") -> bool:
    """在存档里某场景副本文末追加一条变化记录；没有副本返回 ``False``。"""
    path = scene_path(save.save_id, key)
    if not path.is_file():
        return False
    text = str(note or "").strip()
    if not text:
        return False

    body = path.read_text(encoding="utf-8").rstrip()
    if SCENE_CHANGE_HEADER not in body:
        body += f"\n\n{SCENE_CHANGE_HEADER}"
    stamp = time.strftime("%Y-%m-%d %H:%M")
    label = str(kind or "变化").strip() or "变化"
    body += f"\n- [{stamp}] {label}：{text}\n"
    path.write_text(body, encoding="utf-8")
    return True


# --------------------------------------------------------------------------- #
# 复盘：给人看的剧情流水（story.md）
# --------------------------------------------------------------------------- #
#
# 和 ``events.jsonl``（喂给 DM 当 history）不同，复盘是**给玩家看的**：只在 DM 主持
# 结束后追加一次，记下这一轮玩家提交的行动、系统裁定摘要与 DM 的叙述。
# 标题按场景分节（``## 场景：X``），场景里的战斗再降一级（``### 战斗：起因``），
# 战斗打完回到场景级继续写；单卷超过 :data:`STORY_VOLUME_BYTES` 就滚到下一卷。

STORY_FILENAME = "story.md"
#: 单卷复盘的最大字节数（10 MiB），超了就开下一卷（``story-2.md``）
STORY_VOLUME_BYTES = 10 * 1024 * 1024
#: 存档进度里记复盘轮次的键；下划线开头 = 内部键，不进 DM 提示词
STORY_ROUND_KEY = "_story_round"

_STORY_NAME_RE = re.compile(r"^story(?:-(\d+))?\.md$")


def _story_volume_no(path: Path) -> int:
    """从文件名解析卷号（``story.md`` = 1）。"""
    match = _STORY_NAME_RE.match(path.name)
    if match is None:
        return 0
    return int(match.group(1) or 1)


def story_path(save_id: str, volume: int = 1) -> Path:
    """某存档第 ``volume`` 卷复盘的路径（第 1 卷是 ``story.md``）。"""
    name = STORY_FILENAME if volume <= 1 else f"story-{volume}.md"
    return save_dir(save_id) / name


def story_files(save_id: str) -> list[Path]:
    """某存档已有的复盘卷（按卷号排列）。"""
    return sorted(
        (p for p in save_dir(save_id).glob("story*.md") if _story_volume_no(p)),
        key=_story_volume_no,
    )


def _story_one_line(text: str, *, limit: int = 0) -> str:
    """把一段文本压成单行（复盘里每条占一行）。"""
    line = " ".join(str(text or "").split())
    if limit and len(line) > limit:
        line = line[: limit - 1].rstrip() + "…"
    return line


def _story_last_heading(path: Path, *, tail: int = 4096) -> str:
    """取复盘卷里最后一条场景 / 战斗标题（决定要不要补标题）。"""
    if not path.is_file():
        return ""
    try:
        data = path.read_bytes()[-tail:]
    except OSError as e:
        log.warning(f"读取复盘失败：{path}（{e}）")
        return ""
    for line in reversed(data.decode("utf-8", errors="ignore").splitlines()):
        text = line.strip()
        if text.startswith(("## ", "### ")):
            return text
    return ""


def _story_title(save: Save, volume: int) -> str:
    """复盘卷首：卷号 + 开始时间（多卷时注明承上卷）。"""
    started = time.strftime(
        "%Y-%m-%d %H:%M", time.localtime(save.created_at or time.time())
    )
    lines = [f"# 跑团复盘 · {save.save_id}", f"开始于 {started}"]
    if volume > 1:
        lines.append(
            f"（第 {volume} 卷，承上卷 {story_path(save.save_id, volume - 1).name}）"
        )
    return "\n".join(lines)


def _story_target(save_id: str, *, incoming: int) -> tuple[Path, int]:
    """给即将写入的 ``incoming`` 字节挑一卷：当前卷装不下就开新卷。"""
    volume = 1
    while True:
        path = story_path(save_id, volume)
        if not path.is_file():
            return path, volume
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        if size + incoming <= STORY_VOLUME_BYTES:
            return path, volume
        volume += 1


def _story_write(path: Path, text: str) -> None:
    """把一段内容追加到某卷（自动补开头空行）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    prefix = "" if not path.is_file() or path.stat().st_size == 0 else "\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(prefix + text.rstrip() + "\n")


def next_story_round(save: Save) -> int:
    """取本局下一篇复盘的轮次号（自增并落盘）。"""
    current = int(save.progress.get(STORY_ROUND_KEY, 0) or 0) + 1
    save.progress[STORY_ROUND_KEY] = current
    save_save(save)
    return current


def append_story(
    save: Save,
    *,
    round_no: int = 0,
    scene: str = "",
    combat: str = "",
    actions: Sequence[tuple[str, str]] = (),
    rulings: Sequence[str] = (),
    narration: str = "",
    transition: str = "",
) -> Path | None:
    """把本轮复盘追加进复盘文件（满 :data:`STORY_VOLUME_BYTES` 自动滚到下一卷）。

    :param round_no: 轮次序号（``0`` 表示不写「第 N 轮」）
    :param scene: 本轮结束时所在的场景，作为节点标题
    :param combat: 战斗中传起因备注（写成 ``### 战斗：起因``）；战斗打完传空串，
        下一轮就会重新写 ``## 场景：`` 回到场景级
    :param actions: ``[(角色名, 行动描述), ...]``
    :param rulings: 系统裁定摘要（检定 / 伤害 / 法术位等）
    :param narration: DM 的叙述
    :param transition: 场景转移说明，如 ``酒馆 → 后巷``
    """
    entry: list[str] = []
    if transition:
        entry.append(f"（{transition}）")
    if round_no > 0:
        entry.append(f"**第 {round_no} 轮**")
    for name, action in actions:
        text = _story_one_line(action)
        if text:
            entry.append(f"**{name}**：{text}")
    clean_rulings = [
        _story_one_line(item, limit=240) for item in rulings if str(item or "").strip()
    ]
    if clean_rulings:
        entry.append("> 裁定：" + "；".join(clean_rulings))

    lines = str(narration or "").splitlines() or [""]
    for index, line in enumerate(lines):
        prefix = "> **DM**：" if index == 0 else "> "
        entry.append(f"{prefix}{line.strip()}" if line.strip() else ">")

    entry_text = "\n".join(entry).strip()
    if not entry_text:
        return None

    scene_name = _story_one_line(scene) or "（未命名场景）"
    path, volume = _story_target(
        save.save_id, incoming=len(entry_text.encode("utf-8")) + 512
    )
    blocks: list[str] = []
    if not path.is_file():
        blocks.append(_story_title(save, volume))
        last = ""
    else:
        last = _story_last_heading(path)

    scene_head = f"## 场景：{scene_name}"
    combat_label = _story_one_line(combat)
    combat_head = f"### 战斗：{combat_label}" if combat_label else ""
    if combat_head and last == combat_head and not transition:
        pass  # 还在同一场战斗里：标题都已经有了
    elif last == scene_head:
        if combat_head:
            blocks.append(combat_head)
    else:
        blocks.append(scene_head)
        if combat_head:
            blocks.append(combat_head)
    blocks.append(entry_text)

    _story_write(path, "\n\n".join(blocks))
    return path


def append_story_ending(save: Save, note: str = "本局就此结档。") -> Path | None:
    """在复盘末尾落一条结档线（保留复盘，不删档）。"""
    text = _story_one_line(note)
    if not text:
        return None
    stamp = time.strftime("%Y-%m-%d %H:%M")
    payload = f"---\n\n**🏁 {text}**（{stamp}）"
    path, _volume = _story_target(save.save_id, incoming=len(payload.encode("utf-8")))
    _story_write(path, payload)
    return path


# --------------------------------------------------------------------------- #
# 本轮推进：等待名单（由 DM 决定）+ 已提交的行动
# --------------------------------------------------------------------------- #
#
# 跑团按轮推进：同一轮里，DM 决定这一轮「等待哪些玩家提交行动」（可以只等在场的玩家），
# 名单上的玩家各自描述一次行动、规则判断通过后先存下来；名单上的人都提交了，
# 才一次性交给 DM 主持（避免第一个人的行动被立刻结算、后面的人被忽略）。
# DM 未声明名单时：开局默认等待存档里的所有玩家，之后沿用上一轮的名单。


def round_path(save_id: str) -> Path:
    """某存档「本轮推进状态」的落盘路径（``round.json``）。"""
    return save_dir(save_id) / ROUND_FILENAME


@dataclass(slots=True)
class RoundState:
    """本轮的推进状态。"""

    #: 本轮等待提交行动的玩家 user_id；``None`` 表示还没指定过（按全员处理）
    waiting: list[str] | None = None
    #: 本轮已提交的行动：``{user_id: {"action": ..., "resolution": ...}}``
    actions: dict[str, dict[str, str]] = field(default_factory=dict)

    def pending_ids(self) -> list[str]:
        """名单上还没提交行动的玩家 user_id（按名单顺序）。"""
        return [uid for uid in (self.waiting or []) if uid not in self.actions]


def load_round(save: Save) -> RoundState:
    """读取本轮推进状态；不存在或损坏返回空状态。"""
    path = round_path(save.save_id)
    if not path.is_file():
        return RoundState()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning(f"读取本轮状态失败：{path}（{str_traceback(e)}）")
        return RoundState()
    if not isinstance(data, dict):
        return RoundState()

    waiting = data.get("waiting")
    waiting_ids = None if waiting is None else [str(uid) for uid in waiting]
    actions: dict[str, dict[str, str]] = {}
    raw_actions = data.get("actions")
    if isinstance(raw_actions, dict):
        for user_id, item in raw_actions.items():
            if isinstance(item, dict):
                actions[str(user_id)] = {
                    "action": str(item.get("action", "") or ""),
                    "resolution": str(item.get("resolution", "") or ""),
                    "note": str(item.get("note", "") or ""),
                }
            elif item:
                actions[str(user_id)] = {
                    "action": str(item),
                    "resolution": "",
                    "note": "",
                }
    return RoundState(waiting=waiting_ids, actions=actions)


def save_round(save: Save, state: RoundState) -> Path:
    """写入本轮推进状态。"""
    path = round_path(save.save_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"waiting": state.waiting, "actions": state.actions}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def clear_round(save: Save) -> bool:
    """清空本轮推进状态；原本不存在返回 ``False``。"""
    path = round_path(save.save_id)
    if not path.is_file():
        return False
    path.unlink()
    return True


def find_character(save: Save, target: str) -> DndCharacter | None:
    """按 user_id、角色名或 lk 绑定名（精确 / 包含）在存档里找角色卡。"""
    target = str(target or "").strip()
    if not target:
        return None
    card = get_character(save, target)
    if card is not None:
        return card
    for user_id in save.player_ids:
        item = get_character(save, user_id)
        if item is None:
            continue
        # DM 在提示词里看到的是显示名（优先 lk 名），它可能既不是 user_id 也不是
        # 角色名；这里一并认，免得 DM 照提示词复述的玩家名找不到人。
        for name in ((item.name or "").strip(), lk_name(user_id)):
            if name and (name == target or target in name or name in target):
                return item
    return None


# --------------------------------------------------------------------------- #
# 会话绑定：哪个群 / 私聊在进行哪一局
# --------------------------------------------------------------------------- #


def bindings_path() -> Path:
    """会话绑定表路径（``saves/bindings.json``）。"""
    return saves_dir() / BINDINGS_FILENAME


def load_bindings() -> dict[str, str]:
    """读取「会话 -> 存档」绑定表；不存在或损坏返回空表。"""
    path = bindings_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning(f"读取会话绑定失败：{path}（{str_traceback(e)}）")
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if v}


def _write_bindings(data: dict[str, str]) -> None:
    path = bindings_path()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def list_bindings() -> dict[str, str]:
    """全部会话绑定。"""
    return load_bindings()


def get_binding(chat_key: str) -> str:
    """取某会话绑定的存档 ID；没有绑定返回空串。"""
    return load_bindings().get(str(chat_key), "")


def set_binding(chat_key: str, save_id: str) -> None:
    """把某会话绑定到指定存档。"""
    data = load_bindings()
    data[str(chat_key)] = str(save_id)
    _write_bindings(data)


def clear_binding(chat_key: str) -> bool:
    """解除某会话的绑定；原本没有绑定返回 ``False``。"""
    data = load_bindings()
    existed = str(chat_key) in data
    if existed:
        data.pop(str(chat_key), None)
        _write_bindings(data)
    return existed
