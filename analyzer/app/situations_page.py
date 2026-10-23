"""Le bilan : tes résultats par situation (analyzer/situations.py), face à tes repères et aux réguliers de ton field,
et tes situations les plus fortes et les plus faibles."""
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
ul.sit-brief li { margin: 3px 0; }
button.sit-more { font: inherit; font-size: 11px; font-weight: 400; margin-left: 6px; padding: 0 6px; border-radius: 4px;
  border: 1px solid var(--border); background: var(--page); color: var(--ink-2); cursor: pointer; }
"""


def _bb(x: Optional[float], digits: int = 0) -> str:
    return "–" if x is None else num(x, digits, sign=True)


def _cell(x: Optional[float], digits: int = 0, extra: str = "", tone: Optional[float] = None) -> str:
    """Une valeur en bb/100 ; sa couleur suit tone (par défaut, son signe)."""
    tone = x if tone is None else tone
    css = "" if tone is None or abs(tone) < 0.05 else " pos" if tone > 0 else " neg"
    return f'<td class="num{css}">{_bb(x, digits)}{extra}</td>'


def _row(line: situations.Line, k: int, pairs: int = 0, references: bool = False) -> str:
    """Une ligne du tableau : une situation (avec de quoi déplier ses positions : pairs), ou une de ses positions."""
    if line.pair:
        name, attrs = escape(line.pair), f' class="pair" data-cat="{k}" hidden'
    else:
        more = (f'<button type="button" class="sit-more" data-cat="{k}" aria-expanded="false">{pairs} position'
                f'{"s" if pairs > 1 else ""} ▸</button>' if pairs else "")
        name, attrs = escape(line.group.label) + more, ' class="cat"'
    margin = f'<span class="mg">± {num(line.margin, 0)}</span>' if line.margin is not None else ""
    mine = _cell(line.winrate, extra=margin, tone=line.gap)  # vert au-dessus du repère, rouge en dessous
    ref = (f'<td class="num">{_bb(line.reference)}</td>' if references else "")
    field = (f'<td class="num">{_bb(line.field_winrate)}<span class="mg">{line.field.hands} pots</span></td>'
             if line.field.hands else '<td class="num muted">–</td>')
    note = "" if line.gap is None or line.clear else '<span class="mg">indicatif</span>'
    return (f'<tr{attrs}><td>{name}</td><td class="num">{line.mine.hands}</td>{mine}{ref}{field}'
            f"{_cell(line.gap, extra=note)}{_cell(line.impact, 1)}{_cell(line.weight, 1)}</tr>")


def _brief(report: situations.Report) -> str:
    ranked = report.ranked()
    out = []
    best = [x for x in ranked if x.impact > 0][:2]
    worst = [x for x in reversed(ranked) if x.impact < 0][:2]

    def say(x: situations.Line) -> str:
        versus = "ton repère" if x.reference is not None else "les réguliers du field"
        how = "" if x.clear else ", indicatif : dans la marge du hasard"
        return (f"<b>{escape(x.label)}</b> : {_bb(x.winrate)} bb/100 contre {_bb(x.base)} pour {versus}, soit "
                f"{_bb(x.impact, 1)} bb/100 sur ton winrate ({x.mine.hands} pots{how})")
    if worst:
        out.append("Là où tu es le plus faible : " + " ; ".join(say(x) for x in worst) + ".")
    if best:
        out.append("Là où tu es le plus fort : " + " ; ".join(say(x) for x in best) + ".")
    if report.pots < situations.SMALL:
        out.append(f"Échantillon encore petit ({report.pots} pots à deux au flop) : les écarts sont des pistes, pas des "
                   "verdicts.")
    if not out:
        return ""
    return '<ul class="sit-brief">' + "".join(f"<li>{x}</li>" for x in out) + "</ul>"


def situations_html(report: situations.Report) -> str:
    """La section repliable du bilan : l'essentiel, puis le tableau par situation et par positions."""
    if not report.groups:
        return ""
    rows = []
    for k, line in enumerate(report.groups):
        pairs = report.lines.get(line.group.key, [])
        rows.append(_row(line, k, len(pairs), report.references))
        rows += [_row(x, k, references=report.references) for x in pairs]
    ref = '<th class="num">Repère</th>' if report.references else ""
    versus = "au repère (sinon au field)" if report.references else "au field"
    table = ('<div class="scroll"><table class="stats sit-table"><thead><tr><th>Situation</th><th class="num">Pots</th>'
             f'<th class="num">Toi</th>{ref}<th class="num">Réguliers du field</th>'
             f'<th class="num">Écart {versus}</th><th class="num">Effet sur ton winrate</th>'
             f'<th class="num">Poids</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>')
    refs = (" Repère : ton winrate moyen de référence dans la situation (pour ses positions aussi) ; ton winrate est "
            "en vert au-dessus, en rouge en dessous." if report.references else "")
    note = ('<p class="note">Les pots joués à deux au flop après open, 3bet ou 4bet puis call (sans tapis avant le '
            "flop). IP : en position après le flop, OOP : hors de position ; PFR : le dernier relanceur préflop "
            "(l'attaquant), PFC : celui qui a payé (le défenseur). Winrates en bb/100 dans la situation (bb pour 100 "
            "pots), en EV all-in (le hasard des tapis retiré), avec la marge du hasard à 95 %." + refs + " Réguliers "
            "du field : leurs pots dans la même situation, dans tes historiques (contre toi ou contre d'autres). Effet "
            "sur ton winrate : l'écart ramené à toutes tes mains selon la fréquence de la situation (bb/100) ; poids : "
            "ce que la situation apporte à ton winrate. « indicatif » : l'écart reste dans la marge du hasard.</p>")
    hint = f"{report.pots} pots à deux au flop : SRP, 3bet, 4bet, IP ou OOP, PFR ou PFC" + (
        ", face à tes repères" if report.references else "")
    return (f"<style>{STYLE}</style>"
            + fold("Tes résultats par situation", f'<div class="card">{_brief(report)}{table}{note}</div>',
                   escape(hint), "situations") + f"<script>{SCRIPT}</script>")


# Déplier les positions d'une situation.
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
