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
from .battlemap import (
    CELL_FEET,
    battlefield_text,
    build_battlefield,
    chebyshev,
    cover_bonus,
    has_map,
    has_position,
    plan_move,
    range_check,
    terrain_at,
)
from .card import DndCharacter
from .conditions import (
    attack_advantage,
    combine_advantage,
    condition_text,
    is_incapacitated,
    merge_effects,
    normalize_conditions,
    resolve_damage,
)
from .equipment import get_weapon
from .monsters import Monster
from .npcs import (
    build_npc,
    is_npc_key,
    npc_xp,
    register_encounter,
    resolve_monster,
    roster_hp,
    sync_combatant,
    update_npc,
)
from .progression import xp_for_cr
from .save_store import Save, get_character, read_scene, save_dir, set_character

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
    "auto_crit_on_hit",
    "can_act",
    "can_target",
    "clear_combat",
    "combat_path",
    "combat_text",
    "combatant_conditions",
    "combatant_exhaustion",
    "combatant_state_text",
    "cover_text",
    "current_actor",
    "effective_ac",
    "effective_hp",
    "enemy_attack",
    "find_combatant",
    "forget_defeat",
    "heal_combatant",
    "is_dead",
    "is_dying",
    "is_out",
    "load_combat",
    "move_combatant",
    "parse_enemy_list",
    "party_level",
    "peek_following",
    "player_status",
    "record_defeat",
    "resource_of",
    "resources_text",
    "roll_damage",
    "save_combat",
    "start_combat",
    "tick_dot",
    "update_enemy",
    "use_resource",
]


COMBAT_FILENAME = "combat.json"
#: 一场战斗最多容纳的敌人总数（防止 DM 一次拉一支军队）
MAX_ENEMIES = 12


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #


def _int_or(value: Any, default: int) -> int:
    """把 JSON 里的值安全转成整数（缺省 / 空串 / 非法值都退回默认值）。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


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
    #: 棋盘坐标（0 基，``-1`` 表示还没放到棋盘上——老存档的战斗没有地图）
    x: int = -1
    y: int = -1
    #: 速度（尺 / 回合）
    speed: int = 30
    #: 本回合已经走过的距离（尺）
    move_used: int = 0
    #: 当前状态（中毒、倒地、束缚……）；**玩家的状态以角色卡为准**，
    #: 这里只记敌人的（DM 用 ``update_enemy`` 维护）
    conditions: list[str] = field(default_factory=list)
    #: 力竭等级（0~6）；玩家同样以角色卡为准
    exhaustion: int = 0

    @property
    def is_enemy(self) -> bool:
        return self.kind == "enemy"

    @property
    def is_named_npc(self) -> bool:
        """是否是留档的具名 NPC。"""
        return bool(self.npc)

    def reset_turn(self) -> None:
        """新一轮自己的回合开始：清空行动经济记账与已经走过的距离。"""
        self.action_used = False
        self.bonus_used = False
        self.reaction_used = False
        self.moved = False
        self.move_used = 0

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
            "x": self.x,
            "y": self.y,
            "speed": self.speed,
            "move_used": self.move_used,
            "conditions": list(self.conditions),
            "exhaustion": self.exhaustion,
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
            x=_int_or(data.get("x"), -1),
            y=_int_or(data.get("y"), -1),
            speed=_int_or(data.get("speed"), 30) or 30,
            move_used=_int_or(data.get("move_used"), 0),
            conditions=[
                str(item)
                for item in (data.get("conditions") or [])
                if str(item).strip()
            ],
            exhaustion=max(0, min(6, _int_or(data.get("exhaustion"), 0))),
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
    #: 战场棋盘：边长（格）；``0`` 表示这场战斗没有棋盘（老存档）
    width: int = 0
    height: int = 0
    #: 地形：(``"x,y"``) -> 地形键；没记录的格子是空地
    tiles: dict[str, str] = field(default_factory=dict)
    #: 战场风格（模板键 / 展示名），用于叙述素材
    terrain: str = ""
    terrain_label: str = ""
    #: 上一次把地图塞进提示词时的战场指纹（布局没变就不再重画地图）
    map_hash: str = ""
    #: 本场被击败的敌人（结算经验 / 战利品用）：``{"name","monster","cr","xp"}``
    defeated: list[dict[str, Any]] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene": self.scene,
            "note": self.note,
            "round": self.round,
            "turn": self.turn,
            "order": [c.to_dict() for c in self.order],
            "pending": list(self.pending),
            "width": self.width,
            "height": self.height,
            "tiles": dict(self.tiles),
            "terrain": self.terrain,
            "terrain_label": self.terrain_label,
            "map_hash": self.map_hash,
            "defeated": [dict(item) for item in self.defeated],
            "started_at": self.started_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CombatState:
        order = data.get("order")
        tiles = data.get("tiles")
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
            width=_int_or(data.get("width"), 0),
            height=_int_or(data.get("height"), 0),
            tiles=(
                {str(key): str(value) for key, value in tiles.items()}
                if isinstance(tiles, dict)
                else {}
            ),
            terrain=str(data.get("terrain", "") or ""),
            terrain_label=str(data.get("terrain_label", "") or ""),
            map_hash=str(data.get("map_hash", "") or ""),
            defeated=[
                {str(k): v for k, v in item.items()}
                for item in (data.get("defeated") or [])
                if isinstance(item, dict)
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


def effective_ac(
    save: Save, combatant: Combatant, state: CombatState | None = None
) -> int:
    """取参战者的 AC；玩家的以角色卡为准，守护之灵等加值与掩体加值一并算上。"""
    if combatant.kind == "player":
        card = get_character(save, combatant.cid)
        base = card.armor_class if card is not None else combatant.ac
    else:
        base = combatant.ac
    # 增益类状态给的 AC 加值（防护之灵 +2 等）——走状态表，不另开字段
    effect = merge_effects(normalize_conditions(combatant_conditions(save, combatant)))
    base += effect.ac_bonus
    if state is not None:
        base += cover_bonus(state, combatant)
    return base


def cover_text(state: CombatState, combatant: Combatant) -> str:
    """掩体说明（给叙述 / 展示用）；没有掩体返回空串。"""
    bonus = cover_bonus(state, combatant)
    if not bonus:
        return ""
    return f"（掩体 AC +{bonus}）"


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
# 状态条件（玩家的状态以角色卡为准，敌人的记在战斗状态里）
# --------------------------------------------------------------------------- #


def combatant_conditions(save: Save, combatant: Combatant) -> list[str]:
    """参战者当前的状态列表（规范名）。"""
    if combatant.kind == "player":
        card = get_character(save, combatant.cid)
        return normalize_conditions(card.conditions) if card is not None else []
    return normalize_conditions(combatant.conditions)


def combatant_exhaustion(save: Save, combatant: Combatant) -> int:
    """参战者的力竭等级（0~6）。"""
    if combatant.kind == "player":
        card = get_character(save, combatant.cid)
        return int(card.exhaustion or 0) if card is not None else 0
    return int(combatant.exhaustion or 0)


def combatant_state_text(save: Save, combatant: Combatant) -> str:
    """参战者的状态速览（给战斗现状 / 结算文案用）；没有状态返回空串。"""
    if combatant.kind == "player":
        card = get_character(save, combatant.cid)
        if card is None:
            return ""
        return card.conditions_text()
    return condition_text(combatant.conditions, exhaustion=combatant.exhaustion)


def can_act(save: Save, combatant: Combatant) -> bool:
    """能否自行行动：已退场 / 已倒下的不行，处于失能类状态（震慑 / 麻痹 /
    昏迷 / 石化 / 失能）的也不行——但它仍然可以被攻击（麻痹时还会被自动暴击）。
    """
    if is_out(save, combatant):
        return False
    return not is_incapacitated(combatant_conditions(save, combatant))


def auto_crit_on_hit(
    save: Save, target: Combatant, *, melee: bool, distance: int
) -> bool:
    """命中是否自动按暴击算（5e：5 尺内命中麻痹 / 昏迷的目标即暴击）。

    法术攻击同样适用（「命中即暴击」看的是攻击种类，不是武器）。
    """
    if not melee or distance < 0 or distance > 5:
        return False
    return bool({"麻痹", "昏迷"} & set(combatant_conditions(save, target)))


#: 内部别名（历史写法）
_auto_crit = auto_crit_on_hit


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


def tick_dot(save: Save, state: CombatState, combatant: Combatant) -> str:
    """回合开始时结算持续伤害（中毒等），返回给 DM 的一句话。

    5e 里中毒是「**每个自己的回合开始时**失去等于中毒等级的生命」，本该自动
    结算。从前只能靠 DM 每轮手动调一次 ``update_enemy``——漏一轮就少扣血，
    多扣一次就误伤。现在挂在回合推进里，按时点自动扣。

    扣到 0 血按规则走倒地濒死（并掷死亡豁免），不是简单地少一格血。
    """
    effect = merge_effects(combatant_conditions(save, combatant))
    if not effect.dot or effect.dot[1] <= 0:
        return ""
    damage_type, amount = effect.dot
    # 抗性要**在这里**算，不能交给 apply_damage：那里的「抗性后至少 1 点」
    # 是 PHB 对武器伤害的兜底，而中毒抗性应当直接免掉那 1 点。
    # 矮人对毒素有抗性时不该每回合照掉 1 血。
    resist_note = ""
    if combatant.kind == "player":
        card = get_character(save, combatant.cid)
        if card is not None:
            amount, resist_note = card.apply_resistance(amount, damage_type)
    else:
        from .monsters import get_monster

        monster = get_monster(combatant.monster) if combatant.monster else None
        if monster is not None:
            amount, resist_note = resolve_damage(
                amount, damage_type, monster.damage_resistances
            )
    if amount <= 0:
        return f"🔥 {combatant.name} 的持续伤害被抗性完全免掉（{damage_type}）"
    note = apply_damage(save, state, combatant, amount, damage_type=damage_type)
    if resist_note:
        note += f"（{resist_note}）"
    # 扣到 0 血：按 5e 走倒地濒死，接着掷死亡豁免（否则玩家会一直停在 0 血）
    card = get_character(save, combatant.cid) if combatant.kind == "player" else None
    if card is not None and card.is_dying:
        outcome = card.death_save(rng=None)
        set_character(save, card)
        note += f"；随即{outcome.describe()}"
    return f"🔥 {combatant.name} 受到持续伤害（{damage_type}）：{note}"


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
            if is_out(save, combatant):
                continue
            if not can_act(save, combatant):
                # 失能类状态（震慑 / 麻痹 / 昏迷……）：人还在场上（照样能被打），
                # 但这一动轮空——由 DM 在叙述里带一句
                if commit:
                    names = "、".join(combatant_conditions(save, combatant))
                    state.pending.append(
                        f"⏳ {combatant.name} 处于{names or '失能'}，本轮无法行动"
                    )
                continue
            enemies.append(combatant)
            continue
        # —— 玩家：回合开始先结算持续伤害（中毒等），再走倒地濒死 / 跳过 ——
        if commit and can_act(save, combatant):
            dot = tick_dot(save, state, combatant)
            if dot:
                state.pending.append(dot)
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
            # 进入新的一轮：敌人的移动额度也一并重置（它们不走 reset_turn）
            if round_no != state.round:
                for other in order:
                    if other.is_enemy:
                        other.move_used = 0
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


def _card_speed(card: DndCharacter) -> int:
    """角色的速度（尺 / 回合）；含状态修正（力竭减半 / 束缚归零），
    种族数据缺失时按 30 尺。"""
    try:
        return int(card.speed())
    except Exception:  # 种族数据损坏之类：退回 30 尺，别让开战失败
        return 30


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
                speed=_card_speed(card),
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
                    speed=int(getattr(spec.monster, "speed", 30) or 30),
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
    # 战场棋盘：按场景类型与人数生成，玩家在下、敌人在上（零额外模型调用）。
    # 生成失败（例如缺少场景文档）就退化成「没有棋盘」，射程校验自动跳过。
    try:
        scene_doc = read_scene(save, save.current_scene) or ""
        build_battlefield(
            state,
            [c for c in order if c.kind == "player"],
            [c for c in order if c.is_enemy],
            # 场景正文 + DM 给的战场环境都拿来猜地形模板（雨巷 / 林地 / 洞窟……）
            scene_text=f"{scene_doc}\n{note or ''}",
        )
    except Exception as e:  # 棋盘只是增强，不能因为它让开战失败
        log.warning(f"生成战场棋盘失败：{str_traceback(e)}")
        state.width = 0
        state.height = 0
        state.tiles = {}
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


def record_defeat(state: CombatState, enemy: Combatant, reason: str = "倒下") -> None:
    """把被击败的敌人记进 :attr:`CombatState.defeated`，供战斗结算算经验 / 战利品。

    **严格 5e**：只有生命归零（被打倒）才调用这里。逃跑 / 被擒 / 投降走
    :func:`update_enemy` 的退场分支，**不**产生战果。

    去重按**敌人 cid**：同模板的多个个体（``地精1`` / ``地精2``）各记一次，
    而同一只被打到 0 血、又被 DM 标记退场时不会重复记账。

    经验按来源分两路：

    - **档位 NPC**（``npc/<档位>/<等级>``）：走 :func:`~.npcs.npc_xp` 的独立表——
      它不是图鉴条目、没有正式 CR，拿 ``cr``（记 0）换算只会得到 10 XP；
    - **普通怪物**：按 :func:`~.progression.xp_for_cr` 由挑战等级推出；模板已查不到时按 CR0 兜底。
    """
    if not enemy.is_enemy or not enemy.monster:
        return
    key = enemy.monster
    if any(item.get("cid") == enemy.cid for item in state.defeated):
        return
    monster = resolve_monster(key)
    tier_npc = is_npc_key(key)
    cr = float(monster.cr) if monster is not None else 0.0
    xp = npc_xp(key) if tier_npc else xp_for_cr(cr)
    cr_text = "档位" if tier_npc else monster.cr_text if monster is not None else "0"
    state.defeated.append(
        {
            "cid": enemy.cid,
            "name": enemy.name,
            "monster": key,
            "cr": cr,
            "cr_text": cr_text,
            "xp": xp,
            "npc": enemy.npc,
            "reason": reason,
        }
    )


def forget_defeat(state: CombatState, enemy: Combatant) -> None:
    """把某个敌人从战果清单里撤掉（它被治回来了 / 撤销了退场标记）。

    没有这条的话，被治疗救活的敌人仍然会照常计经验与战利品。
    """
    state.defeated = [item for item in state.defeated if item.get("cid") != enemy.cid]


def apply_damage(
    save: Save,
    state: CombatState,
    target: Combatant,
    amount: int,
    *,
    damage_type: str = "",
    crit: bool = False,
    rng: Any = None,
) -> str:
    """对参战者造成伤害并落盘。

    - **抗性结算**：按目标（玩家 = 角色卡抗性 + 状态抗性；敌人 = 怪物表抗性 +
      生物类型默认抗性）过一遍伤害类型，抗性减半 / 免疫归零 / 易伤翻倍；
    - 敌人：改 ``Combatant.hp``（随 ``combat.json`` 一起保存）；
    - 玩家：先扣临时生命，再扣角色卡的生命值并写回存档；生命归零转**倒地濒危**，
      倒地后继续挨打会累计死亡豁免失败次数（暴击 2 次；单次伤害达到生命上限即死）；
    - **专注**：玩家受伤后若正在维持法术，自动掷体质豁免（DC = max(10, 伤害//2)）。
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
        # 抗性 / 免疫 / 易伤（角色卡固定抗性 + 狂暴等状态抗性）
        incoming, res_note = card.apply_resistance(incoming, damage_type)
        before = card.hp
        card.current_hp = max(0, card.hp - incoming)
        notes = [f"{target.name} 剩余 {card.hp}/{card.max_hp}"]
        if absorbed:
            notes.append(f"临时生命吸收 {absorbed}")
        if res_note:
            notes.append(res_note)
        if before <= 0 and incoming > 0:
            # 已经倒地（濒危 / 稳定）又挨打：死亡豁免失败 +1（暴击 +2），达上限即死
            if incoming >= card.max_hp:
                notes.append(card.die_instantly())
            else:
                notes.append(card.add_death_failure(2 if crit else 1))
        elif card.hp <= 0:
            card.mark_downed()
            notes.append(
                f"倒地，进入濒危（死亡豁免 {card.death_saves_success} 成功 / "
                f"{card.death_saves_failures} 失败）"
            )
            if incoming - before >= card.max_hp:
                notes.append(card.die_instantly())
        # 受伤后维持专注的体质豁免；生命归零 = 昏迷，直接断专注（5e）
        if incoming > 0 and card.concentrating:
            if card.hp <= 0:
                notes.append(card.break_concentration("生命归零"))
            else:
                _kept, conc_text = card.concentration_save(incoming, rng=rng)
                notes.append(conc_text)
        set_character(save, card)
        return "；".join(notes) + "。"

    # —— 敌人：按怪物表抗性结算，再扣 Combatant.hp ——
    monster = resolve_monster(target.monster)
    resistances = monster.all_resistances if monster is not None else ()
    amount, res_note = resolve_damage(amount, damage_type, resistances)
    target.hp = max(0, target.hp - amount)
    note = f"{target.name} 剩余 {target.hp}/{target.max_hp}"
    if res_note:
        note += f"（{res_note}）"
    if target.hp <= 0:
        target.out = True
        record_defeat(state, target)
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
        # 从 0 血被救回来（或者撤销了退场标记）就不能再算战果——
        # 否则经验与战利品照发
        forget_defeat(state, target)
    save_combat(save, state)
    sync_combatant(save, target)
    return f"{target.name} 剩余 {target.hp}/{target.max_hp}。"


# --------------------------------------------------------------------------- #
# 攻击
# --------------------------------------------------------------------------- #


def _pick_weapon(save, state, card, attacker, target) -> tuple[Any, str]:
    """DM 没指定武器时替他挑一件，**并说明挑了哪件**。

    判据是到目标的距离：够得着就用近战，够不着就换远程，实在没有才徒手。
    从前一律取 ``primary_weapons()[0]``，于是背上弓的战士说「我射他」时，
    系统在用他的长剑——DM 也不知道出了岔子。

    :return: ``(武器或 None, 给 DM 的说明；无需说明时为空串)``。
    """
    weapons = card.primary_weapons()
    if not weapons:
        return None, ""
    if len(weapons) == 1:
        # 只有一件就不用解释了，但也要写清楚是哪件（DM 未必记得角色卡内容）
        return weapons[0], f"使用 {weapons[0].cn}"

    distance = -1
    if attacker is not None and state is not None:
        from .battlemap import distance_feet

        # 没铺棋盘 / 没落子时返回 -1，走下面的保守分支
        distance = distance_feet(attacker, target)

    melee = [w for w in weapons if not w.is_ranged]
    ranged = [w for w in weapons if w.is_ranged]

    if distance < 0 or distance <= 10:
        # 不知道距离（没铺棋盘）或就在近处：优先近战
        chosen = melee[0] if melee else ranged[0]
    elif melee and distance <= _reach(card, melee[0]):
        chosen = melee[0]
    else:
        # 够不着：能远程就远程，否则仍用近战（马上会被射程校验打回，
        # 那时 DM 会知道应该移动过去）
        chosen = ranged[0] if ranged else melee[0] if melee else weapons[0]

    return chosen, f"自动选用的武器：{chosen.cn}（{_weapon_brief(chosen)}）"


def _reach(card, weapon) -> int:
    """近战触及范围（有「触及」属性的武器是 10 尺）。"""
    return 10 if "触及" in (getattr(weapon, "properties", "") or "") else 5


def _weapon_brief(weapon) -> str:
    """武器的一句简介（远程带射程），给 DM 判断这次判断得对不对。"""
    if getattr(weapon, "is_ranged", False):
        return (
            f"远程，常规射程 {weapon.ranged_normal} 尺"
            f"{f'，极限 {weapon.ranged_max} 尺' if weapon.ranged_max else ''}"
        )
    parts = ["近战"]
    if "触及" in (getattr(weapon, "properties", "") or ""):
        parts.append("触及 10 尺")
    if getattr(weapon, "is_finesse", False):
        parts.append("灵巧")
    return "，".join(parts)


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

    target_ac = effective_ac(save, target, state)
    attacker = next(
        (c for c in state.order if c.kind == "player" and c.cid == str(card.user_id)),
        None,
    )

    weapon = get_weapon(weapon_ref) if weapon_ref else None
    pick_note = ""
    if weapon is None and not weapon_ref:
        # DM 没说用哪件：**按目标距离挑**——近了用近战、够不着就用远程，
        # 永远不会拿着长剑去够 30 尺外的敌人（从前一律取 weapons[0]）。
        weapon, pick_note = _pick_weapon(save, state, card, attacker, target)

    # 指定了武器却不在角色卡装备里：照算，但提醒 DM（临时拾取 / 借用要有说法）
    warn = ""
    if pick_note:
        warn = f"（{pick_note}）\n"
    if weapon is not None and weapon_ref:
        owned = {item.key for item in card.primary_weapons()}
        if weapon.key not in owned:
            warn += f"（注意：{weapon.cn} 不在 {card.name} 的装备里）\n"
    gate = range_check(
        state,
        attacker,
        target,
        reach=10 if (weapon is not None and "触及" in weapon.properties) else 5,
        normal=weapon.ranged_normal if weapon is not None else 0,
        max_range=weapon.ranged_max if weapon is not None else 0,
    )
    if not gate.ok:
        return f"{warn}距离不足：{gate.reason}", False
    if gate.disadvantage and advantage is not Advantage.DISADVANTAGE:
        advantage = Advantage.DISADVANTAGE
        warn += "（目标超出常规射程，本次攻击带劣势）\n"

    # 目标状态带来的优势 / 劣势（倒地、隐形、目盲、束缚……）；攻击者自己的状态
    # （中毒、力竭……）由角色卡的检定函数自动合并，两边相遇时自然抵消
    is_melee = weapon is None or weapon.ranged_normal == 0
    state_adv, state_reason = attack_advantage(
        [], combatant_conditions(save, target), melee=is_melee, distance=gate.distance
    )
    advantage = combine_advantage(advantage, state_adv)
    if state_reason:
        warn += f"（{state_reason}）\n"

    if weapon is not None:
        result: CheckResult = card.check_attack(
            weapon, target_ac, advantage=advantage, rng=rng
        )
        damage_expr = weapon.damage_with_bonus(card.damage_bonus(weapon))
        damage_type = weapon.damage_type
        label = weapon.cn
    else:
        bonus = card.proficiency + card.modifier("str")
        # 徒手也走角色卡的状态合并（中毒 / 力竭的劣势不能漏）
        final_adv, state_note = card.resolve_attack_advantage(advantage)
        result = roll_check(
            bonus, target_ac, kind="徒手攻击", advantage=final_adv, rng=rng
        )
        if state_note:
            result.kind += state_note
        damage_expr = str(max(1, 1 + card.modifier("str")))
        damage_type = "钝击"
        label = "徒手"

    lines = [
        f"【{card.name}】{label} → {result.describe()}"
        f"（目标 AC {target_ac}{cover_text(state, target)}；距离 {gate.distance} 尺）"
        if gate.distance >= 0
        else f"【{card.name}】{label} → {result.describe()}（目标 AC {target_ac}）"
    ]
    if not result.success:
        lines.append(f"未命中 {target.name}。")
        return warn + "\n".join(lines), False

    # 5e：5 尺内命中麻痹 / 昏迷的目标即暴击（伤害骰照翻倍）
    auto_crit = _auto_crit(save, target, melee=is_melee, distance=gate.distance)
    crit = result.nat20 or auto_crit
    damage, detail = roll_damage(damage_expr, crit=crit, rng=rng)
    crit_text = "（暴击，伤害骰翻倍）" if crit else ""
    lines.append(f"命中 {target.name}！{detail}{crit_text}")
    lines.append(
        apply_damage(
            save, state, target, damage, damage_type=damage_type, crit=crit, rng=rng
        )
    )
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
    if not can_act(save, enemy):
        names = "、".join(combatant_conditions(save, enemy)) or "失能"
        return f"{enemy.name} 处于{names}，本轮无法行动。", False

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

    # 距离 / 射程 / 视线：有棋盘时硬校验——够不着这一击就不该结算。
    gate = range_check(
        state,
        enemy,
        target,
        reach=attack_obj.reach,
        normal=attack_obj.ranged_normal,
        max_range=attack_obj.ranged_max,
    )
    if not gate.ok:
        return f"{enemy.name} 够不着 {target.name}：{gate.reason}", False
    if gate.disadvantage and advantage is not Advantage.DISADVANTAGE:
        advantage = Advantage.DISADVANTAGE

    # 双方状态带来的优势 / 劣势（敌人中毒 / 玩家倒地 / 隐形……），相遇抵消
    state_adv, state_reason = attack_advantage(
        combatant_conditions(save, enemy),
        combatant_conditions(save, target),
        melee=attack_obj.ranged_normal == 0,
        distance=gate.distance,
    )
    advantage = combine_advantage(advantage, state_adv)

    target_ac = effective_ac(save, target, state)
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
        f"（目标 {target.name} AC {target_ac}{cover_text(state, target)}"
        + (f"，距离 {gate.distance} 尺）" if gate.distance >= 0 else "）")
    ]
    if state_reason:
        lines.append(f"（{state_reason}）")
    if not result.success:
        lines.append(f"未命中 {target.name}。")
        return "\n".join(lines), False

    # 5e：5 尺内命中麻痹 / 昏迷的目标即暴击（伤害骰照翻倍）
    auto_crit = _auto_crit(
        save, target, melee=attack_obj.ranged_normal == 0, distance=gate.distance
    )
    crit = result.nat20 or auto_crit
    damage, detail = roll_damage(attack_obj.damage, crit=crit, rng=rng)
    crit_text = "（暴击，伤害骰翻倍）" if crit else ""
    lines.append(f"命中 {target.name}！{detail} {attack_obj.damage_type}{crit_text}")
    lines.append(
        apply_damage(
            save,
            state,
            target,
            damage,
            damage_type=attack_obj.damage_type,
            crit=crit,
            rng=rng,
        )
    )
    return "\n".join(lines), True


def update_enemy(
    save: Save,
    state: CombatState,
    ref: str,
    *,
    hp: str = "",
    out: str = "",
    note: str = "",
    conditions: str = "",
) -> str:
    """按 DM 的裁决直接改敌人状态（陷阱、环境伤害、逃跑、投降、被擒……）。

    :param hp: 绝对生命（``"12"``）、增减（``"-5"`` / ``"+8"``）或留空不改。
    :param out: ``是`` / ``否`` 强制标记退场（倒地、逃跑、被擒）。
        **严格 5e**：经验与战利品只在敌人生命被打到 0 时结算；靠 ``out``
        标记的退场（逃跑 / 被擒 / 投降）不算战果，会在结算时被排除。
    :param conditions: 状态列表（``"中毒、束缚"``）；传 ``"清空"`` 移除全部状态。
    """
    enemy = find_combatant(state, ref, kind="enemy") or find_combatant(state, ref)
    if enemy is None:
        return f"没找到「{ref}」。场上敌人：{_name_list([c for c in state.order if c.is_enemy])}"

    notes: list[str] = []
    cond_text = str(conditions or "").strip()
    if cond_text:
        if cond_text in {"清空", "清除", "无", "none"}:
            enemy.conditions = []
            notes.append(f"{enemy.name} 的状态已清空。")
        else:
            enemy.conditions = normalize_conditions(cond_text)
            if enemy.conditions:
                notes.append(f"{enemy.name} 当前状态：{'、'.join(enemy.conditions)}。")
            else:
                notes.append(f"{enemy.name} 的状态没有变化。")
        save_combat(save, state)

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
        # 严格 5e：只有**被打倒**（生命归零）的敌人才结算经验与战利品，
        # 逃跑 / 被擒 / 投降都不算战果。因此这里不记 defeated；
        # 若敌人还活着（hp > 0）却要退场，还要把之前记过的撤掉。
        if enemy.hp > 0:
            forget_defeat(state, enemy)
        notes.append(f"{enemy.name} {'已退场' if enemy.out else '回到战斗'}。")
        if enemy.hp > 0:
            notes.append("（它是自己退场的，按 5e 规则不结算经验与战利品。）")
        if enemy.npc:
            update_npc(save, enemy.npc, status="退场" if enemy.out else "存活")

    if note:
        enemy.note = note
        save_combat(save, state)
        notes.append(f"（{note}）")

    if not notes:
        hp_now, hp_max = effective_hp(save, enemy)
        state_text = combatant_state_text(save, enemy)
        return (
            f"{enemy.name} 状态：{hp_now}/{hp_max}　AC {enemy.ac}"
            + (f"　{state_text}" if state_text else "")
            + (f"　{enemy.note}" if enemy.note else "")
        )
    return "".join(notes)


# --------------------------------------------------------------------------- #
# 移动
# --------------------------------------------------------------------------- #


def _parse_pos(text: str) -> tuple[int, int] | None:
    """解析 ``"4,7"`` / ``"4 7"`` 这样的坐标（**1 基**，与地图上标的一致）。"""
    match = re.fullmatch(r"\s*(\d{1,2})\s*[,，\s]\s*(\d{1,2})\s*", str(text or ""))
    if match is None:
        return None
    return int(match.group(1)) - 1, int(match.group(2)) - 1


def move_combatant(
    save: Save,
    state: CombatState,
    ref: str,
    *,
    target: str = "",
    pos: str = "",
) -> str:
    """让一名参战者移动：靠近某个目标，或走到指定坐标。

    系统按格子算路径与消耗（困难地形一步算两格），并扣掉本回合的移动预算；
    预算用完就走不动了，得等下一回合。
    """
    mover = find_combatant(state, ref)
    if mover is None:
        names = "、".join(c.name for c in state.order)
        return f"没找到「{ref}」。场上：{names}"
    if not has_map(state):
        return "这场战斗没有棋盘（旧战斗存档），移动请直接叙述即可"
    if not has_position(mover):
        return f"{mover.name} 还没有被放到棋盘上，无法移动"

    speed = int(getattr(mover, "speed", 30) or 30)
    used = int(getattr(mover, "move_used", 0) or 0)
    budget = max(0, speed - used)
    if budget <= 0:
        return (
            f"{mover.name} 本回合的移动已经用完（{used}/{speed} 尺），"
            "要挪位置只能等下一回合。"
        )

    dest_ref = str(target or "").strip()
    dest: tuple[int, int] | None = None
    if dest_ref:
        other = find_combatant(state, dest_ref)
        if other is None:
            return f"没找到移动目标「{dest_ref}」"
        if not has_position(other):
            return f"{other.name} 还没有被放到棋盘上"
        if other.cid == mover.cid:
            return "要移动到别处请给出目标或坐标"
        if chebyshev(int(mover.x), int(mover.y), int(other.x), int(other.y)) <= 1:
            # 已经贴身：别再算路径了（贴着目标时「最近的格子」就是原地，会误报无路可走）
            return f"{mover.name} 已经贴到 {other.name} 身边了，不必再移动。"
        dest = (int(other.x), int(other.y))
    elif str(pos or "").strip():
        parsed = _parse_pos(str(pos))
        if parsed is None:
            return f"看不懂坐标「{pos}」，请写成 列,行（例如 5,3；坐标从 1 开始）"
        if not (0 <= parsed[0] < state.width and 0 <= parsed[1] < state.height):
            return f"坐标 {pos} 超出棋盘（{state.width}×{state.height}）"
        occupied = {
            (int(c.x), int(c.y))
            for c in state.order
            if has_position(c) and c.cid != mover.cid and not c.out
        }
        if parsed in occupied:
            return f"坐标 {pos} 上已经有人了，换一格或直接指定目标让他靠近"
        dest = parsed
    else:
        return "请给出要靠近的目标（target）或目标格坐标（pos）"

    start = (int(mover.x), int(mover.y))
    if dest == start:
        return f"{mover.name} 已经在那儿了。"

    blocked = {
        (int(c.x), int(c.y))
        for c in state.order
        if has_position(c) and c.cid != mover.cid and not c.out
    }
    cell, cost, remain = plan_move(state, mover, dest, budget, blocked_cells=blocked)
    if cost <= 0 and cell == start:
        return (
            f"{mover.name} 从 {start[0] + 1},{start[1] + 1} 出发无路可走"
            "（周围被障碍或人堵住了）。"
        )
    # 目标是「人」时贴到相邻格就算到位：别把目标自己站的那一格也算成距离，
    # 否则明明已经贴身了还会提示「还差 5 尺」。
    need = max(0, remain - 1) if dest_ref else remain

    mover.x, mover.y = int(cell[0]), int(cell[1])
    mover.move_used = used + cost
    if cost > 0:
        mover.moved = True
    save_combat(save, state)

    terrain = terrain_at(state, mover.x, mover.y)
    lines = [
        f"【{mover.name}】从 {start[0] + 1},{start[1] + 1} 移动到 "
        f"{mover.x + 1},{mover.y + 1}，走了 {cost} 尺"
        f"（本回合已用 {mover.move_used}/{speed} 尺，剩余 "
        f"{max(0, speed - mover.move_used)} 尺）"
    ]
    if terrain.note and terrain.key != "floor":
        lines.append(f"（脚下是{terrain.cn}：{terrain.note}）")
    if need > 0:
        lines.append(
            f"离目标还差 {need * CELL_FEET} 尺，本回合走不到了——要靠近得等下一回合。"
        )
    else:
        lines.append("已经贴到目标身边。" if dest_ref else "已经走到目标位置。")
    return "\n".join(lines)


def state_tile(state: CombatState, combatant: Combatant):
    """参战者脚下的地形。"""
    return terrain_at(state, combatant.x, combatant.y)


# --------------------------------------------------------------------------- #
# 展示
# --------------------------------------------------------------------------- #


def combat_text(save: Save, state: CombatState, *, with_map: bool = True) -> str:
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
    board = battlefield_text(state, with_map=with_map)
    if board:
        lines.append(board)
    lines.append("先攻顺序：")
    for index, combatant in enumerate(state.order, start=1):
        hp_now, hp_max = effective_hp(save, combatant)
        role = "玩家" if combatant.kind == "player" else "敌人"
        mark = " ← 现在" if current is not None and combatant.cid == current.cid else ""
        status = ""
        state_text = combatant_state_text(save, combatant)
        if state_text:
            status = f"　状态：{state_text}"
        if combatant.kind == "player":
            text = player_status(save, combatant)
            if text:
                status += f"　{text}"
            elif combatant.out:
                status += "　已退场" + (
                    f"（{combatant.note}）" if combatant.note else ""
                )
        elif combatant.out:
            status += "　已退场" + (f"（{combatant.note}）" if combatant.note else "")
        elif hp_now <= 0:
            status += "　已倒下"
        turn_used = ""
        if combatant.kind == "player" and (
            current is None or combatant.cid == current.cid
        ):
            turn_used = f"　本回合已用：{resources_text(combatant)}"
        lines.append(
            f"  {index}. {combatant.initiative:>2} {combatant.name}（{role}）"
            f" HP {hp_now}/{hp_max}　AC {effective_ac(save, combatant, state)}"
            f"{cover_text(state, combatant)}{status}{turn_used}{mark}"
        )
    lines.append(
        "（轮到倒地濒死的玩家时由系统自动掷死亡豁免：DC 10，3 次成功伤势稳定、3 次失败死亡；"
        "倒地期间挨打会累计失败次数，任何治疗都能让他恢复意识）"
    )
    return "\n".join(lines)
