"""DND 5e 魔法物品数据表。

对应 ``res/rule.md`` 第二十二节「物品规则」，把规则里只有文字的部分落成可查的数据：

- **稀有度**：普通 / 非常稀有 / 稀有 / 非常稀有 / 传说；
- **调谐**：多数魔法物品要调谐才能用，没调谐照常占着（力量护符除外）；
- **法术位消耗**：消耗型物品（药水、卷轴）用完即失效；
- **附魔**：武器 / 护甲的 +1/+2/+3。

物品本身**不占背包格子数**——背包位是「装备」在管的事（:mod:`.equipment`），
这里只管「这个东西是什么、能做什么」。

数据是**可玩的近似值**，以本表为准；按 key / 中文名取用 :func:`get_item`。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

__all__ = [
    "ITEMS",
    "ITEM_KINDS",
    "ITEM_RARITIES",
    "Item",
    "format_item_lines",
    "format_item_list",
    "get_item",
    "items_of_kind",
]

#: 物品大类
ITEM_KINDS: tuple[str, ...] = (
    "武器",
    "护甲",
    "药水",
    "卷轴",
    "饰品",
    "工具",
    "奇物",
)

#: 稀有度（由低到高）
ITEM_RARITIES: tuple[str, ...] = (
    "普通",
    "非常稀有",
    "稀有",
    "非常稀有",
    "传说",
)


@dataclass(frozen=True, slots=True)
class Item:
    """一件魔法物品。"""

    key: str
    cn: str  #: 中文名
    en: str  #: 英文名
    kind: str  #: 大类（:data:`ITEM_KINDS` 之一）
    rarity: str = "普通"  #: 稀有度（:data:`ITEM_RARITIES` 之一）
    requires_attunement: bool = False  #: 是否需要调谐
    #: 使用消耗：空 = 永久；"药水一瓶" / "卷轴一张" 这类按次扣
    consumes: str = ""
    #: 附魔值：武器 / 护甲的 +1 / +2 / +3
    bonus: int = 0
    #: 是否为「消耗型」（药水、卷轴）——用完即失效，不能重复用
    consumable: bool = False
    #: 药水效果：治疗量 / 抗性等（药水类才有）
    effect: str = ""
    #: 效果持续时间（药水 / 饰品）
    duration: str = ""
    #: 使用条件（需要某法术位、需要某能力、每天几次）
    requirement: str = ""
    desc: str = ""
    #: 额外别名（除 key / 中英文名外还能被认出来的写法）
    aliases: tuple[str, ...] = ()

    # —— 派生 ——

    @property
    def label(self) -> str:
        """列表里显示的一行。

        附魔值只出现一次：中文名里已经带「+1」时（如「+1 武器」）不再重复。
        """
        name = self.cn
        if self.bonus and not name.startswith("+"):
            name = f"+{self.bonus} {name}"
        return f"{name}（{self.kind}，{self.rarity}）"

    @property
    def usable_text(self) -> str:
        """使用条件一句话。"""
        bits: list[str] = []
        if self.requires_attunement:
            bits.append("需调谐")
        if self.requirement:
            bits.append(self.requirement)
        if self.consumes:
            bits.append(f"消耗 {self.consumes}")
        return "；".join(bits)

    def describe(self) -> str:
        """完整描述（DM 查询用）。"""
        lines = [f"{self.cn}（{self.en}）  {self.kind}，{self.rarity}"]
        if self.bonus:
            lines.append(f"附魔：+{self.bonus}")
        if self.effect:
            lines.append(f"效果：{self.effect}")
        if self.duration:
            lines.append(f"持续：{self.duration}")
        usable = self.usable_text
        if usable:
            lines.append(f"使用：{usable}")
        if self.desc:
            lines.append(self.desc)
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 数据表
# --------------------------------------------------------------------------- #

ITEMS: tuple[Item, ...] = (
    # —— 风骸岛之龙：附录 A 的六件 ——
    Item(
        key="elven_boots",
        cn="精灵之靴",
        en="Elven Boots",
        kind="饰品",
        rarity="稀有",
        requires_attunement=True,
        effect="轻步：无视地形带来的移动困难，且在有光的地方不惊动警觉的生物",
        desc="穿上后脚步几乎不发出声音，靴底会自己找最平的那块石头。",
        aliases=("精灵靴", "轻步靴"),
    ),
    Item(
        key="potion_of_health",
        cn="健康灵药",
        en="Potion of Health",
        kind="药水",
        rarity="非常稀有",
        consumable=True,
        consumes="药水一瓶",
        effect="回复 2d4+4 点生命值，永久提升最大生命值 4 点（上限 +40）",
        duration="永久",
        desc="塔拉克用风骸岛上的红玉肚菌配的方子，喝下去时是温的、带铁锈味。",
        aliases=("红玉肚菌灵药", "健康药剂"),
    ),
    Item(
        key="potion_of_healing",
        cn="治疗药水",
        en="Potion of Healing",
        kind="药水",
        rarity="非常稀有",
        consumable=True,
        consumes="药水一瓶",
        effect="回复 2d4+2 点生命值",
        desc="塔拉克的标准货：蕈人的心顶蘑菇加别的什么，味道像温过的海水。",
        aliases=("治疗药剂", "疗伤药水"),
    ),
    Item(
        key="potion_of_resistance",
        cn="抗性药水",
        en="Potion of Resistance",
        kind="药水",
        rarity="非常稀有",
        consumable=True,
        consumes="药水一瓶",
        effect="对一种伤害类型获得抗性，持续 1 小时（服用时选择类型）",
        duration="1 小时",
        desc="喝下去之后舌头发麻，某一类伤害打在身上像打在湿皮革上。",
        aliases=("抗性药剂",),
    ),
    Item(
        key="scroll_of_spells",
        cn="法术卷轴",
        en="Scroll of Spells",
        kind="卷轴",
        rarity="稀有",
        consumable=True,
        consumes="卷轴一张",
        effect="内含一个 1~3 环法术，可由任何能施放该环位法术的角色使用",
        requirement="施法者能施放对应环位的法术",
        desc="羊皮纸卷起来塞在书里，边缘被火燎过，防止有人随手翻开念。",
        aliases=("卷轴",),
    ),
    Item(
        key="plus_one_weapon",
        cn="+1 武器",
        en="+1 Weapon",
        kind="武器",
        rarity="稀有",
        bonus=1,
        effect="攻击与伤害 +1",
        desc="武器本身没什么特别，握着它的时候手会自己知道该怎么挥。",
        aliases=("加一武器", "魔法武器"),
    ),
    # —— 通用常备 ——
    Item(
        key="cloak_of_protection",
        cn="防护斗篷",
        en="Cloak of Protection",
        kind="饰品",
        rarity="非常稀有",
        requires_attunement=True,
        effect="魅力与豁免加值 +1",
        desc="灰扑扑的粗布，斗篷内衬缝着一层看不出材质的皮。",
    ),
    Item(
        key="ring_of_protection",
        cn="防护戒指",
        en="Ring of Protection",
        kind="饰品",
        rarity="稀有",
        requires_attunement=True,
        effect="AC 与豁免加值 +1",
        desc="黄铜环，内侧刻着磨损的铭文，戴久了手指会发凉。",
    ),
    Item(
        key="bag_of_holding",
        cn="收纳袋",
        en="Bag of Holding",
        kind="奇物",
        rarity="非常稀有",
        effect="额外容纳 500 磅体积的物品，但活物塞进去会被困住",
        desc="布面，鼓鼓的，像装着一袋湿沙。",
    ),
    Item(
        key="cloak_of_invisibility",
        cn="隐形斗篷",
        en="Cloak of Invisibility",
        kind="饰品",
        rarity="非常稀有",
        requires_attunement=True,
        effect="隐形；攻击或施法时隐形结束",
        desc="灰白的一片，边缘磨得起了毛。披上时布料凉得贴皮肤。",
    ),
    Item(
        key="boots_of_striding",
        cn="疾行之靴",
        en="Boots of Striding and Springing",
        kind="饰品",
        rarity="非常稀有",
        requires_attunement=True,
        effect="移动速度翻倍；跳跃距离乘三",
        desc="看着就是双旧靴子，但穿上之后你会想跑。",
    ),
    Item(
        key="weapon_plus_two",
        cn="+2 武器",
        en="+2 Weapon",
        kind="武器",
        rarity="非常稀有",
        bonus=2,
        effect="攻击与伤害 +2",
        desc="这类东西在铁匠那儿没有第二把。",
        aliases=("加二武器",),
    ),
    Item(
        key="weapon_plus_three",
        cn="+3 武器",
        en="+3 Weapon",
        kind="武器",
        rarity="传说",
        bonus=3,
        effect="攻击与伤害 +3",
        desc="传说级的兵器多半有脾气。用它的人多半也有。",
        aliases=("加三武器",),
    ),
    Item(
        key="armor_plus_one",
        cn="+1 护甲",
        en="+1 Armor",
        kind="护甲",
        rarity="稀有",
        bonus=1,
        effect="AC +1",
        desc="甲片内衬某种会呼吸的东西，夜里贴身发暖。",
    ),
    Item(
        key="armor_plus_two",
        cn="+2 护甲",
        en="+2 Armor",
        kind="护甲",
        rarity="非常稀有",
        bonus=2,
        effect="AC +2",
        desc="凑齐一套这样的甲要杀很多人——这是它的一部分来历。",
    ),
    Item(
        key="lamp_of_undying",
        cn="长明灯",
        en="Lamp of Undying",
        kind="奇物",
        rarity="非常稀有",
        effect="火焰不灭，且照亮周围 60 尺；可驱散一处不死性",
        desc="铜灯，油是绿的，烧起来没有烟。",
    ),
    Item(
        key="stone_of_guarding",
        cn="守望石",
        en="Stone of Guarding",
        kind="奇物",
        rarity="非常稀有",
        requires_attunement=True,
        effect="指定一个生物：其死亡时生命值恢复至 1 点，一天一次",
        desc="一颗温热的卵石，握久了会听见很远的海浪。",
    ),
    Item(
        key="helm_of_terror",
        cn="恐吓盔",
        en="Helm of Terror",
        kind="饰品",
        rarity="非常稀有",
        requires_attunement=True,
        effect="豁免减 2；附近敌人对你的恐惧检定劣势",
        desc="戴上后你自己的脸也会变得可怕。",
    ),
    Item(
        key="vial_of_smelling_salts",
        cn="嗅盐",
        en="Vial of Smelling Salts",
        kind="工具",
        rarity="普通",
        consumable=True,
        consumes="一剂",
        effect="让一个昏迷的生物立刻苏醒（对中毒或昏迷有效）",
        desc="玻璃小瓶，摇一摇那味道能把人从梦里掀出来。",
    ),
    Item(
        key="torch",
        cn="火炬",
        en="Torch",
        kind="工具",
        rarity="普通",
        consumable=True,
        consumes="一支",
        effect="照明 20 尺；点燃物体",
        desc="浸过油的木棍，火焰稳定，不容易被风吹灭。",
    ),
    # —— 《凡戴尔的失落矿坑》附录 A 的八件（另五件与上表重复）——
    Item(
        key="boots_of_striding_and_springing",
        cn="跑跳之靴",
        en="Boots of Striding and Springing",
        kind="饰品",
        rarity="非常稀有",
        requires_attunement=True,
        effect="移动速度翻倍；跳跃距离乘三",
        desc="一双旧皮靴。穿上它的人总想去跑。",
        aliases=("疾走之靴", "疾行靴"),
    ),
    Item(
        key="gauntlets_of_ogresmight",
        cn="食人魔力量护手",
        en="Gauntlets of Ogre's Might",
        kind="饰品",
        rarity="非常稀有",
        requires_attunement=True,
        effect="力量变为 19（若原本更高则不变）",
        desc=("一双铁护手，内侧刻着食人魔的纹样。戴上的瞬间，手臂的肌肉会自己撑起来。"),
        aliases=("食人魔护手",),
    ),
    Item(
        key="potion_of_flying",
        cn="飞行药水",
        en="Potion of Flying",
        kind="药水",
        rarity="非常稀有",
        consumable=True,
        consumes="药水一瓶",
        effect="获得 60 尺飞行速度（每秒 60 尺）",
        duration="1 小时",
        desc="喝下去之后胃里一阵翻涌，然后脚就离地了。",
        aliases=("飞行药剂",),
    ),
    Item(
        key="potion_of_invisibility",
        cn="隐形药水",
        en="Potion of Invisibility",
        kind="药水",
        rarity="非常稀有",
        consumable=True,
        consumes="药水一瓶",
        effect="隐形；攻击或施法时隐形结束",
        duration="1 小时",
        desc=("透明的、闻起来像烧过的羽毛。伊阿诺在他的密室里配了很多年，一直没成功。"),
        aliases=("隐形药剂",),
    ),
    Item(
        key="potion_of_vitality",
        cn="活力药水",
        en="Potion of Vitality",
        kind="药水",
        rarity="非常稀有",
        consumable=True,
        consumes="药水一瓶",
        effect="体质变为 20（若原本更高则不变），并回复该数值的生命值",
        duration="永久",
        desc="瓶底沉着一点红东西，摇一摇就化了。喝下去像被火烤了一遍。",
        aliases=("活力药剂",),
    ),
    Item(
        key="spider_staff",
        cn="蜘蛛法杖",
        en="Spider Staff",
        kind="奇物",
        rarity="非常稀有",
        requires_attunement=True,
        effect="可施放蛛网术；持杖者的蜘蛛类生物不攻击自己",
        desc=("一根黑木杖，顶端是一颗琥珀色的小球，里面封着一只会织网的东西的影子。"),
    ),
    Item(
        key="staff_of_protection",
        cn="防护法杖",
        en="Staff of Protection",
        kind="奇物",
        rarity="非常稀有",
        requires_attunement=True,
        effect="可施放法师护甲；持杖者对远程攻击有优势",
        desc=("木杖缠着皮绳，握把处有一层磨得发亮的包浆。伊阿诺逃跑时顺手抓走了它。"),
        aliases=("护盾法杖",),
    ),
    Item(
        key="wand_of_magic_missile",
        cn="魔法飞弹魔杖",
        en="Wand of Magic Missile",
        kind="奇物",
        rarity="非常稀有",
        requires_attunement=True,
        effect="可施放魔法飞弹（1~3 环）",
        desc="细木棍，顶端嵌着一颗不起眼的灰石。握久了手心会发热。",
        aliases=("飞弹魔杖",),
    ),
)


#: 中文名 / 英文名 / key → 物品
ITEM_MAP: dict[str, Item] = {}


def _register(item: Item) -> None:
    for name in (item.key, item.cn, item.en, *item.aliases):
        ITEM_MAP.setdefault(name, item)


for _item in ITEMS:
    _register(_item)


def get_item(value: Item | str) -> Item | None:
    """按 key / 中文名 / 英文名 / 别名取物品；取不到返回 ``None``。"""
    if isinstance(value, Item):
        return value
    return ITEM_MAP.get(value.strip())


def items_of_kind(kind: str) -> list[Item]:
    """按大类取物品（``"药水"`` / ``"武器"`` …）。"""
    return [item for item in ITEMS if item.kind == kind]


def format_item_lines(items: Iterable[Item], *, numbered: bool = True) -> str:
    """把物品列表排成一行一个的文本。"""
    out: list[str] = []
    for index, item in enumerate(items, start=1):
        prefix = f"{index}. " if numbered else "· "
        out.append(prefix + item.label)
    return "\n".join(out)


def format_item_list(items: Iterable[Item]) -> str:
    """按大类分组列出物品。"""
    buckets: dict[str, list[Item]] = {}
    for item in items:
        buckets.setdefault(item.kind, []).append(item)
    out: list[str] = []
    for kind in ITEM_KINDS:
        group = buckets.get(kind)
        if not group:
            continue
        out.append(f"【{kind}】")
        out.append(format_item_lines(group))
    return "\n".join(out)
