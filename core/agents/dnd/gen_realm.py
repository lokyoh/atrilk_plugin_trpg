"""跑团 Agent：**王国/地区**生成（realm）。

这是五层里的第 2 层：**世界 → 王国/地区 → 区域（省）→ 地点 → 场景**。

与它取代的旧「地区文档」(`gen_area`) 的差别是**只管政治，不管地形**：

==============  ==========================================  ==================================
这里            装什么                                        不装什么（那是 :mod:`.gen_province`）
==============  ==========================================  ==================================
``realm``       谁说了算、凭什么、法度、军力、外交、当前局势      地形、气候、水源、人口、产物
==============  ==========================================  ==================================

为什么非拆不可：旧文档把「当前政治局势」「国都」「特殊地区」和「详细地形分布」「地形地貌生态」
塞在同一篇里。后果是**世界自己变动时改不动**——世界演化轨推进的其实是这一层的势力，
但势力换位置时，那一篇里同时写着的「海拔与地势」「植被类型」一个字都不用改，
却和它绑在一起。拆开之后，realm 会随局势重写，province 基本不动。

本 agent 还产出**区域（省）清单**：realm 文档末尾的「区域划分」一节列出它辖下的省。
这一次列名很关键——它让「世界 → 王国 → 省」这条链条从第一次生成就是完整的，
而不是等到玩家走到某处才现编一个省名。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..base import AgentInput, AgentResult, TrpgAgent

__all__ = ["RealmAgent", "RealmResult", "parse_realm", "realm_agent"]


_SYSTEM_PROMPT = """# 任务
为指定王国/地区生成一份**政治向**文档：谁说了算、凭什么、怎么管、跟谁有账。

# 输入
- 本地区信息：世界地图里该地区的形状、主要地形、标志地标、国都与特殊地区、人文特点、政治局势、与周边的分隔方式。
- 周边地区信息：相邻的王国的名称与它们的大致情况。
- 世界上下文：世界名称与一句话简介（定基调用）。

# 输出
一份 Markdown 文档，**六节**，不要多也不要少。

# 输出模板
```markdown
# <王国/地区名称>

## 一、政体
- 国名或通称：
- 政体：<君主制 / 寡头 / 教权 / 商会共和 / 部族联盟……>
- 统治者：<称号 + 名字，或「空位」>
- 权力来源：<血统 / 神授 / 盐票 / 武力 / 钱>
- 继承或更替方式：
- 都城：
- 一句话性格：<这个地方办事的方式，如「一切都要过账」>

## 二、法度
- 通行法：<三条以内，每条一句>
- 禁令：<这一带最不能碰的事>
- 刑罚风格：
- 谁在执行：<官 / 兵 / 商会打手 / 教会执法队>
- 灰色地带：<明面上不许、实际上没人管的事>

## 三、军力
- 常备兵力：
- 兵种与装备特点：
- 要塞与关隘：<名 + 一句话>
- 动员能力：
- 实际战力短板：

## 四、外交
### <相邻王国名>
- 关系：<盟 / 附庸 / 停战 / 敌对 / 通商>
- 争的是什么：
- 明面上的说法：
- 私下在做什么：
（每个相邻王国一节）

## 五、要地
### <要地名称>
- 类型：<国都 / 军镇 / 圣所 / 盐场 / 港口 / 关隘>
- 为什么重要：
- 谁在守：
- 一句话现状：
（两到四处）

## 六、区域划分
### 区域：<省名>
- 大致位置：<相对都城的方位与距离感>
- 地形底色：<一句话>
- 主要据点：<一两个名字>
- 这地方出的麻烦：<一句话>
（三到六个省，覆盖全境）
```

# 生成要求
- **只写政治与要地**。地形、气候、水源、植被、人口密度、产物这些属于区域文档，这里一句都不要展开。
- 统治者、势力、要地一律沿用输入与世界圣经里已有的名字；确需新名字时只新增必要的那几个。
- 「当前局势」写进「一、政体」的「一句话性格」与「五、要地」的现状里，不单独开节。
- 省（区域）划到 **3–6 个**，名字要能指认地貌或产业（「废炉群」「西盐田」），不要叫「北省」「南省」。
- 不新增输入里没有的国家或势力；不与已有设定矛盾。
- 语言简洁，每条一行，可直接用于后续生成区域与场景。"""


#: ``### 区域：<名>`` 小节
_PROVINCE_RE = re.compile(
    r"^#{2,4}\s*(?:区域|省份|省)[^\n：:]{0,6}[：:]\s*(?P<name>.+?)\s*$", re.MULTILINE
)

#: 「大致位置」「地形底色」这类一行字段
_FIELD_RE = re.compile(r"^-\s*(?P<label>[^：]{1,8})[：:]\s*(?P<value>.+?)\s*$")


def _blocks(text: str, pattern: re.Pattern[str]) -> list[tuple[str, str]]:
    """按小标题切块，返回 ``[(名字, 该块正文)]``（保持出现顺序）。"""
    lines = (text or "").splitlines()
    hits: list[tuple[str, int]] = []
    for index, line in enumerate(lines):
        match = pattern.match(line)
        if match:
            hits.append((match.group("name").strip(), index))
    blocks: list[tuple[str, str]] = []
    for order, (name, start) in enumerate(hits):
        end = hits[order + 1][1] if order + 1 < len(hits) else len(lines)
        # 撞到上一个层级的小标题就停（避免把下一节整段吞进来）
        body_lines: list[str] = []
        for line in lines[start + 1 : end]:
            if re.match(r"^#{1,2}\s", line):
                break
            body_lines.append(line)
        blocks.append((name, "\n".join(body_lines).strip()))
    return blocks


def _field(block: str, *labels: str) -> str:
    """取块内某个字段的值（按标签包含匹配，取第一条）。"""
    for line in (block or "").splitlines():
        match = _FIELD_RE.match(line.strip())
        if not match:
            continue
        label, value = match.group("label").strip(), match.group("value").strip()
        if any(want in label for want in labels):
            return value
    return ""


def parse_realm(text: str) -> list[tuple[str, str, str]]:
    """解析 realm 文档的「区域划分」，返回 ``[(省名, 地形底色, 主要据点)]``。

    这三样是**给下一次生成用的钩子**，不是完整数据：省文档生成时才展开。
    """
    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for name, body in _blocks(text, _PROVINCE_RE):
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(
            (
                name,
                _field(body, "地形底色", "地形"),
                _field(body, "主要据点", "据点"),
            )
        )
    return out


@dataclass(slots=True)
class RealmResult(AgentResult):
    """王国/地区生成结果：模型原文 + 解析出的区域清单。"""

    #: ``[(省名, 地形底色, 主要据点)]``
    provinces: list[tuple[str, str, str]] = field(default_factory=list)


class RealmAgent(TrpgAgent):
    """王国/地区生成：政治、法度、军力、外交，外加它辖下的区域清单。"""

    name = "realm"
    title = "王国/地区生成"
    description = "根据地区与周边信息，生成政体 / 法度 / 军力 / 外交，并给出区域（省）划分"
    system_prompt = _SYSTEM_PROMPT
    inputs = (
        AgentInput("realm", "本地区信息", description="该地区在世界地图里的设定"),
        AgentInput(
            "neighbors",
            "周边地区信息",
            required=False,
            description="相邻王国的名称与大致情况，可留空",
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

    async def run(self, **values: object) -> RealmResult:
        result = await super().run(**values)
        return RealmResult(
            agent=result.agent,
            title=result.title,
            content=result.content,
            model_type=result.model_type,
            usage=result.usage,
            usages=list(result.usages),
            provinces=parse_realm(result.content),
        )


#: 全局默认实例
realm_agent = RealmAgent()
