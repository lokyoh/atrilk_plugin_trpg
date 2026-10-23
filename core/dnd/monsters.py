"""DND 5e 怪物数据表。

对应战斗系统需要的**敌人侧数值**：AC、生命（含生命骰）、速度、六大属性、攻击
（攻击加值由属性调整值 + 熟练加值算出、伤害骰原样给出）、豁免 / 技能熟练、特性。

设计约定：

- **攻击加值不写死**，由 :meth:`Monster.attack_bonus` 用「熟练加值（按 CR）+ 属性调整值」
  现算；伤害表达式里的加值与该属性保持一致，所以同一份数据既给 DM 看也能直接掷。
- 加值 / 豁免 / 技能沿用 :mod:`.attributes` 与 :mod:`.skills` 的键，与角色卡同一套算法。
- **数值来自数据文件**（``res/dnd/monsters/*.yaml``），不写在代码里。

**加一只怪不用改代码**：往 ``res/dnd/monsters/`` 丢一张 yaml 就行（顶层是列表，
一条一项）。目录里的表按文件名排序加载，**同名条目先到先得**——所以 ``dosi`` /
``lmop``（模组自带）压过 ``xmm``（图鉴）。

``mm`` 那张是**例外**：2025 版图鉴删掉了一批生物，本项目用得上，所以从 2014 版
图鉴单独补录（出处 ``MM``）——兽人三兄弟（失落矿坑的兽人营地要用）与 2014 独有
的动物（甲虫群 / 蜈蚣群 / 蜘蛛群 / 黄蜂群 / 食人鱼群 / 毒蛇群 / 穴居熊）。
它只放这十只——放全了会整批压在 2025 那张上（``mm`` 排在 ``xmm`` 前面）。

表里**只收有出处的怪物**（图鉴 / 模组数据卡），本项目自己编数值的一概不收——
图鉴里查不到的怪就该由 DM 临场定，不该混在数值表里冒充标准数据。

生成器 / DM 通过 :func:`get_monster` 按 key、中文名或别名取怪物。
"""

from __future__ import annotations

from dataclasses import replace

from ..trpg.dice import DiceError, parse_dice
from .datafiles import DATA_DIR, load_table
from .monster_types import (
    Monster,
    MonsterAttack,
    _cr_text,
    monster_from_row,
    proficiency_for_cr,
)

__all__ = [
    "MONSTERS",
    "MONSTER_MAP",
    "SCALE_SUFFIX",
    "Monster",
    "MonsterAttack",
    "get_monster",
    "monster_names",
    "proficiency_for_cr",
    "scale_monster",
]


#: 缩放标记：``<基础 key>@cr<目标 CR>``（如 ``goblin@cr3``），落盘后可原样还原
SCALE_SUFFIX = "@cr"

#: 各挑战等级下「标准中型怪物」的参考生命值，用来给怪物做 CR 缩放
#: （同 CR 的怪之间体格差异很大，这里只取一个中间值当基准）
_CR_HP_REFERENCE: tuple[tuple[float, int], ...] = (
    (0.0, 4),
    (0.125, 7),
    (0.25, 9),
    (0.5, 11),
    (1.0, 13),
    (2.0, 22),
    (3.0, 30),
    (4.0, 38),
    (5.0, 50),
    (6.0, 62),
    (7.0, 75),
    (8.0, 88),
    (9.0, 100),
    (10.0, 115),
    (11.0, 130),
    (12.0, 145),
    (13.0, 160),
    (14.0, 175),
    (15.0, 190),
    (16.0, 205),
    (17.0, 220),
    (18.0, 235),
    (19.0, 250),
    (20.0, 280),
)


def _reference_hp(cr: float) -> float:
    """某个 CR 下标准中型怪物的参考生命值（档位之间线性插值）。"""
    value = max(0.0, float(cr))
    for index in range(1, len(_CR_HP_REFERENCE)):
        low_cr, low_hp = _CR_HP_REFERENCE[index - 1]
        high_cr, high_hp = _CR_HP_REFERENCE[index]
        if value <= high_cr:
            if high_cr == low_cr:
                return float(high_hp)
            ratio = (value - low_cr) / (high_cr - low_cr)
            return low_hp + (high_hp - low_hp) * ratio
    last_cr, last_hp = _CR_HP_REFERENCE[-1]
    # 超出表尾按每级 +30 继续外推
    return float(last_hp + (value - last_cr) * 30)


def _scale_token(cr: float) -> str:
    """把 CR 写成可还原的短字符串（``0.5`` / ``3`` / ``0.125``）。"""
    return f"{float(cr):g}"


def split_scale_suffix(text: str) -> tuple[str, float | None]:
    """拆出缩放后缀：``"goblin@cr3"`` → ``("goblin", 3.0)``；没有后缀时 CR 为 ``None``。"""
    raw = str(text or "").strip()
    marker = raw.rfind(SCALE_SUFFIX)
    if marker < 0:
        return raw, None
    base = raw[:marker].strip()
    tail = raw[marker + len(SCALE_SUFFIX) :].strip()
    try:
        return base, float(tail)
    except ValueError:
        return raw, None


def scale_key(base_key: str, cr: float) -> str:
    """缩放后怪物的落盘 key（``goblin`` + CR3 → ``goblin@cr3``）。"""
    return f"{base_key}{SCALE_SUFFIX}{_scale_token(cr)}"


# --------------------------------------------------------------------------- #
# 数据表（CR 0 ~ 10）
# --------------------------------------------------------------------------- #

#: 怪物表的目录（``res/dnd/monsters/``）：一张表一个文件，加怪就加文件
MONSTER_DIR = "monsters"


def monster_table_names() -> tuple[str, ...]:
    """目录下有哪些表（按文件名排序 = 加载顺序，**先到先得**）。"""
    return tuple(sorted(path.stem for path in (DATA_DIR / MONSTER_DIR).glob("*.yaml")))


def _load_monsters() -> tuple[Monster, ...]:
    """把目录下的表合并成一张总表；同名（中文名）只留先加载的那条。"""
    seen: set[str] = set()
    loaded: list[Monster] = []
    for name in monster_table_names():
        for row in load_table(f"{MONSTER_DIR}/{name}"):
            monster = monster_from_row(row)
            if monster.cn in seen:
                continue  # 模组自带的版本压过图鉴那份
            seen.add(monster.cn)
            loaded.append(monster)
    return tuple(loaded)


#: 完整怪物表：目录下所有表合并（xmm / dosi / lmop ……）
MONSTERS: tuple[Monster, ...] = _load_monsters()

#: 键 / 中文名 / 英文名 / 别名 -> 怪物
MONSTER_MAP: dict[str, Monster] = {}
for _monster in MONSTERS:
    MONSTER_MAP[_monster.key] = _monster
    MONSTER_MAP[_monster.cn] = _monster
    MONSTER_MAP[_monster.en.lower()] = _monster
    for _alias in _monster.aliases:
        MONSTER_MAP.setdefault(_alias, _monster)


def get_monster(value: Monster | str) -> Monster | None:
    """按对象 / 键 / 中文名 / 别名取怪物，取不到返回 ``None``。

    也认**缩放 key**：``"哥布林@cr3"`` = 哥布林按 CR3 重算的数值。
    """
    if isinstance(value, Monster):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    base, target_cr = split_scale_suffix(text)
    if target_cr is not None:
        found = get_monster(base)
        if found is None:
            return None
        return scale_monster(found, target_cr)
    if text in MONSTER_MAP:
        return MONSTER_MAP[text]
    lowered = text.lower()
    if lowered in MONSTER_MAP:
        return MONSTER_MAP[lowered]
    for name, monster in MONSTER_MAP.items():
        if name in text or text in name:
            return monster
    return None


def monster_names() -> list[str]:
    """所有怪物的中文名（按 CR 升序），用于提示 DM 可用的敌人。"""
    return [m.cn for m in sorted(MONSTERS, key=lambda m: m.cr)]


# --------------------------------------------------------------------------- #
# CR 缩放：把一只现成怪物按目标 CR 重算数值
# --------------------------------------------------------------------------- #


def _scale_damage(expr: str, bonus: int) -> str:
    """给一条伤害表达式加上 ``bonus`` 点加值（骰面数不变，只动固定加值）。"""
    text = str(expr or "").strip()
    if not text:
        return text
    if text.isdigit():
        return str(max(0, int(text) + bonus))
    try:
        parsed = parse_dice(text)
    except DiceError:
        return text
    modifier = parsed.modifier + bonus
    out = f"{parsed.count}d{parsed.faces}"
    if modifier:
        out += f"{modifier:+d}"
    return out


def _hit_dice_for(hp: int) -> str:
    """把生命值折成一个 d8 表达式（平均命中该值），与怪物表保持同样的掷骰手感。"""
    hp = max(1, int(hp))
    dice = max(1, min(30, round(hp / 4.5)))
    bonus = round(hp - dice * 4.5)
    if bonus == 0:
        return f"{dice}d8"
    return f"{dice}d8{bonus:+d}"


def scale_monster(monster: Monster | str, target_cr: float) -> Monster | None:
    """把一只怪物按目标挑战等级重算数值。

    缩放口径（近似值，与 :mod:`.progression` 的 CR 经验表配套）：

    - **生命**：按「同 CR 标准怪物的参考生命值」之比缩放，保留原怪的体型特征
      （血多的怪缩放后依然血多）；生命骰按新生命重算，保证战斗中掷出的生命与表一致。
    - **AC**：每 2 级 CR 差 ±1 AC（向上取整），下限 10。
    - **熟练加值**：随 CR 自动变化（见 :func:`proficiency_for_cr`），攻击加值因此自动跟随。
    - **伤害**：伤害骰不变，固定加值按熟练加值的变化量同步调整（伤害与攻击同步变强）。
    - **key**：写成 ``<基础 key>@cr<目标 CR>``，落盘后可原样还原（见 :func:`get_monster`）。

    :param target_cr: 目标挑战等级（可以是 0.5 这类小数）
    :return: 缩放后的数据块；基础怪物认不出来时返回 ``None``
    """
    base = monster if isinstance(monster, Monster) else get_monster(monster)
    if base is None:
        return None
    target = max(0.0, float(target_cr))
    if abs(target - base.cr) < 1e-9:
        return base

    # —— 生命 ——
    base_ref = _reference_hp(base.cr)
    target_ref = _reference_hp(target)
    factor = target_ref / base_ref if base_ref > 0 else 1.0
    new_hp = max(1, round(base.hp * factor))

    # —— AC ——
    ac_delta = round((target - base.cr) / 2)
    new_ac = max(10, base.ac + ac_delta)

    # —— 伤害（与熟练加值同步） ——
    damage_bonus = proficiency_for_cr(target) - proficiency_for_cr(base.cr)
    new_attacks = tuple(
        replace(attack, damage=_scale_damage(attack.damage, damage_bonus))
        for attack in base.attacks
    )

    traits = list(base.traits)
    traits.insert(0, f"CR 校准：基础 CR{base.cr_text} → CR{_cr_text(target)}")

    return replace(
        base,
        key=scale_key(base.key, target),
        cr=target,
        ac=new_ac,
        hp=new_hp,
        hit_dice=_hit_dice_for(new_hp),
        attacks=new_attacks,
        traits=tuple(traits),
        desc=f"{base.desc}（按 CR{_cr_text(target)} 校准后的数值）",
    )
