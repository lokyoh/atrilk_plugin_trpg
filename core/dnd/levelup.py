"""升级后的**选择会话**。

规则里升级要玩家做的决定只有三类，其它（职业特性、专长换法）交给 DM：

- **3 级选子职**（PHB 2024 所有职业统一 3 级获得）；
- **属性提升（ASI）**：4/8/12/16 级各一次，战士 6/14、游荡者 10 另有；
- **新法术**：可准备数 / 法术书条目随等级涨，这一级新得的几个由玩家挑
  （见 :mod:`.spell_progression`）。

以前这些事只写在升级提示里（「请 DM 询问玩家的选择」），靠人记；现在
升级结算发现有待办时直接给玩家开一个**升级选择会话**：复用建卡的交互管线
（子职 / ASI / 法术的解析、校验、序列化都是现成的），逐项答完写回角色卡，
玩家回到正常对话 —— 中途可以继续干别的，回复「取消」放弃（稍后可用
``/跑团.改卡`` 补）。
"""

from __future__ import annotations

from .card import DndCharacter
from .creation import (
    CreationStep,
    creation_prompt,
    session_from_card,
)
from .session_store import load_session, save_session

__all__ = ["pending_choices", "start_session"]

#: 升级会话里各项的中文名（提示「随后还会问……」用）
_STEP_LABELS: dict[CreationStep, str] = {
    CreationStep.SUBCLASS: "选子职",
    CreationStep.ASI: "属性提升",
    CreationStep.SPELLS: "挑新法术",
}

#: 与 :data:`atrilk_plugin_trpg.core.dnd.creation._LEVELUP_STEPS` 同序
_ORDER: tuple[CreationStep, ...] = (
    CreationStep.SUBCLASS,
    CreationStep.ASI,
    CreationStep.SPELLS,
)


def _session_for(card: DndCharacter, from_level: int):
    """还原会话并标上「升级到当前等级」的上下文。"""
    session = session_from_card(card)
    session.purpose = "levelup"
    session.from_level = max(1, min(int(from_level or card.level - 1), card.level))
    return session


def pending_choices(card: DndCharacter, from_level: int = 0) -> tuple[str, ...]:
    """这张卡升级到当前等级后还欠哪些选择（按会话顺序）。

    - ``"subclass"``：3 级以上且还没有子职；
    - ``"asi"``：当前等级该做的属性提升还没做完
      （按卡上的加点总量反推已做次数，见 :func:`.creation.session_from_card`）；
    - ``"spells"``：这一级新得的法术还没挑（数量按职业表差值算）。

    :param from_level: 升级**前**的等级（算新法术数量用）；0 = 按当前等级 -1。
    """
    session = _session_for(card, from_level)
    return tuple(
        label
        for label, step in zip(
            ("subclass", "asi", "spells"), _ORDER, strict=True
        )
        if session.levelup_step_needed(step)
    )


def start_session(
    card: DndCharacter, save_id: str = "", from_level: int = 0
) -> str | None:
    """为这张卡开一个升级选择会话，返回给玩家看的提示；没有待办返回 ``None``。

    会话复用建卡的存储与续接管线（``sessions/<user_id>.json`` + 常驻响应器），
    从第一个待办步骤开始：中间不需要的步骤由 :meth:`CreationSession.advance`
    自动跳过；全部答完后按建卡的收尾逻辑写回角色卡（有存档写回存档）。
    """
    session = _session_for(card, from_level)
    todo = [step for step in _ORDER if session.levelup_step_needed(step)]
    if not todo:
        return None
    existing = load_session(card.user_id)
    if existing is not None:
        # 已有建卡 / 升级会话在跑：不打断它（升级会话丢一次没关系，改卡能补）
        return None

    session.save_id = str(save_id or "")
    session.step = todo[0]
    save_session(session)

    lines = [
        f"⬆️ Lv.{session.from_level} → Lv.{card.level} 的升级选择"
        "（回复后自动继续；回复「取消」跳过，稍后可用 /跑团.改卡 补）：",
        "",
        creation_prompt(session.step, session),
        "",
        "（选择期间你的消息会被当作选择内容；选完自动保存并回到游戏，"
        "不想现在选就回复「取消」。）",
    ]
    rest = todo[1:]
    if rest:
        lines.append(
            "（随后还会问：" + "、".join(_STEP_LABELS[s] for s in rest) + "）"
        )
    return "\n".join(lines)
