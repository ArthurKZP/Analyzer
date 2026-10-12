"""Le plan de jeu contre le field : le plan suggéré (theory/coach.py) avec quelques ajustements légers pour exploiter
les leaks d'une population de l'Étude du field (les réguliers, les récréatifs, un style de récréatifs) à un niveau
de limites. Chaque leak qui touche une situation du plan (la c-bet, la défense, les barrels, la river…) donne un
ajustement parti du chiffre du plan : « le plan c-bet 58 % de la range ; contre eux, vers 68 % ».

Un plan a deux côtés : celui qui a l'initiative (l'attaque) et l'autre (la défense). En attaque comptent les leaks
des défenseurs (ils foldent trop face à la c-bet, ils paient trop la river…) ; en défense, ceux des attaquants (ils
c-bettent trop, ils abandonnent à la turn…). Les leaks d'avant le flop restent dans l'Étude du field.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from . import field
from .insights import Finding

# Stat d'une étude du field (tables à plusieurs : field.RING_DEFS ; heads-up : insights) -> la situation du plan.
CONCEPTS = {
    "fold_cbet": "fold_cbet", "vs_cbet_flop.fold": "fold_cbet",
    "xr_flop": "raise_cbet", "vs_cbet_flop.raise": "raise_cbet",
    "fold_turn": "fold_turn", "vs_cbet_turn.fold": "fold_turn", "vs_bet_turn.fold": "fold_turn",
    "fold_river": "fold_river", "vs_cbet_river.fold": "fold_river", "vs_bet_river.fold": "fold_river",
    "cbet_hu": "cbet", "cbet_flop_ip": "cbet", "cbet_flop_oop": "cbet",
    "cbet_turn": "barrel", "cbet_river": "barrel3",
    "stab_flop": "stab", "float_flop": "stab", "probe_turn": "probe",
    "wtsd": "wtsd", "afq": "afq",
}
HU_SEATS = {"cbet_flop_ip": True, "cbet_flop_oop": False}  # c-bet en position ou non : selon qui a l'initiative


@dataclass(frozen=True)
class Tweak:
    side: str             # « attack » (tu as l'initiative) ou « defense »
    title: str
    text: str             # {plan} : le chiffre du plan ; {target} : celui visé contre eux
    base: Optional[str]   # le chiffre du plan (coach.baseline), ou None : un conseil sans chiffre
    shift: float = 0.0    # l'écart visé, en part de la range


# (situation, sens de l'écart) -> l'ajustement
TWEAKS = {
    ("fold_cbet", "haut"): Tweak(
        "attack", "C-bet un peu plus souvent",
        "Le plan c-bet {plan} de la range ; contre eux, vers {target} : ajoute des c-bets avec des mains « rien » que "
        "le plan checke (hauteur As ou Roi, backdoors), en petite taille.", "cbet", 0.10),
    ("fold_cbet", "bas"): Tweak(
        "attack", "C-bet moins en bluff",
        "Le plan c-bet {plan} de la range ; contre eux, vers {target} : checke une partie de tes mains « rien », et "
        "mise un peu plus tes mains moyennes : ils paient avec plus faible.", "cbet", -0.10),
    ("raise_cbet", "haut"): Tweak(
        "attack", "Défends plus face à leurs check-raises",
        "Face au check-raise, le plan folde {plan} ; contre eux, vers {target} : paie avec tes paires et tes tirages, "
        "et c-bet un peu plus petit sur les flops où tu mises beaucoup.", "fold_xr", -0.10),
    ("raise_cbet", "bas"): Tweak(
        "attack", "Respecte leurs check-raises",
        "Face au check-raise, le plan folde {plan} ; contre eux, vers {target} : leurs check-raises sont forts, folde "
        "tes mains moyennes ; et c-bet plus large, ils ne te punissent pas.", "fold_xr", 0.10),
    ("fold_turn", "haut"): Tweak(
        "attack", "2e barrel un peu plus souvent",
        "À la turn, le plan mise encore {plan} après une c-bet payée ; contre eux, vers {target} : ajoute des barrels "
        "avec tes tirages et tes overcards, surtout quand la carte change le board.", "barrel", 0.10),
    ("fold_turn", "bas"): Tweak(
        "attack", "2e barrel surtout pour la valeur",
        "À la turn, le plan mise encore {plan} après une c-bet payée ; contre eux, vers {target} : moins de barrels "
        "sans main, garde tes mises pour la valeur et les gros tirages.", "barrel", -0.10),
    ("fold_river", "haut"): Tweak(
        "attack", "Bluffe un peu plus la river",
        "À la river, le plan bluffe {plan} de ses mains « rien » ; contre eux, vers {target}, avec les mains qui "
        "bloquent leurs paiements.", "river_bluff", 0.10),
    ("fold_river", "bas"): Tweak(
        "attack", "Bluffe moins la river, value-bet plus fin",
        "À la river, le plan bluffe {plan} de ses mains « rien » ; contre eux, vers {target} : ils paient trop ; "
        "mise pour la valeur avec tes mains moyennes.", "river_bluff", -0.15),
    ("wtsd", "haut"): Tweak(
        "attack", "Value-bet plus large",
        "Ils vont souvent à l'abattage : à la turn et à la river, mise pour la valeur un peu plus large que le plan, "
        "et bluffe un peu moins.", None),
    ("wtsd", "bas"): Tweak(
        "attack", "Mets-les à l'épreuve",
        "Ils abandonnent souvent avant l'abattage : un peu plus de barrels que le plan à la turn et à la river, avec "
        "tes tirages et tes overcards.", None),
    ("stab", "haut"): Tweak(
        "attack", "Garde des mains fortes dans tes checks",
        "Quand tu checkes, ils misent souvent : checke au flop quelques mains fortes de plus que le plan, pour payer "
        "ou relancer leurs mises.", None),
    ("stab", "bas"): Tweak(
        "attack", "Checke sans crainte",
        "Quand tu checkes, ils misent rarement : checke tes mains moyennes, tu verras la carte suivante gratuitement ; "
        "leurs mises après ton check sont fortes.", None),
    ("probe", "haut"): Tweak(
        "attack", "Piège leurs probes",
        "Après un flop checké, ils mènent souvent à la turn : checke derrière au flop quelques mains fortes de plus, "
        "pour payer ou relancer.", None),
    ("probe", "bas"): Tweak(
        "attack", "Crois leurs probes",
        "Après un flop checké, ils mènent rarement à la turn : quand ils le font, ils ont une main ; folde tes mains "
        "faibles.", None),
    ("cbet", "haut"): Tweak(
        "defense", "Défends un peu plus face à la c-bet",
        "Face à la c-bet, le plan folde {plan} ; contre eux, vers {target} : paie avec tes hauteurs As et tes "
        "backdoors, et relance un peu plus tes tirages.", "fold_cbet", -0.10),
    ("cbet", "bas"): Tweak(
        "defense", "Respecte leurs c-bets, attaque leurs checks",
        "Face à la c-bet, le plan folde {plan} ; contre eux, vers {target} : leurs c-bets sont fortes. Quand ils "
        "checkent, mise plus souvent : ils ont abandonné.", "fold_cbet", 0.08),
    ("barrel", "haut"): Tweak(
        "defense", "Paie un peu plus la turn",
        "Face au 2e barrel, le plan folde {plan} ; contre eux, vers {target} : paie avec tes bluff-catchers (paires "
        "moyennes, top pair petit kicker).", "fold_turn", -0.10),
    ("barrel", "bas"): Tweak(
        "defense", "Folde un peu plus face au 2e barrel",
        "Face au 2e barrel, le plan folde {plan} ; contre eux, vers {target} : leurs mises turn sont fortes. Paie un "
        "peu plus la c-bet pour prendre la turn quand ils checkent.", "fold_turn", 0.08),
    ("barrel3", "haut"): Tweak(
        "defense", "Paie un peu plus la river",
        "Face au 3e barrel, le plan folde {plan} ; contre eux, vers {target} : ils bluffent trop la river.",
        "fold_river", -0.10),
    ("barrel3", "bas"): Tweak(
        "defense", "Folde un peu plus à la river",
        "Face au 3e barrel, le plan folde {plan} ; contre eux, vers {target} : leurs mises river sont pour la valeur.",
        "fold_river", 0.08),
    ("afq", "haut"): Tweak(
        "defense", "Laisse-les bluffer",
        "Très agressifs après le flop. Face au 3e barrel, le plan folde {plan} ; contre eux, vers {target} : paie plus "
        "large avec tes bluff-catchers, et checke tes mains fortes pour les laisser miser.", "fold_river", -0.08),
    ("afq", "bas"): Tweak(
        "defense", "Crois leurs mises",
        "Passifs après le flop : quand ils misent, ils ont une main. Face au 3e barrel, le plan folde {plan} ; contre "
        "eux, vers {target}.", "fold_river", 0.08),
}


def _pct(x: float) -> str:
    return f"{round(100 * x)} %"


def _relevant(finding: Finding, heads_up: bool, aggressor_ip: bool) -> Optional[str]:
    """La situation du plan que touche ce leak, ou None."""
    key = finding.stat.key
    if heads_up and key in HU_SEATS and HU_SEATS[key] != aggressor_ip:
        return None  # la c-bet de l'autre configuration (en position ou non)
    return CONCEPTS.get(key)


def adjustments(base: dict, study: field.Study, aggressor_ip: bool = True) -> dict[str, list[dict]]:
    """Les ajustements du plan (base : coach.baseline) contre cette population : {"attack": […], "defense": […]},
    chacun avec son titre, son texte, la preuve (le leak et ses chiffres) et sa solidité ; les leaks solides d'abord.
    aggressor_ip : celui qui a l'initiative est en position (en heads-up, pour lire la bonne c-bet)."""
    found: dict[tuple[str, str], Finding] = {}
    for f in study.leaks:
        concept = _relevant(f, not study.ring_game, aggressor_ip)
        if concept is None or (concept, f.direction) not in TWEAKS:
            continue
        best = found.get((concept, f.direction))
        if best is None or (f.strong, f.score) > (best.strong, best.score):
            found[(concept, f.direction)] = f
    out: dict[str, list[dict]] = {"attack": [], "defense": []}
    for (concept, direction), f in sorted(found.items(), key=lambda kv: (not kv[1].strong, -kv[1].score)):
        tweak = TWEAKS[(concept, direction)]
        plan = base.get(tweak.base) if tweak.base else None
        if tweak.base and plan is None:
            continue  # le plan n'a pas ce chiffre (pas de flop résolu sur cette ligne)
        text = tweak.text
        if plan is not None:
            target = min(max(plan + tweak.shift, 0.0), 1.0)
            if round(100 * target) == round(100 * plan):
                continue  # le plan est déjà au bout (il ne folde jamais, il mise tout…) : rien à ajuster
            text = text.format(plan=_pct(plan), target=_pct(target))
        fact, numbers, _ = field.finding_text(f)
        out[tweak.side].append({"concept": concept, "title": tweak.title, "text": text, "fact": fact,
                                "numbers": numbers, "strong": f.strong})
    return out
