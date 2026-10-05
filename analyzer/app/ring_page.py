"""Page « Tables à plusieurs » de Mon jeu : tes stats par position en 6-max et en 3-max (analyzer/ring.py)."""
from __future__ import annotations

from html import escape
from typing import Optional
from urllib.parse import quote

from .. import ring
from ..report import cards_html, html_page, num, pct_cell

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
  document.querySelectorAll('.rg-fmt').forEach((s) => { s.hidden = s.dataset.fmt !== b.dataset.fmt; });
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


def _spots(rows: list[dict], table_format: str, ready: bool) -> str:
    """Les coups à deux joueurs au flop, à ouvrir au solveur postflop."""
    mine = [r for r in rows if r["format"] == table_format]
    if not mine:
        return ""
    body = "".join(
        f'<tr><td class="nowrap">{r["date"]:%d/%m %H:%M}</td><td>{escape(r["line"])}</td>'
        f'<td class="pos">{escape(r["hero"])} <span class="muted">vs {escape(r["villain"])}</span></td>'
        f'<td>{cards_html(r["cards"])}</td><td>{cards_html(r["board"])}</td><td class="num">{num(r["total_bb"], 1)}</td>'
        f'<td class="num">{num(r["net_bb"], 1, sign=True)}</td>'
        + (f'<td><a class="rg-open" href="/explorateur/{quote(r["id"], safe="")}" target="_blank" rel="noopener">'
           f'Ouvrir au solveur ↗</a></td>' if r["status"] is None else f"<td>{_why(r['status'])}</td>")
        + "</tr>" for r in mine[:MAX_SPOTS])
    note = "" if ready else (
        f'<p class="note">Tes ranges préflop du {escape(table_format)} ne sont pas encore là : ces coups s\'ouvriront '
        "au solveur dès qu'elles seront ajoutées.</p>")
    if not ready and table_format == "6-max":
        note += ('<p><button type="button" class="rg-load">Charger les charts 6-max 100 bb de Hand2Note Guide</button> '
                 '<span class="muted small">pots simples et pots 3bet ; téléchargés sur ta machine, pour ton usage '
                 "personnel (conditions du site)</span> <span class=\"rg-load-msg small\"></span></p>")
    more = f" Les {MAX_SPOTS} plus gros pots sur {len(mine)}." if len(mine) > MAX_SPOTS else ""
    return (f"<h2>Coups à deux joueurs au flop</h2>{note}"
            '<div class="card scroll"><table class="stats"><thead><tr><th>Date</th><th>Ligne préflop</th><th>Toi</th>'
            '<th>Main</th><th>Flop</th><th class="num">Pot (bb)</th><th class="num">Résultat</th><th>Solveur</th>'
            f"</tr></thead><tbody>{body}</tbody></table></div>"
            f'<p class="note">Résolus comme un coup heads-up à partir du flop, avec les ranges de ta solution préflop '
            f"pour la ligne jouée ; le pot compte l'argent mort des joueurs qui ont foldé.{more}</p>")


def _section(fs: ring.FormatStats, shown: bool, spots: str = "") -> str:
    t = fs.total
    tiles = [("Mains", str(t.hands), " · ".join(fs.sites)),
             ("Résultat", f"{num(t.net_bb, 1, sign=True)} bb", f"{num(t.bb100, 1, sign=True)} bb/100"),
             ("VPIP / PFR", f"{num(t.ratios['vpip'].pct, 0)} / {num(t.ratios['pfr'].pct, 0)}", "%"),
             ("3bet", f"{num(t.ratios['threebet'].pct, 1)}&nbsp;%", f"sur {t.ratios['threebet'].opps} occasions")]
    tiles_html = '<div class="tiles">' + "".join(
        f'<div class="tile"><div class="label">{label}</div><div class="value">{value}</div><div class="sub">{escape(sub)}</div></div>'
        for label, value, sub in tiles) + "</div>"
    period = f"{fs.first:%d/%m/%Y} → {fs.last:%d/%m/%Y}" if fs.first else ""
    note = ("Repères indicatifs d'un régulier en 6-max à 100 bb (stats de tracker courantes), en attendant ceux du "
            "solveur." if fs.table_format == "6-max" else "Pas encore de repère pour ce format.")
    return (f'<section class="rg-fmt" data-fmt="{escape(fs.table_format)}"{"" if shown else " hidden"}>'
            f'<div class="meta">{escape(fs.table_format)} · {period}</div>{tiles_html}'
            f"<h2>Préflop par position</h2>{_table(fs, PREFLOP, True)}"
            f"<h2>Après le flop</h2>{_table(fs, POSTFLOP, False)}"
            f"{_refs(fs)}{spots}"
            f'<p class="note">{note} Chiffres en gris : moins de 15 occasions. Open : tu parles le premier '
            "(personne n'est entré avant toi). Vol : ouverture du CO, du bouton ou de la SB sans caller ; la défense se "
            "lit en SB et BB. C-bet : tu étais le dernier relanceur préflop et on te laisse miser au flop.</p></section>")


def build_ring_page(stats: list[ring.FormatStats], hero: str, embed: bool = True, spots: Optional[list[dict]] = None,
                    ranges: Optional[dict[str, int]] = None) -> str:
    """spots : Library.ring_spots() ; ranges : formats dont la solution préflop est là (ring_ranges.available)."""
    if not stats:
        body = ('<p class="note">Aucune main à une table de 3 joueurs ou plus. Importe des historiques 3-max ou 6-max '
                "(Betclic, Winamax, Unibet) : tes stats par position s'afficheront ici.</p>")
        return html_page("Tables à plusieurs", body, embed)
    switch = ""
    if len(stats) > 1:
        switch = '<div class="rg-switch" role="group" aria-label="Format de table">' + "".join(
            f'<button type="button" data-fmt="{escape(fs.table_format)}" aria-pressed="{str(k == 0).lower()}">'
            f"{escape(fs.table_format)} ({fs.total.hands})</button>" for k, fs in enumerate(stats)) + "</div>"
    heading = "" if embed else f"<h1>Tables à plusieurs — {escape(hero)}</h1>"
    body = STYLE + heading + switch + "".join(
        _section(fs, k == 0, _spots(spots or [], fs.table_format, fs.table_format in (ranges or {})))
        for k, fs in enumerate(stats))
    return html_page(f"Tables à plusieurs — {hero}", body, embed, script=SCRIPT)
