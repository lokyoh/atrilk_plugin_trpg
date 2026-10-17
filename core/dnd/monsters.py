"""DND 5e 怪物数据表。

对应战斗系统需要的**敌人侧数值**：AC、生命（含生命骰）、速度、六大属性、攻击
（攻击加值由属性调整值 + 熟练加值算出、伤害骰原样给出）、豁免 / 技能熟练、特性。

设计约定：

- **攻击加值不写死**，由 :meth:`Monster.attack_bonus` 用「熟练加值（按 CR）+ 属性调整值」
  现算；伤害表达式里的加值与该属性保持一致，所以同一份数据既给 DM 看也能直接掷。
- 加值 / 豁免 / 技能沿用 :mod:`.attributes` 与 :mod:`.skills` 的键，与角色卡同一套算法。
- 这里的数值是可玩的近似值，不追求与《怪物图鉴》逐字一致；**以本表为准**。

生成器 / DM 通过 :func:`get_monster` 按 key、中文名或别名取怪物。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from .attributes import ability_modifier, format_ability_block, get_ability
from .skills import get_skill

__all__ = [
    "MONSTERS",
    "MONSTER_MAP",
    "Monster",
    "MonsterAttack",
    "get_monster",
    "monster_names",
    "proficiency_for_cr",
]


def proficiency_for_cr(cr: float) -> int:
    """按挑战等级（CR）取熟练加值：CR 0~4 → +2，5~8 → +3，9~12 → +4 ……"""
    value = float(cr)
    if value >= 17:
        return 6
    if value >= 13:
        return 5
    if value >= 9:
        return 4
    if value >= 5:
        return 3
    return 2


def _cr_text(cr: float) -> str:
    """把 CR 数值排成习惯写法：0.125 → ``1/8``、0.5 → ``1/2``。"""
    if cr >= 1:
        return str(int(cr)) if float(cr).is_integer() else f"{cr:g}"
    if cr == 0.125:
        return "1/8"
    if cr == 0.25:
        return "1/4"
    if cr == 0.5:
        return "1/2"
    return f"{cr:g}"


@dataclass(frozen=True, slots=True)
class MonsterAttack:
    """怪物的一项攻击。"""

    name: str  #: 攻击名，如 ``"弯刀"`` ``"爪击"``
    ability: str  #: 用来结算攻击加值的属性键（str / dex / con / int / wis / cha）
    damage: str  #: 伤害表达式，如 ``"1d6+2"``；纯数字表示固定伤害
    damage_type: str  #: 伤害类型，如 ``"挥砍"``
    reach: int = 5  #: 触及（尺）；远程攻击为 0
    ranged_normal: int = 0  #: 远程常规射程（尺）
    ranged_max: int = 0  #: 远程最大射程（尺）
    notes: str = ""  #: 补充说明（附赠动作、需要豁免等）

    @property
    def is_ranged(self) -> bool:
        return self.ranged_normal > 0

    @property
    def range_text(self) -> str:
        if self.is_ranged:
            return f"射程 {self.ranged_normal}/{self.ranged_max} 尺"
        return f"触及 {self.reach} 尺"

    def label(self) -> str:
        return f"{self.name}（{self.damage} {self.damage_type}，{self.range_text}）"


@dataclass(frozen=True, slots=True)
class Monster:
    """一只怪物（模板，不含具体个体的当前生命值）。"""

    key: str
    cn: str
    en: str
    cr: float  #: 挑战等级
    size: str  #: 体型
    kind: str  #: 生物类型，如 ``"类人"`` ``"亡灵"``
    ac: int
    hp: int  #: 平均生命值（骰出的结果用 hit_dice）
    hit_dice: str  #: 生命骰表达式，如 ``"2d6"``
    speed: int
    abilities: Mapping[str, int]  #: 六大属性值
    attacks: tuple[MonsterAttack, ...] = ()
    saves: tuple[str, ...] = ()  #: 有豁免熟练的属性键
    skills: tuple[str, ...] = ()  #: 有技能熟练的技能键
    traits: tuple[str, ...] = ()  #: 特性
    senses: str = "被动察觉 10"
    languages: str = ""
    desc: str = ""
    #: 额外别名（除 key / 中文名 / 英文名外还能被认出来的写法）
    aliases: tuple[str, ...] = field(default=())

    # —— 派生数值 ——

    @property
    def proficiency(self) -> int:
        """熟练加值。"""
        return proficiency_for_cr(self.cr)

    @property
    def initiative(self) -> int:
        """先攻加值。"""
        return self.modifier("dex")

    def modifier(self, ability: str) -> int:
        """某属性的调整值。"""
        return ability_modifier(self.abilities.get(get_ability(ability).key, 10))

    @property
    def cr_text(self) -> str:
        return _cr_text(float(self.cr))

    def attack_bonus(self, attack: MonsterAttack | str) -> int:
        """攻击加值 = 熟练加值 + 属性调整值。"""
        item = self.get_attack(attack)
        if item is None:
            return self.proficiency
        return self.proficiency + self.modifier(item.ability)

    def get_attack(self, attack: MonsterAttack | str) -> MonsterAttack | None:
        """按对象 / 名字取一项攻击；没写名字时取第一项。"""
        if isinstance(attack, MonsterAttack):
            return attack
        text = str(attack or "").strip()
        if not text:
            return self.attacks[0] if self.attacks else None
        for item in self.attacks:
            if text == item.name:
                return item
        for item in self.attacks:
            if text in item.name or item.name in text:
                return item
        return None

    def has_save(self, ability: str) -> bool:
        return get_ability(ability).key in self.saves

    def save_bonus(self, ability: str) -> int:
        """豁免加值。"""
        key = get_ability(ability).key
        return self.modifier(key) + (self.proficiency if key in self.saves else 0)

    def skill_bonus(self, skill: str) -> int:
        """技能加值。"""
        skill_obj = get_skill(skill)
        bonus = self.modifier(skill_obj.ability_obj.key)
        return bonus + (self.proficiency if skill_obj.key in self.skills else 0)

    # —— 展示 ——

    def attack_lines(self) -> list[str]:
        lines = []
        for attack in self.attacks:
            lines.append(
                f"{attack.name}：{self.attack_bonus(attack):+d}，"
                f"{attack.damage} {attack.damage_type}，{attack.range_text}"
                + (f"（{attack.notes}）" if attack.notes else "")
            )
        return lines

    def summary(self) -> str:
        """一行摘要，用于战斗开始时列出敌人。"""
        text = (
            f"【{self.cn}】{self.size}{self.kind} CR{self.cr_text}  "
            f"AC {self.ac}  HP {self.hp}({self.hit_dice})  先攻 {self.initiative:+d}  "
            f"速度 {self.speed} 尺"
        )
        if self.attacks:
            first = self.attacks[0]
            text += f"  攻击 {first.name} {self.attack_bonus(first):+d}/{first.damage}"
        return text

    def stat_block(self) -> str:
        """完整数据块（DM 查询用）。"""
        lines = [
            f"{self.cn}（{self.en}）  CR {self.cr_text}  {self.size}{self.kind}",
            f"AC {self.ac}  HP {self.hp}（{self.hit_dice}）  速度 {self.speed} 尺  "
            f"先攻 {self.initiative:+d}  熟练 {self.proficiency:+d}",
            format_ability_block(self.abilities),
        ]
        if self.saves:
            lines.append(
                "豁免："
                + "、".join(
                    f"{get_ability(k).cn}{self.save_bonus(k):+d}" for k in self.saves
                )
            )
        if self.skills:
            lines.append(
                "技能："
                + "、".join(
                    f"{get_skill(k).cn}{self.skill_bonus(k):+d}" for k in self.skills
                )
            )
        if self.attacks:
            lines.append("攻击：" + "；".join(self.attack_lines()))
        if self.traits:
            lines.append("特性：" + "；".join(self.traits))
        if self.senses:
            lines.append(f"感官：{self.senses}")
        if self.languages:
            lines.append(f"语言：{self.languages}")
        if self.desc:
            lines.append(self.desc)
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 数据表（CR 0 ~ 10）
# --------------------------------------------------------------------------- #

MONSTERS: tuple[Monster, ...] = (
    # —— CR 1/8 ——
    Monster(
        key="giant_rat",
        cn="巨鼠",
        en="Giant Rat",
        cr=0.125,
        size="小型",
        kind="野兽",
        ac=12,
        hp=7,
        hit_dice="2d6",
        speed=30,
        abilities={"str": 7, "dex": 15, "con": 11, "int": 2, "wis": 10, "cha": 4},
        attacks=(MonsterAttack("啃咬", "dex", "1d4+2", "穿刺"),),
        traits=("集群战术：有同伴贴着目标时攻击有优势", "黑暗视觉 30 尺"),
        senses="被动察觉 10",
        desc="下水道与粮仓的常客，成群出现时格外难缠。",
        aliases=("大老鼠", "巨鼠群"),
    ),
    Monster(
        key="bat",
        cn="蝙蝠",
        en="Bat",
        cr=0,
        size="微型",
        kind="野兽",
        ac=12,
        hp=1,
        hit_dice="1d4-1",
        speed=5,
        abilities={"str": 2, "dex": 15, "con": 8, "int": 2, "wis": 12, "cha": 4},
        attacks=(MonsterAttack("啃咬", "dex", "1", "穿刺"),),
        traits=("回声定位：耳聋时无法使用盲视", "盲视 60 尺", "飞行 30 尺"),
        senses="盲视 60 尺 被动察觉 11",
        desc="几乎构不成威胁，但成群的振翅声能掩盖别的动静。",
    ),
    Monster(
        key="bandit",
        cn="匪徒",
        en="Bandit",
        cr=0.125,
        size="中型",
        kind="类人",
        ac=12,
        hp=11,
        hit_dice="2d8+2",
        speed=30,
        abilities={"str": 11, "dex": 12, "con": 12, "int": 10, "wis": 10, "cha": 10},
        attacks=(
            MonsterAttack("弯刀", "dex", "1d6+1", "挥砍"),
            MonsterAttack(
                "轻弩",
                "dex",
                "1d8+1",
                "穿刺",
                reach=0,
                ranged_normal=80,
                ranged_max=320,
            ),
        ),
        traits=("皮甲护身，见势不妙会先谈条件",),
        senses="被动察觉 10",
        languages="通用语",
        desc="拦路收“过路费”的普通人，多半只想求财。",
        aliases=("强盗", "土匪", "盗匪", "山贼"),
    ),
    Monster(
        key="guard",
        cn="卫兵",
        en="Guard",
        cr=0.125,
        size="中型",
        kind="类人",
        ac=16,
        hp=11,
        hit_dice="2d8+2",
        speed=30,
        abilities={"str": 13, "dex": 12, "con": 12, "int": 10, "wis": 11, "cha": 10},
        attacks=(
            MonsterAttack(
                "长矛", "str", "1d6+1", "穿刺", notes="可双手持握，伤害改为 1d8+1"
            ),
        ),
        skills=("perception",),
        traits=("链甲衫 + 盾牌",),
        senses="被动察觉 12",
        languages="通用语",
        desc="城门口或商队里的职业守卫，按规章办事。",
        aliases=("守卫", "门卫", "城卫"),
    ),
    Monster(
        key="cultist",
        cn="邪教徒",
        en="Cultist",
        cr=0.125,
        size="中型",
        kind="类人",
        ac=12,
        hp=9,
        hit_dice="2d8",
        speed=30,
        abilities={"str": 11, "dex": 12, "con": 10, "int": 10, "wis": 11, "cha": 10},
        attacks=(MonsterAttack("弯刀", "dex", "1d6+1", "挥砍"),),
        skills=("deception", "religion"),
        traits=("狂信：为首领与信条行动时不会溃散",),
        senses="被动察觉 10",
        languages="通用语",
        desc="被密教拉拢的信徒，往往相信自己在做正确的事。",
        aliases=("信徒", "狂信徒"),
    ),
    Monster(
        key="kobold",
        cn="狗头人",
        en="Kobold",
        cr=0.125,
        size="小型",
        kind="类人",
        ac=12,
        hp=5,
        hit_dice="2d6-2",
        speed=30,
        abilities={"str": 7, "dex": 15, "con": 9, "int": 8, "wis": 7, "cha": 8},
        attacks=(MonsterAttack("匕首", "dex", "1d4+2", "穿刺"),),
        traits=(
            "集群战术：有同伴贴着目标时攻击有优势",
            "阳光敏感：直射阳光下攻击与察觉有劣势",
            "黑暗视觉 60 尺",
            "擅长陷阱：挖坑、设绳、布设机关",
        ),
        senses="黑暗视觉 60 尺 被动察觉 8",
        languages="通用语、龙语",
        desc="胆小又爱摆弄机关的洞穴居民，靠数量与陷阱取胜。",
    ),
    # —— CR 1/4 ——
    Monster(
        key="goblin",
        cn="地精",
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
            MonsterAttack("弯刀", "dex", "1d6+2", "挥砍"),
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
        skills=("stealth",),
        traits=("敏捷撤离：附赠动作撤离或躲藏", "黑暗视觉 60 尺"),
        senses="黑暗视觉 60 尺 被动察觉 9",
        languages="通用语、地精语",
        desc="成群结队的掠夺者，怯懦但狡猾，惯用埋伏与夹击。",
        aliases=("哥布林",),
    ),
    Monster(
        key="wolf",
        cn="狼",
        en="Wolf",
        cr=0.25,
        size="中型",
        kind="野兽",
        ac=13,
        hp=11,
        hit_dice="2d8+2",
        speed=40,
        abilities={"str": 12, "dex": 15, "con": 12, "int": 3, "wis": 12, "cha": 6},
        attacks=(
            MonsterAttack(
                "啃咬",
                "str",
                "2d4+1",
                "穿刺",
                notes="命中后目标力量豁免 DC11，失败倒地",
            ),
        ),
        skills=("perception", "stealth"),
        traits=(
            "集群战术：有同伴贴着目标时攻击有优势",
            "锐敏听觉与嗅觉",
            "黑暗视觉 60 尺",
        ),
        senses="被动察觉 13",
        desc="荒原与林间的猎手，从不单打独斗。",
        aliases=("野狼", "灰狼"),
    ),
    Monster(
        key="skeleton",
        cn="骷髅",
        en="Skeleton",
        cr=0.25,
        size="中型",
        kind="亡灵",
        ac=13,
        hp=13,
        hit_dice="2d8+4",
        speed=30,
        abilities={"str": 10, "dex": 14, "con": 15, "int": 6, "wis": 8, "cha": 5},
        attacks=(
            MonsterAttack("短剑", "dex", "1d6+2", "穿刺"),
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
        traits=(
            "亡灵坚韧：对毒素与力竭免疫，无法被魅惑 / 恐惧",
            "钝击易感：受钝击伤害时额外承受一次伤害骰",
            "黑暗视觉 60 尺",
        ),
        senses="黑暗视觉 60 尺 被动察觉 9",
        languages="理解通用语但不能说",
        desc="被死灵法术驱使的骸骨，忠实地执行最后的命令。",
        aliases=("骷髅兵", "骸骨兵"),
    ),
    Monster(
        key="zombie",
        cn="僵尸",
        en="Zombie",
        cr=0.25,
        size="中型",
        kind="亡灵",
        ac=8,
        hp=22,
        hit_dice="3d8+9",
        speed=20,
        abilities={"str": 13, "dex": 6, "con": 16, "int": 3, "wis": 6, "cha": 5},
        attacks=(MonsterAttack("挥击", "str", "1d6+1", "钝击"),),
        saves=("wis",),
        traits=(
            "亡灵坚韧：受到致死伤害时，体质豁免 DC5+已受伤害，成功则保留 1 点生命",
            "黑暗视觉 60 尺",
        ),
        senses="黑暗视觉 60 尺 被动察觉 8",
        languages="理解生前语言但不能说",
        desc="缓慢、耐久、不知疼痛，只会朝着活物挪过去。",
        aliases=("行尸", "丧尸"),
    ),
    Monster(
        key="boar",
        cn="野猪",
        en="Boar",
        cr=0.25,
        size="中型",
        kind="野兽",
        ac=11,
        hp=11,
        hit_dice="2d8+2",
        speed=40,
        abilities={"str": 13, "dex": 11, "con": 12, "int": 2, "wis": 9, "cha": 5},
        attacks=(MonsterAttack("獠牙", "str", "1d6+1", "穿刺"),),
        traits=(
            "冲锋：直线冲过 20 尺后獠牙额外造成 1d6 伤害，目标力量豁免 DC11 否则倒地",
            "不倒之躯：受到致死伤害时体质豁免 DC10，成功则保留 1 点生命",
        ),
        senses="被动察觉 9",
        desc="护崽与护食时会毫不犹豫地撞上来。",
        aliases=("野公猪",),
    ),
    # —— CR 1/2 ——
    Monster(
        key="hobgoblin",
        cn="大地精",
        en="Hobgoblin",
        cr=0.5,
        size="中型",
        kind="类人",
        ac=18,
        hp=11,
        hit_dice="2d8+2",
        speed=30,
        abilities={"str": 13, "dex": 12, "con": 12, "int": 10, "wis": 10, "cha": 9},
        attacks=(MonsterAttack("长剑", "str", "1d8+1", "挥砍"),),
        traits=(
            "军事优势：目标 5 尺内有盟友时额外造成 1d6 伤害",
            "黑暗视觉 60 尺",
            "严明的军纪与阵型",
        ),
        senses="黑暗视觉 60 尺 被动察觉 10",
        languages="通用语、地精语",
        desc="地精军团的军官，穿阵型作战，比同类更有纪律。",
    ),
    Monster(
        key="orc",
        cn="兽人",
        en="Orc",
        cr=0.5,
        size="中型",
        kind="类人",
        ac=13,
        hp=15,
        hit_dice="2d8+6",
        speed=30,
        abilities={"str": 16, "dex": 12, "con": 16, "int": 7, "wis": 11, "cha": 10},
        attacks=(
            MonsterAttack("巨斧", "str", "1d12+3", "挥砍"),
            MonsterAttack(
                "标枪",
                "str",
                "1d6+3",
                "穿刺",
                reach=0,
                ranged_normal=30,
                ranged_max=120,
            ),
        ),
        skills=("intimidation",),
        traits=("侵略性：附赠动作向可见的敌人移动至其速度", "黑暗视觉 60 尺"),
        senses="黑暗视觉 60 尺 被动察觉 10",
        languages="通用语、兽人语",
        desc="为战而生的掠夺者，崇尚力量与战利品。",
    ),
    Monster(
        key="gnoll",
        cn="豺狼人",
        en="Gnoll",
        cr=0.5,
        size="中型",
        kind="类人",
        ac=15,
        hp=22,
        hit_dice="5d8",
        speed=30,
        abilities={"str": 14, "dex": 12, "con": 11, "int": 6, "wis": 10, "cha": 7},
        attacks=(
            MonsterAttack("咬击", "str", "1d6+2", "穿刺"),
            MonsterAttack(
                "长矛", "str", "1d6+2", "穿刺", notes="可双手持握，伤害改为 1d8+2"
            ),
        ),
        traits=("狂暴冲锋：击倒敌人后附赠动作移动半速并再攻击一次", "黑暗视觉 60 尺"),
        senses="黑暗视觉 60 尺 被动察觉 10",
        languages="豺狼人语",
        desc="鬣狗般的类人，饥饿会压过一切理智。",
    ),
    # —— CR 1 ——
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
            MonsterAttack("咬击", "str", "2d6+1", "穿刺"),
            MonsterAttack(
                "爪击",
                "dex",
                "2d4+2",
                "挥砍",
                notes="命中则目标感知豁免 DC10，失败麻痹",
            ),
        ),
        traits=(
            "麻痹之爪：爪击命中的活物需感知豁免，失败麻痹 1 分钟（精灵免疫）",
            "黑暗视觉 60 尺",
        ),
        senses="黑暗视觉 60 尺 被动察觉 10",
        languages="通用语",
        desc="啃食尸体而生、行动如掠食者的亡灵，爪子令人僵直。",
        aliases=("食尸鬼群"),
    ),
    Monster(
        key="dire_wolf",
        cn="恐狼",
        en="Dire Wolf",
        cr=1,
        size="大型",
        kind="野兽",
        ac=14,
        hp=37,
        hit_dice="5d10+10",
        speed=50,
        abilities={"str": 17, "dex": 15, "con": 15, "int": 3, "wis": 12, "cha": 7},
        attacks=(
            MonsterAttack(
                "啃咬",
                "str",
                "2d6+3",
                "穿刺",
                notes="命中后目标力量豁免 DC13，失败倒地",
            ),
        ),
        skills=("perception", "stealth"),
        traits=(
            "集群战术：有同伴贴着目标时攻击有优势",
            "锐敏听觉与嗅觉",
            "黑暗视觉 60 尺",
        ),
        senses="被动察觉 13",
        desc="小马大小的巨狼，成群时敢于扑倒骑兵。",
        aliases=("凶暴狼", "巨狼"),
    ),
    Monster(
        key="harpy",
        cn="鹰身女妖",
        en="Harpy",
        cr=1,
        size="中型",
        kind="怪兽",
        ac=11,
        hp=38,
        hit_dice="7d8+7",
        speed=20,
        abilities={"str": 12, "dex": 13, "con": 12, "int": 7, "wis": 10, "cha": 13},
        attacks=(
            MonsterAttack("爪击", "dex", "2d4+1", "挥砍"),
            MonsterAttack("木棒", "str", "1d4+1", "钝击"),
        ),
        traits=(
            "迷魂之歌：听到歌声者感知豁免 DC11，失败被魅惑并朝她移动",
            "飞行 40 尺",
        ),
        senses="被动察觉 10",
        languages="通用语",
        desc="用歌声把旅人引向悬崖的半人半鸟怪物。",
        aliases=("哈比", "鹰身女妖群"),
    ),
    Monster(
        key="bugbear",
        cn="熊地精",
        en="Bugbear",
        cr=1,
        size="中型",
        kind="类人",
        ac=16,
        hp=27,
        hit_dice="5d8+5",
        speed=30,
        abilities={"str": 15, "dex": 14, "con": 13, "int": 8, "wis": 11, "cha": 9},
        attacks=(
            MonsterAttack("钉头锤", "str", "2d6+2", "钝击", reach=10),
            MonsterAttack(
                "标枪",
                "str",
                "1d6+2",
                "穿刺",
                reach=0,
                ranged_normal=30,
                ranged_max=120,
            ),
        ),
        skills=("stealth", "survival"),
        traits=(
            "偷袭：对未察觉自己的目标额外造成 2d6 伤害",
            "长臂：触及 10 尺",
            "黑暗视觉 60 尺",
        ),
        senses="黑暗视觉 60 尺 被动察觉 10",
        languages="通用语、地精语",
        desc="比地精高大得多的同族，天生的伏击者与绑匪。",
    ),
    # —— CR 2 ——
    Monster(
        key="ogre",
        cn="食人魔",
        en="Ogre",
        cr=2,
        size="大型",
        kind="巨人",
        ac=11,
        hp=59,
        hit_dice="7d10+21",
        speed=40,
        abilities={"str": 19, "dex": 8, "con": 16, "int": 5, "wis": 7, "cha": 7},
        attacks=(MonsterAttack("大棒", "str", "2d8+4", "钝击", reach=10),),
        traits=("黑暗视觉 60 尺", "贪食：会被食物、酒或财宝引开"),
        senses="黑暗视觉 60 尺 被动察觉 8",
        languages="通用语、巨人语",
        desc="力大无穷又懒得思考的掠夺者，一棒就能把人打飞。",
    ),
    Monster(
        key="bandit_captain",
        cn="匪帮首领",
        en="Bandit Captain",
        cr=2,
        size="中型",
        kind="类人",
        ac=15,
        hp=65,
        hit_dice="10d8+20",
        speed=30,
        abilities={"str": 15, "dex": 16, "con": 14, "int": 14, "wis": 11, "cha": 14},
        attacks=(
            MonsterAttack("弯刀", "dex", "1d6+3", "挥砍"),
            MonsterAttack(
                "匕首", "dex", "1d4+3", "穿刺", reach=0, ranged_normal=20, ranged_max=60
            ),
        ),
        saves=("str", "dex", "wis"),
        skills=("athletics", "deception"),
        traits=("多攻击：每回合用弯刀攻击两次", "格挡：单手持械时 AC +2"),
        senses="被动察觉 10",
        languages="通用语",
        desc="老练的亡命徒，手下听他的，钱包看他的。",
        aliases=("强盗头目", "匪首"),
    ),
    Monster(
        key="gargoyle",
        cn="石像鬼",
        en="Gargoyle",
        cr=2,
        size="中型",
        kind="元素",
        ac=15,
        hp=52,
        hit_dice="7d8+21",
        speed=30,
        abilities={"str": 15, "dex": 11, "con": 16, "int": 6, "wis": 11, "cha": 7},
        attacks=(
            MonsterAttack("咬击", "str", "1d6+2", "穿刺"),
            MonsterAttack("爪击", "str", "1d6+2", "挥砍"),
        ),
        traits=(
            "多攻击：每回合一次咬击 + 一次爪击",
            "石像形态：静止不动时与雕像无法分辨",
            "伤害抗性：非魔法武器",
            "飞行 60 尺",
        ),
        senses="黑暗视觉 60 尺 被动察觉 10",
        languages="通用语、土族语",
        desc="常被当成屋檐雕像，直到它展开翅膀。",
        aliases=("石像鬼群"),
    ),
    Monster(
        key="berserker",
        cn="狂战士",
        en="Berserker",
        cr=2,
        size="中型",
        kind="类人",
        ac=13,
        hp=67,
        hit_dice="9d8+27",
        speed=30,
        abilities={"str": 16, "dex": 12, "con": 17, "int": 9, "wis": 11, "cha": 9},
        attacks=(MonsterAttack("巨斧", "str", "1d12+3", "挥砍"),),
        traits=("鲁莽：本回合攻击有优势，但直到下回合被攻击也有优势",),
        senses="被动察觉 10",
        languages="通用语",
        desc="把疼痛当成燃料的战士，越见血越猛。",
        aliases=("野蛮人战士", "狂战士群"),
    ),
    # —— CR 3 ——
    Monster(
        key="mummy",
        cn="木乃伊",
        en="Mummy",
        cr=3,
        size="中型",
        kind="亡灵",
        ac=11,
        hp=58,
        hit_dice="9d8+18",
        speed=20,
        abilities={"str": 16, "dex": 8, "con": 15, "int": 6, "wis": 10, "cha": 12},
        attacks=(
            MonsterAttack(
                "腐烂之拳", "str", "2d6+3", "钝击", notes="附带腐尸热：目标无法恢复生命"
            ),
        ),
        saves=("wis",),
        traits=(
            "恐怖面容：看到它的活物感知豁免 DC11，失败恐慌",
            "腐尸热：被击中的目标无法恢复生命，直到被治疗疾病",
            "伤害抗性：非魔法武器",
            "黑暗视觉 60 尺",
        ),
        senses="黑暗视觉 60 尺 被动察觉 10",
        languages="生前语言",
        desc="被诅咒守护陵墓的尸身，缠满防腐布条。",
        aliases=("干尸",),
    ),
    Monster(
        key="owlbear",
        cn="枭熊",
        en="Owlbear",
        cr=3,
        size="大型",
        kind="怪兽",
        ac=13,
        hp=59,
        hit_dice="7d10+21",
        speed=40,
        abilities={"str": 20, "dex": 12, "con": 17, "int": 3, "wis": 12, "cha": 7},
        attacks=(
            MonsterAttack("咬击", "str", "1d10+5", "穿刺"),
            MonsterAttack("爪击", "str", "2d8+5", "挥砍"),
        ),
        skills=("perception",),
        traits=(
            "多攻击：每回合一次咬击 + 一次爪击",
            "锐敏视觉与嗅觉",
            "黑暗视觉 60 尺",
        ),
        senses="黑暗视觉 60 尺 被动察觉 13",
        desc="熊身枭首的怪物，护领地时从不退让。",
        aliases=("鸱鸮熊",),
    ),
    Monster(
        key="werewolf",
        cn="狼人",
        en="Werewolf",
        cr=3,
        size="中型",
        kind="类人",
        ac=12,
        hp=58,
        hit_dice="9d8+18",
        speed=30,
        abilities={"str": 15, "dex": 13, "con": 14, "int": 10, "wis": 11, "cha": 10},
        attacks=(
            MonsterAttack("咬击", "str", "1d8+2", "穿刺", notes="人形形态下无法咬击"),
            MonsterAttack("爪击", "str", "2d4+2", "挥砍"),
        ),
        skills=("perception", "stealth"),
        traits=(
            "形态变换：人形 / 狼人 / 巨狼三种形态切换",
            "伤害免疫：非魔法且非银制的武器",
            "兽性嗅觉：可追踪血腥味",
        ),
        senses="被动察觉 14",
        languages="通用语（人形时）",
        desc="受诅咒者，月夜与怒火都会让它现形。",
    ),
    Monster(
        key="knight",
        cn="骑士",
        en="Knight",
        cr=3,
        size="中型",
        kind="类人",
        ac=18,
        hp=52,
        hit_dice="8d8+16",
        speed=30,
        abilities={"str": 16, "dex": 11, "con": 14, "int": 11, "wis": 11, "cha": 15},
        attacks=(MonsterAttack("长剑", "str", "1d8+3", "挥砍"),),
        saves=("con", "wis"),
        traits=(
            "多攻击：每回合用长剑攻击两次",
            "勇气：免疫恐惧",
            "格挡：对被指定目标 AC +2",
        ),
        senses="被动察觉 10",
        languages="通用语",
        desc="受过训练的骑士，讲荣誉，也讲规矩。",
        aliases=("重装骑士",),
    ),
    # —— CR 4 ——
    Monster(
        key="ettin",
        cn="双头巨人",
        en="Ettin",
        cr=4,
        size="大型",
        kind="巨人",
        ac=12,
        hp=85,
        hit_dice="10d10+30",
        speed=40,
        abilities={"str": 18, "dex": 8, "con": 17, "int": 6, "wis": 10, "cha": 8},
        attacks=(MonsterAttack("大棒", "str", "2d8+4", "钝击", reach=10),),
        skills=("perception",),
        traits=(
            "双头：对目盲 / 魅惑 / 恐惧 / 昏迷的豁免有优势",
            "两个头会为了同一件事争吵",
            "黑暗视觉 60 尺",
        ),
        senses="黑暗视觉 60 尺 被动察觉 14",
        languages="巨人语、兽人语",
        desc="两个头共用一个身体，谁也说服不了谁。",
    ),
    Monster(
        key="ghost",
        cn="幽灵",
        en="Ghost",
        cr=4,
        size="中型",
        kind="亡灵",
        ac=11,
        hp=45,
        hit_dice="10d8",
        speed=40,
        abilities={"str": 7, "dex": 17, "con": 10, "int": 10, "wis": 12, "cha": 17},
        attacks=(MonsterAttack("枯萎之触", "dex", "4d6+3", "死灵"),),
        traits=(
            "虚体移动：可穿过生物与物件",
            "恐怖面容：看到它的活物感知豁免 DC13，失败恐慌",
            "附身：魅力豁免 DC13，失败被它控制",
            "飞行 40 尺",
        ),
        senses="黑暗视觉 60 尺 被动察觉 11",
        languages="生前语言",
        desc="执念未散的死者的影，穿过墙壁而来。",
        aliases=("鬼魂", "怨灵"),
    ),
    # —— CR 5 ——
    Monster(
        key="troll",
        cn="巨魔",
        en="Troll",
        cr=5,
        size="大型",
        kind="巨人",
        ac=15,
        hp=84,
        hit_dice="8d10+40",
        speed=30,
        abilities={"str": 18, "dex": 13, "con": 20, "int": 7, "wis": 9, "cha": 7},
        attacks=(
            MonsterAttack("咬击", "str", "1d6+4", "穿刺"),
            MonsterAttack("爪击", "str", "2d6+4", "挥砍"),
        ),
        skills=("perception",),
        traits=(
            "多攻击：每回合一次咬击 + 两次爪击",
            "再生：每回合恢复 10 点生命；受到酸或火焰伤害后该回合不生效",
            "黑暗视觉 60 尺",
        ),
        senses="黑暗视觉 60 尺 被动察觉 12",
        languages="巨人语",
        desc="砍断的肢体会重新长出来——除非用火烧。",
    ),
    Monster(
        key="fire_elemental",
        cn="火元素",
        en="Fire Elemental",
        cr=5,
        size="大型",
        kind="元素",
        ac=13,
        hp=102,
        hit_dice="12d10+36",
        speed=50,
        abilities={"str": 10, "dex": 17, "con": 16, "int": 6, "wis": 10, "cha": 7},
        attacks=(
            MonsterAttack(
                "触碰",
                "dex",
                "2d6+3",
                "火焰",
                notes="命中后目标着火：每回合开始受 1d10 火焰伤害",
            ),
        ),
        traits=(
            "火焰形态：可穿过狭窄缝隙，能点燃可燃物",
            "伤害抗性：非魔法武器",
            "伤害免疫：毒素、火焰",
            "被水克制：涌入水中时受到寒冷伤害",
        ),
        senses="黑暗视觉 60 尺 被动察觉 10",
        languages="火族语",
        desc="一团有意志的烈焰，所过之处只剩灰烬。",
        aliases=("火精灵",),
    ),
    Monster(
        key="vampire_spawn",
        cn="吸血鬼衍体",
        en="Vampire Spawn",
        cr=5,
        size="中型",
        kind="亡灵",
        ac=15,
        hp=82,
        hit_dice="11d8+33",
        speed=30,
        abilities={"str": 16, "dex": 16, "con": 16, "int": 11, "wis": 10, "cha": 12},
        attacks=(
            MonsterAttack("爪击", "str", "2d4+3", "挥砍"),
            MonsterAttack(
                "咬击",
                "str",
                "1d6+3",
                "穿刺",
                notes="命中且目标被擒抱时，生命上限降低等量数值",
            ),
        ),
        saves=("dex", "wis"),
        skills=("perception", "stealth"),
        traits=(
            "多攻击：每回合两次爪击，或一次咬击",
            "再生：每回合恢复 10 点生命；受阳光或圣水伤害后该回合不生效",
            "吸血鬼弱点：阳光、流水、圣徽",
            "黑暗视觉 60 尺",
        ),
        senses="黑暗视觉 60 尺 被动察觉 13",
        languages="生前语言",
        desc="被主人咬过的仆从，饥饿而服从。",
    ),
    # —— CR 6 ~ 10 ——
    Monster(
        key="young_white_dragon",
        cn="青年白龙",
        en="Young White Dragon",
        cr=6,
        size="大型",
        kind="龙",
        ac=17,
        hp=133,
        hit_dice="14d10+56",
        speed=40,
        abilities={"str": 18, "dex": 10, "con": 18, "int": 8, "wis": 11, "cha": 12},
        attacks=(
            MonsterAttack("咬击", "str", "2d10+4", "穿刺"),
            MonsterAttack("爪击", "str", "2d6+4", "挥砍"),
        ),
        saves=("dex", "con", "wis", "cha"),
        skills=("perception", "stealth"),
        traits=(
            "多攻击：每回合一次咬击 + 两次爪击",
            "寒冰吐息：30 尺锥形，敏捷豁免 DC15，10d8 寒冷伤害，成功减半（充能 5~6）",
            "伤害免疫：寒冷",
            "飞行 80 尺、游泳 40 尺、冰面行走如常",
        ),
        senses="盲视 60 尺 黑暗视觉 120 尺 被动察觉 13",
        languages="通用语、龙语",
        desc="刚离巢的冰龙，傲慢、贪婪，还很在意自己的藏品。",
        aliases=("白龙", "冰龙"),
    ),
    Monster(
        key="stone_giant",
        cn="石巨人",
        en="Stone Giant",
        cr=7,
        size="巨型",
        kind="巨人",
        ac=17,
        hp=126,
        hit_dice="11d12+55",
        speed=40,
        abilities={"str": 23, "dex": 15, "con": 20, "int": 10, "wis": 12, "cha": 9},
        attacks=(
            MonsterAttack("巨棒", "str", "3d8+6", "钝击", reach=15),
            MonsterAttack(
                "巨石",
                "str",
                "3d10+6",
                "钝击",
                reach=0,
                ranged_normal=60,
                ranged_max=240,
            ),
        ),
        saves=("dex", "con", "wis"),
        skills=("athletics", "perception"),
        traits=(
            "多攻击：每回合两次巨棒或两次巨石",
            "石之伪装：静止时与石像无异",
            "黑暗视觉 60 尺",
        ),
        senses="黑暗视觉 60 尺 被动察觉 14",
        languages="巨人语",
        desc="住在岩洞里的巨人，投石比攻城器还准。",
    ),
    Monster(
        key="mind_flayer",
        cn="夺心魔",
        en="Mind Flayer",
        cr=7,
        size="中型",
        kind="异怪",
        ac=15,
        hp=71,
        hit_dice="13d8+13",
        speed=30,
        abilities={"str": 11, "dex": 12, "con": 12, "int": 19, "wis": 17, "cha": 17},
        attacks=(
            MonsterAttack(
                "触手",
                "int",
                "2d10+4",
                "心灵",
                notes="命中则目标智力豁免 DC15，失败被擒抱",
            ),
        ),
        saves=("int", "wis", "cha"),
        skills=("arcana", "deception", "insight", "perception", "stealth"),
        traits=(
            "魔法抗性：对抗法术与魔法效果的豁免有优势",
            "心灵震爆：60 尺锥形，智力豁免 DC15，4d8+4 心灵伤害并昏迷 1 分钟",
            "黑暗视觉 120 尺",
        ),
        senses="黑暗视觉 120 尺 被动察觉 16",
        languages="地底通用语、心灵沟通",
        desc="以他人心智为食的触须异怪，最擅长让你怀疑自己。",
        aliases=("灵吸怪", "吸脑怪"),
    ),
    Monster(
        key="frost_giant",
        cn="霜巨人",
        en="Frost Giant",
        cr=8,
        size="巨型",
        kind="巨人",
        ac=15,
        hp=138,
        hit_dice="12d12+60",
        speed=40,
        abilities={"str": 23, "dex": 9, "con": 21, "int": 9, "wis": 10, "cha": 12},
        attacks=(
            MonsterAttack("巨斧", "str", "3d12+6", "挥砍", reach=10),
            MonsterAttack(
                "岩石",
                "str",
                "4d6+6",
                "钝击",
                reach=0,
                ranged_normal=60,
                ranged_max=240,
            ),
        ),
        saves=("con", "wis", "cha"),
        skills=("athletics", "perception"),
        traits=(
            "多攻击：每回合两次巨斧",
            "伤害免疫：寒冷",
            "寒地行走如常",
            "黑暗视觉 60 尺",
        ),
        senses="黑暗视觉 60 尺 被动察觉 13",
        languages="巨人语",
        desc="冰原上的掠袭者，把弱小者当作物产。",
    ),
    Monster(
        key="young_blue_dragon",
        cn="青年蓝龙",
        en="Young Blue Dragon",
        cr=9,
        size="大型",
        kind="龙",
        ac=18,
        hp=152,
        hit_dice="16d10+80",
        speed=40,
        abilities={"str": 21, "dex": 10, "con": 19, "int": 14, "wis": 13, "cha": 17},
        attacks=(
            MonsterAttack("咬击", "str", "2d10+5", "穿刺"),
            MonsterAttack("爪击", "str", "2d6+5", "挥砍"),
        ),
        saves=("dex", "con", "wis", "cha"),
        skills=("perception", "stealth"),
        traits=(
            "多攻击：每回合一次咬击 + 两次爪击",
            "闪电吐息：90 尺线形，敏捷豁免 DC17，16d6 闪电伤害，成功减半（充能 5~6）",
            "伤害免疫：闪电",
            "飞行 80 尺、掘地 20 尺",
        ),
        senses="盲视 60 尺 黑暗视觉 120 尺 被动察觉 17",
        languages="通用语、龙语",
        desc="沙丘下的蓝色暴君，喜欢用闪电与谈话同时折磨来客。",
        aliases=("蓝龙",),
    ),
    Monster(
        key="aboleth",
        cn="深渊鳐",
        en="Aboleth",
        cr=10,
        size="大型",
        kind="异怪",
        ac=17,
        hp=135,
        hit_dice="18d10+36",
        speed=10,
        abilities={"str": 21, "dex": 9, "con": 15, "int": 18, "wis": 15, "cha": 18},
        attacks=(
            MonsterAttack(
                "触手",
                "str",
                "3d6+5",
                "钝击",
                notes="命中则目标体质豁免 DC14，失败染上黏液病",
            ),
        ),
        saves=("con", "int", "wis"),
        skills=("history", "perception"),
        traits=(
            "多攻击：每回合三次触手",
            "心灵控制：60 尺内可见的活物感知豁免 DC14，失败被它支配 1 分钟",
            "黏滑之皮：无法被擒抱，除非目标通过力量豁免 DC16",
            "两栖：水下呼吸，游泳 40 尺",
            "黑暗视觉 120 尺",
        ),
        senses="黑暗视觉 120 尺 被动察觉 20",
        languages="地底通用语、心灵沟通",
        desc="比大多数文明更古老的深海记忆体，把整片水域当成自己的手。",
        aliases=("底栖魔鱼", "魔鱼"),
    ),
)


#: 键 / 中文名 / 英文名 / 别名 -> 怪物
MONSTER_MAP: dict[str, Monster] = {}
for _monster in MONSTERS:
    MONSTER_MAP[_monster.key] = _monster
    MONSTER_MAP[_monster.cn] = _monster
    MONSTER_MAP[_monster.en.lower()] = _monster
    for _alias in _monster.aliases:
        MONSTER_MAP.setdefault(_alias, _monster)


def get_monster(value: Monster | str) -> Monster | None:
    """按对象 / 键 / 中文名 / 别名取怪物，取不到返回 ``None``。"""
    if isinstance(value, Monster):
        return value
    text = str(value or "").strip()
    if not text:
        return None
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
