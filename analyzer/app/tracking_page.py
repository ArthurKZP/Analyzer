"""Page « Suivi » : les leaks d'un joueur dans le temps, période par période, et avant / après le jour où il a commencé
à les travailler (analyzer/tracking.py)."""
from __future__ import annotations

from datetime import date
from html import escape
from typing import Optional

from .. import tracking
from ..leaks import Stat
from ..report import fold, format_query, format_switch, html_page, more

STYLE = """
.tr-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 14px; }
.tr-item { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 6px 18px; align-items: center; }
.tr-item + .tr-item { border-top: 1px solid var(--grid); padding-top: 12px; }
.tr-t { font-weight: 600; }
.tr-s { display: inline-block; font-size: 11px; font-weight: 600; border-radius: 999px; padding: 0 8px;
  margin-left: 4px; border: 1px solid var(--axis); color: var(--ink-2); white-space: nowrap; }
.tr-s.corrige { color: var(--good); border-color: var(--good); }
.tr-s.progres { color: var(--series-1); border-color: var(--series-1); }
.tr-s.aggrave { color: var(--alert); border-color: var(--alert); background: var(--alert-bg); }
.tr-e { font-size: 13px; color: var(--ink-2); margin-top: 2px; }
.tr-a { font-size: 12px; display: flex; gap: 6px; align-items: center; flex-wrap: wrap; margin-top: 4px; }
.tr-a input[type=date] { font: inherit; font-size: 12px; }
.tr-a button { font: inherit; font-size: 12px; padding: 2px 10px; border-radius: 6px; cursor: pointer;
  border: 1px solid var(--border); background: var(--surface); color: var(--ink); }
.tr-a button.go { border-color: var(--series-1); color: var(--series-1); }
svg.tr-spark { display: block; overflow: visible; }
svg.tr-spark .band { fill: var(--grid); }
svg.tr-spark .ref { stroke: var(--muted); stroke-dasharray: 3 3; fill: none; }
svg.tr-spark .line { stroke: var(--series-1); stroke-width: 2; fill: none; stroke-linejoin: round; }
svg.tr-spark .pt { stroke: var(--series-1); stroke-width: 2; fill: var(--surface); }
svg.tr-spark .pt.out { fill: var(--series-1); }
svg.tr-spark .hit { fill: transparent; }
svg.tr-spark .since { stroke: var(--ink-2); stroke-dasharray: 2 2; }
svg.tr-spark text { font-size: 9px; fill: var(--muted); }
table.tr td.plus { background: var(--hi-bg); }
table.tr td.moins { background: var(--lo-bg); }
table.tr span.n { color: var(--muted); font-size: 11px; }
table.tr .where { display: block; font-size: 11px; color: var(--muted); }
@media (max-width: 640px) { .tr-item { grid-template-columns: 1fr; } }
"""

SCRIPT = """
document.querySelectorAll('.tr-item').forEach(function (item) {
  var box = document.querySelector('.tr-box'), api = box.dataset.api + (box.dataset.query || '');
  function send(since) {
    item.querySelectorAll('button').forEach(function (b) { b.disabled = true; });
    fetch(api, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ leak: item.dataset.key, depuis: since, titre: item.dataset.title }) })
      .then(function (r) {
        if (r.ok) location.reload();
        else item.querySelectorAll('button').forEach(function (b) { b.disabled = false; });
      });
  }
  var date = item.querySelector('.tr-date');
  item.querySelectorAll('.tr-follow, .tr-save').forEach(function (b) {
    b.addEventListener('click', function () { if (date.value) send(date.value); });
  });
  var stop = item.querySelector('.tr-stop');
  if (stop) stop.addEventListener('click', function () { send(null); });
});
"""

UNITS = {"mois": "par mois", "semaine": "par semaine", "bloc": "par blocs de mains"}
METHOD = (
    "Chaque période est lue comme le Leakfinding : contre les réguliers, face à la théorie (la solution préflop ou "
    "tes charts, les plans de jeu des flops résolus, le repère ramené à ta taille). Un leak suivi compare tes mains "
    "d'avant la date choisie à celles d'après : « corrigé » quand ta fréquence est depuis dans la tolérance de la "
    "théorie, « en progrès » quand elle s'en est nettement rapprochée (le hasard l'explique mal : test à 90 %), "
    "« s'éloigne de la théorie » quand elle s'en est nettement éloignée ; il faut au moins dix occasions depuis la "
    "date pour juger. Sur chaque courbe : ta fréquence par période (pleine : écart net, vide : dans la tolérance), la "
    "théorie en pointillés et sa tolérance en gris ; le trait vertical marque le début du suivi.")


def _pct(stat: Optional[Stat], scope: str = "reg") -> str:
    r = stat.ratios.get(scope) if stat is not None else None
    return "–" if not r or not r.opps else f"{round(100 * r.hits / r.opps)} %"


def _n(stat: Optional[Stat], scope: str = "reg") -> int:
    r = stat.ratios.get(scope) if stat is not None else None
    return r.opps if r else 0


def _ref(stat: Optional[Stat], scope: str = "reg") -> Optional[float]:
    if stat is None:
        return None
    ref = stat.ref_for(scope)
    if ref is None and stat.band:
        ref = sum(stat.band) / 2
    return ref


def sparkline(track: tracking.Track, found: list, scope: str = "reg", width: int = 260, height: int = 58) -> str:
    """La fréquence d'un écart période par période : un point par période (plein : écart net), la théorie en
    pointillés, sa tolérance en gris, et le début du suivi."""
    n = len(found)
    if not n:
        return ""
    left, right, top, bottom = 6, 6, 6, 14
    xs = [left + (width - left - right) * (k / (n - 1) if n > 1 else 0.5) for k in range(n)]
    values = [(s.ratios[scope].hits / s.ratios[scope].opps) if s is not None and s.ratios.get(scope)
              and s.ratios[scope].opps else None for s in track.stats]
    refs = [_ref(s, scope) for s in track.stats]
    known_refs = [r for r in refs if r is not None]
    fallback = known_refs[-1] if known_refs else None
    refs = [r if r is not None else fallback for r in refs]
    loose = [track.stats[k].tolerances(refs[k])[1] if track.stats[k] is not None and refs[k] is not None else
             (0.08 if refs[k] is not None else None) for k in range(n)]
    high = max([v for v in values if v is not None] + [r + t for r, t in zip(refs, loose) if r is not None] + [0.1])
    high = min(1.0, high * 1.15)
    y = lambda v: top + (height - top - bottom) * (1 - v / high)  # noqa: E731
    parts = [f'<svg class="tr-spark" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" '
             f'aria-label="{escape(track.title)}, période par période">']
    if fallback is not None:
        upper = " ".join(f"{xs[k]:.1f},{y(min(1.0, refs[k] + loose[k])):.1f}" for k in range(n))
        lower = " ".join(f"{xs[k]:.1f},{y(max(0.0, refs[k] - loose[k])):.1f}" for k in reversed(range(n)))
        parts.append(f'<polygon class="band" points="{upper} {lower}"/>')
        parts.append('<polyline class="ref" points="' + " ".join(f"{xs[k]:.1f},{y(refs[k]):.1f}" for k in range(n))
                     + '"/>')
    at = track.since_index(found)
    if track.since is not None:
        x = (xs[at - 1] + xs[at]) / 2 if at else (xs[0] - 4 if at == 0 else xs[-1] + 4)
        parts.append(f'<line class="since" x1="{x:.1f}" x2="{x:.1f}" y1="{top - 2}" y2="{height - bottom}"/>')
    points = [(xs[k], y(v)) for k, v in enumerate(values) if v is not None]
    if len(points) > 1:
        parts.append('<polyline class="line" points="' + " ".join(f"{a:.1f},{b:.1f}" for a, b in points) + '"/>')
    for k, v in enumerate(values):
        if v is None:
            continue
        stat = track.stats[k]
        out = stat.verdict(scope) is not None
        ref = f" · théorie {round(100 * refs[k])} %" if refs[k] is not None else ""
        tip = f"{found[k].label} : {round(100 * v)} % ({_n(stat, scope)} occasions){ref}"
        parts.append(f'<g><title>{escape(tip)}</title><circle class="hit" cx="{xs[k]:.1f}" cy="{y(v):.1f}" r="9"/>'
                     f'<circle class="pt{" out" if out else ""}" cx="{xs[k]:.1f}" cy="{y(v):.1f}" r="4"/></g>')
    parts.append(f'<text x="{xs[0]:.1f}" y="{height - 2}" text-anchor="start">{escape(found[0].label)}</text>')
    if n > 1:
        parts.append(f'<text x="{xs[-1]:.1f}" y="{height - 2}" text-anchor="end">{escape(found[-1].label)}</text>')
    return "".join(parts) + "</svg>"


def _followed_item(track: tracking.Track, found: list) -> str:
    label, why = tracking.STATUSES.get(track.status, ("", ""))
    ref = _ref(track.after) if track.after is not None else _ref(track.before)
    theory = f" · théorie : {round(100 * ref)} %" if ref is not None else ""
    before = f"{_pct(track.before)} ({_n(track.before)} occasions)" if _n(track.before) else "aucune occasion"
    line = (f"Avant le {track.since:%d/%m/%Y} : {before} · depuis : {_pct(track.after)} ({_n(track.after)})"
            f"{theory} — {why}.")
    actions = (f'<label>depuis le <input type="date" class="tr-date" value="{track.since.isoformat()}"></label>'
               '<button type="button" class="tr-save">Changer la date</button>'
               '<button type="button" class="tr-stop">Ne plus suivre</button>')
    return (f'<li class="tr-item" data-key="{escape(track.key)}" data-title="{escape(track.title)}"><div>'
            f'<div class="tr-t">{escape(track.title)} <span class="tr-s {track.status}">{escape(label)}</span></div>'
            f'<div class="tr-e">{escape(line)}</div><div class="tr-a">{actions}</div></div>'
            f'{sparkline(track, found)}</li>')


def _current_item(track: tracking.Track, found: list, today: date) -> str:
    last = next(((found[k], s) for k in reversed(range(len(found))) for s in [track.stats[k]] if _n(s)), None)
    if last is None:
        line = "Pas d'occasion sur la période."
    else:
        period, stat = last
        ref = _ref(stat)
        theory = f" · théorie : {round(100 * ref)} %" if ref is not None else ""
        line = f"Dernière période ({period.label}) : {_pct(stat)} ({_n(stat)} occasions){theory}."
    actions = (f'<label>Je le travaille depuis le <input type="date" class="tr-date" value="{today.isoformat()}">'
               '</label><button type="button" class="go tr-follow">Suivre</button>')
    return (f'<li class="tr-item" data-key="{escape(track.key)}" data-title="{escape(track.title)}"><div>'
            f'<div class="tr-t">{escape(track.title)}</div><div class="tr-e">{escape(line)}</div>'
            f'<div class="tr-a">{actions}</div></div>{sparkline(track, found)}</li>')


def _table(found: tracking.Tracking, scope: str = "reg") -> str:
    head = "".join(f'<th class="num">{escape(p.label)}</th>' for p in found.periods)
    rows = []
    for key, stats in sorted(found.rows.items(), key=lambda kv: found.labels[kv[0]]):
        cells = []
        for stat in stats:
            verdict = stat.verdict(scope) if stat is not None else None
            cls = f' class="num {verdict[0]}"' if verdict else ' class="num"'
            cells.append(f'<td{cls}>{_pct(stat, scope)} <span class="n">({_n(stat, scope)})</span></td>'
                         if _n(stat, scope) else '<td class="num">–</td>')
        last = next((s for s in reversed(stats) if s is not None), None)
        ref = _ref(last, scope)
        section, label = found.labels[key]
        rows.append(f'<tr><td>{escape(label)}<span class="where">{escape(section)}</span></td>{"".join(cells)}'
                    f'<td class="num">{"–" if ref is None else f"{round(100 * ref)} %"}</td></tr>')
    return ('<div class="scroll"><table class="stats tr"><thead><tr><th>Situation</th>'
            f'{head}<th class="num">Théorie</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>')


def build_tracking_page(found: tracking.Tracking, api: str, table_format: str = "HU",
                        formats: Optional[list[tuple[str, int]]] = None, embed: bool = True,
                        today: Optional[date] = None) -> str:
    """found : le suivi (tracking.build) ; api : l'adresse où suivre un leak (POST {leak, depuis, titre})."""
    today = today or date.today()
    switch = format_switch(formats or [], table_format)
    if not found.periods:
        body = (f'{switch}<p class="note">Pas encore de mains datées dans ce format : le suivi se construit avec tes '
                "mains, période par période.</p>")
        return html_page("Suivi des leaks", body, embed)
    followed = [t for t in found.tracks if t.since is not None]
    current = [t for t in found.tracks if t.since is None]
    counts = {key: sum(t.status == key for t in followed) for key in tracking.STATUSES}
    first, last = found.periods[0], found.periods[-1]
    tiles = ('<div class="tiles">'
             f'<div class="tile"><div class="label">Leaks suivis</div><div class="value">{len(followed)}</div>'
             f'<div class="sub">{len(current)} autre{"s" if len(current) > 1 else ""} à suivre</div></div>'
             f'<div class="tile"><div class="label">Corrigés</div><div class="value">{counts["corrige"]}</div></div>'
             f'<div class="tile"><div class="label">En progrès</div><div class="value">{counts["progres"]}</div></div>'
             f'<div class="tile"><div class="label">Périodes</div><div class="value">{len(found.periods)}</div>'
             f'<div class="sub">{escape(UNITS[found.unit])}</div></div></div>')
    if followed:
        items = "".join(_followed_item(t, found.periods) for t in followed)
        mine = f'<div class="card"><ul class="tr-list">{items}</ul></div>'
    else:
        mine = ('<div class="card"><p class="note" style="margin:0">Aucun leak suivi pour l\'instant : choisis plus '
                "bas ceux que tu travailles, et depuis quand ; leur évolution s'affichera ici, avant et "
                "après.</p></div>")
    if current:
        items = "".join(_current_item(t, found.periods, today) for t in current)
        others = f'<div class="card"><ul class="tr-list">{items}</ul></div>'
    else:
        others = ('<div class="card"><p class="note" style="margin:0">Pas d\'autre leak net à suivre pour '
                  "l'instant.</p></div>")
    query = escape(format_query(table_format))
    when = (f"le {first.start:%d/%m/%Y}" if first.start == last.end
            else f"du {first.start:%d/%m/%Y} au {last.end:%d/%m/%Y}")
    body = f"""
<div class="meta">Tes leaks période par période ({escape(UNITS[found.unit])}) : {found.hands} mains, {when}, contre
les réguliers.</div>
{switch}
{tiles}
<div class="tr-box" data-api="{escape(api)}" data-query="{query}">
<h2>Les leaks que tu travailles</h2>
{mine}
<h2>Tes leaks du moment</h2>
{others}
</div>
{fold("Toutes tes stats, période par période", f'<div class="card">{_table(found)}</div>',
      f"{len(found.rows)} situations, {len(found.periods)} périodes", "toutes")}
{more("Comment c'est suivi", escape(METHOD))}
"""
    return html_page("Suivi des leaks", f"<style>{STYLE}</style>{body}", embed, script=SCRIPT)
