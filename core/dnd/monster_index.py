"""按《怪物图鉴》的**四套分类**查怪物：挑战等级 / 生物类型 / 栖息地 / 群组。

图鉴的附录就是这四张表，DM 挑怪的口径也该是这四张——自己另起一套分类必然
和书对不上（「这类算不算那类」只能由书说了算），所以这里**不发明分类**，
只把书上这四套做成可查、可随机抽的索引：

============  ============================================  ==================
分类           取值                                           挑怪时的用处
============  ============================================  ==================
挑战等级 cr    0 / 1/8 / 1/4 / 1/2 / 1 … 30                  按队伍水平挑
生物类型 kind  异怪 / 野兽 / 天族 / 构装 / 龙类 / 元素 /      「来几只亡灵」
              妖精 / 邪魔 / 巨人 / 类人 / 怪兽 / 泥怪 /
              植物 / 亡灵（14 类，照书）
栖息地 env     森林 / 幽暗地域 / 城市 / 水下 / 位面 …        按当前场景挑
群组 group     色彩龙 / 金属龙 / 类地精 / 恶魔 / 恐龙 /       「来一窝地精」
              兽化人 / 巨灵 / 眼魔 / 天使 / 泰坦 …
============  ============================================  ==================

**索引是派生的**，不是另一份抄本：四类信息本来就在每只怪的数据卡里
（``cr`` / ``kind`` / ``environment`` / ``groups``），这里只做「按哪一维切开」。
加怪不用改这张表——往 ``res/dnd/monsters/`` 丢 yaml 就行。

两处口径要记牢：

- **栖息地**：图鉴写「位面, 无底深渊」是两个标签（位面 + 具体位面），
  写「森林、草地、丘陵」是三个；标 ``任何`` 的哪儿都可能出现，挑某个栖息地时
  **任何**也算命中。没写栖息地的（多半是模组自带的）不进任何栖息地。
- **群组**：一只怪可以属于好几个（成年黑龙既是「龙」也是「色彩龙」），
  所以按群组取出来的是有重叠的几堆。

本模块只做查询与随机，不碰存档、不调模型。强度校准（按队伍等级缩放到目标 CR）
在 :mod:`.encounter`，不在随机这里——先挑得出，再谈强弱。
"""

from __future__ import annotations

import random
import re
from collections.abc import Iterable, Sequence

from .monsters import MONSTERS, Monster

__all__ = [
    "ANY_HABITAT",
    "CR_BANDS",
    "GROUPS",
    "KINDS",
    "band_of",
    "by_band",
    "by_cr",
    "by_group",
    "by_habitat",
    "by_kind",
    "groups",
    "habitats",
    "index_text",
    "kinds",
    "parse_query",
    "random_monster",
    "select",
]


#: 图鉴附录「按类型」的 14 类（顺序照书：英文原版按字母序排列）
KINDS: tuple[str, ...] = (
    "异怪",
    "野兽",
    "天族",
    "构装",
    "龙类",
    "元素",
    "妖精",
    "邪魔",
    "巨人",
    "类人",
    "怪兽",
    "泥怪",
    "植物",
    "亡灵",
)

#: 图鉴附录「按挑战等级」的档位（一只怪只落一档）
CR_BANDS: tuple[tuple[str, float, float], ...] = (
    ("CR 0", 0.0, 0.0),
    ("CR 1/8–1/2", 0.125, 0.5),
    ("CR 1–4", 1.0, 4.0),
    ("CR 5–8", 5.0, 8.0),
    ("CR 9–12", 9.0, 12.0),
    ("CR 13–16", 13.0, 16.0),
    ("CR 17–20", 17.0, 20.0),
    ("CR 21+", 21.0, 99.0),
)

#: 图鉴里的「哪儿都有」
ANY_HABITAT = "任何"

#: 没标类型的怪物归到这里（图鉴里都有类型，模组自带的少数几条没有）
UNCATEGORIZED = "未分类"

_SPLIT_HABITAT = re.compile(r"[,、，]")
#: 随机查询的前缀：``随机 野兽 cr 2`` / ``随机 幽暗地域 亡灵``
_RANDOM_PREFIX = re.compile(r"^随机\s*")
#: 数量：``3只`` / ``类地精x3`` / ``类地精×3``（名字要留给分类用）
_COUNT = re.compile(r"^(?:(?P<head>\d+)\s*只|(?P<name>.+?)\s*[x×*]\s*(?P<tail>\d+))$")
#: 挑战等级：``cr 5`` / ``cr2`` / ``cr 1/4``
_CR = re.compile(r"^(?:cr\s*)?(?P<num>\d+)(?:\s*/\s*(?P<den>\d+))?$")


# --------------------------------------------------------------------------- #
# 四张索引
# --------------------------------------------------------------------------- #


def habitats_of(monster: Monster) -> tuple[str, ...]:
    """一只怪的栖息地标签（``"位面, 无底深渊"`` → ``("位面", "无底深渊")``）。"""
    text = str(getattr(monster, "environment", "") or "")
    if not text:
        return ()
    return tuple(part.strip() for part in _SPLIT_HABITAT.split(text) if part.strip())


def _index(key_of) -> dict[str, tuple[Monster, ...]]:
    """按取值函数把总表切开；一只怪可以落好几个键（栖息地与群组都会）。"""
    out: dict[str, list[Monster]] = {}
    for monster in MONSTERS:
        for key in key_of(monster):
            out.setdefault(key, []).append(monster)
    return {
        key: tuple(sorted(value, key=lambda m: (m.cr, m.cn)))
        for key, value in out.items()
    }


#: 生物类型 → 该类全部（按 CR、名字排）
KIND_INDEX: dict[str, tuple[Monster, ...]] = _index(
    lambda m: (str(m.kind) or UNCATEGORIZED,)
)
#: 栖息地 → 该地能碰上的全部
HABITAT_INDEX: dict[str, tuple[Monster, ...]] = _index(habitats_of)
#: 群组 → 该族全部
GROUP_INDEX: dict[str, tuple[Monster, ...]] = _index(lambda m: tuple(m.groups))

#: 群组名（按「该族有多少只」降序）
GROUPS: tuple[str, ...] = tuple(
    sorted(GROUP_INDEX, key=lambda key: (-len(GROUP_INDEX[key]), key))
)


def kinds() -> tuple[str, ...]:
    """图鉴 14 类里**实际有怪**的那些（外加「未分类」兜底），按书的顺序。"""
    known = [kind for kind in KINDS if kind in KIND_INDEX]
    if UNCATEGORIZED in KIND_INDEX:
        known.append(UNCATEGORIZED)
    return tuple(known)


def habitats() -> tuple[str, ...]:
    """全部栖息地，按「该地有多少只」降序（挑怪时更常用的是大的那几个）。"""
    return tuple(sorted(HABITAT_INDEX, key=lambda key: (-len(HABITAT_INDEX[key]), key)))


def groups() -> tuple[str, ...]:
    """全部群组，按「该族有多少只」降序。"""
    return GROUPS


def band_of(cr: float) -> str:
    """某个挑战等级属于哪一档（``3`` → ``CR 1–4``）。"""
    for label, low, high in CR_BANDS:
        if low <= float(cr) <= high:
            return label
    return CR_BANDS[-1][0]


def by_kind(kind: str) -> tuple[Monster, ...]:
    """按生物类型取（``"野兽"``）；图鉴里没这一类返回空。"""
    return KIND_INDEX.get(str(kind or "").strip(), ())


def by_habitat(habitat: str) -> tuple[Monster, ...]:
    """按栖息地取（``"森林"``）；标了「任何」的怪哪儿都算。"""
    text = str(habitat or "").strip()
    if not text:
        return ()
    hits = {m.key: m for m in HABITAT_INDEX.get(text, ())}
    for monster in HABITAT_INDEX.get(ANY_HABITAT, ()):
        hits.setdefault(monster.key, monster)
    return tuple(sorted(hits.values(), key=lambda m: (m.cr, m.cn)))


def by_group(group: str) -> tuple[Monster, ...]:
    """按群组取（``"类地精"`` / ``"色彩龙"``）。"""
    return GROUP_INDEX.get(str(group or "").strip(), ())


def by_cr(cr: float) -> tuple[Monster, ...]:
    """按挑战等级取（精确相等，``0.5`` = CR 1/2）。"""
    return tuple(m for m in MONSTERS if abs(m.cr - float(cr)) < 1e-6)


def by_band(label: str) -> tuple[Monster, ...]:
    """按 CR 档取（``"CR 1–4"``，档名见 :data:`CR_BANDS`）。"""
    for name, low, high in CR_BANDS:
        if name == str(label).strip():
            return tuple(m for m in MONSTERS if low <= m.cr <= high)
    return ()


# --------------------------------------------------------------------------- #
# 组合筛选与随机
# --------------------------------------------------------------------------- #


def select(
    *,
    kind: str = "",
    habitat: str = "",
    group: str = "",
    cr: float | None = None,
    cr_min: float | None = None,
    cr_max: float | None = None,
    pool: Iterable[Monster] | None = None,
) -> tuple[Monster, ...]:
    """四套分类任意组合求交集，按 CR 升序返回。

    哪个都不给就是整张表。``cr`` 是精确值，``cr_min`` / ``cr_max`` 是**闭区间**
    （挑「打得过的」常用 ``cr_max``）。栖息地那套会把「任何」算进来。
    """
    chosen: list[Monster] = list(MONSTERS if pool is None else pool)

    # Monster 是 dataclass（不可哈希），比对一律走 key
    text = str(kind or "").strip()
    if text:
        allowed = {m.key for m in by_kind(text)}
        chosen = [m for m in chosen if m.key in allowed]

    text = str(habitat or "").strip()
    if text:
        allowed = {m.key for m in by_habitat(text)}
        chosen = [m for m in chosen if m.key in allowed]

    text = str(group or "").strip()
    if text:
        allowed = {m.key for m in by_group(text)}
        chosen = [m for m in chosen if m.key in allowed]

    if cr is not None:
        chosen = [m for m in chosen if abs(m.cr - float(cr)) < 1e-6]
    if cr_min is not None:
        chosen = [m for m in chosen if m.cr >= float(cr_min)]
    if cr_max is not None:
        chosen = [m for m in chosen if m.cr <= float(cr_max)]

    return tuple(sorted(chosen, key=lambda m: (m.cr, m.cn)))


def random_monster(
    *,
    kind: str = "",
    habitat: str = "",
    group: str = "",
    cr: float | None = None,
    cr_min: float | None = None,
    cr_max: float | None = None,
    rng: random.Random | None = None,
) -> Monster | None:
    """按条件**随机抽一只**（纯随机，不做强度校准——校准是 :mod:`.encounter` 的事）。

    抽不到（条件太窄）返回 ``None``，让调用方自己放宽：宁可空手，也别塞一只
    不对的怪进去（比如把海洋生物丢进荒漠）。
    """
    pool = select(
        kind=kind,
        habitat=habitat,
        group=group,
        cr=cr,
        cr_min=cr_min,
        cr_max=cr_max,
    )
    if not pool:
        return None
    return (rng or random.Random()).choice(pool)


# --------------------------------------------------------------------------- #
# 给 DM 看的文本
# --------------------------------------------------------------------------- #


def _cr_text(cr: float) -> str:
    if cr >= 1:
        return str(int(cr)) if float(cr).is_integer() else f"{cr:g}"
    return {0.125: "1/8", 0.25: "1/4", 0.5: "1/2"}.get(cr, f"{cr:g}")


def _lines_of(title: str, group: Sequence[Monster], limit: int) -> list[str]:
    """按 CR 分档列名单：一档一行，省得 DM 在几百只里翻。"""
    lines = [f"{title}共 {len(group)} 只："]
    if not group:
        lines.append("（这一类一只都没有，换个条件试试）")
        return lines
    for label, _low, _high in CR_BANDS:
        band = [m for m in group if band_of(m.cr) == label]
        if not band:
            continue
        names = "、".join(m.cn for m in band[:limit])
        tail = f" 等 {len(band)} 只" if len(band) > limit else ""
        lines.append(f"· {label}：{names}{tail}")
    lines.append("（写全名查数据卡，或写「随机 …」让系统掷一只）")
    return lines


def parse_query(text: str) -> dict[str, object]:
    """把 DM 写的一句话拆成筛选条件：``类地精 幽暗地域 cr 2``。

    认得：生物类型（14 类）、栖息地、群组、``cr N``；``N只`` 记数量。
    认不出的词留在 ``rest`` 里——交给调用方报「查不到」。

    开战时写「随机 …」也走这里（见 :func:`~.combat.parse_enemy_list`）。
    """
    cond: dict[str, object] = {"count": 1, "rest": []}
    rest: list[str] = []
    for token in str(text or "").split():
        token = token.strip()
        if not token:
            continue
        if token in KIND_INDEX:
            cond["kind"] = token
            continue
        if token in HABITAT_INDEX:
            cond["habitat"] = token
            continue
        if token in GROUP_INDEX:
            cond["group"] = token
            continue
        count = _COUNT.match(token)
        if count and (count.group("head") or count.group("tail")):
            cond["count"] = int(count.group("head") or count.group("tail"))
            if count.group("name"):
                # 「类地精x3」：数量后面的名字还得按分类走一遍
                for inner in str(count.group("name")).split():
                    if inner in KIND_INDEX:
                        cond["kind"] = inner
                    elif inner in HABITAT_INDEX:
                        cond["habitat"] = inner
                    elif inner in GROUP_INDEX:
                        cond["group"] = inner
                    else:
                        rest.append(inner)
            continue
        cr = _CR.match(token.lower())
        if cr:
            number = float(cr.group("num"))
            if cr.group("den"):
                number /= float(cr.group("den"))
            # 带 cr 的是「这一级的」，光写数字多半也是要这个等级
            cond["cr"] = number
            continue
        rest.append(token)
    cond["rest"] = rest
    return cond


def index_text(
    value: str = "", *, rng: random.Random | None = None, limit: int = 10
) -> str:
    """``query_data(table=monster)`` 的分类侧输出。

    - 留空：四套分类的概览；
    - **类型 / 栖息地 / 群组 / cr**：按 CR 分档列名单（可组合，如「森林 野兽 cr 2」）；
    - **``随机 …``**：系统掷一只，直接出数据卡（拿了就能开战）。
    """
    text = str(value or "").strip()
    if not text:
        kind_bits = "、".join(f"{k} {len(v)}" for k, v in KIND_INDEX.items())
        top_h = [h for h in habitats() if h != ANY_HABITAT][:14]
        hab_bits = "、".join(f"{h} {len(HABITAT_INDEX[h])}" for h in top_h)
        group_bits = "、".join(f"{g} {len(GROUP_INDEX[g])}" for g in GROUPS[:16])
        band_bits = "、".join(
            f"{label} {len(by_band(label))}" for label, _lo, _hi in CR_BANDS
        )
        return "\n".join(
            (
                f"怪物表 {len(MONSTERS)} 只，按《怪物图鉴》的四套分类查：",
                f"· 生物类型（{len(KIND_INDEX)} 类）：{kind_bits}",
                f"· 栖息地（{len(HABITAT_INDEX)} 处）：{hab_bits}"
                f"（「{ANY_HABITAT}」{len(HABITAT_INDEX.get(ANY_HABITAT, ()))} 只哪儿都有）",
                f"· 群组（{len(GROUP_INDEX)} 族）：{group_bits}",
                f"· 挑战等级：{band_bits}",
                "",
                "挑怪：写类型 / 栖息地 / 群组 / cr 5，可组合（如「森林 野兽 cr 2」）；",
                "要随机就写「随机 幽暗地域 亡灵」，系统掷一只并给出数据卡。",
                "（随机只负责挑得出，强度按队伍等级校准是开战时自动做的。）",
            )
        )

    wanted_random = bool(_RANDOM_PREFIX.match(text))
    body = _RANDOM_PREFIX.sub("", text).strip()
    cond = parse_query(body)
    unknown = [str(item) for item in (cond.get("rest") or [])]
    if unknown and not wanted_random:
        raise KeyError(f"看不懂：{' '.join(unknown)}（能写类型 / 栖息地 / 群组 / cr N）")

    pool = select(
        kind=str(cond.get("kind", "")),
        habitat=str(cond.get("habitat", "")),
        group=str(cond.get("group", "")),
        cr=cond.get("cr"),  # type: ignore[arg-type]
    )
    if wanted_random:
        if not pool:
            return "按这个条件一只都挑不出来，放宽点再试（写类型 / 栖息地 / 群组 / cr）。"
        monster = (rng or random.Random()).choice(pool)
        scope = "／".join(
            part
            for part in (
                str(cond.get("kind", "")),
                str(cond.get("group", "")),
                str(cond.get("habitat", "")),
                f"CR{_cr_text(monster.cr)}",
            )
            if part
        )
        return f"🎲 随机抽到（{scope}）：\n{monster.stat_block()}"

    title_bits = [str(cond[k]) for k in ("kind", "group", "habitat") if cond.get(k)]
    if cond.get("cr") is not None:
        title_bits.append(f"CR{_cr_text(float(cond['cr']))}")  # type: ignore[arg-type]
    title = "／".join(title_bits) or "全部"
    return "\n".join(_lines_of(f"{title}：", pool, limit))
