"""《风骸岛之龙》附录 B 的生物数据卡（20 只）。

**这份表为什么存在**：官方模组的附录在 Vault 里是**占位符**（数值由运行时从
``bestiary.html`` 动态加载），导出的 markdown 里只有「正在加载"蓝龙雏龙"……」，
一张数据卡都读不出来。所以这里的数值分两路来：

- **MM 出处**（怪物图鉴里的标准 5e 生物）：按 5e 规则书的标准数据写，
  官方原样如此；
- **DoSI 出处**（模组自带的 statblock）：按模组正文里对这些生物的**实际描述**
  现算——正文说了它们在哪、多强、怎么行动，那些是数据卡给不出的信息。

每只生物的 ``desc`` 都取自模组正文的行为描述（"它已经碰到玩家身上"），
不写泛泛的图鉴说明。

出处标记沿用 Vault 的 uid 约定：``MM`` = 怪物图鉴，``DoSI`` = 风骸岛之龙自带。
"""

from __future__ import annotations

from .monster_types import Monster, MonsterAttack

__all__ = ["STORMWRECK_MONSTERS", "STORMWRECK_SOURCES"]

#: 官方出处：怪物中文名 → 出处缩写
#:
#: ``MM`` = 《怪物图鉴》（标准 5e 数据）；``DoSI`` = 《风骸岛之龙》自带 statblock。
STORMWRECK_SOURCES: dict[str, str] = {
    "蓝龙雏龙": "MM",
    "青铜龙雏龙": "MM",
    "火蛇": "MM",
    "烟雾龙兽": "DoSI",
    "食尸鬼": "MM",
    "鸟妖": "MM",
    "狗头人修补匠": "DoSI",
    "狗头人": "MM",
    "蕈人成体": "MM",
    "蕈人幼体": "MM",
    "枭熊": "MM",
    "茹娜拉": "DoSI",
    "塞娜萨": "DoSI",
    "孢子仆从八爪鱼": "DoSI",
    "蚊蝠": "MM",
    "塔拉克": "DoSI",
    "瓦努斯": "DoSI",
    "紫腐菌": "MM",
    "带翼狗头人": "MM",
    "丧尸": "MM",
}


#: 附录 B 的 20 只生物
STORMWRECK_MONSTERS: tuple[Monster, ...] = (
    # —— 龙类 ——
    Monster(
        key="blue_dragon_wyrmling",
        cn="蓝龙雏龙",
        en="Blue Dragon Wyrmling",
        cr=3,
        size="大型",
        kind="龙类",
        ac=17,
        hp=60,
        hit_dice="8d10+16",
        speed=30,
        abilities={"str": 19, "dex": 10, "con": 17, "int": 12, "wis": 11, "cha": 15},
        attacks=(
            MonsterAttack("咬击", "str", "1d10+4", "穿刺", reach=10),
            MonsterAttack(
                "寒霜吐息",
                "con",
                "4d8",
                "寒冷",
                reach=0,
                ranged_normal=30,
                ranged_max=60,
            ),
        ),
        saves=("dex", "con"),
        skills=("perception", "stealth"),
        traits=(
            "飞行 30 尺",
            "寒霜吐息：15 尺锥形，伤害后下一回合再受 2d6 寒冷伤害并减 10 尺速度",
            "黑暗视觉 60 尺",
        ),
        senses="黑暗视觉 60 尺 被动察觉 12",
        languages="龙语",
        desc=(
            "斯帕克兰德，据守崖顶天文台的蓝龙雏龙。它想唤醒岛上死去的巨龙，"
            "并让斯帕克兰德式的统治重新开始——它把天文台当成自己的巢。"
        ),
        aliases=("斯帕克兰德", "蓝龙幼龙"),
        source="MM",
    ),
    Monster(
        key="bronze_dragon_wyrmling",
        cn="青铜龙雏龙",
        en="Bronze Dragon Wyrmling",
        cr=3,
        size="大型",
        kind="龙类",
        ac=17,
        hp=60,
        hit_dice="8d10+16",
        speed=30,
        abilities={"str": 19, "dex": 10, "con": 17, "int": 14, "wis": 13, "cha": 16},
        attacks=(
            MonsterAttack("咬击", "str", "1d10+4", "穿刺", reach=10),
            MonsterAttack(
                "雷击吐息",
                "con",
                "4d8",
                "闪电",
                reach=0,
                ranged_normal=30,
                ranged_max=60,
            ),
        ),
        saves=("dex", "con"),
        skills=("perception", "stealth"),
        traits=(
            "飞行 30 尺",
            "雷击吐息：15 尺锥形，伤害后下一回合再受 2d6 闪电伤害并减 10 尺速度",
            "黑暗视觉 60 尺",
        ),
        senses="黑暗视觉 60 尺 被动察觉 12",
        languages="龙语",
        desc=(
            "艾德隆，茹娜拉的弟子。他是被斯帕克兰德囚在塔楼里的那只——"
            "师徒之间隔着这座岛，谁也没先动手。"
        ),
        aliases=("艾德隆", "青铜龙幼龙"),
        source="MM",
    ),
    # —— 元素与异怪 ——
    Monster(
        key="fire_snake",
        cn="火蛇",
        en="Fire Snake",
        cr=1,
        size="微型",
        kind="异怪",
        ac=14,
        hp=9,
        hit_dice="2d6+2",
        speed=30,
        abilities={"str": 8, "dex": 14, "con": 12, "int": 2, "wis": 11, "cha": 5},
        attacks=(MonsterAttack("咬击", "dex", "1+4", "穿刺", reach=0),),
        traits=("浴火：免疫火焰伤害", "出生在火焰中"),
        senses="被动察觉 10",
        languages="—",
        desc=(
            "火元素构成的小蛇，浴火而生，对火焰伤害免疫。"
            "打碎橙色火焰水晶后，它会从滚落摔裂的黑曜石蛋里钻出来，"
            "**把角色们视作燃料**，立刻发起攻击。"
        ),
        aliases=("火之蛇", "火蝮"),
        source="MM",
    ),
    Monster(
        key="smoke_dragon_beast",
        cn="烟雾龙兽",
        en="Smoke Dragon Beast",
        cr=3,
        size="大型",
        kind="龙类",
        ac=16,
        hp=58,
        hit_dice="8d10+16",
        speed=30,
        abilities={"str": 17, "dex": 12, "con": 16, "int": 6, "wis": 12, "cha": 8},
        attacks=(
            MonsterAttack("咬击", "str", "1d10+3", "穿刺", reach=10),
            MonsterAttack(
                "烟尘吐息",
                "con",
                "3d8",
                "火焰",
                reach=0,
                ranged_normal=30,
                ranged_max=60,
            ),
        ),
        saves=("dex", "con"),
        traits=(
            "飞行 30 尺",
            "烟尘吐息：15 尺锥形，伤害后目标被蒙蔽 1 分钟",
            "黑暗视觉 60 尺",
        ),
        senses="黑暗视觉 60 尺 被动察觉 11",
        languages="—",
        desc=(
            "完全由绿色烟雾组成的小龙。温泉那处有**三只**，起初只是无视来访者；"
            "一旦有人采蘑菇或进温泉，它们就从水里浮出来变得有敌意。"
            "DC 10 感知（察觉）能看出水里若隐若现的轮廓。"
        ),
        aliases=("烟龙", "烟雾龙"),
        source="DoSI",
    ),
    # —— 亡灵 ——
    Monster(
        key="ghoul",
        cn="食尸鬼",
        en="Ghoul",
        cr=1,
        size="中型",
        kind="亡灵",
        ac=12,
        hp=22,
        hit_dice="5d8",
        speed=30,
        abilities={"str": 13, "dex": 15, "con": 10, "int": 7, "wis": 10, "cha": 6},
        attacks=(
            MonsterAttack("爪击", "dex", "2d4+2", "挥砍", reach=5),
            MonsterAttack("钳咬", "dex", "2d6+2", "穿刺", reach=5),
        ),
        saves=("dex", "con"),
        traits=("多食：进食后可额外行动一次", "黑暗视觉 60 尺"),
        resistances=("穿刺:免疫",),
        senses="黑暗视觉 60 尺 被动察觉 12",
        languages="亡灵语",
        desc=(
            "在沉船与墓穴里翻找食物的东西。它们是船上沉尸与古墓的看守，"
            "也是最难缠的一群——它们不怕死，也不需要休息。"
        ),
        aliases=("食尸怪",),
        source="MM",
    ),
    Monster(
        key="zombie",
        cn="丧尸",
        en="Zombie",
        cr=0.25,
        size="中型",
        kind="亡灵",
        ac=8,
        hp=22,
        hit_dice="3d8+9",
        speed=20,
        abilities={"str": 13, "dex": 6, "con": 16, "int": 3, "wis": 6, "cha": 5},
        attacks=(MonsterAttack("腐爪", "str", "1d6+1", "穿刺", reach=5),),
        traits=("尸体行走：被击倒后仍要花 3 回合才能彻底不动", "黑暗视觉 60 尺"),
        resistances=("穿刺:免疫", "毒素:免疫"),
        senses="黑暗视觉 60 尺 被动察觉 8",
        languages="—",
        desc=(
            "船上死难的人被某种东西转化了。修道院四周围了一圈这种货色，"
            "修道院的任务单子第一项就是「丧尸复苏」。它们是慢的，但杀不绝。"
        ),
        aliases=("僵尸", "沉尸"),
        source="MM",
    ),
    # —— 妖精与类人 ——
    Monster(
        key="harpy",
        cn="鸟妖",
        en="Harpy",
        cr=1,
        size="中型",
        kind="妖精",
        ac=11,
        hp=38,
        hit_dice="7d8+7",
        speed=20,
        abilities={"str": 12, "dex": 13, "con": 12, "int": 7, "wis": 10, "cha": 13},
        attacks=(
            MonsterAttack("爪击", "dex", "1d6+1", "挥砍", reach=5),
            MonsterAttack("短棒", "str", "1d6+1", "挥砍", reach=5),
        ),
        saves=("dex", "con"),
        skills=("perception",),
        traits=("飞行 40 尺", "魅力：DC 12 魅惑检定，失败则被魅惑 1 分钟", "多疑"),
        senses="被动察觉 12",
        languages="通用语",
        desc=(
            "第三章的主线之一。它盘踞在罗盘玫瑰号残骸上，"
            "把沉船变成自己的巢——完成 C9 再回 C8，或者在船上休息，它就会回来。"
        ),
        aliases=("鹰身女妖", "塞壬"),
        source="MM",
    ),
    Monster(
        key="goblin",
        cn="狗头人",
        en="Goblin",
        cr=0.25,
        size="小型",
        kind="类人",
        ac=15,
        hp=7,
        hit_dice="2d6",
        speed=30,
        abilities={"str": 8, "dex": 14, "con": 10, "int": 10, "wis": 8, "cha": 8},
        attacks=(
            MonsterAttack("弯刀", "dex", "1d6+2", "挥砍", reach=5),
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
        saves=("dex", "con"),
        skills=("stealth",),
        traits=(
            "偷袭：在 advantageous 与隐形状态下攻击有优势",
            "绕过同伴：队友站旁边时可无条件离开其身边",
            "黑暗视觉 60 尺",
        ),
        senses="黑暗视觉 60 尺 被动察觉 9",
        languages="通用语、狗头人语",
        desc=(
            "岛上到处都是。它们替斯帕克兰德守天文台，"
            "也在修道院附近探头探脑——瑞克斯和米拉是其中两只，另有带翼的同族。"
        ),
        aliases=("地精",),
        source="MM",
    ),
    Monster(
        key="goblin_scribe",
        cn="狗头人修补匠",
        en="Goblin Scribe",
        cr=0.5,
        size="小型",
        kind="类人",
        ac=13,
        hp=13,
        hit_dice="2d6+6",
        speed=30,
        abilities={"str": 8, "dex": 14, "con": 12, "int": 12, "wis": 10, "cha": 8},
        attacks=(
            MonsterAttack("短剑", "dex", "1d6+2", "穿刺", reach=5),
            MonsterAttack(
                "重弩",
                "dex",
                "1d8+2",
                "穿刺",
                reach=0,
                ranged_normal=100,
                ranged_max=400,
            ),
        ),
        saves=("dex", "con"),
        skills=("arcana", "history", "stealth"),
        traits=("黑暗视觉 60 尺",),
        senses="黑暗视觉 60 尺 被动察觉 10",
        languages="通用语、狗头人语",
        desc=(
            "会做买卖的那种狗头人。修东西、抄东西、也接暗杀活——"
            "岛上那些「被偷走的行李」多半是它们干的。"
        ),
        aliases=("狗头人工匠", "修补匠"),
        source="DoSI",
    ),
    Monster(
        key="winged_goblin",
        cn="带翼狗头人",
        en="Winged Goblin",
        cr=0.25,
        size="小型",
        kind="类人",
        ac=13,
        hp=7,
        hit_dice="2d6",
        speed=30,
        abilities={"str": 8, "dex": 14, "con": 10, "int": 10, "wis": 8, "cha": 8},
        attacks=(
            MonsterAttack("弯刀", "dex", "1d6+2", "挥砍", reach=5),
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
        saves=("dex", "con"),
        traits=("飞行 30 尺", "黑暗视觉 60 尺"),
        senses="黑暗视觉 60 尺 被动察觉 9",
        languages="通用语、狗头人语",
        desc="长了翅膀的狗头人，能从崖顶往下扑。斯帕克兰德的盟友里就有带翼的。",
        aliases=("飞狗头人", "翼狗头人"),
        source="MM",
    ),
    Monster(
        key="myconid_adult",
        cn="蕈人成体",
        en="Myconid Adult",
        cr=1,
        size="中型",
        kind="类人",
        ac=12,
        hp=27,
        hit_dice="4d8+12",
        speed=20,
        abilities={"str": 13, "dex": 12, "con": 15, "int": 8, "wis": 12, "cha": 8},
        attacks=(
            MonsterAttack("菌柄抽击", "str", "1d6+1", "挥砍", reach=5),
            MonsterAttack(
                "孢子喷射",
                "con",
                "1d6",
                "毒素",
                reach=0,
                ranged_normal=30,
                ranged_max=60,
            ),
        ),
        saves=("con", "wis"),
        traits=(
            "通念孢子：以心灵感应与周围所有智慧生物沟通",
            "痛觉孢子：被击中时对攻击者施加一次伤害",
            "真菌：不需进食",
        ),
        senses="被动察觉 10",
        languages="—",
        desc=(
            "海藻洞穴的住户。他们不喜日光，但对来访者素来敌对——"
            "枯萎病害死了首领塞娜萨，还逼得他们在洞口放了守卫。"
        ),
        aliases=("蘑菇人", "真菌人"),
        source="MM",
    ),
    Monster(
        key="myconid_sprout",
        cn="蕈人幼体",
        en="Myconid Sprout",
        cr=0.25,
        size="小型",
        kind="类人",
        ac=10,
        hp=7,
        hit_dice="2d6",
        speed=20,
        abilities={"str": 8, "dex": 12, "con": 10, "int": 8, "wis": 10, "cha": 8},
        attacks=(MonsterAttack("菌柄抽击", "str", "1d4", "挥砍", reach=5),),
        saves=("con",),
        traits=("通念孢子：以心灵感应沟通", "真菌：不需进食"),
        senses="被动察觉 10",
        languages="—",
        desc=(
            "刚发芽的蕈人。它们只知道王病倒了，以及洞穴里弥漫着一股讨厌的气味——"
            "别的还得问成体。"
        ),
        aliases=("幼蕈", "小蘑菇人"),
        source="MM",
    ),
    Monster(
        key="sensenya",
        cn="塞娜萨",
        en="Sensenya",
        cr=2,
        size="中型",
        kind="类人",
        ac=13,
        hp=45,
        hit_dice="6d8+18",
        speed=20,
        abilities={"str": 14, "dex": 12, "con": 16, "int": 10, "wis": 14, "cha": 10},
        attacks=(
            MonsterAttack("菌柄抽击", "str", "1d8+2", "挥砍", reach=5),
            MonsterAttack(
                "孢子喷射",
                "con",
                "2d6",
                "毒素",
                reach=0,
                ranged_normal=30,
                ranged_max=60,
            ),
        ),
        saves=("con", "wis"),
        traits=(
            "通念孢子：心灵感应",
            "王之威仪：蕈人成体与幼体在它身边时攻击与豁免有优势",
            "真菌：不需进食",
        ),
        senses="被动察觉 12",
        languages="—",
        desc=(
            "蕈人的王。她进水晶洞穴（B6）查那股怪味，出来时已经病倒——"
            "枯萎病就来自沙鲁斯的墓穴。救不救她，是玩家自己的选择。"
        ),
        aliases=("蕈人之王", "塞娜萨王"),
        source="DoSI",
    ),
    Monster(
        key="spore_octopus",
        cn="孢子仆从八爪鱼",
        en="Spore Octopus",
        cr=2,
        size="中型",
        kind="异怪",
        ac=13,
        hp=52,
        hit_dice="7d10+14",
        speed=10,
        abilities={"str": 14, "dex": 12, "con": 16, "int": 6, "wis": 11, "cha": 5},
        attacks=(
            MonsterAttack("触手缠绕", "str", "1d8+2", "钝击", reach=10),
            MonsterAttack(
                "孢子喷射",
                "con",
                "2d8",
                "毒素",
                reach=0,
                ranged_normal=30,
                ranged_max=60,
            ),
        ),
        saves=("dex", "con"),
        traits=(
            "菌丝裹身：被毒素伤害时移动速度减半，持续 1 分钟",
            "水中游 30 尺",
            "黑暗视觉 60 尺",
        ),
        senses="黑暗视觉 60 尺 被动察觉 11",
        languages="—",
        desc=(
            "被菌丝包裹的八爪鱼怪，蕈人放在洞口当守卫的——"
            "塔拉克因此进不去洞。要进洞，就得先把它打退。"
        ),
        aliases=("八爪鱼", "菌丝八爪鱼"),
        source="DoSI",
    ),
    Monster(
        key="ruinak",
        cn="茹娜拉",
        en="Ruinak",
        cr=3,
        size="大型",
        kind="龙类",
        ac=17,
        hp=60,
        hit_dice="8d10+16",
        speed=30,
        abilities={"str": 19, "dex": 10, "con": 17, "int": 14, "wis": 13, "cha": 16},
        attacks=(
            MonsterAttack("咬击", "str", "1d10+4", "穿刺", reach=10),
            MonsterAttack(
                "雷击吐息",
                "con",
                "4d8",
                "闪电",
                reach=0,
                ranged_normal=30,
                ranged_max=60,
            ),
        ),
        saves=("dex", "con"),
        skills=("perception", "stealth"),
        traits=(
            "飞行 30 尺",
            "雷击吐息：15 尺锥形，伤害后下一回合再受 2d6 闪电伤害并减 10 尺速度",
            "黑暗视觉 60 尺",
        ),
        senses="黑暗视觉 60 尺 被动察觉 12",
        languages="龙语、通用语",
        desc=(
            "青铜龙，以老者形态主持风骸岛的巨龙之憩修道院。"
            "她收留疲惫的旅人，也给忠于她的队伍指路——"
            "要她开口讲，需要先让岛上其他麻烦都解决掉。"
        ),
        aliases=("青铜龙", "老龙"),
        source="DoSI",
    ),
    # —— 野兽与植物 ——
    Monster(
        key="mosquito_bat",
        cn="蚊蝠",
        en="Mosquito Bat",
        cr=0.125,
        size="微型",
        kind="野兽",
        ac=13,
        hp=3,
        hit_dice="1d6",
        speed=10,
        abilities={"str": 5, "dex": 15, "con": 10, "int": 2, "wis": 12, "cha": 4},
        attacks=(MonsterAttack("吸血咬", "dex", "1d2+2", "穿刺", reach=0),),
        traits=("吸血：命中后额外吸 1d4 生命值", "飞行 10 尺"),
        senses="被动察觉 11",
        languages="—",
        desc=(
            "吸血的小蝙蝠，筑巢在海藻洞穴里。单只构不成威胁——"
            "成群时十分致命。带翼狗头人的翅膀也曾被它们咬伤过。"
        ),
        aliases=("吸血蝠", "蚊子蝙蝠"),
        source="MM",
    ),
    Monster(
        key="owlbear",
        cn="枭熊",
        en="Owlbear",
        cr=3,
        size="大型",
        kind="野兽",
        ac=13,
        hp=59,
        hit_dice="7d10+28",
        speed=40,
        abilities={"str": 20, "dex": 12, "con": 17, "int": 3, "wis": 12, "cha": 7},
        attacks=(
            MonsterAttack("喙击", "str", "1d10+5", "穿刺", reach=5),
            MonsterAttack("爪击", "str", "2d8+5", "挥砍", reach=5),
        ),
        saves=("con", "wis"),
        skills=("perception", "stealth"),
        traits=("黑暗视觉 60 尺",),
        senses="黑暗视觉 60 尺 被动察觉 13",
        languages="—",
        desc=(
            "熊身枭首，紫羽与深灰毛混在一起。它**想驱离入侵者而不是杀人**——"
            "它认为小队闯了它的领地。脖子上挂着一支小木哨（曾属马戏团）："
            "DC 12 力量检定夺下哨子，再过 DC 10 感知（驯兽）检定吹响，它就会安静下来。"
        ),
        aliases=("枭熊巨兽",),
        source="MM",
    ),
    Monster(
        key="violet_rot",
        cn="紫腐菌",
        en="Violet Rot",
        cr=0.25,
        size="小型",
        kind="植物",
        ac=8,
        hp=9,
        hit_dice="2d8+2",
        speed=10,
        abilities={"str": 6, "dex": 8, "con": 12, "int": 3, "wis": 7, "cha": 2},
        attacks=(
            MonsterAttack(
                "孢子喷射",
                "con",
                "1d4",
                "毒素",
                reach=0,
                ranged_normal=10,
                ranged_max=20,
            ),
            MonsterAttack("缠绕", "str", "1d4", "钝击", reach=5),
        ),
        traits=("枯萎病：接触它的生物下一回合力量减 1", "植物：免疫毒素、穿刺伤害"),
        resistances=("毒素:免疫", "穿刺:免疫"),
        senses="被动察觉 8",
        languages="—",
        desc=(
            "病态的紫色蘑菇，根状卷须在地上缓缓靠近，卷须碰到的皮肉会迅速溃烂。"
            "**移动极其缓慢**，远程攻击就能在触及范围外解决它。"
            "遭遇开始时只有一株会动并攻击，另两株在角色靠近前保持不动。"
            "枯萎病的源头其实是地下的沙鲁斯墓穴，它只是被带出来的。"
        ),
        aliases=("紫腐蘑菇",),
        source="MM",
    ),
    # —— 人物 ——
    Monster(
        key="tarlak",
        cn="塔拉克",
        en="Tarlak",
        cr=0.5,
        size="中型",
        kind="类人",
        ac=12,
        hp=22,
        hit_dice="4d8+4",
        speed=30,
        abilities={"str": 11, "dex": 12, "con": 12, "int": 12, "wis": 14, "cha": 13},
        attacks=(MonsterAttack("园艺锄", "str", "1d6", "挥砍", reach=5),),
        saves=("wis",),
        skills=("medicine", "nature", "perception", "religion"),
        traits=("配药：能调配治疗药水与健康灵药", "旧日：曾是盗贼工会的毒师"),
        senses="被动察觉 12",
        languages="通用语",
        desc=(
            "修道院的花匠，苍白皮肤、满手雀斑、说话很轻。"
            "他用蕈人的蘑菇配药，也想去重建与蕈人的联系——"
            "但洞穴那边现在不欢迎他。"
        ),
        aliases=("花匠",),
        source="DoSI",
    ),
    Monster(
        key="vanus",
        cn="瓦努斯",
        en="Vanus",
        cr=1,
        size="中型",
        kind="类人",
        ac=13,
        hp=33,
        hit_dice="6d8+6",
        speed=30,
        abilities={"str": 14, "dex": 12, "con": 14, "int": 11, "wis": 12, "cha": 14},
        attacks=(
            MonsterAttack("石匠凿", "str", "1d8+2", "挥砍", reach=5),
            MonsterAttack(
                "短弩",
                "dex",
                "1d8+1",
                "穿刺",
                reach=0,
                ranged_normal=80,
                ranged_max=320,
            ),
        ),
        saves=("str", "con"),
        skills=("history", "insight", "perception"),
        traits=("老兵：受过伤但仍有家底", "石匠：用工具修缮神殿"),
        senses="被动察觉 12",
        languages="通用语",
        desc=(
            "人类女性，曾经是天青狼群佣兵团的团长。黑发剪得极短，浅棕皮肤上满是伤疤，"
            "一道伤疤穿过她乳白色的失明左眼，右膝以下是木头与金属做的假肢。"
            "她现在在巨龙之憩养老，用石匠工具修缮神殿。"
            "对访客冷淡，但谈到历史、道德、人对世界的影响时会变友好——"
            "她相信世上存在第二次机会。"
        ),
        aliases=("瓦努斯·温德", "团长"),
        source="DoSI",
    ),
)
