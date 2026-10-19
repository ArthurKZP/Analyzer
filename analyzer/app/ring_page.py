"""Page « Tables à plusieurs » de Mon jeu › Mes mains : tes coups à deux joueurs au flop, à ouvrir au solveur ; et tes
stats position par position à toutes tes tables de 3 joueurs et plus (analyzer/ring.py), sur toutes tes mains, contre
les réguliers et contre les récréatifs, pour le bilan des tables à plusieurs (positions_html)."""
from __future__ import annotations

from html import escape
from typing import Optional
from urllib.parse import quote

from .. import ring
from ..report import cards_html, fold, format_query, html_page, num, pct_cell
from ..ring_leaks import FORMAT

STYLE = """<style>
.rg-switch { display: flex; gap: 6px; margin-top: 14px; }
.rg-switch button { font: inherit; font-size: 13px; padding: 4px 12px; border-radius: 999px; border: 1px solid var(--border);
  background: var(--surface); color: var(--ink-2); cursor: pointer; }
.rg-switch button[aria-pressed="true"] { background: var(--ink); color: var(--page); border-color: var(--ink); }
.rg-ref { color: var(--muted); font-size: 11px; display: block; }
table.stats td.pos { font-weight: 600; white-space: nowrap; }
table.stats td.nowrap { white-space: nowrap; }
a.rg-open { font-weight: 600; text-decoration: none; color: var(--series-1); white-space: nowrap; }
</style>"""

SCRIPT = """
document.querySelectorAll('.rg-load').forEach((b) => b.addEventListener('click', async () => {
  const msg = b.parentElement.querySelector('.rg-load-msg');
  b.disabled = true;
  msg.textContent = 'Téléchargement…';
  const res = await fetch('/api/ranges/hand2note', { method: 'POST' }).catch(() => null);
  const data = res ? await res.json().catch(() => ({})) : {};
  if (res && res.ok) location.reload();
  else { msg.textContent = data.error || 'Téléchargement impossible.'; b.disabled = false; }
}));
document.querySelectorAll('.rg-switch button').forEach((b) => b.addEventListener('click', () => {
  document.querySelectorAll('.rg-fmt').forEach((s) => { s.hidden = s.dataset.scope !== b.dataset.scope; });
  document.querySelectorAll('.rg-switch button').forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
}));
"""

PREFLOP = ("vpip", "pfr", "open", "limp", "threebet", "flat", "fold_3bet", "fold_steal", "threebet_steal")
POSTFLOP = ("cbet_hu", "cbet_multi", "fold_cbet", "wtsd", "wsd", "wwsf")


def _ref_text(r) -> str:
    return f'<span class="rg-ref">repère {r[0]:.0f}–{r[1]:.0f}&nbsp;%</span>' if r else ""


def _table(fs: ring.FormatStats, keys: tuple[str, ...], with_result: bool,
           positions: Optional[tuple[str, ...]] = None) -> str:
    """Une ligne par position (positions : seulement celles-là), puis toutes ensemble."""
    head = "".join(f'<th class="num">{escape(ring.LABEL[k])}</th>' for k in keys)
    lead = '<th class="num">Mains</th><th class="num">bb/100</th>' if with_result else ""
    rows = []
    shown = [ps for ps in fs.positions if positions is None or ps.position in positions]
    for ps in shown + [fs.total]:
        cells = []
        for k in keys:
            stat = ring.stat_def(fs.table_format, k, ps.position if ps is not fs.total else None)
            cell = pct_cell(ps.ratios[k], stat=stat)
            if stat.ref and k == "open" and ps is not fs.total:
                cell = cell.replace("</td>", _ref_text(stat.ref) + "</td>", 1)
            cells.append(cell)
        result = (f'<td class="num">{ps.hands}</td><td class="num">{num(ps.bb100, 1, sign=True)}</td>'
                  if with_result else "")
        style = ' style="border-top:2px solid var(--axis)"' if ps is fs.total else ""
        rows.append(f'<tr{style}><td class="pos">{escape(ps.position)}</td>{result}{"".join(cells)}</tr>')
    return (f'<div class="card scroll"><table class="stats"><thead><tr><th>Position</th>{lead}{head}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')


MAX_SPOTS = 60
WHY = (("Pas de range", "pas de range préflop"), ("Ta solution ne donne pas", "range manquante"),
       ("Un troisième joueur", "argent mort d'un 3e joueur"), ("Tapis préflop", "tapis préflop"))


def _why(status: str) -> str:
    short = next((label for prefix, label in WHY if status.startswith(prefix)), "non couvert")
    return f'<span class="muted" title="{escape(status)}">{escape(short)}</span>'


def _spots(rows: list[dict], ready: bool, opened: bool = False) -> str:
    """Les coups à deux joueurs au flop (toutes tables), à ouvrir au solveur postflop."""
    mine = rows
    if not mine:
        return ""
    body = "".join(
        f'<tr><td class="nowrap">{r["date"]:%d/%m %H:%M}</td><td class="nowrap">{escape(r["format"])}</td>'
        f'<td>{escape(r["line"])}</td>'
        f'<td class="pos">{escape(r["hero"])} <span class="muted">vs {escape(r["villain"])}</span></td>'
        f'<td>{cards_html(r["cards"])}</td><td>{cards_html(r["board"])}</td><td class="num">{num(r["total_bb"], 1)}</td>'
        f'<td class="num">{num(r["net_bb"], 1, sign=True)}</td>'
        + (f'<td><a class="rg-open" href="/explorateur/{quote(r["id"], safe="")}" target="_blank" rel="noopener">'
           f'Ouvrir au solveur ↗</a></td>' if r["status"] is None else f"<td>{_why(r['status'])}</td>")
        + "</tr>" for r in mine[:MAX_SPOTS])
    note = "" if ready else (
        '<p class="note">Tes ranges préflop ne sont pas encore là : ces coups s\'ouvriront au solveur dès qu\'elles '
        "seront ajoutées.</p>")
    if not ready:  # les charts 6-max couvrent aussi le 3-max et, place par place, les tables de 7 à 9 joueurs
        note += ('<p><button type="button" class="rg-load">Charger les charts 100 bb de Hand2Note Guide (6-max et 3-max)'
                 '</button> <span class="muted small">pots simples, 3bet et 4bet ; téléchargés sur ta machine, pour ton '
                 "usage personnel (conditions du site)</span> <span class=\"rg-load-msg small\"></span></p>")
    extra = f" Les {MAX_SPOTS} plus gros pots sur {len(mine)}." if len(mine) > MAX_SPOTS else ""
    table = ('<div class="card scroll"><table class="stats"><thead><tr><th>Date</th><th>Table</th>'
             '<th>Ligne préflop</th><th>Toi</th><th>Main</th><th>Flop</th><th class="num">Pot (bb)</th>'
             '<th class="num">Résultat</th>'
             f"<th>Solveur</th></tr></thead><tbody>{body}</tbody></table></div>"
             f'<p class="note">Résolus comme un coup heads-up à partir du flop, avec les ranges de ta solution préflop '
             "pour la ligne jouée (à une table sans solution à elle, les charts 6-max à même nombre de joueurs "
             f"derrière) ; le pot compte l'argent mort des joueurs qui ont foldé.{extra}</p>")
    return fold("Coups à deux joueurs au flop", note + table,
                f"{len(mine)} coup{'s' if len(mine) > 1 else ''} à ouvrir au solveur, les plus gros pots d'abord",
                "coups", opened=opened or not ready)


def _gaps(found: Optional[list]) -> str:
    """Tes écarts à la théorie les plus importants contre les réguliers (charts, plans de jeu des flops résolus), en
    tête de page ; le détail est dans le Leakfinding des tables à plusieurs."""
    if found is None:
        return ""
    from .leaks_page import gaps_table
    link = (f'<a href="leaks{escape(format_query(FORMAT))}">Le Leakfinding des tables à plusieurs</a> donne le détail de '
            "toutes tes stats, les mains à passer au solveur et les leaks à travailler.")
    if not found:
        body = ('<p class="muted">Aucun écart net à la théorie contre les réguliers pour l\'instant (charts avant le '
                "flop, plans de jeu des flops résolus après) : pas assez d'occasions, ou un jeu proche de la théorie. "
                f"{link}</p>")
    else:
        body = (gaps_table(found, "reg", "Contre réguliers") + '<p class="note">Contre les réguliers, comparé aux charts '
                "avec les mêmes cartes (avant le flop) et aux plans de jeu des flops 6-max résolus (après le flop, pots à "
                "deux joueurs) ; les nets d'abord, puis selon la taille de l'écart, sa fréquence et ce qu'il met en jeu. "
                f"{link}</p>")
    return f'<h2>Tes écarts les plus importants</h2><div class="card">{body}</div>'


LABELS = {"all": "Toutes tes mains", "reg": "Contre les réguliers", "rec": "Contre les récréatifs"}


def positions_html(shown: list[tuple[str, str, ring.FormatStats]], positions: Optional[tuple[str, ...]] = None,
                   preflop: tuple[str, ...] = PREFLOP, postflop: tuple[str, ...] = POSTFLOP) -> str:
    """Tes stats position par position (avant et après le flop), une portée à la fois : shown, [(portée, libellé,
    stats de toutes tes tables ensemble)] — toutes tes mains, contre les réguliers, contre les récréatifs ; positions :
    seulement celles-là (ring.SIX_MAX : le bilan). Avec STYLE et SCRIPT."""
    switch = ('<div class="rg-switch" role="group" aria-label="Adversaires">' + "".join(
        f'<button type="button" data-scope="{escape(scope)}" aria-pressed="{str(k == 0).lower()}">'
        f"{escape(LABELS.get(scope, label))} ({fs.total.hands})</button>" for k, (scope, label, fs) in enumerate(shown))
        + "</div>") if len(shown) > 1 else ""
    sections = "".join(
        f'<section class="rg-fmt" data-scope="{escape(scope)}"{"" if k == 0 else " hidden"}>'
        f'<h3>Préflop par position</h3>{_table(fs, preflop, True, positions)}'
        f'<h3>Après le flop</h3>{_table(fs, postflop, False, positions)}</section>'
        for k, (scope, _, fs) in enumerate(shown))
    only = (" Les positions d'une table 6-max seulement (les autres, à 7-9 joueurs : onglet Statistiques) ; « Toutes » "
            "compte toutes tes mains." if positions else "")
    note = ('<p class="note">Une position compte ses mains de chaque taille de table. Repères indicatifs d\'un régulier '
            "en 6-max à 100 bb ; chiffres en gris : moins de 15 occasions. Open : tu parles le premier. Vol : ouverture "
            "du CO, du bouton ou de la SB sans caller ; la défense se lit en SB et BB. C-bet : tu étais le dernier "
            "relanceur préflop et on te laisse miser au flop. WWSF : tu as gagné le pot après avoir vu le flop."
            f"{only}</p>")
    return switch + sections + note


def build_ring_page(scopes: list[tuple[str, str, list]], hero: str, embed: bool = True,
                    spots: Optional[list[dict]] = None, ranges: Optional[dict[str, int]] = None) -> str:
    """Tes coups à deux joueurs au flop aux tables à plusieurs, à ouvrir au solveur. scopes : [(portée, libellé,
    ring.analyze(…, merge=True))] — toutes tes mains, contre les réguliers, contre les récréatifs ; spots :
    Library.ring_spots() ; ranges : formats dont la solution préflop est là (ring_ranges.available)."""
    shown = [(scope, label, stats[0]) for scope, label, stats in scopes if stats]
    if not shown:
        body = ('<p class="note">Aucune main à une table de 3 joueurs ou plus. Importe des historiques 3-max, 6-max ou de '
                "7 à 9 joueurs : tes stats par position s'afficheront ici.</p>")
        if not ranges:  # les charts servent aussi à l'explorateur (coup à plusieurs), même sans mains
            body += ('<p><button type="button" class="rg-load">Charger les charts 100 bb de Hand2Note Guide (6-max et '
                     '3-max)</button> <span class="muted small">pour étudier les coups à plusieurs dans l\'explorateur ; '
                     'téléchargés sur ta machine, pour ton usage personnel (conditions du site)</span> '
                     '<span class="rg-load-msg small"></span></p>')
        return html_page("Tables à plusieurs", body, embed, script=SCRIPT)
    heading = "" if embed else f"<h1>Tables à plusieurs — {escape(hero)}</h1>"
    fs = shown[0][2]
    period = f"{fs.first:%d/%m/%Y} → {fs.last:%d/%m/%Y} · " if fs.first else ""
    meta = (f'<div class="meta">{fs.total.hands} mains aux tables à plusieurs ({escape(", ".join(fs.formats))}) · '
            f"{period}tes stats position par position et tes écarts à la théorie sont dans le "
            f'<a href="bilan{escape(format_query(FORMAT))}">bilan des tables à plusieurs</a> et le '
            f'<a href="leaks{escape(format_query(FORMAT))}">Leakfinding</a>.</div>')
    found = _spots(spots or [], bool(ranges), opened=True)
    if not found:
        found = ('<p class="note">Aucun coup à deux joueurs au flop pour l\'instant : tes pots à plusieurs joueurs au '
                 "flop ne se résolvent pas.</p>")
    return html_page(f"Tables à plusieurs — {hero}", STYLE + heading + meta + found, embed, script=SCRIPT)
