"""遭遇校准：把怪物的挑战等级与冒险小队的水平对齐。

对应「现有怪物生成是否满足单个普通怪物的挑战等级等于四人一组冒险者们的平均等级，
成群出现的怪物其挑战等级应该低于冒险小队的等级」这条需求。

规则口径（近似值，够用且好解释）：

- **单个普通怪物**：目标 CR = **队伍平均等级**（四人一组的平均水平）；
- **成对 / 成群**：目标 CR 随数量下调，保证同场遭遇的总威胁不超标：

  ==========  ====================
  怪物数量    目标 CR
  ==========  ====================
  1 只        队伍平均等级
  2 只        队伍平均等级 - 1
  3 ~ 4 只    队伍平均等级 - 2
  5 ~ 6 只    队伍平均等级 - 3
  7 只以上    队伍平均等级 - 4
  ==========  ====================

- **人数微调**：比四人多一人加 0.5 CR，比四人少一人减 0.5 CR（人多力量大，人少要轻松些）。

缩放由 :func:`~.monsters.scale_monster` 完成，落盘 key 形如 ``哥布林@cr3``，
战斗状态里存的就是这个 key，事后能原样还原（见 :func:`~.monsters.get_monster`）。

本模块只做**算术与建议**，不碰存档。
"""

from __future__ import annotations

from .monsters import MONSTERS, Monster, scale_monster

__all__ = [
    "GROUP_CR_OFFSET",
    "calibrate_monster",
    "calibrate_specs",
    "recommended_monsters",
    "target_cr_for",
    "target_cr_text",
]


#: 怪物数量 → 目标 CR 相对队伍平均等级的下调幅度
GROUP_CR_OFFSET: dict[int, int] = {
    1: 0,
    2: 1,
    3: 2,
    4: 2,
    5: 3,
    6: 3,
}


def _offset_for(count: int) -> int:
    """取该数量对应的 CR 下调幅度（7 只以上按 4 算）。"""
    count = max(1, int(count))
    if count in GROUP_CR_OFFSET:
        return GROUP_CR_OFFSET[count]
    return 4


def target_cr_for(level: int, count: int = 1, *, party_size: int = 4) -> float:
    """按队伍等级与怪物数量算目标 CR。

    :param level: 队伍平均等级（通常由 :func:`~.combat.party_level` 给出）
    :param count: 同类怪物的数量
    :param party_size: 参与玩家人数（以四人为基准微调）
    """
    level = max(1, int(level))
    count = max(1, int(count))
    size = max(1, int(party_size))
    base = float(level) - _offset_for(count)
    base += 0.5 * (size - 4)
    return max(0.0, base)


def _cr_text(cr: float) -> str:
    """CR 的习惯写法：0.5 → ``1/2``。"""
    if cr >= 1:
        return str(int(cr)) if float(cr).is_integer() else f"{cr:g}"
    if cr == 0.125:
        return "1/8"
    if cr == 0.25:
        return "1/4"
    if cr == 0.5:
        return "1/2"
    return f"{cr:g}"


def target_cr_text(level: int, count: int = 1, *, party_size: int = 4) -> str:
    """目标 CR 的文本写法。"""
    return _cr_text(target_cr_for(level, count, party_size=party_size))


def recommended_monsters(level: int, count: int = 1, *, party_size: int = 4) -> list[Monster]:
    """按目标 CR 推荐怪物表里最合适的几只（CR 越接近目标越靠前）。"""
    target = target_cr_for(level, count, party_size=party_size)
    return sorted(
        MONSTERS,
        key=lambda m: (abs(m.cr - target), m.cr),
    )[:5]


def calibrate_monster(
    monster: Monster | str,
    level: int,
    count: int = 1,
    *,
    party_size: int = 4,
) -> tuple[Monster | None, str]:
    """把怪物校准到目标 CR，返回 ``(数据块, 说明)``。

    规则：

    - 怪物 CR **高于**目标：认为是 DM 故意安排的高强度遭遇（首领 / 精英），**不缩**，
      只在说明里提示风险；
    - 怪物 CR **低于**目标：缩放到目标 CR（群怪因此统一带上校准后的数值）；
    - 档位 NPC（数值本就按队伍等级现算）不在这里处理。
    """
    base = monster if isinstance(monster, Monster) else None
    if base is None:
        return None, "认不出这只怪物"
    target = target_cr_for(level, count, party_size=party_size)
    if abs(base.cr - target) < 0.26:
        return base, f"{base.cn} CR{base.cr_text}，与目标 CR{_cr_text(target)} 一致"
    if base.cr > target:
        return base, (
            f"{base.cn} CR{base.cr_text} 高于目标 CR{_cr_text(target)}"
            "（按首领 / 精英处理，未缩放；注意这场可能超出小队承受）"
        )
    scaled = scale_monster(base, target)
    if scaled is None:
        return base, f"{base.cn} 无法缩放，保持原数值"
    return scaled, (
        f"{base.cn} CR{base.cr_text} → 校准为 CR{_cr_text(target)}"
        f"（{count} 只，对应队伍平均等级 {level}）"
    )


def calibrate_specs(
    specs: list, level: int, *, party_size: int = 4
) -> tuple[list, list[str]]:
    """把一批 :class:`~.combat.EnemySpec` 按遭遇规则整体校准。

    **NPC 不在这里缩放**，两种都跳过：

    - **具名 NPC**（``spec.npc``）：``名字:怪物模板`` 是 DM 有意安排的角色；
    - **档位 NPC**（key 以 ``npc/`` 开头）：数值本就按队伍等级现算，
      而且缩放后key 会变成 ``npc/veteran/3@cr1``——这种 key 再拿
      :func:`~.monsters.get_monster` 还原时会解析成 ``npc/veteran/1``，
      数值全错档，因此这里必须原样放过。

    :return: ``(新的敌人清单, 校准说明)``；说明是给 DM 看的开战提示，空列表表示无需调整。
    """
    from dataclasses import replace  # 局部导入：encounter 被 combat 之外的模块依赖

    from .npcs import is_npc_key  # 局部导入：避免 npcs ←encounter 的模块级环

    result: list = []
    notes: list[str] = []
    for spec in specs:
        monster = getattr(spec, "monster", None)
        count = max(1, int(getattr(spec, "count", 1) or 1))
        if monster is None or getattr(spec, "npc", False) or is_npc_key(monster.key):
            result.append(spec)
            continue
        calibrated, note = calibrate_monster(
            monster, level, count, party_size=party_size
        )
        if calibrated is not None and calibrated is not monster:
            spec = replace(spec, monster=calibrated)
        # 说明一律收下：哪怕「与目标一致」「高于目标未缩放」也是 DM 该知道的信息
        if note:
            notes.append(note)
        result.append(spec)
    return result, notes
