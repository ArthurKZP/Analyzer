"""python -m analyzer gtopen : installer le solveur GTOpen et résoudre une main dans le terminal."""
from __future__ import annotations

import argparse
import sys
from typing import Optional

from . import postflop


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m analyzer gtopen",
        description="Résolution postflop de tes mains avec le moteur de GTOpen. Sans option : état du solveur.",
    )
    parser.add_argument("paths", nargs="*", default=["hands"], help="historiques où chercher la main (défaut : hands/)")
    parser.add_argument("--installer", action="store_true", help="récupère GTOpen si besoin et compile le solveur")
    parser.add_argument("--source", help="dossier d'une copie de GTOpen déjà présente sur ton ordinateur")
    parser.add_argument("--gpu", action="store_true",
                        help="avec --installer : compile aussi le moteur CUDA (carte NVIDIA, expérimental)")
    parser.add_argument("-m", "--main", help="numéro de la main à résoudre (ou sa fin)")
    parser.add_argument("--spots", choices=["srp"], help="résout la série de spots d'étude (ex. srp : 24 flops)")
    parser.add_argument("--texture", action="append", help="avec --spots : seulement cette texture (répétable)")
    parser.add_argument("--hero", help="ton pseudo (détecté automatiquement)")
    parser.add_argument("--iterations", type=int, default=postflop.DEFAULT_ITERATIONS, help="itérations maximum")
    parser.add_argument("--precision", type=float, default=postflop.DEFAULT_TARGET,
                        help="exploitabilité visée, en %% du pot (défaut : %(default)s)")
    parser.add_argument("--threads", type=int, default=0, help="nombre de cœurs (défaut : tous)")
    parser.add_argument("--sans-cache", action="store_true", help="ignore une résolution déjà enregistrée")
    args = parser.parse_args(argv)

    if args.installer:
        try:
            binary = postflop.install(args.source, gpu=args.gpu)
        except postflop.SolverError as exc:
            print(exc, file=sys.stderr)
            return 1
        print(f"Solveur prêt : {binary}")
        return 0

    if args.spots:
        from .studyspots import TEXTURES, find_texture, solve_set
        textures = [find_texture(t) for t in args.texture or []]
        unknown = [t for t, found in zip(args.texture or [], textures) if found is None]
        if unknown:
            print(f"Texture inconnue : {', '.join(unknown)} (choix : {', '.join(TEXTURES)})", file=sys.stderr)
            return 1
        try:
            n = solve_set(args.spots, textures or None, iterations=args.iterations, target=args.precision,
                          threads=args.threads)
        except postflop.SolverError as exc:
            print(exc, file=sys.stderr)
            return 1
        print(f"{n} spot(s) résolu(s) ; ils sont dans « Études du solveur ».")
        return 0

    if not args.main:
        state = postflop.status()
        print(state["message"])
        print(f"  programme : {state['binary']}")
        print(f"  GTOpen    : {state['source'] or 'introuvable'}")
        return 0 if state["ready"] else 1

    from ..cli import detect_hero
    from ..parsers import load_hands

    hands = load_hands(args.paths)
    match = [h for h in hands if h.hand_id == args.main] or [h for h in hands if h.hand_id.endswith(args.main)]
    if len(match) != 1:
        print(f"Main introuvable ou ambiguë : {args.main!r} ({len(match)} correspondances).", file=sys.stderr)
        return 1
    hand = match[0]
    hero = args.hero or detect_hero(hands)
    try:
        spot = postflop.build_spot(hand, hero)
    except postflop.Unsupported as exc:
        print(exc, file=sys.stderr)
        return 1
    request = spot.request(args.iterations, args.precision, args.threads)

    def progress(p: dict) -> None:
        if "iteration" in p:
            print(f"\r  itération {p['iteration']:4d} · exploitabilité {p['exploit_pct']:6.2f} % du pot · "
                  f"{p['elapsed']:.0f} s", end="", file=sys.stderr, flush=True)
        elif "tree_nodes" in p:
            print(f"  arbre : {p['tree_nodes']:,} nœuds, {p['arena_mb']:.0f} Mo".replace(",", " "), file=sys.stderr)

    try:
        raw = postflop.solve(request, on_progress=progress, use_cache=not args.sans_cache, save_study=True)
        if postflop.study_path(request).is_file():
            postflop.write_study_meta(spot, request, raw)  # visible dans « Études du solveur »
    except postflop.SolverError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1
    print(file=sys.stderr)
    print(postflop.result_text(postflop.interpret(spot, raw), hero))
    return 0


if __name__ == "__main__":
    sys.exit(main())
