"""战斗结算：经验分享、战利品与升级。

一场战斗打完之后要回答三件事，本模块就是这三件事的落点：

- **经验奖励**：5e 规则「一只怪物的经验由全队共享」——先按每只被击败怪物的挑战等级
  累加经验（:func:`~.progression.xp_for_cr`），再**除以参战人数**平分给每位玩家，
  写入角色卡的 ``exp`` 字段。
- **战利品**：按怪物的挑战等级掷金币与物品（:func:`roll_loot`），结果记进存档进度的
  ``战利品`` 字段，由 DM 决定怎么分（也可以直接 ``update_character`` 塞进装备）。
- **升级**：分完经验后逐个检查阈值（:func:`~.progression.can_level_up`），
  达标的自动 :func:`~.progression.level_up`，并把生命上限 / 熟练加值 / 法术位的变化
  写进结算文案。

设计约定：

- 战果数据来自 :attr:`~.combat.CombatState.defeated`（敌人倒下时由
  :func:`~.combat.record_defeat` 记下），因此**逃跑 / 被擒**的敌人只要 DM 用
  ``update_enemy`` 标记了退场也会一并计入。
- 参战人数优先取本场先攻顺序里的玩家；这场没人（比如战斗刚开始就崩了）就退回存档里
  的全体玩家。
- 本模块负责写盘（角色卡 + 进度 + 事件日志），但**不清除战斗状态**——清理由调用方
  （``end_combat`` 工具 / 剧情循环）自己做，顺序是「先结算，再清战斗」。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from .card import DndCharacter
from .combat import CombatState
from .progression import can_level_up, level_up, xp_for_cr
from .save_store import Save, get_character, log_event, set_character, update_progress

__all__ = [
    "LOOT_TABLE",
    "SettlementResult",
    "coins_text",
    "roll_coins",
    "roll_loot",
    "settle_combat",
    "total_xp",
]


#: 战利品档位：``(CR 下限, 金币骰, 金币颗数/骰, 物品个数上限, 候选物品)``
#:
#: 候选物品直接从装备表里按「价格感」挑：CR 越高越可能出现好装备。
LOOT_TABLE: tuple[tuple[float, int, int, int, tuple[str, ...]], ...] = (
    (0.0, 1, 6, 0, ("箭", "绳索", "火把", "干粮")),
    (1.0, 1, 8, 1, ("箭", "绳索", "火把", "干粮", "短剑", "皮甲")),
    (2.0, 2, 6, 1, ("箭", "火把", "短剑", "皮甲", "轻弩", "锁子甲")),
    (4.0, 2, 8, 1, ("轻弩", "锁子甲", "长剑", "弯刀", "头盔", "盾牌")),
    (6.0, 3, 6, 2, ("长剑", "弯刀", "锁子甲", "头盔", "盾牌", "战锤", "银币袋")),
    (9.0, 4, 6, 2, ("长剑", "战锤", "板甲", "塔盾", "宝石", "金币袋")),
    (13.0, 5, 8, 2, ("板甲", "塔盾", "宝石", "金币袋", "魔法卷轴")),
)


def _loot_band(cr: float) -> tuple[float, int, int, int, tuple[str, ...]]:
    """按 CR 取战利品档位（取不超过它的最高一档）。"""
    band = LOOT_TABLE[0]
    for row in LOOT_TABLE:
        if float(cr) >= row[0]:
            band = row
        else:
            break
    return band


def roll_coins(cr: float, rng: Any = None) -> dict[str, int]:
    """按怪物的 CR 掷一袋金币。

    :return: ``{"银": n, "金": n, "铂": n}``（没有的币种不出现）
    """
    randomer = rng or random
    _, dice, faces, _, _ = _loot_band(cr)
    gold = randomer.randint(0, dice) * faces
    silver = randomer.randint(0, faces) * 5
    coins = {"银": silver, "金": gold}
    # 高 CR 偶尔出手阔绰，给一把铂金
    if float(cr) >= 9 and randomer.randint(1, 100) <= 5 + int(float(cr)):
        coins["铂"] = randomer.randint(1, max(1, int(float(cr) // 5)))
    return {name: value for name, value in coins.items() if value > 0}


def coins_text(coins: dict[str, int]) -> str:
    """金币字典转成一句中文，如 ``金 35 · 银 20``。"""
    return " · ".join(f"{name} {value}" for name, value in coins.items()) or "无"


def roll_loot(cr: float, rng: Any = None) -> dict[str, Any]:
    """按 CR 掷一只怪物的战利品。

    :return: ``{"coins": {...}, "items": [名称, ...]}``；``items`` 用的是装备表里认得出来的
        中文名，DM 可以直接 ``update_character`` 塞进装备栏。
    """
    randomer = rng or random
    _, _, _, item_count, candidates = _loot_band(cr)
    count = randomer.randint(0, item_count) if item_count else 0
    items: list[str] = []
    for _ in range(count):
        name = randomer.choice(candidates)
        # 杂物给个数量，装备给 1 件
        amount = randomer.randint(1, 6) if name in {"箭", "绳索", "火把", "干粮"} else 1
        text = name if amount <= 1 else f"{name}×{amount}"
        items.append(text)
    return {"coins": roll_coins(cr, rng), "items": items}


def total_xp(state: CombatState) -> int:
    """本场战斗累计可分配的经验值（各被击败怪物 CR 对应经验之和）。"""
    return sum(int(item.get("xp") or xp_for_cr(_as_float(item.get("cr"))))
               for item in state.defeated)


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


@dataclass(slots=True)
class SettlementResult:
    """一次战斗结算的结果。"""

    #: 本场总经验
    xp: int = 0
    #: 参与分成的玩家数
    share_count: int = 0
    #: 每人分到的经验
    share: int = 0
    #: 每人分到的金币与物品（同一份，全队共享）
    coins: dict[str, int] = field(default_factory=dict)
    items: list[str] = field(default_factory=list)
    #: 逐人的经验变化与升级文案：``user_id -> [文本, ...]``
    lines: dict[str, list[str]] = field(default_factory=dict)
    #: 事件日志（被击败的敌人清单）
    defeated_text: str = ""

    @property
    def xp_text(self) -> str:
        if not self.share_count:
            return "本场没有可分配经验的战果。"
        return (
            f"本场共 {self.xp} XP，由 {self.share_count} 名参战者平分，"
            f"每人 {self.share} XP。"
        )

    @property
    def loot_text(self) -> str:
        parts = []
        if self.coins:
            parts.append(f"金币 {coins_text(self.coins)}")
        if self.items:
            parts.append("物品 " + "、".join(self.items))
        return "；".join(parts) or "没有搜出任何东西。"

    def text(self) -> str:
        """给 DM 与玩家看的结算说明。"""
        lines = ["【战斗结算】"]
        if self.defeated_text:
            lines.append(self.defeated_text)
        lines.append(self.xp_text)
        if self.share_count:
            lines.append(f"战利品：{self.loot_text}")
        for chunks in self.lines.values():
            lines.extend(chunks)
        return "\n".join(lines)


def _participants(save: Save, state: CombatState) -> list[str]:
    """本场参战的玩家 user_id（先攻顺序里的优先，否则全体玩家）。"""
    ids = [c.cid for c in state.order if c.kind == "player"]
    if not ids:
        ids = list(save.player_ids)
    seen: list[str] = []
    for user_id in ids:
        if user_id not in seen:
            seen.append(user_id)
    return seen


def _defeated_text(state: CombatState) -> str:
    if not state.defeated:
        return "本场没有记录到被击败的敌人。"
    rows = []
    for item in state.defeated:
        name = str(item.get("name") or "?")
        cr_text = str(item.get("cr_text") or "")
        xp = int(item.get("xp") or 0)
        reason = str(item.get("reason") or "")
        rows.append(f"{name}（CR {cr_text}{'，' + reason if reason else ''}）→ {xp} XP")
    return "击败：" + "、".join(rows) + "。"


def _award_xp(card: DndCharacter, share: int) -> list[str]:
    """给一张卡发经验并处理升级，返回给玩家看的文案。"""
    lines: list[str] = []
    before = card.exp
    card.exp = max(0, before + share)
    lines.append(f"【{card.name}】经验 {before} → {card.exp} XP（+{share}）")
    while can_level_up(card):
        lines.append(level_up(card))
    return lines


def settle_combat(save: Save, state: CombatState, *, rng: Any = None) -> SettlementResult:
    """结算一场战斗：经验平分 + 战利品 + 升级，全部写盘。

    调用方负责在结算**之后**清除战斗状态（:func:`~.combat.clear_combat`）。

    :param state: 战斗状态；用 :attr:`~.combat.CombatState.defeated` 作为战果来源。
    :param rng: 掷战利品用的随机源（测试可注入）。
    :return: 结算结果，:meth:`SettlementResult.text` 是给 DM / 玩家看的说明。
    """
    result = SettlementResult()
    result.defeated_text = _defeated_text(state)
    result.xp = total_xp(state)
    participants = _participants(save, state)
    # 分成只算「真的能上场的角色」：找不到角色卡的（中途加入 / 已退出的）不算人头
    cards: list[tuple[str, DndCharacter]] = []
    for user_id in participants:
        card = get_character(save, user_id)
        if card is not None:
            cards.append((user_id, card))
    result.share_count = len(cards)
    result.share = result.xp // len(cards) if cards else 0

    # 战利品：CR 最高的那只决定档次（打爆了个首领就该有首领的排面）
    top_cr = max((_as_float(item.get("cr")) for item in state.defeated), default=0.0)
    loot = roll_loot(top_cr, rng) if state.defeated else {"coins": {}, "items": []}
    result.coins = loot["coins"]
    result.items = list(loot["items"])

    for user_id, card in cards:
        result.lines[user_id] = _award_xp(card, result.share)
        set_character(save, card)
    if result.share:
        # 经验没分完的零头留在总账里，不补给某个人（避免 DM 每次都要算零头）
        log_event(save, "combat", result.text(), actor="dm")

    if result.coins or result.items:
        current = list(save.progress.get("战利品") or [])
        entry = f"{result.loot_text}（来自 CR {top_cr:g} 的战利品）"
        update_progress(save, 战利品=current + [entry])

    return result
