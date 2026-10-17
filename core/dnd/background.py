"""DND 5e 背景数据。

背景在建卡时提供：两项技能熟练、若干工具 / 语言熟练、以及一段起始装备，
是「技能选择」（``skill_choice.md``）之外的第二条熟练来源。
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "BACKGROUNDS",
    "BACKGROUND_MAP",
    "Background",
    "background_choices",
    "get_background",
]


@dataclass(frozen=True, slots=True)
class Background:
    """一项背景。"""

    key: str
    cn: str
    en: str
    skills: tuple[str, ...]  # 提供的技能内部键
    languages: int = 0  # 可自选的语言数量
    tools: tuple[str, ...] = ()  # 工具熟练
    equipment: tuple[str, ...] = ()  # 起始装备
    feature: str = ""  # 背景特性
    desc: str = ""

    @property
    def skill_count(self) -> int:
        return len(self.skills)


BACKGROUNDS: tuple[Background, ...] = (
    Background(
        key="acolyte",
        cn="侍僧",
        en="Acolyte",
        skills=("insight", "religion"),
        languages=2,
        equipment=("圣徽", "祈祷书", "5 支香", "祭祀法衣", "15 金币"),
        feature="信仰庇护：同信仰的神殿会为你提供庇护与协助。",
        desc="在神殿中长大，熟稔教义与仪式。",
    ),
    Background(
        key="charlatan",
        cn="骗子",
        en="Charlatan",
        skills=("deception", "sleight_of_hand"),
        tools=("伪装工具", "伪造工具"),
        equipment=("精致服装", "伪装工具", "15 金币"),
        feature="假身份：你经营着一个经得起查验的虚假身份。",
        desc="擅长编造身份，靠一张嘴行走江湖。",
    ),
    Background(
        key="criminal",
        cn="罪犯",
        en="Criminal",
        skills=("deception", "stealth"),
        tools=("一种赌博工具", "盗贼工具"),
        equipment=("撬棍", "深色普通服装", "15 金币"),
        feature="地下人脉：你能联系到犯罪网络中的熟人。",
        desc="与地下世界打过交道，知道规矩也懂得门路。",
    ),
    Background(
        key="folk_hero",
        cn="民间英雄",
        en="Folk Hero",
        skills=("animal_handling", "survival"),
        tools=("一种工匠工具", "载具（陆运）"),
        equipment=("工匠工具", "铁锅", "普通服装", "10 金币"),
        feature="平民认同：普通百姓愿意为你提供藏身之处与帮助。",
        desc="出身平凡，却因一事被乡里视为英雄。",
    ),
    Background(
        key="noble",
        cn="贵族",
        en="Noble",
        skills=("history", "persuasion"),
        tools=("一种赌博工具",),
        languages=1,
        equipment=("精致服装", "印章戒指", "族谱卷轴", "25 金币"),
        feature="特权地位：人们会默认你有权有势，愿意接见你。",
        desc="含着金汤匙出生，懂得权术与礼仪。",
    ),
    Background(
        key="sage",
        cn="智者",
        en="Sage",
        skills=("arcana", "history"),
        languages=2,
        equipment=("墨水笔", "小刀", "死去的师长的信件", "普通服装", "10 金币"),
        feature="研究者：遇到无从得知的知识时，你常能回忆起相关线索。",
        desc="把一生投入书卷，知识就是你的武器。",
    ),
    Background(
        key="soldier",
        cn="士兵",
        en="Soldier",
        skills=("athletics", "intimidation"),
        tools=("一种赌博工具", "载具（陆运）"),
        equipment=("军阶徽记", "战利品", "骰子", "普通服装", "10 金币"),
        feature="军阶：你在军队中的身份仍具影响力。",
        desc="在军中度过青春，懂得纪律与服从。",
    ),
    Background(
        key="outlander",
        cn="化外之民",
        en="Outlander",
        skills=("athletics", "survival"),
        tools=("一种乐器",),
        languages=1,
        equipment=("旅人服装", "狩猎陷阱", "动物战利品", "10 金币"),
        feature="漂泊者：你总能找到食物与水源，也能记住走过的路。",
        desc="在荒野中长大，城市对你而言才是异乡。",
    ),
    Background(
        key="entertainer",
        cn="艺人",
        en="Entertainer",
        skills=("acrobatics", "performance"),
        tools=("伪装工具", "一种乐器"),
        equipment=("乐器", "倾慕者的信物", "戏服", "15 金币"),
        feature="万众瞩目：在有人群的地方你总能找到演出机会。",
        desc="舞台是你的战场，掌声是你的报酬。",
    ),
    Background(
        key="guild_artisan",
        cn="行会工匠",
        en="Guild Artisan",
        skills=("insight", "persuasion"),
        tools=("一种工匠工具",),
        languages=1,
        equipment=("工匠工具", "行会介绍信", "旅人服装", "15 金币"),
        feature="行会成员：行会会为你提供食宿、庇护与政治支持。",
        desc="手艺精湛，受行会庇护也受行会约束。",
    ),
    Background(
        key="hermit",
        cn="隐士",
        en="Hermit",
        skills=("medicine", "religion"),
        tools=("草药工具",),
        languages=1,
        equipment=("卷轴匣", "冬被", "草药工具", "普通服装", "5 金币"),
        feature="发现：独居期间你获得了一个重要而独特的发现。",
        desc="离群索居多年，对世界有独到的理解。",
    ),
)

BACKGROUND_MAP: dict[str, Background] = {b.key: b for b in BACKGROUNDS}


def get_background(value: Background | str) -> Background:
    """按背景对象 / 内部键 / 中文名取出背景。"""
    if isinstance(value, Background):
        return value
    text = str(value).strip()
    if text in BACKGROUND_MAP:
        return BACKGROUND_MAP[text]
    for bg in BACKGROUNDS:
        if bg.cn == text or bg.en.lower() == text.lower():
            return bg
    raise KeyError(f"未知背景：{value}")


def background_choices() -> tuple[str, ...]:
    """全部背景中文名。"""
    return tuple(b.cn for b in BACKGROUNDS)
