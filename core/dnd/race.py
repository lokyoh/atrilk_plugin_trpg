"""DND 5e 种族数据。

对应流程中的 ``race.md``（建卡第 2 步）。每个种族给出属性加值、体型 / 速度、
特性描述与可选的语言、亚种；亚种的属性加值在种族加值之上再叠加。
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "RACES",
    "RACE_MAP",
    "Race",
    "SubRace",
    "default_subrace",
    "get_race",
    "race_choices",
]


@dataclass(frozen=True, slots=True)
class SubRace:
    """亚种（如山地矮人、高等精灵）。"""

    key: str
    cn: str
    bonus: dict[str, int] = field(default_factory=dict)
    traits: tuple[str, ...] = ()
    desc: str = ""

    @property
    def label(self) -> str:
        return self.cn


@dataclass(frozen=True, slots=True)
class Race:
    """种族。"""

    key: str
    cn: str
    en: str
    bonus: dict[str, int]
    speed: int
    size: str
    traits: tuple[str, ...]
    languages: tuple[str, ...] = ()
    subraces: tuple[SubRace, ...] = ()
    desc: str = ""

    def bonus_of(self, subrace: SubRace | str | None = None) -> dict[str, int]:
        """叠加亚种后的属性加值。"""
        bonus = dict(self.bonus)
        sub = self.resolve_subrace(subrace) if subrace else None
        if sub:
            for key, value in sub.bonus.items():
                bonus[key] = bonus.get(key, 0) + value
        return bonus

    def resolve_subrace(self, value: SubRace | str | None) -> SubRace | None:
        """把亚种名 / 键解析为亚种对象；无亚种时返回 ``None``。"""
        if value is None or isinstance(value, SubRace):
            return value
        text = str(value).strip()
        for sub in self.subraces:
            if text in (sub.key, sub.cn):
                return sub
        raise KeyError(f"{self.cn}没有名为「{value}」的亚种")

    def traits_of(self, subrace: SubRace | str | None = None) -> tuple[str, ...]:
        """种族特性 + 亚种特性。"""
        traits = list(self.traits)
        sub = self.resolve_subrace(subrace) if subrace else None
        if sub:
            traits.extend(sub.traits)
        return tuple(traits)

    def format_bonus(self, subrace: SubRace | str | None = None) -> str:
        """属性加值的展示文本。"""
        from .attributes import get_ability

        bonus = self.bonus_of(subrace)
        if not bonus:
            return "无属性加值"
        return "、".join(f"{get_ability(k).cn}{v:+d}" for k, v in bonus.items() if v)


RACES: tuple[Race, ...] = (
    Race(
        key="human",
        cn="人类",
        en="Human",
        bonus={"str": 1, "dex": 1, "con": 1, "int": 1, "wis": 1, "cha": 1},
        speed=30,
        size="中型",
        traits=("全属性 +1，适应力极强，在任意文明地区都不易引起注意",),
        languages=("通用语", "一门自选语言"),
        desc="寿命短却野心勃勃，遍布世界的每一个角落，是最常见的冒险者。",
    ),
    Race(
        key="elf",
        cn="精灵",
        en="Elf",
        bonus={"dex": 2},
        speed=30,
        size="中型",
        traits=(
            "黑暗视觉 60 尺",
            "敏锐感官（察觉熟练）",
            "精类血统：免疫魔法睡眠，对抗魅惑有优势",
            "出神：4 小时完成长休",
        ),
        languages=("通用语", "精灵语"),
        subraces=(
            SubRace(
                "high_elf",
                "高等精灵",
                bonus={"int": 1},
                traits=(
                    "额外习得一门自选语言",
                    "戏法：任选一个法师戏法",
                    "武器训练：长剑、短剑、短弓、长弓",
                ),
                desc="血脉中流淌着魔法，学识渊博。",
            ),
            SubRace(
                "wood_elf",
                "木精灵",
                bonus={"wis": 1},
                traits=(
                    "额外习得一门自选语言",
                    "轻捷步伐：速度提升至 35 尺",
                    "林间隐匿：自然环境中隐匿有优势",
                ),
                desc="与森林共生，行动迅捷而隐秘。",
            ),
            SubRace(
                "drow",
                "卓尔（黑暗精灵）",
                bonus={"cha": 1},
                traits=(
                    "黑暗视觉提升至 120 尺",
                    "阳光敏感：直射阳光下攻击与察觉检定有劣势",
                    "卓尔魔法：舞光术；等级提升后可习得浮空术、黑暗术",
                ),
                desc="幽暗地域的精灵，被地表世界所忌惮。",
            ),
        ),
        desc="优雅而长寿的族群，感官敏锐，与魔法有着天然的亲和。",
    ),
    Race(
        key="dwarf",
        cn="矮人",
        en="Dwarf",
        bonus={"con": 2},
        speed=25,
        size="中型",
        traits=(
            "黑暗视觉 60 尺",
            "矮人坚韧：对抗毒素有优势，且对中毒有抗性",
            "矮人战斗训练：战斧、手斧、轻锤、战锤",
            "工具熟练：任选一种工匠工具",
            "石材敏锐：石制建筑相关的历史检定有双倍熟练加值",
        ),
        languages=("通用语", "矮人语"),
        subraces=(
            SubRace(
                "hill_dwarf",
                "丘陵矮人",
                bonus={"wis": 1},
                traits=("矮人强健：每级生命值上限 +1",),
                desc="坚韧而警觉，最有耐心的一支。",
            ),
            SubRace(
                "mountain_dwarf",
                "山地矮人",
                bonus={"str": 2},
                traits=("矮人护甲训练：可穿轻甲与中甲",),
                desc="矮人中的战士与铁匠，身强体壮。",
            ),
        ),
        desc="岩石与炉火之民，坚韧、固执而重信义。",
    ),
    Race(
        key="halfling",
        cn="半身人",
        en="Halfling",
        bonus={"dex": 2},
        speed=25,
        size="小型",
        traits=(
            "幸运：d20 掷出 1 时可重掷一次",
            "勇敢：对抗恐惧有优势",
            "半身人灵巧：可穿过比自身大一级生物的空间",
        ),
        languages=("通用语", "半身人语"),
        subraces=(
            SubRace(
                "lightfoot",
                "轻足半身人",
                bonus={"cha": 1},
                traits=("天生善于隐藏，在仅被较大生物遮挡时也能尝试隐匿",),
                desc="善于交际、行踪不定。",
            ),
            SubRace(
                "stout",
                "壮足半身人",
                bonus={"con": 1},
                traits=("对抗毒素有优势，且对毒素伤害有抗性",),
                desc="比同族更结实耐劳。",
            ),
        ),
        desc="身形娇小却运气极佳，重视安逸的家园生活。",
    ),
    Race(
        key="dragonborn",
        cn="龙裔",
        en="Dragonborn",
        bonus={"str": 2, "cha": 1},
        speed=30,
        size="中型",
        traits=(
            "龙族血统：选择一种龙类，决定吐息武器与伤害抗性",
            "吐息武器：以动作喷吐能量，范围内敏捷豁免减半伤害",
            "伤害抗性与祖先龙类一致",
        ),
        languages=("通用语", "龙语"),
        desc="骄傲的龙之血脉，荣誉高于一切。",
    ),
    Race(
        key="gnome",
        cn="侏儒",
        en="Gnome",
        bonus={"int": 2},
        speed=25,
        size="小型",
        traits=("黑暗视觉 60 尺", "侏儒狡黠：对魔法进行的智力 / 感知 / 魅力豁免有优势"),
        languages=("通用语", "侏儒语"),
        subraces=(
            SubRace(
                "forest_gnome",
                "森林侏儒",
                bonus={"dex": 1},
                traits=(
                    "自然幻术：任选一个幻术戏法",
                    "与小型动物交谈：可传达简单概念",
                ),
                desc="与林中生灵为伴。",
            ),
            SubRace(
                "rock_gnome",
                "岩石侏儒",
                bonus={"con": 1},
                traits=(
                    "工匠知识：任选一项工匠工具熟练",
                    "发条装置：可制作小型机械装置",
                ),
                desc="天生的发明家与工匠。",
            ),
        ),
        desc="矮小却好奇心旺盛，热衷于发明与恶作剧。",
    ),
    Race(
        key="half_elf",
        cn="半精灵",
        en="Half-Elf",
        bonus={"cha": 2},
        speed=30,
        size="中型",
        traits=(
            "黑暗视觉 60 尺",
            "精类血统：免疫魔法睡眠，对抗魅惑有优势",
            "自选两项技能熟练",
            "额外习得一门自选语言",
        ),
        languages=("通用语", "精灵语"),
        desc="介于两个族群之间，善于交际又懂得自处。",
    ),
    Race(
        key="half_orc",
        cn="半兽人",
        en="Half-Orc",
        bonus={"str": 2, "con": 1},
        speed=30,
        size="中型",
        traits=(
            "黑暗视觉 60 尺",
            "威吓熟练",
            "不屈韧性：生命值降到 0 时可改为保留 1 点（每次长休一次）",
            "狂暴攻击：近战暴击时额外掷一个伤害骰",
        ),
        languages=("通用语", "兽人语"),
        desc="被两个世界排斥，用蛮力与意志证明自己。",
    ),
    Race(
        key="tiefling",
        cn="提夫林",
        en="Tiefling",
        bonus={"cha": 2, "int": 1},
        speed=30,
        size="中型",
        traits=(
            "黑暗视觉 60 尺",
            "火焰抗性",
            "炼狱血脉：习得奇术（Thaumaturgy）戏法；等级提升后可习得地狱斥责、黑暗术",
        ),
        languages=("通用语", "炼狱语"),
        desc="背负着血脉中的契约与偏见，命运由自己书写。",
    ),
)

RACE_MAP: dict[str, Race] = {r.key: r for r in RACES}


def get_race(value: Race | str) -> Race:
    """按种族对象 / 内部键 / 中文名取出种族。"""
    if isinstance(value, Race):
        return value
    text = str(value).strip()
    if text in RACE_MAP:
        return RACE_MAP[text]
    for race in RACES:
        if race.cn == text or race.en.lower() == text.lower():
            return race
    raise KeyError(f"未知种族：{value}")


def race_choices() -> tuple[str, ...]:
    """全部种族中文名，用于生成给玩家的选项列表。"""
    return tuple(r.cn for r in RACES)


def default_subrace(race: Race) -> SubRace | None:
    """取默认亚种（第一个）；无亚种返回 ``None``。"""
    return race.subraces[0] if race.subraces else None
