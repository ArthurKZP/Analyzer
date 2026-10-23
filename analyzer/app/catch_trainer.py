"""Entraîneur › Bluffcatch : t'entraîner à payer ou coucher une mise river sur tes mains jouées.

Chaque exercice est une mise (ou relance) river que tu as affrontée (bluffcatch.faced) : la main se rejoue jusqu'à ta
décision (le replayer, arrêté avant elle), tu choisis de payer ou de coucher, puis tu lis le plan de la situation —
ce que disent ses mises vues dans la même ligne et la même taille (sinon la même ligne, sinon toutes ses mises river),
contre le même type d'adversaires et au même format (bluffcatch.advice) — ce que tu as fait à la table et, quand elles
sont connues, ses cartes. Les filtres choisissent la situation à travailler : format, ligne, taille, adversaires, ce
que tu as fait.
"""
from __future__ import annotations

import json
from typing import Optional

from .. import bluffcatch, replay
from ..cards import describe_holding
from ..models import CALL, FOLD, RAISE, Hand
from ..report import html_page

MAX_EXERCISES = 1500  # les plus récents
INTENTS = {"bluff": "bluff (rien de fait à la river)", "thin": "value fine", "value": "value"}


def _reply_index(hand: Hand, index: int, hero: str) -> Optional[int]:
    """L'action où tu réponds à la mise (la décision à prendre)."""
    for k in range(index + 1, len(hand.actions)):
        a = hand.actions[k]
        if a.street != "river":
            return None
        if a.player == hero and a.kind in (FOLD, CALL, RAISE):
            return k
    return None


def _plan(found: dict, x: bluffcatch.Faced) -> Optional[bluffcatch.Spot]:
    """La situation qui fait le plan : la ligne et la taille, sinon la ligne, sinon toutes ses mises, la plus précise
    qui tranche (sinon la plus précise)."""
    keys = [(bluffcatch.LINE_SIZE, f"{x.line}, {x.size}" if x.size else ""), (bluffcatch.LINE, x.line),
            (bluffcatch.ALL, "toutes")]
    spots = [found[k] for k in keys if k in found]
    return next((s for s in spots if s.decision), spots[0] if spots else None)


def exercises(groups: list[tuple[str, list[Hand], dict]], hero: str, api: str = "/api") -> dict:
    """groups : (format : « HU » ou « ring », tes mains, type de chaque adversaire) ; renvoie les exercices (les plus
    récents d'abord) et les choix des filtres."""
    out = []
    for fmt, hands, kinds in groups:
        names = {p for h in hands for p in h.seats if p != hero}
        items = bluffcatch.faced(hands, names, hero)
        kind_of = {p: (kinds.get(p) or {}).get("kind", "reg") for p in names}
        found = {}
        for kind in ("reg", "rec"):
            mine = [x for x in items if kind_of[x.player] == kind]
            found[kind] = {(s.dimension, s.value): s for s in bluffcatch.spots(mine)}
        for x in items:
            stop = _reply_index(x.hand, x.index, hero)
            if stop is None:
                continue
            kind = kind_of[x.player]
            spot = _plan(found[kind], x)
            plural = spot is not None and len({i.player for i in spot.items}) > 1
            mine_cards = x.hand.hole_cards.get(hero, [])
            his = x.hand.hole_cards.get(x.player, [])
            out.append({
                "hand": x.hand.hand_id, "stop": stop, "fmt": fmt, "table": x.hand.table_format,
                "date": x.hand.date.strftime("%d/%m/%Y"), "ts": x.hand.date.isoformat(),
                "player": x.player, "kind": kind, "line": x.line, "size": x.size or "relance",
                "pot": round(x.pot, 1), "bet": round(x.bet, 1), "call": round(x.to_call, 1),
                "required": round(x.required, 3), "alpha": round(x.alpha, 3),
                "cards": mine_cards, "board": x.hand.board[:5],
                "holding": describe_holding(mine_cards, x.hand.board[:5]),
                "reply": x.reply, "his": his if len(his) == 2 else [], "intent": x.intent, "won": x.won,
                "profit": None if x.profit is None else round(x.profit, 1),
                "plan": None if spot is None else {
                    "title": spot.title, "decision": spot.decision[0] if spot.decision else None,
                    "sure": spot.decision[1] if spot.decision else None,
                    "evidence": bluffcatch.evidence(spot, plural), "advice": bluffcatch.advice(spot, plural)},
                "replay": replay.href(x.hand.hand_id, api),
            })
    out.sort(key=lambda e: e["ts"], reverse=True)
    out = out[:MAX_EXERCISES]

    def counts(key: str) -> list[list]:
        found: dict[str, int] = {}
        for e in out:
            found[e[key]] = found.get(e[key], 0) + 1
        return [[k, n] for k, n in sorted(found.items(), key=lambda kv: -kv[1])]
    return {"exercises": out, "lines": counts("line"), "sizes": counts("size"), "formats": counts("fmt"),
            "kinds": counts("kind"), "intents": INTENTS}


STYLE = """<style>
.bc-filters { display: flex; flex-wrap: wrap; gap: 8px 12px; align-items: flex-end; padding: 10px 12px;
  margin-bottom: 14px; background: var(--surface); border: 1px solid var(--border); border-radius: 10px; }
.bc-filters label { display: flex; flex-direction: column; gap: 2px; font-size: 12px; color: var(--muted); }
.bc-filters select { font: inherit; font-size: 13px; padding: 4px 8px; border-radius: 6px;
  border: 1px solid var(--border); background: var(--page); color: var(--ink); max-width: 260px; }
.bc-filters .bc-count { font-size: 13px; color: var(--ink-2); align-self: center; margin-left: auto; }
.bc-play { display: grid; grid-template-columns: minmax(0, 3fr) minmax(280px, 2fr); gap: 14px; align-items: start; }
.bc-frame { width: 100%; height: 640px; border: 1px solid var(--border); border-radius: 10px; background: var(--page); }
.bc-facts { font-size: 14px; line-height: 1.6; }
.bc-facts b { font-variant-numeric: tabular-nums; }
.bc-choice { display: flex; gap: 10px; margin: 12px 0; flex-wrap: wrap; }
.bc-choice button { font: inherit; font-weight: 700; border: 0; border-radius: 8px; padding: 10px 18px; cursor: pointer;
  color: #fff; min-width: 120px; }
.bc-choice .pay { background: var(--series-3); } .bc-choice .fold { background: var(--series-1); }
.bc-choice .next { background: var(--ink); color: var(--page); }
.bc-choice button:disabled { opacity: 0.4; cursor: default; }
.bc-verdict { border-radius: 8px; padding: 8px 10px; margin: 8px 0; font-weight: 600; }
.bc-verdict.ok { background: rgba(27,175,122,0.14); } .bc-verdict.ko { background: var(--alert-bg); }
.bc-verdict.open { background: var(--lo-bg); }
.bc-score { font-size: 13px; color: var(--ink-2); }
.bc-feedback p { margin: 6px 0; font-size: 14px; }
.bc-feedback .muted { font-size: 13px; }
@media (max-width: 860px) { .bc-play { grid-template-columns: 1fr; } }
</style>"""

SCRIPT = r"""
(function () {
  var D = JSON.parse(document.getElementById('bc-data').textContent);
  var $ = function (id) { return document.getElementById(id); };
  var SUITS = { s: '♠', h: '♥', d: '♦', c: '♣' };
  var pct = function (x) { return Math.round(100 * x) + ' %'; };
  var num = function (x) { return (Math.round(x * 10) / 10).toLocaleString('fr-FR'); };
  var cards = function (list) {
    return list.map(function (c) {
      return '<span class="pc s' + c[1] + '">' + c[0] + SUITS[c[1]] + '</span>';
    }).join('');
  };
  var ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' };
  var esc = function (t) { return String(t).replace(/[&<>"]/g, function (c) { return ESC[c]; }); };
  var KEY = 'bluffcatch:filtres';
  var saved = {};
  try { saved = JSON.parse(localStorage.getItem(KEY) || '{}'); } catch (e) {}
  var fill = function (id, list, names) {
    var sel = $(id);
    list.forEach(function (row) {
      var o = document.createElement('option');
      o.value = row[0];
      o.textContent = (names ? names[row[0]] || row[0] : row[0]) + ' (' + row[1] + ')';
      sel.append(o);
    });
    if (saved[id] && list.some(function (r) { return r[0] === saved[id]; })) sel.value = saved[id];
  };
  fill('f-fmt', D.formats, { HU: 'Heads-up', ring: 'Tables à plusieurs' });
  fill('f-line', D.lines);
  fill('f-size', D.sizes);
  fill('f-kind', D.kinds, { reg: 'Réguliers', rec: 'Récréatifs' });
  if (saved['f-did']) $('f-did').value = saved['f-did'];
  var pool = [], current = null, answered = false, score = { n: 0, plan: 0, judged: 0, known: 0, right: 0 };

  function pick() {
    var f = { fmt: $('f-fmt').value, line: $('f-line').value, size: $('f-size').value, kind: $('f-kind').value,
      did: $('f-did').value };
    saved = { 'f-fmt': f.fmt, 'f-line': f.line, 'f-size': f.size, 'f-kind': f.kind, 'f-did': f.did };
    try { localStorage.setItem(KEY, JSON.stringify(saved)); } catch (e) {}
    pool = D.exercises.filter(function (e) {
      return (!f.fmt || e.fmt === f.fmt) && (!f.line || e.line === f.line) && (!f.size || e.size === f.size)
        && (!f.kind || e.kind === f.kind) && (!f.did || (f.did === 'fold' ? e.reply === 'fold' : e.reply !== 'fold'));
    });
    $('bc-count').textContent = pool.length + ' situation' + (pool.length > 1 ? 's' : '');
    next();
  }

  function next() {
    answered = false;
    if (!pool.length) {
      current = null;
      $('bc-play').hidden = true;
      $('bc-empty').hidden = false;
      return;
    }
    $('bc-empty').hidden = true;
    $('bc-play').hidden = false;
    current = pool[Math.floor(Math.random() * pool.length)];
    var e = current;
    $('bc-frame').src = e.replay + (e.replay.indexOf('?') < 0 ? '?' : '&') + 'stop=' + e.stop + '&embed=1';
    $('bc-facts').innerHTML = '<p><b>' + esc(e.line) + '</b> · mise ' + esc(e.size) + ' · '
      + (e.kind === 'rec' ? 'récréatif' : 'régulier') + ' · ' + (e.fmt === 'HU' ? 'heads-up' : esc(e.table)) + ' · '
      + e.date + '</p><p>Il mise <b>' + num(e.bet) + ' bb</b> dans <b>' + num(e.pot) + ' bb</b> ; à payer : <b>'
      + num(e.call) + ' bb</b>.</p><p>Il te faut <b>' + pct(e.required) + '</b> d\'équité pour payer ; ses bluffs '
      + 'gagnent si tu couches plus de <b>' + pct(e.alpha) + '</b> de ces mises.</p>'
      + '<p>Ta main : ' + cards(e.cards) + ' sur ' + cards(e.board) + ' — <b>' + esc(e.holding) + '</b></p>';
    $('bc-feedback').innerHTML = '';
    $('b-pay').disabled = $('b-fold').disabled = false;
    $('b-next').hidden = true;
  }

  function answer(choice) {
    if (!current || answered) return;
    answered = true;
    var e = current, plan = e.plan, out = [];
    $('b-pay').disabled = $('b-fold').disabled = true;
    $('b-next').hidden = false;
    score.n += 1;
    var wanted = plan && plan.decision ? (plan.decision === 'coucher' ? 'fold' : 'call') : null;
    if (wanted) {
      score.judged += 1;
      var ok = wanted === choice;
      if (ok) score.plan += 1;
      var what = wanted === 'fold' ? 'coucher ses bluffcatchers' : plan.decision === 'payer' ? 'payer'
        : 'défendre plus (payer avec tes meilleurs bluffcatchers)';
      out.push('<div class="bc-verdict ' + (ok ? 'ok' : 'ko') + '">' + (ok ? 'Conforme au plan' : 'Contre le plan')
        + ' : ' + what
        + (plan.sure === 'à confirmer' ? ' — à confirmer' : '') + '.</div>');
    } else {
      out.push('<div class="bc-verdict open">Pas de plan net dans cette situation : défends à la fréquence '
        + 'minimale.</div>');
    }
    if (plan) {
      out.push('<p><b>' + esc(plan.title) + '</b> : ' + esc(plan.advice) + '</p><p class="muted">'
        + esc(plan.evidence) + '</p>');
    }
    var did = { call: 'payé', fold: 'couché', raise: 'relancé' }[e.reply] || e.reply;
    out.push('<p>À la table, tu as <b>' + did + '</b>.</p>');
    if (e.his.length) {
      score.known += 1;
      var win = e.won;
      if ((choice === 'call') === !!win) score.right += 1;
      var amount = e.profit !== null ? ' (' + num(Math.abs(e.profit)) + ' bb)' : '';
      out.push('<p>Ses cartes : ' + cards(e.his) + (e.intent ? ' — ' + esc(D.intents[e.intent] || e.intent) : '')
        + '. ' + (win === true ? 'Payer gagnait' + amount + '.' : win === false ? 'Payer perdait' + amount + '.' : '')
        + '</p>');
    } else {
      out.push('<p class="muted">Ses cartes ne sont pas connues (pas d\'abattage).</p>');
    }
    out.push('<p><a href="' + esc(e.replay) + '" target="_blank" rel="noopener">Revoir la main en entier ↗</a></p>');
    $('bc-feedback').innerHTML = out.join('');
    $('bc-score').textContent = score.n + ' décision' + (score.n > 1 ? 's' : '') + ' · conformes au plan : '
      + score.plan + ' / ' + score.judged
      + (score.known ? ' · gagnantes quand ses cartes sont connues : ' + score.right + ' / ' + score.known : '');
  }

  ['f-fmt', 'f-line', 'f-size', 'f-kind', 'f-did'].forEach(function (id) { $(id).addEventListener('change', pick); });
  $('b-pay').addEventListener('click', function () { answer('call'); });
  $('b-fold').addEventListener('click', function () { answer('fold'); });
  $('b-next').addEventListener('click', next);
  document.addEventListener('keydown', function (ev) {
    if (ev.target.closest('select, input')) return;
    if (ev.key === 'p' || ev.key === 'P') answer('call');
    else if (ev.key === 'c' || ev.key === 'C') answer('fold');
    else if ((ev.key === 'Enter' || ev.key === ' ') && answered) { ev.preventDefault(); next(); }
  });
  pick();
})();
"""


def build_page(data: dict, embed: bool = True) -> str:
    """La page de l'entraîneur bluffcatch (data : exercises())."""
    if not data["exercises"]:
        body = ('<h2>Bluffcatch : tes mains</h2><p class="note">Aucune mise river affrontée avec tes cartes connues '
                "pour l'instant : importe des mains où un adversaire mise à la river face à toi.</p>")
        return html_page("Bluffcatch", body, embed)
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    body = f"""{STYLE}
<h2>Bluffcatch : tes mains</h2>
<p class="note">Une mise river que tu as affrontée : la main se rejoue jusqu'à ta décision. Paie (P) ou couche (C),
puis lis le plan de la situation — ce que disent ses mises vues dans la même ligne et la même taille, contre le même
type d'adversaires — ce que tu as fait à la table et, quand elles sont connues, ses cartes. Choisis la situation à
travailler avec les filtres.</p>
<div class="bc-filters" role="group" aria-label="La situation à travailler">
  <label>Format<select id="f-fmt"><option value="">Tous</option></select></label>
  <label>Ligne<select id="f-line"><option value="">Toutes</option></select></label>
  <label>Taille<select id="f-size"><option value="">Toutes</option></select></label>
  <label>Adversaires<select id="f-kind"><option value="">Tous</option></select></label>
  <label>Ce que tu as fait<select id="f-did"><option value="">Tout</option>
    <option value="call">Tu as payé (ou relancé)</option><option value="fold">Tu as couché</option></select></label>
  <span class="bc-count" id="bc-count"></span>
</div>
<p class="note" id="bc-empty" hidden>Aucune situation avec ces filtres.</p>
<div class="bc-play" id="bc-play" hidden>
  <iframe class="bc-frame" id="bc-frame" title="La main, jusqu'à ta décision"></iframe>
  <div class="card">
    <div class="bc-facts" id="bc-facts"></div>
    <div class="bc-choice">
      <button type="button" class="pay" id="b-pay" title="P">Payer</button>
      <button type="button" class="fold" id="b-fold" title="C">Coucher</button>
      <button type="button" class="next" id="b-next" hidden title="Entrée">Situation suivante</button>
    </div>
    <div class="bc-feedback" id="bc-feedback" aria-live="polite"></div>
    <p class="bc-score" id="bc-score"></p>
  </div>
</div>
<script type="application/json" id="bc-data">{payload}</script>"""
    return html_page("Bluffcatch", body, embed, script=SCRIPT)
