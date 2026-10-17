"""NPC 数据与名单。

对应「与 NPC 战斗」这一需求。DM 在剧情里临时拉出来的角色（商人、佣兵、卫兵队长……）
不该被怪物表绑死，因此这里提供两件事：

1. **档位表**：平民 / 士兵 / 老兵 / 首领 四档人形 NPC。DM 只报档位，系统按
   **队伍等级**现算 AC / 生命 / 攻击（等级越高越硬），生成的
   :class:`~.monsters.Monster` 用 key ``npc/<档位>/<等级>`` 标识，
   :func:`resolve_monster` 任何时候都能把它还原出来（战斗落盘只存 key）。
   DM 也可以直接指定用怪物表里的模板（``黑心商人卡尔:匪帮首领``），此时沿用那张数据块。
2. **NPC 名单**（``saves/<存档ID>/npcs.json``）：出场过的**具名** NPC 记下名字、模板、
   当前生命、状态、备注与最后出现地点。下次再出场时带着旧伤与旧账，而不是满血重生。
   只当背景板的杂兵（``士兵x2`` 这种没名字的）不落名单。

数值同 :mod:`.monsters`，是可玩的近似值，**以本表为准**。
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from ATRI.exceptions import str_traceback
from ATRI.log import log

from .monsters import Monster, MonsterAttack, get_monster
from .save_store import Save, save_dir

__all__ = [
    "NPC_FILENAME",
    "NPC_TIERS",
    "NPC_TIER_MAP",
    "NpcRecord",
    "NpcTier",
    "build_npc",
    "find_npc",
    "get_tier",
    "level_of_tier",
    "list_npcs",
    "load_npcs",
    "npc_monster",
    "npc_names",
    "npcs_path",
    "npcs_text",
    "register_encounter",
    "remove_npc",
    "resolve_monster",
    "roster_hp",
    "save_npcs",
    "sync_combatant",
    "tier_by_key",
    "tier_from_key",
    "tier_names",
    "update_npc",
    "upsert_npc",
]

NPC_FILENAME = "npcs.json"
#: 档位模板的 key 前缀
NPC_KEY_PREFIX = "npc"
#: 档位随等级成长时的等级上限（再高的队伍就按这个等级给数值）
MAX_NPC_LEVEL = 12


# --------------------------------------------------------------------------- #
# 档位表
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class NpcTier:
    """人形 NPC 的一档难度。数值按队伍等级现算。"""

    key: str
    cn: str
    en: str
    desc: str
    ac: int  #: 1 级队伍时的 AC
    hp: int  #: 1 级队伍时的生命值
    hp_growth: int  #: 队伍每高 1 级增加的生命值
    attack_name: str  #: 攻击名
    damage: str  #: 伤害表达式（不含加值）
    damage_type: str
    abilities: Mapping[str, int]  #: 六大属性
    attack_ability: str = "str"  #: 结算攻击加值用的属性
    ac_bonus: int = 0  #: 队伍达到 ``ac_bonus_level`` 起追加的 AC
    ac_bonus_level: int = 5
    multiattack_level: int = 0  #: 队伍达到该等级起获得「每回合两次攻击」
    saves: tuple[str, ...] = ()
    traits: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()

    @property
    def attack(self) -> MonsterAttack:
        return MonsterAttack(
            self.attack_name, self.attack_ability, self.damage, self.damage_type
        )

    def level_hp(self, level: int) -> int:
        """该等级队伍下这个档位的生命值。"""
        return self.hp + self.hp_growth * (max(1, int(level)) - 1)

    def level_ac(self, level: int) -> int:
        """该等级队伍下这个档位的 AC。"""
        level = max(1, int(level))
        return self.ac + (self.ac_bonus if level >= self.ac_bonus_level else 0)

    def level_traits(self, level: int) -> tuple[str, ...]:
        """该等级下的特性（到等级会有多攻击）。"""
        level = max(1, int(level))
        traits: list[str] = []
        if self.multiattack_level and level >= self.multiattack_level:
            traits.append("多攻击：每回合攻击两次")
        traits.extend(self.traits)
        return tuple(traits)


NPC_TIERS: tuple[NpcTier, ...] = (
    NpcTier(
        key="civilian",
        cn="平民",
        en="Civilian",
        desc="没有受过训练的普通人：店员、农夫、书生。一推就倒，但也不一定非要打。",
        ac=10,
        hp=4,
        hp_growth=2,
        attack_name="拳脚 / 顺手家伙",
        damage="1d4",
        damage_type="钝击",
        abilities={"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10},
        traits=("见血就跑，会喊人",),
        aliases=(
            "村民",
            "百姓",
            "市民",
            "路人",
            "农民",
            "学徒",
            "伙计",
            "难民",
            "老翁",
            "小贩",
        ),
    ),
    NpcTier(
        key="soldier",
        cn="士兵",
        en="Soldier",
        desc="吃这碗饭的普通武装：巡逻兵、佣兵、打手、保镖。有点甲，有点训练。",
        ac=13,
        hp=11,
        hp_growth=7,
        attack_name="长剑",
        damage="1d8+1",
        damage_type="挥砍",
        abilities={"str": 12, "dex": 12, "con": 12, "int": 10, "wis": 11, "cha": 10},
        traits=("皮甲 + 盾牌 / 锁子甲",),
        aliases=("佣兵", "打手", "巡逻兵", "军士", "保镖", "武装平民", "打手们"),
    ),
    NpcTier(
        key="veteran",
        cn="老兵",
        en="Veteran",
        desc="上过战场的老手：副官、剑士、佣兵头目。经验、甲具与杀意都到位。",
        ac=15,
        hp=22,
        hp_growth=10,
        attack_name="长剑",
        damage="1d8+2",
        damage_type="挥砍",
        abilities={"str": 14, "dex": 13, "con": 14, "int": 11, "wis": 12, "cha": 11},
        ac_bonus=1,
        ac_bonus_level=5,
        multiattack_level=5,
        saves=("con", "wis"),
        traits=("老兵直觉：不会轻易被吓退，懂得配合夹击",),
        aliases=("队长", "精锐", "副官", "军官", "剑士", "雇佣兵头目", "军团长"),
    ),
    NpcTier(
        key="leader",
        cn="首领",
        en="Leader",
        desc="这一带的头面人物：帮派老大、贵族武官、族中强者。打得赢也未必敢打。",
        ac=16,
        hp=45,
        hp_growth=15,
        attack_name="长剑",
        damage="1d10+3",
        damage_type="挥砍",
        abilities={"str": 16, "dex": 14, "con": 15, "int": 12, "wis": 12, "cha": 13},
        ac_bonus=1,
        ac_bonus_level=9,
        multiattack_level=5,
        saves=("str", "con", "wis"),
        traits=("号令：身边的手下听他的指挥", "格挡：单手持械时 AC +2"),
        aliases=("头目", "老大", "贵族", "将领", "大人物", "族长", "帮主"),
    ),
)

NPC_TIER_MAP: dict[str, NpcTier] = {}
for _tier in NPC_TIERS:
    NPC_TIER_MAP[_tier.key] = _tier
    NPC_TIER_MAP[_tier.cn] = _tier
    NPC_TIER_MAP[_tier.en.lower()] = _tier
    for _alias in _tier.aliases:
        NPC_TIER_MAP.setdefault(_alias, _tier)


def tier_names() -> list[str]:
    """四档的中文名（提示 DM 用）。"""
    return [t.cn for t in NPC_TIERS]


def tier_by_key(value: str) -> NpcTier | None:
    """**只**按键 / 中文名 / 英文名 / 别名精确取档位（不做包含匹配）。

    用来和怪物表的宽松匹配划清界限：``匪帮首领`` 是怪物，``首领`` 才是档位。
    """
    text = str(value or "").strip()
    if not text:
        return None
    return NPC_TIER_MAP.get(text) or NPC_TIER_MAP.get(text.lower())


def get_tier(value: NpcTier | str) -> NpcTier | None:
    """按对象 / 键 / 中文名 / 别名取档位，取不到返回 ``None``（末段做包含匹配）。"""
    if isinstance(value, NpcTier):
        return value
    exact = tier_by_key(str(value or ""))
    if exact is not None:
        return exact
    text = str(value or "").strip()
    if not text:
        return None
    for name, tier in NPC_TIER_MAP.items():
        if name in text or text in name:
            return tier
    return None


def _hit_dice_for(hp: int) -> str:
    """把生命值折成一个 d8 表达式（平均命中该值），保持与怪物表一致的掷骰手感。"""
    hp = max(1, int(hp))
    dice = max(1, min(30, round(hp / 4.5)))
    bonus = round(hp - dice * 4.5)
    if bonus == 0:
        return f"{dice}d8"
    return f"{dice}d8{bonus:+d}"


def npc_monster(value: NpcTier | str, level: int = 1) -> Monster | None:
    """按档位 + 队伍等级造一只怪物数据块；认不出档位返回 ``None``。"""
    tier = get_tier(value)
    if tier is None:
        return None
    return _npc_monster_cached(tier.key, max(1, min(int(level or 1), MAX_NPC_LEVEL)))


@lru_cache(maxsize=256)
def _npc_monster_cached(tier_key: str, level: int) -> Monster:
    """档位模板的缓存构造（参数只放可哈希的键与等级）。"""
    tier = NPC_TIER_MAP[tier_key]
    hp = tier.level_hp(level)
    return Monster(
        key=f"{NPC_KEY_PREFIX}/{tier.key}/{level}",
        cn=tier.cn,
        en=tier.en,
        cr=float(level),
        size="中型",
        kind="类人",
        ac=tier.level_ac(level),
        hp=hp,
        hit_dice=_hit_dice_for(hp),
        speed=30,
        abilities=dict(tier.abilities),
        attacks=(tier.attack,),
        saves=tuple(tier.saves),
        traits=tier.level_traits(level),
        senses="被动察觉 10",
        languages="通用语",
        desc=f"{tier.desc}（按 {level} 级队伍给的数值）",
        aliases=(),
    )


def tier_from_key(value: str) -> tuple[NpcTier, int] | None:
    """解析 ``npc/veteran/3`` 或 ``npc/老兵/3`` 形式的 key。"""
    text = str(value or "").strip()
    if not text.startswith(f"{NPC_KEY_PREFIX}/"):
        return None
    parts = text.split("/")
    if len(parts) < 2:
        return None
    tier = get_tier(parts[1])
    if tier is None:
        return None
    level = 1
    if len(parts) >= 3:
        try:
            level = int(parts[2])
        except ValueError:
            level = 1
    return tier, max(1, min(level, MAX_NPC_LEVEL))


def level_of_tier(value: str) -> int:
    """从档位 key 里取回等级（取不到按 1）。"""
    parsed = tier_from_key(value)
    return parsed[1] if parsed else 1


def resolve_monster(value) -> Monster | None:
    """把战斗状态里存的 ``monster`` 字段还原成数据块：先查怪物表，再查档位模板。"""
    monster = get_monster(value)
    if monster is not None:
        return monster
    parsed = tier_from_key(str(value or ""))
    if parsed is not None:
        tier, level = parsed
        return npc_monster(tier, level)
    return None


def build_npc(template: str, level: int = 1) -> tuple[Monster, str] | None:
    """按 DM 写的模板造一个可参战的 NPC。

    :param template: 档位名（``老兵`` / ``打手``）或怪物表里的名字（``匪帮首领`` / ``卫兵``）。
    :return: ``(数据块, 存进战斗状态的模板键)``；不认识返回 ``None``。

    匹配顺序：**档位精确名** → 怪物表（含别名）→ 档位包含匹配。
    这样 ``首领`` 落到档位、``匪帮首领`` 落到怪物，不会互相抢。
    """
    tier = tier_by_key(template)
    if tier is not None:
        monster = npc_monster(tier, level)
        if monster is None:
            return None
        return monster, monster.key
    monster = get_monster(template)
    if monster is not None:
        return monster, monster.key
    tier = get_tier(template)
    if tier is None:
        return None
    monster = npc_monster(tier, level)
    if monster is None:
        return None
    return monster, monster.key


# --------------------------------------------------------------------------- #
# NPC 名单（落盘）
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class NpcRecord:
    """名单上的一名 NPC。"""

    name: str
    template: str = "soldier"  #: 档位 key 或怪物 key
    level: int = 1  #: 生成时的队伍等级（档位模板按它还原数值）
    hp: int = 0  #: 当前生命值
    max_hp: int = 0
    status: str = ""  #: 存活 / 受伤 / 倒下 / 逃跑 / 被俘 / 失踪……
    note: str = ""  #: 关系、身份、欠下的账……
    place: str = ""  #: 最后出现的地点 / 场景
    met: int = 1  #: 出场次数
    first_seen: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "template": self.template,
            "level": self.level,
            "hp": self.hp,
            "max_hp": self.max_hp,
            "status": self.status,
            "note": self.note,
            "place": self.place,
            "met": self.met,
            "first_seen": self.first_seen,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> NpcRecord:
        return cls(
            name=str(data.get("name", "") or ""),
            template=str(data.get("template", "soldier") or "soldier"),
            level=int(data.get("level", 1) or 1),
            hp=int(data.get("hp", 0) or 0),
            max_hp=int(data.get("max_hp", 0) or 0),
            status=str(data.get("status", "") or ""),
            note=str(data.get("note", "") or ""),
            place=str(data.get("place", "") or ""),
            met=int(data.get("met", 1) or 1),
            first_seen=str(data.get("first_seen", "") or ""),
            updated_at=str(data.get("updated_at", "") or ""),
        )

    # —— 派生 ——

    @property
    def monster(self) -> Monster | None:
        """按模板还原数据块。"""
        return resolve_monster(self.template) if self.template else None

    @property
    def template_text(self) -> str:
        """模板的可读写法：档位带等级，怪物模板直接写名字。"""
        tier = get_tier(self.template)
        if tier is not None:
            return f"{tier.cn}（按 {self.level} 级队伍）"
        monster = get_monster(self.template)
        return monster.cn if monster else self.template or "未知"

    @property
    def hp_text(self) -> str:
        return f"{self.hp}/{self.max_hp}" if self.max_hp else "—"

    def brief(self) -> str:
        """一行摘要（查询 / 提示用）。"""
        text = f"{self.name}　{self.template_text}　HP {self.hp_text}"
        if self.status:
            text += f"　{self.status}"
        if self.place:
            text += f"　在{self.place}"
        if self.note:
            text += f"　（{self.note}）"
        if self.met > 1:
            text += f"　出场 {self.met} 次"
        return text


def npcs_path(save_id: str) -> Path:
    """某存档 NPC 名单的落盘路径（``npcs.json``）。"""
    return save_dir(save_id) / NPC_FILENAME


def load_npcs(save: Save) -> dict[str, NpcRecord]:
    """读取 NPC 名单；没有或读坏了返回空表。"""
    path = npcs_path(save.save_id)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning(f"读取 NPC 名单失败：{path}（{str_traceback(e)}）")
        return {}
    if not isinstance(data, dict):
        return {}
    records: dict[str, NpcRecord] = {}
    for key, item in data.items():
        if isinstance(item, dict):
            record = NpcRecord.from_dict(item)
            records[str(record.name or key)] = record
    return records


def save_npcs(save: Save, records: Mapping[str, NpcRecord]) -> Path:
    """写入 NPC 名单。"""
    path = npcs_path(save.save_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {name: record.to_dict() for name, record in records.items()}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def list_npcs(save: Save) -> list[NpcRecord]:
    """名单上的所有 NPC（按出场次数与名字排序）。"""
    records = load_npcs(save)
    return sorted(records.values(), key=lambda r: (-r.met, r.name))


def find_npc(save: Save, name: str) -> NpcRecord | None:
    """按名字找名单上的 NPC；支持包含匹配。"""
    text = str(name or "").strip()
    if not text:
        return None
    records = load_npcs(save)
    if text in records:
        return records[text]
    for key, record in records.items():
        if key in text or text in key:
            return record
    return None


def upsert_npc(save: Save, record: NpcRecord) -> NpcRecord:
    """把一名 NPC 写进名单（同名覆盖），并刷新更新时间。"""
    records = load_npcs(save)
    old = records.get(record.name)
    if old is not None:
        record.met = max(1, record.met, old.met)
        if not record.first_seen:
            record.first_seen = old.first_seen
        if not record.note:
            record.note = old.note
        if not record.place:
            record.place = old.place
        if not record.status:
            record.status = old.status
    if not record.first_seen:
        record.first_seen = time.strftime("%Y-%m-%d %H:%M", time.localtime())
    record.updated_at = time.strftime("%Y-%m-%d %H:%M", time.localtime())
    records[record.name] = record
    save_npcs(save, records)
    return record


def update_npc(
    save: Save,
    name: str,
    *,
    hp: int | None = None,
    max_hp: int | None = None,
    status: str | None = None,
    note: str | None = None,
    place: str | None = None,
    template: str | None = None,
    level: int | None = None,
) -> NpcRecord | None:
    """改名单上某名 NPC 的状态；找不到返回 ``None``。"""
    record = find_npc(save, name)
    if record is None:
        return None
    if hp is not None:
        record.hp = max(0, int(hp))
    if max_hp is not None:
        record.max_hp = max(1, int(max_hp))
    if status is not None:
        record.status = status
    if note is not None:
        record.note = note
    if place is not None:
        record.place = place
    if template:
        built = build_npc(template, level or record.level)
        if built is not None:
            monster, key = built
            record.template = key
            record.level = level or record.level
            if max_hp is None:
                record.max_hp = monster.hp
    elif level is not None:
        record.level = max(1, int(level))
    return upsert_npc(save, record)


def remove_npc(save: Save, name: str) -> bool:
    """把某名 NPC 从名单上删掉。"""
    records = load_npcs(save)
    record = find_npc(save, name)
    if record is None:
        return False
    records.pop(record.name, None)
    save_npcs(save, records)
    return True


def npc_names(save: Save) -> list[str]:
    """名单上的名字。"""
    return [r.name for r in list_npcs(save)]


def npcs_text(save: Save, limit: int = 20) -> str:
    """名单文本（DM 查询 / 玩家看到的熟人）。"""
    records = list_npcs(save)
    if not records:
        return "（这局还没有记录在案的 NPC）"
    lines = [f"已知 NPC（{len(records)} 名）："]
    for record in records[:limit]:
        lines.append("  " + record.brief())
    if len(records) > limit:
        lines.append(f"  ……还有 {len(records) - limit} 名")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 与战斗状态对接
# --------------------------------------------------------------------------- #


def roster_hp(save: Save, name: str) -> tuple[int, int] | None:
    """出场过的 NPC 带着的旧伤：``(当前生命, 生命上限)``；没记录返回 ``None``。"""
    record = find_npc(save, name)
    if record is None or record.max_hp <= 0:
        return None
    return max(0, min(record.hp, record.max_hp)), record.max_hp


def register_encounter(
    save: Save,
    *,
    name: str,
    template: str,
    level: int,
    hp: int,
    max_hp: int,
    place: str = "",
) -> NpcRecord:
    """登记一次出场：名单上有就带旧状态更新，没有就新建。"""
    old = find_npc(save, name)
    record = NpcRecord(
        name=name,
        template=template,
        level=int(level or 1),
        hp=max(0, int(hp)),
        max_hp=max(1, int(max_hp)),
        status="",
        place=place or (old.place if old else ""),
        met=(old.met if old else 0) + 1,
    )
    if old is not None:
        if old.status and record.hp > 0:
            record.status = old.status
        if record.hp <= 0:
            record.status = old.status or "倒下"
    return upsert_npc(save, record)


def sync_combatant(save: Save, enemy) -> None:
    """把战斗中的敌人状态同步回名单（只对具名 NPC 生效）。"""
    name = str(getattr(enemy, "npc", "") or "")
    if not name:
        return
    record = find_npc(save, name)
    if record is None:
        return
    record.hp = max(0, int(getattr(enemy, "hp", 0) or 0))
    record.max_hp = max(1, int(getattr(enemy, "max_hp", 0) or record.max_hp))
    if record.hp <= 0:
        record.status = record.status or "倒下"
    elif record.status in {"倒下"}:
        record.status = "存活"
    upsert_npc(save, record)
