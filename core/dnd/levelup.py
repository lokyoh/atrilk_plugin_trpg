"""升级后的**选择会话**。

规则里升级要玩家做的决定只有两类，其它（职业特性、专长换法）交给 DM：

- **3 级选子职**（PHB 2024 所有职业统一 3 级获得）；
- **属性提升（ASI）**：4/8/12/16 级各一次，战士 6/14、游荡者 10 另有。

以前这两件事只写在升级提示里（「请 DM 询问玩家的选择」），靠人记；现在
升级结算发现有待办时直接给玩家开一个**升级选择会话**：复用建卡的交互管线
（子职与 ASI 的解析、校验、序列化都是现成的），逐项答完写回角色卡，
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
from .subclass import SUBCLASS_LEVEL

__all__ = ["pending_choices", "start_session"]


def pending_choices(card: DndCharacter) -> tuple[str, ...]:
    """这张卡升级到当前等级后还欠哪些选择（按会话顺序）。

    - ``"subclass"``：3 级以上且还没有子职；
    - ``"asi"``：当前等级该做的属性提升还没做完
      （按卡上的加点总量反推已做次数，见 :func:`.creation.session_from_card`）。
    """
    items: list[str] = []
    if card.level >= SUBCLASS_LEVEL and card.subclass_obj is None and card.profession:
        items.append("subclass")
    session = session_from_card(card)
    if session.asi_pending() > 0:
        items.append("asi")
    return tuple(items)


def start_session(card: DndCharacter, save_id: str = "") -> str | None:
    """为这张卡开一个升级选择会话，返回给玩家看的提示；没有待办返回 ``None``。

    会话复用建卡的存储与续接管线（``sessions/<user_id>.json`` + 常驻响应器），
    从第一个待办步骤开始：中间不需要的步骤由 :meth:`CreationSession.advance`
    自动跳过；全部答完后按建卡的收尾逻辑写回角色卡（有存档写回存档）。
    """
    items = pending_choices(card)
    if not items:
        return None
    existing = load_session(card.user_id)
    if existing is not None:
        # 已有建卡 / 升级会话在跑：不打断它（升级会话丢一次没关系，改卡能补）
        return None

    session = session_from_card(card)
    session.purpose = "levelup"
    session.save_id = str(save_id or "")
    # 「重做属性加点」才清 asi；这里要**补**：已做的保留，只把待办的接上
    if "subclass" in items:
        session.step = CreationStep.SUBCLASS
    else:
        session.step = CreationStep.ASI
    save_session(session)

    lines = [
        f"⬆️ Lv.{card.level} 的升级选择（回复后自动继续；"
        "回复「取消」跳过，稍后可用 /跑团.改卡 补）：",
        "",
        creation_prompt(session.step, session),
        "",
        "（选择期间你的消息会被当作选择内容；选完自动保存并回到游戏，"
        "不想现在选就回复「取消」。）",
    ]
    if "subclass" in items and "asi" in items:
        lines.append("")
        lines.append("（选完子职还会问属性提升，答完自动保存并回到游戏。）")
    else:
        lines.append("")
        lines.append("（答完自动保存并回到游戏。）")
    return "\n".join(lines)
