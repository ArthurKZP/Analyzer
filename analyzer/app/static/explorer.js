(function () {
  'use strict';

  const HAND = document.body.dataset.hand;
  const PRE = HAND === 'preflop';  // arbre préflop de la solution, jusqu'au choix du flop d'un spot d'étude
  const TABLES = [['HU', 'Heads-up'], ['6-max', 'À plusieurs (6-max)']];
  // Petites préférences gardées dans le navigateur (le format du préflop).
  function store(key, value) {
    try {
      if (value === undefined) return localStorage.getItem('analyzer-' + key);
      localStorage.setItem('analyzer-' + key, value);
    } catch (e) { /* stockage indisponible : on fait sans */ }
    return null;
  }
  // PRE : la solution heads-up, ou (#table=6-max) l'arbre des charts 6-max ; sans ligne donnée, le dernier choisi.
  let TABLE = (() => {
    const hash = new URLSearchParams(location.hash.slice(1));
    const wanted = hash.get('table') || (hash.has('ligne') ? 'HU' : store('explorer-table'));
    return TABLES.some(([id]) => id === wanted) ? wanted : 'HU';
  })();
  const SPOT = HAND.startsWith('spot:');  // spot d'étude : pas de main jouée, les joueurs sont nommés par leur position
  const FAMILY = SPOT ? HAND.split(':')[1] : null;
  const $ = (id) => document.getElementById(id);
  const RANKS = 'AKQJT98765432';
  const SUITS = { s: '♠', h: '♥', d: '♦', c: '♣' };
  const STREET = ['Flop', 'Turn', 'River'];
  let POS = ['BB', 'BTN'];  // joueur 0 (hors de position), joueur 1 : à une table à plusieurs, leurs positions
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
  let noCharts = false;  // PRE à plusieurs : pas encore de charts 6-max
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
    const oop = (meta.hero_oop !== undefined ? meta.hero_oop : meta.hero_position === 'BB') ? 'H' : 'V';
    return p === 0 ? oop : (oop === 'H' ? 'V' : 'H');
  }
  const playerOf = (role) => (roleOf(0) === role ? 0 : 1);
  const who = (p) => (SPOT || PRE ? POS[p] : POS[p] + ' · ' + NAME[roleOf(p)]);
  const streetName = (s) => (s < 0 ? 'Préflop' : STREET[s]);
  const tableHash = (table) => (table && table !== 'HU' ? 'table=' + encodeURIComponent(table) + '&' : '');
  const preHref = (line, table) => '/explorateur/preflop#' + tableHash(table) + 'ligne=' + line.join('.');

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

  // Taille jouée dans la main, ajoutée à l'arbre (ou à la place d'une taille théorique proche) : signalée.
  function sizeTag(a) {
    const info = a.played_size;
    if (!info) return '';
    return el('span', {
      class: 'sz-tag',
      title: info.how === 'added'
        ? 'Taille jouée dans la main (' + info.size + '), ajoutée à l\'arbre à côté de la théorie (' + info.theory + ') pour juger la décision. '
          + 'La part de la range sur chaque taille compare ces options : ce n\'est pas un mélange à reproduire.'
        : 'Taille jouée dans la main (' + info.size + '), à la place de la taille théorique proche (' + info.theory + ') pour suivre le coup exactement.',
    }, info.how === 'added' ? 'jouée' : 'jouée ≈');
  }

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
  // Main que le solveur ne joue presque jamais au nœud : le serveur a mis sa meilleure action selon l'EV.
  const rare = (combo) => !!(node.settled && node.settled.includes(combo));

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
      if (!p.length) { if (prefix) location.href = preHref(prefix.preflop.line, prefix.preflop.table); return; }  // retour au préflop
      p.pop();
      // au début d'un spot, un check forcé (la BB ne mène pas) ramène directement au préflop
      if (SPOT && !p.length && prefix && nodes.has('[]') && forced(nodes.get('[]'))) { location.href = preHref(prefix.preflop.line, prefix.preflop.table); return; }
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
      node = await api('/api/explorateur/preflop', TABLE === 'HU' ? { line } : { table: TABLE, line });
      noCharts = false;
      if (preLine.join('.') !== line.join('.')) { picked = []; flopInfo = null; }
      preLine = line.slice();
      history.replaceState(null, '', '#' + tableHash(TABLE) + 'ligne=' + preLine.join('.'));
      POS = node.positions || ['BB', 'BTN'];  // à plusieurs : les deux joueurs du nœud
      if (node.player !== null && node.player !== undefined) viewPlayer = node.player;
      if (!node.hands[viewPlayer].length) viewPlayer = 0;
      selected = null;
      notice = '';
    } catch (e) {
      notice = e.message;
      noCharts = TABLE !== 'HU' && e.status === 404 && /charts/.test(e.message);
    } finally {
      document.body.classList.remove('busy');
    }
    render();
  }

  // Les charts 6-max gratuits de Hand2Note Guide, téléchargés sur ta machine (comme dans Tables à plusieurs).
  async function loadCharts(button) {
    button.disabled = true;
    button.textContent = 'Téléchargement…';
    try {
      await api('/api/ranges/hand2note', {});
      notice = '';
      await goLine([]);
    } catch (e) {
      notice = e.message;
      button.disabled = false;
      render();
    }
  }

  async function loadState() {
    state = await api('/api/explorateur/etat', { hand: HAND });
    meta = state.meta;
    if (meta && meta.positions) POS = meta.positions;
    live = !!state.live;
    const decisions = state.result ? state.result.decisions : [];
    const last = decisions[decisions.length - 1];
    played = last ? last.path.concat(last.chosen === null ? [] : [{ type: 'action', index: last.chosen }]) : [];
  }

  // Précision visée (exploitabilité en % du pot) et durée estimée de la résolution, selon cet ordinateur.
  const PRECISIONS = [3, 2, 1.5, 1, 0.5];
  let estim = null;  // /api/estimation : durée estimée de ce spot pour chaque précision
  const dur = (sec) => (sec < 60 ? Math.max(5, Math.round(sec / 5) * 5) + ' s'
    : sec < 3600 ? Math.round(sec / 60) + ' min'
      : Math.floor(sec / 3600) + ' h ' + String(Math.round((sec % 3600) / 60)).padStart(2, '0'));
  const estimateOf = (t) => { const o = estim && estim.options.find((x) => x.target === t); return o ? o.seconds : null; };
  const withTime = (t) => (estimateOf(t) !== null ? ' (≈ ' + dur(estimateOf(t)) + ')' : '');

  async function loadEstimate() {
    if (PRE || estim) return;
    try {
      estim = await api('/api/estimation', { hand: HAND });
      renderStatus();
    } catch (e) { /* solveur absent, coup non couvert : pas d'estimation */ }
  }

  function precisionPicker() {
    const sel = el('select', { 'aria-label': 'Précision visée', title: 'Exploitabilité visée, en % du pot : plus elle est basse, '
      + 'plus la solution est proche de l\'équilibre, et plus la résolution est longue. Réglage gardé pour les prochaines résolutions.' });
    PRECISIONS.forEach((t) => sel.append(el('option', { value: t, selected: t === state.precision },
      num(t, 1) + ' % du pot' + (estimateOf(t) !== null ? ' · ≈ ' + dur(estimateOf(t)) : ''))));
    sel.onchange = async () => {
      try {
        state.precision = (await api('/api/precision', { precision: Number(sel.value) })).precision;
      } catch (e) {
        notice = e.message;
      }
      renderStatus();
    };
    return el('label', { class: 'prec' }, 'Précision ', sel);
  }

  // Résultat moins précis que le réglage : on propose de l'affiner (nouvelle résolution).
  function refineButton() {
    if (!state.precision || !state.target || state.target <= state.precision + 1e-9) return '';
    return el('button', { type: 'button', onclick: () => solve(true) }, 'Affiner à ' + num(state.precision, 1) + ' %' + withTime(state.precision));
  }

  async function solve(fresh) {
    try {
      const v = await api('/api/resoudre', { hand: HAND, start: true, force: true, fresh: fresh === true });
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
      box.append('Spot d\'étude · ' + (meta.format && meta.format !== 'HU' ? meta.format + ' · ' : '') + meta.pot_type
        + ' · ' + (meta.pair || 'BTN contre BB') + ' · flop ', cards(meta.board),
        meta.texture ? ' · ' + meta.texture + ' · pot ' + num(meta.pot) + ' bb, tapis ' + num(meta.stack) + ' bb' : '');
      box.append(adjustedBadge());
      return;
    }
    const parts = ['Main ' + meta.hand, meta.date];
    if (state.result) parts.push(state.result.pot_type);
    if (meta.table_format && meta.table_format !== 'HU') parts.push(meta.table_format, 'toi ' + meta.hero_position + ' contre ' + POS[meta.hero_oop ? 1 : 0]);
    else parts.push('toi ' + (meta.hero_position === 'BTN' ? 'au bouton' : 'en BB'));
    const box = $('meta');
    box.textContent = '';
    box.append(parts.join(' · ') + ' · ', cards(meta.hero_cards), ' vs ',
      revealed() && meta.villain_cards.length ? cards(meta.villain_cards) : '??',
      ' · ' + meta.villain + (meta.stack ? ' · tapis effectif ' + num(meta.stack) + ' bb' : '')
      + ' · résultat ' + (meta.net > 0 ? '+' : '') + num(meta.net) + ' bb', adjustedBadge());
    renderSizes();
  }

  // Tailles de l'arbre d'un coup joué : d'où elles viennent, et les tailles jouées ajoutées pour juger la décision.
  function renderSizes() {
    const box = $('sizes');
    box.textContent = '';
    const info = !PRE && !SPOT && meta ? meta.sizes : null;
    box.hidden = !info;
    if (!info) return;
    const head = el('div', { title: 'Tailles de l\'arbre : ' + info.plan });
    if (info.source === 'flop') head.append(el('b', {}, 'Tailles théoriques de ce flop'), ' (' + info.family_title
      + ') : choisies comme pour les spots d\'étude.');
    else if (info.source === 'proche') head.append(el('b', {}, 'Tailles théoriques empruntées à '), cards(info.board),
      ' (' + info.family_title + (info.same_texture ? ', même texture' : ', autre texture') + ') : '
      + 'celles de ce flop ne sont pas encore choisies.');
    else head.append(el('b', {}, 'Tailles par défaut'), ' (33 % au flop, 75 % à la turn et à la river, relance 60 %) : '
      + 'pas encore de tailles théoriques pour ce type de pot.');
    if (info.choose) head.append(el('button', { type: 'button', onclick: chooseSizes, disabled: ['waiting', 'running'].includes(state.state),
      title: 'Le choix se fait dans la série ' + info.choose_title + ' (même jeu que ce coup)' },
      'Choisir les tailles de ce flop (' + info.choose_time + ')'));
    box.append(head);
    const played = info.played.filter((q) => q.how !== 'same');
    played.forEach((q) => box.append(el('div', { class: 'played' },
      (q.who === 'H' ? 'Toi' : 'Lui') + ' · ' + q.label + ' ' + q.size_text + ' : ' + (q.how === 'added'
        ? 'taille jouée, ajoutée à l\'arbre à côté de la théorie (' + q.theory_text + ').'
        : 'taille jouée, à la place de la théorie proche (' + q.theory_text + ').'))));
    if (played.some((q) => q.how === 'added')) box.append(el('div', { class: 'small' },
      'Une taille ajoutée sert à juger la décision (l\'EV de chaque taille) : la part de la range sur chaque taille compare ces options, '
      + 'ce n\'est pas un mélange à reproduire. Repère « jouée » sur l\'action.'));
  }

  async function chooseSizes() {
    const info = meta.sizes;
    if (!window.confirm('Choisir les tailles théoriques de ce flop, comme pour un spot d\'étude : ' + info.choose_time
      + ' de calcul sur 4 cœurs, puis la résolution du coup avec elles. Le flop rejoindra ensuite les spots d\'étude. Lancer ?')) return;
    try {
      const v = await api('/api/explorateur/tailles', { hand: HAND });
      Object.assign(state, v);
      renderStatus();
      renderSizes();
      poll();
    } catch (e) {
      notice = e.message;
      renderStatus();
    }
  }

  // Ranges préflop ajustées en jeu : un rappel, qui ouvre l'onglet Ranges.
  function adjustedBadge() {
    const out = [];
    if (state && state.adjusted) out.push(el('button', {
      type: 'button', class: 'rg-badge', title: 'Le solveur joue avec tes ranges préflop, pas celles de la référence',
      onclick: () => { rightTab = 'ranges'; renderTabs(); },
    }, 'tes ranges' + (state.adjusted === 'ligne' ? ' (ligne)' : '')));
    if (state && state.edits) out.push(el('button', {
      type: 'button', class: 'rg-badge', title: 'Le solveur joue ton arbre : tes tailles de mise ou tes nœuds verrouillés',
      onclick: () => { rightTab = 'tree'; renderTabs(); },
    }, 'ton arbre' + (state.edits.locks ? ' 🔒' + state.edits.locks : '')));
    return out.length ? el('span', {}, out) : '';
  }

  // Heads-up ou coup à plusieurs : l'arbre préflop change, la ligne repart de zéro.
  function tableSwitch() {
    const seg = el('div', { class: 'seg', role: 'group', 'aria-label': 'Format du coup' });
    TABLES.forEach(([id, label]) => seg.append(el('button', {
      type: 'button', 'aria-pressed': TABLE === id ? 'true' : 'false', onclick: () => setTable(id),
    }, label)));
    return seg;
  }

  function setTable(table) {
    if (table === TABLE) return;
    TABLE = table;
    store('explorer-table', table);
    node = null;
    preLine = [];
    picked = [];
    flopInfo = null;
    selected = null;
    ['ribbon', 'grid', 'legend', 'overview', 'combos', 'players'].forEach((id) => { $(id).textContent = ''; });
    document.title = 'Explorateur — préflop' + (TABLE !== 'HU' ? ' ' + TABLE : '');
    goLine([]);
  }

  function renderStatus() {
    const box = $('status');
    box.textContent = '';
    if (PRE) {
      box.append(tableSwitch());
      if (noCharts) {
        box.append(el('button', { type: 'button', class: 'go', onclick: (e) => loadCharts(e.currentTarget) },
          'Charger les charts 100 bb de Hand2Note Guide'),
        el('span', { class: 'muted' }, 'téléchargés sur ta machine, pour ton usage personnel (conditions du site)'));
      }
      if (node) box.append(el('span', {}, node.type === 'flop' ? 'Choisis le flop : les flops résolus s\'ouvrent en quelques secondes.'
        : node.type === 'allin' ? 'Tapis préflop : pas de jeu après le flop à étudier.'
          : TABLE !== 'HU' ? 'Charts ' + TABLE + ' : choisis l\'action de chaque position dans le déroulé ; le premier qui paie '
            + 'ou relance l\'open reste seul face à l\'ouvreur (les charts couvrent les pots à deux) ; au call, tu choisis le flop.'
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
        : state.mode === 'choose' ? (state.edits && state.edits.pending ? 'Ton arbre : le solveur choisit les tailles que tu lui as '
          + 'laissées, puis résout. ' : SPOT ? 'Flop de la série : choix des tailles de mise, puis résolution. '
          : 'Choix des tailles théoriques de ce flop, puis résolution du coup avec elles. ') + (p.stage || '') + elapsed
        : (p.iteration ? 'Résolution : itération ' + p.iteration + ' / ' + state.max_iterations + ' · exploitabilité ' + num(p.exploit_pct) + ' % du pot (objectif ' + num(state.target) + ' %)'
          : p.tree_nodes ? 'Résolution lancée : arbre de ' + p.tree_nodes.toLocaleString('fr-FR') + ' nœuds, première mesure après 10 itérations'
            : 'Construction de l\'arbre…') + elapsed),
      el('div', { class: 'bar' }, el('span', { style: 'width:' + (100 * frac).toFixed(1) + '%' })),
      el('button', { type: 'button', onclick: () => api('/api/resoudre/' + encodeURIComponent(state.job) + '/arreter', {}).then((v) => { Object.assign(state, v); renderStatus(); }) }, 'Arrêter'));
    } else if (s === 'done' && live) {
      const r = state.result;
      box.append(el('span', {}, 'Session active : toutes les branches et toutes les cartes sont explorables (fermée après 30 min sans activité). '
        + r.iterations + ' itérations, exploitabilité ' + num(r.exploit_pct, 2) + ' % du pot.'), precisionPicker(), refineButton());
    } else if (s === 'done' && state.study && SPOT) {
      box.append(el('span', {}, 'Étude enregistrée.'),
        el('button', { type: 'button', class: 'go', onclick: () => solve() }, 'Ouvrir l\'étude (quelques secondes)'));
    } else if (s === 'done' && SPOT) {
      box.append(el('span', {}, 'L\'étude de ce spot a été supprimée : recalcule-le pour l\'explorer.'), precisionPicker(),
        el('button', { type: 'button', class: 'go', onclick: () => solve(true) }, 'Recalculer' + withTime(state.precision)));
    } else if (s === 'done' && state.study) {
      box.append(el('span', {}, 'Étude enregistrée : ligne jouée affichée.'),
        el('button', { type: 'button', class: 'go', onclick: () => solve() }, 'Ouvrir l\'étude complète (quelques secondes)'));
    } else if (s === 'done') {
      box.append(el('span', {}, 'Ligne jouée seulement (résultat enregistré). Pour explorer les autres branches et changer les cartes :'),
        precisionPicker(),
        el('button', { type: 'button', class: 'go', onclick: () => solve(true) }, 'Recalculer' + withTime(state.precision)));
    } else {
      if (state.error) box.append(el('span', { class: 'err' }, state.error));
      box.append(el('span', {}, SPOT ? 'Ce spot n\'est pas encore résolu (il sera gardé dans les études).'
        : 'Ce coup n\'est pas encore résolu.'), precisionPicker(),
        el('button', { type: 'button', class: 'go', onclick: () => solve() }, (SPOT ? 'Résoudre ce spot' : 'Résoudre ce coup')
          + withTime(state.precision)));
      loadEstimate();
    }
    if (s === 'done') loadEstimate();
    if (!['waiting', 'running', 'unsupported', 'unavailable'].includes(s)) {
      box.append(el('button', { type: 'button', 'aria-expanded': String(cb.open), title: 'Les actions et les tailles de chaque '
        + 'situation, street par street, avant la résolution', onclick: () => (cb.open ? closeBuilder() : openBuilder()) },
      cb.open ? 'Fermer ton arbre' : 'Construire l\'arbre'));
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

  // Comme Wizard : le tapis (effectif) du joueur au-dessus de ses actions, le pot sur chaque street.
  const stackOf = (x, title) => el('span', { class: 'stk', title: title || 'Tapis effectif restant avant d\'agir' }, num(x) + ' bb');
  const streetHead = (name, pot) => el('div', { class: 'head' }, el('span', { class: 'st' }, name.toUpperCase()), el('span', {}, 'pot ' + num(pot)));

  function actionStep(h, k, current) {
    const role = roleOf(h.player);
    const step = el('div', { class: 'step' + (current ? ' current' : ''), title: 'pot ' + num(h.pot) + ' bb' },
      el('div', { class: 'head' }, el('span', { class: role }, who(h.player)), stackOf(h.stack)));
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
      }, el('span', {}, labels[j]), sizeTag(a), isPlayed ? el('span', { class: 'dot', title: 'Joué dans la main' }, '●') : ''));
    });
    return step;
  }

  function cardStep(h, k) {
    const btn = el('button', {
      type: 'button', disabled: !live, title: live ? 'Changer de carte' : 'Recalcule pour changer de carte', 'data-k': k,
      onclick: (e) => openPicker(k, e.currentTarget),
    }, h.card ? card(h.card) : '?');
    const step = el('div', { class: 'step cards' }, streetHead(STREET[h.street] || 'Carte', h.pot), btn);
    return navStep(step, path.slice(0, k + 1), false);
  }

  // Étape préflop : chaque action mène à son nœud (navigate reçoit la ligne jusqu'à cette action).
  function preStep(h, k, line, current, navigate) {
    const step = el('div', { class: 'step pre' + (current ? ' current' : ''), title: 'Préflop · pot ' + num(h.pot) + ' bb' },
      el('div', { class: 'head' }, el('span', {}, h.position || POS[h.player]), stackOf(h.stack)));
    h.actions.forEach((a, j) => step.append(el('button', {
      type: 'button', class: 'act' + (h.chosen === j ? ' on' : ''), onclick: () => navigate(line.slice(0, k).concat([h.keys[j]])),
    }, el('span', {}, a.name))));
    return step;
  }

  // Action préflop d'une main jouée (tailles réelles) : ouvre la solution préflop à ce moment du coup.
  function playedPreStep(s) {
    // à une table à plusieurs, les joueurs qui ne voient pas le flop n'ont que leur position (player null)
    const inPot = s.player !== null && s.player !== undefined;
    const step = el('div', { class: 'step pre' + (inPot ? '' : ' out'), title: 'Préflop · pot ' + num(s.pot) + ' bb' },
      el('div', { class: 'head' }, el('span', { class: inPot ? roleOf(s.player) : '' }, inPot ? who(s.player) : s.position),
        stackOf(s.stack, meta.table_format && meta.table_format !== 'HU' ? 'Son tapis restant avant d\'agir' : null)));
    step.append(el('button', {
      type: 'button', class: 'act on', disabled: !s.line, title: s.line ? 'Voir la solution préflop à ce moment du coup' : null,
      onclick: () => window.open(preHref(s.line), '_blank', 'noopener'),
    }, el('span', {}, s.name), el('span', { class: 'dot', title: 'Joué dans la main' }, '●')));
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
          streetHead('Flop', h.pot), el('span', { class: 'muted small' }, 'à choisir')));
        else box.append(el('div', { class: 'step end current' }, h.kind === 'terminal_fold' ? 'Fin : fold' : 'Tapis préflop'));
      });
      box.scrollLeft = box.scrollWidth;
      return;
    }
    if (prefix) {
      prefix.history.forEach((h, k) => {
        if (h.kind === 'action') box.append(preStep(h, k, prefix.preflop.line, false, (line) => { location.href = preHref(line, prefix.preflop.table); }));
      });
    }
    (meta.preflop || []).forEach((s) => box.append(playedPreStep(s)));
    box.append(navStep(el('div', { class: 'step cards' },
      streetHead('Flop', state.result.pot), cards(meta.board.slice(0, 3)),
      prefix ? el('a', { class: 'change', href: preHref(prefix.preflop.line, prefix.preflop.table), title: 'Choisir un autre flop' }, 'changer') : ''),
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
    [0, 1].forEach((p) => {
      if (PRE && !node.hands[p].length && node.player !== p) return;  // à plusieurs : personne n'a encore ouvert
      players.append(el('button', {
        type: 'button', 'aria-pressed': viewPlayer === p ? 'true' : 'false',
        onclick: () => { viewPlayer = p; viewAuto = actor && p === node.player; selected = null; render(); },
      }, who(p) + (actor && node.player === p ? ' (agit)' : '')));
    });
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
    const lockedSet = strat && node.lock ? new Set(node.lock.edited) : null;
    let anyRare = false;
    const villainCls = revealed() && viewPlayer === playerOf('V') && meta.villain_cards.length === 2 ? classOf(meta.villain_cards.join('')) : null;
    for (let i = 0; i < 13; i++) for (let j = 0; j < 13; j++) {
      const hand = handAt(i, j);
      const a = agg[hand];
      const settledCell = strat && a && a.rows.every((r) => rare(r[0]));
      if (settledCell) anyRare = true;
      const lockedCell = lockedSet && a && a.rows.some((r) => lockedSet.has(r[0]));
      const cls = 'cell' + (a ? '' : ' out') + (settledCell ? ' rare' : '') + (hand === heroCls ? ' me' : '') + (hand === villainCls ? ' him' : '') + (hand === selected ? ' sel' : '') + (lockedCell ? ' lk' : '');
      const cell = el('div', { class: cls, 'data-hand': hand, title: settledCell ? 'Presque jamais jouée ici : meilleure action selon l\'EV' : null },
        el('span', { class: 'h' }, hand));
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
        cell.addEventListener('click', () => {
          selected = selected === hand ? null : hand;
          renderGridSelection();
          renderCombos();
          if (rightTab === 'tree') { if (selected) tr.target = 'cell'; renderTree(); }
        });
      }
      grid.append(cell);
    }
    grid.onmouseleave = () => { hovered = null; renderCombos(); };
    const legend = $('legend');
    legend.textContent = '';
    if (strat && (mode === 'strategy' || mode === 'strategy_ev')) {
      nodeLabels().forEach((label, k) => legend.append(el('span', {}, el('i', { style: 'background:' + cols[k] }), label)));
    }
    if (anyRare && mode !== 'equity') legend.append(el('span', {}, 'Nom en italique : main que le solveur ne joue presque jamais '
      + 'ici ; on montre sa meilleure action selon l\'EV.'));
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
        el('div', { class: 'l' }, nodeLabels()[k], sizeTag(a)), el('div', { class: 'p' }, total ? pct(freqs[k] / total) : '—'),
        el('div', { class: 'c' }, num(freqs[k], 1) + ' combos'))));
      panel.append(tiles);
      const stack = el('div', { class: 'stack' });
      freqs.forEach((f, k) => stack.append(el('span', { style: 'width:' + (total ? 100 * f / total : 0).toFixed(2) + '%;background:' + cols[k] })));
      panel.append(stack);
      if (node.locked) panel.append(el('div', { class: 'tr-locked' }, '🔒 Nœud verrouillé' + (node.lock
        ? ' : ' + node.lock.edits.map((e) => e.label + ' ' + mixText(e.freqs, nodeLabels())).join(' ; ') + ' ; les autres mains gardent la stratégie d\'origine.'
        : ' (stratégie imposée).')));
      if (full) panel.append(el('div', { class: 'muted small', style: 'margin-top:6px' },
        'Filtre actif : ' + pct(total / full) + ' de la range (' + num(total, 1) + ' combos) ; fréquences de ces mains seulement.'));
      if (node.rare && node.rare.length) panel.append(el('div', { class: 'warn' }, 'Ligne que le solveur ne prend presque jamais : '
        + node.rare.map((q) => who(q) + ' y arrive avec ' + (node.presence[q] < 0.001 ? 'moins de 0,1' : num(100 * node.presence[q], 1)) + ' % de sa range').join(', ')
        + '. Les stratégies qui suivent n\'y sont pas optimisées : lis les EV plutôt que les fréquences, et avec prudence.'));
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
    box.append(el('div', { class: 'rg-row' }, el('a', { class: 'fl-go', href: spotHref(f.id) + '#resoudre', title: 'Tailles : ' + f.sizes },
      'Résoudre ce flop (' + f.cost + ')'), el('a', { class: 'fl-tree', href: spotHref(f.id) + '#arbre',
      title: 'Choisir les actions et les tailles de chaque street avant la résolution' }, 'Construire l\'arbre d\'abord')));
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
    panel.append(el('div', { class: 'fl-head' }, el('b', {}, info.series === false ? 'Flops types et flops déjà résolus'
      : 'Flops de la série et flops déjà résolus'),
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
    const settled = strat && rare(row[0]);
    if (strat && live && !PRE && node.type === 'action' && na > 1) {
      head.append(el('button', { type: 'button', class: 'lock-btn', title: 'Verrouiller ce combo (onglet Arbre)',
        'aria-label': 'Verrouiller ' + row[0], onclick: () => { tr.combo = row[0]; tr.target = 'combo'; tr.mix = null; rightTab = 'tree'; renderTabs(); } }, '🔒'));
    }
    const box = el('div', { class: 'combo' + (settled ? ' settled' : '') + (node.lock && node.lock.edited.includes(row[0]) && strat ? ' locked' : '') }, head);
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
      if (settled) box.append(el('div', { class: 'note' }, 'Le solveur ne joue presque jamais cette main ici : sa fréquence '
        + 'n\'y est pas apprise. On montre sa meilleure action selon l\'EV.'));
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

  // ---------- ranges préflop ajustées (onglet Ranges) ----------
  // Tes ranges à la place de celles de la référence, pour ce coup ou par défaut pour toute sa ligne : le coup
  // se résout à nouveau, dans une étude à part (celle de la référence reste et se rouvre en quelques secondes).
  const BRUSHES = [[1, '100 %'], [0.75, '75 %'], [0.5, '50 %'], [0.25, '25 %'], [0, 'Retirer']];
  const HAND_RE = /^(?:([2-9TJQKA])\1|[2-9TJQKA]{2}[so])$/;
  const RESIZE = 0.10;  // « plus serré » / « plus large » : environ 10 % des combos de la range
  const ALL_HANDS = [];
  for (let i = 0; i < 13; i++) for (let j = 0; j < 13; j++) ALL_HANDS.push(handAt(i, j));
  const rg = { data: null, edit: null, player: 0, brush: 1, scope: 'coup', painting: false, busy: false, msg: '', ui: null };

  const comboCount = (h) => (h.length === 2 ? 6 : h[2] === 's' ? 4 : 12);
  const combosOf = (w) => Object.entries(w).reduce((t, [h, x]) => t + x * comboCount(h), 0);
  const validHand = (h) => HAND_RE.test(h) && (h.length === 2 || RANKS.indexOf(h[0]) < RANKS.indexOf(h[1]));
  const sameRange = (a, b) => [...new Set(Object.keys(a).concat(Object.keys(b)))].every((h) => Math.abs((a[h] || 0) - (b[h] || 0)) <= 0.002);
  const rangeText = (w) => ALL_HANDS.filter((h) => (w[h] || 0) >= 0.001)
    .map((h) => (w[h] >= 0.999 ? h : h + ':' + Math.round(w[h] * 1000) / 1000)).join(',');

  function parseRange(text) {
    const out = {};
    for (const item of text.replace(/\s+/g, '').split(',')) {
      if (!item) continue;
      const [h, weight] = item.split(':');
      const v = weight === undefined || weight === '' ? 1 : Number(weight);
      if (!validHand(h) || !Number.isFinite(v) || v < 0) throw new Error('Illisible : « ' + item + ' ». Écris AA, AKs, AKo…, avec un poids éventuel (AKo:0.5).');
      if (v > 0) out[h] = Math.min(1, v);
    }
    if (!Object.keys(out).length) throw new Error('Range vide : garde au moins une main.');
    return out;
  }

  // Plus serré : retire environ 10 % des combos, en partant des mains les plus faibles de la range (équité contre
  // une main au hasard) ; plus large : en ajoute autant, en partant des plus fortes qui n'y sont pas en entier.
  function resize(w, sign) {
    const out = { ...w };
    let left = RESIZE * combosOf(w);
    if (sign > 0) left = Math.max(left, 0.01 * 1326);
    for (const h of sign < 0 ? rg.data.order.slice().reverse() : rg.data.order) {
      if (left <= 0.01) break;
      const x = out[h] || 0;
      const room = sign < 0 ? x : 1 - x;
      if (room <= 0) continue;
      const d = Math.min(room, left / comboCount(h));
      const v = Math.round((x + sign * d) * 1000) / 1000;
      if (v >= 0.001) out[h] = v; else delete out[h];
      left -= d * comboCount(h);
    }
    return Object.keys(out).length ? out : w;
  }

  function setRanges(data) {
    rg.data = data;
    rg.edit = data.supported ? data.players.map((p) => ({ ...p.current })) : null;
    const line = data.supported && data.players.some((p) => p.source === 'ligne') && !data.players.some((p) => p.source === 'coup');
    rg.scope = data.can_line && line ? 'ligne' : 'coup';
  }

  async function mountRanges() {
    if (!rg.data) {
      $('ranges-box').textContent = 'Chargement…';
      try {
        setRanges(await api('/api/explorateur/ranges', { hand: HAND }));
      } catch (e) {
        $('ranges-box').textContent = e.message;
        return;
      }
    }
    renderRanges();
  }

  const cellTitle = (h, x, r) => h + ' · ' + pct(x) + (Math.abs(x - r) > 0.002 ? ' (référence ' + pct(r) + ')' : '');
  function paintCell(cell) {
    const h = cell.dataset.h;
    const w = rg.edit[rg.player];
    if ((w[h] || 0) === rg.brush) return;
    if (rg.brush > 0) w[h] = rg.brush; else delete w[h];
    refreshRanges();
  }

  function rangeGrid() {
    const grid = el('div', { class: 'rgrid', 'aria-label': 'Range : clique ou glisse sur les mains pour les peindre' });
    rg.ui.cells = ALL_HANDS.map((h) => grid.appendChild(el('div', { class: 'rcell', 'data-h': h }, el('span', { class: 'h' }, h))));
    // souris et doigt : on peint les cases survolées tant que le bouton (ou le doigt) reste appuyé
    grid.addEventListener('pointerdown', (e) => {
      const cell = e.target.closest('.rcell');
      if (!cell || rg.busy) return;
      rg.painting = true;
      paintCell(cell);
      e.preventDefault();
    });
    grid.addEventListener('pointermove', (e) => {
      if (!rg.painting) return;
      const target = document.elementFromPoint(e.clientX, e.clientY);
      const cell = target && target.closest('.rcell');
      if (cell && grid.contains(cell)) paintCell(cell);
    });
    return grid;
  }

  const playerLabel = (q) => q.position + (q.role ? ' · ' + NAME[q.role] : '');

  // Le panneau ; ce qui dépend des ranges en cours d'édition se met à jour à part (refreshRanges), sans
  // reconstruire les boutons : un clic juste après avoir tapé une range ne se perd pas.
  function renderRanges() {
    const box = $('ranges-box');
    box.textContent = '';
    rg.ui = null;
    const d = rg.data;
    if (!d) return;
    if (!d.supported) { box.append(el('p', { class: 'empty' }, d.message)); return; }
    const p = d.players[rg.player];
    const ui = rg.ui = {};
    const wrap = el('div', { class: 'rg' });
    box.append(wrap);
    const what = SPOT ? 'ce spot' : 'ce coup';
    wrap.append(el('p', { class: 'small muted' }, 'Ligne préflop : ' + d.line + (d.format !== 'HU' ? ' en ' + d.format : '')
      + ' · référence : ' + d.reference + '. Ajuste les ranges, puis résous ' + what + ' avec elles.'));

    const seg = el('div', { class: 'seg', role: 'group', 'aria-label': 'Range modifiée' });
    ui.players = d.players.map((q, k) => seg.appendChild(el('button', {
      type: 'button', 'aria-pressed': String(k === rg.player), onclick: () => { rg.player = k; renderRanges(); },
    }, playerLabel(q))));
    const source = { coup: 'ta range pour ' + what, ligne: 'ta range par défaut de la ligne', reference: 'range de la référence' }[p.source];
    wrap.append(el('div', { class: 'rg-row' }, seg, el('span', { class: 'small muted' }, 'En jeu : ' + source)));
    ui.count = wrap.appendChild(el('div', { class: 'small' }));

    const brushes = el('div', { class: 'seg', role: 'group', 'aria-label': 'Pinceau' });
    const brushButtons = BRUSHES.map(([v, label]) => brushes.appendChild(el('button', {
      type: 'button', 'aria-pressed': String(rg.brush === v), title: v ? 'Peindre les mains à ' + label : 'Retirer les mains de la range',
      onclick: () => { rg.brush = v; brushButtons.forEach((b, k) => b.setAttribute('aria-pressed', String(BRUSHES[k][0] === v))); },
    }, label)));
    const change = (next) => { rg.edit[rg.player] = next; rg.msg = ''; refreshRanges(); };
    const current = () => rg.edit[rg.player];
    wrap.append(el('div', { class: 'rg-row' }, brushes,
      el('button', { type: 'button', class: 'rg-btn', disabled: rg.busy, title: 'Retire environ 10 % des combos, en commençant par les mains les plus faibles', onclick: () => change(resize(current(), -1)) }, 'Plus serré'),
      el('button', { type: 'button', class: 'rg-btn', disabled: rg.busy, title: 'Ajoute environ 10 % de combos, en commençant par les mains les plus fortes', onclick: () => change(resize(current(), 1)) }, 'Plus large')));

    wrap.append(rangeGrid());
    wrap.append(el('div', { class: 'small muted' }, 'Clique ou glisse sur les mains avec le pinceau choisi. Contour orange : différent de la référence.'));

    ui.text = el('textarea', { class: 'rg-text', rows: 4, spellcheck: 'false', 'aria-label': 'Range de ' + p.position + ' en texte' });
    ui.text.addEventListener('input', () => {  // la grille suit la saisie dès qu'elle se lit
      try { rg.edit[rg.player] = parseRange(ui.text.value); rg.msg = ''; refreshRanges(); } catch (e) { /* avis au change */ }
    });
    ui.text.addEventListener('change', () => {
      const typed = ui.text.value;
      try { change(parseRange(typed)); } catch (e) { rg.msg = e.message; refreshRanges(); ui.text.value = typed; }  // à corriger
    });
    wrap.append(ui.text);

    ui.toRef = el('button', { type: 'button', class: 'rg-btn', onclick: () => change({ ...p.reference }) }, 'Range de la référence');
    const resets = el('div', { class: 'rg-row' }, ui.toRef);
    if (p.line && p.source === 'coup') resets.append(el('button', { type: 'button', class: 'rg-btn', onclick: () => change({ ...p.line }) }, 'Ta range de la ligne'));
    ui.undo = resets.appendChild(el('button', { type: 'button', class: 'rg-btn', onclick: () => change({ ...p.current }) }, 'Annuler mes changements'));
    wrap.append(resets);

    if (d.can_line) {
      const scope = el('div', { class: 'rg-scope', role: 'radiogroup', 'aria-label': 'Portée' });
      [['coup', 'Pour ce coup seulement'],
        ['ligne', 'Par défaut pour « ' + d.line + ' » en ' + d.format + ' : tes autres coups de cette ligne, leur analyse et le leakfinding']]
        .forEach(([v, label]) => {
          const input = el('input', { type: 'radio', name: 'rg-scope', value: v, checked: rg.scope === v, onchange: () => { rg.scope = v; refreshRanges(); } });
          scope.append(el('label', {}, input, el('span', {}, label)));
        });
      wrap.append(scope);
    }

    ui.solve = el('button', { type: 'button', class: 'rg-btn go', onclick: saveRanges }, 'Résoudre avec ces ranges');
    const actions = el('div', { class: 'rg-row' }, ui.solve);
    if (d.adjusted) actions.append(el('button', { type: 'button', class: 'rg-btn', disabled: rg.busy, onclick: resetRanges }, 'Revenir à la référence'));
    wrap.append(actions);
    ui.msg = wrap.appendChild(el('p', { class: 'err', role: 'alert' }));
    wrap.append(el('p', { class: 'small muted' }, 'Une autre range change la solution : ' + what + ' se résout à nouveau, dans une étude à part. '
      + 'Celle de la référence reste : « Revenir à la référence » la rouvre en quelques secondes.'));
    refreshRanges();
  }

  function refreshRanges() {
    const ui = rg.ui, d = rg.data;
    if (!ui) return;
    const p = d.players[rg.player];
    const w = rg.edit[rg.player];
    ui.cells.forEach((cell) => {
      const h = cell.dataset.h, x = w[h] || 0, r = p.reference[h] || 0;
      cell.style.setProperty('--w', Math.round(100 * x) + '%');
      cell.classList.toggle('diff', Math.abs(x - r) > 0.002);
      cell.title = cellTitle(h, x, r);
    });
    const n = combosOf(w), ref = combosOf(p.reference);
    ui.count.textContent = num(n, 0) + ' combos (' + num(100 * n / 1326, 1) + ' % des mains) · référence '
      + num(ref, 0) + ' (' + num(100 * ref / 1326, 1) + ' %)';
    ui.players.forEach((b, k) => { b.textContent = playerLabel(d.players[k]) + (sameRange(rg.edit[k], d.players[k].current) ? '' : ' •'); });
    if (document.activeElement !== ui.text) ui.text.value = rangeText(w);
    ui.toRef.disabled = rg.busy || sameRange(w, p.reference);
    ui.undo.disabled = rg.busy || sameRange(w, p.current);
    const dirty = rg.edit.some((x, k) => !sameRange(x, d.players[k].current))
      || (rg.scope === 'ligne' && d.players.some((q) => q.source === 'coup'));
    ui.solve.disabled = !dirty || rg.busy;
    ui.msg.textContent = rg.msg;
    ui.msg.hidden = !rg.msg;
  }

  async function saveRanges() {
    if (rg.ui && rg.ui.text.value !== rangeText(rg.edit[rg.player])) {  // texte tapé sans quitter la zone
      try { rg.edit[rg.player] = parseRange(rg.ui.text.value); } catch (e) { rg.msg = e.message; refreshRanges(); return; }
    }
    const ranges = {};
    rg.data.players.forEach((p, k) => { ranges[p.position] = rangeText(rg.edit[k]); });
    if (Object.values(ranges).some((t) => !t)) { rg.msg = 'Range vide : garde au moins une main.'; refreshRanges(); return; }
    await rangesCall('/api/explorateur/ranges/enregistrer', { hand: HAND, scope: rg.scope, ranges }, true);
  }

  async function resetRanges() {
    const d = rg.data;
    const scope = d.players.some((p) => p.source === 'coup') ? 'coup' : 'ligne';
    if (scope === 'ligne' && !window.confirm('Effacer ta range par défaut pour « ' + d.line + ' » en ' + d.format
      + ' ? Elle ne s\'appliquera plus à aucun coup de cette ligne.')) return;
    await rangesCall('/api/explorateur/ranges/effacer', { hand: HAND, scope }, false);
  }

  async function rangesCall(url, body, solveNow) {
    rg.busy = true;
    rg.msg = '';
    renderRanges();
    try {
      setRanges(await api(url, body));
      await reopen(solveNow);
    } catch (e) {
      rg.msg = e.message;
    }
    rg.busy = false;
    renderRanges();
  }

  // Les ranges ou l'arbre ont changé : un autre coup pour le solveur (ou celui d'origine, souvent déjà résolu).
  // keepPath : on revient ensuite au nœud affiché (s'il existe encore dans le nouvel arbre).
  async function reopen(solveNow, keepPath) {
    resume = keepPath && path.length ? path.slice() : null;
    clearTimeout(pollTimer);
    nodes.clear();
    node = null;
    path = [];
    selected = null;
    hovered = null;
    for (const id of ['ribbon', 'overview', 'mine', 'combos', 'grid', 'legend']) $(id).textContent = '';
    await loadState();
    await showSpot(solveNow);
  }

  // ---------- arbre : tes tailles de mise et tes nœuds verrouillés ----------
  // Les tailles changées attendent ici « Résoudre avec cet arbre » ; un verrou se compose main par main (case, combo
  // ou mains filtrées), puis « Verrouiller et résoudre ». Chaque version de l'arbre se résout dans une étude à part.
  const tr = { data: null, key: null, pending: {}, focus: null, edits: [], editsKey: null, combo: null, target: null,
    mix: null, mixFor: null, busy: false, msg: '' };
  let resume = null;  // le nœud à rouvrir quand la résolution de l'arbre modifié est prête
  const STREET_WORDS = ['Flop', 'Turn', 'River'];

  function sizeText(x) {
    if (x === 'a') return 'tapis';
    if (x === 'geo') return 'géo';
    if (typeof x === 'string' && x.startsWith('geo')) return 'géo ' + x.slice(3) + ' streets';
    if (typeof x === 'string' && x.startsWith('x')) return 'x' + num(Number(x.slice(1)), 2);
    return x === 100 ? 'pot' : num(x, 1) + ' %';
  }
  const sizesText = (list) => (list.length ? list.map(sizeText).join(' · ') : 'aucune');
  const comboLabel = (c) => c[0] + SUITS[c[1]] + c[2] + SUITS[c[3]];
  const mixText = (freqs, labels) => freqs.map((f, k) => (f > 0.0005 ? Math.round(100 * f) + ' % ' + String(labels[k] || '').toLowerCase() : null))
    .filter(Boolean).join(', ');

  async function mountTree(force) {
    const key = JSON.stringify(path);
    if (tr.editsKey !== key) { tr.edits = []; tr.mix = null; tr.editsKey = key; }
    if (!force && tr.data && tr.key === key) { renderTree(); return; }
    tr.key = key;
    if (!tr.data) $('tree-box').textContent = 'Chargement…';
    try {
      tr.data = await api('/api/explorateur/arbre', { hand: HAND, path });
      tr.msg = '';
    } catch (e) {
      tr.msg = e.message;
    }
    if (tr.focus && tr.data && !tr.data.situations.some((x) => x.key === tr.focus)) tr.focus = null;
    renderTree();
  }

  function renderTree() {
    const box = $('tree-box');
    box.textContent = '';
    const d = tr.data;
    if (!d) { box.append(el('p', { class: 'empty' }, tr.msg || 'Chargement…')); return; }
    const wrap = el('div', { class: 'rg tr' });
    box.append(wrap);
    const changed = d.situations.filter((x) => x.changed).length;
    const what = [changed ? changed + ' situation(s) modifiée(s)' : '', d.locks.length ? d.locks.length + ' nœud(s) verrouillé(s)' : '']
      .filter(Boolean).join(' · ');
    wrap.append(el('div', { class: 'rg-row' }, el('b', {}, d.edited ? 'Ton arbre' : 'Arbre d\'origine'),
      el('span', { class: 'small muted' }, d.edited ? what : 'les tailles de la série, ou celles par défaut'),
      d.edited ? el('button', { type: 'button', class: 'rg-btn', disabled: tr.busy, onclick: resetTree }, 'Revenir à l\'arbre d\'origine') : ''));
    wrap.append(sizesPanel(d), lockPanel());
    if (d.locks.length) wrap.append(locksList(d));
    if (tr.msg) wrap.append(el('p', { class: 'err', role: 'alert' }, tr.msg));
  }

  // --- tailles ---
  const situationOf = (d, key) => d.situations.find((x) => x.key === key) || (d.here && d.here.key === key ? d.here : null);
  const sizesOf = (s) => (s.key in tr.pending ? tr.pending[s.key] : s.sizes);
  const sameSizes = (a, b) => JSON.stringify(a.map(String).sort()) === JSON.stringify(b.map(String).sort());

  function setPending(s, list) {
    if (sameSizes(list, s.sizes)) delete tr.pending[s.key]; else tr.pending[s.key] = list;
    renderTree();
  }

  function sizeEditor(s, here) {
    const box = el('div', { class: 'tr-sizes' });
    const list = sizesOf(s);
    box.append(el('div', { class: 'rg-row' }, el('b', {}, s.title), here ? el('span', { class: 'small muted' }, 'situation de ce nœud') : ''));
    const chips = el('div', { class: 'tr-chips' });
    list.forEach((x, k) => chips.append(el('span', { class: 'tr-chip' }, sizeText(x), el('button', {
      type: 'button', title: 'Retirer cette taille', 'aria-label': 'Retirer ' + sizeText(x),
      onclick: () => setPending(s, list.filter((_, j) => j !== k)),
    }, '×'))));
    if (!list.length) chips.append(el('span', { class: 'small muted' }, s.raise ? 'Pas de relance : fold ou call seulement.' : 'Pas de mise : check seulement.'));
    box.append(chips);
    const push = (size) => { if (!list.some((x) => String(x) === String(size))) setPending(s, list.concat([size])); };
    const input = el('input', { type: 'number', min: '1', max: '1000', step: 'any', class: 'tr-in', placeholder: s.raise ? '60' : '50',
      'aria-label': 'Nouvelle taille' });
    const unit = s.raise ? el('select', { 'aria-label': 'Unité de la relance' }, el('option', { value: 'pct' }, '% du pot'), el('option', { value: 'x' }, '× la mise'))
      : el('span', { class: 'small' }, '% du pot');
    const add = () => {
      const v = Number(String(input.value).replace(',', '.'));
      if (!(v > 0)) { input.focus(); return; }
      push(s.raise && unit.value === 'x' ? 'x' + v : v);
    };
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') add(); });
    box.append(el('div', { class: 'rg-row' }, input, unit, el('button', { type: 'button', class: 'rg-btn', onclick: add }, 'Ajouter'),
      el('button', { type: 'button', class: 'rg-btn', title: 'La même fraction du pot à chaque street, pour finir à tapis à la river', onclick: () => push('geo') }, '+ géo'),
      el('button', { type: 'button', class: 'rg-btn', onclick: () => push('a') }, '+ tapis')));
    if (s.raise) box.append(el('div', { class: 'small muted' }, 'Relance : en % du pot après le call (comme les libellés des actions), ou en multiple de la mise (x3).'));
    if (s.changed || s.key in tr.pending) {
      box.append(el('div', { class: 'small muted' }, 'D\'origine : ' + sizesText(s.base) + ' ',
        el('button', { type: 'button', class: 'link', onclick: () => setPending(s, s.base.slice()) }, 'remettre')));
    }
    return box;
  }

  function sizesPanel(d) {
    const box = el('div', { class: 'tr-box' }, el('h3', {}, 'Tailles de mise'));
    const key = tr.focus || (d.here && d.here.key);
    const s = key ? situationOf(d, key) : null;
    if (s) box.append(sizeEditor(s, !!(d.here && d.here.key === key)));
    else box.append(el('p', { class: 'small muted' }, node && node.type === 'action'
      ? 'Ici, personne ne peut miser ni relancer : choisis une situation dans la liste.' : 'Choisis une situation dans la liste.'));
    if (tr.focus && d.here && tr.focus !== d.here.key) box.append(el('button', { type: 'button', class: 'link', onclick: () => { tr.focus = null; renderTree(); } }, 'Revenir à la situation de ce nœud'));
    const all = el('details', { class: 'tr-all' }, el('summary', {}, 'Toutes les situations de l\'arbre (' + d.situations.length + ')'));
    [0, 1, 2].forEach((st) => {
      const rows = d.situations.filter((x) => x.street === st);
      if (!rows.length) return;
      all.append(el('h4', {}, STREET_WORDS[st]));
      rows.forEach((x) => all.append(el('button', {
        type: 'button', class: 'tr-sit' + (x.key === key ? ' on' : '') + (x.changed || x.key in tr.pending ? ' mod' : ''),
        onclick: () => { tr.focus = x.key; renderTree(); },
      }, el('span', {}, x.title), el('span', { class: 'muted' }, sizesText(sizesOf(x))))));
    });
    if (!d.situations.length) all.append(el('p', { class: 'small muted' }, 'Arbre par défaut : chaque street a ses tailles. Une situation que tu modifies s\'ajoute ici.'));
    box.append(all);
    const n = Object.keys(tr.pending).length;
    box.append(el('div', { class: 'rg-row' },
      el('button', { type: 'button', class: 'rg-btn go', disabled: !n || tr.busy, onclick: saveSizes }, 'Résoudre avec cet arbre' + (n ? ' (' + n + ')' : '')),
      n ? el('button', { type: 'button', class: 'rg-btn', onclick: () => { tr.pending = {}; renderTree(); } }, 'Annuler') : ''));
    box.append(el('p', { class: 'small muted' }, 'Une situation (c-bet, 2e barrel, check-raise…) garde les mêmes tailles sur toutes les cartes. '
      + 'L\'arbre modifié se résout dans une étude à part ; celle d\'origine reste.'));
    return box;
  }

  async function treeCall(url, body, solveNow, after) {
    tr.busy = true;
    tr.msg = '';
    renderTree();
    try {
      tr.data = await api(url, body);
      if (after) after();
      tr.key = null;
      await reopen(solveNow, true);
    } catch (e) {
      tr.msg = e.message;
    }
    tr.busy = false;
    if (rightTab === 'tree') mountTree(true); else renderTree();
  }

  async function saveSizes() {
    const n = tr.data.locks.length;
    if (n && !window.confirm('Changer les tailles retire tes ' + n + ' verrou(s) : leurs chemins ne mènent plus aux mêmes nœuds. Continuer ?')) return;
    await treeCall('/api/explorateur/arbre/tailles', { hand: HAND, plan: tr.pending }, true, () => { tr.pending = {}; });
  }

  async function resetTree() {
    if (!window.confirm('Revenir à l\'arbre d\'origine ? Tes tailles et tes verrous de ce coup sont effacés '
      + '(les résolutions déjà faites restent dans Études du solveur).')) return;
    await treeCall('/api/explorateur/arbre/origine', { hand: HAND }, false, () => { tr.pending = {}; tr.edits = []; tr.focus = null; });
  }

  // ---------- ton arbre, avant la résolution : street par street ----------
  // Chaque situation (c-bet, 2e barrel, check-raise…) garde ses tailles d'origine, prend les tiennes, n'a plus de mise
  // (sa branche disparaît : le solveur ne l'explore pas), ou est laissée au solveur, qui choisit lui-même sa taille
  // parmi celles que tu lui donnes, avant la résolution. Une street entière se règle d'un coup.
  const cb = { data: null, draft: {}, open: false, busy: false, msg: '', street: 0, raises: [true, false, false], quick: {} };
  const CB_MODES = [['default', 'Origine'], ['fixed', 'Fixées'], ['none', 'Aucune'], ['auto', 'Le solveur choisit']];

  async function openBuilder() {
    cb.open = true;
    cb.msg = '';
    renderBuilder();
    renderStatus();
    try {
      cb.data = await api('/api/explorateur/construction', { hand: HAND });
      cb.draft = draftOf(cb.data);
    } catch (e) {
      cb.msg = e.message;
    }
    renderBuilder();
  }

  function closeBuilder() {
    cb.open = false;
    renderBuilder();
    renderStatus();
  }

  function draftOf(d) {
    const out = {};
    (d.rows || []).forEach((r) => {
      if (r.mode !== 'default') out[r.key] = { mode: r.mode, sizes: (r.mode === 'auto' ? r.candidates : r.sizes).slice() };
    });
    return out;
  }

  const choiceOf = (r) => cb.draft[r.key] || { mode: 'default', sizes: r.base.slice() };

  function setChoice(r, mode, sizes) {
    const c = choiceOf(r);
    if (mode === 'default') delete cb.draft[r.key];
    else if (mode === 'none') cb.draft[r.key] = { mode, sizes: [] };
    else cb.draft[r.key] = { mode, sizes: (sizes || (mode === 'auto' ? r.candidates : c.sizes.length ? c.sizes : r.base)).slice() };
  }

  // Une situation peut-elle encore arriver avec ces choix ? (comme sizing.reachable)
  function reachableNow() {
    const rows = cb.data.rows;
    const keys = new Set(rows.map((r) => r.key));
    const eff = {};
    rows.forEach((r) => { const c = choiceOf(r); eff[r.key] = c.mode === 'default' ? r.base : c.sizes; });
    const can = (k) => !keys.has(k) || eff[k].length > 0;
    const memo = {};
    const reach = (k) => {
      if (k in memo) return memo[k];
      memo[k] = true;
      const [kind, where, past, level] = k.split(':');
      let ok = true;
      for (let i = 0; ok && i < past.length; i += 1) {
        if (past[i] === 'x') continue;
        const before = past.slice(0, i);
        const opts = rows.filter((r) => r.key.split(':')[1] === 'ftr'[i] + past[i] && r.key.split(':')[2] === before);
        if (opts.length && !opts.some((r) => can(r.key) && reach(r.key))) ok = false;
      }
      if (ok && kind === 'raise') {
        const other = where[1] === 'o' ? 'i' : 'o';
        const n = Number(level);
        const parent = n === 0 ? 'bet:' + where[0] + other + ':' + past : 'raise:' + where[0] + other + ':' + past + ':' + (n - 1);
        ok = can(parent) && (!keys.has(parent) || reach(parent));
      }
      memo[k] = ok;
      return ok;
    };
    const out = {};
    rows.forEach((r) => { out[r.key] = reach(r.key); });
    return out;
  }

  function chipsEditor(r, list, onChange, raise) {
    const box = el('div', { class: 'cb-sizes' });
    const chips = el('div', { class: 'tr-chips' });
    list.forEach((x, k) => chips.append(el('span', { class: 'tr-chip' }, sizeText(x), el('button', {
      type: 'button', title: 'Retirer cette taille', 'aria-label': 'Retirer ' + sizeText(x),
      onclick: () => onChange(list.filter((_, j) => j !== k)),
    }, '×'))));
    if (!list.length) chips.append(el('span', { class: 'small muted' }, 'Ajoute une taille.'));
    const push = (size) => { if (!list.some((x) => String(x) === String(size))) onChange(list.concat([size])); };
    const input = el('input', { type: 'number', min: '1', max: '1000', step: 'any', class: 'tr-in', placeholder: raise ? '60' : '50',
      'aria-label': 'Nouvelle taille' + (r ? ' : ' + r.title : '') });
    const unit = raise ? el('select', { 'aria-label': 'Unité de la relance' }, el('option', { value: 'pct' }, '% du pot'),
      el('option', { value: 'x' }, '× la mise')) : el('span', { class: 'small' }, '% du pot');
    const add = () => {
      const v = Number(String(input.value).replace(',', '.'));
      if (!(v > 0)) { input.focus(); return; }
      push(raise && unit.value === 'x' ? 'x' + v : v);
    };
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') add(); });
    chips.append(input, unit, el('button', { type: 'button', class: 'rg-btn', onclick: add }, 'Ajouter'),
      el('button', { type: 'button', class: 'rg-btn', title: 'La même fraction du pot à chaque street, pour finir à tapis à la river',
        onclick: () => push('geo') }, '+ géo'),
      el('button', { type: 'button', class: 'rg-btn', onclick: () => push('a') }, '+ tapis'));
    box.append(chips);
    return box;
  }

  function builderRow(r, reach) {
    const c = choiceOf(r);
    const seg = el('div', { class: 'seg cb-modes', role: 'group', 'aria-label': 'Choix : ' + r.title });
    CB_MODES.forEach(([m, label]) => {
      const blocked = m === 'none' && r.played;
      seg.append(el('button', {
        type: 'button', 'aria-pressed': String(c.mode === m), disabled: blocked,
        title: blocked ? 'La ligne jouée passe par là : elle garde une mise' : m === 'none' ? (r.raise ? 'Pas de relance : fold ou call seulement'
          : 'Pas de mise : check seulement ; la branche disparaît de l\'arbre') : m === 'auto' ? 'Le solveur compare ces tailles et garde la meilleure, avant la résolution' : '',
        onclick: () => { setChoice(r, m); renderBuilder(); },
      }, m === 'default' ? 'Origine : ' + (r.base.length ? sizesText(r.base) : 'aucune') : label));
    });
    const tags = [r.player];
    if (r.played) tags.push('ligne jouée');
    if (!reach) tags.push('ne peut plus arriver');
    const row = el('div', { class: 'cb-row' + (reach ? '' : ' off') + (c.mode !== 'default' ? ' mod' : '') },
      el('div', { class: 'cb-title' }, el('b', {}, r.title), el('span', { class: 'muted small' }, ' · ' + tags.join(' · '))), seg);
    if (c.mode === 'fixed' || c.mode === 'auto') {
      row.append(chipsEditor(r, c.sizes, (list) => { cb.draft[r.key] = { mode: c.mode, sizes: list }; renderBuilder(); }, r.raise));
    }
    if (c.mode === 'auto') {
      row.append(el('div', { class: 'small muted' }, 'Il en garde ' + r.choose + (r.choose > 1 ? ' (river)' : '')
        + (r.chosen && cb.data.rows.find((x) => x.key === r.key).mode === 'auto' ? ' · déjà choisi : ' + sizesText(r.chosen) : '')));
    }
    return row;
  }

  // Toute une street d'un coup (mises, ou relances) : un choix et ses tailles, appliqués à chaque situation.
  function quickRow(street, raise) {
    const rows = cb.data.rows.filter((r) => r.street === street && r.raise === raise);
    if (!rows.length) return '';
    const id = street + (raise ? 'r' : 'b');
    const q = cb.quick[id] || (cb.quick[id] = { mode: 'auto', sizes: [] });
    const select = el('select', { 'aria-label': 'Choix pour toutes les ' + (raise ? 'relances' : 'mises') + ' de la street',
      onchange: (e) => { q.mode = e.target.value; renderBuilder(); } },
    CB_MODES.map(([m, label]) => el('option', { value: m, selected: q.mode === m }, label)));
    const apply = () => {
      if (q.mode === 'fixed' && !q.sizes.length) { cb.msg = 'Ajoute au moins une taille à appliquer.'; renderBuilder(); return; }
      const reach = reachableNow();
      rows.forEach((r) => {
        if (q.mode === 'none' && r.played) return;  // la ligne jouée garde sa mise
        if (q.mode !== 'default' && !reach[r.key]) return;  // une situation qui ne peut plus arriver reste telle quelle
        setChoice(r, q.mode, q.mode === 'auto' ? (q.sizes.length ? q.sizes : r.candidates) : q.sizes);
      });
      cb.msg = '';
      renderBuilder();
    };
    const box = el('div', { class: 'cb-quick' }, el('div', { class: 'rg-row' },
      el('b', {}, (raise ? 'Toutes les relances' : 'Toutes les mises') + ' (' + rows.length + ')'), select,
      el('button', { type: 'button', class: 'rg-btn', onclick: apply }, 'Appliquer')));
    if (q.mode === 'fixed' || q.mode === 'auto') {
      box.append(chipsEditor(null, q.sizes, (list) => { q.sizes = list; renderBuilder(); }, raise));
      if (q.mode === 'auto' && !q.sizes.length) box.append(el('div', { class: 'small muted' }, 'Sans taille ajoutée : celles que le solveur compare d\'habitude dans chaque situation.'));
    }
    return box;
  }

  function renderBuilder() {
    const box = $('builder');
    const layout = document.querySelector('main.layout');
    box.hidden = !cb.open;
    if (layout) layout.hidden = cb.open;
    box.textContent = '';
    if (!cb.open) return;
    const d = cb.data;
    box.append(el('div', { class: 'cb-head' }, el('h2', {}, 'Ton arbre'),
      d && d.available ? el('span', { class: 'muted small' }, d.family_title + ' · ' + d.positions.join(' contre ')) : '',
      el('button', { type: 'button', class: 'rg-btn', onclick: closeBuilder }, 'Fermer')));
    if (!d) { box.append(el('p', { class: 'muted' }, cb.msg || 'Chargement…')); return; }
    if (!d.available) { box.append(el('p', {}, d.message)); return; }
    box.append(el('p', { class: 'small muted cb-intro' }, 'Avant la résolution, pour chaque situation : ses tailles d\'origine, '
      + 'les tiennes (« Fixées »), aucune mise ou relance (« Aucune » : sa branche disparaît, le solveur ne l\'explore pas), ou '
      + '« Le solveur choisit » : il compare les tailles que tu lui donnes et garde la meilleure (une à la river : deux), avant '
      + 'de résoudre. Plus il a de tailles à comparer, plus c\'est long : quelques minutes par situation au flop.'));
    const reach = reachableNow();
    const tabs = el('div', { class: 'seg', role: 'group', 'aria-label': 'Street' });
    STREET_WORDS.forEach((w, st) => {
      const n = d.rows.filter((r) => r.street === st && cb.draft[r.key]).length;
      tabs.append(el('button', { type: 'button', 'aria-pressed': String(cb.street === st), onclick: () => { cb.street = st; renderBuilder(); } },
        w + (n ? ' · ' + n : '')));
    });
    const counts = { fixed: 0, none: 0, auto: 0 };
    Object.values(cb.draft).forEach((c) => { counts[c.mode] += 1; });
    const summary = [counts.fixed ? counts.fixed + ' fixée(s)' : '', counts.none ? counts.none + ' sans mise ni relance' : '',
      counts.auto ? counts.auto + ' au choix du solveur' : ''].filter(Boolean).join(' · ') || 'l\'arbre d\'origine';
    box.append(el('div', { class: 'rg-row' }, tabs, el('span', { class: 'small muted' }, summary)));
    const st = cb.street;
    const panel = el('div', { class: 'cb-street' });
    panel.append(el('div', { class: 'cb-quicks' }, quickRow(st, false), quickRow(st, true)));
    const rows = d.rows.filter((r) => r.street === st);
    const raises = rows.filter((r) => r.raise).length;
    if (raises) {
      panel.append(el('label', { class: 'small cb-toggle' }, el('input', { type: 'checkbox', checked: cb.raises[st],
        onchange: (e) => { cb.raises[st] = e.target.checked; renderBuilder(); } }), ' Montrer les relances, une par une (' + raises + ')'));
    }
    const lines = [...new Set(rows.map((r) => r.line))];
    lines.forEach((line) => {
      const shown = rows.filter((r) => r.line === line && (!r.raise || cb.raises[st]));
      if (!shown.length) return;
      if (line) panel.append(el('h4', {}, 'Après : ' + (d.lines[line] || line)));
      shown.forEach((r) => panel.append(builderRow(r, reach[r.key])));
    });
    box.append(panel);
    const sorted = (x) => JSON.stringify(Object.keys(x).sort().map((k) => [k, x[k]]));
    const toChoose = counts.auto && (d.pending || sorted(cb.draft) !== sorted(draftOf(d)));
    const actions = el('div', { class: 'rg-row cb-actions' },
      el('button', { type: 'button', class: 'rg-btn go', disabled: cb.busy, onclick: () => saveBuilder(true) },
        toChoose ? 'Choisir les tailles, puis résoudre' : 'Résoudre avec cet arbre'),
      el('button', { type: 'button', class: 'rg-btn', disabled: cb.busy, onclick: () => saveBuilder(false) }, 'Enregistrer'),
      Object.keys(cb.draft).length ? el('button', { type: 'button', class: 'rg-btn', disabled: cb.busy,
        onclick: () => { cb.draft = {}; renderBuilder(); } }, 'Tout remettre à l\'origine') : '');
    box.append(actions);
    if (cb.msg) box.append(el('p', { class: 'small' + (cb.msg.endsWith('.') && !/^Arbre|^Rien/.test(cb.msg) ? ' err' : '') }, cb.msg));
    box.append(el('p', { class: 'small muted' }, 'Tailles comparées d\'habitude : ' + d.compared + '. Ton arbre se résout '
      + 'dans une étude à part : celle d\'origine reste. Après la résolution, l\'onglet Arbre change encore une situation ou '
      + 'verrouille un nœud.'));
  }

  async function saveBuilder(solveAfter) {
    if (cb.data.locks && !window.confirm('Changer l\'arbre retire tes ' + cb.data.locks + ' verrou(s) : leurs chemins ne mènent '
      + 'plus aux mêmes nœuds. Continuer ?')) return;
    cb.busy = true;
    cb.msg = '';
    renderBuilder();
    try {
      cb.data = await api('/api/explorateur/construction/enregistrer', { hand: HAND, choices: cb.draft });
      cb.draft = draftOf(cb.data);
      cb.busy = false;
      tr.data = null;
      if (solveAfter) {
        cb.open = false;
        renderBuilder();
        await reopen(true, false);
        return;
      }
      cb.msg = cb.data.changed ? 'Arbre enregistré : « Résoudre » le calcule.' : 'Rien n\'a changé.';
      await loadState();
      renderStatus();
    } catch (e) {
      cb.msg = e.message;
    }
    cb.busy = false;
    renderBuilder();
  }

  // --- verrou ---
  function lockTargets() {
    const p = node.player;
    const rows = node.hands[p];
    const out = [];
    if (selected) {
      const cell = rows.filter((r) => classOf(r[0]) === selected);
      if (cell.length) out.push({ id: 'cell', label: selected, combos: cell.map((r) => r[0]), rows: cell });
    }
    if (tr.combo) {
      const row = rows.find((r) => r[0] === tr.combo);
      if (row) out.push({ id: 'combo', label: comboLabel(row[0]), combos: [row[0]], rows: [row] });
    }
    if (filterActive() && viewPlayer === p) {
      const kept = rows.filter((r, i) => passes(p, i, r));
      if (kept.length) out.push({ id: 'filter', label: 'Mains filtrées', combos: kept.map((r) => r[0]), rows: kept });
    }
    return out;
  }

  function currentMix(t) {
    const na = node.actions.length;
    const w = t.rows.reduce((x, r) => x + r[1], 0) || 1;
    return node.actions.map((_, k) => Math.round(100 * t.rows.reduce((x, r) => x + r[1] * r[4 + k], 0) / w));
  }

  function addEdit(t, freqs) {
    tr.edits.push({ label: t.label, combos: t.combos, freqs });
    tr.mix = null;
    renderTree();
  }

  function lockPanel() {
    const box = el('div', { class: 'tr-box' }, el('h3', {}, 'Verrou (nodelock)'));
    if (!node || node.type !== 'action' || node.actions.length < 2) {
      box.append(el('p', { class: 'small muted' }, 'Va à un nœud où le joueur a le choix pour verrouiller sa stratégie.'));
      return box;
    }
    if (!live) {
      box.append(el('p', { class: 'small muted' }, 'Ouvre la résolution complète (session active) pour verrouiller ce nœud.'));
      return box;
    }
    const p = node.player;
    const labels = nodeLabels();
    const cols = colors(node.actions);
    box.append(el('p', { class: 'small' }, who(p) + ' agit : choisis des mains et leur façon de jouer ici, puis relance la résolution.'));
    if (viewPlayer !== p) box.append(el('p', { class: 'small muted' }, 'La grille montre la range de ' + who(viewPlayer) + ' : affiche celle de '
      + who(p) + ' pour choisir ses mains.'));
    const targets = lockTargets();
    if (!targets.some((t) => t.id === tr.target)) tr.target = targets.length ? targets[0].id : null;
    if (!targets.length) {
      box.append(el('p', { class: 'small muted' }, 'Mains : clique une case de la grille, le cadenas d\'un combo (onglet Mains), '
        + 'ou choisis des mains dans les Filtres (main faite, tirage, équité…).'));
    } else {
      const seg = el('div', { class: 'seg', role: 'group', 'aria-label': 'Mains à verrouiller' });
      targets.forEach((t) => seg.append(el('button', {
        type: 'button', 'aria-pressed': String(t.id === tr.target), onclick: () => { tr.target = t.id; tr.mix = null; renderTree(); },
      }, t.label + ' (' + t.combos.length + ')')));
      box.append(el('div', { class: 'rg-row' }, el('span', { class: 'small' }, 'Mains'), seg));
      const t = targets.find((x) => x.id === tr.target);
      const acts = el('div', { class: 'tr-acts' });
      labels.forEach((label, k) => acts.append(el('button', {
        type: 'button', class: 'rg-btn', onclick: () => addEdit(t, node.actions.map((_, j) => (j === k ? 1 : 0))),
      }, el('i', { class: 'sw', style: 'background:' + cols[k] }), 'Toujours ' + label.toLowerCase())));
      box.append(el('div', { class: 'small' }, 'Stratégie : une action à 100 %…'), acts);
      if (!tr.mix || tr.mixFor !== t.id + ':' + t.label) { tr.mix = currentMix(t); tr.mixFor = t.id + ':' + t.label; }
      const mix = tr.mix;
      const inputs = el('div', { class: 'tr-mix' });
      labels.forEach((label, k) => inputs.append(el('label', {}, el('i', { class: 'sw', style: 'background:' + cols[k] }), label + ' ',
        el('input', { type: 'number', min: '0', max: '100', step: '1', value: String(mix[k]), class: 'tr-in',
          oninput: (e) => { mix[k] = Math.max(0, Number(e.target.value) || 0); } }), ' %')));
      box.append(el('div', { class: 'small' }, '… ou un mélange (en %, ramené à 100 ; au départ, celui du solveur pour ces mains) :'), inputs,
        el('button', { type: 'button', class: 'rg-btn', onclick: () => {
          const total = mix.reduce((x, y) => x + y, 0);
          if (total > 0) addEdit(t, mix.map((x) => x / total));
        } }, 'Ajouter ce mélange'));
    }
    if (tr.edits.length) {
      const list = el('ul', { class: 'tr-edits' });
      tr.edits.forEach((e, k) => list.append(el('li', {}, el('b', {}, e.label), ' (' + e.combos.length + ') : ' + mixText(e.freqs, labels) + ' ',
        el('button', { type: 'button', class: 'link', onclick: () => { tr.edits.splice(k, 1); renderTree(); } }, 'retirer'))));
      box.append(el('div', { class: 'small' }, 'À verrouiller à ce nœud :'), list,
        el('div', { class: 'rg-row' }, el('button', { type: 'button', class: 'rg-btn go', disabled: tr.busy, onclick: saveLock }, 'Verrouiller et résoudre')));
    }
    box.append(el('p', { class: 'small muted' }, 'Le solveur verrouille le nœud entier (comme PioSolver) : les autres mains de '
      + who(p) + ' gardent la stratégie affichée, et tout le reste de l\'arbre s\'adapte à la nouvelle résolution. '
      + 'Une main et ses équivalents de couleur sur ce board sont verrouillés ensemble.'));
    return box;
  }

  async function saveLock() {
    const edits = tr.edits.map(({ label, combos, freqs }) => ({ label, combos, freqs }));
    await treeCall('/api/explorateur/arbre/verrou', { hand: HAND, path, edits, labels: nodeLabels() }, true,
      () => { tr.edits = []; tr.combo = null; });
  }

  function locksList(d) {
    const box = el('div', { class: 'tr-box' }, el('h3', {}, 'Tes verrous (' + d.locks.length + ')'));
    d.locks.forEach((x) => {
      const here = same(x.path, path);
      box.append(el('div', { class: 'tr-lock' + (here ? ' on' : '') }, el('div', {}, el('b', {}, x.title)),
        el('div', { class: 'small' }, x.edits.map((e) => e.label + ' (' + e.combos + ') : ' + mixText(e.freqs, x.actions)).join(' ; ')),
        el('div', { class: 'rg-row' }, here ? el('span', { class: 'small muted' }, 'nœud affiché')
          : el('button', { type: 'button', class: 'rg-btn', disabled: !live, onclick: () => goTo(x.path) }, 'Y aller'),
        el('button', { type: 'button', class: 'rg-btn', disabled: tr.busy, onclick: () => treeCall('/api/explorateur/arbre/deverrouiller',
          { hand: HAND, index: x.index }, true) }, 'Retirer'))));
    });
    if (d.locks.length > 1) box.append(el('button', { type: 'button', class: 'rg-btn', disabled: tr.busy,
      onclick: () => treeCall('/api/explorateur/arbre/deverrouiller', { hand: HAND, index: null }, true) }, 'Retirer tous les verrous'));
    return box;
  }

  function renderTabs() {
    const n = filters.keys.size;
    $('tab-combos').setAttribute('aria-selected', rightTab === 'combos' ? 'true' : 'false');
    $('tab-filters').setAttribute('aria-selected', rightTab === 'filters' ? 'true' : 'false');
    $('tab-coach').setAttribute('aria-selected', rightTab === 'coach' ? 'true' : 'false');
    $('tab-ranges').setAttribute('aria-selected', rightTab === 'ranges' ? 'true' : 'false');
    $('tab-tree').setAttribute('aria-selected', rightTab === 'tree' ? 'true' : 'false');
    $('pane-coach').hidden = rightTab !== 'coach';
    $('pane-ranges').hidden = rightTab !== 'ranges';
    $('pane-tree').hidden = rightTab !== 'tree';
    if (rightTab === 'coach') mountCoach();
    if (rightTab === 'ranges') mountRanges();
    if (rightTab === 'tree' && node) mountTree();
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
    $('b-train').disabled = PRE || !live || node.type !== 'action' || node.actions.length < 2 || !!(state && state.edits);
    $('b-train').title = state && state.edits ? 'L\'entraîneur joue l\'arbre d\'origine : reviens-y pour t\'entraîner ici'
      : 'Jouer des mains à partir de ce moment du coup, face au solveur';
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

  // Premier nœud affiché ; dans un spot d'étude, on passe le check forcé de la BB (qui ne mène pas). Après une
  // nouvelle résolution de ton arbre, le nœud où tu étais, s'il existe encore.
  async function goStart() {
    if (resume) {
      const target = resume;
      resume = null;
      await goTo(target);
      if (node) return;
    }
    await goTo(initialPath(), true);
  }

  // Le coup tel que l'état le donne ; solveNow : le résoudre s'il ne l'est pas encore (nouvelles ranges).
  async function showSpot(solveNow) {
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
    else if (state.state === 'absent' && (solveNow || (SPOT && /resoudre/.test(location.hash)))) {
      if (!solveNow) history.replaceState(null, '', location.pathname);  // choisi dans le sélecteur de flop
      solve();
    } else poll();
  }

  // ---------- démarrage ----------
  $('b-back').onclick = back;
  $('tab-combos').onclick = () => { rightTab = 'combos'; renderTabs(); };
  $('tab-filters').onclick = () => { rightTab = 'filters'; renderTabs(); };
  $('tab-coach').onclick = () => { rightTab = 'coach'; renderTabs(); };
  $('tab-ranges').onclick = () => { rightTab = 'ranges'; renderTabs(); };
  $('tab-tree').onclick = () => { rightTab = 'tree'; renderTabs(); };
  document.addEventListener('pointerup', () => { rg.painting = false; });
  $('b-line').onclick = () => state && state.result && goTo(state.result.decisions[0].path);
  $('b-train').onclick = () => window.open('/entraineur?spot=' + encodeURIComponent(HAND) + '&chemin='
    + encodeURIComponent(JSON.stringify(path)), '_blank', 'noopener');
  if (SPOT) {  // pas de ligne jouée ni de main adverse à dévoiler
    $('b-line').hidden = true;
    $('reveal').closest('label').hidden = true;
    const ident = HAND.split(':');
    document.title = 'Explorateur — ' + ident[1].replace(/^6max_/, '6-max ').replace(/_/g, ' ').toUpperCase() + ' ' + ident[2];
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
    $('tab-ranges').hidden = true;
    $('tab-tree').hidden = true;
    document.title = 'Explorateur — préflop' + (TABLE !== 'HU' ? ' ' + TABLE : '');
  }
  if (SPOT) {  // ligne préflop du spot (solution heads-up, ou charts 6-max), en tête du déroulé
    api('/api/explorateur/preflop', FAMILY.startsWith('6max_') ? { table: '6-max', family: FAMILY } : { family: FAMILY })
      .then((n) => { prefix = n; if (node) render(); }).catch(() => {});
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
    if (/arbre/.test(location.hash)) {  // « Construire l'arbre d'abord » : pas de résolution tout de suite
      history.replaceState(null, '', location.pathname);
      await showSpot(false);
      openBuilder();
      return;
    }
    await showSpot(false);
  })();
})();
