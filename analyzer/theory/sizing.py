"""Choix des tailles de mise : une taille par situation pour tout le flop (deux à la river).

Une situation est un moment de la ligne : c-bet, 2e barrel, c-bet retardée, probe, bet/check/bet,
check-raise… (clés décrites dans native/arbre.rs). Pour chacune, la taille retenue est celle qui donne
la meilleure EV à celui qui mise ou relance quand elle est sa seule option ; si l'écart est sous la
précision des résolutions (EPS), la plus petite. Les situations se choisissent une à une, les autres
gardant leur choix courant, street par street :

- flop : arbres complets, EV à la racine (c-bet, check-raise, relance du bouton) ;
- turn, puis river : sous-jeux partant de la turn, avec les ranges du flop résolu, sur un échantillon de
  cartes turn ; EV moyenne à la racine du sous-jeu. Une situation rare se compare sur moins de cartes ;
  presque jamais atteinte, elle prend la plus petite taille.

Le résultat (le plan et le détail des comparaisons) est gardé par flop ; les spots d'étude l'utilisent.
"""
from __future__ import annotations

import itertools
import json
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from . import postflop

EPS = 0.02  # bb : en dessous, deux choix sont à égalité et le plus petit l'emporte
# Arbres complets (flop) : égalité sous 0,4 % du pot de départ (0,02 bb en SRP, 0,09 bb en pot 3bet).
EPS_FLOP_POT = 0.004
# Sous-jeux : égalité sous 0,5 % du pot de la turn par occurrence de la situation (au moins 0,002 bb à la racine).
EPS_POT, EPS_FLOOR = 0.005, 0.002
METHOD = 2  # version de la méthode (un choix plus ancien peut se refaire)
FLOP_TARGET = 0.4  # % du pot : précision des arbres complets comparés
SUB_TARGET = 0.5  # % du pot du sous-jeu
TURN_CARDS = 12  # cartes turn de l'échantillon (situations fréquentes)
RIVER_CARDS = 8  # pour les tailles de la river (plus de situations, chacune plus coûteuse)
RARE_CARDS = 4  # cartes turn pour une situation rare
COMMON, RARE = 0.03, 0.003  # probabilité d'atteindre la situation (depuis le flop) : fréquente, rare

@dataclass
class Situation:
    key: str
    candidates: list
    choose: int = 1

    @property
    def street(self) -> int:
        return "ftr".index(self.key.split(":")[1][0])

    @property
    def player(self) -> int:
        return 0 if self.key.split(":")[1][1] == "o" else 1

    @property
    def past(self) -> str:
        return self.key.split(":")[2]

    def options(self) -> list[tuple]:
        """Choix possibles, du plus petit au plus grand (les candidats sont rangés par taille)."""
        return list(itertools.combinations(self.candidates, self.choose))

    def initial(self) -> list:
        """Choix de départ, avant comparaison : la taille du milieu (deux à la river)."""
        if self.choose == 2:
            return [self.candidates[1], self.candidates[3]]
        return [self.candidates[len(self.candidates) // 2]]


@dataclass(frozen=True)
class Profile:
    """Les tailles comparées d'une famille de pots. Clés et lettres : voir native/arbre.rs (o = BB, i = bouton ;
    pour chaque street jouée, son dernier agresseur, ou x si elle a été checkée)."""
    flop: tuple  # situations du flop, dans l'ordre où elles se choisissent : (clé, tailles)
    turn_bets: dict  # (joueur, flop) -> tailles ; absente : la BB ne mène pas (donk)
    river_bets: Callable[[str, str], Optional[tuple]]  # (joueur, passé) -> (tailles, nombre à garder) ou None
    raises: dict  # street -> tailles de relance (et de sur-relance)
    lines: dict  # lettre du flop -> actions qui mènent à la turn
    flop_river: list  # mise river unique pendant le choix du flop (arbre complet plus petit)
    names: dict  # libellés des situations principales
    flop_line: dict  # lettre du flop -> « c-bet payée »…
    main: tuple  # situations résumées dans les menus
    description: str


def _srp_river(bettor: str, past: str) -> Optional[tuple]:
    if bettor == "o" and past[-1] == "i":
        return None  # la BB ne mène pas dans l'agresseur de la street précédente (donk)
    return [50, 75, 100, 150, "a"], 2


def _3bet_river(bettor: str, past: str) -> Optional[tuple]:
    if bettor == "o":
        return None if past[-1] == "i" else ([33, 50, 75, "a"], 2)  # 3e barrel, probe, bet/check/bet…
    if past[-1] == "i":
        return [33, 50, 75, "a"], 2  # le bouton continue après sa mise payée
    return [25, 50, "a"], 1  # stab : la BB checke


PROFILES = {
    "srp": Profile(
        flop=(("bet:fi:", [33, 75, "geo"]), ("raise:fo::0", [33, 66, "geo"]), ("raise:fi::1", [33, 66, "geo"])),
        turn_bets={
            ("i", "i"): [50, 100, "geo"],  # 2e barrel
            ("i", "x"): [33, 66, "geo"],  # c-bet retardée
            ("o", "x"): [33, 75, 100, "geo"],  # probe turn
            ("o", "o"): [50, 100, "geo"],  # barrel de la BB après son check-raise payé
            ("i", "o"): [33, 66, "geo"],  # mise du bouton quand la BB checke après son check-raise payé
        },
        river_bets=_srp_river,
        raises={"t": [33, 66, "geo"], "r": [33, 66, "a"]},  # à la river, le géométrique est le tapis
        lines={"i": ["check", "bet", "call"], "x": ["check", "check"], "o": ["check", "bet", "raise", "call"]},
        flop_river=[75],
        names={
            "bet:fi:": "C-bet", "raise:fo::0": "Check-raise flop", "raise:fi::1": "Relance du BTN face au check-raise",
            "bet:ti:i": "2e barrel", "bet:ti:x": "C-bet retardée", "bet:to:x": "Probe turn",
            "bet:to:o": "Barrel de la BB après son check-raise", "bet:ti:o": "Mise du BTN après le check-raise payé",
            "bet:ri:ii": "3e barrel", "bet:ri:ix": "Bet/check/bet", "bet:ri:xi": "Check/bet/bet",
            "bet:ri:xx": "Mise river du BTN après deux streets checkées",
            "bet:ro:ix": "Probe river (c-bet payée, turn checkée)", "bet:ro:xx": "Probe river (deux streets checkées)",
            "bet:ro:xo": "Barrel de la BB après son probe turn payé",
        },
        flop_line={"i": "c-bet payée", "x": "flop checké", "o": "check-raise payé"},
        main=("bet:fi:", "raise:fo::0", "bet:ti:i", "bet:ti:x", "bet:to:x", "bet:ri:ii", "bet:ri:ix", "bet:ro:ix"),
        description="c-bet 33 / 75 % / géo, 2e barrel 50 % / pot / géo, c-bet retardée 33 / 66 % / géo, probe turn "
                    "33 / 75 % / pot / géo, river deux parmi 50 / 75 % / pot / 150 % / tapis, relances 33 / 66 % / "
                    "géo (tapis à la river)",
    ),
    # Pot 3bet : la BB a 3betté, elle est hors de position et à l'initiative ; le bouton a payé.
    "3bet": Profile(
        flop=(("bet:fo:", [33, 75, "geo2"]),  # c-bet : géométrique sur deux streets (tapis à la turn)
              ("raise:fi::0", [33, 66, "a"]),  # relance du bouton face à la c-bet
              ("bet:fi:", [25, 50, "a"]),  # stab du bouton quand la BB checke
              ("raise:fo::0", [33, 66, "a"]),  # check-raise de la BB face au stab
              ("raise:fo::1", [33, 66, "a"]),  # sur-relance de la BB
              ("raise:fi::1", [33, 66, "a"])),  # sur-relance du bouton
        turn_bets={
            ("o", "o"): [33, 50, 75, "a"],  # 2e barrel
            ("o", "x"): [33, 75, "geo"],  # c-bet retardée : les tailles de la c-bet (géo sur turn et river)
            ("i", "o"): [25, 50, "a"],  # stab turn du bouton (c-bet payée, la BB checke)
            ("i", "x"): [25, 50, "a"],  # stab turn du bouton (flop checké)
            ("i", "i"): [33, 50, 75, "a"],  # le bouton continue après son stab payé
        },
        river_bets=_3bet_river,
        raises={"t": [33, 66, "a"], "r": [33, 66, "a"]},
        lines={"o": ["bet", "call"], "x": ["check", "check"], "i": ["check", "bet", "call"]},
        flop_river=[50],
        names={
            "bet:fo:": "C-bet", "raise:fi::0": "Relance du BTN face à la c-bet",
            "raise:fo::1": "Sur-relance de la BB face à la relance", "bet:fi:": "Stab flop du BTN",
            "raise:fo::0": "Check-raise de la BB face au stab", "raise:fi::1": "Sur-relance du BTN face au check-raise",
            "bet:to:o": "2e barrel", "bet:to:x": "C-bet retardée", "bet:ti:o": "Stab turn du BTN (c-bet payée)",
            "bet:ti:x": "Stab turn du BTN (flop checké)", "bet:ti:i": "Barrel turn du BTN après son stab payé",
            "bet:ro:oo": "3e barrel", "bet:ro:ox": "Bet/check/bet de la BB", "bet:ro:xo": "Barrel river après la c-bet retardée",
            "bet:ro:xx": "Probe river de la BB (deux streets checkées)", "bet:ri:oo": "Stab river du BTN (deux barrels payés)",
            "bet:ri:ox": "Stab river du BTN (c-bet payée, turn checkée)", "bet:ri:xx": "Stab river du BTN (deux streets checkées)",
        },
        flop_line={"o": "c-bet payée", "x": "flop checké", "i": "stab payé"},
        main=("bet:fo:", "raise:fi::0", "bet:fi:", "bet:to:o", "bet:to:x", "bet:ti:o", "bet:ro:oo", "bet:ro:ox"),
        description="c-bet de la BB 33 / 75 % / géo sur deux streets, 2e barrel 33 / 50 / 75 % / tapis, c-bet retardée "
                    "33 / 75 % / géo, river (3e barrel, probe) deux parmi 33 / 50 / 75 % / tapis, stab du bouton "
                    "25 / 50 % / tapis, relances 33 / 66 % / tapis",
    ),
}


def situations(family: str = "srp") -> list[Situation]:
    """Les situations d'une famille de pots : mises et relances, street par street (le flop dans l'ordre du choix)."""
    profile = PROFILES[family]
    out = [Situation(key, sizes) for key, sizes in profile.flop]
    for street, pasts in (("t", list(profile.lines)), ("r", [a + b for a in profile.lines for b in "ixo"])):
        for past in pasts:
            for bettor in "oi":
                if street == "t":
                    bets, n = profile.turn_bets.get((bettor, past)), 1
                else:
                    bets, n = profile.river_bets(bettor, past) or (None, 1)
                if bets is None:
                    continue  # pas de donk
                other = "i" if bettor == "o" else "o"
                out.append(Situation(f"bet:{street}{bettor}:{past}", bets, n))
                out.append(Situation(f"raise:{street}{other}:{past}:0", profile.raises[street]))
                out.append(Situation(f"raise:{street}{bettor}:{past}:1", profile.raises[street]))
    return out


def initial_plan(family: str = "srp") -> dict:
    return {s.key: s.initial() for s in situations(family)}


# --- Libellés ------------------------------------------------------------------------------

def size_text(size) -> str:
    if size == "a":
        return "tapis"
    if isinstance(size, str) and size.startswith("geo"):
        return "géo" if size == "geo" else f"géo {size[3:]} streets"
    return "pot" if size == 100 else f"{size} %"


def sizes_text(sizes: list) -> str:
    return " / ".join(size_text(s) for s in sizes)


def label(key: str, family: str = "srp") -> str:
    profile = PROFILES[family]
    if key in profile.names:
        return profile.names[key]
    kind, where, past, *level = key.split(":")
    street = {"f": "flop", "t": "turn", "r": "river"}[where[0]]
    who = "BB" if where[1] == "o" else "BTN"
    line = [profile.flop_line[past[0]]] if past else []
    if len(past) > 1:
        line.append({"i": "mise du BTN payée à la turn", "x": "turn checkée", "o": "mise de la BB payée à la turn"}[past[1]])
    what = "Mise" if kind == "bet" else "Relance" if level == ["0"] else "Sur-relance"
    return f"{what} {street} {'de la' if who == 'BB' else 'du'} {who}" + (f" ({', '.join(line)})" if line else "")


def plan_text(plan: dict, family: str = "srp") -> str:
    """Les tailles des situations principales, pour les menus."""
    profile = PROFILES[family]
    def name(key: str) -> str:
        text = label(key, family)
        return text[0].lower() + text[1:]
    return " · ".join(f"{name(k)} {sizes_text(plan[k])}" for k in profile.main if k in plan)


# --- Résolutions -----------------------------------------------------------------------------

def run_lot(bases: list[dict], runs: list[dict], threads: int = 0,
            on_start: Optional[Callable[[subprocess.Popen], None]] = None) -> list[dict]:
    """Résout des variantes (analyzer-solve --lot) ; un résultat par variante, dans l'ordre."""
    exe = postflop.binary_path()
    if not exe.is_file():
        raise postflop.SolverError(postflop.status()["message"])
    with tempfile.TemporaryDirectory(prefix="analyzer-tailles-") as tmp:
        path = Path(tmp) / "lot.json"
        path.write_text(json.dumps({"threads": threads, "bases": bases, "runs": runs}), encoding="utf-8")
        proc = subprocess.Popen([str(exe), "--lot", str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                **postflop.PIPE_TEXT)
        if on_start:
            on_start(proc)
        out, err = proc.communicate()
    results = sorted((json.loads(line) for line in out.splitlines() if line.strip()), key=lambda r: r["i"])
    errors = [r["error"] for r in results if "error" in r]
    if proc.returncode != 0 or errors or len(results) != len(runs):
        raise postflop.SolverError(errors[0] if errors else (err.strip()[-300:] or "lot interrompu"))
    return results


def pick(options: list, evs: list[float], eps: float = EPS) -> int:
    """Meilleure EV ; à moins de eps de la meilleure, la plus petite option (la première)."""
    best = max(evs)
    return next(k for k, ev in enumerate(evs) if ev >= best - eps)


def subgame(spot, node: dict, card: str, past: str) -> dict:
    """Sous-jeu partant de la turn `card`, avec les ranges (présences) du nœud de chance `node`."""
    ranges = [",".join(f"{r[0]}:{r[1]}" for r in node["hands"][p] if r[1] > 0 and card not in (r[0][:2], r[0][2:]))
              for p in (0, 1)]
    tree = spot.tree()
    tree["starting_pot"], tree["effective_stack"] = node["pot"], node["stacks"][0]
    return {"spot": {"board": "".join(spot.board) + card, "range_oop": ranges[0], "range_ip": ranges[1], "tree": tree},
            "line": [], "max_iterations": 300, "target_exploit_pct": SUB_TARGET, "past": past}


def sample(cards: list[str], n: int) -> list[str]:
    """n cartes réparties dans le paquet (rangé par hauteur puis couleur)."""
    if len(cards) <= n:
        return list(cards)
    return [cards[(2 * k + 1) * len(cards) // (2 * n)] for k in range(n)]


def _mass(node: dict) -> float:
    return sum(r[1] for r in node["hands"][0]) * sum(r[1] for r in node["hands"][1])


def _path_to_turn(session, kinds: list[str]) -> Optional[list]:
    """Chemin du nœud de chance de la turn au bout de ces actions du flop, si l'arbre l'a."""
    def step(node, kind):
        k = next((i for i, a in enumerate(node["actions"]) if a["kind"] == kind and not a.get("allin")), None)
        return None if k is None else {"type": "action", "index": k}
    path: list = []
    for kind in kinds:
        node = session.node(path)
        if node["type"] != "action":
            return None
        s = step(node, kind)
        if s is None:
            return None
        path.append(s)
    return path if session.node(path)["type"] == "chance" else None


class Selection:
    """Le choix des tailles d'un flop, étape par étape (voir l'en-tête du module)."""

    def __init__(self, spot, log: Callable[[str], None] = print, threads: int = 0, cards: int = TURN_CARDS,
                 on_start: Optional[Callable[[subprocess.Popen], None]] = None,
                 stopped: Optional[Callable[[], bool]] = None):
        """on_start reçoit chaque programme lancé (pour l'arrêter) ; stopped() dit si l'arrêt est demandé."""
        self.spot, self.log, self.threads, self.cards = spot, log, threads, cards
        self.on_start = on_start
        self.stopped = stopped or (lambda: False)
        self.family = spot.family
        self.profile = PROFILES[spot.family]
        self.plan = initial_plan(spot.family)
        self.report: dict[str, dict] = {}
        self.session: Optional[postflop.Session] = None
        self.root_mass = 0.0

    def _request(self, plan: dict) -> dict:
        self.spot.plan = plan
        return self.spot.request(iterations=400, target=FLOP_TARGET, threads=self.threads)

    # --- flop : arbres complets ---
    def _flop_plan(self, plan: dict) -> dict:
        """Pendant le choix du flop, la river garde une seule mise (75 % en SRP) et pas de relance : l'arbre
        complet reste deux fois plus petit ; ses tailles se choisissent ensuite, sur les sous-jeux."""
        out = dict(plan)
        for sit in situations(self.family):
            if sit.street == 2:
                out[sit.key] = list(self.profile.flop_river) if sit.key.startswith("bet:") else []
        return out

    def _flop_situations(self) -> list[Situation]:
        return [s for s in situations(self.family) if s.street == 0]

    def _label(self, key: str) -> str:
        return label(key, self.family)

    def _check(self) -> None:
        if self.stopped():
            raise postflop.SolverError("Choix des tailles arrêté.")

    def _lot(self, bases: list[dict], runs: list[dict]) -> list[dict]:
        self._check()
        return run_lot(bases, runs, self.threads, self.on_start)

    def _solve(self, plan: dict) -> tuple[dict, postflop.Session]:
        """Arbre complet résolu, gardé en mémoire (environ 1 Go) : les ranges de la turn en viendront."""
        self._check()
        session = postflop.Session(self._request(self._flop_plan(plan)), save=False)
        try:
            return session.start(on_start=self.on_start), session
        except BaseException:
            session.close()
            raise

    def flop(self) -> None:
        known: dict[str, tuple[dict, postflop.Session]] = {}  # arbre (plan) -> résolution, sans doublon
        try:
            for sit in self._flop_situations():
                options = sit.options()
                evs, idents = [], []
                for opt in options:
                    plan = dict(self.plan, **{sit.key: list(opt)})
                    ident = json.dumps(self._flop_plan(plan), sort_keys=True)
                    if ident not in known:
                        start = time.time()
                        known[ident] = self._solve(plan)
                        raw = known[ident][0]
                        self.log(f"    {self._label(sit.key)} {sizes_text(opt)} : EV {raw['root_ev'][sit.player]:.3f} bb "
                                 f"({raw['iterations']} itérations, {raw['exploit_pct']} % du pot, "
                                 f"{time.time() - start:.0f} s)")
                    evs.append(known[ident][0]["root_ev"][sit.player])
                    idents.append(ident)
                k = pick(options, evs, EPS_FLOP_POT * self.spot.pot_bb)
                for ident in [i for i in known if i != idents[k]]:  # seul l'arbre retenu reste en mémoire
                    known.pop(ident)[1].close()
                self.plan[sit.key] = list(options[k])
                self.report[sit.key] = {"label": self._label(sit.key), "options": [list(o) for o in options],
                                        "evs": [round(e, 4) for e in evs], "chosen": list(options[k]), "reach": 1.0,
                                        "cards": None, "method": "arbre complet"}
                self.log(f"  {self._label(sit.key)} : {sizes_text(options[k])}")
        except BaseException:
            for _, session in known.values():
                session.close()
            raise
        # L'arbre retenu : les ranges de la turn de chaque ligne en viennent.
        self.session = next(iter(known.values()))[1]
        self.root_mass = _mass(self.session.node([]))

    # --- turn et river : sous-jeux ---
    def later(self, street: int) -> None:
        for line, kinds in self.profile.lines.items():
            path = _path_to_turn(self.session, kinds)
            if path is None:
                continue
            node = self.session.node(path)
            weight = _mass(node) / self.root_mass  # probabilité d'arriver à la turn par cette ligne
            cards = sample(node["cards"], self.cards if street == 1 else min(self.cards, RIVER_CARDS))
            bases = [subgame(self.spot, node, card, line) for card in cards]
            sits = [s for s in situations(self.family) if s.street == street and s.past[0] == line]
            # Pour la turn, la river reste simple (comme au flop) ; elle se choisit ensuite.
            view = self._flop_plan if street == 1 else dict
            # Fréquence de chaque situation dans le sous-jeu, avec le plan courant.
            stats = self._lot(bases, [{"base": k, "plan": view(self.plan)} for k in range(len(bases))])
            reach = {s.key: sum(r["stats"].get(s.key, {}).get("reach", 0.0) for r in stats) / len(stats) for s in sits}
            usage = self._river_usage(bases, sits, reach, weight) if street == 2 else {}
            for sit in sorted(sits, key=lambda s: -reach[s.key]):
                self._compare(sit, bases, reach[sit.key], weight, node["pot"], view, usage.get(sit.key))

    def _river_usage(self, bases: list[dict], sits: list[Situation], reach: dict, weight: float) -> dict:
        """River : chaque mise propose toutes ses tailles à la fois ; leur fréquence d'emploi retient les trois
        plus utilisées, dont on compare ensuite les paires (trois au lieu de dix sur cinq tailles)."""
        bets = [s for s in sits if s.choose == 2 and len(s.candidates) > 3 and weight * reach[s.key] >= RARE]
        if not bets:
            return {}
        plan = dict(self.plan, **{s.key: list(s.candidates) for s in bets})
        n = min(RARE_CARDS * 2, len(bases))
        used = bases[:: max(1, len(bases) // n)][:n]
        results = self._lot(used, [{"base": b, "plan": plan} for b in range(len(used))])
        out = {}
        for sit in bets:
            n = len(sit.candidates)
            share = [sum(r["stats"].get(sit.key, {}).get("usage", [0.0] * n)[j] for r in results) for j in range(n)]
            out[sit.key] = [round(x / len(results), 6) for x in share]
        return out

    def _compare(self, sit: Situation, bases: list[dict], reach: float, weight: float, pot: float,
                 view: Callable[[dict], dict], usage: Optional[list] = None) -> None:
        """Choisit la taille d'une situation. reach : sa fréquence dans le sous-jeu ; weight : la probabilité
        d'atteindre ce sous-jeu depuis le flop."""
        options = sit.options()
        if usage is not None:  # river : paires parmi les trois tailles les plus employées
            top = sorted(sorted(range(len(sit.candidates)), key=lambda j: -usage[j])[:3])
            options = list(itertools.combinations([sit.candidates[j] for j in top], 2))
        overall = weight * reach  # probabilité d'atteindre la situation depuis le flop
        entry = {"label": self._label(sit.key), "options": [list(o) for o in options], "reach": round(overall, 5)}
        if usage is not None:
            entry["usage"] = usage
        if overall < RARE:
            k, entry["evs"], entry["cards"], entry["method"] = 0, None, 0, "rare"
        else:
            n = len(bases) if overall >= COMMON else min(RARE_CARDS, len(bases))
            used = bases[:: max(1, len(bases) // n)][:n]
            runs = [{"base": b, "plan": view(dict(self.plan, **{sit.key: list(opt)}))}
                    for opt in options for b in range(len(used))]
            results = self._lot(used, runs)
            evs = [sum(r["root_ev"][sit.player] for r in results[j * len(used):(j + 1) * len(used)]) / len(used)
                   for j in range(len(options))]
            # L'écart se juge quand la situation arrive : à la racine du sous-jeu, il est dilué par sa fréquence.
            eps = max(EPS_FLOOR, EPS_POT * pot * reach)
            k = pick(options, evs, eps)
            entry.update(evs=[round(e, 4) for e in evs], cards=len(used), method="sous-jeux",
                         per_occurrence=[round((e - evs[k]) / reach, 3) if reach > 0 else None for e in evs])
        self.plan[sit.key] = list(options[k])
        entry["chosen"] = list(options[k])
        self.report[sit.key] = entry
        if entry["method"] != "rare":
            gaps = ", ".join(f"{sizes_text(o)} {g:+.2f}" for o, g in zip(options, entry["per_occurrence"]) if o != options[k])
            self.log(f"  {self._label(sit.key)} : {sizes_text(options[k])} (atteinte {100 * overall:.1f} %, "
                     f"{entry['cards']} cartes ; écart par occurrence : {gaps} bb)")

    def run(self, resume: Optional[dict] = None) -> dict:
        """resume : un choix précédent dont on garde le flop (seule la turn et la river se refont)."""
        start = time.time()
        try:
            if resume:
                self.log("  Flop : choix repris…")
                self.resume_flop(resume)
            else:
                self.log("  Flop (arbres complets)…")
                self.flop()
            self.log("  Turn (sous-jeux)…")
            self.later(1)
            self.log("  River (sous-jeux)…")
            self.later(2)
        finally:
            if self.session is not None:
                self.session.close()
        return {"plan": self.plan, "report": self.report, "seconds": round(time.time() - start),
                "cards": self.cards, "created": time.strftime("%d/%m/%Y %H:%M"), "method": METHOD}

    def resume_flop(self, previous: dict) -> None:
        for sit in self._flop_situations():
            self.plan[sit.key] = list(previous["plan"][sit.key])
            self.report[sit.key] = previous["report"][sit.key]
        _, self.session = self._solve(self.plan)
        self.root_mass = _mass(self.session.node([]))
