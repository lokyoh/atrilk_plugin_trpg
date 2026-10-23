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
import random
import re
import time
from collections import Counter
from collections.abc import Iterable
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
    distance_feet,
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
from .environment import underwater_disadvantage
from .equipment import get_weapon, parse_item_stack
from .monster_rules import (
    DeathBurst,
    attack_advantage_of,
    blocked_conditions,
    rules_of,
)
from .monsters import Monster
from .npcs import (
    build_npc,
    is_npc_key,
    npc_xp,
    register_encounter,
    resolve_monster,
    roster_hp,
    sync_combatant,
    sync_roster_after_combat,
    update_npc,
)
from .progression import xp_for_cr
from .save_store import Save, get_character, read_scene, save_dir, set_character
from .threat import apply_arena_terrain, pick_boss, target_focus

__all__ = [
    "COMBAT_FILENAME",
    "MAX_ENEMIES",
    "TURN_RESOURCES",
    "CombatState",
    "Combatant",
    "EnemySpec",
    "advance_turn",
    "all_enemies_out",
    "apply_condition",
    "apply_damage",
    "apply_threat_profile",
    "attack",
    "auto_crit_on_hit",
    "auto_enemy_turns",
    "can_act",
    "can_target",
    "clear_combat",
    "combat_path",
    "combat_text",
    "combatant_conditions",
    "combatant_exhaustion",
    "combatant_state_text",
    "count_legendary_use",
    "cover_text",
    "current_actor",
    "effective_ac",
    "effective_hp",
    "enemy_attack",
    "enemy_attack_count",
    "enemy_target",
    "find_combatant",
    "forget_defeat",
    "heal_combatant",
    "is_dead",
    "is_dying",
    "is_out",
    "legendary_available",
    "load_combat",
    "move_combatant",
    "parse_enemy_list",
    "party_level",
    "peek_following",
    "player_status",
    "record_defeat",
    "reinforcement_pending",
    "resource_of",
    "resources_text",
    "roll_damage",
    "save_combat",
    "start_combat",
    "summon_reinforcements",
    "threat_focus_name",
    "threat_text",
    "tick_dot",
    "tick_regeneration",
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
    #: **再生被打断**：本轮挨了火焰 / 强酸（见数据卡 ``rules.regeneration`` 的
    #: ``blocked_by``），到它下次行动前不再生。新一轮自动清除。
    regen_suppressed: bool = False
    #: 已经用掉的**传奇抗性**次数（豁免失败改成功，每日有上限）
    legendary_resistance_used: int = 0
    #: 体型（中型 / 小型 / 大型……）。**重型武器**对小型攻击者有劣势，
    #: 擒抱 / 推撞的体型限制也看它。
    size: str = "中型"
    #: 它**在第几轮已经行动过**（0 = 还没）。系统替敌人自动结算时先看这个，
    #: 免得同一轮既被 DM 手动打了一次、又被自动再打一次。
    acted_round: int = 0
    #: 法术 / 效果挂上的状态还剩几轮：``{状态名: 轮数}``。
    #: 只记**会自动到期**的那些（法术数据里的 ``condition_rounds``）；
    #: 没有记的状态不会自动消失（魅惑这类以小时计，交给剧情）。
    condition_rounds: dict[str, int] = field(default_factory=dict)

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
            "regen_suppressed": self.regen_suppressed,
            "legendary_resistance_used": self.legendary_resistance_used,
            "size": self.size,
            "acted_round": self.acted_round,
            "condition_rounds": dict(self.condition_rounds),
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
            regen_suppressed=bool(data.get("regen_suppressed", False)),
            legendary_resistance_used=max(
                0, _int_or(data.get("legendary_resistance_used"), 0)
            ),
            size=str(data.get("size", "中型") or "中型"),
            acted_round=max(0, _int_or(data.get("acted_round"), 0)),
            condition_rounds={
                str(name): max(0, _int_or(rounds, 0))
                for name, rounds in (data.get("condition_rounds") or {}).items()
                if str(name).strip()
            },
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
    #: —— 威胁层级（见 :mod:`.threat`）——
    #: 生效的档位标签（``elite`` / ``attrition`` / ``legendary`` / ``rulebreak``）
    threat_tags: list[str] = field(default_factory=list)
    #: 专攻目标的 cid（elite 档）：敌方优先打他
    threat_focus: str = ""
    #: 首领的 cid（legendary 档）：每轮可以多一次行动
    threat_boss: str = ""
    #: 传奇行动计数所属的轮次（轮次变了就清零重数）
    legendary_round: int = 0
    #: 首领本轮的攻击次数：``cid -> 次数``。第二次即视为传奇行动（不占动作）
    legendary_counts: dict[str, int] = field(default_factory=dict)
    #: 增援计划（attrition 档）：``{"count","target_cr","label","cr_text"}``
    threat_reinforcement: dict[str, Any] = field(default_factory=dict)
    #: 增援已经放出来了几波
    reinforcement_waves: int = 0
    #: 场地规则键（rulebreak 档，见 :data:`~.threat.ARENA_RULES`）
    arena_rule: str = ""
    #: 场地规则对掩体的修正（负数 = 掩体变差）。存在状态上，
    #: 这样 :func:`~.battlemap.cover_bonus` 不用反向依赖 :mod:`.threat`
    arena_cover_delta: int = 0
    #: 这里有没有**日光**：``True`` 有 / ``False`` 没有 / ``None`` 不知道。
    #: 只影响「日照敏感」的怪；不知道时 :func:`enemy_attack` 会提示 DM 自己判。
    daylight: bool | None = None
    #: 本场战斗耗掉的弹药：``{"玩家cid:箭": 3}``。战后按 5e 回收一半
    #: （花一分钟搜 battlefield），见 :func:`recover_ammo`。
    ammo_used: dict[str, int] = field(default_factory=dict)

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
            "threat_tags": list(self.threat_tags),
            "threat_focus": self.threat_focus,
            "threat_boss": self.threat_boss,
            "legendary_round": self.legendary_round,
            "legendary_counts": {
                str(k): int(v) for k, v in (self.legendary_counts or {}).items()
            },
            "threat_reinforcement": dict(self.threat_reinforcement),
            "reinforcement_waves": self.reinforcement_waves,
            "arena_rule": self.arena_rule,
            "arena_cover_delta": self.arena_cover_delta,
            # 三态（有 / 没有 / 不知道）：不知道就不写这个键
            **({} if self.daylight is None else {"daylight": bool(self.daylight)}),
            "ammo_used": {
                str(k): int(v) for k, v in (self.ammo_used or {}).items() if int(v) > 0
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CombatState:
        order = data.get("order")
        tiles = data.get("tiles")
        reinforcement = data.get("threat_reinforcement")
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
            threat_tags=[str(x) for x in (data.get("threat_tags") or []) if str(x)],
            threat_focus=str(data.get("threat_focus", "") or ""),
            threat_boss=str(data.get("threat_boss", "") or ""),
            legendary_round=_int_or(data.get("legendary_round"), 0),
            legendary_counts={
                str(k): int(v)
                for k, v in (data.get("legendary_counts") or {}).items()
                if str(k)
            },
            threat_reinforcement=(
                {str(k): v for k, v in reinforcement.items()}
                if isinstance(reinforcement, dict)
                else {}
            ),
            reinforcement_waves=_int_or(data.get("reinforcement_waves"), 0),
            arena_rule=str(data.get("arena_rule", "") or ""),
            arena_cover_delta=_int_or(data.get("arena_cover_delta"), 0),
            daylight=(
                None if data.get("daylight") is None else bool(data.get("daylight"))
            ),
            ammo_used={
                str(k): max(0, int(v)) for k, v in (data.get("ammo_used") or {}).items()
            },
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
    """结束战斗并删除战斗状态；原本不在战斗中返回 ``False``。

    **收尾顺手把名单对齐**（:func:`~.npcs.sync_roster_after_combat`）：
    战斗期间 DM 不能动名单，血量与结局都由这里记；不趁这一刻回填，
    战后名单就永远停在开战前的样子（实测出现过「名单 15/22 存活、战场上一只都没了」）。
    同步失败只记日志——它不该拖住「战斗已经结束」这件事。
    """
    path = combat_path(save.save_id)
    if not path.is_file():
        return False
    state = load_combat(save)  # 先读：删掉就没得同步了
    if state is not None:
        try:
            sync_roster_after_combat(save, state)
        except Exception as e:
            log.warning(f"战斗结束后同步 NPC 名单失败：{str_traceback(e)}")
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


def apply_condition(
    save: Save, combatant: Combatant, names: Iterable[str], *, rounds: int = 0
) -> str:
    """给参战者挂状态（敌我通用），返回一句给 DM 的说明；没挂上返回空串。

    - **玩家**：写进角色卡（``card.conditions``），战斗外也照样生效；
    - **敌人**：写进 ``Combatant.conditions``——状态只活这一场，战后不回填名单；
    - ``rounds > 0`` 时记进 ``condition_rounds``，在**它自己的回合开始**时递减，
      归零自动摘掉（控制法术的「撑几轮」就在这儿兑现）。

    状态名先规范化（「俯伏」→ 倒地、「被魅惑」→ 魅惑），免疫的系统挡得住
    （骷髅免疫中毒、幽魂免疫倒地……挂了也白挂）。
    """
    wanted = [name for name in normalize_conditions(list(names)) if name]
    if not wanted:
        return ""
    if combatant.is_enemy:
        blocked = blocked_conditions(
            rules_of(resolve_monster(combatant.monster)), wanted
        )
        wanted = [name for name in wanted if name not in blocked]
        if not wanted:
            return f"{combatant.name} 免疫这些状态，没挂上。"

    current = combatant_conditions(save, combatant)
    merged = list(dict.fromkeys([*current, *wanted]))
    if combatant.is_enemy:
        combatant.conditions = merged
    else:
        card = get_character(save, combatant.cid)
        if card is None:
            return ""
        card.conditions = merged
        set_character(save, card)
    if rounds > 0:
        for name in wanted:
            combatant.condition_rounds[name] = rounds

    state_text = f"陷入「{'、'.join(wanted)}」"
    if rounds > 0:
        state_text += f"（{rounds} 轮后自动解除）"
    return state_text


def _tick_condition_timers(save: Save, combatant: Combatant) -> list[str]:
    """轮到它的回合：计时状态递减，到期的摘掉（返回给 DM 的说明）。

    只处理 ``condition_rounds`` 里记过的；没记的（魅惑这类以小时计）不会自动消失，
    由剧情或 DM 用 update_character 收尾。
    """
    if not combatant.condition_rounds:
        return []
    lines: list[str] = []
    for name in list(combatant.condition_rounds):
        left = combatant.condition_rounds[name] - 1
        if left > 0:
            combatant.condition_rounds[name] = left
            continue
        combatant.condition_rounds.pop(name, None)
        current = combatant_conditions(save, combatant)
        if name not in current:
            continue
        remaining = [item for item in current if item != name]
        if combatant.is_enemy:
            combatant.conditions = remaining
        else:
            card = get_character(save, combatant.cid)
            if card is not None:
                card.conditions = remaining
                set_character(save, card)
        lines.append(f"⌛ {combatant.name} 的「{name}」已结束")
    return lines


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


def tick_dot(
    save: Save,
    state: CombatState,
    combatant: Combatant,
    *,
    rng: random.Random | None = None,
) -> str:
    """回合开始时结算持续伤害（中毒等），返回给 DM 的一句话。

    5e 里中毒是「**每个自己的回合开始时**失去等于中毒等级的生命」，本该自动
    结算。从前只能靠 DM 每轮手动调一次 ``update_enemy``——漏一轮就少扣血，
    多扣一次就误伤。现在挂在回合推进里，按时点自动扣。

    扣到 0 血按规则走倒地濒死（并掷死亡豁免），不是简单地少一格血。

    :param rng: 死亡豁免用的随机源。留空用全局随机——**测试要传一个固定的**，
        否则掷出天然 20 会回 1 血脱离濒死，让「扣到 0 就倒地」这条断言时过时不过。
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
        outcome = card.death_save(rng=rng)
        set_character(save, card)
        note += f"；随即{outcome.describe()}"
    return f"🔥 {combatant.name} 受到持续伤害（{damage_type}）：{note}"


def tick_regeneration(
    save: Save, state: CombatState, combatant: Combatant, *, rng: Any = None
) -> str:
    """**再生**：轮到它行动时按数据卡回一点血（巨魔 15、断肢 5……）。

    条件照书上来：还有气（0 血不回）、没满血、本轮没挨过打断再生的那类伤害
    （火焰 / 强酸——巨魔的再生就怕这两个）。

    与 :func:`tick_dot` 一样挂在回合推进里：靠 DM 每轮手动调一次 ``update_enemy``
    必然会漏，漏一轮就是白掉十几点血。
    """
    monster = resolve_monster(combatant.monster)
    rules = rules_of(monster)
    if not rules.regeneration:
        return ""
    if combatant.hp <= 0 or combatant.hp >= combatant.max_hp:
        return ""
    if combatant.regen_suppressed:
        blocked = "、".join(rules.regeneration_blocked_by) or "火焰 / 强酸"
        return f"🩸 {combatant.name} 本轮挨过{blocked}，再生不生效"
    amount = min(rules.regeneration, combatant.max_hp - combatant.hp)
    combatant.hp += amount
    save_combat(save, state)
    sync_combatant(save, combatant)
    return f"🩸 {combatant.name} 再生 {amount}（{combatant.hp}/{combatant.max_hp}）"


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
            # 计时状态（定身 / 目盲……）在它自己的回合开始递减——
            # 必须排在 can_act 之前，否则被「麻痹」定住的怪永远轮不到、
            # 这个状态也就永远摘不掉。
            if commit:
                state.pending.extend(_tick_condition_timers(save, combatant))
            if not can_act(save, combatant):
                # 失能类状态（震慑 / 麻痹 / 昏迷……）：人还在场上（照样能被打），
                # 但这一动轮空——由 DM 在叙述里带一句
                if commit:
                    names = "、".join(combatant_conditions(save, combatant))
                    state.pending.append(
                        f"⏳ {combatant.name} 处于{names or '失能'}，本轮无法行动"
                    )
                continue
            if commit:
                regen = tick_regeneration(save, state, combatant)
                if regen:
                    state.pending.append(regen)
            enemies.append(combatant)
            continue
        # —— 玩家：回合开始先结算持续伤害（中毒等），再走倒地濒死 / 跳过 ——
        if commit:
            state.pending.extend(_tick_condition_timers(save, combatant))
        if commit and can_act(save, combatant):
            dot = tick_dot(save, state, combatant, rng=rng)
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
                        other.regen_suppressed = False
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


def _random_pick(token: str, *, rng: Any = None) -> tuple[Monster, int] | None:
    """``随机 幽暗地域 亡灵x3`` → ``(抽到的怪, 数量)``；挑不出返回 ``None``。

    只管挑得出，**不管强度**——按队伍等级缩放到目标 CR 是 :mod:`.encounter`
    在开战时统一做的（``calibrate_specs``）。
    """
    from .monster_index import parse_query, random_monster

    cond = parse_query(token[len("随机") :].strip())
    monster = random_monster(
        kind=str(cond.get("kind", "")),
        habitat=str(cond.get("habitat", "")),
        group=str(cond.get("group", "")),
        cr=cond.get("cr"),  # type: ignore[arg-type]
        rng=rng,
    )
    if monster is None:
        return None
    return monster, max(1, int(cond.get("count", 1) or 1))


def parse_enemy_list(
    text: str, *, level: int = 1, rng: Any = None
) -> tuple[list[EnemySpec], list[str]]:
    """把 DM 写的敌人清单解析成 :class:`EnemySpec` 列表。

    支持：

    - 怪物名 / 档位名：``地精x3`` / ``地精×3`` / ``3只地精`` / ``地精 3`` / ``地精``
    - **具名 NPC**：``黑心商人卡尔:老兵``（档位按队伍等级给数值）、
      ``卡尔:匪帮首领``（直接用怪物表里的数据块）；名字后可带数量 ``打手x2:士兵``
    - **随机**：``随机 幽暗地域 亡灵x3`` ——DM 不点名，按《怪物图鉴》的四套分类
      （类型 / 栖息地 / 群组 / 挑战等级）掷一只；抽不到就报「没认出来」

    多个敌人用 ``、`` 等分隔。

    :param level: 队伍等级，决定档位 NPC 的数值。
    :param rng: 随机源（随机抽怪用）；留空用系统随机。
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

        # —— 随机：按图鉴分类抽一只（同种若干只）——
        if token.startswith("随机"):
            picked = _random_pick(token, rng=rng)
            if picked is None:
                if token not in unknown:
                    unknown.append(token)
                continue
            monster, count = picked
            room = _room()
            if room <= 0:
                break
            count = max(1, min(count, room))
            specs.append(EnemySpec(monster, count))
            total += count
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


def _parse_daylight(text: str) -> bool | None:
    """``是`` / ``否`` → 有没有日光；留空或看不懂 = 不知道（``None``）。"""
    value = str(text or "").strip()
    if value in {"是", "有", "有日光", "白天", "户外", "yes", "true", "1"}:
        return True
    if value in {"否", "无", "没有", "夜", "夜晚", "室内", "地下", "no", "false", "0"}:
        return False
    return None


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
    daylight: str = "",
    rng: Any = None,
) -> CombatState:
    """按敌人清单与存档里的玩家开战：掷先攻、排顺序并落盘。

    :param specs: :func:`parse_enemy_list` 的结果（也接受旧的 ``(怪物, 数量)`` 元组）。
    :param current: 当前回合从哪名玩家开始（通常是发起这场冲突的玩家）。
    :param daylight: 这里有没有**日光**（``是`` / ``否``）；留空表示不知道。
        只影响「日照敏感」那批怪（狗头人、卓尔……）：有日光时它们的攻击带劣势。
        ⚠️ 不按时钟猜——地牢深处也是白天，猜错了就是凭空给它们扣劣势。
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
                size=(card.race_obj.size if card.race_obj else "中型"),
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
                    size=str(getattr(spec.monster, "size", "中型") or "中型"),
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
        scene=save.current_scene,
        note=str(note or ""),
        round=1,
        turn=turn,
        order=order,
        daylight=_parse_daylight(daylight),
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
# 威胁层级
# --------------------------------------------------------------------------- #


def apply_threat_profile(
    state: CombatState,
    profile: Any,
    *,
    rng: Any = None,
) -> str:
    """把威胁剖面落到这场战斗上，返回给 DM 看的一行提示（没有威胁时返回空串）。

    这一步是**规则落地**，不是提示：档位一旦生效，它就会改变战场本身
    （专攻目标、增援计划、首领、场地地形），后续结算照着算。

    :param state: 战斗状态（就地修改）。
    :param profile: :class:`~.threat.ThreatProfile`。
    """
    if profile is None or not getattr(profile, "active", False):
        return ""

    state.threat_tags = list(profile.tags)

    # —— elite：专攻目标 ——
    if profile.has("elite"):
        focus = str(profile.focus_cid or "")
        if not focus:
            focus = target_focus([c for c in state.order if c.kind == "player"])
        state.threat_focus = focus

    # —— legendary：首领 ——
    if profile.has("legendary"):
        boss = str(profile.boss_cid or "")
        if not boss:
            boss = pick_boss([c for c in state.order if c.is_enemy], resolve_monster)
        state.threat_boss = boss
        state.legendary_round = 0
        state.legendary_counts = {}

    # —— attrition：增援计划 ——
    if profile.has("attrition"):
        state.threat_reinforcement = dict(profile.reinforcement or {})
        state.reinforcement_waves = 0

    # —— rulebreak：场地规则 ——
    if profile.has("rulebreak") and profile.arena is not None:
        state.arena_rule = profile.arena.key
        state.arena_cover_delta = int(profile.arena.cover_delta or 0)
        if profile.arena.terrain and state.width and state.height:
            apply_arena_terrain(
                state.tiles,
                state.width,
                state.height,
                profile.arena,
                occupied=[(c.x, c.y) for c in state.order if c.x >= 0 and c.y >= 0],
                rng=rng,
            )

    lines = profile.dm_lines()
    return "\n".join(lines)


def threat_focus_name(state: CombatState) -> str:
    """专攻目标的显示名（没有专攻目标时返回空串）。"""
    cid = str(getattr(state, "threat_focus", "") or "")
    if not cid:
        return ""
    for combatant in state.order:
        if combatant.cid == cid:
            return combatant.name
    return ""


def _legendary_count(state: CombatState, combatant: Combatant) -> int:
    """首领本轮已经攻击了几次（不是首领恒为 0）。轮次变了自动重数。"""
    boss = str(getattr(state, "threat_boss", "") or "")
    if not boss or combatant.cid != boss:
        return 0
    if int(getattr(state, "legendary_round", 0) or 0) != int(state.round):
        return 0
    return int((state.legendary_counts or {}).get(combatant.cid, 0))


def _count_legendary(state: CombatState, combatant: Combatant) -> bool:
    """记一次首领的攻击，返回它是不是传奇行动（本轮第二次起算）。

    传奇行动**不占动作**，所以系统不扣首领的动作资源，只记次数。
    """
    boss = str(getattr(state, "threat_boss", "") or "")
    if not boss or combatant.cid != boss:
        return False
    if int(getattr(state, "legendary_round", 0) or 0) != int(state.round):
        state.legendary_round = int(state.round)
        state.legendary_counts = {}
    counts = state.legendary_counts or {}
    used = int(counts.get(combatant.cid, 0)) + 1
    counts[combatant.cid] = used
    state.legendary_counts = counts
    return used >= 2


def legendary_available(state: CombatState, combatant: Combatant) -> bool:
    """这只敌人本轮还能不能发动传奇行动。

    口径：只有首领能，且**每轮一次**——也就是本轮攻击次数还没到 2 次。
    """
    boss = str(getattr(state, "threat_boss", "") or "")
    if not boss or combatant.cid != boss:
        return False
    return _legendary_count(state, combatant) < 2


def count_legendary_use(state: CombatState, combatant: Combatant) -> bool:
    """记一次首领的攻击，返回它是不是传奇行动（第二次及以后算）。

    保留给外部调用；:func:`~.enemy_attack` 内部用的是先查后数的两段式，
    因为它需要在上限被突破时**拦下**而不是只记账。
    """
    return _count_legendary(state, combatant)


def reinforcement_pending(state: CombatState) -> dict[str, Any]:
    """还没放出来的增援计划（没有则返回空 dict）。"""
    if "attrition" not in (getattr(state, "threat_tags", []) or []):
        return {}
    plan = dict(getattr(state, "threat_reinforcement", {}) or {})
    if not plan:
        return {}
    if int(getattr(state, "reinforcement_waves", 0) or 0) > 0:
        return {}
    return plan


def summon_reinforcements(
    save: Save,
    state: CombatState,
    *,
    rng: Any = None,
) -> str:
    """放出一波增援（attrition 档），返回给 DM 看的说明（没有增援时返回空串）。

    **触发时机**：原敌人被打到只剩一半以下时——这时放增援最吓人，
    也最符合「每场战斗结束前追加一波」的意思（战斗快结束时压力最大）。
    一场战斗只放一波。

    增援是**打折的杂兵**（CR = 队伍平均等级 − 2），不是新的主力：
    它的作用是拖时间与消耗资源，不是提高单场难度上限。

    :return: 放出了就返回一段说明（含新敌人的名字与数量），没放返回空串。
    """
    plan = reinforcement_pending(state)
    if not plan:
        return ""

    enemies = [c for c in state.order if c.is_enemy]
    living = [c for c in enemies if not c.out and c.hp > 0]
    # 只在原敌人被清掉一半以上时放增援
    if len(living) * 2 > len(enemies):
        return ""

    count = max(1, int(plan.get("count", 0) or 0))
    target_cr = float(plan.get("target_cr", 1.0) or 1.0)
    label = str(plan.get("label", "") or "增援")

    # 从怪物表里挑最接近目标 CR 的杂兵
    from .monsters import MONSTERS, scale_monster

    candidates = sorted(MONSTERS, key=lambda m: (abs(m.cr - target_cr), m.cr))
    if not candidates:
        return ""
    base = candidates[0]
    monster = scale_monster(base, target_cr) or base

    # 名字：沿用场上已有的同名计数，避免撞名
    counters: Counter[str] = Counter()
    for combatant in state.order:
        if combatant.is_enemy:
            counters[combatant.name] += 1
    added: list[str] = []
    for _ in range(min(count, MAX_ENEMIES - len(enemies))):
        counters[base.cn] += 1
        index = counters[base.cn]
        name = f"{base.cn}{index}" if index > 1 else base.cn
        hp = max(1, roll(monster.hit_dice, rng=rng).total)
        state.order.append(
            Combatant(
                cid=f"{monster.key}#r{index}",
                kind="enemy",
                name=name,
                initiative=roll_d20(rng=rng)[1] + monster.initiative,
                ac=monster.ac,
                hp=hp,
                max_hp=hp,
                monster=monster.key,
                speed=int(getattr(monster, "speed", 30) or 30),
            )
        )
        added.append(name)

    if not added:
        return ""
    state.reinforcement_waves = 1
    save_combat(save, state)
    return (
        f"增援到了：{label} {len(added)} 只（{'、'.join(added)}，"
        f"CR {plan.get('cr_text', '')}）从战场边缘涌了进来。"
    )


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
    magical: bool = False,
    burst: bool = False,
    rng: Any = None,
) -> str:
    """对参战者造成伤害并落盘。

    - **抗性结算**：按目标（玩家 = 角色卡抗性 + 状态抗性；敌人 = 怪物表抗性 +
      生物类型默认抗性）过一遍伤害类型，抗性减半 / 免疫归零 / 易伤翻倍；
      ``magical`` 决定「非魔法武器」那类抗性生不生效（+1 武器打得动石像鬼）；
    - 敌人：改 ``Combatant.hp``（随 ``combat.json`` 一起保存）；归零时先看
      **不死坚韧**（骷髅 / 丧尸：体质豁免过了就留 1 血），再看**自爆**
      （见 :func:`_death_burst`）；
    - 玩家：先扣临时生命，再扣角色卡的生命值并写回存档；生命归零转**倒地濒危**，
      倒地后继续挨打会累计死亡豁免失败次数（暴击 2 次；单次伤害达到生命上限即死）；
    - **专注**：玩家受伤后若正在维持法术，自动掷体质豁免（DC = max(10, 伤害//2)）。

    :param burst: 本次伤害是不是某次自爆的连带伤害——防止两只自爆怪互相引爆
        时无限递归（自爆不再触发自爆）。
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
        incoming, res_note = card.apply_resistance(
            incoming, damage_type, magical=magical
        )
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
    rules = rules_of(monster)
    resistances = monster.all_resistances if monster is not None else ()
    amount, res_note = resolve_damage(amount, damage_type, resistances, magical=magical)
    target.hp = max(0, target.hp - amount)
    note = f"{target.name} 剩余 {target.hp}/{target.max_hp}"
    if res_note:
        note += f"（{res_note}）"
    # 再生被打断（巨魔挨了火焰 / 强酸，下个回合不再生）
    if damage_type and damage_type in rules.regeneration_blocked_by:
        target.regen_suppressed = True
    if target.hp <= 0:
        # 不死坚韧：非光耀、非暴击的伤害先给一次体质豁免，过了就留 1 血
        if rules.undead_fortitude and damage_type != "光耀" and not crit:
            dc = 5 + amount
            if monster is not None:
                result = roll_check(
                    monster.save_bonus("con"),
                    dc,
                    kind="不死坚韧（体质豁免）",
                    rng=rng,
                )
            else:
                result = None
            if result is not None and result.success:
                target.hp = 1
                note += f"，但{result.describe()} → 不死坚韧，留在 1 血"
                save_combat(save, state)
                sync_combatant(save, target)
                return note + "。"
        target.out = True
        record_defeat(state, target)
        note += "，已倒下"
        if rules.death_burst is not None and not burst:
            note += "\n" + _death_burst(save, state, target, rules.death_burst, rng=rng)
    save_combat(save, state)
    sync_combatant(save, target)
    # 自爆那段自己带句号（最后一行是别人的结算回执），别再叠一个
    return note if note.endswith("。") else note + "。"


def _allies_within(state: CombatState, enemy: Combatant, target: Combatant) -> bool:
    """**集群战术**的判据：目标 5 尺内有没有它**未失能的同类**盟友。

    有棋盘按格子算（5 尺 = 相邻一格）；没有棋盘时退一步——场上还有别的同种
    敌人活着且能行动，就算围上来了（三只狗头人通常是一起扑上来的）。
    """
    same_kind = (
        other
        for other in state.order
        if other.cid != enemy.cid
        and other.is_enemy
        and not other.out
        and other.monster == enemy.monster
        and not is_incapacitated(other.conditions)
    )
    if has_map(state) and has_position(enemy) and has_position(target):
        return any(
            has_position(other)
            and chebyshev(int(other.x), int(other.y), int(target.x), int(target.y)) <= 1
            for other in same_kind
        )
    return any(True for _ in same_kind)


def _death_burst(
    save: Save,
    state: CombatState,
    enemy: Combatant,
    burst: DeathBurst,
    *,
    rng: Any = None,
) -> str:
    """自爆（``死亡时爆炸``）：给半径内的每人掷一次豁免，失败吃伤害、成功半伤。

    图鉴里这类特性结构统一（「敏捷豁免 DC 11，源自它的 5 尺内的每名生物，失败：
    7（2d6）火焰伤害，成功：半伤」），所以能自动算；伤害为空的那种（泥魔蝠：失败
    只是陷入束缚）只把规则原文交给 DM。

    没有棋盘时**不猜**——半径是几何概念，站在谁旁边得看地图。
    """
    save_cn = burst.save_cn
    lines = [
        f"💥 {enemy.name} 死亡时爆炸：{burst.radius} 尺内每名生物 "
        f"{save_cn}豁免 DC {burst.dc}"
    ]
    if not burst.has_damage:
        lines.append("　（失败的效果不是掉血，按特性正文叙述）")
        return "\n".join(lines)
    if not has_map(state) or not has_position(enemy):
        lines.append(
            f"　（这场战斗没有棋盘，谁在 {burst.radius} 尺内由你判定；"
            f"失败 {burst.damage} {burst.damage_type}伤害，成功半伤）"
        )
        return "\n".join(lines)

    cells = max(1, burst.radius // CELL_FEET)
    for other in list(state.order):
        if other.cid == enemy.cid or is_out(save, other) or not has_position(other):
            continue
        if chebyshev(int(enemy.x), int(enemy.y), int(other.x), int(other.y)) > cells:
            continue
        if other.kind == "player":
            card = get_character(save, other.cid)
            result = card.check_save(burst.save, burst.dc, rng=rng) if card else None
        else:
            monster = resolve_monster(other.monster)
            result = (
                roll_check(
                    monster.save_bonus(burst.save),
                    burst.dc,
                    kind=f"{other.name}的{save_cn}豁免",
                    rng=rng,
                )
                if monster is not None
                else None
            )
        if result is None:
            lines.append(f"　· {other.name}：没有数据可查豁免，由你判定")
            continue
        if result.success:
            lines.append(f"　· {other.name}：{result.describe()} → 成功，半伤")
            damage, _detail = roll_damage(burst.damage, rng=rng)
            damage = damage // 2
        else:
            lines.append(f"　· {other.name}：{result.describe()} → 失败")
            damage, _detail = roll_damage(burst.damage, rng=rng)
        if damage > 0:
            lines.append(
                "　　"
                + apply_damage(
                    save,
                    state,
                    other,
                    damage,
                    damage_type=burst.damage_type,
                    burst=True,
                    rng=rng,
                )
            )
    return "\n".join(lines)


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

    if distance < 0:
        # 不知道距离（没铺棋盘 / 没落子）：保守地用近战——剧情模式常见，
        # DM 说「我砍他」时多半就是近战。
        chosen = melee[0] if melee else ranged[0]
    elif melee and distance <= _reach(melee[0]):
        # 够得着才用近战。**触及按具体武器算**：长剑只有 5 尺，10 尺处
        # 拿着它就伸不到——写死成 10 会让这段式子选错武器。
        chosen = melee[0]
    else:
        # 够不着：能远程就远程，否则仍用近战（马上会被射程校验打回，
        # 那时 DM 会知道应该移动过去）
        chosen = ranged[0] if ranged else melee[0] if melee else weapons[0]

    return chosen, f"自动选用的武器：{chosen.cn}（{_weapon_brief(chosen)}）"


#: 玩家说「我用拳头打」时的写法；这些是真要徒手，不算「没认出武器」。
_UNARMED_WORDS = frozenset(
    {"徒手", "拳头", "空手", "赤手", "拳", "掌", "unarmed", "fist", "punch"}
)


def _reach(weapon) -> int:
    """近战触及范围（有「触及」属性的武器是 10 尺，其余 5 尺）。"""
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
    if getattr(weapon, "is_thrown", False):
        parts.append(f"可投掷 {weapon.thrown_normal}/{weapon.thrown_max} 尺")
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
    two_handed: bool = False,
    off_hand: bool = False,
    rng: Any = None,
) -> tuple[str, bool]:
    """玩家攻击：自动掷命中、掷伤害并扣血。

    武器词条都在这一层结算（对照《快速参考 (5e_2014).md》武器词条）：

    - **投掷**：够不着时按投掷射程打远程（匕首 20/60、标枪 30/120……）；
    - **弹药**：弓 / 弩 / 吹箭筒 / 投石索每次攻击耗一枚，战后回收一半；
    - **两用**：``two_handed=True`` 时伤害取双手骰（长剑 1d10）；
    - **重型**：小型攻击者（半身人 / 侏儒）用重型武器有劣势；
    - **骑枪**：打 5 尺内的目标有劣势；
    - **双武器**：``off_hand=True`` 时副手攻击不加大幅值（5e 双武器战斗）；
    - **5 尺内远程劣势**：身边 5 尺内有敌人时远程攻击带劣势；
    - **捕网**：不造成伤害，命中即束缚；
    - **临时武器**：远程武器贴身肉搏、近战武器硬投，都是 1d4。

    :param two_handed: 两用武器是否双手持用。
    :param off_hand: 副手攻击（双武器战斗）：伤害不加大幅值。
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
    elif weapon is None and weapon_ref:
        # 指定了武器却查不到（卡上没有 / 名字对不上）：从前一声不响按徒手算，
        # DM 看到回执里的「徒手」也不明所以，照样写「剑锋劈下」——
        # 玩家说的武器与结算的武器对不上。这里要么明说「改成哪件」，
        # 要么明说「只能徒手」，让 DM 有得可写。
        if str(weapon_ref).strip().lower() in _UNARMED_WORDS:
            pass  # 玩家本就是要赤手空拳
        else:
            fallback, note = _pick_weapon(save, state, card, attacker, target)
            if fallback is not None:
                weapon = fallback
                pick_note = (
                    f"没认出武器「{weapon_ref}」"
                    f"（{card.name} 的装备里也没有），已改用 {fallback.cn}；"
                    f"{note}"
                )
            else:
                pick_note = (
                    f"没认出武器「{weapon_ref}」，{card.name} 身上也没有别的武器，"
                    "按徒手结算"
                )

    # 指定了武器却不在角色卡装备里：照算，但提醒 DM（临时拾取 / 借用要有说法）
    warn = ""
    if pick_note:
        warn = f"（{pick_note}）\n"
    if weapon is not None and weapon_ref:
        owned = {item.key for item in card.primary_weapons()}
        if weapon.key not in owned:
            warn += f"（注意：{weapon.cn} 不在 {card.name} 的装备里）\n"

    # —— 先量距离，再定攻击方式：近战 / 远程 / 投掷 / 临时武器 ——
    reach = 10 if (weapon is not None and weapon.is_reach) else 5
    distance = range_check(state, attacker, target, reach=reach).distance
    beyond = distance < 0 or distance > reach  # 没棋盘时按「够不着」处理
    ranged_attack = False
    improvised = False
    normal = max_range = 0
    if weapon is not None:
        if beyond:
            if weapon.is_ranged:
                ranged_attack = True
                normal, max_range = weapon.ranged_normal, weapon.ranged_max
            elif weapon.is_thrown:
                ranged_attack = True
                normal, max_range = weapon.thrown_normal, weapon.thrown_max
                warn += f"（投掷{weapon.cn}，射程 {normal}/{max_range}）\n"
            else:
                # 近战武器没有投掷属性却够不着：按临时投掷武器（1d4，20/60）
                improvised = True
                normal, max_range = 20, 60
                warn += f"（{weapon.cn}没有投掷属性，当临时武器扔出去：1d4）\n"
        elif weapon.is_ranged and not weapon.is_thrown:
            # 弹药武器贴身肉搏 = 临时武器（1d4）
            improvised = True
            warn += f"（{weapon.cn}当临时武器抡：1d4）\n"

    gate = range_check(
        state, attacker, target, reach=reach, normal=normal, max_range=max_range
    )
    if not gate.ok:
        return f"{warn}距离不足：{gate.reason}", False
    if gate.disadvantage and advantage is not Advantage.DISADVANTAGE:
        advantage = Advantage.DISADVANTAGE
        warn += "（目标超出常规射程，本次攻击带劣势）\n"
    distance = gate.distance
    is_melee = not ranged_attack

    # —— 5 尺内远程劣势：身边 5 尺内有敌人时，远程攻击带劣势（5e）——
    if ranged_attack and enemy_within(state, attacker, 5):
        advantage = combine_advantage(advantage, Advantage.DISADVANTAGE)
        warn += "（身边 5 尺内有敌人，远程攻击带劣势）\n"

    # —— 水下战斗：攻击者站在浅水（water）地形上，武器攻击受限（5e2014）——
    if (
        has_map(state)
        and has_position(attacker)
        and terrain_at(state, attacker.x, attacker.y).key == "water"
    ):
        _has_swim = bool(card.swim_speed)
        if ranged_attack:
            if weapon is not None and distance > normal:
                return (
                    f"{warn}水下远程攻击：目标超出常规射程，水阻力让远射直接落空（未命中）。",
                    False,
                )
            if weapon is not None and underwater_disadvantage(
                "ranged", weapon.key, _has_swim
            ):
                advantage = combine_advantage(advantage, Advantage.DISADVANTAGE)
                warn += "（水下远程攻击带劣势：除非用弩 / 捕网 / 标枪类投掷武器）\n"
        else:  # 近战（含徒手）
            _wkey = weapon.key if weapon is not None else ""
            if underwater_disadvantage("melee", _wkey, _has_swim):
                advantage = combine_advantage(advantage, Advantage.DISADVANTAGE)
                warn += "（水下近战攻击带劣势：除非用匕首 / 标枪 / 短剑 / 矛 / 三叉戟，或有游泳速度）\n"

    # —— 弹药：远程且是弹药武器就要花一枚（用完就打不出）——
    if ranged_attack and weapon is not None and weapon.is_ammunition:
        names = weapon.ammo_names
        if card.ammo_count(names) <= 0:
            return (
                f"{warn}{card.name} 的{' / '.join(names)}用完了，没法再开火——"
                "换近战武器，或让 DM 按叙述裁决（地上捡 / 借）",
                False,
            )
        spent, ammo_note, ammo_name = card.spend_ammo(names)
        if spent:
            key = f"{card.user_id}:{ammo_name}"
            state.ammo_used[key] = int(state.ammo_used.get(key, 0)) + 1
            set_character(save, card)
            save_combat(save, state)
            warn += f"{ammo_note}\n"

    # —— 重型：小型攻击者用重型武器有劣势 ——
    if (
        weapon is not None
        and weapon.is_heavy
        and _is_small(card.race_obj.size if card.race_obj else "中型")
    ):
        advantage = combine_advantage(advantage, Advantage.DISADVANTAGE)
        warn += f"（{weapon.cn}是重型武器，体型小驾驭不动：攻击有劣势）\n"

    # —— 骑枪：打 5 尺内的目标有劣势 ——
    if weapon is not None and weapon.key == "lance" and 0 <= distance <= 5:
        advantage = combine_advantage(advantage, Advantage.DISADVANTAGE)
        warn += "（骑枪打 5 尺内的目标有劣势）\n"

    # 目标状态带来的优势 / 劣势（倒地、隐形、目盲、束缚……）；攻击者自己的状态
    # （中毒 / 力竭……）由角色卡的检定函数自动合并，两边相遇时自然抵消
    state_adv, state_reason = attack_advantage(
        [], combatant_conditions(save, target), melee=is_melee, distance=distance
    )
    advantage = combine_advantage(advantage, state_adv)
    if state_reason:
        warn += f"（{state_reason}）\n"

    grip = ""
    if two_handed and weapon is not None and weapon.is_versatile:
        grip = "（双手持握）"

    if weapon is not None:
        result: CheckResult = card.check_attack(
            weapon, target_ac, advantage=advantage, rng=rng
        )
        if improvised:
            damage_expr = "1d4"  # 临时武器不吃属性加值
        else:
            base = (
                weapon.two_handed_damage
                if (two_handed and weapon.is_versatile)
                else weapon.damage
            )
            if off_hand:
                # 双武器战斗（副手攻击）：伤害不加大幅值（5e）；需轻型武器
                damage_expr = base
                if not weapon.is_light:
                    warn += "（双武器战斗的副手攻击应当用轻型武器）\n"
            else:
                bonus = card.damage_bonus(weapon)
                damage_expr = f"{base}{bonus:+d}" if bonus else base
        damage_type = weapon.damage_type
        label = weapon.cn + grip
    else:
        bonus = card.proficiency + card.modifier("str")
        # 徒手也走角色卡的状态合并（中毒 / 力竭的劣势不能漏）
        final_adv, state_note = card.resolve_attack_advantage(advantage)
        result = roll_check(
            bonus,
            target_ac,
            kind="徒手攻击",
            advantage=final_adv,
            auto_critical=True,
            rng=rng,
        )
        if state_note:
            result.kind += state_note
        damage_expr = str(max(1, 1 + card.modifier("str")))
        damage_type = "钝击"
        label = "徒手"

    lines = [
        f"【{card.name}】{label} → {result.describe()}"
        f"（目标 AC {target_ac}{cover_text(state, target)}；距离 {distance} 尺）"
        if distance >= 0
        else f"【{card.name}】{label} → {result.describe()}（目标 AC {target_ac}）"
    ]
    if not result.success:
        lines.append(f"未命中 {target.name}。")
        return warn + "\n".join(lines), False

    # 捕网：不掷伤害，命中即束缚（解脱：动作 DC10 力量，或对网 5 点挥砍 [AC 10]）
    if weapon is not None and weapon.key == "net":
        note = _add_target_condition(save, state, target, "束缚")
        lines.append(f"捕网罩住了 {target.name}！{note}")
        lines.append(
            "（束缚至解脱：用动作做 DC10 力量检定，或对网造成 5 点挥砍伤害 [AC 10]；"
            "无形体与巨型以上体型不受影响）"
        )
        return warn + "\n".join(lines), True

    # 5e：5 尺内命中麻痹 / 昏迷的目标即暴击（伤害骰照翻倍）
    auto_crit = _auto_crit(save, target, melee=is_melee, distance=distance)
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


def _is_small(size: str) -> bool:
    """是不是小型或更小（重型武器对它们有劣势）。"""
    return str(size or "中型") in ("小型", "微型")


def enemy_within(state: Any, attacker: Any, feet: int = 5) -> bool:
    """攻击者 ``feet`` 尺内是否还有站着的敌人（5e：远程攻击时身边有敌人则劣势）。

    没铺棋盘 / 攻击者没落子 / 周围没站着的敌人，都返回 False——剧情模式（无坐标）
    无从判断「贴身」，这种情况交给 DM 按叙述裁定。
    """
    if attacker is None or not has_map(state) or not has_position(attacker):
        return False
    for enemy in living_enemies(state):
        if enemy.cid == getattr(attacker, "cid", None):
            continue
        if not has_position(enemy):
            continue
        d = distance_feet(attacker, enemy)
        if d >= 0 and d <= feet:
            return True
    return False


def _add_target_condition(
    save: Save, state: CombatState, target: Combatant, name: str
) -> str:
    """给参战者补一条状态（玩家写角色卡，敌人写 Combatant），返回说明。"""
    if target.kind == "player":
        card = get_character(save, target.cid)
        if card is None:
            return f"（找不到 {target.name} 的角色卡，状态没写上）"
        if name in card.conditions:
            return f"{target.name} 已经处于{name}"
        card.conditions = [*card.conditions, name]
        set_character(save, card)
        return f"{target.name} 陷入{name}"
    if name in target.conditions:
        return f"{target.name} 已经处于{name}"
    target.conditions = [*target.conditions, name]
    save_combat(save, state)
    return f"{target.name} 陷入{name}"


def recover_ammo(save: Save, state: CombatState) -> str:
    """战后搜 battlefield：5e 回收一半耗掉的弹药。

    :return: 给结算文本的一行说明；没耗过弹药返回空串。
    """
    if not state.ammo_used:
        return ""
    lines: list[str] = []
    for key, spent in sorted(state.ammo_used.items()):
        spent = int(spent or 0)
        if spent <= 0:
            continue
        cid, _, ammo = key.partition(":")
        back = spent // 2
        card = get_character(save, cid) if cid else None
        if card is None:
            continue
        if back > 0:
            # 并进装备栏里已有的同种弹药（没有就新起一条）
            merged = False
            for index, item in enumerate(card.equipment):
                stack = parse_item_stack(item)
                if stack.name == ammo:
                    card.equipment[index] = f"{ammo}×{stack.count + back}"
                    merged = True
                    break
            if not merged:
                card.equipment.append(f"{ammo}×{back}")
        set_character(save, card)
        who = card.name or cid
        lines.append(
            f"{who} 回收 {back} 枚{ammo}（耗 {spent}，剩 {card.ammo_count((ammo,))}）"
        )
    state.ammo_used = {}
    save_combat(save, state)
    if not lines:
        return ""
    return "战场搜索：回收了一半弹药——" + "；".join(lines) + "。"


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
    # 攻击名认不出就**回退到第一项攻击并说明**，不打回。
    #
    # 打回一次就是一整批功能调用作废（模型会把同一轮的调用整段重发），实测：
    # DM 连猜三轮「沉重拳击」（这怪只有「腐爪」），每轮两条一起废、再重发「腐爪」——
    # 六次无效往返只为问一个名字。程序这边明明知道它只有哪几招，
    # 挑一项打下去、把「你要的那个没有，我用了这个」讲清楚就够了。
    # 与玩家侧「武器认不出 → 自动挑武器」是同一条口径。
    attack_obj = monster.get_attack(attack_name)
    fallback_note = ""
    if attack_obj is None:
        if not monster.attacks:
            return (
                f"{monster.cn} 的数据里没有攻击项，请改用 roll_dice 手算。",
                False,
            )
        wanted = str(attack_name or "").strip()
        attack_obj = monster.attacks[0]
        names = "、".join(a.name for a in monster.attacks)
        fallback_note = (
            f"（{monster.cn}没有「{wanted}」这项攻击，已改用「{attack_obj.name}」；"
            f"它有：{names}）"
            if wanted
            else ""
        )

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

    # 专攻（elite 档）：敌方优先打最脆弱的那个。选了别人就提醒一句——
    # **不拦**：战术取舍是 DM 的判断力，系统只保证规则被看见。
    focus_hint = ""
    focus_cid = str(getattr(state, "threat_focus", "") or "")
    if focus_cid and target.cid != focus_cid:
        focus_name = threat_focus_name(state)
        if focus_name:
            focus_hint = f"（{enemy.name}的专攻目标是{focus_name}，不是{target.name}）"

    # 双方状态带来的优势 / 劣势（敌人中毒 / 玩家倒地 / 隐形……），相遇抵消
    state_adv, state_reason = attack_advantage(
        combatant_conditions(save, enemy),
        combatant_conditions(save, target),
        melee=attack_obj.ranged_normal == 0,
        distance=gate.distance,
    )
    advantage = combine_advantage(advantage, state_adv)

    # 怪物**特性**带来的优势（集群战术 / 血腥狂怒 / 日照敏感）：
    # 书上有明文，却最容易忘——三只狗头人围上来还按普通攻击掷，压力凭空少一截。
    rules = rules_of(monster)
    daylight = getattr(state, "daylight", None)
    trait_adv, trait_reason = attack_advantage_of(
        rules,
        allies_near=_allies_within(state, enemy, target),
        target_wounded=effective_hp(save, target)[0] < effective_hp(save, target)[1],
        sunlight=bool(daylight),
    )
    advantage = combine_advantage(advantage, trait_adv)

    target_ac = effective_ac(save, target, state)
    bonus = monster.attack_bonus(attack_obj)
    # 记一笔「它这一轮已经出手了」：自动结算据此不重复打同一只怪
    # （DM 手动补刀也算），同一轮里连打几次（多重攻击）不受影响。
    enemy.acted_round = state.round
    result = roll_check(
        bonus,
        target_ac,
        kind=f"{attack_obj.name}攻击",
        advantage=advantage,
        # 敌人的攻击同样是攻击检定：天然 20 必中（且暴击）、天然 1 必失
        auto_critical=True,
        rng=rng,
    )
    lines = [
        f"【{enemy.name}】{attack_obj.name} → {result.describe()}"
        f"（目标 {target.name} AC {target_ac}{cover_text(state, target)}"
        + (f"，距离 {gate.distance} 尺）" if gate.distance >= 0 else "）")
    ]
    if fallback_note:
        lines.append(fallback_note)
    if state_reason:
        lines.append(f"（{state_reason}）")
    if trait_reason:
        lines.append(f"（{trait_reason}）")
    elif rules.sunlight_sensitivity and daylight is None:
        # 有没有日光系统判不了（地牢里的白天也不是白天），交给 DM，但**提醒他判**
        lines.append(
            "（它有日照敏感：这里有没有日光由你裁定，"
            "有的话这一击带劣势——下次开战可以用 daylight=是/否 定下来）"
        )
    if focus_hint:
        lines.append(focus_hint)

    # 传奇行动（threat 的 legendary 档）：首领每轮可以多一次行动，不占动作。
    # 口径：**每轮至多 2 次攻击**（1 次常规 + 1 次传奇），超出的直接拦下——
    # 这条要是只提示不拦，DM 多调几次就等于没有上限，压力凭空消失。
    legendary = False
    if enemy.cid == str(getattr(state, "threat_boss", "") or ""):
        used = _legendary_count(state, enemy)
        if used >= 2:
            return (
                f"{enemy.name} 本轮的传奇行动已经用过了（首领每轮只有 1 次额外行动）。",
                False,
            )
        legendary = _count_legendary(state, enemy)
        if legendary:
            lines.append("（传奇行动：这一击不占它的动作）")
        else:
            lines.append("（它本轮还可以再发动一次传奇行动）")

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
            magical=rules.magic_weapons,
            rng=rng,
        )
    )
    return "\n".join(lines), True


# --------------------------------------------------------------------------- #
# 敌人回合自动结算：靠近 → 选招 → 掷骰扣血
# --------------------------------------------------------------------------- #
#
# 为什么要它：**「怎么算」本来就全是程序的事**（命中、伤害、抗性、暴击、射程、
# 倒地濒死、死亡豁免都在这一层），只有「谁打谁、用哪招」压在 DM 身上。
# 于是每轮每只怪都要 DM 单独调一次 ``enemy_attack``，一局十几次；而攻击名还得
# 它自己猜——实测有怪物只有「腐爪」，DM 连猜三轮「沉重拳击」，每轮两条调用
# 一起作废再重发，六次往返只为问一个名字。
#
# 自动结算只做**机械部分**：挑一个目标、够不着就先走过去、挑一招够得着的、
# 该打几次打几次。战术判断（吐息什么时候放、先掐治疗还是先控场、要不要投降）
# 一律留给 DM——工具还在，它随时能接管。

#: 中文数字 → 攻击次数（数据卡里「多重攻击：发动两次啃咬攻击」是汉字）
_CN_COUNTS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5}


def enemy_attack_count(monster: Monster | None) -> int:
    """这只怪一回合打几次（读数据卡的「多重攻击」特性，读不到按 1 次）。

    5e 把多重攻击写成一句话（「魔冢发动两次时械剑刃攻击」），没有独立字段；
    图鉴里这类特性 300+ 条，全是「…N 次…」的句式，按次数取足够稳。
    上限 4 次：再多的多半是「每轮能打十下」的特殊怪，交给 DM 手动处理。
    """
    if monster is None:
        return 1
    for trait in monster.traits or ():
        text = str(trait)
        if "多重攻击" not in text:
            continue
        hit = re.search(r"([一二两三四五1-5１-５])\s*次", text)
        if hit is None:
            return 2  # 写了多重攻击却没写次数：按最普遍的两次算
        char = hit.group(1)
        if char in _CN_COUNTS:
            return _CN_COUNTS[char]
        return max(1, min(4, int(char.translate(str.maketrans("１２３４５", "12345")))))
    return 1


def enemy_target(save: Save, state: CombatState, enemy: Combatant) -> Combatant | None:
    """敌人在自己的回合打谁：**专攻目标**优先，其次最近的活人（同距打血最少的）。

    够不着的算「远」但不排除——先记下来，调用方会先让它走过去。
    没有棋盘的老存档一律算同距，那就按血量挑，至少不会一直打满血那个。
    """
    pool = [
        c
        for c in state.order
        if c.kind == "player" and not is_out(save, c) and can_target(save, c)
    ]
    if not pool:
        return None
    focus = str(getattr(state, "threat_focus", "") or "")
    if focus:
        for c in pool:
            if c.cid == focus:
                return c
    pinned = has_position(enemy)

    def key(c: Combatant) -> tuple[int, int]:
        distance = distance_feet(enemy, c) if pinned and has_position(c) else 0
        return (distance, effective_hp(save, c)[0])

    return min(pool, key=key)


def _pick_enemy_attack(
    state: CombatState, enemy: Combatant, target: Combatant, monster: Monster
) -> tuple[Any, bool]:
    """挑一招打这个目标，返回 ``(攻击项, 现在够不够得着)``。

    优先级：**够得着的近战** > 够得着的远程 > 第一项（够不着，调用方先移动）。
    近战优先是因为远程在贴身时本来就有劣势（5e），而且怪物多半只有近战。
    """
    if not monster.attacks:
        return None, False
    fallback: Any = None
    for item in monster.attacks:
        gate = range_check(
            state,
            enemy,
            target,
            reach=item.reach,
            normal=item.ranged_normal,
            max_range=item.ranged_max,
        )
        if not gate.ok:
            continue
        if item.ranged_normal == 0:  # 近战够得着：直接用
            return item, True
        if fallback is None:
            fallback = item
    if fallback is not None:
        return fallback, True
    return monster.attacks[0], False


def _flee_cell(state: CombatState, enemy: Combatant) -> tuple[int, int] | None:
    """给要逃的敌人挑一个「离所有活人最远」的空格（0 基返回）。"""
    if not has_map(state) or not has_position(enemy):
        return None
    players = [
        c for c in state.order if c.kind == "player" and has_position(c) and not c.out
    ]
    if not players:
        return None
    occupied = {
        (int(c.x), int(c.y))
        for c in state.order
        if has_position(c) and not c.out and c.cid != enemy.cid
    }
    best: tuple[int, int] | None = None
    best_score = -1
    for x in range(state.width):
        for y in range(state.height):
            if (x, y) in occupied:
                continue
            if terrain_at(state, x, y).key == "blocked":
                continue
            score = min(chebyshev(x, y, int(p.x), int(p.y)) for p in players)
            if score > best_score:
                best_score, best = score, (x, y)
    return best


def _flee(save: Save, state: CombatState, enemy: Combatant) -> str:
    """被恐惧压垮的敌人：不攻击，往外撤；真跑不掉了就当场退出战斗。

    这是 5e「恐惧 → 必须远离恐惧源」的机械落地。**逃跑不给经验**（严格 5e）：
    退出战斗时撤销击败记录，结算时不算战果——只有真打到 0 血才算。
    """
    threat = enemy_target(save, state, enemy)
    moved = ""
    dest = _flee_cell(state, enemy)
    if dest is not None:
        text = move_combatant(
            save, state, enemy.cid, pos=f"{dest[0] + 1},{dest[1] + 1}"
        )
        moved = text.splitlines()[0]
    cornered = False
    if threat is not None and has_position(enemy) and has_position(threat):
        cornered = chebyshev(enemy.x, enemy.y, threat.x, threat.y) <= 1
    elif threat is None:
        cornered = False
    if cornered:
        enemy.out = True
        enemy.note = "被恐惧压垮，丢下武器逃出战场"
        forget_defeat(state, enemy)
        if enemy.npc:
            update_npc(save, enemy.npc, status="逃跑")
        return f"【{enemy.name}】惊恐至极，转身逃出战场（逃跑不给经验）。"
    if moved:
        return f"【{enemy.name}】被恐惧压住，不敢上前：{moved}"
    return f"【{enemy.name}】被恐惧压住，缩在原地不敢动手。"


def auto_enemy_turns(
    save: Save,
    state: CombatState,
    *,
    enemies: list[Combatant] | None = None,
    rng: Any = None,
) -> list[str]:
    """系统替敌人打完这一轮（机械部分），返回**可以直接播给玩家**的结算文案。

    :param enemies: 这一轮夹在玩家之间行动的敌人；留空则取场上所有还站着的。
        传 :func:`advance_turn` 的返回值最准——那是系统算出来的「该谁动」。

    每只怪做的事：挑目标 → 够不着就先 :func:`move_combatant` 靠近 →
    按 :func:`_pick_enemy_attack` 选招 → 按 :func:`enemy_attack_count` 打 N 次。
    某一步做不成就写一句「这回合空过」而不是抛错：战斗不能因为一只怪卡住就不动。
    """
    report: list[str] = []
    pool = list(enemies) if enemies is not None else living_enemies(state)
    for enemy in pool:
        if not enemy.is_enemy or is_out(save, enemy) or not can_act(save, enemy):
            continue
        if int(getattr(enemy, "acted_round", 0) or 0) == state.round:
            continue  # 这一轮已经替它打过了（或 DM 手动打过）
        monster = resolve_monster(enemy.monster)
        if monster is None:
            report.append(f"（{enemy.name} 没有怪物数据，这一回合空过）")
            enemy.acted_round = state.round
            continue

        target = enemy_target(save, state, enemy)
        if target is None:
            break  # 没人可打了（全队倒地），剩下的敌人不用再动

        # 被恐惧压住的敌人不攻击，只往外撤（5e：必须远离恐惧源）
        if "恐慌" in combatant_conditions(save, enemy):
            report.append(_flee(save, state, enemy))
            enemy.acted_round = state.round
            continue

        attack, reachable = _pick_enemy_attack(state, enemy, target, monster)
        if attack is None:
            report.append(f"（{enemy.name} 没有攻击项，这一回合空过）")
            enemy.acted_round = state.round
            continue
        if not reachable:
            # 够不着就走过去。移动失败（被堵住 / 本回合额度用完）也照样往下走：
            # 它会得到一句「这回合还是够不着」，而不是让整轮结算中断。
            walked = move_combatant(save, state, enemy.cid, target=target.name)
            report.append(walked.splitlines()[0])
            attack, reachable = _pick_enemy_attack(state, enemy, target, monster)
            if attack is None or not reachable:
                report.append(f"（{enemy.name} 这回合还是够不着 {target.name}）")
                enemy.acted_round = state.round
                continue

        times = enemy_attack_count(monster)
        if enemy.cid == str(getattr(state, "threat_boss", "") or ""):
            times = min(times, 2)  # 首领每轮至多两次（另一次算传奇行动）
        for _ in range(times):
            if is_out(save, enemy):
                break
            if is_out(save, target) or not can_target(save, target):
                # 目标倒了就换一个还站着的（同一轮里继续打）
                target = enemy_target(save, state, enemy)
                if target is None:
                    break
                attack, reachable = _pick_enemy_attack(state, enemy, target, monster)
                if attack is None or not reachable:
                    break
            text, _hit = enemy_attack(
                save, state, enemy.cid, target.cid, attack.name, rng=rng
            )
            report.append(text)
        enemy.acted_round = state.round

    # 增援（attrition 档）：原来挂在 enemy_attack 上，敌人攻击改成系统自动结算后
    # 就搬到这儿——否则「打到一半追加一波」这条规则再也不会触发。
    try:
        wave = summon_reinforcements(save, state, rng=rng)
    except Exception as e:  # 增援是增强项，不该让这一轮结算崩掉
        log.warning(f"放增援失败（忽略）：{str_traceback(e)}")
        wave = ""
    if wave:
        report.append(wave)
    return report


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
            wanted = normalize_conditions(cond_text)
            # 状态免疫（骷髅免疫中毒、幽魂免疫倒地……）：挡掉，别让它白挨一个状态
            immune = blocked_conditions(
                rules_of(resolve_monster(enemy.monster)), wanted
            )
            enemy.conditions = [name for name in wanted if name not in immune]
            if immune:
                notes.append(f"{enemy.name} 免疫{'、'.join(immune)}，这几个没生效。")
            if enemy.conditions:
                notes.append(f"{enemy.name} 当前状态：{'、'.join(enemy.conditions)}。")
            elif not immune:
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
    # 虚体 / 无定形 / 集群：能穿过别的生物（幽魂穿墙、鼠群从脚边漫过去）
    if rules_of(resolve_monster(mover.monster)).passes_through:
        blocked = set()
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


def threat_text(state: CombatState) -> str:
    """威胁层级的一句话摘要（给 DM 看；没有威胁时返回空串）。

    写进战斗块的开头，让 DM 一眼看到「这场有哪些额外规则」——
    规则藏得太深就等于没有规则。
    """
    tags = list(getattr(state, "threat_tags", []) or [])
    if not tags:
        return ""
    from .threat import ARENA_RULES

    parts: list[str] = []
    if "elite" in tags:
        name = threat_focus_name(state)
        parts.append(f"敌方专攻{name}" if name else "敌方专攻最脆弱的冒险者")
    if "attrition" in tags:
        plan = dict(getattr(state, "threat_reinforcement", {}) or {})
        count = int(plan.get("count", 0) or 0)
        if count and int(getattr(state, "reinforcement_waves", 0) or 0) == 0:
            parts.append(f"还有一波增援未到（{count} 只 {plan.get('label', '杂兵')}）")
    if "legendary" in tags:
        boss = ""
        for combatant in state.order:
            if combatant.cid == str(getattr(state, "threat_boss", "") or ""):
                boss = combatant.name
                break
        parts.append(
            f"首领{boss}每轮有 1 次传奇行动" if boss else "首领每轮有 1 次传奇行动"
        )
    if "rulebreak" in tags:
        key = str(getattr(state, "arena_rule", "") or "")
        for rule in ARENA_RULES:
            if rule.key == key:
                parts.append(f"场地规则：{rule.name}（{rule.desc}）")
                break
    if not parts:
        return ""
    return "【威胁】" + "；".join(parts)


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
    threat_line = threat_text(state)
    if threat_line:
        lines.append(threat_line)
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
