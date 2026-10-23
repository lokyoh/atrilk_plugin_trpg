"""玩家战斗描述 → 结构化意图，**纯规则、纯函数、暂不执行**。

现在只做**影子解析**：把玩家那句话解析成「攻击谁 / 用什么武器 / 施什么法术」，
结果**追加写进存档目录下的独立文件** ``intent_log.jsonl``（一行一条、全量不截断），
供统计与回放，**不参与任何结算**。
等命中率看得过去了，再让它接管 ``submit_action`` 的战斗分支、跳过 DM 调用。

为什么不用模型：这层存在的意义就是省掉战斗里的模型往返，用模型来理解一句话
等于白省。好在可选词是有限的——敌人名、武器名、法术名全是结构化数据，玩家又
照着战场图底部那行「常用名词」打，句式会自然收敛（「攻击 地精1」「施法 火球术」）。

## 覆盖范围

认得的动词：攻击 / 施法 / 移动 / 治疗 / 撤退 / 躲闪 / 协助。
认不出的返回 ``kind="unknown"``，调用方将来可以回落到 DM 或追问玩家。

## 判据优先级

撤退 > 治疗 > 施法 > 攻击 > 移动 > 躲闪 > 协助。
「冲上去砍他」同时有移动和攻击动词，按攻击结算并记 ``move_first``。
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .card import DndCharacter
from .combat import CombatState
from .equipment import get_weapon, parse_item_stack
from .save_store import save_dir

__all__ = [
    "INTENT_LOG_FILENAME",
    "Intent",
    "intent_log_path",
    "intent_stats",
    "parse_combat_intent",
    "read_intents",
    "record_intent",
]

#: 解析日志文件名：存在 ``saves/<save_id>/`` 下，一行一条 JSON，**全量不截断**
INTENT_LOG_FILENAME = "intent_log.jsonl"

#: 认得的一级动词。顺序即优先级，越靠前越先认。
_VERBS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("retreat", re.compile(r"(撤退|撤离|逃跑|逃离|溜走|退出战斗|脱离战斗)")),
    ("heal", re.compile(r"(治疗|疗伤|治愈|包扎|急救|回血|喝.{0,4}药|药水|药剂)")),
    ("cast", re.compile(r"(施法|施放|施展|念(?:动)?咒|念咒|吟唱|咏唱|召唤|放出法术)")),
    (
        "attack",
        re.compile(
            r"(攻击|砍|劈|刺|捅|斩|射|开火|开枪|放箭|出手|揍|打(?!算|听|探|开)|"
            r"挥剑|挥刀|突刺|背刺|偷袭|斩击)"
        ),
    ),
    (
        "move",
        re.compile(
            r"(移动|走|跑|冲|跳|爬|潜行|后退|后撤|上前|靠近|贴近|绕到|扑过去|退到)"
        ),
    ),
    ("dodge", re.compile(r"(躲闪|闪避|躲避|防御姿态|举盾)")),
    ("help", re.compile(r"(协助|帮忙|掩护|援护|支援)")),
)

#: 代词：没点名时按「最近的那个敌人」兜底
_PRONOUN_RE = re.compile(r"(他|她|它|那只|这个|那个|对方|敌人)")

#: 目标格坐标：``7,4`` / ``7，4``
_POS_RE = re.compile(r"(\d{1,3})\s*[,，]\s*(\d{1,3})")

#: 武器泛称：玩家常写「用剑砍」而装备栏里是「长剑」
_WEAPON_WORDS = (
    "匕首",
    "长剑",
    "短剑",
    "巨剑",
    "剑",
    "刀",
    "斧",
    "锤",
    "矛",
    "戟",
    "弓",
    "弩",
    "杖",
    "枪",
)


@dataclass(frozen=True)
class Intent:
    """一条解析结果。字段与 ``attack`` / ``cast_spell`` 等工具参数对齐。"""

    #: attack / cast / move / heal / retreat / dodge / help / unknown
    kind: str
    #: 目标名（规范化成战斗里的显示名）
    target: str = ""
    weapon: str = ""
    spell: str = ""
    advantage: str = "普通"
    two_handed: bool = False
    #: 是否先移动再打（「冲上去砍它」）
    move_first: bool = False
    #: 目标格坐标（0 基），只在写死坐标时有
    pos: tuple[int, int] | None = None
    #: 0~1：自己估的把握，用于将来定「多高才允许跳过 DM」
    confidence: float = 0.0
    #: 为什么这么判（统计时看错在哪）
    reason: str = ""
    raw: str = ""

    @property
    def usable(self) -> bool:
        """这条够不够程序自己结算（不需要 DM 兜底）。"""
        if self.kind in {"dodge", "help", "retreat", "heal"}:
            return True
        if self.kind == "attack":
            return bool(self.target)
        if self.kind == "cast":
            return bool(self.spell)
        if self.kind == "move":
            return bool(self.target or self.pos)
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "target": self.target,
            "weapon": self.weapon,
            "spell": self.spell,
            "advantage": self.advantage,
            "move_first": self.move_first,
            "pos": list(self.pos) if self.pos else None,
            "confidence": round(self.confidence, 2),
            "usable": self.usable,
            "reason": self.reason,
            "raw": self.raw,
        }


def _hit_name(text: str, names: Iterable[str]) -> str:
    """在话里找出一个已知名字：先整名，再试去掉尾部数字（地精1 ← 「地精」）。"""
    pool = [n for n in names if n]
    direct = [n for n in pool if n in text]
    if direct:
        return max(direct, key=len)
    for name in sorted(pool, key=len, reverse=True):
        stem = re.sub(r"\d+$", "", name).strip()
        if len(stem) >= 2 and stem in text:
            return name
    return ""


def _hit_spell(text: str, card: DndCharacter) -> str:
    """认法术名：整名命中，或去掉尾字（火球 ← 火球术）。"""
    known = [s.cn for s in card.known_spells() if s.cn]
    direct = [n for n in known if n in text]
    if direct:
        return max(direct, key=len)
    for name in sorted(known, key=len, reverse=True):
        stem = name.rstrip("术箭击咒")
        if len(stem) >= 2 and stem in text:
            return name
    return ""


def _hit_weapon(text: str, card: DndCharacter) -> str:
    """认武器：先认装备里的全名，再按「剑 / 刀」这类泛称去装备里找。"""
    names: list[str] = []
    for raw in card.equipment:
        stack = parse_item_stack(str(raw))
        if get_weapon(stack.name) is not None:
            names.append(stack.name)
    hit = _hit_name(text, names)
    if hit:
        return hit
    for word in _WEAPON_WORDS:
        if word in text:
            for name in names:
                if word in name:
                    return name
    return ""


#: 动词名 -> 正则，供「只问有没有某个动词」时查用
_VERB_MAP = dict(_VERBS)


def _nearest_enemy(combat: CombatState) -> str:
    """代词兜底时用的「最近的敌人」。

    现在取先攻序最靠前的未退场敌人——够用，因为真要点名玩家会写名字；
    将来若接具体出手者坐标，就在这里换成长辈距离比较。
    """
    alive = [c for c in combat.order if c.kind == "enemy" and not c.out]
    return alive[0].name if alive else ""


def parse_combat_intent(text: str, combat: CombatState, card: DndCharacter) -> Intent:
    """把一句玩家的话解析成 :class:`Intent`；认不出返回 ``kind="unknown"``。"""
    raw = str(text or "").strip()
    flat = re.sub(r"\s+", " ", raw)
    if not flat:
        return Intent(kind="unknown", raw=raw, reason="空输入")

    enemies = [c.name for c in combat.order if c.kind == "enemy" and not c.out]
    others = [c.name for c in combat.order if c.kind == "player"]
    target = _hit_name(flat, enemies)
    ally = _hit_name(flat, others)
    weapon = _hit_weapon(flat, card)
    spell = _hit_spell(flat, card)
    advantage = "优势" if "优势" in flat else ("劣势" if "劣势" in flat else "普通")
    two_handed = "双手" in flat
    pos_match = _POS_RE.search(flat)
    pos = (int(pos_match.group(1)), int(pos_match.group(2))) if pos_match else None

    kind = ""
    for name, pattern in _VERBS:
        if pattern.search(flat):
            kind = name
            break

    # 法术名出现即视为施法：「对地精1 用火球术」不一定要写「施放」
    if spell and kind in {"", "attack"}:
        kind = "cast"

    # 没点名但用了代词：按最近的敌人兜底（「砍它」）
    pronoun = bool(_PRONOUN_RE.search(flat))
    if not target and pronoun and kind in {"attack", "cast"}:
        target = _nearest_enemy(combat)

    moves = bool(_VERB_MAP["move"].search(flat))
    move_first = moves and kind in {"attack", "cast"}

    if kind == "retreat":
        return Intent("retreat", confidence=1.0, reason="动词：撤退", raw=raw)
    if kind == "heal":
        return Intent(
            "heal",
            target=ally or target,
            confidence=1.0 if (ally or target) else 0.8,
            reason="动词：治疗" + ("+目标" if (ally or target) else "（默认自己）"),
            raw=raw,
        )
    if kind == "cast":
        if not spell:
            return Intent(
                "cast",
                target=target,
                advantage=advantage,
                confidence=0.4,
                reason="有施法动词但没认出法术名",
                raw=raw,
            )
        bits = ["法术名命中"]
        if target:
            bits.append("有目标")
        return Intent(
            "cast",
            target=target,
            spell=spell,
            advantage=advantage,
            move_first=move_first,
            confidence=1.0 if target else 0.75,
            reason="+".join(bits),
            raw=raw,
        )
    if kind == "attack":
        if not target:
            return Intent(
                "attack",
                weapon=weapon,
                advantage=advantage,
                two_handed=two_handed,
                move_first=moves,
                confidence=0.45,
                reason="有攻击动词但没认出目标",
                raw=raw,
            )
        named = bool(_hit_name(flat, enemies))
        bits = ["攻击动词"]
        # 目标是自己点名的、还是靠代词兜底的，日志里要分得清——后者更容易判错
        bits.append(("目标：" if named else "代词就近：") + target)
        if weapon:
            bits.append("武器：" + weapon)
        return Intent(
            "attack",
            target=target,
            weapon=weapon,
            advantage=advantage,
            two_handed=two_handed,
            move_first=move_first,
            confidence=1.0 if not move_first else 0.9,
            reason="+".join(bits),
            raw=raw,
        )
    if kind == "move":
        dest = target or ally
        if dest:
            return Intent(
                "move",
                target=dest,
                pos=pos,
                confidence=1.0,
                reason="移动动词+目标：" + dest,
                raw=raw,
            )
        if pos:
            return Intent(
                "move", pos=pos, confidence=0.9, reason="移动动词+坐标", raw=raw
            )
        return Intent(
            "move", confidence=0.5, reason="只有移动动词，没认出目的地", raw=raw
        )
    if kind == "dodge":
        return Intent("dodge", confidence=1.0, reason="动词：躲闪", raw=raw)
    if kind == "help":
        return Intent(
            "help",
            target=ally,
            confidence=1.0 if ally else 0.7,
            reason="动词：协助",
            raw=raw,
        )

    # 没有动词：只认出了名字也算一条线索（将来可当「明确目标但没说做什么」追问）
    if target or spell:
        return Intent(
            "unknown",
            target=target,
            spell=spell,
            confidence=0.3,
            reason="只有目标 / 法术名，没有动词",
            raw=raw,
        )
    return Intent("unknown", confidence=0.0, reason="没有可识别的动词与目标", raw=raw)


def intent_log_path(save: Any) -> Path | None:
    """这份存档的解析日志路径；没有 ``save_id`` 时返回 ``None``。"""
    save_id = str(getattr(save, "save_id", "") or "").strip()
    if not save_id:
        return None
    return save_dir(save_id) / INTENT_LOG_FILENAME


def record_intent(
    save: Any, intent: Intent, *, round_no: int = 0, actor: str = ""
) -> dict[str, Any]:
    """把一条解析结果**追加**进日志文件（一行一条 JSON）。

    用独立文件而不是 ``save.json`` 里的字段：日志只增不改、还会很长，
    混进存档会让每次保存都重写整份进度，也不好在外部直接扫。
    """
    entry = intent.to_dict()
    entry["round"] = int(round_no or 0)
    entry["actor"] = actor
    entry["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    path = intent_log_path(save)
    if path is None:
        return entry
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # 上次若被杀在半个 JSON 上，文件末尾没有换行——直接追加会把新条目粘到
        # 那半行后面，两条一起报废。补一个换行，让坏行只坏它自己。
        pad = ""
        if path.is_file() and path.stat().st_size:
            with path.open("rb") as fh:
                fh.seek(-1, 2)
                if fh.read(1) != b"\n":
                    pad = "\n"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(pad + json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        # 写不进去不该影响这一轮（调用方本来也只是实验）
        return entry
    return entry


def read_intents(save: Any, limit: int | None = None) -> list[dict[str, Any]]:
    """读解析日志：``limit=None`` 读全部，否则读最后若干条。坏行跳过。"""
    path = intent_log_path(save)
    if path is None or not path.is_file():
        return []
    items: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue  # 半行（写到一半被杀）不该毁掉整份日志
                if isinstance(item, dict):
                    items.append(item)
    except OSError:
        return []
    return items[-limit:] if limit else items


def intent_stats(save: Any) -> dict[str, Any]:
    """统计这份存档的解析表现：总数、可用数、命中率、各动词条数。"""
    items = read_intents(save)
    total = len(items)
    usable = sum(1 for item in items if item.get("usable"))
    kinds = Counter(str(item.get("kind") or "unknown") for item in items)
    return {
        "total": total,
        "usable": usable,
        "rate": round(usable / total, 2) if total else 0.0,
        "by_kind": dict(kinds),
    }
