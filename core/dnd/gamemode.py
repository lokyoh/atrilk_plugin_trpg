"""开局配置：一局怎么跑，从开局那一刻就定下来。

**为什么需要这一层**：织法（:class:`~.event_progress.Recipe`）与纪元长度原先只有「读」
没有「写」——``save.progress["_recipe"]`` 全仓一个写入点都没有，于是所有参数实际都跑在
``from_dict({})`` 的缺省上。参数不落盘，就等于没有参数。

现在的分工：

==============  ============================================
这里            负责
==============  ============================================
:data:`MODES`   每种玩法的参数（织法 / 纪元长度 / 一句话说明）
:func:`start_mode`  把玩法写进存档（``_recipe`` + ``_epoch.length`` + ``_mode``）
:func:`mode_of` 读回来，用于展示
==============  ============================================

**刻意不做成「模组文档」**：旧设计的 frame 是挂在世界上的文档，而世界层必须干净
（换条故事线来跑该是同一副骨架）。玩法是一组参数，不是一段内容，所以它存在**存档**里，
一个世界可以用不同玩法开很多局。

两种玩法现在只差参数，不差机制——事件线本来就是无尽的，区别在于
节奏更密（支线更频繁）还是更松。谁也别假装「无尽」是个新引擎。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .epoch import DEFAULT_EPOCH_LENGTH, load_epoch, store_epoch
from .event_progress import Recipe

__all__ = [
    "DEFAULT_MODE",
    "MODES",
    "START_MODE_KEY",
    "GameMode",
    "mode_of",
    "mode_text",
    "start_mode",
]

#: 玩法名在 ``save.progress`` 里的键名。下划线开头 = 内部键。
START_MODE_KEY = "_mode"

#: 默认玩法
DEFAULT_MODE = "standard"


@dataclass(slots=True)
class GameMode:
    """一种玩法的参数。"""

    key: str
    #: 展示名
    title: str
    #: 一句话说明（进开局文案与手册）
    note: str
    #: 织法
    recipe: Recipe = field(default_factory=Recipe)
    #: 几个章算一个纪元
    epoch_length: int = DEFAULT_EPOCH_LENGTH
    #: 触发词（``/跑团.开局 <词>``）
    words: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "note": self.note,
            "recipe": self.recipe.to_dict(),
            "epoch_length": self.epoch_length,
        }


#: 全部玩法。键是 :attr:`GameMode.key`。
#:
#: ``standard``：四个事件一个纪元，收束节奏适中。
#:
#: ``endless``：三个事件一个纪元。理由是长跑最怕**一个纪元拖太久失去形状**——
#: 走太远才结算一次，玩家早忘了自己做过什么，称号也就说不出东西。收得紧一点，
#: 每个纪元都像一段有头有尾的故事。
#:
#: 两种玩法只差纪元长度，不差机制：事件线本来就是无尽的，
#: 谁也别假装「无尽」是个新引擎。
MODES: dict[str, GameMode] = {
    "standard": GameMode(
        key="standard",
        title="标准",
        note="按默认节奏推进，四个事件一个纪元",
        recipe=Recipe(),
        epoch_length=DEFAULT_EPOCH_LENGTH,
        words=(),
    ),
    "endless": GameMode(
        key="endless",
        title="无尽",
        note="三个事件一次收束，每个纪元都收得拢，能一直往下跑",
        recipe=Recipe(),
        epoch_length=3,
        words=("无尽", "endless", "--无尽", "--endless"),
    ),
}


def _mode_key(value: Any) -> str:
    """把触发词 / 玩法名归一成 :data:`MODES` 的键；认不出返回空串。"""
    word = str(value or "").strip().lower()
    if not word:
        return ""
    if word in MODES:
        return word
    for mode in MODES.values():
        if word in {w.lower() for w in mode.words}:
            return mode.key
    return ""


def start_mode(save: Any, mode: str = DEFAULT_MODE) -> GameMode:
    """把玩法写进存档（织法 + 纪元长度 + 玩法名），返回它。

    认不出的玩法名**退回默认**而不是报错：这是开局路径上的一步，
    玩家写错一个词不该让整局开不起来。
    """
    picked = MODES.get(_mode_key(mode) or DEFAULT_MODE, MODES[DEFAULT_MODE])
    progress = getattr(save, "progress", None)
    if progress is not None:
        progress["_recipe"] = picked.recipe.to_dict()
        progress[START_MODE_KEY] = picked.key
    ep = load_epoch(save)
    ep.length = max(1, int(picked.epoch_length))
    store_epoch(save, ep)
    return picked


def mode_of(save: Any) -> GameMode:
    """读回这一局用的玩法；存档里没写就按默认。"""
    key = _mode_key((getattr(save, "progress", None) or {}).get(START_MODE_KEY))
    return MODES.get(key or DEFAULT_MODE, MODES[DEFAULT_MODE])


def mode_text(save: Any, *, for_player: bool = True) -> str:
    """玩法的一行说明（进手册 / 开局文案）。"""
    mode = mode_of(save)
    return f"{mode.title}模式：{mode.note}"
