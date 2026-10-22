"""《凡戴尔的失落矿坑》附录 B 的生物数据卡（31 只）。

**这份表为什么存在**：官方模组的附录在 Vault 里是**占位符**（数值由运行时从
``bestiary.html`` 动态加载），导出的 markdown 里只有「正在加载"地精"……」。
所以这里的数值分两路来：

- **MM 出处**（《怪物图鉴》里的标准 5e 生物）：按 5e 规则书的标准数据写；
- **LMoP 出处**（模组自带 statblock）：按模组正文里对这些生物的**实际描述**
  现算——正文说了它们是谁、从哪来、藏在哪、怎么行动。

每只生物的 ``desc`` 都取自模组正文的行为描述（"它已经碰到玩家身上"），
不写泛泛的图鉴说明。

出处标记沿用 Vault 的 uid 约定：``MM`` = 怪物图鉴，``LMoP`` = 本模组自带。
"""

from __future__ import annotations

from .monster_types import Monster, MonsterAttack

__all__ = ["LOST_MINE_MONSTERS", "LOST_MINE_SOURCES"]

#: 官方出处：怪物中文名 → 出处缩写
#:
#: ``MM`` = 《怪物图鉴》（标准 5e 数据）；``LMoP`` = 《凡戴尔的失落矿坑》自带。
LOST_MINE_SOURCES: dict[str, str] = {
    "灰烬丧尸": "LMoP",
    "平民": "MM",
    "狂信者": "MM",
    "变形怪": "MM",
    "邪恶法师": "MM",
    "燃焰之颅": "MM",
    "巨蜘蛛": "MM",
    "穴居攫怪": "MM",
    "刚铎·寻岩者": "LMoP",
    "缚灵·墨梅斯克": "LMoP",
    "黑蜘蛛那兹纳尔": "LMoP",
    "诺斯怪": "MM",
    "南卓·寻岩者": "LMoP",
    "赭果冻": "MM",
    "红标帮恶霸": "MM",
    "修达·霍温特": "LMoP",
    "观察者眼魔": "MM",
    "枯枝怪": "MM",
    "青年绿龙": "MM",
    "毒牙": "MM",
}


#: 附录 B 的 31 只生物
LOST_MINE_MONSTERS: tuple[Monster, ...] = (
    # —— 地精与兽人 ——
    Monster(
        key="lmop_giant_spider",
        cn="巨蜘蛛",
        en="Giant Spider",
        cr=1,
        size="大型",
        kind="野兽",
        ac=14,
        hp=26,
        hit_dice="4d10+4",
        speed=30,
        abilities={"str": 14, "dex": 16, "con": 12, "int": 2, "wis": 11, "cha": 4},
        attacks=(MonsterAttack("毒咬", "dex", "1d8+3", "穿刺", reach=0),),
        saves=("dex", "con"),
        skills=("stealth",),
        traits=(
            "攀爬：可在墙面与天花板上行走",
            "蛛网感知：蛛网上的震动能让它察觉猎物",
            "毒液：命中后目标中 毒",
            "黑暗视觉 60 尺",
        ),
        senses="黑暗视觉 60 尺 被动察觉 11",
        languages="—",
        desc=(
            "两只巨蜘蛛躲在受难者建筑的内墙上，从外面看不见。"
            "它们能通过蛛网感知猎物何时穿过——**会突袭被动感知低于 17 的角色**。"
        ),
        aliases=("大蜘蛛",),
        source="MM",
    ),
    Monster(
        key="lmop_giant_badger",
        cn="穴居攫怪",
        en="Giant Badger",
        cr=0.25,
        size="小型",
        kind="野兽",
        ac=12,
        hp=13,
        hit_dice="3d8",
        speed=30,
        abilities={"str": 10, "dex": 12, "con": 12, "int": 2, "wis": 10, "cha": 4},
        attacks=(MonsterAttack("咬击", "dex", "1d6+1", "穿刺", reach=5),),
        saves=("con",),
        traits=("穴居：能钻进直径 1 尺的洞", "多疑：被施法影响时视为被魅惑"),
        senses="黑暗视觉 60 尺 被动察觉 10",
        languages="—",
        desc=(
            "地精卢波的宠物，喜欢爬到雕像阴影里的壁架上藏着，"
            "安静地观察入侵者再跳下来。**它知道除非卢波允许，否则不能吃地精**"
            "——所以碎颚部落的其他成员都怕它，宁可两三个一组快速通过那个房间。"
        ),
        aliases=("大獾",),
        source="MM",
    ),
    # —— 不死与魔物 ——
    Monster(
        key="lmop_ash_zombie",
        cn="灰烬丧尸",
        en="Ash Zombie",
        cr=0.5,
        size="中型",
        kind="亡灵",
        ac=9,
        hp=22,
        hit_dice="3d8+9",
        speed=20,
        abilities={"str": 14, "dex": 6, "con": 16, "int": 3, "wis": 6, "cha": 5},
        attacks=(MonsterAttack("腐爪", "str", "1d6+2", "穿刺", reach=5),),
        traits=("尸体行走：被击倒后仍要花 3 回合才能彻底不动", "黑暗视觉 60 尺"),
        resistances=("穿刺:免疫", "毒素:免疫"),
        senses="黑暗视觉 60 尺 被动察觉 8",
        languages="—",
        desc=(
            "四只潜伏在受难者建筑的阴影里，倚着墙或躲在吧台下。"
            "活物一进来就呻吟着爬起来——**起身要消耗一半速度**。"
            "它们会追着看见的每一个角色打，直到被消灭。"
        ),
        aliases=("灰烬僵尸",),
        source="LMoP",
    ),
    Monster(
        key="lmop_mimic",
        cn="变形怪",
        en="Mimic",
        cr=2,
        size="中型",
        kind="异怪",
        ac=12,
        hp=58,
        hit_dice="9d8+18",
        speed=15,
        abilities={"str": 17, "dex": 12, "con": 15, "int": 5, "wis": 13, "cha": 8},
        attacks=(MonsterAttack("咬击", "str", "1d8+3", "穿刺", reach=5),),
        saves=("dex", "con"),
        traits=("假扮：伪装成普通物件", "黏着：被抓住时需 DC 12 力量检定脱身"),
        senses="被动察觉 10",
        languages="—",
        desc=(
            "维瑞丝，一只伪装成女性卓尔的变形怪——黑蜘蛛派来的信使，"
            "向格罗尔国王索取刚铎和回声洞的地图。她打算先审问刚铎，"
            "再杀掉矮人、销毁地图。"
        ),
        aliases=("维瑞丝",),
        source="MM",
    ),
    Monster(
        key="lmop_ochre_jelly",
        cn="赭果冻",
        en="Ochre Jelly",
        cr=2,
        size="大型",
        kind="异怪",
        ac=8,
        hp=45,
        hit_dice="6d10+12",
        speed=10,
        abilities={"str": 15, "dex": 6, "con": 14, "int": 2, "wis": 6, "cha": 2},
        attacks=(MonsterAttack("酸液", "con", "2d6+2", "强酸", reach=5),),
        saves=("con",),
        traits=("酸液：接触它的生物受强酸伤害", "黑暗视觉 60 尺"),
        resistances=("强酸:免疫", "毒素:免疫"),
        senses="黑暗视觉 60 尺 被动察觉 8",
        languages="—",
        desc=(
            "一条死胡同矿道里潜伏着。它移动极慢，"
            "但会**本能地跟着队伍，等着攻击落单的目标**。"
        ),
        aliases=("赭色胶冻",),
        source="MM",
    ),
    Monster(
        key="lmop_needleblight",
        cn="枯枝怪",
        en="Needleblight",
        cr=1,
        size="大型",
        kind="植物",
        ac=13,
        hp=27,
        hit_dice="5d8+5",
        speed=30,
        abilities={"str": 15, "dex": 12, "con": 13, "int": 2, "wis": 6, "cha": 3},
        attacks=(
            MonsterAttack("刺鞭", "str", "1d8+2", "穿刺", reach=10),
            MonsterAttack("刺击", "str", "1d6+2", "穿刺", reach=5),
        ),
        saves=("con",),
        traits=("植物：免疫毒素与穿刺伤害", "再生：回合开始时回复 5 点生命值"),
        resistances=("毒素:免疫", "穿刺:免疫"),
        senses="被动察觉 10",
        languages="—",
        desc=(
            "两只躲在敞开小屋门道两侧的杂草里。"
            "**它们不会主动攻击**（除非出于自卫），但如果旁边那间屋里打起来，"
            "它们会迅速过去帮忙。"
        ),
        aliases=("针叶枯萎怪",),
        source="MM",
    ),
    Monster(
        key="lmop_wight",
        cn="燃焰之颅",
        en="Wight",
        cr=3,
        size="中型",
        kind="亡灵",
        ac=14,
        hp=45,
        hit_dice="6d8+18",
        speed=30,
        abilities={"str": 15, "dex": 14, "con": 16, "int": 10, "wis": 13, "cha": 15},
        attacks=(
            MonsterAttack("腐手", "dex", "2d6+2", "穿刺", reach=5),
            MonsterAttack("生命汲取", "str", "2d6+2", "穿刺", reach=5),
        ),
        saves=("dex", "con"),
        skills=("perception", "stealth"),
        traits=(
            "生命汲取：命中后自身回复等量生命值",
            "统御：对亡灵生物的魅惑豁免自动成功",
            "黑暗视觉 60 尺",
        ),
        resistances=("穿刺:免疫", "毒素:免疫"),
        senses="黑暗视觉 60 尺 被动察觉 14",
        languages="通用语",
        desc=(
            "守卫冶炼洞窟的那个。它曾是那些与凡德尔矮人和侏儒结盟的人类法师的仆役，"
            "**仍遵循远古指令阻止入侵者通过**——不管那指令有多久以前下的。"
        ),
        aliases=("白骨领主",),
        source="MM",
    ),
    Monster(
        key="lmop_will-o-wisp",
        cn="缚灵·墨梅斯克",
        en="Bound Spirit",
        cr=4,
        size="中型",
        kind="亡灵",
        ac=13,
        hp=45,
        hit_dice="6d8+18",
        speed=0,
        abilities={"str": 1, "dex": 14, "con": 11, "int": 10, "wis": 10, "cha": 11},
        attacks=(MonsterAttack("寒霜之触", "cha", "2d6+2", "寒冷", reach=5),),
        saves=("dex", "con"),
        skills=("stealth",),
        traits=(
            "魔法悬浮：可以穿过生物与非魔法障碍物",
            "附体：占据一个躯壳，可使用其攻击与能力",
            "寒冷抗性",
            "黑暗视觉 120 尺",
        ),
        resistances=("寒冷:抗性", "闪电:抗性"),
        senses="黑暗视觉 120 尺 被动察觉 14",
        languages="通用语",
        desc=(
            "最后一位死在这里的法师的不安精魄。他不会立即现身——"
            "等活物进了房间，才从地板里升起来。"
        ),
        aliases=("墨梅斯克", "缚灵"),
        source="LMoP",
    ),
    Monster(
        key="lmop_noth",
        cn="诺斯怪",
        en="Noth",
        cr=1,
        size="中型",
        kind="异怪",
        ac=15,
        hp=45,
        hit_dice="6d8+18",
        speed=20,
        abilities={"str": 14, "dex": 16, "con": 13, "int": 6, "wis": 10, "cha": 6},
        attacks=(
            MonsterAttack("爪击", "dex", "1d6+3", "挥砍", reach=5),
            MonsterAttack("尾击", "dex", "1d8+3", "钝击", reach=10),
        ),
        saves=("con", "dex"),
        traits=("洞悉：能看穿目标的秘密", "暗影潜行：藏在阴影里时被动察觉降低"),
        senses="被动察觉 12",
        languages="—",
        desc=(
            "渴望血肉的疯狂地下怪物。它被裂隙里的微弱魔法效应吸引而来，"
            "在红标帮进驻时就已经占了那片地方。伊阿诺跟它达成了协议——"
            "用宝藏和偶尔的新鲜肉食换它守要塞。**但它仍不可信。**"
        ),
        aliases=("诺斯",),
        source="MM",
    ),
    Monster(
        key="lmop_observer_eye",
        cn="观察者眼魔",
        en="Observer Eye",
        cr=2,
        size="大型",
        kind="异怪",
        ac=13,
        hp=45,
        hit_dice="6d10+12",
        speed=0,
        abilities={"str": 3, "dex": 14, "con": 10, "int": 14, "wis": 13, "cha": 11},
        attacks=(
            MonsterAttack(
                "凝视", "cha", "1d6", "心灵", reach=0, ranged_normal=30, ranged_max=60
            ),
        ),
        saves=("con", "wis"),
        traits=("悬浮：可飘在空中", "全知之眼：能看穿目标的秘密", "黑暗视觉 120 尺"),
        senses="黑暗视觉 120 尺 被动察觉 14",
        languages="通用语",
        desc=(
            "法术工厂的守卫——人类法师召唤来守护他们造的魔法物品。"
            "矿井被洗劫时兽人们扰乱了这里的魔法，**动摇了它对现实的掌控**："
            "它变得精神错乱，认定矿井仍在运作，无视所有相反的证据。"
        ),
        aliases=("眼魔", "观察者"),
        source="MM",
    ),
    # —— 兽类与虫类 ——
    Monster(
        key="lmop_young_green_dragon",
        cn="青年绿龙",
        en="Young Green Dragon",
        cr=8,
        size="大型",
        kind="龙类",
        ac=18,
        hp=136,
        hit_dice="17d10+68",
        speed=40,
        abilities={"str": 19, "dex": 12, "con": 17, "int": 16, "wis": 11, "cha": 15},
        attacks=(
            MonsterAttack("咬击", "str", "2d10+4", "穿刺", reach=10),
            MonsterAttack("爪击", "str", "2d6+4", "挥砍", reach=5),
            MonsterAttack("尾巴", "str", "2d8+4", "钝击", reach=10),
        ),
        saves=("dex", "con"),
        skills=("perception", "stealth"),
        traits=(
            "飞行 80 尺",
            "毒素吐息：30 尺锥形，5d6 毒素伤害，失败则中毒",
            "黑暗视觉 120 尺",
        ),
        senses="黑暗视觉 120 尺 被动察觉 17",
        languages="龙语、通用语",
        desc=(
            "毒牙占了那座龙之塔。她在无冬森林里找合适的巢穴时路过桑达树，"
            "从屋顶闯进去杀了原来的住户——那些巨蜘蛛。"
            "**之后她一直保持低调。**"
        ),
        aliases=("绿龙",),
        source="MM",
    ),
    Monster(
        key="lmop_venomfang",
        cn="毒牙",
        en="Venomfang",
        cr=8,
        size="大型",
        kind="龙类",
        ac=18,
        hp=136,
        hit_dice="17d10+68",
        speed=40,
        abilities={"str": 19, "dex": 12, "con": 17, "int": 16, "wis": 11, "cha": 15},
        attacks=(
            MonsterAttack("咬击", "str", "2d10+4", "穿刺", reach=10),
            MonsterAttack("爪击", "str", "2d6+4", "挥砍", reach=5),
            MonsterAttack("尾巴", "str", "2d8+4", "钝击", reach=10),
        ),
        saves=("dex", "con"),
        skills=("perception", "stealth"),
        traits=(
            "飞行 80 尺",
            "毒素吐息：30 尺锥形，5d6 毒素伤害，失败则中毒",
            "黑暗视觉 120 尺",
        ),
        senses="黑暗视觉 120 尺 被动察觉 17",
        languages="龙语、通用语",
        desc="青年绿龙的名字。她在龙之塔里待着，那些巨蜘蛛的尸体还在楼下。",
        aliases=("毒牙龙",),
        source="MM",
    ),
    # —— 类人：NPC 与反派 ——
    Monster(
        key="lmop_commoner",
        cn="平民",
        en="Commoner",
        cr=0,
        size="中型",
        kind="类人",
        ac=10,
        hp=4,
        hit_dice="1d8",
        speed=30,
        abilities={"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10},
        attacks=(MonsterAttack("徒手", "str", "0", "钝击", reach=5),),
        traits=("非战斗人员",),
        senses="被动察觉 10",
        languages="通用语",
        desc=("刚铎·寻岩者就是其中一个——一名矮人平民，昏迷但状态稳重，生命值 0。"),
        aliases=("凡人",),
        source="MM",
    ),
    Monster(
        key="lmop_cult_fanatic",
        cn="狂信者",
        en="Cult Fanatic",
        cr=0.25,
        size="中型",
        kind="类人",
        ac=12,
        hp=9,
        hit_dice="2d8",
        speed=30,
        abilities={"str": 11, "dex": 12, "con": 12, "int": 10, "wis": 11, "cha": 10},
        attacks=(
            MonsterAttack("弯刀", "dex", "1d6+1", "挥砍", reach=5),
            MonsterAttack("火把", "dex", "1d4+1", "火焰", reach=5),
        ),
        saves=("dex", "con"),
        traits=("暗影伏击：光照下攻击有优势",),
        senses="被动察觉 10",
        languages="通用语、龙族血脉语",
        desc=(
            "六名人类邪教徒藏在受难者建筑里：四名站岗（每间房两名），"
            "其余在较大的房间休息。"
            "他们穿剪裁成龙翼形状的黑斗篷，戴着风格化龙角的黑色皮甲面具。"
        ),
        aliases=("龙裔邪教徒",),
        source="MM",
    ),
    Monster(
        key="lmop_evil_wizard",
        cn="邪恶法师",
        en="Evil Wizard",
        cr=2,
        size="中型",
        kind="类人",
        ac=12,
        hp=40,
        hit_dice="9d8",
        speed=30,
        abilities={"str": 8, "dex": 14, "con": 12, "int": 15, "wis": 12, "cha": 10},
        attacks=(MonsterAttack("魔杖", "dex", "1d6+2", "穿刺", reach=5),),
        saves=("dex", "con"),
        skills=("arcana", "history", "insight", "perception"),
        traits=("法术：可施放法师护甲与攻击性法术", "推斥：可推开相邻生物"),
        senses="被动察觉 12",
        languages="通用语",
        desc=(
            "伊阿诺，躲在裂隙那头的黑蜘蛛手下。"
            "他正试图掌握酿造药水的技艺——房里散落的书是炼金术基础文本，"
            "**装置是为了酿造隐形药水而设的，不过他还没成功**。"
        ),
        aliases=("伊阿诺",),
        source="MM",
    ),
    Monster(
        key="lmop_red_lash_brute",
        cn="红标帮恶霸",
        en="Red Lash Brute",
        cr=1,
        size="中型",
        kind="类人",
        ac=13,
        hp=27,
        hit_dice="5d8+5",
        speed=30,
        abilities={"str": 15, "dex": 12, "con": 14, "int": 8, "wis": 10, "cha": 9},
        attacks=(
            MonsterAttack("巨棒", "str", "1d8+2", "钝击", reach=5),
            MonsterAttack(
                "重弩",
                "dex",
                "1d8+2",
                "穿刺",
                reach=0,
                ranged_normal=80,
                ranged_max=320,
            ),
        ),
        saves=("str", "con"),
        traits=("包抄：与同伴夹击时攻击有优势",),
        senses="被动察觉 10",
        languages="通用语",
        desc=("红标帮的四个恶霸。**如果其中三个被击败，最后一个会逃向崔森德庄园。**"),
        aliases=("红标帮打手",),
        source="MM",
    ),
    Monster(
        key="lmop_gandfinder",
        cn="刚铎·寻岩者",
        en="Gandfinder",
        cr=1,
        size="中型",
        kind="类人",
        ac=14,
        hp=22,
        hit_dice="4d8+4",
        speed=25,
        abilities={"str": 12, "dex": 14, "con": 12, "int": 13, "wis": 15, "cha": 11},
        attacks=(MonsterAttack("矿工镐", "str", "1d6+1", "挥砍", reach=5),),
        saves=("con", "dex"),
        skills=("perception", "survival", "investigation"),
        traits=("矿脉知识：能在岩石迷宫中找到最短路径",),
        senses="被动察觉 14",
        languages="通用语、矮人语",
        desc=(
            "寻岩者三兄弟之一。他们发现了早已迷失的回声洞入口——"
            "凡德林契约矿场的所在地。他的地图被地精带去了克拉摩堡。"
        ),
        aliases=("刚铎",),
        source="LMoP",
    ),
    Monster(
        key="lmop_shuda_hovent",
        cn="修达·霍温特",
        en="Shuda Hovent",
        cr=1,
        size="中型",
        kind="类人",
        ac=13,
        hp=22,
        hit_dice="4d8+4",
        speed=30,
        abilities={"str": 15, "dex": 12, "con": 13, "int": 10, "wis": 12, "cha": 13},
        attacks=(
            MonsterAttack("长剑", "str", "1d8+2", "挥砍", reach=5),
            MonsterAttack(
                "短弓",
                "dex",
                "1d6+2",
                "穿刺",
                reach=0,
                ranged_normal=80,
                ranged_max=320,
            ),
        ),
        saves=("str", "con"),
        skills=("athletics", "insight", "perception"),
        traits=("老兵：恐惧检定有优势",),
        senses="被动察觉 12",
        languages="通用语",
        desc=(
            "人类战士，护送刚铎一行的。他和矮人一起被地精伏击俘虏——"
            "救回他之后，队伍才得知刚铎他们发现了一座著名的失落矿井。"
        ),
        aliases=("修达",),
        source="LMoP",
    ),
    Monster(
        key="lmop_nanzarth",
        cn="南卓·寻岩者",
        en="Nanzarth",
        cr=1,
        size="中型",
        kind="类人",
        ac=14,
        hp=22,
        hit_dice="4d8+4",
        speed=25,
        abilities={"str": 12, "dex": 14, "con": 12, "int": 13, "wis": 15, "cha": 11},
        attacks=(MonsterAttack("矿工镐", "str", "1d6+1", "挥砍", reach=5),),
        saves=("con", "dex"),
        skills=("perception", "survival", "investigation"),
        traits=("矿脉知识：能在岩石迷宫中找到最短路径",),
        senses="被动察觉 14",
        languages="通用语、矮人语",
        desc=(
            "寻岩者三兄弟之二。**他成了矿井里的囚犯**——塔登已死，队伍在矿井里会找到他。"
        ),
        aliases=("南卓",),
        source="LMoP",
    ),
    Monster(
        key="lmop_black_spider",
        cn="黑蜘蛛那兹纳尔",
        en="Neznarth the Black Spider",
        cr=4,
        size="中型",
        kind="类人",
        ac=15,
        hp=52,
        hit_dice="8d8+24",
        speed=30,
        abilities={"str": 10, "dex": 17, "con": 14, "int": 15, "wis": 14, "cha": 16},
        attacks=(
            MonsterAttack("弯刀", "dex", "1d8+3", "挥砍", reach=5),
            MonsterAttack("毒刃", "dex", "1d6+3", "穿刺", reach=5),
        ),
        saves=("dex", "con"),
        skills=("deception", "insight", "perception", "stealth"),
        traits=("黑暗视觉 60 尺", "精灵血统：可通过隐蔽处无声移动"),
        senses="黑暗视觉 60 尺 被动察觉 16",
        languages="通用语、卓尔语、地下通用语",
        desc=(
            "内兹纳，一名卓尔冒险者。克拉摩地精替他干活——"
            "他想要刚铎和回声洞的地图。**他在神像下有自己的神庙。**"
        ),
        aliases=("内兹纳", "那兹纳尔", "黑蜘蛛"),
        source="LMoP",
    ),
)
