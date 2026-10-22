"""跑团 Agent：**区域（省）**生成（province）。

五层里的第 3 层：**世界 → 王国/地区 → 区域（省）→ 地点 → 场景**。

它装的是「这片地方本身长什么样」：地形、气候、水源、交通、人口、产物、本地风险。
政治（谁说了算、法度、军力、外交）属于上一层 :mod:`.gen_realm`，这里一句都不展开。

它同时产出**地点索引**：末尾「地点」一节列出辖区内有哪些据点 / 野外地点 / 地下城，
每个一行。这份索引是 :func:`~...agents.dnd.on_demand.ensure_scene` 的关键——
玩家说要去「旧盐井」时，程序得知道它在哪个省、是什么类型，才能决定拉哪个生成器。

**地点索引只有名字与类型，没有细节**：细节等玩家真的要去那儿时由
`city` / `place` / `dungeon` 三个 agent 之一写。这样「一个省」只要一次调用，
而不是把辖区里十个地点全展开——那正是旧「地区文档」又长又难改的原因。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ...dnd.travel import DEFAULT_CROSSING, MAX_CROSSING
from ..base import AgentInput, AgentResult, TrpgAgent

__all__ = [
    "KIND_ALIASES",
    "ProvinceAgent",
    "ProvinceResult",
    "parse_crossing",
    "parse_province",
    "province_agent",
]

#: 小标题里写的类型 / 括号里写的类型 -> 地点种类。
#:
#: **第 4 层是三个种类，不是「一个种类 + 子类型」**：据点落 ``city``、
#: 野外地点落 ``place``、地下城落 ``dungeon``，三者各有生成器与目录。
#: 这张表只负责把中文写法认出来。
KIND_ALIASES: dict[str, str] = {
    "city": "city",
    "settlement": "city",
    "据点": "city",
    "城镇": "city",
    "城市": "city",
    "村落": "city",
    "营地": "city",
    "place": "place",
    "wild": "place",
    "野外": "place",
    "野外地点": "place",
    "自然地点": "place",
    "地形": "place",
    "dungeon": "dungeon",
    "地下城": "dungeon",
    "遗迹": "dungeon",
    "洞窟": "dungeon",
}

_SYSTEM_PROMPT = """# 任务
为指定区域（省）生成一份**地理向**文档：这片地方本身长什么样，以及它辖下有哪些地点。

# 输入
- 本区域信息：王国文档里这一节给的「大致位置 / 地形底色 / 主要据点 / 这地方出的麻烦」。
- 所属王国：这个省归谁管、什么政体、法度与军力如何（用于让人文与风险对得上）。
- 世界上下文：世界名称与一句话简介。

# 输出
一份 Markdown 文档，**五节**，不要多也不要少。

# 输出模板
```markdown
# <区域名称>

## 一、区域概览
- 区域名称：
- 所属王国：
- 大致位置：<相对都城的方位与距离感>
- 面积感：<走一趟要几天 / 半天就穿过>
- 一句话定位：<这地方为什么值得来>

## 二、地形与气候
- 地形构成：
- 海拔与地势：
- 气候：
- 季节变化：
- 水源分布：
- 跨越难度：<1-5 的数字>｜<一句理由：什么地形挡路、要什么条件才能过>

## 三、人文与产出
- 人口密度：
- 主要生计：<种什么、挖什么、做什么买卖>
- 特产：
- 集市与换货点：
- 通行规矩：<进这片地方要过什么关、给谁交钱>
- 外来者待遇：

## 四、风险
- 自然环境的风险：
- 人的风险：<盗匪、军兵、收税、抓丁>
- 魔物与异象：
- 本地人的忌讳：
- 最可能把玩家卷进去的事：<一句话，具体到场面>

## 五、地点
### 地点：<名称>（据点）
- 一句话：<这是什么、为什么玩家会去>
（据点 / 野外地点 / 地下城各有若干，见下）

### 地点：<名称>（野外地点）
- 一句话：

### 地点：<名称>（地下城）
- 一句话：
```

# 生成要求
- **括号里的类型只能是** `据点` / `野外地点` / `地下城` 三者之一，必须写。
- 地点给 **3–6 个**，覆盖不同用途（一个能补给的地方、一处能探索的野外、一个值得下去的地下城）。
- 地点只给「一句话」，**不要写房间、不要写怪物配置、不要写 NPC 细节**——那是下一层的事。
- 地点名沿用输入与世界圣经里已有的名字；确需新名字时只新增必要的那几个。
- 「最可能把玩家卷进去的事」要具体到一个场面（「夜里有人往盐垛里掺水」），
  不要写「这片区域很危险」。
- 「跨越难度」写 **1–5 的数字**再加一句理由：1 通衢（半天）/ 2 好走（一天）/
  3 难行（两天）/ 4 险阻（四天）/ 5 几乎不可越（八天起，或要向导 / 季节 / 买路）。
  这个数字决定玩家跨过这片地方要多久，**必须与上面的地形描述一致**——
  写「一马平川」却给 5，文档就是自相矛盾的。
- 不新增输入里没有的王国；与上层法度、军力、外交保持一致（比如重税法度的省，通行规矩要收钱）。
- 语言简洁，每条一行。"""


#: ``### 地点：<名>（据点）`` / ``### 据点：<名>``（兼容旧写法）
_SITE_RE = re.compile(
    r"^#{2,4}\s*(?:地点|据点|野外地点|自然地点|地下城|遗迹)"
    r"[^\n：:（(]{0,6}[：:]\s*(?P<name>[^（(\n]+?)"
    r"\s*(?:[（(]\s*(?P<type>[^）)\n]{1,8})\s*[）)])?\s*$",
    re.MULTILINE,
)

#: 小标题本身带类型时（``### 据点：盐仓码头``），从标题里取
_HEADING_KIND_RE = re.compile(
    r"^#{2,4}\s*(?P<kind>地点|据点|野外地点|自然地点|地下城|遗迹)", re.MULTILINE
)


def _heading_kind(line: str) -> str:
    """从 ``### 据点：xxx`` 这种小标题里取种类；认不出返回空串。"""
    match = _HEADING_KIND_RE.match(line.strip())
    if not match:
        return ""
    return KIND_ALIASES.get(match.group("kind").strip(), "")


_CROSSING_RE = re.compile(r"跨越难度[：:]\s*([1-5])")


def parse_crossing(text: str) -> int:
    """从区域文档里取**跨越难度**（1–5）；没写或写坏按「好走」算。

    这个数字决定玩家跨过这片地方要多久（见 :mod:`~...dnd.travel`）。
    程序只做两件事：认数字、钳范围。**不从地形描述反推**——
    「裂谷」可以是 3 也可以是 5，取决于有没有栈道、有没有人维护，那需要常识。
    """
    match = _CROSSING_RE.search(text or "")
    if not match:
        return DEFAULT_CROSSING
    return max(1, min(int(match.group(1)), MAX_CROSSING))


def parse_province(text: str) -> list[tuple[str, str, str]]:
    """解析区域文档的「地点」一节，返回 ``[(名字, 种类, 一句话)]``。

    种类归一成 ``city`` / ``place`` / ``dungeon``；
    括号里没写、标题里也没写时按 ``place``（野外地点）算——
    宁可当成一处野外，也不要凭空造一个有人住的据点。
    """
    lines = (text or "").splitlines()
    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for index, line in enumerate(lines):
        match = _SITE_RE.match(line)
        if not match:
            continue
        name = match.group("name").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        raw_type = (match.group("type") or "").strip() or _heading_kind(line)
        kind = KIND_ALIASES.get(raw_type, "place")
        note = ""
        for follow in lines[index + 1 : index + 4]:
            body = follow.strip()
            if body.startswith("-") and "：" in body:
                note = body.split("：", 1)[1].strip()
                break
            if body.startswith("#"):
                break
        out.append((name, kind, note))
    return out


@dataclass(slots=True)
class ProvinceResult(AgentResult):
    """区域生成结果：模型原文 + 解析出的地点索引。"""

    #: ``[(地点名, 种类, 一句话)]``
    sites: list[tuple[str, str, str]] = field(default_factory=list)


class ProvinceAgent(TrpgAgent):
    """区域生成：地形气候、人文产出、风险，外加辖区内的地点索引。"""

    name = "province"
    title = "区域生成"
    description = "生成一个省的地形气候 / 人文产出 / 风险，并列出辖区内的地点索引"
    system_prompt = _SYSTEM_PROMPT
    inputs = (
        AgentInput("province", "本区域信息", description="王国文档里这一节的设定"),
        AgentInput(
            "realm",
            "所属王国",
            required=False,
            description="王国文档（政体 / 法度 / 军力 / 外交），用于人文与风险对得上",
        ),
        AgentInput(
            "inhabited",
            "所属王国的人口状况",
            required=False,
            description="只有无人区才传：这片地方没人管事，不该出据点",
        ),
        AgentInput(
            "world",
            "世界上下文",
            required=False,
            description="世界名称 / 一句话简介，供保持一致，可留空",
        ),
        AgentInput(
            "bible",
            "世界圣经",
            required=False,
            description="世界专有名词与事实的登记；命名与事实以它为准，可留空",
        ),
    )

    async def run(self, **values: object) -> ProvinceResult:
        result = await super().run(**values)
        return ProvinceResult(
            agent=result.agent,
            title=result.title,
            content=result.content,
            model_type=result.model_type,
            usage=result.usage,
            usages=list(result.usages),
            sites=parse_province(result.content),
        )


#: 全局默认实例
province_agent = ProvinceAgent()
