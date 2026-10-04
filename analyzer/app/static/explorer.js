(function () {
  'use strict';

  const HAND = document.body.dataset.hand;
  const PRE = HAND === 'preflop';  // arbre préflop de la solution, jusqu'au choix du flop d'un spot d'étude
  const SPOT = HAND.startsWith('spot:');  // spot d'étude : pas de main jouée, les joueurs sont nommés par leur position
  const FAMILY = SPOT ? HAND.split(':')[1] : null;
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
  let rightTab = 'combos';
  let preLine = [];   // PRE : actions préflop depuis l'open du bouton
  let picked = [];    // PRE : cartes choisies pour le flop
  let flopInfo = null;  // PRE : le flop choisi et les flops résolus proches (/api/explorateur/flop)
  let prefix = null;  // SPOT : la ligne préflop de la famille (nœud « flop »), en tête du déroulé
  const nodes = new Map();
  // Filtres : clés « m:i » (main faite), « d:i » (tirage), « e:i » / « q:i » (équité), « o:s » / « s:s » (couleurs).
  const filters = { mode: 'include', keys: new Set() };
  const EQ_SIMPLE = [['Meilleures mains', 0.7, 2], ['Mains bonnes', 0.5, 0.7], ['Mains faibles', 0.33, 0.5], ['Mains poubelles', -1, 0.33]];
  const EQ_ADVANCED = [['Mains 90-100', 0.9, 2], ['Mains 80-90', 0.8, 0.9], ['Mains 70-80', 0.7, 0.8], ['Mains 60-70', 0.6, 0.7],
    ['Mains 50-60', 0.5, 0.6], ['Mains 25-50', 0.25, 0.5], ['Mains 0-25', -1, 0.25]];

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
  const who = (p) => (SPOT || PRE ? POS[p] : POS[p] + ' · ' + NAME[roleOf(p)]);
  const streetName = (s) => (s < 0 ? 'Préflop' : STREET[s]);
  const preHref = (line) => '/explorateur/preflop#ligne=' + line.join('.');

  // Libellés des actions d'un nœud. Mise : % du pot ; relance : montant ajouté en % du pot après le
  // call (convention des solveurs : relancer à 4,5 sur une mise de 1,7 dans un pot de 5 = 33 %).
  function actLabels(actions, pot, put, player) {
    const call = actions.find((x) => x.kind === 'call');
    const base = call && put && player !== null && player !== undefined ? 2 * put[1 - player] : 0;
    return actions.map((a) => {
      if (a.name) return a.name;  // préflop : « Open 2,5 », « 3bet 11,5 »…
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

  // Un nœud à une seule action (la BB qui ne peut que checker, sans donk).
  const forced = (n) => n.type === 'action' && n.actions.length === 1;

  // forward=true (on avance dans le coup) : dans un spot d'étude, les nœuds forcés sont passés.
  async function goTo(target, forward = false) {
    let p = target.slice();
    document.body.classList.add('busy');  // un nœud proche du flop peut prendre une ou deux secondes
    try {
      // Sans session, seules les décisions de la ligne jouée sont connues : on passe les cartes jouées.
      if (!live) while (prefixOf(p, played) && p.length < played.length && played[p.length].type === 'card') p.push(played[p.length]);
      let n = await fetchNode(p);
      let pick = null;  // carte posée d'office (pas de carte réelle) : on propose de la choisir
      for (;;) {
        if (n.type === 'chance') {
          const real = meta.board[n.board.length];
          if (!n.cards.includes(real) && pick === null) pick = p.length;
          p = p.concat([{ type: 'card', card: n.cards.includes(real) ? real : n.cards[0] }]);
        } else if (forward && SPOT && forced(n)) {
          p = p.concat([{ type: 'action', index: 0 }]);
        } else break;
        n = await fetchNode(p);
      }
      path = p;
      node = n;
      if (viewAuto && n.player !== null && n.player !== undefined) viewPlayer = n.player;
      selected = null;
      notice = '';
      render();
      if (pick !== null) {
        const btn = document.querySelector('#ribbon [data-k="' + pick + '"]');
        if (btn) setTimeout(() => openPicker(pick, btn), 0);
      }
    } catch (e) {
      notice = e.message;
      if (e.status === 409 && live) {  // session fermée entre-temps (inactivité) : on propose de recalculer
        live = false;
        try { await loadState(); } catch (err) { /* l'avis suffit */ }
      }
      render();
    } finally {
      document.body.classList.remove('busy');
    }
  }

  async function back() {
    if (PRE) { if (preLine.length) goLine(preLine.slice(0, -1)); return; }
    const p = path.slice();
    for (;;) {
      while (p.length && p[p.length - 1].type === 'card') p.pop();
      if (!p.length) { if (prefix) location.href = preHref(prefix.preflop.line); return; }  // retour au préflop
      p.pop();
      // au début d'un spot, un check forcé (la BB ne mène pas) ramène directement au préflop
      if (SPOT && !p.length && prefix && nodes.has('[]') && forced(nodes.get('[]'))) { location.href = preHref(prefix.preflop.line); return; }
      if (!SPOT || !p.length) break;
      // dans un spot, on remonte aussi au-delà d'un nœud forcé (sauf la racine)
      const n = nodes.get(JSON.stringify(p));
      if (!n || !forced(n)) break;
    }
    goTo(p);
  }

  async function goLine(line) {
    document.body.classList.add('busy');
    try {
      node = await api('/api/explorateur/preflop', { line });
      if (preLine.join('.') !== line.join('.')) { picked = []; flopInfo = null; }
      preLine = line.slice();
      history.replaceState(null, '', '#ligne=' + preLine.join('.'));
      if (node.player !== null && node.player !== undefined) viewPlayer = node.player;
      selected = null;
      notice = '';
    } catch (e) {
      notice = e.message;
    } finally {
      document.body.classList.remove('busy');
    }
    render();
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
          if (SPOT && !live && state.study) { solve(); return; }  // résolu sans session : on rouvre l'étude
          renderMeta();
          await (node ? goTo(path) : goStart());
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
    if (PRE) {
      $('meta').textContent = node ? 'Préflop · ' + node.preflop.description : 'Préflop';
      return;
    }
    if (SPOT) {
      const box = $('meta');
      box.textContent = '';
      box.title = meta.family_label + (meta.sizes ? '\nTailles : ' + meta.sizes : '');
      box.append('Spot d\'étude · ' + meta.pot_type + ' · BTN contre BB · flop ', cards(meta.board), ' · ' + meta.texture
        + ' · pot ' + num(meta.pot) + ' bb, tapis ' + num(meta.stack) + ' bb');
      return;
    }
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
    if (PRE) {
      if (node) box.append(el('span', {}, node.type === 'flop' ? 'Choisis le flop : les flops résolus s\'ouvrent en quelques secondes.'
        : node.type === 'allin' ? 'Tapis préflop : pas de jeu après le flop à étudier.'
          : 'Solution préflop : choisis les actions dans le déroulé ; au call, tu choisis le flop et le coup continue au postflop.'));
      if (notice) box.append(el('span', { class: 'err' }, notice));
      return;
    }
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
        : state.mode === 'choose' ? 'Flop de la série : choix des tailles de mise, puis résolution. ' + (p.stage || '') + elapsed
        : (p.iteration ? 'Résolution : itération ' + p.iteration + ' / ' + state.max_iterations + ' · exploitabilité ' + num(p.exploit_pct) + ' % du pot (objectif ' + num(state.target) + ' %)'
          : p.tree_nodes ? 'Résolution lancée : arbre de ' + p.tree_nodes.toLocaleString('fr-FR') + ' nœuds, première mesure après 10 itérations'
            : 'Construction de l\'arbre…') + elapsed),
      el('div', { class: 'bar' }, el('span', { style: 'width:' + (100 * frac).toFixed(1) + '%' })),
      el('button', { type: 'button', onclick: () => api('/api/resoudre/' + encodeURIComponent(state.job) + '/arreter', {}).then((v) => { Object.assign(state, v); renderStatus(); }) }, 'Arrêter'));
    } else if (s === 'done' && live) {
      const r = state.result;
      box.append(el('span', {}, 'Session active : toutes les branches et toutes les cartes sont explorables (fermée après 30 min sans activité). '
        + r.iterations + ' itérations, exploitabilité ' + num(r.exploit_pct, 2) + ' % du pot.'));
    } else if (s === 'done' && state.study && SPOT) {
      box.append(el('span', {}, 'Étude enregistrée.'),
        el('button', { type: 'button', class: 'go', onclick: solve }, 'Ouvrir l\'étude (quelques secondes)'));
    } else if (s === 'done' && SPOT) {
      box.append(el('span', {}, 'L\'étude de ce spot a été supprimée : recalcule-le pour l\'explorer.'),
        el('button', { type: 'button', class: 'go', onclick: solve }, 'Recalculer (quelques minutes)'));
    } else if (s === 'done' && state.study) {
      box.append(el('span', {}, 'Étude enregistrée : ligne jouée affichée.'),
        el('button', { type: 'button', class: 'go', onclick: solve }, 'Ouvrir l\'étude complète (quelques secondes)'));
    } else if (s === 'done') {
      box.append(el('span', {}, 'Ligne jouée seulement (résultat enregistré). Pour explorer les autres branches et changer les cartes :'),
        el('button', { type: 'button', class: 'go', onclick: solve }, 'Recalculer (quelques minutes)'));
    } else {
      if (state.error) box.append(el('span', { class: 'err' }, state.error));
      box.append(el('span', {}, SPOT ? 'Ce spot n\'est pas encore résolu (quelques minutes ; il sera gardé dans les études).'
        : 'Ce coup n\'est pas encore résolu.'),
        el('button', { type: 'button', class: 'go', onclick: solve }, SPOT ? 'Résoudre ce spot' : 'Résoudre ce coup'));
    }
    if (notice) box.append(el('span', { class: 'err' }, notice));
  }

  // Une case du déroulé ramène à son moment du coup (sans session : seulement les décisions de la ligne jouée).
  function reachable(p) {
    return live || (state.result && state.result.decisions.some((d) => same(d.path, p)));
  }
  function navStep(step, target, current) {
    if (current || !reachable(target)) return step;
    step.classList.add('nav');
    step.title = 'Revenir à ce moment du coup';
    step.addEventListener('click', (e) => { if (!e.target.closest('button, a')) goTo(target); });
    return step;
  }

  function actionStep(h, k, current) {
    const role = roleOf(h.player);
    const step = el('div', { class: 'step' + (current ? ' current' : '') },
      el('div', { class: 'head' }, el('span', { class: role }, who(h.player)),
        el('span', { title: 'tapis ' + num(h.stack) + ' bb' }, 'pot ' + num(h.pot))));
    const prefix = path.slice(0, k);
    navStep(step, prefix, current);
    const onLine = prefixOf(prefix, played) && played[k] && played[k].type === 'action';
    const labels = actLabels(h.actions, h.pot, h.put, h.player);
    h.actions.forEach((a, j) => {
      const isPlayed = onLine && played[k].index === j;
      const allowed = live || isPlayed;
      step.append(el('button', {
        type: 'button', class: 'act' + (h.chosen === j ? ' on' : ''), disabled: !allowed,
        title: allowed ? null : 'Hors de la ligne jouée : recalcule pour explorer cette branche',
        onclick: () => goTo(prefix.concat([{ type: 'action', index: j }]), true),
      }, el('span', {}, labels[j]), isPlayed ? el('span', { class: 'dot', title: 'Joué dans la main' }, '●') : ''));
    });
    return step;
  }

  function cardStep(h, k) {
    const btn = el('button', {
      type: 'button', disabled: !live, title: live ? 'Changer de carte' : 'Recalcule pour changer de carte', 'data-k': k,
      onclick: (e) => openPicker(k, e.currentTarget),
    }, h.card ? card(h.card) : '?');
    const step = el('div', { class: 'step cards' }, el('div', { class: 'head' }, el('span', {}, STREET[h.street] || 'Carte'), el('span', {}, 'pot ' + num(h.pot))), btn);
    return navStep(step, path.slice(0, k + 1), false);
  }

  // Étape préflop : chaque action mène à son nœud (navigate reçoit la ligne jusqu'à cette action).
  function preStep(h, k, line, current, navigate) {
    const step = el('div', { class: 'step pre' + (current ? ' current' : '') },
      el('div', { class: 'head' }, el('span', {}, POS[h.player] + ' · préflop'), el('span', {}, 'pot ' + num(h.pot))));
    h.actions.forEach((a, j) => step.append(el('button', {
      type: 'button', class: 'act' + (h.chosen === j ? ' on' : ''), onclick: () => navigate(line.slice(0, k).concat([h.keys[j]])),
    }, el('span', {}, a.name))));
    return step;
  }

  function renderRibbon() {
    const box = $('ribbon');
    box.textContent = '';
    if (PRE) {
      node.history.forEach((h, k) => {
        const current = k === node.history.length - 1;
        if (h.kind === 'action') box.append(preStep(h, k, preLine, current, goLine));
        else if (h.kind === 'flop') box.append(el('div', { class: 'step cards current' },
          el('div', { class: 'head' }, el('span', {}, 'Flop'), el('span', {}, 'pot ' + num(h.pot))), el('span', { class: 'muted small' }, 'à choisir')));
        else box.append(el('div', { class: 'step end current' }, h.kind === 'terminal_fold' ? 'Fin : fold' : 'Tapis préflop'));
      });
      box.scrollLeft = box.scrollWidth;
      return;
    }
    if (prefix) {
      prefix.history.forEach((h, k) => {
        if (h.kind === 'action') box.append(preStep(h, k, prefix.preflop.line, false, (line) => { location.href = preHref(line); }));
      });
    }
    box.append(navStep(el('div', { class: 'step cards' },
      el('div', { class: 'head' }, el('span', {}, 'Flop'), el('span', {}, 'pot ' + num(state.result.pot))), cards(meta.board.slice(0, 3)),
      prefix ? el('a', { class: 'change', href: preHref(prefix.preflop.line), title: 'Choisir un autre flop' }, 'changer') : ''),
    [], path.length === 0));
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
        onclick: () => { closePicker(); goTo(path.slice(0, k).concat([{ type: 'card', card: c }]), true); },
      }, r + SUITS[s]));
    }
    picker.append(rows);
    picker.hidden = false;
    const rect = anchor.getBoundingClientRect();
    picker.style.left = Math.max(8, Math.min(rect.left, innerWidth - picker.offsetWidth - 8)) + 'px';
    picker.style.top = (rect.bottom + 6) + 'px';
  }
  function closePicker() { $('picker').hidden = true; }

  function comboKeys(p, i, row) {
    const keys = [];
    const c = node.cats && node.cats[p] && node.cats[p][i];
    if (c) {
      keys.push('m:' + c[0]);
      state.categories.draws.forEach((_, b) => { if (c[1] & (1 << b)) keys.push('d:' + b); });
    }
    const eq = row[2];
    if (eq !== null) {
      EQ_SIMPLE.forEach(([, lo, hi], k) => { if (eq >= lo && eq < hi) keys.push('e:' + k); });
      EQ_ADVANCED.forEach(([, lo, hi], k) => { if (eq >= lo && eq < hi) keys.push('q:' + k); });
    }
    const s1 = row[0][1], s2 = row[0][3];
    if (s1 === s2) keys.push('s:' + s1); else keys.push('o:' + s1, 'o:' + s2);
    return keys;
  }
  const filterActive = () => filters.keys.size > 0;

  function passes(p, i, row) {
    if (!filterActive()) return true;
    const keys = comboKeys(p, i, row);
    if (filters.mode === 'exclude') return !keys.some((k) => filters.keys.has(k));
    const sel = [...filters.keys];
    const hands = sel.filter((k) => k[0] !== 'o' && k[0] !== 's');
    const suits = sel.filter((k) => k[0] === 'o' || k[0] === 's');
    return (!hands.length || hands.some((k) => keys.includes(k))) && (!suits.length || suits.some((k) => keys.includes(k)));
  }

  function aggregate(p, filtered = true) {
    const na = node.actions.length;
    const strat = p === node.player;
    const out = {};
    node.hands[p].forEach((row, i) => {
      if (filtered && p === viewPlayer && !passes(p, i, row)) return;
      const cls = classOf(row[0]);
      const r = row[1];
      const a = out[cls] || (out[cls] = { w: 0, ev: 0, evw: 0, eq: 0, eqw: 0, s: new Array(na).fill(0), rows: [] });
      a.w += r;
      a.rows.push(row);
      if (row[3] !== null) { a.ev += r * row[3]; a.evw += r; }
      if (row[2] !== null) { a.eq += r * row[2]; a.eqw += r; }
      if (strat) for (let j = 0; j < na; j++) a.s[j] += r * row[4 + j];
    });
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
    (PRE ? MODES.slice(0, 1) : MODES).forEach(([id, label]) => modes.append(el('button', {
      type: 'button', 'aria-pressed': mode === id ? 'true' : 'false',
      onclick: () => { mode = id; render(); },
    }, label)));
    const players = $('players');
    players.textContent = '';
    [0, 1].forEach((p) => players.append(el('button', {
      type: 'button', 'aria-pressed': viewPlayer === p ? 'true' : 'false',
      onclick: () => { viewPlayer = p; viewAuto = actor && p === node.player; selected = null; render(); },
    }, who(p) + (actor && node.player === p ? ' (agit)' : ''))));
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
      const full = filterActive() && p === viewPlayer ? node.hands[p].reduce((s, r) => s + r[1], 0) : 0;
      const freqs = node.actions.map((_, k) => Object.values(agg).reduce((s, a) => s + a.w * a.s[k], 0));
      const cols = colors(node.actions);
      panel.append(el('div', { class: 'ttl' }, el('h2', {}, streetName(node.street) + ' · ' + who(p) + ' agit'),
        el('span', { class: 'muted small' }, 'pot ' + num(node.pot) + ' bb · tapis ' + num(node.stacks[p]) + ' bb')));
      const tiles = el('div', { class: 'tiles' });
      node.actions.forEach((a, k) => tiles.append(el('div', { class: 'tile', style: 'background:' + cols[k] },
        el('div', { class: 'l' }, nodeLabels()[k]), el('div', { class: 'p' }, total ? pct(freqs[k] / total) : '—'),
        el('div', { class: 'c' }, num(freqs[k], 1) + ' combos'))));
      panel.append(tiles);
      const stack = el('div', { class: 'stack' });
      freqs.forEach((f, k) => stack.append(el('span', { style: 'width:' + (total ? 100 * f / total : 0).toFixed(2) + '%;background:' + cols[k] })));
      panel.append(stack);
      if (full) panel.append(el('div', { class: 'muted small', style: 'margin-top:6px' },
        'Filtre actif : ' + pct(total / full) + ' de la range (' + num(total, 1) + ' combos) ; fréquences de ces mains seulement.'));
    } else if (node.type === 'flop') {
      box.append(flopChooser());
      return;
    } else {
      panel.append(el('div', { class: 'ttl' }, el('h2', {}, node.type === 'terminal_fold' ? 'Fin du coup : fold'
        : node.type === 'allin' ? 'Tapis préflop' : 'Abattage'),
      el('span', { class: 'muted small' }, 'pot ' + num(node.pot) + ' bb')));
    }
    const eqs = [0, 1].map((p) => {
      const rows = node.hands[p].filter((r) => r[2] !== null);
      const w = rows.reduce((s, r) => s + r[1], 0);
      return w ? rows.reduce((s, r) => s + r[1] * r[2], 0) / w : null;
    });
    if (eqs.some((e) => e !== null)) {
      panel.append(el('div', { class: 'muted small', style: 'margin-top:6px' },
        'Équité des ranges : ' + [0, 1].map((p) => (SPOT ? POS[p] : POS[p] + ' (' + NAME[roleOf(p)] + ')') + ' ' + (eqs[p] === null ? '—' : pct(eqs[p]))).join(' · ')));
    } else if (PRE) {
      panel.append(el('div', { class: 'muted small', style: 'margin-top:6px' }, 'Fréquences de la solution préflop (pas d\'EV à ce stade).'));
    }
    box.append(panel);
  }

  // Fin de la ligne préflop (call) : trois cartes parmi les 52, avec les flops déjà résolus qui s'en approchent.
  async function pickCard(c) {
    const i = picked.indexOf(c);
    if (i >= 0) picked.splice(i, 1);
    else if (picked.length < 3) picked.push(c);
    flopInfo = null;
    if (picked.length === 3) {
      try {
        flopInfo = await api('/api/explorateur/flop', { family: node.preflop.family, board: picked });
      } catch (e) { notice = e.message; renderStatus(); }
    }
    renderOverview();
  }

  const spotHref = (id) => '/explorateur/' + encodeURIComponent(id);

  function flopResult() {
    const f = flopInfo;
    const box = el('div', { class: 'fl-result' }, el('div', { class: 'fl-pick' }, cards(f.board),
      el('span', { class: 'muted small' }, f.texture + ' · ' + f.pattern)));
    if (f.solved) {
      box.append(el('a', { class: 'fl-go', href: spotHref(f.id) }, 'Ouvrir l\'étude de ce flop'));
      return box;
    }
    if (f.suggestions.length) {
      box.append(el('div', { class: 'small' }, 'Déjà résolus, à ouvrir tout de suite :'));
      box.append(el('ul', { class: 'fl-sugg' }, f.suggestions.map((x) => el('li', {},
        el('a', { class: 'fl solved', href: spotHref(x.id) }, cards(x.board)), el('span', { class: 'muted small' }, ' ' + x.relation)))));
    }
    box.append(el('a', { class: 'fl-go', href: spotHref(f.id) + '#resoudre', title: 'Tailles : ' + f.sizes },
      'Résoudre ce flop (' + f.cost + ')'));
    box.append(el('div', { class: 'muted small' }, f.sizes_from
      ? 'Tailles du flop le plus proche dont les tailles sont choisies (' + f.sizes_from.match(/../g).map((c) => c[0] + SUITS[c[1]]).join('') + '). L\'étude est gardée.'
      : 'L\'étude est gardée ensuite.'));
    return box;
  }

  function flopChooser() {
    const info = node.preflop;
    const panel = el('div', { class: 'card' }, el('div', { class: 'ttl' }, el('h2', {}, 'Flop · ' + info.family_name),
      el('span', { class: 'muted small' }, 'pot ' + num(node.pot) + ' bb · tapis ' + num(node.stacks[0]) + ' bb')));
    panel.append(el('p', { class: 'muted small fl-note' }, info.family_label + '. Grille : la range de chacun au flop.'));
    const deck = el('div', { class: 'deck', role: 'group', 'aria-label': 'Cartes du flop' });
    for (const st of 'shdc') {
      for (const r of RANKS) {
        const c = r + st, on = picked.includes(c);
        deck.append(el('button', {
          type: 'button', class: 'dc s' + st + (on ? ' on' : ''), 'aria-pressed': on ? 'true' : 'false',
          disabled: picked.length >= 3 && !on, onclick: () => pickCard(c),
        }, r + SUITS[st]));
      }
    }
    panel.append(el('div', { class: 'deck-head' }, el('b', {}, 'Choisis trois cartes'),
      picked.length ? cards(picked) : el('span', { class: 'muted small' }, 'ou un flop résolu ci-dessous'),
      picked.length ? el('button', { type: 'button', class: 'linkish', onclick: () => { picked = []; flopInfo = null; renderOverview(); } }, 'Effacer') : ''),
    deck);
    if (picked.length === 3 && flopInfo) panel.append(flopResult());
    panel.append(el('div', { class: 'fl-head' }, el('b', {}, 'Flops de la série et flops déjà résolus'),
      el('span', { class: 'muted small' }, ' surlignés quand ils sont résolus')));
    const groups = el('div', { class: 'flops' });
    for (const t of info.textures) {
      const list = info.spots.filter((x) => x.texture === t);
      if (!list.length) continue;
      groups.append(el('div', { class: 'fl-group' }, el('div', { class: 'fl-t' }, t), el('div', { class: 'fl-list' }, list.map((x) => el('a', {
        class: 'fl' + (x.solved ? ' solved' : ''), href: '/explorateur/' + encodeURIComponent(x.id),
        title: x.solved ? 'Ouvrir l\'étude' : 'Pas encore résolu : il se résout à l\'ouverture',
      }, cards(x.board))))));
    }
    panel.append(groups);
    return panel;
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
    } else if (!PRE) {
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
    $('combos-title').textContent = hand ? 'Mains ' + hand + ' — ' + who(viewPlayer) : 'Mains';
    $('combos-hint').hidden = !!hand;
    if (!hand) return;
    const a = aggregate(viewPlayer)[hand];
    if (!a) {
      box.append(el('div', { class: 'empty' }, filterActive() && aggregate(viewPlayer, false)[hand]
        ? 'Aucun combo de cette case ne passe le filtre.' : 'Pas dans la range à ce moment du coup.'));
      return;
    }
    a.rows.slice().sort((x, y) => y[1] - x[1]).forEach((row) => box.append(comboCard(row, viewPlayer)));
  }

  // ---------- filtres ----------
  function toggleFilter(key) {
    if (filters.keys.has(key)) filters.keys.delete(key); else filters.keys.add(key);
    render();
  }
  function clearFilters() { filters.keys.clear(); render(); }

  function renderFilters() {
    const box = $('filters');
    box.textContent = '';
    const p = viewPlayer;
    const strat = p === node.player;
    const na = node.actions.length;
    const cols = strat ? colors(node.actions) : [];
    const rows = node.hands[p];
    const total = rows.reduce((s, r) => s + r[1], 0);
    const stats = {};
    rows.forEach((row, i) => {
      for (const k of comboKeys(p, i, row)) {
        const st = stats[k] || (stats[k] = { w: 0, s: new Array(na).fill(0) });
        st.w += row[1];
        if (strat) for (let j = 0; j < na; j++) st.s[j] += row[1] * row[4 + j];
      }
    });
    const head = el('div', { class: 'fhead' });
    const seg = el('div', { class: 'seg', role: 'group', 'aria-label': 'Mode du filtre' });
    [['include', 'Inclure'], ['exclude', 'Exclure']].forEach(([id, label]) => seg.append(el('button', {
      type: 'button', 'aria-pressed': filters.mode === id ? 'true' : 'false', onclick: () => { filters.mode = id; render(); },
    }, label)));
    head.append(seg, el('span', { class: 'muted small' }, 'Range : ' + who(p)));
    if (filterActive()) head.append(el('button', { type: 'button', class: 'clear', onclick: clearFilters }, 'Effacer les filtres'));
    box.append(head);

    const suits = el('div', { class: 'suits' });
    const suitBtn = (key, text, cls, title) => el('button', {
      type: 'button', class: cls, title, 'aria-pressed': filters.keys.has(key) ? 'true' : 'false', onclick: () => toggleFilter(key),
    }, text);
    suits.append(el('div', { class: 'grp' }, el('span', {}, 'Dépareillées'),
      [...'shdc'].map((s) => suitBtn('o:' + s, SUITS[s], 's' + s, 'Mains dépareillées avec une carte ' + SUITS[s]))));
    suits.append(el('div', { class: 'grp' }, el('span', {}, 'Assorties'),
      [...'shdc'].map((s) => suitBtn('s:' + s, SUITS[s] + SUITS[s], 's' + s, 'Mains assorties à ' + SUITS[s]))));
    box.append(suits);

    const row = (key, label, hideEmpty) => {
      const st = stats[key];
      if (!st && hideEmpty) return null;
      const w = st ? st.w : 0;
      const bar = el('span', { class: 'fbar' });
      if (st && strat && st.w) st.s.forEach((x, j) => bar.append(el('span', { style: 'width:' + (100 * x / st.w).toFixed(1) + '%;background:' + cols[j] })));
      else if (w) bar.append(el('span', { style: 'width:' + (100 * w / total).toFixed(1) + '%;background:var(--axis)' }));
      return el('button', {
        type: 'button', class: 'frow' + (w ? '' : ' empty'), 'aria-pressed': filters.keys.has(key) ? 'true' : 'false',
        title: strat ? 'Barre : actions de ces mains' : 'Barre : part de la range', onclick: () => toggleFilter(key),
      }, el('span', {}, label), el('span', { class: 'pc-share' }, total ? (100 * w / total).toFixed(1).replace('.', ',') + ' %' : '—'), bar);
    };
    const groups = el('div', { class: 'fgroups' });
    const group = (title, items) => {
      const rowsEl = items.filter(Boolean);
      if (rowsEl.length) groups.append(el('div', { class: 'fgroup' }, el('h3', {}, title), rowsEl));
    };
    group('Mains', state.categories.made.map(([, label], k) => row('m:' + k, label, true)));
    if (node.board.length < 5) group('Tirages', state.categories.draws.map(([, label], k) => row('d:' + k, label, true)));
    group('Équité — simple', EQ_SIMPLE.map(([label], k) => row('e:' + k, label, false)));
    group('Équité — avancée', EQ_ADVANCED.map(([label], k) => row('q:' + k, label, false)));
    box.append(groups);
    box.append(el('p', { class: 'muted small' }, 'Clique une ou plusieurs lignes pour ne garder (ou écarter) ces mains dans la grille, '
      + 'la synthèse et le détail des combos. Les catégories se combinent (« ou ») ; les couleurs s\'ajoutent comme une condition (« et »). '
      + 'L\'équité est celle de la main contre la range adverse à ce moment du coup.'));
  }

  // Le coach sait ce que l'élève regarde : le spot, la ligne jouée jusqu'ici et la case sélectionnée.
  let coachMounted = false;
  function mountCoach() {
    if (coachMounted || !window.AnalyzerCoach) return;
    coachMounted = true;
    window.AnalyzerCoach.mount($('coach-box'), {
      context: () => (PRE ? null : { spot: HAND, path, main: selected || null }),
      suggestions: PRE ? ['Comment construire ma range de défense de BB ?', 'Pourquoi 3better certaines mains en bluff ?']
        : ['Pourquoi le solveur joue-t-il ainsi ici ?', 'Quelle stratégie simple retenir ici ?',
          'Quelles mains mettent la pression ici, et pourquoi ?', 'Comment exploiter mon adversaire ici ?'],
    });
  }

  function renderTabs() {
    const n = filters.keys.size;
    $('tab-combos').setAttribute('aria-selected', rightTab === 'combos' ? 'true' : 'false');
    $('tab-filters').setAttribute('aria-selected', rightTab === 'filters' ? 'true' : 'false');
    $('tab-coach').setAttribute('aria-selected', rightTab === 'coach' ? 'true' : 'false');
    $('pane-coach').hidden = rightTab !== 'coach';
    if (rightTab === 'coach') mountCoach();
    $('tab-filters').textContent = 'Filtres';
    if (n) $('tab-filters').append(el('span', { class: 'count' }, n));
    $('pane-combos').hidden = rightTab !== 'combos';
    $('pane-filters').hidden = rightTab !== 'filters';
    const badge = $('filter-badge');
    badge.textContent = '';
    badge.hidden = !n;
    if (n) badge.append(el('span', {}, el('b', {}, 'Filtre actif'), ' (' + n + ', ' + (filters.mode === 'include' ? 'inclure' : 'exclure') + ')'),
      el('button', { type: 'button', onclick: () => { rightTab = 'filters'; renderTabs(); } }, 'Voir'),
      el('button', { type: 'button', onclick: clearFilters }, 'Effacer'));
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
    renderFilters();
    renderTabs();
    $('b-back').disabled = PRE ? !preLine.length : !path.some((s) => s.type === 'action') && !prefix;
    // L'entraîneur rejoue depuis ce nœud : il faut l'étude ouverte et une vraie décision.
    $('b-train').disabled = PRE || !live || node.type !== 'action' || node.actions.length < 2;
  }

  function initialPath() {
    const decisions = state.result ? state.result.decisions : [];
    const hash = new URLSearchParams(location.hash.slice(1));
    // #chemin=[…] : un moment précis du coup (lien de l'entraîneur)
    if (hash.has('chemin') && live) {
      try {
        const p = JSON.parse(hash.get('chemin'));
        if (Array.isArray(p) && p.every((s) => s && (s.type === 'action' || s.type === 'card'))) return p;
      } catch (e) { /* chemin illisible : départ habituel */ }
    }
    const k = Number(hash.get('d'));
    return (decisions[k] || decisions[0] || { path: [] }).path;
  }

  // Premier nœud affiché ; dans un spot d'étude, on passe le check forcé de la BB (qui ne mène pas).
  const goStart = () => goTo(initialPath(), true);

  // ---------- démarrage ----------
  $('b-back').onclick = back;
  $('tab-combos').onclick = () => { rightTab = 'combos'; renderTabs(); };
  $('tab-filters').onclick = () => { rightTab = 'filters'; renderTabs(); };
  $('tab-coach').onclick = () => { rightTab = 'coach'; renderTabs(); };
  $('b-line').onclick = () => state && state.result && goTo(state.result.decisions[0].path);
  $('b-train').onclick = () => window.open('/entraineur?spot=' + encodeURIComponent(HAND) + '&chemin='
    + encodeURIComponent(JSON.stringify(path)), '_blank', 'noopener');
  if (SPOT) {  // pas de ligne jouée ni de main adverse à dévoiler
    $('b-line').hidden = true;
    $('reveal').closest('label').hidden = true;
    const ident = HAND.split(':');
    document.title = 'Explorateur — ' + ident[1].toUpperCase() + ' ' + ident[2];
  }
  $('reveal').onchange = render;
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { closePicker(); if (selected) { selected = null; renderGridSelection(); renderCombos(); } }
    else if (e.key === 'ArrowLeft' && node) { back(); e.preventDefault(); }
  });
  document.addEventListener('click', (e) => {
    const picker = $('picker');
    if (!picker.hidden && !picker.contains(e.target) && !e.target.closest('.step.cards')) closePicker();
  });

  if (window.self !== window.top) document.body.classList.add('embed');  // dans l'application : pas de bandeau
  if (PRE) {
    meta = { hero_cards: [], villain_cards: [], board: [], hero_position: 'BB' };
    state = { state: 'pre', categories: { made: [], draws: [] } };
    mode = 'strategy';
    $('b-line').hidden = true;
    $('reveal').closest('label').hidden = true;
    $('b-train').hidden = true;
    $('tab-filters').hidden = true;  // les filtres (mains faites, tirages, équité) n'ont de sens qu'au postflop
    document.title = 'Explorateur — préflop';
  }
  if (SPOT) {  // ligne préflop du spot, en tête du déroulé
    api('/api/explorateur/preflop', { family: FAMILY }).then((n) => { prefix = n; if (node) render(); }).catch(() => {});
  }

  (async () => {
    if (PRE) {
      const line = (new URLSearchParams(location.hash.slice(1)).get('ligne') || '').split('.').filter(Boolean);
      await goLine(line);
      if (!node) await goLine([]);
      return;
    }
    try {
      await loadState();
    } catch (e) {
      $('status').textContent = e.message;
      return;
    }
    renderMeta();
    renderStatus();
    // Un spot d'étude n'a pas de ligne jouée en cache : il s'affiche une fois l'étude ouverte.
    if (state.result && (live || !SPOT)) await goStart();
    else $('grid').append(el('div', { class: 'empty', style: 'grid-column: 1 / -1' },
      state.study ? 'Ouverture de l\'étude…' : SPOT ? 'Résous ce spot pour voir la stratégie du solveur.'
        : 'Résous ce coup pour voir la stratégie du solveur.'));
    // L'étude se rouvre seule, en quelques secondes (aussi quand le résultat en cache manque, par exemple
    // après une mise à jour du solveur).
    if (['done', 'absent'].includes(state.state) && !live && state.study) solve();
    else if (state.state === 'absent' && SPOT && /resoudre/.test(location.hash)) {  // choisi dans le sélecteur de flop
      history.replaceState(null, '', location.pathname);
      solve();
    } else poll();
  })();
})();
