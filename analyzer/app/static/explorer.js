(function () {
  'use strict';

  const HAND = document.body.dataset.hand;
  const $ = (id) => document.getElementById(id);
  const RANKS = 'AKQJT98765432';
  const SUITS = { s: '♠', h: '♥', d: '♦', c: '♣' };
  const STREET = ['Flop', 'Turn', 'River'];
  const POS = ['BB', 'BTN'];
  const NAME = { H: 'Toi', V: 'Lui' };
  const MODES = [['strategy', 'Stratégie'], ['strategy_ev', 'Stratégie + EV'], ['ev', 'EV'], ['equity', 'Équité']];
  const SHADES = ['var(--g-bet1)', 'var(--g-bet2)', 'var(--g-bet3)', 'var(--g-bet4)'];

  let state = null;   // état de la main et de sa résolution (/api/explorateur/etat)
  let meta = null;
  let played = [];    // chemin complet de la ligne jouée dans l'arbre
  let path = [];
  let node = null;
  let live = false;
  let mode = 'strategy_ev';
  let viewPlayer = 0;
  let viewAuto = true;
  let hovered = null;
  let selected = null;
  let notice = '';
  let pollTimer = null;
  const nodes = new Map();

  // ---------- utilitaires ----------
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
  const num = (x, d = 1) => ((Math.round(x * 10 ** d) / 10 ** d) || 0).toLocaleString('fr-FR', { maximumFractionDigits: d });
  const pct = (x) => Math.round(100 * x) + ' %';
  const card = (c) => el('span', { class: 'pc s' + c[1] }, c[0] + SUITS[c[1]]);
  const cards = (list) => el('span', {}, list.map(card));
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  const prefixOf = (p, full) => p.length <= full.length && same(p, full.slice(0, p.length));

  function roleOf(p) {
    const oop = meta.hero_position === 'BB' ? 'H' : 'V';
    return p === 0 ? oop : (oop === 'H' ? 'V' : 'H');
  }
  const playerOf = (role) => (roleOf(0) === role ? 0 : 1);

  // Libellés des actions d'un nœud. Mise : % du pot ; relance : montant ajouté en % du pot après le
  // call (convention des solveurs : relancer à 4,5 sur une mise de 1,7 dans un pot de 5 = 33 %).
  function actLabels(actions, pot, put, player) {
    const call = actions.find((x) => x.kind === 'call');
    const base = call && put && player !== null && player !== undefined ? 2 * put[1 - player] : 0;
    return actions.map((a) => {
      if ((a.kind === 'bet' || a.kind === 'raise') && a.allin) return 'Tapis ' + num(a.amount);
      if (a.kind === 'bet') return 'Mise ' + num(a.amount) + ' (' + Math.round(100 * a.amount / pot) + ' %)';
      if (a.kind === 'raise') return 'Relance ' + num(a.amount) + (base ? ' (' + Math.round(100 * (a.amount - call.amount) / base) + ' %)' : '');
      return { check: 'Check', call: 'Call', fold: 'Fold' }[a.kind] || a.kind;
    });
  }
  const nodeLabels = () => actLabels(node.actions, node.pot, node.put, node.player);

  function colors(actions) {
    const sized = actions.map((a, k) => k).filter((k) => (actions[k].kind === 'bet' || actions[k].kind === 'raise') && !actions[k].allin);
    return actions.map((a, k) => {
      if (a.kind === 'fold') return 'var(--g-fold)';
      if (a.kind === 'check' || a.kind === 'call') return 'var(--g-pass)';
      if (a.allin) return 'var(--g-allin)';
      const rank = sized.indexOf(k);
      return SHADES[sized.length === 1 ? 1 : Math.round(rank * 3 / (sized.length - 1))];
    });
  }

  function classOf(combo) {
    const r1 = combo[0], r2 = combo[2];
    if (r1 === r2) return r1 + r2;
    const [hi, lo] = RANKS.indexOf(r1) < RANKS.indexOf(r2) ? [r1, r2] : [r2, r1];
    return hi + lo + (combo[1] === combo[3] ? 's' : 'o');
  }
  const handAt = (i, j) => (i === j ? RANKS[i] + RANKS[i] : i < j ? RANKS[i] + RANKS[j] + 's' : RANKS[j] + RANKS[i] + 'o');

  function liveCounts(board) {
    const deck = [];
    for (const r of RANKS) for (const s of 'cdhs') if (!board.includes(r + s)) deck.push(r + s);
    const out = {};
    for (let i = 0; i < deck.length; i++) for (let j = i + 1; j < deck.length; j++) {
      const c = classOf(deck[i] + deck[j]);
      out[c] = (out[c] || 0) + 1;
    }
    return out;
  }

  function findRow(p, hole) {
    if (!hole || hole.length !== 2) return null;
    return node.hands[p].find((row) => (row[0].slice(0, 2) === hole[0] && row[0].slice(2) === hole[1])
      || (row[0].slice(0, 2) === hole[1] && row[0].slice(2) === hole[0])) || null;
  }
  const revealed = () => $('reveal').checked;

  // ---------- serveur ----------
  async function api(url, body) {
    const res = await fetch(url, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const err = new Error(data.error || 'Erreur ' + res.status);
      err.status = res.status;
      throw err;
    }
    return data;
  }

  async function fetchNode(p) {
    const key = JSON.stringify(p);
    if (nodes.has(key)) return nodes.get(key);
    const reply = await api('/api/explorateur/noeud', { hand: HAND, path: p });
    if (reply.live !== live) { live = reply.live; renderStatus(); }
    nodes.set(key, reply.node);
    return reply.node;
  }

  async function goTo(target) {
    let p = target.slice();
    try {
      // Sans session, seules les décisions de la ligne jouée sont connues : on passe les cartes jouées.
      if (!live) while (prefixOf(p, played) && p.length < played.length && played[p.length].type === 'card') p.push(played[p.length]);
      let n = await fetchNode(p);
      while (n.type === 'chance') {
        const real = meta.board[n.board.length];
        p = p.concat([{ type: 'card', card: n.cards.includes(real) ? real : n.cards[0] }]);
        n = await fetchNode(p);
      }
      path = p;
      node = n;
      if (viewAuto && n.player !== null && n.player !== undefined) viewPlayer = n.player;
      selected = null;
      notice = '';
      render();
    } catch (e) {
      notice = e.message;
      if (e.status === 409 && live) {  // session fermée entre-temps (inactivité) : on propose de recalculer
        live = false;
        try { await loadState(); } catch (err) { /* l'avis suffit */ }
      }
      render();
    }
  }

  function back() {
    const p = path.slice();
    while (p.length && p[p.length - 1].type === 'card') p.pop();
    if (p.length) { p.pop(); goTo(p); }
  }

  async function loadState() {
    state = await api('/api/explorateur/etat', { hand: HAND });
    meta = state.meta;
    live = !!state.live;
    const decisions = state.result ? state.result.decisions : [];
    const last = decisions[decisions.length - 1];
    played = last ? last.path.concat(last.chosen === null ? [] : [{ type: 'action', index: last.chosen }]) : [];
  }

  async function solve() {
    try {
      const v = await api('/api/resoudre', { hand: HAND, start: true, force: true });
      Object.assign(state, v);
      renderStatus();
      poll();
    } catch (e) {
      notice = e.message;
      renderStatus();
    }
  }

  function poll() {
    clearTimeout(pollTimer);
    if (!state || !['waiting', 'running'].includes(state.state)) return;
    pollTimer = setTimeout(async () => {
      try {
        const v = await api('/api/resoudre/' + encodeURIComponent(state.job));
        if (v.state === 'done') {
          nodes.clear();
          await loadState();
          renderMeta();
          await goTo(node ? path : initialPath());
          renderStatus();
          return;
        }
        Object.assign(state, v);
        renderStatus();
        poll();
      } catch (e) {
        notice = e.message;
        renderStatus();
      }
    }, 1500);
  }

  // ---------- rendu ----------
  function renderMeta() {
    const parts = ['Main ' + meta.hand, meta.date];
    if (state.result) parts.push(state.result.pot_type);
    parts.push('toi ' + (meta.hero_position === 'BTN' ? 'au bouton' : 'en BB'));
    const box = $('meta');
    box.textContent = '';
    box.append(parts.join(' · ') + ' · ', cards(meta.hero_cards), ' vs ',
      revealed() && meta.villain_cards.length ? cards(meta.villain_cards) : '??',
      ' · ' + meta.villain + ' · résultat ' + (meta.net > 0 ? '+' : '') + num(meta.net) + ' bb');
  }

  function renderStatus() {
    const box = $('status');
    box.textContent = '';
    if (!state) return;
    const s = state.state;
    if (s === 'unsupported') box.append(el('span', {}, state.message));
    else if (s === 'unavailable' || (s === 'absent' && state.solver && !state.solver.ready)) {
      const info = state.solver || state;
      box.append(el('span', {}, info.message), el('code', {}, info.install));
    } else if (s === 'waiting' || s === 'running') {
      const p = state.progress || {};
      const frac = p.iteration ? Math.min(1, p.iteration / state.max_iterations) : 0;
      const elapsed = state.elapsed ? ' · ' + (state.elapsed >= 60 ? Math.floor(state.elapsed / 60) + ' min ' : '') + Math.round(state.elapsed % 60) + ' s' : '';
      box.append(el('span', {}, s === 'waiting' ? 'En attente d\'une autre résolution…'
        : state.mode === 'load' ? 'Ouverture de l\'étude enregistrée…' + elapsed
        : (p.iteration ? 'Résolution : itération ' + p.iteration + ' / ' + state.max_iterations + ' · exploitabilité ' + num(p.exploit_pct) + ' % du pot (objectif ' + num(state.target) + ' %)'
          : p.tree_nodes ? 'Résolution lancée : arbre de ' + p.tree_nodes.toLocaleString('fr-FR') + ' nœuds, première mesure après 10 itérations'
            : 'Construction de l\'arbre…') + elapsed),
      el('div', { class: 'bar' }, el('span', { style: 'width:' + (100 * frac).toFixed(1) + '%' })),
      el('button', { type: 'button', onclick: () => api('/api/resoudre/' + encodeURIComponent(state.job) + '/arreter', {}).then((v) => { Object.assign(state, v); renderStatus(); }) }, 'Arrêter'));
    } else if (s === 'done' && live) {
      const r = state.result;
      box.append(el('span', {}, 'Session active : toutes les branches et toutes les cartes sont explorables (fermée après 30 min sans activité). '
        + r.iterations + ' itérations, exploitabilité ' + num(r.exploit_pct, 2) + ' % du pot.'));
    } else if (s === 'done' && state.study) {
      box.append(el('span', {}, 'Étude enregistrée : ligne jouée affichée.'),
        el('button', { type: 'button', class: 'go', onclick: solve }, 'Ouvrir l\'étude complète (quelques secondes)'));
    } else if (s === 'done') {
      box.append(el('span', {}, 'Ligne jouée seulement (résultat enregistré). Pour explorer les autres branches et changer les cartes :'),
        el('button', { type: 'button', class: 'go', onclick: solve }, 'Recalculer (quelques minutes)'));
    } else {
      if (state.error) box.append(el('span', { class: 'err' }, state.error));
      box.append(el('span', {}, 'Ce coup n\'est pas encore résolu.'),
        el('button', { type: 'button', class: 'go', onclick: solve }, 'Résoudre ce coup'));
    }
    if (notice) box.append(el('span', { class: 'err' }, notice));
  }

  function actionStep(h, k, current) {
    const role = roleOf(h.player);
    const step = el('div', { class: 'step' + (current ? ' current' : '') },
      el('div', { class: 'head' }, el('span', { class: role }, POS[h.player] + ' · ' + NAME[role]), el('span', {}, num(h.stack))));
    const prefix = path.slice(0, k);
    const onLine = prefixOf(prefix, played) && played[k] && played[k].type === 'action';
    const labels = actLabels(h.actions, h.pot, h.put, h.player);
    h.actions.forEach((a, j) => {
      const isPlayed = onLine && played[k].index === j;
      const allowed = live || isPlayed;
      step.append(el('button', {
        type: 'button', class: 'act' + (h.chosen === j ? ' on' : ''), disabled: !allowed,
        title: allowed ? null : 'Hors de la ligne jouée : recalcule pour explorer cette branche',
        onclick: () => goTo(prefix.concat([{ type: 'action', index: j }])),
      }, el('span', {}, labels[j]), isPlayed ? el('span', { class: 'dot', title: 'Joué dans la main' }, '●') : ''));
    });
    return step;
  }

  function cardStep(h, k) {
    const btn = el('button', {
      type: 'button', disabled: !live, title: live ? 'Changer de carte' : 'Recalcule pour changer de carte',
      onclick: (e) => openPicker(k, e.currentTarget),
    }, h.card ? card(h.card) : '?');
    return el('div', { class: 'step cards' }, el('div', { class: 'head' }, el('span', {}, STREET[h.street] || 'Carte'), el('span', {}, 'pot ' + num(h.pot))), btn);
  }

  function renderRibbon() {
    const box = $('ribbon');
    box.textContent = '';
    box.append(el('div', { class: 'step cards' },
      el('div', { class: 'head' }, el('span', {}, 'Flop'), el('span', {}, 'pot ' + num(state.result.pot))), cards(meta.board.slice(0, 3))));
    node.history.forEach((h, k) => {
      const current = k === node.history.length - 1;
      if (h.kind === 'card') box.append(cardStep(h, k));
      else if (h.kind === 'action') box.append(actionStep(h, k, current));
      else box.append(el('div', { class: 'step end current' }, node.type === 'terminal_fold' ? 'Fin : fold' : 'Abattage'));
    });
    box.scrollLeft = box.scrollWidth;
  }

  async function openPicker(k, anchor) {
    const picker = $('picker');
    let chance;
    try { chance = await fetchNode(path.slice(0, k)); } catch (e) { notice = e.message; renderStatus(); return; }
    const current = path[k] && path[k].card;
    picker.textContent = '';
    picker.append(el('div', { class: 'hint' }, el('span', {}, 'Choisis la ' + (STREET[chance.street] || 'carte').toLowerCase()),
      el('button', { type: 'button', onclick: closePicker }, '✕')));
    const rows = el('div', { class: 'rows' });
    for (const s of 'shdc') for (const r of RANKS) {
      const c = r + s;
      rows.append(el('button', {
        type: 'button', class: 'pc s' + s + (c === current ? ' on' : ''), disabled: !chance.cards.includes(c),
        onclick: () => { closePicker(); goTo(path.slice(0, k).concat([{ type: 'card', card: c }])); },
      }, r + SUITS[s]));
    }
    picker.append(rows);
    picker.hidden = false;
    const rect = anchor.getBoundingClientRect();
    picker.style.left = Math.max(8, Math.min(rect.left, innerWidth - picker.offsetWidth - 8)) + 'px';
    picker.style.top = (rect.bottom + 6) + 'px';
  }
  function closePicker() { $('picker').hidden = true; }

  function aggregate(p) {
    const na = node.actions.length;
    const strat = p === node.player;
    const out = {};
    for (const row of node.hands[p]) {
      const cls = classOf(row[0]);
      const r = row[1];
      const a = out[cls] || (out[cls] = { w: 0, ev: 0, evw: 0, eq: 0, eqw: 0, s: new Array(na).fill(0), rows: [] });
      a.w += r;
      a.rows.push(row);
      if (row[3] !== null) { a.ev += r * row[3]; a.evw += r; }
      if (row[2] !== null) { a.eq += r * row[2]; a.eqw += r; }
      if (strat) for (let j = 0; j < na; j++) a.s[j] += r * row[4 + j];
    }
    for (const a of Object.values(out)) {
      a.ev = a.evw ? a.ev / a.evw : null;
      a.eq = a.eqw ? a.eq / a.eqw : null;
      if (strat && a.w) a.s = a.s.map((x) => x / a.w);
    }
    return out;
  }

  function renderToolbar() {
    const modes = $('modes');
    modes.textContent = '';
    const actor = node.player !== null && node.player !== undefined;
    MODES.forEach(([id, label]) => modes.append(el('button', {
      type: 'button', 'aria-pressed': mode === id ? 'true' : 'false',
      onclick: () => { mode = id; render(); },
    }, label)));
    const players = $('players');
    players.textContent = '';
    [0, 1].forEach((p) => players.append(el('button', {
      type: 'button', 'aria-pressed': viewPlayer === p ? 'true' : 'false',
      onclick: () => { viewPlayer = p; viewAuto = actor && p === node.player; selected = null; render(); },
    }, POS[p] + ' · ' + NAME[roleOf(p)] + (actor && node.player === p ? ' (agit)' : ''))));
  }

  function renderGrid() {
    const grid = $('grid');
    grid.textContent = '';
    const agg = aggregate(viewPlayer);
    const counts = liveCounts(node.board);
    const strat = viewPlayer === node.player;
    const cols = strat ? colors(node.actions) : [];
    const key = mode === 'equity' ? 'eq' : 'ev';
    const values = Object.values(agg).map((a) => a[key]).filter((x) => x !== null);
    const lo = Math.min(...values), hi = Math.max(...values);
    const heroCls = viewPlayer === playerOf('H') && meta.hero_cards.length === 2 ? classOf(meta.hero_cards.join('')) : null;
    const villainCls = revealed() && viewPlayer === playerOf('V') && meta.villain_cards.length === 2 ? classOf(meta.villain_cards.join('')) : null;
    for (let i = 0; i < 13; i++) for (let j = 0; j < 13; j++) {
      const hand = handAt(i, j);
      const a = agg[hand];
      const cls = 'cell' + (a ? '' : ' out') + (hand === heroCls ? ' me' : '') + (hand === villainCls ? ' him' : '') + (hand === selected ? ' sel' : '');
      const cell = el('div', { class: cls, 'data-hand': hand }, el('span', { class: 'h' }, hand));
      if (a) {
        const presence = Math.min(1, a.w / (counts[hand] || 1));
        const height = Math.max(6, Math.round(100 * presence)) + '%';
        if (mode === 'ev' || mode === 'equity') {
          const v = a[key];
          const t = v === null || hi === lo ? 0.5 : (v - lo) / (hi - lo);
          cell.style.background = 'linear-gradient(rgba(var(--heat), ' + (0.12 + 0.78 * t).toFixed(2) + '), rgba(var(--heat), ' + (0.12 + 0.78 * t).toFixed(2) + ')) bottom / 100% ' + height + ' no-repeat, var(--g-empty)';
          if (v !== null) cell.append(el('span', { class: 'v' }, mode === 'equity' ? pct(v) : num(v, 2)));
        } else {
          let stops;
          if (strat) {
            let x = 0;
            stops = [];
            a.s.forEach((f, k) => { if (f > 0) { stops.push(cols[k] + ' ' + (100 * x).toFixed(1) + '% ' + (100 * (x + f)).toFixed(1) + '%'); x += f; } });
          }
          const fill = stops && stops.length ? 'linear-gradient(to right, ' + stops.join(', ') + ')' : 'linear-gradient(var(--axis), var(--axis))';
          cell.style.background = fill + ' bottom / 100% ' + height + ' no-repeat, var(--g-empty)';
          if (mode === 'strategy_ev' && a.ev !== null) cell.append(el('span', { class: 'v' }, num(a.ev, 2)));
        }
        cell.addEventListener('mouseenter', () => { hovered = hand; renderCombos(); });
        cell.addEventListener('click', () => { selected = selected === hand ? null : hand; renderGridSelection(); renderCombos(); });
      }
      grid.append(cell);
    }
    grid.onmouseleave = () => { hovered = null; renderCombos(); };
    const legend = $('legend');
    legend.textContent = '';
    if (strat && (mode === 'strategy' || mode === 'strategy_ev')) {
      nodeLabels().forEach((label, k) => legend.append(el('span', {}, el('i', { style: 'background:' + cols[k] }), label)));
    }
    legend.append(el('span', {}, mode === 'equity' ? 'Couleur : équité de la main (plus foncé = plus forte).'
      : mode === 'ev' ? 'Couleur : EV de la main (plus foncé = plus élevée).'
        : 'Hauteur : part de la main encore présente.' + (mode === 'strategy_ev' ? ' Nombre : EV de la main.' : '')));
    if (mode !== 'strategy' && mode !== 'equity') {
      legend.append(el('span', {}, 'EV en bb à partir de ce moment du coup : un fold vaut 0, le pot déjà au milieu est à gagner, '
        + 'les mises à venir sont dépensées. Dans la grille, l\'EV d\'une main est celle de sa stratégie (moyenne de ses actions '
        + 'selon leurs fréquences) ; le détail par action est au survol.'));
    }
  }

  function renderGridSelection() {
    document.querySelectorAll('#grid .cell').forEach((c) => c.classList.toggle('sel', c.dataset.hand === selected));
  }

  function renderOverview() {
    const box = $('overview');
    box.textContent = '';
    const panel = el('div', { class: 'card' });
    if (node.type === 'action') {
      const p = node.player;
      const role = roleOf(p);
      const agg = aggregate(p);
      const total = Object.values(agg).reduce((s, a) => s + a.w, 0);
      const freqs = node.actions.map((_, k) => Object.values(agg).reduce((s, a) => s + a.w * a.s[k], 0));
      const cols = colors(node.actions);
      panel.append(el('div', { class: 'ttl' }, el('h2', {}, STREET[node.street] + ' · ' + POS[p] + ' · ' + NAME[role] + ' agit'),
        el('span', { class: 'muted small' }, 'pot ' + num(node.pot) + ' bb · tapis ' + num(node.stacks[p]) + ' bb')));
      const tiles = el('div', { class: 'tiles' });
      node.actions.forEach((a, k) => tiles.append(el('div', { class: 'tile', style: 'background:' + cols[k] },
        el('div', { class: 'l' }, nodeLabels()[k]), el('div', { class: 'p' }, total ? pct(freqs[k] / total) : '—'),
        el('div', { class: 'c' }, num(freqs[k], 1) + ' combos'))));
      panel.append(tiles);
      const stack = el('div', { class: 'stack' });
      freqs.forEach((f, k) => stack.append(el('span', { style: 'width:' + (total ? 100 * f / total : 0).toFixed(2) + '%;background:' + cols[k] })));
      panel.append(stack);
    } else {
      panel.append(el('div', { class: 'ttl' }, el('h2', {}, node.type === 'terminal_fold' ? 'Fin du coup : fold' : 'Abattage'),
        el('span', { class: 'muted small' }, 'pot ' + num(node.pot) + ' bb')));
    }
    const eqs = [0, 1].map((p) => {
      const rows = node.hands[p].filter((r) => r[2] !== null);
      const w = rows.reduce((s, r) => s + r[1], 0);
      return w ? rows.reduce((s, r) => s + r[1] * r[2], 0) / w : null;
    });
    panel.append(el('div', { class: 'muted small', style: 'margin-top:6px' },
      'Équité des ranges : ' + [0, 1].map((p) => POS[p] + ' (' + NAME[roleOf(p)] + ') ' + (eqs[p] === null ? '—' : pct(eqs[p]))).join(' · ')));
    box.append(panel);
  }

  function comboCard(row, p, title) {
    const strat = p === node.player;
    const na = node.actions.length;
    const cols = colors(node.actions);
    const labels = nodeLabels();
    const head = el('div', { class: 'ch' }, el('span', {}, title ? title + ' ' : '', cards([row[0].slice(0, 2), row[0].slice(2)])),
      el('span', { class: 'muted' }, (row[1] < 0.995 ? 'présence ' + pct(row[1]) + ' · ' : '') + (row[2] === null ? '' : 'éq. ' + pct(row[2]))));
    const box = el('div', { class: 'combo' }, head);
    if (strat) {
      const s = row.slice(4, 4 + na);
      const evs = row.slice(4 + na, 4 + 2 * na);
      const bar = el('div', { class: 'sb' });
      s.forEach((f, k) => bar.append(el('span', { style: 'width:' + (100 * f).toFixed(1) + '%;background:' + cols[k] })));
      box.append(bar);
      const best = evs.reduce((b, e, k) => (e !== null && (b < 0 || e > evs[b]) ? k : b), -1);
      const body = el('tbody', {});
      node.actions.forEach((a, k) => {
        const tds = [el('td', {}, el('span', { class: 'sw', style: 'background:' + cols[k] }), labels[k])];
        if (mode !== 'ev' && mode !== 'equity') tds.push(el('td', { class: 'n' }, pct(s[k])));
        if (mode !== 'strategy') tds.push(el('td', { class: 'n' }, evs[k] === null ? '—' : num(evs[k], 2)));
        body.append(el('tr', { class: k === best && mode !== 'strategy' ? 'best' : '' }, tds));
      });
      box.append(el('table', {}, body));
      if (mode !== 'strategy' && row[3] !== null) {
        box.append(el('div', { class: 'muted evs' }, 'EV de la main avec cette stratégie : ' + num(row[3], 2) + ' bb'));
      }
    } else {
      box.append(el('div', { class: 'muted' }, 'EV ' + (row[3] === null ? '—' : num(row[3], 2) + ' bb')));
    }
    return box;
  }

  function renderMine() {
    const box = $('mine');
    box.textContent = '';
    const blocks = [];
    const h = playerOf('H');
    const heroRow = findRow(h, meta.hero_cards);
    if (heroRow) blocks.push(comboCard(heroRow, h, 'Ta main'));
    if (revealed()) {
      const v = playerOf('V');
      const row = findRow(v, meta.villain_cards);
      if (row) blocks.push(comboCard(row, v, 'Sa main'));
    }
    if (blocks.length) box.append(el('div', { class: 'combos' }, blocks));
  }

  function renderCombos() {
    const hand = hovered || selected;
    const box = $('combos');
    box.textContent = '';
    $('combos-title').textContent = hand ? 'Mains ' + hand + ' — ' + POS[viewPlayer] + ' · ' + NAME[roleOf(viewPlayer)] : 'Mains';
    $('combos-hint').hidden = !!hand;
    if (!hand) return;
    const a = aggregate(viewPlayer)[hand];
    if (!a) { box.append(el('div', { class: 'empty' }, 'Pas dans la range à ce moment du coup.')); return; }
    a.rows.slice().sort((x, y) => y[1] - x[1]).forEach((row) => box.append(comboCard(row, viewPlayer)));
  }

  function render() {
    renderMeta();
    renderStatus();
    if (!node) return;
    renderRibbon();
    renderToolbar();
    renderGrid();
    renderOverview();
    renderMine();
    renderCombos();
    $('b-back').disabled = !path.some((s) => s.type === 'action');
  }

  function initialPath() {
    const decisions = state.result ? state.result.decisions : [];
    const k = Number(new URLSearchParams(location.hash.slice(1)).get('d'));
    return (decisions[k] || decisions[0] || { path: [] }).path;
  }

  // ---------- démarrage ----------
  $('b-back').onclick = back;
  $('b-line').onclick = () => state && state.result && goTo(state.result.decisions[0].path);
  $('reveal').onchange = render;
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { closePicker(); if (selected) { selected = null; renderGridSelection(); renderCombos(); } }
    else if (e.key === 'ArrowLeft' && node) { back(); e.preventDefault(); }
  });
  document.addEventListener('click', (e) => {
    const picker = $('picker');
    if (!picker.hidden && !picker.contains(e.target) && !e.target.closest('.step.cards')) closePicker();
  });

  (async () => {
    try {
      await loadState();
    } catch (e) {
      $('status').textContent = e.message;
      return;
    }
    renderMeta();
    renderStatus();
    if (state.result) await goTo(initialPath());
    else $('grid').append(el('div', { class: 'empty', style: 'grid-column: 1 / -1' }, 'Résous ce coup pour voir la stratégie du solveur.'));
    if (state.state === 'done' && !live && state.study) solve();  // l'étude se rouvre seule, en quelques secondes
    else poll();
  })();
})();
