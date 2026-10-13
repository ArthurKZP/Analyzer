"""Onglet « Ranges préflop » des Paramètres : tes ranges par défaut, ligne par ligne et format par format, sans passer
par un coup. La liste vient de /api/ranges/defaut (Library.default_ranges_view) ; l'éditeur d'une ligne (grille à
peindre, texte, plus serré ou plus large : static/rangekit.js, comme l'onglet Ranges de l'explorateur) enregistre par
/api/ranges/defaut/enregistrer. Les ranges sont gardées par custom_ranges, portée « ligne »."""
from __future__ import annotations

from ..report import html_page

STYLE = """
.rp-opt { display: flex; gap: 8px; align-items: baseline; cursor: pointer; }
.rp-opt input { margin: 0; flex: none; }
.rp-status { font-size: 12px; min-height: 1.2em; margin-top: 6px; color: var(--muted); }
.rp-status.err, .rp-err { color: var(--alert); font-size: 12px; }
table.rp-lines td { vertical-align: middle; }
table.rp-lines tr.group td { font-size: 12px; font-weight: 600; color: var(--muted); padding-top: 14px;
  border-bottom: 1px solid var(--axis); }
table.rp-lines td.act { text-align: right; white-space: nowrap; }
table.rp-lines tr.open td { border-bottom: 0; }
table.rp-lines tr.editor > td { padding: 0 6px 16px; }
.rp-mine { color: var(--series-2); font-weight: 600; }
.rp-ed { display: flex; flex-direction: column; gap: 8px; padding-top: 4px; }
.rp-ed p { margin: 0; }
.rp-row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.rp-btn { font: inherit; font-size: 13px; padding: 3px 10px; border-radius: 6px; cursor: pointer;
  border: 1px solid var(--border); background: var(--surface); color: var(--ink); }
.rp-btn:hover:not(:disabled) { border-color: var(--series-1); }
.rp-btn:disabled { opacity: 0.4; cursor: default; }
.rp-btn.go { background: var(--series-1); border-color: var(--series-1); color: #fff; }
.seg { display: inline-flex; border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
.seg button { font: inherit; font-size: 13px; background: var(--surface); color: var(--ink); border: 0;
  border-right: 1px solid var(--border); padding: 4px 10px; cursor: pointer; }
.seg button:last-child { border-right: 0; }
.seg button[aria-pressed="true"] { background: var(--series-1); color: #fff; }
.rgrid { display: grid; grid-template-columns: repeat(13, minmax(0, 1fr)); gap: 2px; touch-action: none;
  user-select: none; max-width: 520px; }
.rcell { position: relative; aspect-ratio: 1.15; border-radius: 3px; cursor: crosshair; font-size: 11px; line-height: 1;
  background: linear-gradient(to top, color-mix(in srgb, var(--series-1) 55%, var(--surface)) var(--w, 0%),
  var(--grid) var(--w, 0%)); }
.rcell .h { position: absolute; left: 2px; top: 2px; font-weight: 700; }
.rcell.diff { box-shadow: inset 0 0 0 2px var(--series-2); }
.rp-text { width: 100%; max-width: 520px; box-sizing: border-box; font: 12px/1.4 ui-monospace, monospace;
  background: var(--page); color: var(--ink); border: 1px solid var(--border); border-radius: 6px; padding: 6px;
  resize: vertical; }
@media (max-width: 560px) { .rcell { font-size: 9px; } .rcell .h { left: 1px; top: 1px; } }
"""

SCRIPT = r"""
(function () {
  'use strict';
  const K = window.AnalyzerRanges;  // rangekit.js
  const $ = (id) => document.getElementById(id);
  const POTS = { 'pot limpé': 'Pots limpés', SRP: 'Pots simples (open, call)', 'pot 3bet': 'Pots 3bet', 'pot 4bet': 'Pots 4bet' };
  const num = (x, d = 1) => ((Math.round(x * 10 ** d) / 10 ** d) || 0).toLocaleString('fr-FR', { maximumFractionDigits: d });
  const pct = (x) => Math.round(100 * x) + ' %';
  function el(tag, attrs, ...children) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'class') n.className = v;
      else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? '' : v);
    }
    for (const c of children.flat()) if (c !== null && c !== undefined && c !== '') n.append(c instanceof Node ? c : String(c));
    return n;
  }
  async function api(url, body) {
    const res = await fetch(url, body === undefined ? {} : {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || res.statusText);
    return data;
  }

  let view = null;  // les lignes de chaque format (/api/ranges/defaut)
  // la ligne ouverte : ses ranges (data), celles en cours d'édition (edit), la position montrée, le pinceau
  let ed = null;

  const dirty = () => !!ed && !!ed.data && ed.edit.some((w, k) => !K.sameRange(w, ed.data.players[k].current));
  const leave = () => !dirty() || window.confirm('Tes changements de « ' + ed.data.line + ' » ne sont pas enregistrés. Les abandonner ?');
  window.addEventListener('beforeunload', (e) => { if (dirty()) { e.preventDefault(); e.returnValue = ''; } });

  async function load() {
    try {
      view = await api('/api/ranges/defaut');
    } catch (e) {
      $('rp-list').textContent = e.message;
      return;
    }
    $('rp-spots-box').checked = view.for_spots;
    $('rp-charts').hidden = view.charts;
    renderList();
  }

  function status(line) {
    if (!line.adjusted.length) return el('span', { class: 'muted' }, 'référence');
    return el('span', { class: 'rp-mine' }, 'ta range : ' + line.adjusted.join(', '));
  }

  function renderList() {
    const box = $('rp-list');
    box.textContent = '';
    for (const group of view.formats) {
      box.append(el('h2', {}, group.format === 'HU' ? 'Heads-up' : group.format,
        group.hands ? el('span', { class: 'muted', style: 'font-weight:400;font-size:14px' },
          ' · ' + num(group.hands, 0) + (group.hands > 1 ? ' mains' : ' main')) : ''));
      const card = box.appendChild(el('div', { class: 'card' }));
      card.append(el('p', { class: 'note', style: 'margin-top:0' }, 'Référence : ' + group.reference + '.'));
      if (!group.lines.length) {
        card.append(el('p', { class: 'muted' }, 'Aucune ligne à deux joueurs au flop dans ta référence pour ce format.'));
        continue;
      }
      const table = card.appendChild(el('table', { class: 'stats rp-lines' }));
      let pot = null;
      for (const line of group.lines) {
        if (line.pot_type !== pot) {
          pot = line.pot_type;
          table.append(el('tr', { class: 'group' }, el('td', { colspan: 3 }, POTS[pot] || pot)));
        }
        const open = ed && ed.context === line.context;
        const row = table.appendChild(el('tr', { class: open ? 'open' : null },
          el('td', {}, el('b', {}, line.line)), el('td', {}, status(line)),
          el('td', { class: 'act' }, el('button', { type: 'button', class: 'rp-btn', 'aria-expanded': String(open),
            onclick: () => toggle(line) }, open ? 'Fermer' : 'Modifier'))));
        if (open) {
          const cell = el('td', { colspan: 3 });
          row.after(el('tr', { class: 'editor' }, cell));
          renderEditor(cell);
        }
      }
    }
    if (view.orphans.length) {
      box.append(el('h2', {}, 'Réglages sans référence'));
      const card = box.appendChild(el('div', { class: 'card' }));
      card.append(el('p', { class: 'note', style: 'margin-top:0' }, 'Tes ranges de lignes que ta référence ne couvre plus '
        + '(charts retirés, format que tu ne joues plus) : elles ne servent pas tant que la ligne n\'a pas de référence.'));
      const table = card.appendChild(el('table', { class: 'stats rp-lines' }));
      for (const o of view.orphans) {
        table.append(el('tr', {}, el('td', {}, el('b', {}, o.line || o.context), ' ', el('span', { class: 'muted' }, o.format)),
          el('td', {}, el('span', { class: 'rp-mine' }, 'ta range : ' + o.positions.join(', '))),
          el('td', { class: 'act' }, el('button', { type: 'button', class: 'rp-btn', onclick: () => forget(o) }, 'Effacer'))));
      }
    }
  }

  async function toggle(line) {
    if (ed && ed.context === line.context) {
      if (!leave()) return;
      ed = null;
      renderList();
      return;
    }
    if (!leave()) return;
    ed = { context: line.context, data: null, edit: null, player: 0, brush: 1, busy: false, msg: '', ui: null };
    renderList();
    try {
      setLine(await api('/api/ranges/defaut', { context: line.context }));
    } catch (e) {
      ed.msg = e.message;
    }
    renderList();
  }

  function setLine(data) {
    ed.data = data;
    ed.edit = data.players.map((p) => ({ ...p.current }));
    const line = view.formats.flatMap((g) => g.lines).find((l) => l.context === data.context);
    if (line) line.adjusted = data.players.filter((p) => p.source === 'ligne').map((p) => p.position);
  }

  async function forget(o) {
    if (!window.confirm('Effacer ta range de « ' + (o.line || o.context) + ' » en ' + o.format + ' ?')) return;
    try {
      await api('/api/ranges/defaut/effacer', { context: o.context });
    } catch (e) {
      window.alert(e.message);
    }
    load();
  }

  // L'éditeur d'une ligne ; ce qui dépend des ranges en cours d'édition se met à jour à part (refresh), sans
  // reconstruire les boutons : un clic juste après avoir tapé une range ne se perd pas.
  function renderEditor(cell) {
    ed.ui = null;
    const d = ed.data;
    if (!d) { cell.append(el('p', { class: ed.msg ? 'rp-err' : 'muted' }, ed.msg || 'Chargement…')); return; }
    const p = d.players[ed.player];
    const ui = ed.ui = {};
    const wrap = cell.appendChild(el('div', { class: 'rp-ed' }));
    wrap.append(el('p', { class: 'note' }, d.line + (d.format !== 'HU' ? ' en ' + d.format : '') + ' · ' + d.pot_type
      + ' · référence : ' + d.reference + '. Les ranges sont celles de chaque joueur au flop.'));
    const seg = el('div', { class: 'seg', role: 'group', 'aria-label': 'Position' });
    ui.players = d.players.map((q, k) => seg.appendChild(el('button', {
      type: 'button', 'aria-pressed': String(k === ed.player), onclick: () => { ed.player = k; renderList(); },
    }, q.position)));
    wrap.append(el('div', { class: 'rp-row' }, seg, el('span', { class: 'note', style: 'margin:0' },
      p.source === 'ligne' ? 'Enregistrée : ta range' : 'Enregistrée : celle de la référence')));
    ui.count = wrap.appendChild(el('div', { class: 'note', style: 'margin:0' }));

    const brushes = el('div', { class: 'seg', role: 'group', 'aria-label': 'Pinceau' });
    const brushButtons = K.BRUSHES.map(([v, label]) => brushes.appendChild(el('button', {
      type: 'button', 'aria-pressed': String(ed.brush === v), title: v ? 'Peindre les mains à ' + label : 'Retirer les mains de la range',
      onclick: () => { ed.brush = v; brushButtons.forEach((b, k) => b.setAttribute('aria-pressed', String(K.BRUSHES[k][0] === v))); },
    }, label)));
    const change = (next) => { ed.edit[ed.player] = next; ed.msg = ''; refresh(); };
    const current = () => ed.edit[ed.player];
    wrap.append(el('div', { class: 'rp-row' }, brushes,
      el('button', { type: 'button', class: 'rp-btn', title: 'Retire environ 10 % des combos, en commençant par les mains les plus faibles',
        onclick: () => change(K.resize(current(), -1, d.order)) }, 'Plus serré'),
      el('button', { type: 'button', class: 'rp-btn', title: 'Ajoute environ 10 % de combos, en commençant par les mains les plus fortes',
        onclick: () => change(K.resize(current(), 1, d.order)) }, 'Plus large')));

    const grid = K.grid((h) => {
      const w = ed.edit[ed.player];
      if ((w[h] || 0) === ed.brush) return;
      if (ed.brush > 0) w[h] = ed.brush; else delete w[h];
      ed.msg = '';
      refresh();
    }, () => ed.busy);
    ui.cells = grid.cells;
    wrap.append(grid.box);
    wrap.append(el('p', { class: 'note' }, 'Clique ou glisse sur les mains avec le pinceau choisi. Contour orange : différent de la référence.'));

    ui.text = el('textarea', { class: 'rp-text', rows: 4, spellcheck: 'false', 'aria-label': 'Range de ' + p.position + ' en texte' });
    ui.text.addEventListener('input', () => {  // la grille suit la saisie dès qu'elle se lit
      try { ed.edit[ed.player] = K.parseRange(ui.text.value); ed.msg = ''; refresh(); } catch (e) { /* avis au change */ }
    });
    ui.text.addEventListener('change', () => {
      const typed = ui.text.value;
      try { change(K.parseRange(typed)); } catch (e) { ed.msg = e.message; refresh(); ui.text.value = typed; }  // à corriger
    });
    wrap.append(ui.text);

    ui.toRef = el('button', { type: 'button', class: 'rp-btn', onclick: () => change({ ...p.reference }) }, 'Range de la référence');
    ui.undo = el('button', { type: 'button', class: 'rp-btn', onclick: () => change({ ...p.current }) }, 'Annuler mes changements');
    wrap.append(el('div', { class: 'rp-row' }, ui.toRef, ui.undo));

    ui.save = el('button', { type: 'button', class: 'rp-btn go', onclick: save }, 'Enregistrer');
    const actions = el('div', { class: 'rp-row' }, ui.save);
    if (d.adjusted) actions.append(el('button', { type: 'button', class: 'rp-btn', disabled: ed.busy, onclick: reset }, 'Revenir à la référence'));
    wrap.append(actions);
    ui.msg = wrap.appendChild(el('p', { role: 'status' }));
    refresh();
  }

  function refresh() {
    const ui = ed && ed.ui;
    if (!ui) return;
    const d = ed.data;
    const p = d.players[ed.player];
    const w = ed.edit[ed.player];
    ui.cells.forEach((cell) => {
      const h = cell.dataset.h, x = w[h] || 0, r = p.reference[h] || 0;
      cell.style.setProperty('--w', Math.round(100 * x) + '%');
      cell.classList.toggle('diff', Math.abs(x - r) > 0.002);
      cell.title = h + ' · ' + pct(x) + (Math.abs(x - r) > 0.002 ? ' (référence ' + pct(r) + ')' : '');
    });
    const n = K.combosOf(w), ref = K.combosOf(p.reference);
    ui.count.textContent = num(n, 0) + ' combos (' + num(100 * n / 1326, 1) + ' % des mains) · référence '
      + num(ref, 0) + ' (' + num(100 * ref / 1326, 1) + ' %)';
    ui.players.forEach((b, k) => { b.textContent = d.players[k].position + (K.sameRange(ed.edit[k], d.players[k].current) ? '' : ' •'); });
    if (document.activeElement !== ui.text) ui.text.value = K.rangeText(w);
    ui.toRef.disabled = ed.busy || K.sameRange(w, p.reference);
    ui.undo.disabled = ed.busy || K.sameRange(w, p.current);
    ui.save.disabled = ed.busy || !dirty();
    ui.msg.textContent = ed.msg;
    ui.msg.className = ed.msg && ed.error ? 'rp-err' : 'note';
    ui.msg.hidden = !ed.msg;
  }

  async function call(url, body, done) {
    ed.busy = true;
    ed.msg = '';
    refresh();
    try {
      const data = await api(url, body);
      if (data.players) setLine(data);
      else setLine(await api('/api/ranges/defaut', { context: ed.context }));
      ed.msg = done;
      ed.error = false;
    } catch (e) {
      ed.msg = e.message;
      ed.error = true;
    }
    ed.busy = false;
    renderList();
  }

  function save() {
    if (ed.ui && ed.ui.text.value !== K.rangeText(ed.edit[ed.player])) {  // texte tapé sans quitter la zone
      try { ed.edit[ed.player] = K.parseRange(ed.ui.text.value); } catch (e) { ed.msg = e.message; ed.error = true; refresh(); return; }
    }
    const ranges = {};
    ed.data.players.forEach((p, k) => { ranges[p.position] = K.rangeText(ed.edit[k]); });
    if (Object.values(ranges).some((t) => !t)) { ed.msg = 'Range vide : garde au moins une main.'; ed.error = true; refresh(); return; }
    call('/api/ranges/defaut/enregistrer', { context: ed.context, ranges },
      'Enregistré : tes coups de cette ligne se résolvent désormais avec ces ranges.');
  }

  function reset() {
    if (!window.confirm('Effacer tes ranges de « ' + ed.data.line + ' » en ' + ed.data.format
      + ' ? La ligne reprend celles de la référence.')) return;
    call('/api/ranges/defaut/effacer', { context: ed.context }, 'La ligne reprend les ranges de la référence.');
  }

  $('rp-spots-box').addEventListener('change', async (e) => {
    const out = $('rp-spots-status');
    out.textContent = 'Enregistrement…';
    out.classList.remove('err');
    try {
      const data = await api('/api/ranges/defaut/spots', { for_spots: e.target.checked });
      e.target.checked = data.for_spots;
      out.textContent = 'Enregistré.';
    } catch (err) {
      e.target.checked = !e.target.checked;
      out.textContent = err.message;
      out.classList.add('err');
    }
  });
  $('rp-charts-go').addEventListener('click', async (e) => {
    const out = $('rp-charts-status');
    e.target.disabled = true;
    out.textContent = 'Téléchargement…';
    out.classList.remove('err');
    try {
      await api('/api/ranges/hand2note', {});
      out.textContent = '';
      load();
    } catch (err) {
      out.textContent = err.message;
      out.classList.add('err');
    }
    e.target.disabled = false;
  });

  // Les charts par niveau de limites : importer un fichier JSON, le retirer.
  async function loadStakes() {
    const box = $('rp-stakes');
    let data;
    try {
      data = await api('/api/ranges/limites');
    } catch (e) {
      box.textContent = e.message;
      return;
    }
    box.textContent = '';
    for (const f of data.formats) {
      const rows = f.bands.map((b) => {
        const c = b.charts;
        const out = el('span', { class: 'rp-err' });
        const status = c
          ? el('span', {}, el('b', {}, 'Importés'), c.source ? ' · ' + c.source : '',
            c.rake ? ' · rake : ' + c.rake : '')
          : el('span', { class: 'muted' }, f.reference ? 'Ceux de la référence (micro limites, rake élevé)'
            : 'Pas de charts de référence (charge ceux de Hand2Note Guide, plus bas)');
        const input = el('input', { type: 'file', accept: '.json,application/json', hidden: true });
        input.addEventListener('change', async () => {
          const file = input.files[0];
          input.value = '';
          if (!file) return;
          out.textContent = 'Import…';
          try {
            const charts = JSON.parse(await file.text());
            await api('/api/ranges/limites', { format: f.format, limites: b.band, charts });
            loadStakes();
          } catch (err) {
            out.textContent = err instanceof SyntaxError ? 'Fichier illisible : il faut du JSON.' : err.message;
          }
        });
        const pick = el('button', { type: 'button', class: 'rp-btn', onclick: () => input.click() },
          c ? 'Remplacer…' : 'Importer…');
        const drop = c ? el('button', { type: 'button', class: 'rp-btn', onclick: async () => {
          if (!window.confirm('Retirer les charts ' + f.label + ' des ' + b.label.toLowerCase()
            + ' ? Ces mains reprennent ceux de la référence.')) return;
          try {
            await api('/api/ranges/limites/retirer', { format: f.format, limites: b.band });
            loadStakes();
          } catch (err) { out.textContent = err.message; }
        } }, 'Retirer') : null;
        return el('tr', {}, el('td', {}, b.label), el('td', { class: 'num' }, b.hands ? num(b.hands, 0) : '–'),
          el('td', {}, status, ' ', out), el('td', { class: 'act' }, pick, drop ? ' ' : '', drop, input));
      });
      box.append(el('h3', {}, f.label), el('div', { class: 'scroll' }, el('table', { class: 'stats rp-lines' },
        el('thead', {}, el('tr', {}, el('th', {}, 'Limites'), el('th', { class: 'num' }, 'Tes mains'),
          el('th', {}, 'Charts'), el('th', {}, ''))), el('tbody', {}, rows))));
    }
  }
  loadStakes();
  load();
})();
"""


def build_ranges_page(embed: bool = True) -> str:
    body = """<div class="meta">Tes charts préflop selon tes limites, qui jugent ton préflop ; puis tes ranges par
défaut, ligne par ligne : à la place de celles de la référence (la solution heads-up, les charts des tables à
plusieurs), le solveur part des tiennes dans tous tes coups de la ligne.</div>
<h2>Charts selon les limites</h2>
<div class="card">
<p style="margin-top:0">Le rake change la théorie : plus il pèse, plus on ouvre et on défend serré, moins on paie et on
limpe. Les charts de la référence (la solution heads-up, les charts 6-max) sont ceux des micro limites, au rake élevé.
Importe, pour un niveau de limites, des charts faits pour lui (fichier JSON : en heads-up, une range par action à chaque
nœud — open du bouton, BB face à l'open, bouton face au 3bet, BB face au 4bet — ; aux tables à plusieurs, une solution
par lignes, voir le README) : tes mains de ce niveau sont jugées avec eux dans le Leakfinding, les mains de départ, le
préflop et le suivi. Les coups résolus au solveur gardent leurs ranges (celles de la référence, ou les tiennes plus
bas) : rien n'est à résoudre à nouveau.</p>
<div id="rp-stakes"><p class="muted">Chargement…</p></div>
</div>
<h2>Tes ranges, ligne par ligne : où elles s'appliquent</h2>
<div class="card">
<p style="margin-top:0">Tes coups de la ligne et du format : l'explorateur, l'analyse de tes mains face au solveur et le
Leakfinding. Un coup réglé à part dans l'explorateur (onglet <b>Ranges</b>, « pour ce coup seulement ») garde son
réglage. Les séries d'étude, leurs plans de jeu et les repères restent faits avec la référence, pour rester
comparables.</p>
<label class="rp-opt"><input type="checkbox" id="rp-spots-box"><span>Aussi pour les spots d'étude ouverts dans
l'explorateur <span class="muted">(ils se résolvent alors à nouveau, dans une étude à part)</span></span></label>
<div class="rp-status" id="rp-spots-status" aria-live="polite"></div>
<p class="note">Une range changée change la solution : un coup déjà résolu se résout à nouveau à sa prochaine ouverture,
dans une étude à part ; celle de la référence reste.</p>
</div>
<div class="card" id="rp-charts" hidden style="margin-top:12px">
<p style="margin-top:0">Pas encore de charts pour les tables à plusieurs : charge les charts 6-max de Hand2Note Guide
(usage personnel, gardés dans ta base) pour régler leurs lignes, ou importe ta solution (<code>python -m analyzer ranges
--importer FICHIER.json</code>).</p>
<button type="button" class="rp-btn" id="rp-charts-go">Charger les charts 6-max</button>
<div class="rp-status" id="rp-charts-status" aria-live="polite"></div>
</div>
<div id="rp-list"><p class="muted">Chargement…</p></div>
<script src="/static/rangekit.js"></script>
"""
    return html_page("Ranges préflop", f"<style>{STYLE}</style>{body}", embed, script=SCRIPT)
