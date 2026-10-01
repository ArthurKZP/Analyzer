"""Page « Études du solveur » : les coups résolus avec GTOpen, gardés sur disque pour être réexplorés."""
from __future__ import annotations

from html import escape
from urllib.parse import quote

from ..report import cards_html, html_page, num
from ..theory import postflop

STYLE = """
.studies td.actions { white-space: nowrap; text-align: right; }
.studies a.open { font-weight: 600; text-decoration: none; }
.studies button.del { background: none; border: 1px solid var(--border, rgba(0,0,0,0.1)); border-radius: 6px; padding: 2px 8px;
  cursor: pointer; color: var(--muted, #898781); margin-left: 8px; font: inherit; font-size: 12px; }
.studies button.del:hover { color: var(--loss, #d03b3b); border-color: currentColor; }
"""

SCRIPT = """
document.querySelectorAll('button.del').forEach(function (b) {
  b.addEventListener('click', function () {
    if (!confirm('Supprimer cette étude ? Il faudra résoudre le coup à nouveau pour l\\'explorer.')) return;
    fetch('/api/etudes/supprimer', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key: b.dataset.key }) }).then(function () { location.reload(); });
  });
});
"""


def _size(n: int) -> str:
    return f"{num(n / 1e6, 0)} Mo" if n < 1e9 else f"{num(n / 1e9, 1)} Go"


def build_studies_page(embed: bool = True) -> str:
    studies = postflop.list_studies()
    total = sum(s["size"] for s in studies)
    rows = []
    for s in studies:
        exploit = s.get("exploit_pct")
        rows.append(
            "<tr>"
            f"<td>{escape(s['date'])}</td><td>{escape(s['villain'])}</td>"
            f"<td>{cards_html(s.get('hero_cards', []))}</td><td>{cards_html(s['board'])}</td>"
            f"<td>{escape(s['pot_type'])}</td><td>{'BB' if s['hero_position'] == 'BB' else 'Bouton'}</td>"
            f'<td class="num">{num(s["net"], 1, sign=True)} bb</td>'
            f'<td class="num">{num(exploit, 2) + " %" if exploit is not None else "–"}</td>'
            f'<td class="num">{_size(s["size"])}</td>'
            f'<td class="actions"><a class="open" href="/explorateur/{quote(s["hand"], safe="")}" target="_blank" '
            f'rel="noopener">Ouvrir ↗</a><button type="button" class="del" data-key="{escape(s["key"])}">Supprimer</button></td>'
            "</tr>"
        )
    if rows:
        table = ('<div class="scroll"><table class="stats studies"><thead><tr><th>Coup</th><th>Adversaire</th>'
                 '<th>Ta main</th><th>Board</th><th>Pot</th><th>Position</th><th class="num">Résultat</th>'
                 '<th class="num">Précision</th><th class="num">Taille</th><th></th></tr></thead><tbody>'
                 + "".join(rows) + "</tbody></table></div>")
    else:
        table = ('<p class="muted">Aucune étude pour l\'instant. Ouvre un coup dans <b>Spots</b> et clique sur '
                 '<b>Résoudre ce coup</b> : la résolution est gardée ici.</p>')
    body = f"""
<div class="meta">{len(studies)} étude(s) · {_size(total)} sur le disque · {escape(str(postflop.studies_dir()))}</div>
<div class="card"><p class="note" style="margin-top:0">Chaque coup résolu est gardé : l'explorateur le rouvre en quelques
secondes, sans recalculer. Précision : exploitabilité de la solution, en % du pot (plus c'est bas, plus elle est
proche de l'équilibre).</p>{table}</div>
"""
    return html_page("Études du solveur", f"<style>{STYLE}</style>{body}", embed, script=SCRIPT)
