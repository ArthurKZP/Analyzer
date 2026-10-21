"""Le bilan : tes résultats par situation (analyzer/situations.py), face aux réguliers de ton field, et tes situations
les plus fortes et les plus faibles."""
from __future__ import annotations

from html import escape
from typing import Optional

from .. import situations
from ..report import fold, num

STYLE = """
table.sit-table tr.cat td { font-weight: 600; border-top: 1px solid var(--axis); }
table.sit-table tr.pair td:first-child { padding-left: 20px; }
table.sit-table td.pos { color: var(--good); } table.sit-table td.neg { color: var(--alert); }
table.sit-table .mg { color: var(--muted); font-size: 11px; display: block; font-weight: 400; }
ul.sit-brief { margin: 4px 0 8px; padding-left: 18px; }
button.sit-more { font: inherit; font-size: 11px; font-weight: 400; margin-left: 6px; padding: 0 6px; border-radius: 4px;
  border: 1px solid var(--border); background: var(--page); color: var(--ink-2); cursor: pointer; }
ul.sit-brief li { margin: 3px 0; }
"""


def _bb(x: Optional[float], digits: int = 2) -> str:
    return "–" if x is None else f"{num(x, digits, sign=True)}"


def _cell(x: Optional[float], digits: int = 2, extra: str = "") -> str:
    tone = "" if x is None or abs(x) < 0.005 else " pos" if x > 0 else " neg"
    return f'<td class="num{tone}">{_bb(x, digits)}{extra}</td>'


def _row(line: situations.Line, k: int, pairs: int = 0) -> str:
    """Une ligne du tableau : une catégorie (avec de quoi déplier ses positions : pairs), ou une de ses positions."""
    if line.pair:
        name, attrs = escape(line.pair), f' class="pair" data-cat="{k}" hidden'
    else:
        more = (f'<button type="button" class="sit-more" data-cat="{k}" aria-expanded="false">{pairs} position'
                f'{"s" if pairs > 1 else ""} ▸</button>' if pairs else "")
        name, attrs = escape(situations.category_label(line.category)) + more, ' class="cat"'
    margin = f'<span class="mg">± {num(line.margin, 2)}</span>' if line.margin is not None else ""
    field = (f'<td class="num">{_bb(line.field_per_pot)}<span class="mg">{line.field.hands} pots</span></td>'
             if line.field.hands else '<td class="num muted">–</td>')
    gap = _cell(line.gap, extra="" if line.clear or line.gap is None else '<span class="mg">indicatif</span>')
    ref = (f'<td class="num">{_bb(line.reference)}</td>' if situations.REFERENCES else "")
    return (f'<tr{attrs}><td>{name}</td><td class="num">{line.mine.hands}</td>{_cell(line.per_pot, extra=margin)}'
            f"{field}{gap}{_cell(line.impact, 1)}{_cell(line.weight, 1)}{ref}</tr>")


def _brief(report: situations.Report) -> str:
    ranked = report.ranked()
    out = []
    best = [x for x in ranked if x.impact > 0][:2]
    worst = [x for x in reversed(ranked) if x.impact < 0][:2]

    def say(x: situations.Line) -> str:
        how = "" if x.clear else " (indicatif : dans la marge du hasard)"
        return (f"<b>{escape(x.label)}</b> : {_bb(x.per_pot)} bb par pot contre {_bb(x.field_per_pot)} pour les "
                f"réguliers, soit {_bb(x.impact, 1)} bb/100 sur ton winrate ({x.mine.hands} pots){how}")
    if worst:
        out.append("Là où tu perds le plus face aux réguliers : " + " ; ".join(say(x) for x in worst) + ".")
    if best:
        out.append("Là où tu gagnes le plus face à eux : " + " ; ".join(say(x) for x in best) + ".")
    if report.pots < situations.SMALL:
        out.append(f"Échantillon encore petit ({report.pots} pots à deux au flop) : les écarts sont des pistes, pas des "
                   "verdicts.")
    if not out:
        return ""
    return '<ul class="sit-brief">' + "".join(f"<li>{x}</li>" for x in out) + "</ul>"


def situations_html(report: situations.Report) -> str:
    """La section repliable du bilan : l'essentiel, puis le tableau par catégorie et par positions."""
    if not report.categories:
        return ""
    rows = []
    for k, cat in enumerate(report.categories):
        pairs = report.lines.get(cat.category, [])
        rows.append(_row(cat, k, len(pairs)))
        rows += [_row(x, k) for x in pairs]
    ref = '<th class="num">Repère</th>' if situations.REFERENCES else ""
    table = ('<div class="scroll"><table class="stats sit-table"><thead><tr><th>Situation</th><th class="num">Pots</th>'
             '<th class="num">Toi (bb/pot)</th><th class="num">Réguliers du field</th><th class="num">Écart</th>'
             f'<th class="num">Écart (bb/100)</th><th class="num">Poids (bb/100)</th>{ref}</tr></thead>'
             f'<tbody>{"".join(rows)}</tbody></table></div>')
    note = ('<p class="note">Les pots joués à deux au flop après open, 3bet ou 4bet puis call (sans tapis avant le '
            "flop). IP : en position après le flop ; attaquant : le dernier relanceur préflop, défenseur : celui qui a "
            "payé. Résultats en EV all-in (le hasard des tapis retiré), en bb par pot, avec la marge du hasard à 95 %. "
            "Réguliers du field : leurs pots dans la même situation, dans tes historiques (contre toi ou contre "
            "d'autres). Écart (bb/100) : ton écart avec eux, ramené à toutes tes mains selon la fréquence de la "
            "situation ; poids : ce que la situation apporte à ton winrate. « indicatif » : l'écart reste dans la marge "
            "du hasard.</p>")
    hint = f"{report.pots} pots à deux au flop : SRP, 3bet, 4bet, en position ou non, attaquant ou défenseur"
    return (f"<style>{STYLE}</style>"
            + fold("Tes résultats par situation", f'<div class="card">{_brief(report)}{table}{note}</div>',
                   escape(hint), "situations") + f"<script>{SCRIPT}</script>")


# Déplier les positions d'une catégorie.
SCRIPT = """
document.querySelectorAll('button.sit-more').forEach(function (b) {
  b.addEventListener('click', function () {
    var open = b.getAttribute('aria-expanded') !== 'true';
    b.setAttribute('aria-expanded', String(open));
    b.textContent = b.textContent.replace(open ? '▸' : '▾', open ? '▾' : '▸');
    document.querySelectorAll('tr.pair[data-cat="' + b.dataset.cat + '"]').forEach(function (r) { r.hidden = !open; });
  });
});
"""
