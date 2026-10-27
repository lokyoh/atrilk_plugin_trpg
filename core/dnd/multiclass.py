"""兼职（multiclassing，PHB 2024「兼职」节）。

引擎原本是**单职业模型**（一张卡一个 ``profession`` 一个 ``level``）。兼职要改的
不是「多存一个职业名」，而是三件会连锁的事：

1. **总等级**驱动熟练加值与升级门槛，**职业等级**驱动特性 / 资源 / 法术数量；
2. **生命值**按每一级所属职业的生命骰算（起始职业的 1 级拿满骰，其余按升级算）；
3. **法术位**按「兼职施法者」表累加：全职施法职业等级全算、圣武士 / 游侠减半
   向上取整、奥法骑士 / 诡术师三分之一向下取整；**魔契师单列一套契约位**。

所以卡上存的是 :attr:`~.card.DndCharacter.level_classes` ——「第 N 级加在哪个职业」
的序列（下标 0 是 1 级）。这是唯一真相：职业等级、HP、法术位都从它推，不另存一份
``{职业: 等级}``，免得两处不同步。

设计约定：

- **起始职业**（``level_classes[0]``）决定豁免熟练 —— 5e 明文：兼职不给新豁免；
  卡上的 ``profession`` 字段始终等于起始职业（展示顺序也按它排）。
- 进入新职业时才补该职业的**兼职熟练**（:data:`res/dnd/multiclass.yaml` 那一条），
  不是整个职业的熟练表；技能那部分要给玩家挑，返回的说明里会点名。
- **额外攻击不叠加**（手册明文），由 :attr:`~.card.DndCharacter.attacks_per_action`
  取各职业的最大值。
- 子职**按职业各存一个**（``subclasses``），子职特性只在自己职业的等级上解锁。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .datafiles import as_texts, load_table
from .profession import get_class
from .spells import FULL_CASTER_SLOTS, PACT_SLOTS

__all__ = [
    "MAX_PREREQUISITE",
    "MulticlassGrant",
    "advance",
    "casting_ability_for",
    "class_level",
    "class_levels",
    "classes_text",
    "effective_caster_level",
    "grants_for",
    "hit_die_for_level",
    "is_multiclass",
    "level_classes",
    "pact_slots",
    "prerequisite",
    "primary_caster",
    "spell_slots",
    "subclass_for",
]

#: 兼职先决：新职业与现有职业的主要属性都要到这个数（手册 2554）。
MAX_PREREQUISITE = 13

#: 兼职时进入第二个职业能拿到的熟练（``res/dnd/multiclass.yaml``）。
_GRANT_ROWS: dict[str, dict[str, Any]] = {
    str(row.get("key", "")): row for row in load_table("multiclass")
}


@dataclass(frozen=True, slots=True)
class MulticlassGrant:
    """进入某个职业时补的熟练项。"""

    armor: tuple[str, ...] = ()
    weapons: tuple[str, ...] = ()
    skills: int = 0  # 还要从该职业技能池里选几个
    tools: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        return not (self.armor or self.weapons or self.skills or self.tools)

    def text(self) -> str:
        """展示文本（空项省略）。"""
        bits: list[str] = []
        if self.armor:
            bits.append("护甲 " + "、".join(self.armor))
        if self.weapons:
            bits.append("武器 " + "、".join(self.weapons))
        if self.tools:
            bits.append("工具 " + "、".join(self.tools))
        if self.skills:
            bits.append(f"技能 {self.skills} 项（自选）")
        return "；".join(bits)


def grants_for(profession: str | Any) -> MulticlassGrant:
    """某职业的兼职熟练（认不出 / 没登记 → 空）。"""
    key = getattr(profession, "key", profession)
    row = _GRANT_ROWS.get(str(key or "").strip(), {})
    return MulticlassGrant(
        armor=as_texts(row.get("armor")),
        weapons=as_texts(row.get("weapons")),
        skills=int(row.get("skills", 0) or 0),
        tools=as_texts(row.get("tools")),
    )


# --------------------------------------------------------------------------- #
# 职业等级：唯一真相是「第 N 级加在哪个职业」
# --------------------------------------------------------------------------- #


def level_classes(card: Any) -> list[str]:
    """每一级所属的职业键（下标 0 = 1 级）。

    没记过（单职业卡的常态）时，就是「全部等级都加在 ``profession`` 上」。
    """
    stored = list(getattr(card, "level_classes", []) or [])
    total = max(1, int(getattr(card, "level", 1) or 1))
    profession = str(getattr(card, "profession", "") or "")
    if not stored:
        return [profession] * total
    if len(stored) < total:
        # 旧存档 / 手工写坏了：补到总等级长度（补最后一级的职业）
        stored.extend([stored[-1] if stored else profession] * (total - len(stored)))
    return stored[:total]


def class_levels(card: Any) -> dict[str, int]:
    """``{职业键: 该职业等级}``（按首次出现的顺序）。"""
    out: dict[str, int] = {}
    for key in level_classes(card):
        key = str(key or "")
        if not key:
            continue
        out[key] = out.get(key, 0) + 1
    return out


def class_level(card: Any, profession: str = "") -> int:
    """某个职业的等级；不传就看卡的主职业。"""
    key = str(profession or getattr(card, "profession", "") or "")
    return int(class_levels(card).get(key, 0))


def is_multiclass(card: Any) -> bool:
    """是不是兼职角色（职业数 > 1）。"""
    return len(class_levels(card)) > 1


def classes_text(card: Any) -> str:
    """展示文本：``战士 5 / 法师 3``（单职业只给职业名）。"""
    parts: list[str] = []
    for key, level in class_levels(card).items():
        try:
            cn = get_class(key).cn
        except KeyError:
            cn = key
        parts.append(f"{cn} {level}")
    return " / ".join(parts)


def hit_die_for_level(card: Any, level: int) -> int:
    """第 ``level`` 级（1 起）加的那个职业的生命骰面数。"""
    order = level_classes(card)
    index = max(1, min(int(level or 1), len(order))) - 1
    key = order[index] if order else ""
    try:
        return int(get_class(key).hit_die)
    except KeyError:
        return 8


def subclass_for(card: Any, profession: str = "") -> str:
    """某职业选的子职键（没选 → 空串）。"""
    key = str(profession or getattr(card, "profession", "") or "")
    book = getattr(card, "subclasses", {}) or {}
    if key in book:
        return str(book.get(key, "") or "")
    # 单职业卡的旧写法：``subclass`` 就是主职业的子职
    if key == str(getattr(card, "profession", "") or ""):
        return str(getattr(card, "subclass", "") or "")
    return ""


# --------------------------------------------------------------------------- #
# 施法
# --------------------------------------------------------------------------- #


def _third_caster_levels(card: Any) -> int:
    """奥法骑士 / 诡术师：这两个子职让战士 / 游荡者按三分之一下取整算施法等级。"""
    third = {"fighter": "fighter-eldritch-knight", "rogue": "rogue-arcane-trickster"}
    total = 0
    for key, level in class_levels(card).items():
        wanted = third.get(key)
        if wanted and subclass_for(card, key) == wanted:
            total += int(level) // 3
    return total


def effective_caster_level(card: Any) -> int:
    """兼职施法等级（查「兼职施法者」表用；魔契师**不计入**）。

    - 吟游诗人 / 牧师 / 德鲁伊 / 术士 / 法师：职业等级全算；
    - 圣武士 / 游侠：一半（向上取整）；
    - 奥法骑士 / 诡术师：三分之一（向下取整）。
    """
    total = 0
    for key, level in class_levels(card).items():
        try:
            kind = get_class(key).caster_kind
        except KeyError:
            continue
        if kind == "full":
            total += int(level)
        elif kind == "half":
            total += (int(level) + 1) // 2
        # pact 不计入；非施法者的三分之一施法者另算
    return min(20, total + _third_caster_levels(card))


def pact_slots(card: Any) -> dict[int, int]:
    """魔契师的契约法术位（与普通法术位**不是同一池**，但花费上通用）。"""
    level = class_level(card, "warlock")
    if level <= 0:
        return {}
    return dict(PACT_SLOTS.get(min(level, 20), {}))


def spell_slots(card: Any) -> dict[int, int]:
    """兼职时的法术位上限：兼职施法者表 + 契约池（同环阶相加）。"""
    out: dict[int, int] = {}
    effective = effective_caster_level(card)
    if effective > 0:
        for level, count in FULL_CASTER_SLOTS.get(min(effective, 20), {}).items():
            out[level] = out.get(level, 0) + int(count)
    for level, count in pact_slots(card).items():
        out[level] = out.get(level, 0) + int(count)
    return out


def primary_caster(card: Any) -> str:
    """等级最高的施法职业键（同级时取先出现的）；没有施法职业返回空串。"""
    best = ""
    best_level = 0
    for key, level in class_levels(card).items():
        try:
            is_caster = get_class(key).is_caster
        except KeyError:
            is_caster = False
        if is_caster and level > best_level:
            best, best_level = key, level
    return best


def casting_ability_for(card: Any, spell: Any = None) -> str:
    """这道法术用哪个职业的施法属性。

    兼职时**法术按所属职业算**（4 级游侠 / 3 级术士：游侠法术用感知、术士法术用
    魅力）。判定顺序：法术的职业列表 ∩ 卡上的施法职业 → 唯一就用它；否则取等级
    最高的施法职业（DM 想换就用 ``update_character`` 调整职业等级）。
    """
    casters = [
        key
        for key in class_levels(card)
        if _is_caster(key)
    ]
    if not casters:
        return ""
    owners = [
        key
        for key in casters
        if spell is not None and key in tuple(getattr(spell, "classes", ()) or ())
    ]
    if len(owners) == 1:
        return get_class(owners[0]).casting
    return get_class(primary_caster(card)).casting if primary_caster(card) else ""


def _is_caster(key: str) -> bool:
    try:
        return bool(get_class(key).is_caster)
    except KeyError:
        return False


# --------------------------------------------------------------------------- #
# 先决与升级
# --------------------------------------------------------------------------- #


def prerequisite(card: Any, profession: str | Any) -> tuple[bool, str]:
    """兼职先决：新职业与**每个现有职业**的主要属性都要到 13。

    :return: ``(是否满足, 不满足时的说明)``
    """
    try:
        new_class = get_class(profession)
    except KeyError:
        return False, f"没有这个职业：{profession}"
    short: list[str] = []
    abilities = getattr(card, "abilities", None)
    for cls in [new_class, *(_existing_classes(card))]:
        try:
            scores = [int(abilities[key]) for key in cls.primary]
        except (TypeError, KeyError):
            continue
        if scores and max(scores) < MAX_PREREQUISITE:
            names = " 或 ".join(_ability_cn(key) for key in cls.primary)
            short.append(f"{cls.cn}（需 {names} 13+）")
    if short:
        return False, "兼职先决不满足：" + "、".join(dict.fromkeys(short))
    return True, ""


def _existing_classes(card: Any) -> list[Any]:
    out: list[Any] = []
    for key in class_levels(card):
        try:
            out.append(get_class(key))
        except KeyError:
            continue
    return out


def _ability_cn(key: str) -> str:
    from .attributes import get_ability

    return get_ability(key).cn


def advance(card: Any, profession: str | Any, *, levels: int = 1) -> str:
    """给角色加上若干**新职业等级**（改卡 / 升级共用；不碰存档，只看卡）。

    会做四件事：先决校验 → 记「这一级加在哪个职业」→ 首次进入补兼职熟练 →
    把 ``level`` 同步成总等级。返回给 DM 看的说明（失败时说明原因、卡不动）。
    """
    try:
        cls = get_class(profession)
    except KeyError:
        return f"没有这个职业：{profession}"
    count = max(1, int(levels or 1))
    total = int(getattr(card, "level", 1) or 1)
    if total + count > 20:
        return f"{cls.cn} 加不进去：角色已经 {total} 级，总等级上限 20。"

    order = level_classes(card)
    was_empty = not bool(getattr(card, "level_classes", []) or [])
    already = class_level(card, cls.key) > 0
    if not already:
        ok, why = prerequisite(card, cls)
        if not ok:
            return why

    order.extend([cls.key] * count)
    card.level_classes = order
    card.level = len(order)
    if not str(getattr(card, "profession", "") or ""):
        # 空卡（没有起始职业）直接兼职 = 第一级就是起始职业
        card.profession = cls.key
    lines = [
        f"{getattr(card, 'name', '') or '角色'} 现在 {classes_text(card)}（Lv.{card.level}）"
    ]
    if was_empty:
        # 第一次兼职：把「其实一直是单职业」的事实落进序列里，之后改卡不再猜
        lines.append(f"职业构成已记下：{classes_text(card)}")
    if not already:
        grant = grants_for(cls)
        if grant.armor:
            card.armor_proficiencies = list(
                dict.fromkeys([*card.armor_proficiencies, *grant.armor])
            )
        if grant.weapons:
            card.weapon_proficiencies = list(
                dict.fromkeys([*card.weapon_proficiencies, *grant.weapons])
            )
        if grant.tools:
            card.equipment = list(dict.fromkeys([*card.equipment, *grant.tools]))
        if grant.empty:
            lines.append(f"兼职 {cls.cn}：这个职业不给额外熟练。")
        else:
            lines.append(f"兼职 {cls.cn} 补熟练：{grant.text()}")
        if grant.skills:
            pool = "、".join(cls.skill_names())
            lines.append(
                f"还要从 {cls.cn} 的技能池里选 {grant.skills} 项（{pool}）——"
                "用 update_character 的「技能」字段补。"
            )
        lines.append(
            f"⚠️ 兼职不给豁免熟练，也**不叠**额外攻击；{cls.cn} 的特性按它自己的等级"
            "解锁。"
        )
    if class_level(card, cls.key) == 3 and not subclass_for(card, cls.key):
        lines.append(
            f"{cls.cn} 到 3 级了，可以选它的子职 —— 用 update_character 的「子职」"
            f"字段指定（写法「{cls.cn}:子职名」）。"
        )
    return "\n".join(lines)
