"""Ligne de commande : python -m analyzer [fichiers ou dossiers] [options]."""
from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

from .insights import DUELS, combined, duel_verdict, findings
from .lines import fold_holdings_by_street, verdict, villain_lines
from .models import Hand
from .parsers import load_hands
from .report import build_report, num
from .stats import PlayerStats, analyze

KEY_STATS = [
    ("VPIP / PFR bouton", "vpip_sb", "pfr_sb"),
    ("VPIP / PFR BB", "vpip_bb", "pfr_bb"),
    ("BB : fold / 3bet vs open", "bb_vs_open.fold", "bb_vs_open.raise"),
    ("BTN : fold / 4bet vs 3bet", "sb_vs_3bet.fold", "sb_vs_3bet.raise"),
    ("C-bet flop / fold vs c-bet", "cbet_flop", "vs_cbet_flop.fold"),
    ("Check-raise flop / fold vs bet river", "xr_flop", "vs_bet_river.fold"),
    ("WTSD / W$SD", "wtsd", "wsd"),
]


def slugify(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "-", ascii_name).strip("-").lower() or "adversaire"


def detect_hero(hands: list[Hand]) -> str | None:
    heroes = Counter(h.hero for h in hands if h.hero)
    if heroes:
        return heroes.most_common(1)[0][0]
    players = Counter(p for h in hands for p in h.seats)
    return players.most_common(1)[0][0] if players else None


def find_player(query: str, names: list[str]) -> str | None:
    exact = [n for n in names if n == query]
    if exact:
        return exact[0]
    folded = slugify(query)
    matches = [n for n in names if folded in slugify(n)]
    return matches[0] if len(matches) == 1 else None


def summary(villain: PlayerStats, hero: PlayerStats, hands: list[Hand]) -> str:
    lines = [
        f"== {villain.name} — {villain.hands} mains ==",
        f"Ton résultat : {num(hero.net_bb, 1, sign=True)} bb ({num(hero.net, 2, sign=True)} €), "
        f"{num(hero.bb_per_100, 1, sign=True)} bb/100 · EV all-in : {num(hero.net_bb + hero.ev_adjust_bb, 1, sign=True)} bb",
        "",
        f"{'':38s}{'Lui':>14s}{'Toi':>14s}",
    ]
    for label, a, b in KEY_STATS:
        pairs = [f"{num(ps.pct(a), 0)} / {num(ps.pct(b), 0)}" for ps in (villain, hero)]
        lines.append(f"{label:38s}{pairs[0]:>14s}{pairs[1]:>14s}")
    lines.append("")
    top = findings(villain)[:6]
    if top:
        lines.append("Ses tendances exploitables :")
        for f in top:
            tag = "net" if f.strong else "tendance"
            lines.append(
                f"  - [{tag}] {f.stat.label} {num(f.ratio.pct, 0)} % ({f.ratio.hits}/{f.ratio.opps}) : "
                f"{f.reading.fact} → {f.reading.exploit}"
            )
    lines_ = villain_lines(hands, villain.name, hero.name)
    folds = fold_holdings_by_street(lines_)
    alerts = []
    for d in DUELS:
        level, text = duel_verdict(d, combined(villain, d.villain_key), combined(hero, d.hero_key),
                                   folds[d.street] if d.street else None)
        if level == "alerte":
            alerts.append(f"  - {d.title} : {text}")
    if alerts:
        lines.append("Points d'attention dans le duel :")
        lines.extend(alerts)
    frequent = sorted((ln for ln in lines_ if len(ln.seen) >= 3), key=lambda ln: -ln.count)[:8]
    if frequent:
        lines.append("Ses lignes les plus vues à l'abattage :")
        for ln in frequent:
            v = verdict(ln)
            lines.append(f"  - {ln.street} · {ln.name} ({ln.count} fois, {len(ln.seen)} vues) : {v.kind} — {v.reading}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m analyzer",
        description="Analyse tes adversaires Heads-Up à partir de tes historiques de mains.",
    )
    parser.add_argument("paths", nargs="*", default=["hands"], help="fichiers ou dossiers d'historiques (défaut : hands/)")
    parser.add_argument("-a", "--adversaire", help="nom (ou partie du nom) de l'adversaire à analyser")
    parser.add_argument("--hero", help="ton pseudo (détecté automatiquement via le tag Hero)")
    parser.add_argument("-o", "--sortie", default="reports", help="dossier des rapports HTML (défaut : reports/)")
    parser.add_argument("-l", "--liste", action="store_true", help="liste les adversaires trouvés et quitte")
    parser.add_argument("--min-mains", type=int, default=30, help="nb de mains minimum pour générer un rapport")
    args = parser.parse_args(argv)

    try:
        hands = load_hands(args.paths)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1
    if not hands:
        print("Aucune main reconnue dans : " + ", ".join(args.paths), file=sys.stderr)
        return 1
    hero = args.hero or detect_hero(hands)
    hands = [h for h in hands if hero in h.seats and len(h.seats) == 2]
    opponents = Counter(h.opponent_of(hero) for h in hands)

    if args.liste:
        print(f"Toi : {hero} — {len(hands)} mains HU")
        for name, n in opponents.most_common():
            print(f"  {n:6d}  {name}")
        return 0

    if args.adversaire:
        target = find_player(args.adversaire, list(opponents))
        if target is None:
            print(f"Adversaire introuvable ou ambigu : {args.adversaire!r}. Utilise --liste.", file=sys.stderr)
            return 1
        targets = [target]
    else:
        targets = [name for name, n in opponents.most_common() if n >= args.min_mains]
        if not targets:
            print(f"Aucun adversaire avec au moins {args.min_mains} mains (voir --liste).", file=sys.stderr)
            return 1

    out_dir = Path(args.sortie)
    out_dir.mkdir(parents=True, exist_ok=True)
    for villain in targets:
        match = [h for h in hands if villain in h.seats]
        stats = analyze(match)
        path = out_dir / f"{slugify(villain)}.html"
        path.write_text(build_report(match, stats, hero, villain), encoding="utf-8")
        print(summary(stats[villain], stats[hero], match))
        print(f"\nRapport : {path}\n")
    return 0
