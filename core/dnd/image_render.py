"""图片渲染（喂给 ``ATRI.system.htmlrender`` 的 ``html_to_pic`` 转成图片）。

纯展示：只读参战状态、角色卡派生数据与调用方传进来的元信息，不碰存档、不调模型。
颜色一律取 :mod:`.theme` 的 CSS 变量，布局在这里。

- :func:`combat_to_html`：战场图——先攻序里每名参战者的血量 / AC / 状态 / 站位，
  并高亮当前正在行动的那个；末尾可挂本轮判定明细。
- :func:`player_panel_html`：玩家面板——装备（含数值）、法术（按环阶附简介）、
  以及当前这一回合还剩下的可用动作（动作 / 附赠动作 / 反应 / 移动 / 法术位）。
- :func:`narration_to_html`：DM 叙述卡——顶部一行此刻的时间 / 地点 / 事件，下面是正文。
"""

from __future__ import annotations

import html
from collections.abc import Sequence

from .card import DndCharacter
from .combat import Combatant, CombatState
from .spells import _LEVEL_LABELS
from .theme import css_vars, get_theme

__all__ = [
    "combat_to_html",
    "data_card_html",
    "narration_to_html",
    "player_panel_html",
]

#: 地形键 -> (图例中文名, 单字标记)；底色走主题变量 ``--t-<key>``
_TERRAIN = {
    "floor": ("空地", "."),
    "difficult": ("困难地形", ","),
    "cover_half": ("半掩体", "o"),
    "cover_three": ("四分之三掩体", "O"),
    "blocked": ("障碍", "#"),
    "hazard": ("危险地形", "!"),
    "water": ("浅水", "~"),
}


def _board_html(state: CombatState, current_cid: str | None) -> str:
    """把 ``state`` 的棋盘渲染成一块格子；没有棋盘（老存档）返回空串。"""
    w, h = int(getattr(state, "width", 0) or 0), int(getattr(state, "height", 0) or 0)
    if w <= 0 or h <= 0:
        return ""

    # 谁站在哪一格：一格里可能有多人，只在大镜面上显示第一个 + 「+n」
    occ: dict[tuple[int, int], list[Combatant]] = {}
    for c in state.order:
        x, y = int(getattr(c, "x", -1) or -1), int(getattr(c, "y", -1) or -1)
        if x >= 0 and y >= 0 and 0 <= x < w and 0 <= y < h:
            occ.setdefault((x, y), []).append(c)

    # 每格约 26px 起，最多 30px，整体塞进约 505px 宽
    cell = min(30, 505 // w)
    cells: list[str] = []
    for y in range(h):
        for x in range(w):
            key = state.tiles.get(f"{x},{y}", "floor")
            glyph = _TERRAIN.get(key, _TERRAIN["floor"])[1]
            inner = ""
            here = occ.get((x, y))
            if here:
                first = here[0]
                cls = "tok-player" if first.kind == "player" else "tok-enemy"
                if first.cid == current_cid:
                    cls += " now"
                label = _esc(first.name[:1])
                cnt = (
                    f'<span class="cnt">+{len(here) - 1}</span>'
                    if len(here) > 1
                    else ""
                )
                inner = f'<div class="token {cls}">{label}{cnt}</div>'
            else:
                inner = glyph if glyph != "." else ""
            cells.append(f'<div class="cell t-{key}">{inner}</div>')

    legend = "".join(
        f'<span><i class="swatch" style="background:var(--t-{key})"></i>{_esc(cn)}</span>'
        for key, (cn, _g) in _TERRAIN.items()
    )
    return (
        '<div class="board-wrap">'
        f'<div class="board-title">战场棋盘（每格 5 尺，斜向也算 1 格）</div>'
        f'<div class="board" style="grid-template-columns:repeat({w},{cell}px);'
        f'grid-auto-rows:{cell}px">'
        f"{''.join(cells)}"
        "</div>"
        f'<div class="legend">{legend}</div>'
        "</div>"
    )


_PAGE_CSS = """
*{margin:0;padding:0;box-sizing:border-box}
body{width:540px;font-family:"PingFang SC","Microsoft YaHei","Noto Sans CJK SC","Heiti SC",sans-serif;background:var(--page);padding:14px}
.card{background:linear-gradient(160deg,var(--card1),var(--card2));border:1px solid var(--border);border-radius:14px;overflow:hidden;color:var(--text);box-shadow:0 6px 20px rgba(0,0,0,.5)}
.header{background:linear-gradient(135deg,var(--header1),var(--header2));padding:14px 18px}
.name{font-size:20px;font-weight:700;color:var(--text)}
.meta{font-size:12px;color:var(--text2);margin-top:4px;opacity:.92}
.section{padding:10px 18px;border-top:1px solid rgba(255,255,255,.06);font-size:13px;line-height:1.7}
.section .title{color:var(--accent);font-weight:700;margin-bottom:5px}
.muted{color:var(--text2);font-size:13px}
.sub{color:var(--accent);font-weight:700;font-size:13px;margin:6px 0 2px}
.tag{display:inline-block;background:var(--tag-bg);border:1px solid var(--tag-border);border-radius:6px;padding:2px 8px;margin:2px 5px 2px 0;font-size:12px}
.cb{padding:8px 18px}
.fighter{display:flex;align-items:center;gap:12px;padding:9px 12px;margin:6px 0;border-radius:10px;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.08)}
.fighter.now{background:var(--tag-bg);border:1px solid var(--accent)}
.fighter.out{opacity:.5}
.fmeta{flex:1;min-width:0}
.fname{font-size:15px;font-weight:700;color:var(--text);display:flex;align-items:center;gap:6px}
.badge{font-size:11px;padding:1px 6px;border-radius:5px}
.b-now{background:var(--accent);color:var(--card2);font-weight:700}
.b-player{background:rgba(123,150,220,.45);color:var(--text)}
.b-enemy{background:rgba(220,90,90,.4);color:#ffe0e0}
.b-out{background:rgba(120,120,120,.5);color:var(--text2)}
.fstats{font-size:12px;color:var(--text2);margin-top:3px}
.hpbar{height:6px;background:var(--track);border-radius:4px;overflow:hidden;margin-top:5px}
.hpfill{height:100%;background:linear-gradient(90deg,var(--hp1),var(--hp2))}
.kv{display:flex;gap:14px;padding:6px 18px;flex-wrap:wrap}
.kv .it{font-size:13px}
.kv .it b{color:var(--accent)}
.empty{color:var(--text3);font-size:13px;padding:8px 18px}
.rulings{background:rgba(0,0,0,.14)}
.ruling{font-size:12px;color:var(--text2);line-height:1.6;padding:2px 0 2px 9px;border-left:2px solid var(--tag-border);margin:4px 0;word-break:break-word}
.board-wrap{padding:10px 18px}
.board-title{color:var(--accent);font-weight:700;margin-bottom:6px;text-align:center}
.board{display:grid;gap:1px;background:var(--board-bg);padding:3px;border-radius:8px;width:max-content;margin:0 auto}
.cell{position:relative;display:flex;align-items:center;justify-content:center;font-size:11px;color:var(--text3);overflow:hidden}
.t-floor{background:var(--t-floor)}
.t-difficult{background:var(--t-difficult)}
.t-cover_half{background:var(--t-cover_half)}
.t-cover_three{background:var(--t-cover_three)}
.t-blocked{background:var(--t-blocked);color:var(--text3)}
.t-hazard{background:var(--t-hazard);color:#e8b0a8}
.t-water{background:var(--t-water)}
.token{position:absolute;width:80%;height:80%;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;color:#fff;line-height:1}
.tok-player{background:var(--player)}
.tok-enemy{background:var(--enemy)}
.token.now{box-shadow:0 0 0 2px var(--accent),0 0 7px var(--accent)}
.token .cnt{position:absolute;right:-2px;top:-4px;font-size:9px;background:var(--accent);color:var(--card2);border-radius:6px;padding:0 2px;line-height:1.2}
.legend{display:flex;flex-wrap:wrap;gap:10px;font-size:11px;color:var(--text2);margin-top:8px;justify-content:center}
.legend span{display:inline-flex;align-items:center;gap:4px}
.swatch{width:11px;height:11px;border-radius:3px;display:inline-block}
.chip{display:inline-block;margin-left:8px;padding:1px 7px;border-radius:5px;font-size:11px;background:var(--tag-bg);border:1px solid var(--tag-border);color:var(--text)}
.chip-combat{background:rgba(220,90,90,.35);border-color:rgba(220,90,90,.62);color:#ffe3e3}
.story{padding:16px 20px 18px;font-size:14px;line-height:1.95;color:var(--text)}
.story p{margin:0 0 12px}
.story p:last-child{margin-bottom:0}

"""

#: 查询数据卡（/跑团.查 优先返回的图片）
_DATA_CARD_CSS = """
*{margin:0;padding:0;box-sizing:border-box}
body{width:540px;font-family:"PingFang SC","Microsoft YaHei","Noto Sans CJK SC","Heiti SC",sans-serif;background:var(--page);padding:14px}
.dcard{background:linear-gradient(160deg,var(--card1),var(--card2));border:1px solid var(--border);border-radius:14px;overflow:hidden;color:var(--text);box-shadow:0 6px 20px rgba(0,0,0,.5)}
.dhead{background:linear-gradient(135deg,var(--header1),var(--header2));padding:14px 18px}
.dtitle{font-size:20px;font-weight:700;color:var(--text)}
.dsub{font-size:12px;color:var(--text2);margin-top:4px;opacity:.92}
.dbody{padding:12px 18px;font-size:13px;line-height:1.7;color:var(--text)}
.dline{white-space:pre-wrap;word-break:break-word}
"""


def data_card_html(table: str, name: str) -> str | None:
    """把一项查询数据渲染成图片卡的 HTML；不支持的表或查不到时返回 ``None``。

    交给 :func:`~.handlers._render_html_image` 用 ``html_to_pic`` 转成图片；
    返回 ``None`` 时调用方退回纯文本（详见 :func:`data_card_parts` 的覆盖范围）。
    """
    try:
        from ..agents.dnd.tools import data_card_parts
    except Exception:
        return None
    parts = data_card_parts(table, name)
    if parts is None:
        return None
    title, subtitle, body = parts
    lines = "".join(f'<div class="dline">{_esc(ln)}</div>' for ln in body.splitlines())
    return (
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
        f"<style>{css_vars(get_theme())}{_DATA_CARD_CSS}</style></head><body>"
        f'<div class="dcard"><div class="dhead">'
        f'<div class="dtitle">{_esc(title)}</div>'
        f'<div class="dsub">{_esc(subtitle)}</div></div>'
        f'<div class="dbody">{lines}</div></div></body></html>'
    )


def _esc(text: object) -> str:
    return html.escape(str(text), quote=True)


def _rulings_html(rulings: Sequence[str] | None) -> str:
    """本轮判定明细（骰点 + 成败）：贴着卡面最底部列出来；没有就整块不画。"""
    items = [str(r).strip() for r in (rulings or []) if str(r or "").strip()]
    if not items:
        return ""
    rows = "".join(f'<div class="ruling">{_esc(item)}</div>' for item in items[:10])
    return f'<div class="section rulings"><div class="title">🎲 判定</div>{rows}</div>'


def combat_to_html(
    state: CombatState,
    *,
    conditions_by_cid: dict[str, str] | None = None,
    current_cid: str | None = None,
) -> str:
    """渲染一张战场图 HTML。

    :param conditions_by_cid: ``{参战者 cid: 状态文本}``——玩家的状态以角色卡为准，
        由调用方算好传进来（这里不碰存档）；敌人直接取 ``combatant.conditions``。
    :param current_cid: 当前正在行动的参战者 cid，用于高亮。

    判定明细不画在这里：叙述卡（:func:`narration_to_html`）本来就带着它，
    同一轮发两条消息时没必要说两遍。
    """
    conditions_by_cid = conditions_by_cid or {}
    by_init = sorted(state.order, key=lambda c: -c.initiative)

    rows: list[str] = []
    for c in by_init:
        is_now = c.cid == current_cid
        if c.out:
            cls = "fighter out"
        elif is_now:
            cls = "fighter now"
        else:
            cls = "fighter"

        badges = []
        if is_now:
            badges.append('<span class="badge b-now">行动中</span>')
        badges.append(
            '<span class="badge b-player">玩家</span>'
            if c.kind == "player"
            else '<span class="badge b-enemy">敌人</span>'
        )
        if c.out:
            badges.append('<span class="badge b-out">退场</span>')

        pct = max(0, min(100, int(100 * c.hp / max(1, c.max_hp))))
        cond = conditions_by_cid.get(c.cid) or "、".join(c.conditions)
        meta_bits = [f"先攻 {c.initiative}", f"AC {c.ac}"]
        if c.x >= 0:
            meta_bits.append(f"坐标 {c.x},{c.y}")
        if c.kind == "player":
            meta_bits.append(f"速度 {c.speed} 尺")
        if cond:
            meta_bits.append(f"状态：{cond}")
        if c.note:
            meta_bits.append(_esc(c.note))

        rows.append(
            f'<div class="{cls}">'
            f'<div class="fmeta">'
            f'<div class="fname">{_esc(c.name)}{"".join(badges)}</div>'
            f'<div class="fstats">{"　".join(meta_bits)}</div>'
            f'<div class="hpbar"><div class="hpfill" style="width:{pct}%"></div></div>'
            f"</div>"
            f'<div style="text-align:right;min-width:64px">'
            f'<div style="font-size:16px;font-weight:700;color:#ffd86b">{c.hp}/{c.max_hp}</div>'
            f'<div style="font-size:11px;color:#c3bce8">生命</div>'
            f"</div>"
            f"</div>"
        )

    fighters = "".join(rows) if rows else '<div class="empty">（还没有参战者）</div>'

    note = f'<div class="meta">{_esc(state.note)}</div>' if state.note else ""
    board = _board_html(state, current_cid)
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><style>{css_vars(get_theme())}{_PAGE_CSS}</style></head><body><div class="card">
<div class="header"><div class="name">⚔ 战场 · 第 {state.round} 轮</div>{note}</div>
{board}
<div class="cb">{fighters}</div>
</div></body></html>"""


def player_panel_html(
    card: DndCharacter,
    combatant: Combatant | None = None,
) -> str:
    """渲染一张玩家面板 HTML：装备（含数值）+ 法术（按环阶附简介）+ 本回合可用动作。

    :param combatant: 当前回合的战斗个体；提供时显示动作经济（动作 / 附赠动作 /
        反应 / 移动 / 法术位），没有则只显示装备与法术。
    """
    race = card.race_obj
    race_text = race.cn if race else "未知种族"
    if card.subrace_obj:
        race_text += f"·{card.subrace_obj.cn}"
    prof = card.profession_obj
    prof_text = prof.cn if prof else "无业"

    # 装备（含伤害骰 / AC）
    equip = card.equipment_display()
    equip_body = (
        "".join(f'<span class="tag">{_esc(x)}</span>' for x in equip)
        if equip
        else '<span class="muted">（空）</span>'
    )

    # 法术：按环阶分组，逐条附简介
    spell_parts: list[str] = []
    known = card.known_spells()
    if known:
        by_level: dict[int, list] = {}
        for s in known:
            by_level.setdefault(s.level, []).append(s)
        for lv in sorted(by_level):
            label = _LEVEL_LABELS.get(
                by_level[lv][0].level, f"{by_level[lv][0].level} 环"
            )
            spell_parts.append(f'<div class="sub">{_esc(label)}</div>')
            for s in by_level[lv]:
                line = _esc(s.cn)
                if s.desc:
                    line += "：" + _esc(s.desc)
                spell_parts.append(f'<div class="muted">{line}</div>')

    # 本回合可用动作
    action_parts: list[str] = []
    if combatant is not None:
        action_parts.append(
            f'<span class="it">动作 <b>{"已用" if combatant.action_used else "可用"}</b></span>'
        )
        action_parts.append(
            f'<span class="it">附赠动作 <b>{"已用" if combatant.bonus_used else "可用"}</b></span>'
        )
        action_parts.append(
            f'<span class="it">反应 <b>{"已用" if combatant.reaction_used else "可用"}</b></span>'
        )
        if combatant.moved:
            rest = max(0, combatant.speed - combatant.move_used)
            move_text = f"已走 {combatant.move_used}/{combatant.speed} 尺（剩 {rest}）"
        else:
            move_text = f"未移动（{combatant.speed} 尺）"
        action_parts.append(f'<span class="it">移动 <b>{_esc(move_text)}</b></span>')
    slots = card.spell_slots_text()
    if card.max_spell_slots():
        action_parts.append(
            f'<span class="it">法术位 <b>{_esc(slots or "（无）")}</b></span>'
        )

    spell_section = (
        '<div class="section"><div class="title">法术</div>'
        + "".join(spell_parts)
        + "</div>"
        if spell_parts
        else ""
    )
    action_section = (
        '<div class="section"><div class="title">本回合可用</div>'
        f'<div class="kv">{"".join(action_parts)}</div></div>'
        if action_parts
        else ""
    )

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><style>{css_vars(get_theme())}{_PAGE_CSS}</style></head><body><div class="card">
<div class="header"><div class="name">{_esc(card.name or "无名者")}</div>
<div class="meta">{_esc(race_text)} · {_esc(prof_text)} · Lv.{card.level}　HP {card.hp}/{card.max_hp}　AC {card.armor_class_with_effects}</div></div>
<div class="section"><div class="title">装备</div>{equip_body}</div>
{spell_section}
{action_section}
</div></body></html>"""


def narration_to_html(
    narration: str,
    *,
    time_text: str = "",
    place_text: str = "",
    event_text: str = "",
    combat: bool = False,
    rulings: Sequence[str] | None = None,
) -> str:
    """渲染一张 DM 叙述卡。

    :param narration: DM 的正文（按行拆段；空行不留空段落）
    :param time_text: 世界时间，如「第 3 天 14:20 · 下午」（现状见 :mod:`.clock`）
    :param place_text: 当前场景名（可带上级地点）
    :param event_text: 眼下这件事，如「【第 2 件事】黑帆疑云」
    :param combat: 是否在战斗中——是就在顶部挂一枚「⚔ 战斗中」徽章
    :param rulings: 判定明细（``TurnResult.rulings``）——战场图不再画这份，
        每轮只有这张卡会说一次骰点与成败
    """
    bits = [part for part in (time_text, place_text, event_text) if part]
    chips = '<span class="chip chip-combat">⚔ 战斗中</span>' if combat else ""
    meta_line = " ｜ ".join(bits)
    meta = (
        f'<div class="meta">{_esc(meta_line)}{chips}</div>'
        if meta_line or chips
        else ""
    )
    paras = "".join(
        f"<p>{_esc(line.strip())}</p>"
        for line in str(narration or "").splitlines()
        if line.strip()
    )
    body = paras or '<p class="muted">（DM 沉默了片刻）</p>'
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><style>{css_vars(get_theme())}{_PAGE_CSS}</style></head><body><div class="card">
<div class="header"><div class="name">✒ DM</div>{meta}</div>
<div class="story">{body}</div>
{_rulings_html(rulings)}
</div></body></html>"""
