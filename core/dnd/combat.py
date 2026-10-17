"""跑团战斗状态。

一场战斗 = 存档目录下的 ``combat.json``：先攻顺序、当前回合、参战者（玩家 + 敌人个体）的
生命 / AC / 是否退场，以及战斗起因。敌人数值来自 :mod:`.monsters`（怪物表）与
:mod:`.npcs`（人形 NPC 档位表），玩家数值来自角色卡副本。

设计要点：

- **先攻驱动**：开战时为所有参战者掷先攻并排序；系统按顺序推进回合，
  轮到玩家时由该玩家提交行动（:func:`...agents.dnd.game.submit_action`），
  轮到敌人时由 DM 在叙述里用 ``enemy_attack`` 结算。玩家一轮 = 一个 DND 回合。
- **玩家生命以角色卡为准**：:class:`Combatant` 里的 ``hp`` 只对敌人有意义，
  玩家的 HP / AC 每次现读角色卡，避免两处数值打架。
- **敌人**：怪物表里的杂兵打完即散；DM 具名拉出来的 NPC（``黑心商人卡尔:老兵``）
  会在 :mod:`.npcs` 的名单里留档，下次出场带着旧伤与旧账（:attr:`Combatant.npc`）。
- **倒地与死亡豁免**：玩家生命归零转**倒地濒死**，轮到其回合时由系统自动掷死亡豁免
  （DC 10，3 成功稳定 / 3 失败死亡，天然 20 回 1 血清醒，天然 1 记 2 次失败）；
  倒地后继续挨打会累计失败（暴击 2 次，单次伤害达生命上限即死）。进度记在**角色卡**上。
- **行动经济**：只做**软提示**——按工具类型记下「动作 / 附赠动作 / 反应 / 移动」，
  超支时在返回文本里提醒 DM，不拦截（最终裁决权在 DM）。

落盘形态（``markdown 文件 + JSON``）：

.. code-block:: text

    data/plugins/跑团/saves/<save_id>/
        combat.json                  # 先攻顺序 + 当前回合 + 敌人个体状态
        npcs.json                    # 出场过的具名 NPC 名单（旧伤 / 关系）
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ATRI.exceptions import str_traceback
from ATRI.log import log

from ..trpg.check import CheckResult, roll_check
from ..trpg.dice import Advantage, DiceError, DiceExpr, parse_dice, roll, roll_d20
from .card import DndCharacter
from .equipment import get_weapon
from .monsters import Monster, get_monster
from .npcs import (
    build_npc,
    register_encounter,
    resolve_monster,
    roster_hp,
    sync_combatant,
    update_npc,
)
from .save_store import Save, get_character, save_dir, set_character

__all__ = [
    "COMBAT_FILENAME",
    "MAX_ENEMIES",
    "TURN_RESOURCES",
    "CombatState",
    "Combatant",
    "EnemySpec",
    "advance_turn",
    "all_enemies_out",
    "apply_damage",
    "attack",
    "can_target",
    "clear_combat",
    "combat_path",
    "combat_text",
    "current_actor",
    "effective_ac",
    "effective_hp",
    "enemy_attack",
    "find_combatant",
    "heal_combatant",
    "is_dead",
    "is_dying",
    "is_out",
    "load_combat",
    "parse_enemy_list",
    "party_level",
    "peek_following",
    "player_status",
    "resource_of",
    "resources_text",
    "roll_damage",
    "save_combat",
    "start_combat",
    "update_enemy",
    "use_resource",
]


COMBAT_FILENAME = "combat.json"
#: 一场战斗最多容纳的敌人总数（防止 DM 一次拉一支军队）
MAX_ENEMIES = 12


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class Combatant:
    """一名参战者：玩家（``kind="player"``）或敌人个体（``kind="enemy"``）。"""

    cid: str  #: 战斗内唯一 id：玩家为 user_id，敌人为 ``goblin#1``
    kind: str  #: ``"player"`` / ``"enemy"``
    name: str  #: 显示名（角色名 / 地精1）
    initiative: int  #: 先攻总值
    ac: int
    hp: int
    max_hp: int
    monster: str = ""  #: 敌人：怪物 key 或 NPC 档位 key（``npc/veteran/3``）
    out: bool = False  #: 是否已退场（倒地 / 逃跑 / 被擒……）
    note: str = ""  #: 附注，如「被推下悬崖」
    npc: str = ""  #: 具名 NPC：名单上的名字（空 = 杂兵，打完即散）
    #: 本回合的行动经济记账（只对玩家有意义，软提示用）
    action_used: bool = False  #: 已用过动作
    bonus_used: bool = False  #: 已用过附赠动作
    reaction_used: bool = False  #: 已用过反应
    moved: bool = False  #: 本回合移动过

    @property
    def is_enemy(self) -> bool:
        return self.kind == "enemy"

    @property
    def is_named_npc(self) -> bool:
        """是否是留档的具名 NPC。"""
        return bool(self.npc)

    def reset_turn(self) -> None:
        """新一轮自己的回合开始：清空行动经济记账。"""
        self.action_used = False
        self.bonus_used = False
        self.reaction_used = False
        self.moved = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "cid": self.cid,
            "kind": self.kind,
            "name": self.name,
            "initiative": self.initiative,
            "ac": self.ac,
            "hp": self.hp,
            "max_hp": self.max_hp,
            "monster": self.monster,
            "out": self.out,
            "note": self.note,
            "npc": self.npc,
            "action_used": self.action_used,
            "bonus_used": self.bonus_used,
            "reaction_used": self.reaction_used,
            "moved": self.moved,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Combatant:
        return cls(
            cid=str(data.get("cid", "")),
            kind=str(data.get("kind", "enemy") or "enemy"),
            name=str(data.get("name", "") or data.get("cid", "")),
            initiative=int(data.get("initiative", 0) or 0),
            ac=int(data.get("ac", 10) or 10),
            hp=int(data.get("hp", 0) or 0),
            max_hp=int(data.get("max_hp", 0) or 0),
            monster=str(data.get("monster", "") or ""),
            out=bool(data.get("out", False)),
            note=str(data.get("note", "") or ""),
            npc=str(data.get("npc", "") or ""),
            action_used=bool(data.get("action_used", False)),
            bonus_used=bool(data.get("bonus_used", False)),
            reaction_used=bool(data.get("reaction_used", False)),
            moved=bool(data.get("moved", False)),
        )


@dataclass(slots=True)
class CombatState:
    """一场战斗的推进状态。"""

    #: 战斗发生时的场景键
    scene: str = ""
    #: 起因 / 环境，如「在酒馆后巷伏击」
    note: str = ""
    #: 第几轮（每个参战者行动一次为一轮）
    round: int = 1
    #: 当前回合在 ``order`` 里的下标（总是指向玩家）
    turn: int = 0
    #: 先攻顺序（从高到低）
    order: list[Combatant] = field(default_factory=list)
    #: 推进回合时系统自动结算的事（死亡豁免等），交给 DM 叙述；下次推进时清空
    pending: list[str] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene": self.scene,
            "note": self.note,
            "round": self.round,
            "turn": self.turn,
            "order": [c.to_dict() for c in self.order],
            "pending": list(self.pending),
            "started_at": self.started_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CombatState:
        order = data.get("order")
        return cls(
            scene=str(data.get("scene", "") or ""),
            note=str(data.get("note", "") or ""),
            round=int(data.get("round", 1) or 1),
            turn=int(data.get("turn", 0) or 0),
            order=[
                Combatant.from_dict(item)
                for item in (order or [])
                if isinstance(item, dict)
            ],
            pending=[
                str(item) for item in (data.get("pending") or []) if str(item).strip()
            ],
            started_at=float(data.get("started_at", 0.0) or 0.0),
        )


# --------------------------------------------------------------------------- #
# 落盘
# --------------------------------------------------------------------------- #


def combat_path(save_id: str) -> Path:
    """某存档战斗状态的落盘路径（``combat.json``）。"""
    return save_dir(save_id) / COMBAT_FILENAME


def load_combat(save: Save) -> CombatState | None:
    """读取战斗状态；没在战斗返回 ``None``。"""
    path = combat_path(save.save_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning(f"读取战斗状态失败：{path}（{str_traceback(e)}）")
        return None
    if not isinstance(data, dict):
        return None
    return CombatState.from_dict(data)


def save_combat(save: Save, state: CombatState) -> Path:
    """写入战斗状态。"""
    path = combat_path(save.save_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tmp.replace(path)
    return path


def clear_combat(save: Save) -> bool:
    """结束战斗并删除战斗状态；原本不在战斗中返回 ``False``。"""
    path = combat_path(save.save_id)
    if not path.is_file():
        return False
    path.unlink()
    return True


# --------------------------------------------------------------------------- #
# 现状查询（玩家的 HP / AC 一律现读角色卡）
# --------------------------------------------------------------------------- #


def effective_hp(save: Save, combatant: Combatant) -> tuple[int, int]:
    """取参战者的 ``(当前生命, 生命上限)``；玩家的以角色卡为准。"""
    if combatant.kind == "player":
        card = get_character(save, combatant.cid)
        if card is None:
            return 0, 0
        return card.hp, card.max_hp
    return combatant.hp, combatant.max_hp


def effective_ac(save: Save, combatant: Combatant) -> int:
    """取参战者的 AC；玩家的以角色卡为准。"""
    if combatant.kind == "player":
        card = get_character(save, combatant.cid)
        return card.armor_class if card is not None else combatant.ac
    return combatant.ac


def is_out(save: Save, combatant: Combatant) -> bool:
    """是否已不能行动（死亡 / 明确退场 / 生命归零）。"""
    if combatant.out:
        return True
    if not combatant.is_enemy and is_dead(save, combatant):
        return True
    hp, _max_hp = effective_hp(save, combatant)
    return hp <= 0


def is_dead(save: Save, combatant: Combatant) -> bool:
    """玩家是否已阵亡（死亡豁免 3 次失败）；敌人恒为 ``False``。"""
    if combatant.is_enemy:
        return False
    card = get_character(save, combatant.cid)
    return card is not None and card.is_dead


def is_dying(save: Save, combatant: Combatant) -> bool:
    """是否倒地濒死（0 血、未稳定、未死亡）：轮到自己的回合由系统自动掷死亡豁免。"""
    if combatant.is_enemy or combatant.out:
        return False
    card = get_character(save, combatant.cid)
    return card is not None and card.is_dying


def can_target(save: Save, combatant: Combatant) -> bool:
    """能否被选作攻击目标。

    已退场 / 已阵亡的不能再打；**倒地濒死或已稳定**的可以（倒地后挨打会累计失败
    次数，这是死亡豁免走向死亡的主要途径）。敌人则看生命值。
    """
    if combatant.out:
        return False
    if combatant.is_enemy:
        return combatant.hp > 0
    return not is_dead(save, combatant)


def player_status(save: Save, combatant: Combatant) -> str:
    """玩家的生存状态文本（死亡 / 稳定 / 濒死）；正常站着的返回空串。"""
    if combatant.is_enemy:
        return ""
    card = get_character(save, combatant.cid)
    return card.death_save_text() if card is not None else ""


# --------------------------------------------------------------------------- #
# 行动经济（软提示：只记账 + 提醒，最终由 DM 裁决）
# --------------------------------------------------------------------------- #

#: 玩家在一轮内的行动资源
TURN_RESOURCES: tuple[str, ...] = ("action", "bonus", "reaction", "move")
_RESOURCE_CN = {
    "action": "动作",
    "bonus": "附赠动作",
    "reaction": "反应",
    "move": "移动",
}


def resource_of(timing: str) -> str:
    """把施法时间 / 行动描述归到 ``action`` / ``bonus`` / ``reaction``。"""
    text = str(timing or "")
    if "附赠" in text:
        return "bonus"
    if "反应" in text:
        return "reaction"
    return "action"


def use_resource(save: Save, state: CombatState | None, ref: str, resource: str) -> str:
    """标记某名玩家本回合用过一项资源（动作 / 附赠 / 反应 / 移动）。

    **软提示**：超支不拦截，只在返回文本里提醒 DM。返回空串 = 首次使用（或不在
    战斗中、找不到该玩家、资源名不认识）；返回提示文本 = 这项资源本回合已经用过了
    （记账由本函数负责落盘）。
    """
    if state is None:
        return ""
    combatant = find_combatant(state, ref, kind="player")
    if combatant is None:
        return ""
    key = str(resource or "").strip()
    if key == "move":
        if combatant.moved:
            return ""
        combatant.moved = True
        save_combat(save, state)
        return ""
    flag = f"{key}_used"
    if key not in TURN_RESOURCES or not hasattr(combatant, flag):
        return ""
    label = _RESOURCE_CN.get(key, key)
    if getattr(combatant, flag):
        return (
            f"（提示：{combatant.name} 本回合的{label}已经用过了；"
            "若是同一动作的多段攻击（额外攻击等）或你裁定允许的额外行动，忽略即可）"
        )
    setattr(combatant, flag, True)
    save_combat(save, state)
    return ""


def resources_text(combatant: Combatant) -> str:
    """某参战者本回合已用掉的资源（展示用）。"""
    used = [
        _RESOURCE_CN[key]
        for key in TURN_RESOURCES
        if getattr(combatant, f"{key}_used" if key != "move" else "moved")
    ]
    return "、".join(used) if used else "无"


def current_actor(state: CombatState) -> Combatant | None:
    """当前回合的参战者（正常总是指向玩家）。"""
    if not state.order:
        return None
    index = state.turn if 0 <= state.turn < len(state.order) else 0
    return state.order[index]


def find_combatant(state: CombatState, ref: str, *, kind: str = "") -> Combatant | None:
    """按 id / 名字找参战者；``kind`` 限定 ``player`` / ``enemy``。

    名字支持包含匹配（「地精」能对上「地精2」），同名多个时取还站着的第一个。
    """
    text = str(ref or "").strip()
    if not text:
        return None
    pool = [c for c in state.order if not kind or c.kind == kind]
    for combatant in pool:
        if combatant.cid == text:
            return combatant
    for combatant in pool:
        if combatant.name == text:
            return combatant
    matched = [c for c in pool if text in c.name or c.name in text]
    if matched:
        return matched[0]
    return None


def living_enemies(state: CombatState) -> list[Combatant]:
    """还站着的敌人（按先攻顺序）。"""
    return [c for c in state.order if c.is_enemy and not c.out and c.hp > 0]


def _name_list(combatants: list[Combatant]) -> str:
    """把参战者名字拼成一行；没人时给个占位。"""
    names = [c.name for c in combatants]
    return "、".join(names) if names else "（无）"


# --------------------------------------------------------------------------- #
# 回合推进
# --------------------------------------------------------------------------- #


def _walk(
    save: Save,
    state: CombatState,
    *,
    commit: bool,
    rng: Any = None,
) -> tuple[list[Combatant], Combatant | None]:
    """从当前回合往前走：收集夹在中间的敌人，停在下一名该行动的玩家。

    路过**倒地濒死**的玩家时（仅 ``commit``）由系统自动掷一次死亡豁免，结果记进
    ``state.pending`` 交给 DM 叙述；已稳定 / 已阵亡的玩家直接跳过。

    :return: ``(夹在中间需要行动的敌人, 下一名该行动的玩家)``；没有可行动的玩家时后者为 ``None``。
    """
    order = state.order
    total = len(order)
    if total == 0:
        return [], None

    if commit:
        state.pending = []

    enemies: list[Combatant] = []
    index = state.turn
    round_no = state.round
    for _ in range(total * 2 + 1):
        index += 1
        if index >= total:
            index = 0
            round_no += 1
        combatant = order[index]
        if combatant.is_enemy:
            if not is_out(save, combatant):
                enemies.append(combatant)
            continue
        # —— 玩家：倒地濒死的先自动掷死亡豁免，然后跳过 ——
        card = get_character(save, combatant.cid)
        if card is not None and card.is_dying:
            if commit:
                outcome = card.death_save(rng=rng)
                set_character(save, card)
                state.pending.append(f"☠ {card.name} 死亡豁免：{outcome.describe()}")
            continue
        if is_out(save, combatant):
            continue
        if commit:
            state.turn = index
            state.round = round_no
            combatant.reset_turn()
        return enemies, combatant
    return enemies, None


def peek_following(
    save: Save, state: CombatState
) -> tuple[list[Combatant], Combatant | None]:
    """看一眼「当前回合之后」的走向，但不改变状态（也不会掷死亡豁免）。"""
    return _walk(save, state, commit=False)


def advance_turn(
    save: Save, state: CombatState, *, rng: Any = None
) -> tuple[list[Combatant], Combatant | None]:
    """结束当前回合，推进到下一名玩家，返回 ``(中间的敌人, 下一名玩家)``。

    路过倒地濒死的玩家时会**自动掷死亡豁免**（结果见 ``state.pending``）。
    """
    return _walk(save, state, commit=True, rng=rng)


def all_enemies_out(state: CombatState) -> bool:
    """敌人是否已全部退场。"""
    return not living_enemies(state)


# --------------------------------------------------------------------------- #
# 开战
# --------------------------------------------------------------------------- #

_SPLIT_RE = re.compile(r"[、,，;；/\n]+")
_COUNT_SUFFIX_RE = re.compile(r"^(?P<name>.+?)\s*[x×*]\s*(?P<count>\d{1,2})$")
_COUNT_PREFIX_RE = re.compile(r"^(?P<count>\d{1,2})\s*[只个名头]?\s*(?P<name>.+)$")
_COUNT_TAIL_RE = re.compile(r"^(?P<name>.+?)\s*(?P<count>\d{1,2})\s*[只个名头]?$")


@dataclass(frozen=True, slots=True)
class EnemySpec:
    """一批敌人：数据块 + 数量；具名 NPC 另外带名字。

    :param npc: 为真时表示这是 DM 具名拉出来的 NPC（要落 NPC 名单）。
    """

    monster: Monster
    count: int = 1
    name: str = ""  #: 具名 NPC 的名字（空 = 沿用数据块的名字）
    npc: bool = False


def party_level(save: Save) -> int:
    """队伍等级（所有玩家角色卡等级的平均值，至少 1）。"""
    levels: list[int] = []
    for user_id in save.player_ids:
        card = get_character(save, user_id)
        if card is not None:
            levels.append(max(1, int(card.level or 1)))
    if not levels:
        return 1
    return max(1, round(sum(levels) / len(levels)))


def _split_template(token: str) -> tuple[str, str]:
    """把 ``黑心商人卡尔:老兵`` 拆成 ``("黑心商人卡尔", "老兵")``；没有冒号时后半为空。"""
    text = str(token or "").strip()
    for sep in ("：", ":"):
        if sep in text:
            head, _, tail = text.rpartition(sep)
            if head.strip() and tail.strip():
                return head.strip(), tail.strip()
    return text, ""


def parse_enemy_list(text: str, *, level: int = 1) -> tuple[list[EnemySpec], list[str]]:
    """把 DM 写的敌人清单解析成 :class:`EnemySpec` 列表。

    支持：

    - 怪物名 / 档位名：``地精x3`` / ``地精×3`` / ``3只地精`` / ``地精 3`` / ``地精``
    - **具名 NPC**：``黑心商人卡尔:老兵``（档位按队伍等级给数值）、
      ``卡尔:匪帮首领``（直接用怪物表里的数据块）；名字后可带数量 ``打手x2:士兵``

    多个敌人用 ``、`` 等分隔。

    :param level: 队伍等级，决定档位 NPC 的数值。
    :return: ``(识别到的敌人, 没认出来的写法)``
    """
    specs: list[EnemySpec] = []
    unknown: list[str] = []
    total = 0

    def _room() -> int:
        return MAX_ENEMIES - total

    for raw in _SPLIT_RE.split(str(text or "")):
        token = raw.strip()
        if not token:
            continue
        head, template = _split_template(token)

        # —— 具名 NPC：名字:模板 ——
        if template:
            count = 1
            name = head
            match = _COUNT_SUFFIX_RE.match(head)
            if match:
                name = match.group("name").strip()
                count = int(match.group("count"))
            built = build_npc(template, level)
            if built is None:
                if token not in unknown:
                    unknown.append(token)
                continue
            monster, _key = built
            room = _room()
            if room <= 0:
                break
            count = max(1, min(count, room))
            specs.append(EnemySpec(monster, count, name=name or monster.cn, npc=True))
            total += count
            continue

        # —— 普通敌人：怪物名 / 档位名（可带数量）——
        count = 1
        name = token
        match = _COUNT_SUFFIX_RE.match(token)
        if match:
            name = match.group("name").strip()
            count = int(match.group("count"))
        else:
            match = _COUNT_PREFIX_RE.match(token)
            if match and build_npc(match.group("name"), level) is not None:
                name = match.group("name").strip()
                count = int(match.group("count"))
            else:
                match = _COUNT_TAIL_RE.match(token)
                if match and build_npc(match.group("name"), level) is not None:
                    name = match.group("name").strip()
                    count = int(match.group("count"))
        built = build_npc(name, level)
        if built is None:
            if token not in unknown:
                unknown.append(token)
            continue
        monster, _key = built
        room = _room()
        if room <= 0:
            break
        count = max(1, min(count, room))
        specs.append(EnemySpec(monster, count))
        total += count
    return specs, unknown


def _normalize_specs(specs: list) -> list[EnemySpec]:
    """兼容 ``(monster, count)`` 元组写法。"""
    out: list[EnemySpec] = []
    for item in specs or []:
        if isinstance(item, EnemySpec):
            out.append(item)
            continue
        try:
            monster, count = item[0], item[1] if len(item) > 1 else 1
        except (TypeError, IndexError):
            continue
        out.append(EnemySpec(monster, max(1, int(count or 1))))
    return out


def start_combat(
    save: Save,
    specs: list,
    *,
    current: str = "",
    note: str = "",
    rng: Any = None,
) -> CombatState:
    """按敌人清单与存档里的玩家开战：掷先攻、排顺序并落盘。

    :param specs: :func:`parse_enemy_list` 的结果（也接受旧的 ``(怪物, 数量)`` 元组）。
    :param current: 当前回合从哪名玩家开始（通常是发起这场冲突的玩家）。
    """
    level = party_level(save)
    normalized = _normalize_specs(specs)

    order: list[Combatant] = []
    for user_id in save.player_ids:
        card = get_character(save, user_id)
        if card is None:
            continue
        order.append(
            Combatant(
                cid=str(user_id),
                kind="player",
                name=card.name or str(user_id),
                initiative=roll_d20(rng=rng)[1] + card.initiative,
                ac=card.armor_class,
                hp=card.hp,
                max_hp=card.max_hp,
            )
        )

    # 先统计每个显示名要出现几次，决定加不加序号
    name_pool: Counter[str] = Counter()
    for spec in normalized:
        base = spec.name or spec.monster.cn
        name_pool[base] += max(1, int(spec.count))

    counters: Counter[str] = Counter()
    encounters: dict[str, tuple[Monster, int, int]] = {}
    for spec in normalized:
        for _ in range(max(1, int(spec.count))):
            base = spec.name or spec.monster.cn
            counters[base] += 1
            index = counters[base]
            name = base if name_pool[base] == 1 else f"{base}{index}"
            if spec.npc and spec.name:
                # 具名 NPC：满血上限取模板平均值，旧伤按名单带过来
                max_hp = max(1, spec.monster.hp)
                carried = roster_hp(save, spec.name)
                hp = (
                    max_hp
                    if carried is None or carried[0] <= 0
                    else min(carried[0], max_hp)
                )
                encounters.setdefault(base, (spec.monster, hp, max_hp))
                npc_name = spec.name
            else:
                max_hp = max(1, roll(spec.monster.hit_dice, rng=rng).total)
                hp = max_hp
                npc_name = ""
            order.append(
                Combatant(
                    cid=f"{spec.monster.key}#{index}",
                    kind="enemy",
                    name=name,
                    initiative=roll_d20(rng=rng)[1] + spec.monster.initiative,
                    ac=spec.monster.ac,
                    hp=hp,
                    max_hp=max_hp,
                    monster=spec.monster.key,
                    npc=npc_name,
                )
            )

    # 先攻从高到低；同点时玩家先手，再按名字稳定排序。
    order.sort(key=lambda c: (-c.initiative, 0 if c.kind == "player" else 1, c.name))

    turn = 0
    for index, combatant in enumerate(order):
        if combatant.kind == "player" and combatant.cid == str(current):
            turn = index
            break
    else:
        for index, combatant in enumerate(order):
            if combatant.kind == "player":
                turn = index
                break

    state = CombatState(
        scene=save.current_scene, note=str(note or ""), round=1, turn=turn, order=order
    )
    save_combat(save, state)

    # 具名 NPC 登记进名单（首次出场创建，之后带着旧伤与旧账）
    for name, (monster, hp, max_hp) in encounters.items():
        register_encounter(
            save,
            name=name,
            template=monster.key,
            level=level,
            hp=hp,
            max_hp=max_hp,
            place=save.current_scene,
        )
    return state


# --------------------------------------------------------------------------- #
# 伤害结算
# --------------------------------------------------------------------------- #


def roll_damage(expr: str, *, crit: bool = False, rng: Any = None) -> tuple[int, str]:
    """结算一次伤害表达式；``crit`` 为真时伤害骰翻倍（加值不加倍）。

    :return: ``(伤害点数, 明细说明)``
    """
    text = str(expr or "").strip()
    if not text:
        return 0, "0"
    if text.isdigit():
        value = int(text)
        return value, str(value)
    try:
        parsed = parse_dice(text)
    except DiceError:
        return 0, f"（无法解析的伤害表达式：{text}）"
    count = parsed.count * 2 if crit else parsed.count
    result = roll(
        DiceExpr(count=count, faces=parsed.faces, modifier=parsed.modifier),
        label="伤害",
        rng=rng,
    )
    return result.total, result.describe()


def apply_damage(
    save: Save,
    state: CombatState,
    target: Combatant,
    amount: int,
    *,
    crit: bool = False,
) -> str:
    """对参战者造成伤害并落盘。

    - 敌人：改 ``Combatant.hp``（随 ``combat.json`` 一起保存）；
    - 玩家：先扣临时生命，再扣角色卡的生命值并写回存档；生命归零转**倒地濒死**，
      倒地后继续挨打会累计死亡豁免失败次数（暴击 2 次；单次伤害达到生命上限即死）。
    """
    amount = max(0, int(amount))
    if target.kind == "player":
        card = get_character(save, target.cid)
        if card is None:
            return f"（找不到 {target.name} 的角色卡）"
        if card.is_dead:
            return f"{target.name} 已经阵亡，无需再结算伤害。"
        absorbed = min(card.temp_hp, amount)
        card.temp_hp -= absorbed
        incoming = amount - absorbed
        before = card.hp
        card.current_hp = max(0, card.hp - incoming)
        notes = [f"{target.name} 剩余 {card.hp}/{card.max_hp}"]
        if absorbed:
            notes.append(f"临时生命吸收 {absorbed}")
        if before <= 0 and incoming > 0:
            # 已经倒地（濒死 / 稳定）又挨打：死亡豁免失败 +1（暴击 +2），达上限即死
            if incoming >= card.max_hp:
                notes.append(card.die_instantly())
            else:
                notes.append(card.add_death_failure(2 if crit else 1))
        elif card.hp <= 0:
            card.mark_downed()
            notes.append(
                f"倒地，进入濒死（死亡豁免 {card.death_saves_success} 成功 / "
                f"{card.death_saves_failures} 失败）"
            )
            if incoming - before >= card.max_hp:
                notes.append(card.die_instantly())
        set_character(save, card)
        return "；".join(notes) + "。"

    target.hp = max(0, target.hp - amount)
    note = f"{target.name} 剩余 {target.hp}/{target.max_hp}"
    if target.hp <= 0:
        target.out = True
        note += "，已倒下"
    save_combat(save, state)
    sync_combatant(save, target)
    return note + "。"


def heal_combatant(
    save: Save, state: CombatState, target: Combatant, amount: int
) -> str:
    """治疗参战者（玩家写回角色卡，敌人写回战斗状态）。

    治疗倒地玩家会让其恢复意识并**清空死亡豁免进度**；已阵亡的玩家普通治疗无效
    （复活需要复活术 / 神迹，可由 DM 用 ``update_character`` 裁决）。
    """
    amount = max(0, int(amount))
    if target.kind == "player":
        card = get_character(save, target.cid)
        if card is None:
            return f"（找不到 {target.name} 的角色卡）"
        if card.is_dead:
            return (
                f"{target.name} 已经阵亡，普通治疗无效；复活需要复活术 / 神迹，"
                "可由 DM 用 update_character 的「死亡豁免」字段裁决。"
            )
        was_down = card.hp <= 0
        card.current_hp = min(card.max_hp, card.hp + amount)
        note = f"{target.name} 剩余 {card.hp}/{card.max_hp}"
        if was_down and card.hp > 0:
            card.clear_death_state()
            note += "，恢复意识（死亡豁免清零）"
        set_character(save, card)
        return note + "。"

    target.hp = min(target.max_hp, target.hp + amount)
    if target.hp > 0:
        target.out = False
    save_combat(save, state)
    sync_combatant(save, target)
    return f"{target.name} 剩余 {target.hp}/{target.max_hp}。"


# --------------------------------------------------------------------------- #
# 攻击
# --------------------------------------------------------------------------- #


def attack(
    save: Save,
    card: DndCharacter,
    state: CombatState,
    target_ref: str,
    weapon_ref: str = "",
    *,
    advantage: Advantage = Advantage.NORMAL,
    rng: Any = None,
) -> tuple[str, bool]:
    """玩家攻击：自动掷命中、掷伤害并扣血。

    :return: ``(给 DM 的结算文案, 是否命中)``
    """
    target = find_combatant(state, target_ref)
    if target is None:
        return (
            f"没找到目标「{target_ref}」。场上敌人：{_name_list(living_enemies(state))}",
            False,
        )
    if target.cid == str(card.user_id):
        return "不能攻击自己。", False
    if not can_target(save, target):
        return f"{target.name} 已经阵亡或退场了，换个目标吧。", False

    target_ac = effective_ac(save, target)
    weapon = get_weapon(weapon_ref) if weapon_ref else None
    if weapon is None and not weapon_ref:
        weapons = card.primary_weapons()
        weapon = weapons[0] if weapons else None

    # 指定了武器却不在角色卡装备里：照算，但提醒 DM（临时拾取 / 借用要有说法）
    warn = ""
    if weapon is not None and weapon_ref:
        owned = {item.key for item in card.primary_weapons()}
        if weapon.key not in owned:
            warn = f"（注意：{weapon.cn} 不在 {card.name} 的装备里）\n"

    if weapon is not None:
        result: CheckResult = card.check_attack(
            weapon, target_ac, advantage=advantage, rng=rng
        )
        damage_expr = weapon.damage_with_bonus(card.damage_bonus(weapon))
        label = weapon.cn
    else:
        bonus = card.proficiency + card.modifier("str")
        result = roll_check(
            bonus, target_ac, kind="徒手攻击", advantage=advantage, rng=rng
        )
        damage_expr = str(max(1, 1 + card.modifier("str")))
        label = "徒手"

    lines = [f"【{card.name}】{label} → {result.describe()}（目标 AC {target_ac}）"]
    if not result.success:
        lines.append(f"未命中 {target.name}。")
        return warn + "\n".join(lines), False

    damage, detail = roll_damage(damage_expr, crit=result.nat20, rng=rng)
    crit_text = "（暴击，伤害骰翻倍）" if result.nat20 else ""
    lines.append(f"命中 {target.name}！{detail}{crit_text}")
    lines.append(apply_damage(save, state, target, damage, crit=result.nat20))
    return warn + "\n".join(lines), True


def enemy_attack(
    save: Save,
    state: CombatState,
    enemy_ref: str,
    target_ref: str,
    attack_name: str = "",
    *,
    advantage: Advantage = Advantage.NORMAL,
    rng: Any = None,
) -> tuple[str, bool]:
    """敌人攻击玩家：按怪物数据自动掷命中、掷伤害并扣玩家的血。

    :return: ``(给 DM 的结算文案, 是否命中)``
    """
    enemy = find_combatant(state, enemy_ref, kind="enemy")
    if enemy is None:
        return (
            f"没找到敌人「{enemy_ref}」。场上敌人：{_name_list(living_enemies(state))}",
            False,
        )
    if is_out(save, enemy):
        return f"{enemy.name} 已经倒下了，不能行动。", False

    monster = resolve_monster(enemy.monster)
    if monster is None:
        return f"{enemy.name} 没有对应的怪物数据，请改用 roll_dice 手算。", False
    attack_obj = monster.get_attack(attack_name)
    if attack_obj is None:
        names = "、".join(a.name for a in monster.attacks) or "（无）"
        return f"{monster.cn} 没有「{attack_name}」这项攻击，可用：{names}。", False

    target = find_combatant(state, target_ref, kind="player") or find_combatant(
        state, target_ref
    )
    if target is None:
        players = [c.name for c in state.order if c.kind == "player"]
        return (
            f"没找到目标「{target_ref}」。场上玩家：{'、'.join(players) or '（无）'}",
            False,
        )
    if target.cid == enemy.cid or not can_target(save, target):
        return f"{target.name} 已经不能作为目标了。", False

    target_ac = effective_ac(save, target)
    bonus = monster.attack_bonus(attack_obj)
    result = roll_check(
        bonus,
        target_ac,
        kind=f"{attack_obj.name}攻击",
        advantage=advantage,
        rng=rng,
    )
    lines = [
        f"【{enemy.name}】{attack_obj.name} → {result.describe()}"
        f"（目标 {target.name} AC {target_ac}）"
    ]
    if not result.success:
        lines.append(f"未命中 {target.name}。")
        return "\n".join(lines), False

    damage, detail = roll_damage(attack_obj.damage, crit=result.nat20, rng=rng)
    crit_text = "（暴击，伤害骰翻倍）" if result.nat20 else ""
    lines.append(f"命中 {target.name}！{detail} {attack_obj.damage_type}{crit_text}")
    lines.append(apply_damage(save, state, target, damage, crit=result.nat20))
    return "\n".join(lines), True


def update_enemy(
    save: Save,
    state: CombatState,
    ref: str,
    *,
    hp: str = "",
    out: str = "",
    note: str = "",
) -> str:
    """按 DM 的裁决直接改敌人状态（陷阱、环境伤害、逃跑、投降、被擒……）。

    :param hp: 绝对生命（``"12"``）、增减（``"-5"`` / ``"+8"``）或留空不改。
    :param out: ``是`` / ``否`` 强制标记退场（倒地、逃跑、被擒）。
    """
    enemy = find_combatant(state, ref, kind="enemy") or find_combatant(state, ref)
    if enemy is None:
        return f"没找到「{ref}」。场上敌人：{_name_list([c for c in state.order if c.is_enemy])}"

    notes: list[str] = []
    text = str(hp or "").strip()
    if text:
        if text.startswith(("+", "-")):
            delta = int(re.sub(r"[^\d-]", "", text) or 0)
            if delta >= 0:
                notes.append(heal_combatant(save, state, enemy, delta))
            else:
                notes.append(apply_damage(save, state, enemy, -delta))
        else:
            try:
                value = int(re.sub(r"[^\d]", "", text) or 0)
            except ValueError:
                return f"看不懂的生命值写法：{hp}"
            if value >= enemy.hp:
                notes.append(heal_combatant(save, state, enemy, value - enemy.hp))
            else:
                notes.append(apply_damage(save, state, enemy, enemy.hp - value))

    flag = str(out or "").strip()
    if flag:
        enemy.out = flag not in {"否", "不是", "no", "false", "0", "无"}
        save_combat(save, state)
        notes.append(f"{enemy.name} {'已退场' if enemy.out else '回到战斗'}。")
        if enemy.npc:
            update_npc(save, enemy.npc, status="退场" if enemy.out else "存活")

    if note:
        enemy.note = note
        save_combat(save, state)
        notes.append(f"（{note}）")

    if not notes:
        hp_now, hp_max = effective_hp(save, enemy)
        return f"{enemy.name} 状态：{hp_now}/{hp_max}　AC {enemy.ac}" + (
            f"　{enemy.note}" if enemy.note else ""
        )
    return "".join(notes)


# --------------------------------------------------------------------------- #
# 展示
# --------------------------------------------------------------------------- #


def combat_text(save: Save, state: CombatState) -> str:
    """战斗现状文本，用于 DM 输入与玩家提示。"""
    current = current_actor(state)
    lines = [
        f"【战斗】第 {state.round} 轮，当前回合：{current.name if current else '（无人可行动）'}"
    ]
    if state.pending:
        lines.append("系统已自动结算（请在叙述里体现）：")
        lines.extend(f"  · {item}" for item in state.pending)
    if state.note:
        lines.append(f"起因 / 环境：{state.note}")
    lines.append("先攻顺序：")
    for index, combatant in enumerate(state.order, start=1):
        hp_now, hp_max = effective_hp(save, combatant)
        role = "玩家" if combatant.kind == "player" else "敌人"
        mark = " ← 现在" if current is not None and combatant.cid == current.cid else ""
        status = ""
        if combatant.kind == "player":
            text = player_status(save, combatant)
            if text:
                status = f"　{text}"
            elif combatant.out:
                status = "　已退场" + (
                    f"（{combatant.note}）" if combatant.note else ""
                )
        elif combatant.out:
            status = "　已退场" + (f"（{combatant.note}）" if combatant.note else "")
        elif hp_now <= 0:
            status = "　已倒下"
        turn_used = ""
        if combatant.kind == "player" and (
            current is None or combatant.cid == current.cid
        ):
            turn_used = f"　本回合已用：{resources_text(combatant)}"
        lines.append(
            f"  {index}. {combatant.initiative:>2} {combatant.name}（{role}）"
            f" HP {hp_now}/{hp_max}　AC {effective_ac(save, combatant)}{status}{turn_used}{mark}"
        )
    lines.append(
        "（轮到倒地濒死的玩家时由系统自动掷死亡豁免：DC 10，3 次成功伤势稳定、3 次失败死亡；"
        "倒地期间挨打会累计失败次数，任何治疗都能让他恢复意识）"
    )
    return "\n".join(lines)
