"""Le filtre des gains du bilan (limite, room, mois, moment du mois, créneau horaire) et la répartition de tes gains
selon chacune de ces dimensions (analyzer/gains.py)."""
from __future__ import annotations

from html import escape

from .. import gains
from ..models import Hand
from ..report import fold, more, num

STYLE = """
.gf { display: flex; flex-wrap: wrap; gap: 8px 12px; align-items: flex-end; margin: 4px 0 14px; padding: 10px 12px;
  background: var(--surface); border: 1px solid var(--border); border-radius: 10px; }
.gf .gf-title { font-size: 13px; font-weight: 600; color: var(--ink-2); width: 100%; }
.gf label { display: flex; flex-direction: column; gap: 2px; font-size: 12px; color: var(--muted); }
.gf select { font: inherit; font-size: 13px; padding: 4px 8px; border-radius: 6px; border: 1px solid var(--border);
  background: var(--page); color: var(--ink); max-width: 220px; }
.gf select.on { border-color: var(--ink); font-weight: 600; }
.gf a { font-size: 13px; margin-left: auto; align-self: center; }
.gf-on { font-size: 13px; color: var(--ink-2); margin: -6px 0 10px; }
.gn-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(460px, 100%), 1fr)); gap: 12px; }
.gn-grid h3 { margin: 0 0 8px; }
.gn-rows { display: flex; flex-direction: column; gap: 2px; }
.gn-row { display: grid; grid-template-columns: minmax(118px, 1.3fr) 56px minmax(120px, 2.4fr) 112px; gap: 10px;
  align-items: center; padding: 3px 6px; border-radius: 6px; color: inherit; text-decoration: none; font-size: 13px; }
a.gn-row:hover { background: var(--surface); }
.gn-row.on { background: var(--hi-bg); font-weight: 600; }
.gn-row.few { color: var(--muted); }
.gn-row.head { font-size: 11px; color: var(--muted); padding-top: 0; padding-bottom: 0; }
.gn-row .n, .gn-row .v { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.gn-row .v .pm { color: var(--muted); font-size: 11px; font-weight: 400; margin-left: 3px; }
.gn-bar { position: relative; height: 14px; }
.gn-bar::before { content: ""; position: absolute; left: 50%; top: -2px; bottom: -2px; width: 1px;
  background: var(--axis); }
.gn-bar i { position: absolute; top: 3px; height: 8px; }
.gn-bar i.up { background: var(--good); border-radius: 0 4px 4px 0; }
.gn-bar i.down { background: var(--alert); border-radius: 4px 0 0 4px; }
.gn-row.few .gn-bar i { opacity: .45; }
.gn-bar b { position: absolute; top: 6px; height: 2px; background: var(--ink-2); opacity: .55; }
.gn-bar b::before, .gn-bar b::after { content: ""; position: absolute; top: -3px; height: 8px; width: 1px;
  background: var(--ink-2); }
.gn-bar b::before { left: 0; } .gn-bar b::after { right: 0; }
.gn-bar .clip { position: absolute; top: -1px; font-size: 11px; color: var(--muted); }
.gn-scale { display: flex; justify-content: space-between; font-size: 11px; color: var(--muted); }
"""

# Un choix applique le filtre aussitôt (les valeurs vides ne vont pas dans l'adresse).
SCRIPT = """
document.querySelectorAll('form.gf').forEach(function (f) {
  f.addEventListener('change', function () {
    var q = new URLSearchParams();
    new FormData(f).forEach(function (v, k) { if (v) q.set(k, v); });
    location.search = q.toString();
  });
});
"""

FEW = 300  # mains : en dessous, un winrate dit surtout la variance (ligne pâle, hors de l'échelle)

NOTE = ("Le filtre s'applique à tes résultats (les tuiles, la courbe, cette répartition) ; tes stats et tes écarts "
        "restent calculés sur toutes tes mains de la période. Chaque bloc compare les valeurs d'une dimension sur "
        "les mains que les autres filtres gardent : la barre est le winrate (bb/100, vert au-dessus de zéro, rouge "
        "en dessous), le trait qui la traverse sa marge du hasard (95 %) ; tant que les traits de deux lignes se "
        f"recouvrent, leur écart peut n'être que de la variance. Lignes pâles : moins de {FEW} mains, hors de "
        "l'échelle (‹ › : la barre déborde). Un clic sur une ligne filtre sur elle ; le survol et « Les chiffres » "
        "donnent les bb, l'EV all-in et les euros. L'heure est celle de tes historiques.")


def _href(fmt: str, filters: dict[str, str]) -> str:
    q = gains.query(filters)
    return f"?format={escape(fmt)}" + (f"&amp;{escape(q)}" if q else "")


def _shown(hands: list[Hand], filters: dict[str, str]) -> list[tuple[str, str]]:
    """Les dimensions utiles : celles où tes mains ont au moins deux valeurs (ou déjà filtrées)."""
    return [(d, name) for d, name in gains.DIMENSIONS
            if d in filters or len({gains.key(h, d) for h in hands}) > 1]


def filter_bar(hands: list[Hand], filters: dict[str, str], fmt: str) -> str:
    """Les listes du filtre (avec le nombre de mains de chaque valeur), et le lien pour tout afficher."""
    shown = _shown(hands, filters)
    if not shown:
        return ""
    boxes = []
    for dimension, name in shown:
        chosen = filters.get(dimension, "")
        opts = "".join(f'<option value="{escape(v)}"{" selected" if v == chosen else ""}>{escape(text)} ({n})</option>'
                       for v, text, n in gains.options(hands, dimension, filters))
        on = ' class="on"' if chosen else ""
        boxes.append(f'<label>{escape(name)}<select name="{dimension}"{on}>'
                     f'<option value="">{gains.EVERY[dimension]}</option>{opts}</select></label>')
    reset = f'<a href="?format={escape(fmt)}">Tout afficher</a>' if filters else ""
    return (f'<form class="gf" method="get" aria-label="Filtrer tes gains"><span class="gf-title">Filtrer tes gains'
            f'</span><input type="hidden" name="format" value="{escape(fmt)}">{"".join(boxes)}{reset}</form>')


def active_note(filters: dict[str, str], kept: int, among: int) -> str:
    """« Filtre : NL500 · Soir — 812 mains sur 2 340. »"""
    if not filters:
        return ""
    plural = "s" if kept > 1 else ""
    return f'<p class="gf-on">Filtre : <b>{escape(gains.describe(filters))}</b> — {kept} main{plural} sur {among}.</p>'


def _scale(rows: list) -> float:
    """La moitié de la largeur des barres, en bb/100 : le plus grand winrate (marge comprise) des lignes d'au moins
    FEW mains (toutes, s'il n'y en a pas), arrondi au-dessus ; les autres débordent (marquées)."""
    solid = [r for r in rows if r.hands >= FEW] or rows
    top = max((abs(r.bb100 or 0.0) + (r.margin or 0.0) for r in solid), default=0.0)
    for step in (5, 10, 20, 25, 50, 100, 200, 250, 500, 1000):
        if top <= step:
            return float(step)
    return float(top)


def _bar(r, scale: float) -> str:
    """La barre du winrate (verte au-dessus de zéro, rouge en dessous) et sa marge (± 95 %), à l'échelle."""
    def x(v: float) -> float:  # position en % de la largeur, zéro au milieu
        return max(0.0, min(100.0, 50 + 50 * v / scale))
    value = r.bb100 or 0.0
    left, right = sorted((x(0.0), x(value)))
    width = max(right - left, 0.6)
    out = f'<i class="{"up" if value >= 0 else "down"}" style="left:{left:.1f}%;width:{width:.1f}%"></i>'
    if r.margin:
        lo, hi = x(value - r.margin), x(value + r.margin)
        out += f'<b style="left:{lo:.1f}%;width:{max(hi - lo, 0.5):.1f}%"></b>'
    if abs(value) > scale:
        out += f'<span class="clip" style="{"right:0" if value > 0 else "left:0"}">{"›" if value > 0 else "‹"}</span>'
    return f'<span class="gn-bar">{out}</span>'


def _detail(r) -> str:
    """Le détail d'une ligne, au survol."""
    parts = [f"{r.hands} mains", f"{num(r.net_bb, 1, sign=True)} bb", f"{num(r.bb100, 1, sign=True)} bb/100"
             + (f" (± {num(r.margin, 0)})" if r.margin else ""),
             f"EV all-in {num(r.ev100, 1, sign=True)} bb/100", f"{num(r.net, 2, sign=True)} €"]
    return " · ".join(parts)


def _table(hands: list[Hand], hero: str, dimension: str, filters: dict[str, str], fmt: str) -> str:
    """Une dimension : une ligne par valeur — ses mains, la barre de son winrate (bb/100, marge à 95 %) et sa valeur ;
    un clic filtre sur elle. Le tableau complet (bb, EV, €) se déplie dessous."""
    rows = gains.breakdown(hands, hero, dimension, filters)
    if not rows:
        return ""
    chosen = filters.get(dimension)
    scale = _scale(rows)
    lines, table = [], []
    for r in rows:
        link = _href(fmt, {**filters, dimension: r.value})
        cls = "gn-row" + (" on" if r.value == chosen else "") + (" few" if r.hands < FEW else "")
        margin = f'<span class="pm">± {num(r.margin, 0)}</span>' if r.margin else ""
        lines.append(f'<a class="{cls}" href="{link}" title="{escape(_detail(r))}"><span>{escape(r.label)}</span>'
                     f'<span class="n">{num(r.hands, 0)}</span>{_bar(r, scale)}'
                     f'<span class="v">{num(r.bb100, 1, sign=True)}{margin}</span></a>')
        table.append(f'<tr><td>{escape(r.label)}</td><td class="num">{r.hands}</td>'
                     f'<td class="num">{num(r.net_bb, 1, sign=True)}</td><td class="num">{num(r.bb100, 1, sign=True)}'
                     f'</td><td class="num">{num(r.ev100, 1, sign=True)}</td><td class="num">{num(r.net, 2, sign=True)}'
                     '</td></tr>')
    head = ('<div class="gn-row head" aria-hidden="true"><span></span><span class="n">mains</span>'
            f'<span class="gn-scale"><span>−{num(scale, 0)}</span><span>0</span><span>+{num(scale, 0)}</span></span>'
            '<span class="v">bb/100</span></div>')
    numbers = more("Les chiffres", '<div class="scroll"><table class="stats"><thead><tr><th></th>'
                   '<th class="num">Mains</th><th class="num">bb</th><th class="num">bb/100</th>'
                   '<th class="num" title="EV all-in">EV/100</th><th class="num">€</th></tr></thead>'
                   f'<tbody>{"".join(table)}</tbody></table></div>')
    return (f'<div class="card"><h3>{escape(gains.NAMES[dimension])}</h3><div class="gn-rows">{head}{"".join(lines)}'
            f'</div>{numbers}</div>')


def breakdown_html(hands: list[Hand], hero: str, filters: dict[str, str], fmt: str) -> str:
    """Tes gains par limite, room, mois, moment du mois et créneau horaire (une section repliable)."""
    if not hands:
        return ""
    shown = _shown(hands, filters)
    if not shown:
        return ""
    tables = "".join(_table(hands, hero, d, filters, fmt) for d, _ in shown)
    hint = ", ".join(gains.NAMES[d].lower() for d, _ in shown)
    return fold("Tes gains en détail", f'<div class="gn-grid">{tables}</div><p class="note">{escape(NOTE)}</p>',
                f"par {hint}", "gains")
