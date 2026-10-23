"""数据表：``res/dnd/*.yaml``。

**为什么数值不写在代码里**：种族、职业、背景、物品、装备、法术、怪物这些表是
《玩家手册》《怪物图鉴》与两个模组的抄本——改一个数值、加一条法术、补一只怪，
不该动 ``.py``；也让「代码在做什么」和「书上写了什么」分开看。规则正文早在
``res/rule.md``，这里与它并列。

**放在插件目录下**（``atrilk_plugin_trpg/res/dnd/``），不散到 ATRI 框架里：
换框架不该把游戏数据带走，反过来也不该往别人的仓里塞东西。

读法：

- 一张表一个文件，**顶层是列表**，一条一项（``equipment.yaml`` 例外，见
  :func:`load_mapping` 的三段式）；
- 缺省值不必写——加载器按 dataclass 的默认值补齐，所以 YAML 里只出现「与默认不同」
  的字段，读起来就是这张表真正的内容；
- **读不到就抛错**：这些表是战斗与建卡的数值来源，缺一张只会让规则静默算错
  （怪物没 AC、法术没伤害），比启动就报错难查得多。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

__all__ = [
    "DATA_DIR",
    "PLUGIN_ROOT",
    "DataFileError",
    "as_rows",
    "as_texts",
    "load_mapping",
    "load_table",
]

#: 插件根目录（``core/dnd/datafiles.py`` 上溯两层）
PLUGIN_ROOT = Path(__file__).resolve().parents[2]
#: 数据目录（随插件分发）
DATA_DIR = PLUGIN_ROOT / "res" / "dnd"


class DataFileError(RuntimeError):
    """数据表缺失或格式不对——打包问题，不能静默降级成空表。"""


def _read(name: str) -> Any:
    path = DATA_DIR / f"{name}.yaml"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise DataFileError(f"读不到数据表 {path}（{e}）") from e
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise DataFileError(f"数据表格式不对 {path}：{e}") from e


def _rows(name: str, data: Any) -> tuple[dict[str, Any], ...]:
    if data is None:
        return ()
    if not isinstance(data, list):
        raise DataFileError(
            f"数据表 {name}.yaml 的顶层应当是列表，实际是 {type(data).__name__}"
        )
    for index, item in enumerate(data, 1):
        if not isinstance(item, dict):
            raise DataFileError(
                f"数据表 {name}.yaml 第 {index} 条不是映射：{item!r}"
            )
    return tuple(data)


@lru_cache(maxsize=None)
def load_table(name: str) -> tuple[dict[str, Any], ...]:
    """读一张表（``res/dnd/<name>.yaml``），返回「一条一项」的元组。

    结果缓存：数据随插件分发，运行期不变；改表要重启。
    """
    return _rows(name, _read(name))


@lru_cache(maxsize=None)
def load_mapping(name: str) -> dict[str, tuple[dict[str, Any], ...]]:
    """读一张**分段的**表（顶层是映射，每个值是一组条目）。

    目前只有 ``equipment.yaml``：护甲 / 盾牌 / 武器三张表同属一类，
    合在一个文件里比拆三个文件好找。
    """
    data = _read(name)
    if not isinstance(data, dict):
        raise DataFileError(
            f"数据表 {name}.yaml 的顶层应当是映射，实际是 {type(data).__name__}"
        )
    return {str(key): _rows(f"{name}.{key}", value) for key, value in data.items()}


def as_texts(value: object) -> tuple[str, ...]:
    """YAML 里的「一列字符串」→ 元组。

    **单条时作者常常只写一个字符串**（``aliases: 食尸鬼群``），这是常见写法而不是
    错误：把它当成一条，别按字符拆开（``tuple("食尸鬼群")`` 会得到「食」「尸」…）。
    """
    if value is None or value == "":
        return ()
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value if item not in (None, ""))


def as_rows(value: object) -> tuple[dict[str, object], ...]:
    """YAML 里的「一列映射」→ 元组；**只有一条时作者常直接写映射**，当成一条。"""
    if isinstance(value, dict):
        return (value,)
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, dict))
