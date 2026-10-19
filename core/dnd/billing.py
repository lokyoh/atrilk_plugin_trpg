"""跑团计费统计：把每次模型调用的 token 与花费累计到存档里。

费用按模型在 ``provider.yml`` 里配置的单价（每百万 token）换算；没配单价的模型
只统计 token，金额记 0。累计结果写进存档的 ``progress["billing"]``，``/跑团.结束``
与 ``/跑团.结档`` 时读出来给玩家看。

调用方用 :func:`bind_save` 把当前存档绑定到上下文，agent 层只要把
:class:`~atrilk_plugin_trpg.core.agents.base.AgentResult` 交给 :func:`record_result`
即可，不必知道计费细节。

**为什么还要一份内存快照**：用量是边跑边记的——DM 每轮主持完立刻记账，而 DM 的
功能调用（改卡、切场景、按需生成）用的是各自 ``load_save`` 出来的存档副本，之后
还会整份写回 ``save.json``。调用方手里那份「跑团开始前读到的」旧对象一旦落盘，
就会把刚记上的用量写回旧值（看起来就是「DM 的调用没记上」）。所以累计结果同时
留在 :data:`_latest`，``save_store.save_save`` 落盘前用它覆盖对象里的 ``billing``。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from ATRI.log import log

from .save_store import load_save, save_save

if TYPE_CHECKING:
    from ATRI.system.agent.llm import ModelPricing

__all__ = [
    "BillingReport",
    "bind_save",
    "forget",
    "format_report",
    "record_result",
    "record_usages",
    "report",
    "sync_billing",
]

#: 存档 ``progress`` 里存累计用量的键
BILLING_KEY = "billing"

#: 当前正在跑团的存档 ID（agent 层据此把用量写回存档）
_current_save: ContextVar[str] = ContextVar("trpg_billing_save", default="")

#: 每份存档最新的累计用量（save_id -> billing 字典）。
#: 见模块文档：调用方手里的旧存档对象随时可能整份写回，这份快照保证用量不被覆盖。
_latest: dict[str, dict[str, Any]] = {}


@contextmanager
def bind_save(save_id: str) -> Iterator[None]:
    """在上下文里绑定当前存档，期间产生的模型调用都会计到它身上。"""
    token = _current_save.set(str(save_id or ""))
    try:
        yield
    finally:
        _current_save.reset(token)


def _pricing_map() -> dict[str, ModelPricing]:
    """模型名 -> 单价（与 WebUI 的统计口径一致，读 ProviderManager）。

    延迟导入 ``ATRI.system``：它在导入时就会碰 nonebot driver，而本模块会被插件
    的包 ``__init__`` 连带导入——留着顶层导入会让「只读存档 / 只算价钱」的代码
    也必须先初始化机器人（测试里就是这么炸的）。
    """
    from ATRI.system.agent.llm import ProviderManager

    result: dict[str, ModelPricing] = {}
    for provider in getattr(ProviderManager, "providers", {}).values():
        for model in getattr(provider, "models", []) or []:
            pricing = getattr(model, "pricing", None)
            if pricing is not None:
                result[str(model.name)] = pricing
    return result


def _empty_item() -> dict[str, Any]:
    return {
        "calls": 0,
        "prompt": 0,
        "output": 0,
        "cache_hit": 0,
        "cost": 0.0,
        "priced": False,
    }


def _add_usage(save_id: str, usages: list[Any]) -> None:
    """把若干次调用的用量累加进存档。"""
    save = load_save(save_id)
    if save is None:
        return
    # 先看内存快照：万一磁盘上的存档刚被别的副本写回旧值，也不会把已记的用量算丢。
    billing = dict(_latest.get(save_id) or save.progress.get(BILLING_KEY) or {})
    models: dict[str, Any] = dict(billing.get("models") or {})
    pricing_map = _pricing_map()
    changed = False
    for entry in usages:
        usage = getattr(entry, "usage", None)
        if not usage:
            continue
        prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
        output = int(getattr(usage, "output_tokens", 0) or 0)
        cache_hit = int(getattr(usage, "cache_hit_tokens", 0) or 0)
        name = str(getattr(entry, "model", "") or "") or "未知模型"
        item = dict(models.get(name) or _empty_item())
        item["calls"] = int(item.get("calls", 0)) + 1
        item["prompt"] = int(item.get("prompt", 0)) + prompt
        item["output"] = int(item.get("output", 0)) + output
        item["cache_hit"] = int(item.get("cache_hit", 0)) + cache_hit
        pricing = pricing_map.get(name)
        if pricing is not None:
            item["cost"] = round(
                float(item.get("cost", 0.0))
                + pricing.calculate_cost(prompt, output, cache_hit),
                6,
            )
            item["priced"] = True
        models[name] = item
        changed = True
    if not changed:
        return
    billing["models"] = models
    _latest[save_id] = billing
    save.progress[BILLING_KEY] = billing
    try:
        save_save(save)
    except OSError as e:
        log.warning(f"跑团计费写入失败：{e}")


def sync_billing(save: Any) -> None:
    """把内存里最新的计费用量并回存档对象（由 ``save_store.save_save`` 调用）。

    调用方手里的存档可能是跑团开始时读的旧对象：它落盘时会把 ``progress`` 整份
    写回，把 DM 主持 / 按需生成期间刚记上的用量覆盖掉。落盘前先并回最新快照，
    这样任何一次写存档都不会丢用量。
    """
    save_id = str(getattr(save, "save_id", "") or "")
    billing = _latest.get(save_id)
    progress = getattr(save, "progress", None)
    if billing is None or not isinstance(progress, dict):
        return
    progress[BILLING_KEY] = billing


def forget(save_id: str) -> None:
    """丢掉某存档的内存快照（存档被删除时调用）。"""
    _latest.pop(str(save_id or ""), None)


def record_usages(save_id: str, usages: list[Any]) -> None:
    """把若干次调用记到指定存档。

    用于开局生成世界这类「用量产生时存档还不存在（或另有上下文）」的场合。
    """
    if save_id and usages:
        _add_usage(save_id, list(usages))


def record_result(result: Any) -> None:
    """记录一次 agent 运行产生的全部模型调用（未绑定存档时直接跳过）。"""
    save_id = _current_save.get()
    if not save_id:
        return
    usages = list(getattr(result, "usages", None) or [])
    if not usages:
        usage = getattr(result, "usage", None)
        if usage:
            from ..agents.base import UsageEntry

            usages = [UsageEntry(model="", usage=usage)]
    if usages:
        _add_usage(save_id, usages)


class BillingReport:
    """一局跑团的用量汇总，供指令回复展示。"""

    def __init__(self, models: dict[str, Any]):
        self.models = models

    @property
    def calls(self) -> int:
        return sum(int(item.get("calls", 0)) for item in self.models.values())

    @property
    def prompt_tokens(self) -> int:
        return sum(int(item.get("prompt", 0)) for item in self.models.values())

    @property
    def output_tokens(self) -> int:
        return sum(int(item.get("output", 0)) for item in self.models.values())

    @property
    def cache_hit_tokens(self) -> int:
        return sum(int(item.get("cache_hit", 0)) for item in self.models.values())

    @property
    def cost(self) -> float:
        return round(
            sum(float(item.get("cost", 0.0)) for item in self.models.values()), 6
        )

    @property
    def has_unpriced(self) -> bool:
        return any(not item.get("priced") for item in self.models.values())

    def __bool__(self) -> bool:
        return bool(self.models)


def report(save_id: str) -> BillingReport:
    """读取某存档的用量汇总（优先用内存里的最新累计）。"""
    if not save_id:
        return BillingReport({})
    billing = _latest.get(save_id)
    if billing is None:
        save = load_save(save_id)
        billing = (save.progress.get(BILLING_KEY) or {}) if save else {}
    return BillingReport(dict(billing.get("models") or {}))


def format_report(save_id: str, title: str = "本局用量") -> str:
    """把用量汇总格式化成一段给玩家看的文本；没有数据时说明暂不可用。"""
    data = report(save_id)
    if not data:
        return f"📊 {title}：暂未统计到模型调用（可能还没用过模型）。"
    lines = [
        f"📊 {title}：",
        f"· 调用：{data.calls} 次",
        f"· Token：输入 {data.prompt_tokens} / 输出 {data.output_tokens}"
        + (f"（缓存命中 {data.cache_hit_tokens}）" if data.cache_hit_tokens else ""),
        f"· 花费：约 {data.cost:.4f} 元",
    ]
    if data.has_unpriced:
        lines.append("· 注：部分模型没在配置里设置单价，这部分费用未计入。")
    return "\n".join(lines)
