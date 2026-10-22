"""空窗期的小事件：底色与高光的**素材**，纯程序。

## 为什么是素材而不是段子

空窗期判定是**每一跳**都做的（见 :mod:`.events` 的 ``judge_hop``），所以它必须零模型调用。
但「零模型」不等于「只写死几十条句子」——那样第三跳就开始重复了。这里做的是两件事：

- **底色**：模板 + 槽位。``{place}`` ``{faction}`` 由当前场景与所在势力填进去，
  同一条模板在不同地方读起来是两回事。
- **高光**：**素材组合**。怪物按队伍等级从 :data:`~.monsters.MONSTERS` 里抽、
  地点按场景类别抽、由头填槽位。组合出来的「一窝狗头人占了灰肺坑」不是谁写的，
  是算出来的——但它是具体的。

## 两类小事件的分工

| | 底色 | 高光 |
|---|---|---|
| 作用 | 让世界不空 + 把流言递到耳边 | **给取舍**：拿损耗换战利品 |
| 后果 | 只消耗时间 | 战后损耗 + 战利品（走 ``settle_combat``） |
| 频率 | 约 3 | 约 1 |

**底色不许产生实质后果**：一旦它能给东西，空窗期就变成可刷的副本了。
**高光必须有实质后果**：这一点被纠正过一次——被打劫的商队、被占的山洞，
本来就是空窗期里最实在的东西。
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .monsters import MONSTERS

__all__ = [
    "KIND_LABELS",
    "SCENE_KINDS",
    "Skirmish",
    "Vignette",
    "pack_size",
    "scene_kind",
    "skirmish_material",
    "vignette",
]

#: 场景类别 -> 中文名。类别只用来挑模板，**不是世界数据**——认不出就落 ``any``。
KIND_LABELS: dict[str, str] = {
    "port": "码头",
    "street": "街市",
    "wild": "野外",
    "dungeon": "地下",
    "shrine": "神殿",
    "any": "任何地方",
}

#: 场景名 -> 类别的关键词表。**顺序即优先级**：先匹配到的算。
#: 刻意粗糙——它只决定「去哪一堆模板里抽」，抽错了也不致命。
SCENE_KINDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("shrine", ("圣殿", "神殿", "祠", "庙", "祭", "地窖")),
    ("port", ("码头", "渡口", "港", "栈", "船", "仓", "滩", "泊")),
    ("dungeon", ("地下", "窟", "洞", "墓", "井", "塔", "矿", "遗迹", "坑道", "地宫")),
    (
        "wild",
        ("原", "岭", "林", "野", "沟", "坑", "坡", "谷", "沼", "荒", "崖", "泉", "雪"),
    ),
    ("street", ("街", "市", "镇", "城", "巷", "店", "酒馆", "客栈", "铺", "坊")),
)


def scene_kind(scene: str) -> str:
    """猜一个场景属于哪一类。认不出返回 ``any``。

    **故意不精确**：同一座城里既有码头也有街市，模板抽错了不过是「码头式的景物出现在街上」，
    读者不会受伤；反过来做一张精确分类表，维护成本远高于收益。
    """
    name = str(scene or "")
    for kind, words in SCENE_KINDS:
        if any(word in name for word in words):
            return kind
    return "any"


# --------------------------------------------------------------------------- #
# 底色
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class Vignette:
    """一条填好槽位的底色小插曲。

    它**只消耗时间**（外加可能捎带一条流言），不扣血、不给东西、不产生任务。
    """

    id: str
    text: str
    #: 时间消耗（分钟）
    minutes: int = 20
    #: 是否捎带一条流言
    carries: bool = False
    #: 抽中它的场景类别
    kind: str = "any"


@dataclass(frozen=True, slots=True)
class _Template:
    id: str
    #: 适用场景类别；空 = 通用
    where: tuple[str, ...]
    text: str
    minutes: int = 20
    carries: bool = False


#: 底色模板。``{place}`` 填当前场景名，``{faction}`` 填当地势力（没有就用「他们」）。
_TEMPLATES: tuple[_Template, ...] = (
    _Template(
        "shoulder",
        (),
        "一个挑担的从你身边挤过去，担子撞了你的肩，他头也不回地骂了一句，"
        "走出几步又嘟囔：「{place}那帮人夜里不睡觉，早晚出事。」",
        15,
        True,
    ),
    _Template(
        "whisper",
        ("port", "street"),
        "两个人在墙角低声争一个价钱，看见你走近，话头立刻拐到今年的雨水上。",
        15,
        False,
    ),
    _Template(
        "dog",
        (),
        "一只瘦狗叼着东西跑过，掉下一块木牌——上面打了号，还有烧过的痕。",
        10,
        True,
    ),
    _Template(
        "painter",
        ("street", "port"),
        "有人正在墙上刷掉一行字。刷子是新的，字刷到一半，露出底下更旧的一行。",
        20,
        False,
    ),
    _Template(
        "waterboy",
        ("street", "wild"),
        "一个卖水的孩子问你要不要带路，说完又补一句：「{place}那边我是不去的。」",
        15,
        True,
    ),
    _Template(
        "drunk",
        ("street",),
        "一个醉汉拉住你的袖子说「别往那边走」，说完自己晃晃悠悠往那边去了。",
        20,
        False,
    ),
    _Template(
        "patrol",
        ("street", "port"),
        "两个巡逻的走过去。走过去之后，其中一个回头看了你一眼，又转回去。",
        10,
        False,
    ),
    _Template(
        "notice",
        (),
        "一阵风把一张告示吹到你脚边：上头在找一个走失的人，落款是三年前。",
        15,
        False,
    ),
    _Template(
        "counter",
        ("street", "port"),
        "有人蹲在路边数铜子，数到一半忽然收进怀里，站起来就走。",
        10,
        False,
    ),
    _Template(
        "elder",
        (),
        "一位老人跟你讨口水喝，喝完说：「{faction}的人前天也在这站过。」",
        20,
        True,
    ),
    _Template(
        "bowls",
        ("street", "shrine"),
        "街角摆着三只碗，两只是空的。第三只里的水还没干。",
        10,
        False,
    ),
    _Template(
        "child",
        ("street", "wild"),
        "一个孩子往屋檐上扔石头，被大人一把拽走。没人解释为什么。",
        10,
        False,
    ),
    _Template(
        "whitecloth",
        ("street", "shrine"),
        "一扇门框上挂着新白布，屋里一点声音也没有。路过的人都加快了脚步。",
        15,
        False,
    ),
    _Template(
        "behind",
        ("street", "port", "wild"),
        "有人在你身后停下来。你回头，那人已经走远了，只留下一串脚印。",
        10,
        True,
    ),
    _Template(
        "porter",
        ("port", "wild"),
        "一个挑担的换肩时掉下一件东西，他看了一眼，没捡，继续走。",
        15,
        False,
    ),
    _Template(
        "market",
        ("street", "port"),
        "集市上有人在低价抛一批货。你问来路，他就笑，笑完说：「问这个做什么。」",
        20,
        True,
    ),
    _Template(
        "wind",
        ("wild", "dungeon"),
        "风从背后来，带着一股不该在这时候出现的味道——像是很久以前烧过的东西。",
        15,
        False,
    ),
    _Template(
        "rope",
        ("dungeon", "wild"),
        "一段旧绳子从上面垂下来，末端是新断的，断口还没起毛。",
        15,
        True,
    ),
)


def vignette(
    scene: str,
    *,
    place: str = "",
    faction: str = "",
    rng: random.Random | None = None,
) -> Vignette:
    """抽一条底色小插曲，把槽位填好。

    先按场景类别挑，挑不到再退回通用模板；**两者都空时返回一条最普通的**——
    空窗期宁可给一句平淡的，也不要什么都不给（那就成了「程序没反应」）。
    """
    generator = rng if rng is not None else random
    kind = scene_kind(scene)
    pool = [t for t in _TEMPLATES if kind in t.where] or [
        t for t in _TEMPLATES if not t.where
    ]
    template = generator.choice(pool)
    text = template.text.format(
        place=place or scene or "那边",
        faction=faction or "他们",
    )
    return Vignette(
        id=template.id,
        text=text,
        minutes=template.minutes,
        carries=template.carries,
        kind=kind,
    )


# --------------------------------------------------------------------------- #
# 高光
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class Skirmish:
    """一场「空窗期里的机会」的素材。

    **它不是战斗本身**：程序只给出「谁、几个、在哪、凭什么由头」，
    真正开战仍走 DM 的 ``start_combat``——所以这里没有先攻、没有 HP。
    """

    #: 怪物 key（可直接传给 ``start_combat``）
    monster: str
    #: 中文名，给叙述用
    name: str
    #: 只数
    count: int
    #: 挑战等级
    cr: float
    #: 一句话由头（已填槽位）
    hook: str
    #: 地点（场景名）
    site: str
    #: 打完之后大概过了多久（分钟）
    minutes: int = 60
    #: 该事件给 DM 的一句话提示
    hint: str = ""


#: 高光由头模板。``{monster}`` 怪物中文名，``{site}`` 地点。
_HOOKS: tuple[str, ...] = (
    "一伙{monster}占了{site}，把这条路掐断了。",
    "一支商队在{site}附近被{monster}劫了，货还扔在原地，人不知去向。",
    "{site}里传出声响。凑近看，是一窝{monster}。",
    "{monster}在{site}边上游荡了好几天，已经没人敢走夜路。",
    "有人出钱请人清掉{site}里的{monster}。出价不高，也没人接。",
    "{site}的门被从里面顶住了，里面是{monster}的动静。",
)


def pack_size(cr: float, level: int) -> int:
    """几只。CR 越低越多，但封顶 6——再多就是一片杂兵，打起来只剩掷骰。

    这不是精确的遭遇预算（那是 :mod:`~.encounter` 的事）：
    高光小事件本来就该「打得过但不轻松」，精确校准反而会让它变成刷经验。
    """
    gap = float(level) - float(cr)
    if gap < 1.0:
        return 1
    if gap < 2.5:
        return 2
    if gap < 4.0:
        return 3
    if gap < 6.0:
        return 4
    return 6


def skirmish_material(
    level: int,
    site: str,
    *,
    rng: random.Random | None = None,
    max_cr: float = 0.0,
) -> Skirmish:
    """组合一场高光小事件的素材。

    :param level: 队伍平均等级——决定抽多强的怪。**不是「推荐等级」**，
        玩家任意等级都可能撞上：强的那一批只是更难打，不是不许来。
    :param site: 发生在哪个场景（只用于措辞）
    :param max_cr: 上限；留空按 ``level + 1`` 算（压着「能打但要付代价」这条线）
    """
    generator = rng if rng is not None else random
    lvl = max(1, int(level))
    ceiling = float(max_cr) if max_cr > 0 else lvl + 1.0

    # 抽怪：优先 CR 在「等级的一半到等级＋1」之间的——太弱没有代价，太强没法打。
    # 找不到就放宽到全表最接近的，总比返回空好。
    band = [m for m in MONSTERS if 0 < m.cr <= ceiling and m.cr >= max(0.125, lvl / 2)]
    if not band:
        band = [m for m in MONSTERS if 0 < m.cr <= ceiling] or list(MONSTERS)
    monster = generator.choice(band)
    count = pack_size(monster.cr, lvl)
    hook = generator.choice(_HOOKS).format(monster=monster.cn, site=site or "那边")

    return Skirmish(
        monster=monster.key,
        name=monster.cn,
        count=count,
        cr=monster.cr,
        hook=hook,
        site=site or "",
        hint=(
            f"这是空窗期里的一场机会：{count} 只{monster.cn}（CR {monster.cr:g}）。"
            "玩家可以介入，也可以绕开——**两条都不算错**。"
            "介入就用 start_combat 开战，战后照常 settle_combat 结算"
            "（经验与战利品程序会算）。"
        ),
    )
