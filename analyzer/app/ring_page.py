"""Page « Tables à plusieurs » de Mon jeu : tes stats par position à toutes tes tables de 3 joueurs et plus
(analyzer/ring.py), sur toutes tes mains, contre les réguliers et contre les récréatifs, après tes écarts à la théorie
les plus importants contre les réguliers (analyzer/ring_leaks.py, le détail dans le Leakfinding)."""
from __future__ import annotations

from html import escape
from typing import Optional
from urllib.parse import quote

from .. import ring
from ..report import cards_html, format_query, html_page, num, pct_cell

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
POSTFLOP = ("cbet_hu", "cbet_multi", "fold_cbet", "wtsd", "wsd")


def _ref_text(r) -> str:
    return f'<span class="rg-ref">repère {r[0]:.0f}–{r[1]:.0f}&nbsp;%</span>' if r else ""


def _table(fs: ring.FormatStats, keys: tuple[str, ...], with_result: bool) -> str:
    head = "".join(f'<th class="num">{escape(ring.LABEL[k])}</th>' for k in keys)
    lead = '<th class="num">Mains</th><th class="num">bb/100</th>' if with_result else ""
    rows = []
    for ps in fs.positions + [fs.total]:
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


def _refs(fs: ring.FormatStats) -> str:
    rows = []
    for key, label in ring.STATS:
        stat = ring.stat_def(fs.table_format, key)
        r = fs.total.ratios[key]
        if stat.ref and r.opps:
            rows.append(f"<tr><td>{escape(label)}</td>{pct_cell(r, stat=stat)}"
                        f'<td class="num muted">{stat.ref[0]:.0f}–{stat.ref[1]:.0f}&nbsp;%</td></tr>')
    if not rows:
        return ""
    return ('<h2>Toutes positions face aux repères</h2><div class="card"><table class="stats"><thead><tr><th></th>'
            f'<th class="num">Toi</th><th class="num">Repère</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>')


MAX_SPOTS = 60
WHY = (("Pas de range", "pas de range préflop"), ("Ta solution ne donne pas", "range manquante"),
       ("Un troisième joueur", "argent mort d'un 3e joueur"), ("Tapis préflop", "tapis préflop"))


def _why(status: str) -> str:
    short = next((label for prefix, label in WHY if status.startswith(prefix)), "non couvert")
    return f'<span class="muted" title="{escape(status)}">{escape(short)}</span>'


def _spots(rows: list[dict], ready: bool) -> str:
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
    more = f" Les {MAX_SPOTS} plus gros pots sur {len(mine)}." if len(mine) > MAX_SPOTS else ""
    return (f"<h2>Coups à deux joueurs au flop</h2>{note}"
            '<div class="card scroll"><table class="stats"><thead><tr><th>Date</th><th>Table</th><th>Ligne préflop</th>'
            '<th>Toi</th><th>Main</th><th>Flop</th><th class="num">Pot (bb)</th><th class="num">Résultat</th>'
            f"<th>Solveur</th></tr></thead><tbody>{body}</tbody></table></div>"
            f'<p class="note">Résolus comme un coup heads-up à partir du flop, avec les ranges de ta solution préflop '
            "pour la ligne jouée (à une table sans solution à elle, les charts 6-max à même nombre de joueurs derrière) "
            f"; le pot compte l'argent mort des joueurs qui ont foldé.{more}</p>")


def _gaps(found: Optional[list]) -> str:
    """Tes écarts à la théorie les plus importants contre les réguliers (charts, plans de jeu des flops résolus), en
    tête de page ; le détail est dans le Leakfinding des tables à plusieurs."""
    if found is None:
        return ""
    from ..ring_leaks import FORMAT
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


def _section(fs: ring.FormatStats, scope: str, shown: bool) -> str:
    t = fs.total
    tiles = [("Mains", str(t.hands), " · ".join(fs.sites)),
             ("Résultat", f"{num(t.net_bb, 1, sign=True)} bb", f"{num(t.bb100, 1, sign=True)} bb/100"),
             ("VPIP / PFR", f"{num(t.ratios['vpip'].pct, 0)} / {num(t.ratios['pfr'].pct, 0)}", "%"),
             ("3bet", f"{num(t.ratios['threebet'].pct, 1)}&nbsp;%", f"sur {t.ratios['threebet'].opps} occasions")]
    tiles_html = '<div class="tiles">' + "".join(
        f'<div class="tile"><div class="label">{label}</div><div class="value">{value}</div><div class="sub">{escape(sub)}</div></div>'
        for label, value, sub in tiles) + "</div>"
    period = f"{fs.first:%d/%m/%Y} → {fs.last:%d/%m/%Y}" if fs.first else ""
    tables = f" · {', '.join(fs.formats)}" if fs.formats else ""
    return (f'<section class="rg-fmt" data-scope="{escape(scope)}"{"" if shown else " hidden"}>'
            f'<div class="meta">{period}{escape(tables)}</div>{tiles_html}'
            f"<h2>Préflop par position</h2>{_table(fs, PREFLOP, True)}"
            f"<h2>Après le flop</h2>{_table(fs, POSTFLOP, False)}"
            f"{_refs(fs)}</section>")


def build_ring_page(scopes: list[tuple[str, str, list]], hero: str, embed: bool = True,
                    spots: Optional[list[dict]] = None, ranges: Optional[dict[str, int]] = None,
                    gaps: Optional[list] = None) -> str:
    """scopes : [(portée, libellé, ring.analyze(…, merge=True))] — toutes tes mains, contre les réguliers, contre les
    récréatifs ; spots : Library.ring_spots() ; ranges : formats dont la solution préflop est là (ring_ranges.available) ;
    gaps : tes écarts les plus importants contre les réguliers (leaks.top_stat_gaps)."""
    shown = [(scope, label, stats[0]) for scope, label, stats in scopes if stats]
    if not shown:
        body = ('<p class="note">Aucune main à une table de 3 joueurs ou plus. Importe des historiques 3-max, 6-max ou de '
                "7 à 9 joueurs (Betclic, Winamax, Unibet) : tes stats par position s'afficheront ici.</p>")
        if not ranges:  # les charts servent aussi à l'explorateur (coup à plusieurs), même sans mains
            body += ('<p><button type="button" class="rg-load">Charger les charts 100 bb de Hand2Note Guide (6-max et '
                     '3-max)</button> <span class="muted small">pour étudier les coups à plusieurs dans l\'explorateur ; '
                     'téléchargés sur ta machine, pour ton usage personnel (conditions du site)</span> '
                     '<span class="rg-load-msg small"></span></p>')
        return html_page("Tables à plusieurs", body, embed, script=SCRIPT)
    labels = {"all": "Toutes tes mains", "reg": "Contre les réguliers", "rec": "Contre les récréatifs"}
    switch = ('<div class="rg-switch" role="group" aria-label="Adversaires">' + "".join(
        f'<button type="button" data-scope="{escape(scope)}" aria-pressed="{str(k == 0).lower()}">'
        f"{escape(labels.get(scope, label))} ({fs.total.hands})</button>" for k, (scope, label, fs) in enumerate(shown))
        + "</div>") if len(shown) > 1 else ""
    heading = "" if embed else f"<h1>Tables à plusieurs — {escape(hero)}</h1>"
    style = ""
    if gaps is not None:
        from .leaks_page import STYLE as LEAK_STYLE
        from .review_page import STYLE as REVIEW_STYLE
        style = f"<style>{REVIEW_STYLE}{LEAK_STYLE}</style>"
    sections = "".join(_section(fs, scope, k == 0) for k, (scope, _, fs) in enumerate(shown))
    note = ("Toutes tes tables de 3 joueurs et plus ensemble (3-max, 6-max, 7 à 9 joueurs) ; une position compte ses "
            "mains de chaque taille de table. Une main compte contre les récréatifs quand un récréatif a mis de l'argent "
            "dans le pot pendant que tu y étais encore (le type de chaque adversaire se règle dans le Leakfinding des "
            "tables à plusieurs, ou dans Étude du field). Repères indicatifs d'un régulier en 6-max à 100 bb (stats de "
            "tracker courantes). Chiffres en gris : moins de 15 occasions. Open : tu parles le premier (personne n'est "
            "entré avant toi). Vol : ouverture du CO, du bouton ou de la SB sans caller ; la défense se lit en SB et BB. "
            "C-bet : tu étais le dernier relanceur préflop et on te laisse miser au flop.")
    body = (STYLE + style + heading + _gaps(gaps) + switch + sections
            + _spots(spots or [], bool(ranges)) + f'<p class="note">{note}</p>')
    return html_page(f"Tables à plusieurs — {hero}", body, embed, script=SCRIPT)
