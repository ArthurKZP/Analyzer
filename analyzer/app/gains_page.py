"""Le filtre des gains du bilan (limite, room, mois, moment du mois, créneau horaire) et la répartition de tes gains
selon chacune de ces dimensions (analyzer/gains.py)."""
from __future__ import annotations

from html import escape

from .. import gains
from ..models import Hand
from ..report import fold, num

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
.gn-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 12px; }
.gn-grid table { width: 100%; }
.gn-grid tr.on td { font-weight: 600; background: var(--hi-bg); }
.gn-grid td a { color: inherit; }
.gn-grid .pm { color: var(--muted); font-size: 11px; margin-left: 4px; white-space: nowrap; }
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

NOTE = ("Le filtre s'applique à tes résultats (les tuiles, la courbe, cette répartition) ; tes stats et tes écarts "
        "restent calculés sur toutes tes mains de la période. Chaque tableau compare les valeurs d'une dimension sur "
        "les mains que les autres filtres gardent. ± : la marge du hasard (95 %) sur le winrate ; tant qu'elle dépasse "
        "l'écart entre deux lignes, cet écart peut n'être que de la variance. L'heure est celle de tes historiques.")


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


def _table(hands: list[Hand], hero: str, dimension: str, filters: dict[str, str], fmt: str) -> str:
    rows = gains.breakdown(hands, hero, dimension, filters)
    if not rows:
        return ""
    chosen = filters.get(dimension)
    body = []
    for r in rows:
        link = _href(fmt, {**filters, dimension: r.value})
        margin = f'<span class="pm">± {num(r.margin, 0)}</span>' if r.margin is not None else ""
        on = ' class="on"' if r.value == chosen else ""
        body.append(f'<tr{on}><td><a href="{link}">{escape(r.label)}</a></td>'
                    f'<td class="num">{r.hands}</td><td class="num">{num(r.net_bb, 1, sign=True)}</td>'
                    f'<td class="num">{num(r.bb100, 1, sign=True)}{margin}</td>'
                    f'<td class="num">{num(r.ev100, 1, sign=True)}</td>'
                    f'<td class="num">{num(r.net, 2, sign=True)}</td></tr>')
    return (f'<div class="card scroll"><h3>{escape(gains.NAMES[dimension])}</h3><table class="stats"><thead><tr>'
            '<th></th><th class="num">Mains</th><th class="num">bb</th><th class="num">bb/100</th>'
            '<th class="num" title="EV all-in">EV/100</th><th class="num">€</th></tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')


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
