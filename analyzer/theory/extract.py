"""Lit une grille de stratégie préflop (capture d'écran d'un solveur) et en tire les fréquences.

Chaque case 13 × 13 est lue ainsi :
- la largeur de chaque bande de couleur donne la fréquence de l'action ;
- la hauteur de remplissage donne la part de la main présente dans la range à ce nœud.

Couleurs reconnues (convention des solveurs courants) : violet = tapis, orange = relance,
vert = call, bleu = fold. Nécessite Pillow (`pip install pillow`), uniquement pour cet outil.

    python -m analyzer.theory.extract capture.png
    python -m analyzer.theory.extract capture.png --solution analyzer/theory/data/hu_100bb.json --noeud bb_vs_open
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RANKS = "AKQJT98765432"
COLORS = {"allin": (112, 0, 136), "raise": (240, 96, 0), "call": (96, 224, 88), "fold": (8, 144, 200)}
ACTIONS = tuple(COLORS)
COLOR_TOLERANCE = 70
CELL_WIDTH, CELL_HEIGHT = 84, 56  # ordre de grandeur, pour filtrer les lignes parasites


def hand_at(row: int, col: int) -> str:
    if row == col:
        return RANKS[row] * 2
    if col > row:
        return RANKS[row] + RANKS[col] + "s"
    return RANKS[col] + RANKS[row] + "o"


def combos(hand: str) -> int:
    return 6 if len(hand) == 2 else 4 if hand[2] == "s" else 12


def _classify(pixel) -> str | None:
    best, dist = None, float("inf")
    for action, color in COLORS.items():
        d = sum((a - b) ** 2 for a, b in zip(pixel, color)) ** 0.5
        if d < dist:
            best, dist = action, d
    return best if dist < COLOR_TOLERANCE else None


def _grid_lines(px, width: int, height: int) -> tuple[list[int], list[int]]:
    """Positions des séparateurs (lignes presque noires) de la grille."""
    def dark(p):
        return max(p[:3]) <= 21

    area = min(width, CELL_WIDTH * 13 + 20)
    cols = [x for x in range(area) if sum(dark(px[x, y]) for y in range(30, height)) > 0.6 * (height - 30)]
    rows = [y for y in range(28, height) if sum(dark(px[x, y]) for x in range(area)) > 0.6 * area]

    def spaced(values: list[int], step: int) -> list[int]:
        out: list[int] = []
        for v in values:
            if not out or v - out[-1] > step * 0.7:
                out.append(v)
        return out

    cols, rows = spaced(cols, CELL_WIDTH), spaced(rows, CELL_HEIGHT)
    if len(cols) == 13:  # bord gauche collé au bord de l'image
        cols = [max(0, cols[0] - (cols[1] - cols[0]))] + cols
    return cols[:14], rows[:14]


def extract_grid(path: str | Path) -> dict[str, dict[str, float]]:
    """{main: {"w": poids, action: fréquence, ...}} ; poids 0 = main absente à ce nœud."""
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - dépend de l'environnement
        raise SystemExit("Cet outil nécessite Pillow : pip install pillow") from exc
    image = Image.open(path).convert("RGB")
    width, height = image.size
    px = image.load()
    cols, rows = _grid_lines(px, width, height)
    if len(cols) != 14 or len(rows) != 14:
        raise ValueError(f"Grille 13 × 13 introuvable dans {path} ({len(cols)} colonnes, {len(rows)} lignes).")
    grid: dict[str, dict[str, float]] = {}
    for i in range(13):
        for j in range(13):
            x0, x1 = cols[j] + 2, cols[j + 1] - 1
            y0, y1 = rows[i] + 2, rows[i + 1] - 1
            counts = dict.fromkeys(ACTIONS, 0)
            top = y1
            for x in range(x0, x1):
                found = None
                for y in range(y1 - 1, y0 - 1, -1):  # du bas vers le haut : le texte blanc est ignoré
                    action = _classify(px[x, y])
                    if action:
                        found = found or action
                        top = min(top, y)
                if found:
                    counts[found] += 1
            total = sum(counts.values())
            hand = hand_at(i, j)
            if total < 0.5 * (x1 - x0):
                grid[hand] = {"w": 0.0}
                continue
            cell = {"w": round(min(1.0, (y1 - top) / (y1 - y0)), 2)}
            cell.update({a: round(n / total, 3) for a, n in counts.items() if n})
            grid[hand] = cell
    return grid


def totals(grid: dict[str, dict[str, float]]) -> dict[str, float]:
    """Fréquences globales pondérées par le nombre de combos (pour comparer avec le solveur)."""
    weight = sum(combos(h) * c["w"] for h, c in grid.items())
    out = {"combos": round(weight, 1)}
    for action in ACTIONS:
        share = sum(combos(h) * c["w"] * c.get(action, 0) for h, c in grid.items())
        out[action] = round(100 * share / weight, 1) if weight else 0.0
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m analyzer.theory.extract", description=__doc__.splitlines()[0])
    parser.add_argument("image", help="capture de la grille 13 × 13")
    parser.add_argument("--solution", help="fichier de solution JSON à compléter")
    parser.add_argument("--noeud", help="nom du nœud à remplacer dans la solution (ex. bb_vs_open)")
    args = parser.parse_args(argv)

    grid = extract_grid(args.image)
    summary = totals(grid)
    print("Total : " + ", ".join(f"{k} {v}" for k, v in summary.items()), file=sys.stderr)
    hands = {h: {a: f for a, f in c.items() if a != "w"} for h, c in grid.items() if c["w"] > 0}
    if args.solution and args.noeud:
        path = Path(args.solution)
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"nodes": {}}
        node = data.setdefault("nodes", {}).setdefault(args.noeud, {})
        node["hands"] = hands
        node["measured"] = summary
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"Nœud {args.noeud} écrit dans {path}", file=sys.stderr)
    else:
        print(json.dumps(hands, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
