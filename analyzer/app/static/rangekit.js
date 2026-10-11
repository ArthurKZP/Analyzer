// Ranges préflop : lire, écrire et retoucher une range (169 classes de mains, poids de 0 à 1), et la grille 13 × 13
// à peindre. Partagé par l'explorateur (onglet Ranges) et les Paramètres (Ranges préflop).
(function () {
  'use strict';

  const RANKS = 'AKQJT98765432';
  const HAND_RE = /^(?:([2-9TJQKA])\1|[2-9TJQKA]{2}[so])$/;
  const RESIZE = 0.10;  // « plus serré » / « plus large » : environ 10 % des combos de la range
  const BRUSHES = [[1, '100 %'], [0.75, '75 %'], [0.5, '50 %'], [0.25, '25 %'], [0, 'Retirer']];
  const handAt = (i, j) => (i === j ? RANKS[i] + RANKS[i] : i < j ? RANKS[i] + RANKS[j] + 's' : RANKS[j] + RANKS[i] + 'o');
  const ALL_HANDS = [];
  for (let i = 0; i < 13; i++) for (let j = 0; j < 13; j++) ALL_HANDS.push(handAt(i, j));

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

  // Plus serré (sign < 0) : retire environ 10 % des combos, en partant des mains les plus faibles de la range (order :
  // les 169 mains de la plus forte à la plus faible) ; plus large : en ajoute autant, en partant des plus fortes qui
  // n'y sont pas en entier.
  function resize(w, sign, order) {
    const out = { ...w };
    let left = RESIZE * combosOf(w);
    if (sign > 0) left = Math.max(left, 0.01 * 1326);
    for (const h of sign < 0 ? order.slice().reverse() : order) {
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

  // La grille : paint(main) pour chaque case cliquée ou survolée tant que le bouton (ou le doigt) reste appuyé ;
  // busy() : rien ne se peint pendant un enregistrement.
  let painting = false;
  document.addEventListener('pointerup', () => { painting = false; });
  function grid(paint, busy) {
    const box = document.createElement('div');
    box.className = 'rgrid';
    box.setAttribute('aria-label', 'Range : clique ou glisse sur les mains pour les peindre');
    const cells = ALL_HANDS.map((h) => {
      const cell = box.appendChild(document.createElement('div'));
      cell.className = 'rcell';
      cell.dataset.h = h;
      const label = cell.appendChild(document.createElement('span'));
      label.className = 'h';
      label.textContent = h;
      return cell;
    });
    box.addEventListener('pointerdown', (e) => {
      const cell = e.target.closest('.rcell');
      if (!cell || (busy && busy())) return;
      painting = true;
      paint(cell.dataset.h);
      e.preventDefault();
    });
    box.addEventListener('pointermove', (e) => {
      if (!painting) return;
      const target = document.elementFromPoint(e.clientX, e.clientY);
      const cell = target && target.closest('.rcell');
      if (cell && box.contains(cell)) paint(cell.dataset.h);
    });
    return { box, cells };
  }

  window.AnalyzerRanges = { BRUSHES, ALL_HANDS, comboCount, combosOf, sameRange, rangeText, parseRange, resize, grid };
})();
