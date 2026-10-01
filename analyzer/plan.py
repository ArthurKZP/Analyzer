"""Plan de jeu simple contre un adversaire, déduit de l'analyse.

Chaque consigne est produite par une règle générique (valable pour n'importe quel
adversaire) qui ne se déclenche que si les données la justifient. Elle est
accompagnée de sa preuve chiffrée et d'un niveau de confiance :

- solide : l'écart reste vrai même en tenant compte du hasard (intervalle à 90 %) ;
- indicatif : la tendance est nette mais l'échantillon reste modeste ;
- à confirmer : peu de mains, à vérifier sur les prochaines sessions.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from statistics import median
from typing import Optional

from .insights import MIN_SAMPLE, bluff_break_even, size_bucket, wilson
from .lines import Line, check_ranges, pooled_by_size, verdict, villain_lines
from .theory.preflop import NodeSummary, decisions, gto_ref, gto_value, summarize
from .models import BET, CALL, FOLD, POSTFLOP, RAISE, Hand
from .stats import PlayerStats, Ratio

PHASES = ("Préflop", "Quand tu mises", "Face à ses mises", "À tester")
MAX_PER_PHASE = 4
SIZE_WORDS = {
    "petite (≤ 55 %)": "petites mises",
    "grosse (56–120 %)": "grosses mises",
    "overbet (> 120 %)": "overbets",
    "": "relances",
}
CONFIDENCE_RANK = {"solide": 0, "indicatif": 1, "à confirmer": 2}


@dataclass
class PlanItem:
    phase: str
    action: str  # ce qu'il faut faire
    why: str  # la preuve chiffrée
    confidence: str  # "solide", "indicatif", "à confirmer"
    weight: float = 0.0  # importance, pour trier dans une même phase


@dataclass
class Plan:
    profile: str
    items: list = field(default_factory=list)

    def by_phase(self) -> dict[str, list[PlanItem]]:
        grouped: dict[str, list[PlanItem]] = {phase: [] for phase in PHASES}
        for item in self.items:
            grouped[item.phase].append(item)
        for phase, items in grouped.items():
            items.sort(key=lambda it: (CONFIDENCE_RANK[it.confidence], -it.weight))
            grouped[phase] = items[:MAX_PER_PHASE]
        return grouped


def _pct(r: Ratio) -> str:
    return f"{r.pct:.0f} % ({r.hits}/{r.opps})"


def _confidence(r: Ratio, limit: float, above: bool) -> str:
    """Solide si l'intervalle de confiance entier est du bon côté de la limite."""
    lo, hi = wilson(r)
    if (above and lo > limit) or (not above and hi < limit):
        return "solide"
    return "indicatif" if r.opps >= MIN_SAMPLE else "à confirmer"


def _line_confidence(n: int) -> str:
    return "solide" if n >= 8 else "indicatif" if n >= 4 else "à confirmer"


def _share_confidence(hits: int, n: int, limit: float = 50.0) -> str:
    """Solide si la part observée dépasse la limite même au bas de l'intervalle de confiance."""
    lo, _ = wilson(Ratio(hits, n))
    return "solide" if lo > limit else "indicatif" if n >= 4 else "à confirmer"


def _priority(level: int, n: int) -> float:
    return level * 1000 + n


# --- Profil -------------------------------------------------------------------------

def _below(r: Ratio, node: str, action: str) -> bool:
    return r.opps >= MIN_SAMPLE and r.pct < gto_ref(node, action)[0]


def _above(r: Ratio, node: str, action: str) -> bool:
    return r.opps >= MIN_SAMPLE and r.pct > gto_ref(node, action)[1]


def profile(v: PlayerStats) -> str:
    tags = []
    if _below(v.r("bb_vs_open.fold"), "bb_vs_open", "fold"):
        tags.append("défend presque toutes ses BB")
    if _above(v.r("bb_vs_open.raise"), "bb_vs_open", "raise"):
        tags.append("3bet beaucoup")
    sticky = [k for k in ("vs_cbet_flop.fold", "vs_cbet_turn.fold") if v.r(k).opps >= MIN_SAMPLE and v.pct(k) < 35]
    if sticky or _below(v.r("sb_vs_3bet.fold"), "sb_vs_3bet", "fold"):
        tags.append("collant (lâche peu face aux mises)")
    _, afq = v.aggression()
    if (afq is not None and afq > 55) or (v.r("xr_flop").opps >= MIN_SAMPLE and v.pct("xr_flop") > 18):
        tags.append("agressif postflop")
    elif afq is not None and afq < 40:
        tags.append("passif postflop")
    numbers = []
    for key, text in (("sb_first.raise", "ouvre {} au bouton"), ("bb_vs_open.fold", "folde {} de ses BB"),
                      ("bb_vs_open.raise", "3bet {}"), ("vs_cbet_flop.fold", "folde {} aux c-bets")):
        r = v.r(key)
        if r.opps:
            numbers.append(text.format(f"{r.pct:.0f} %"))
    head = ", ".join(tags) if tags else "pas d'écart marqué"
    return f"{head[0].upper()}{head[1:]} — " + ", ".join(numbers) + "."


# --- Préflop ------------------------------------------------------------------------

# Consigne associée à un écart au solveur : (nœud, action jouée, action du solveur).
DEVIATION_PHRASES = {
    ("sb_open", "raise", "fold"): "Au bouton, jette ces mains que le solveur n'ouvre jamais",
    ("sb_open", "fold", "raise"): "Au bouton, ouvre aussi ces mains",
    ("sb_open", "call", "raise"): "Au bouton, ne limpe pas ces mains : ouvre-les",
    ("sb_open", "call", "fold"): "Au bouton, ne limpe pas ces mains : jette-les",
    ("bb_vs_open", "raise", "fold"): "En BB, arrête ces 3bets bluff : le solveur jette ces mains",
    ("bb_vs_open", "raise", "call"): "En BB, paye plutôt que 3bet avec ces mains",
    ("bb_vs_open", "fold", "call"): "En BB, défends ces mains au lieu de les jeter",
    ("bb_vs_open", "fold", "raise"): "En BB, 3bet ces mains au lieu de les jeter",
    ("bb_vs_open", "call", "fold"): "En BB, jette ces mains au lieu de payer",
    ("bb_vs_open", "call", "raise"): "En BB, 3bet ces mains au lieu de payer",
    ("sb_vs_3bet", "fold", "call"): "Face au 3bet, continue avec ces mains",
    ("sb_vs_3bet", "fold", "raise"): "Face au 3bet, 4bet ces mains au lieu de les jeter",
    ("sb_vs_3bet", "call", "fold"): "Face au 3bet, jette ces mains",
    ("bb_vs_4bet", "fold", "call"): "Face au 4bet, continue avec ces mains",
    ("bb_vs_4bet", "call", "fold"): "Face au 4bet, jette ces mains",
}


def _exploit_allows(key: tuple[str, str, str], v: PlayerStats) -> bool:
    """Un écart au solveur peut être un bon exploit : on ne le reproche pas dans ce cas."""
    node, action, best = key
    if node == "bb_vs_open" and action == "raise" and best == "call":
        return _below(v.r("sb_vs_3bet.fold"), "sb_vs_3bet", "fold")  # il paie trop : 3bet value plus large
    if node == "bb_vs_open" and action == "raise" and best == "fold":
        return _above(v.r("sb_vs_3bet.fold"), "sb_vs_3bet", "fold")  # il lâche trop : 3bet light
    if node == "sb_open" and action == "raise" and best == "fold":
        return _above(v.r("bb_vs_open.fold"), "bb_vs_open", "fold")  # il abandonne sa BB : open large
    return False


def _theory_items(v: PlayerStats, summaries: list[NodeSummary]) -> list[PlanItem]:
    items = []
    for s in summaries:
        for (action, best), group in s.deviations():
            key = (s.node.key, action, best)
            if len(group) < 4 or _exploit_allows(key, v):
                continue
            combos = ", ".join(sorted({d.combo for d in group}, key=lambda c: (len(c) != 2, c))[:10])
            phrase = DEVIATION_PHRASES.get(key, f"{s.node.label} : {s.node.word(best)} plutôt que "
                                                f"{s.node.word(action)} avec ces mains")
            share = 100 * sum(d.strategy.get(best, 0.0) for d in group) / len(group)
            never = "jamais" if all(not d.frequency for d in group) else "presque jamais"
            why = f"{len(group)} fois {s.node.word(action)} ; le solveur {s.node.word(best)} " + (
                "toujours ces mains." if share >= 99.5 else
                f"ces mains {share:.0f} % du temps et ne les {s.node.word(action)} {never}.")
            if key == ("sb_open", "raise", "fold") and _below(v.r("bb_vs_open.fold"), "bb_vs_open", "fold"):
                why += f" Lui ne folde que {_pct(v.r('bb_vs_open.fold'))} de ses BB : ces opens ne volent rien."
            items.append(PlanItem("Préflop", f"{phrase} : {combos}.", why,
                                  "solide" if len(group) >= 8 else "indicatif",
                                  weight=_priority(5 if len(group) >= 8 else 4, len(group))))
    return items


def _defense_vs_3bet(v: PlayerStats, summaries: list[NodeSummary]) -> list[PlanItem]:
    """Ta défense contre ses 3bets, comparée à ce que ferait le solveur avec les mêmes mains."""
    s = next((x for x in summaries if x.node.key == "sb_vs_3bet"), None)
    if s is None or len(s.in_range) < MIN_SAMPLE:
        return []
    n = len(s.in_range)
    act, exp = s.actual.get("fold", 0.0), s.expected.get("fold", 0.0)
    folds = round(act * n / 100)
    v3 = v.r("bb_vs_open.raise")
    context = f" Lui 3bet {_pct(v3)} (solveur {gto_value('bb_vs_open', 'raise'):.0f} %)." if v3.opps else ""
    if act - exp >= 5:
        return [PlanItem(
            "Préflop", "Face à ses 3bets, défends plus : paye en position les mains jouables que tu jettes.",
            f"Avec les mêmes mains, le solveur folde {exp:.0f} % ; toi {act:.0f} % ({folds}/{n}).{context}",
            _confidence(Ratio(folds, n), exp, above=True), weight=_priority(5, n))]
    return []


def _preflop(v: PlayerStats, h: PlayerStats, summaries: list[NodeSummary]) -> list[PlanItem]:
    items = _theory_items(v, summaries) + _defense_vs_3bet(v, summaries)
    v4 = v.r("bb_vs_4bet.fold")
    if v4.opps >= 3 and v4.pct < gto_ref("bb_vs_4bet", "fold")[0]:
        items.append(PlanItem(
            "Préflop", "Tes 4bets : uniquement pour la value, il ne lâche pas.",
            (f"Il n'a jamais foldé face à tes 4bets (0/{v4.opps})." if v4.hits == 0
             else f"Il n'a foldé que {v4.hits}/{v4.opps} fois face à tes 4bets.")
            + f" Le solveur folde {gto_value('bb_vs_4bet', 'fold'):.0f} %.",
            _line_confidence(v4.opps), weight=_priority(2, v4.opps)))
    vf3 = v.r("sb_vs_3bet.fold")
    lo, hi = gto_ref("sb_vs_3bet", "fold")
    if _below(vf3, "sb_vs_3bet", "fold"):
        calls = Counter(e.combo for e in v.showdowns if e.position == "BTN" and e.preflop_line == "open → call 3bet")
        examples = ", ".join(c for c, _ in calls.most_common(5))
        items.append(PlanItem(
            "Préflop",
            "En BB, 3bet pour la value avec un range large et linéaire (As, broadways, paires), presque sans bluff : "
            "il paie trop.",
            f"Il ne folde que {_pct(vf3)} face à tes 3bets (solveur {gto_value('sb_vs_3bet', 'fold'):.0f} %)"
            + (f" et a payé avec {examples}." if examples else "."),
            _confidence(vf3, lo, above=False), weight=_priority(4, vf3.opps)))
    elif _above(vf3, "sb_vs_3bet", "fold"):
        items.append(PlanItem(
            "Préflop", "En BB, 3bet light : il lâche trop souvent face aux 3bets.",
            f"Il folde {_pct(vf3)} face à tes 3bets (solveur {gto_value('sb_vs_3bet', 'fold'):.0f} %).",
            _confidence(vf3, hi, above=True), weight=_priority(4, vf3.opps)))
    vfo = v.r("bb_vs_open.fold")
    lo, hi = gto_ref("bb_vs_open", "fold")
    opens_flagged = any(it.action.startswith(DEVIATION_PHRASES[("sb_open", "raise", "fold")]) for it in items)
    if _below(vfo, "bb_vs_open", "fold") and not opens_flagged:
        items.append(PlanItem(
            "Préflop",
            "Au bouton, n'ouvre pas pour voler : resserre le bas de ton range d'ouverture et joue les mains "
            "qui gagnent de la value après le flop.",
            f"Il ne folde que {_pct(vfo)} de ses BB (solveur {gto_value('bb_vs_open', 'fold'):.0f} %).",
            _confidence(vfo, lo, above=False), weight=_priority(3, vfo.opps)))
    elif _above(vfo, "bb_vs_open", "fold"):
        items.append(PlanItem(
            "Préflop", "Ouvre très large et petit au bouton : il abandonne trop sa BB.",
            f"Il folde {_pct(vfo)} de ses BB (solveur {gto_value('bb_vs_open', 'fold'):.0f} %).",
            _confidence(vfo, hi, above=True), weight=_priority(3, vfo.opps)))
    vo, hdo = v.r("sb_first.raise"), h.r("bb_vs_open.fold")
    if _above(vo, "sb_open", "raise") and _above(hdo, "bb_vs_open", "fold"):
        items.append(PlanItem(
            "Préflop", "En BB, défends plus large : il ouvre presque tout.",
            f"Il ouvre {_pct(vo)} ; tu folds {_pct(hdo)} de tes BB (solveur {gto_value('bb_vs_open', 'fold'):.0f} %).",
            _confidence(hdo, gto_ref("bb_vs_open", "fold")[1], above=True), weight=_priority(3, hdo.opps)))
    return items


# --- Quand tu mises -----------------------------------------------------------------

def bluff_results(hands: list[Hand], bettor: str, responder: str) -> dict:
    """Pour chaque mise de `bettor`, ce qu'aurait rapporté un bluff pur (en fraction du pot).

    Un bluff pur gagne le pot si l'adversaire folde et perd la mise sinon.
    Renvoie {street: {"all": stats, tranche: stats}} avec stats = n, folds, résultat moyen, tailles.
    """
    out: dict = {s: defaultdict(lambda: {"n": 0, "folds": 0, "result": 0.0, "sizes": []}) for s in POSTFLOP}
    for hd in hands:
        acts = [a for a in hd.actions if a.street != "preflop" and a.kind in (BET, RAISE, CALL, FOLD)]
        for i, a in enumerate(acts):
            if a.player != bettor or a.kind != BET or not a.pot_before:
                continue
            reply = next((b for b in acts[i + 1:] if b.player == responder and b.street == a.street), None)
            if reply is None:
                continue
            size = a.amount / a.pot_before
            for key in ("all", size_bucket(100 * size)):
                cell = out[a.street][key]
                cell["n"] += 1
                cell["folds"] += reply.kind == FOLD
                cell["result"] += 1.0 if reply.kind == FOLD else -size
                cell["sizes"].append(100 * size)
    return out


def _sizing_advice(street: str, buckets: list) -> list[PlanItem]:
    """Quand le bilan global est neutre, signale les tailles où tes bluffs gagnent ou perdent nettement."""
    scored = [(k, c, c["result"] / c["n"]) for k, c in buckets]
    worst = [s for s in scored if s[2] < -0.15]
    best = [s for s in scored if s[2] > 0.1]
    if not worst:
        return []
    k, c, _ = min(worst, key=lambda s: s[2])
    fold = Ratio(c["folds"], c["n"])
    action = f"{street.capitalize()} : ne bluffe pas avec des mises {k}"
    why = f"Il y folde {_pct(fold)}, un bluff pur y perd {-100 * c['result'] / c['n']:.0f} % du pot en moyenne."
    if best:
        bk, bc, _ = max(best, key=lambda s: s[2])
        action += f" ; si tu bluffes, préfère {bk}"
        why += f" Avec {bk}, il folde {100 * bc['folds'] / bc['n']:.0f} % ({bc['folds']}/{bc['n']})."
    threshold = bluff_break_even(median(c["sizes"]))
    return [PlanItem("Quand tu mises", action + ".", why, _confidence(fold, threshold, above=False),
                     weight=_priority(4, c["n"]))]


def _betting(hands: list[Hand], hero: str, villain: str, lines: list[Line]) -> list[PlanItem]:
    items = []
    results = bluff_results(hands, hero, villain)
    for street in POSTFLOP:
        cell = results[street].get("all")
        if not cell or cell["n"] < 12:
            continue
        avg = 100 * cell["result"] / cell["n"]
        fold = Ratio(cell["folds"], cell["n"])
        threshold = bluff_break_even(median(cell["sizes"]))
        buckets = [(k, c) for k, c in results[street].items() if k != "all" and c["n"] >= 8]
        why = (f"Il folde {_pct(fold)} face à tes mises {street} ; un bluff pur y "
               f"{'gagne' if avg >= 0 else 'perd'} {abs(avg):.0f} % du pot en moyenne.")
        if avg < -10:
            best = [(k, c) for k, c in buckets if c["result"] / c["n"] > 0.1]
            if best:
                k, c = max(best, key=lambda kc: kc[1]["result"] / kc[1]["n"])
                why += f" Exception : tes mises {k}, il y folde {100 * c['folds'] / c['n']:.0f} %."
            items.append(PlanItem(
                "Quand tu mises",
                f"{street.capitalize()} : ne bluffe pas, mise pour la value et avec de gros tirages.",
                why, _confidence(fold, threshold, above=False), weight=_priority(5, cell["n"])))
        elif avg > 10:
            items.append(PlanItem(
                "Quand tu mises", f"{street.capitalize()} : bluffe davantage, il lâche trop.",
                why, _confidence(fold, threshold, above=True), weight=_priority(5, cell["n"])))
        else:
            items += _sizing_advice(street, [(k, c) for k, c in buckets if c["n"] >= 12])
    for ln in pooled_by_size(lines):
        if ln.size or ln.street != "flop" or len(ln.seen) < 4:
            continue
        c = ln.intents
        if (c["value"] + c["thin"]) / len(ln.seen) >= 0.6:
            items.append(PlanItem(
                "Quand tu mises",
                "Quand il relance ta mise au flop, continue seulement avec top paire ou mieux, ou un gros tirage.",
                f"Ses relances flop montrées : {c['value'] + c['thin']}/{len(ln.seen)} mains faites.",
                _share_confidence(c["value"] + c["thin"], len(ln.seen)), weight=_priority(3, ln.count)))
    return items


# --- Face à ses mises ---------------------------------------------------------------

def _facing(hands: list[Hand], hero: str, villain: str, lines: list[Line]) -> list[PlanItem]:
    items = []
    for ln in pooled_by_size(lines):
        n = len(ln.seen)
        if n < 4:
            continue
        v = verdict(ln)
        c = ln.intents
        subject = f"Ses {SIZE_WORDS[ln.size]} {ln.street}"
        made = c["value"] + c["thin"]
        called = [s for s in ln.seen if s.hero_reply == CALL]
        required = 100 * median(ln.required) if ln.required else 0.0
        level = 5 if ln.street == "river" else 4
        if not ln.size and ln.street == "flop":
            continue  # déjà couvert dans « Quand tu mises »
        if v.kind == "value" and not ln.size:
            action = f"{subject} : de la value. Sans top paire ou mieux, lâche."
            why = f"{made}/{n} mains faites à l'abattage."
            conf = _share_confidence(made, n)
        elif v.kind == "value":
            won = sum(s.equity < 0.5 for s in called)
            if ln.street == "river" and c["thin"] > c["value"] and called and 100 * won / len(called) >= required:
                action = f"{subject} : paye avec tes paires, il y met surtout des paires faibles."
                why = (f"{c['thin']}/{n} paires faibles, {c['bluff']} bluff(s) ; tu as gagné {won}/{len(called)} fois "
                       f"en payant (il suffit de {required:.0f} %).")
                conf = _share_confidence(won, len(called), required)
            else:
                action = f"{subject} : surtout de la value. Folde tes mains faibles, paye avec top paire ou mieux."
                why = f"{made}/{n} mains faites à l'abattage."
                conf = _share_confidence(made, n)
        elif v.kind == "bluff":
            action = f"{subject} : souvent du bluff. Paye plus large avec tes paires."
            why = f"{c['bluff'] + c['semi']}/{n} sans main faite à l'abattage."
            conf = _share_confidence(c["bluff"] + c["semi"], n)
            folds = sum(ln.fold_holdings.values())
            if folds and ln.fold_holdings["rien"] >= 0.6 * folds:
                action += " Quand tu lâches, tu n'as souvent rien : garde des mains moyennes dans tes checks pour pouvoir payer."
                why += f" {ln.fold_holdings['rien']}/{folds} de tes folds étaient sans paire ni tirage."
        elif v.kind == "semi-bluff":
            eq = f" Quand tu as payé, ton équité moyenne était de {100 * sum(1 - s.equity for s in called) / len(called):.0f} %." \
                if called else ""
            action = (f"{subject} : surtout de gros tirages. Paye avec tes paires correctes, relance tes mains fortes, "
                      "et respecte la river si le tirage rentre.")
            why = f"{c['semi']}/{n} gros tirages, {made} mains faites.{eq}"
            conf = _share_confidence(c["semi"], n)
        else:
            continue
        items.append(PlanItem("Face à ses mises", action, why, conf, weight=_priority(level, ln.count)))

    checks = check_ranges(hands, hero, villain)
    for street in ("river", "turn"):
        cell = checks[street]
        total = sum(cell["holdings"].values())
        if cell["faced"] >= 15 and total and cell["bets"] / cell["faced"] >= 0.4 and cell["holdings"]["rien"] / total >= 0.4:
            items.append(PlanItem(
                "Face à ses mises",
                f"Quand tu checkes le {street}, il attaque : garde quelques paires moyennes dans tes checks "
                "pour pouvoir payer, et n'abandonne pas systématiquement.",
                f"Il mise {100 * cell['bets'] / cell['faced']:.0f} % ({cell['bets']}/{cell['faced']}) derrière ton check ; "
                f"tu n'y as rien {100 * cell['holdings']['rien'] / total:.0f} % du temps.",
                "indicatif", weight=_priority(3, cell["faced"])))
            break
    return items


# --- À tester -----------------------------------------------------------------------

def _to_test(lines: list[Line]) -> list[PlanItem]:
    items = []
    covered = set()
    for ln in pooled_by_size(lines):
        made_folds = ln.fold_holdings["paire"] + ln.fold_holdings["fort"]
        if ln.street == "flop" or len(ln.seen) >= 4 or made_folds < 3:
            continue
        covered.add((ln.street, ln.size))
        required = 100 * median(ln.required) if ln.required else 0.0
        items.append(PlanItem(
            "À tester",
            f"Ses {SIZE_WORDS[ln.size]} {ln.street} : paye de temps en temps avec top paire ou mieux pour savoir "
            "ce qu'il y met.",
            f"{ln.count} fois, seulement {len(ln.seen)} vue(s) ; tu as foldé {made_folds} fois avec une paire ou mieux. "
            f"Payer est rentable s'il bluffe au moins {required:.0f} % du temps.",
            "à confirmer", weight=made_folds))
    for ln in lines:
        if ln.street == "flop" or ln.seen or ln.fold_holdings["fort"] < 2 or (ln.street, ln.size) in covered:
            continue
        items.append(PlanItem(
            "À tester", f"Sa ligne {ln.street} « {ln.name} » : jamais vue à l'abattage, teste-la avec tes meilleures mains.",
            f"Tu as lâché top paire ou mieux {ln.fold_holdings['fort']} fois sur {ln.count}.",
            "à confirmer", weight=ln.fold_holdings["fort"]))
    return items


def build_plan(hands: list[Hand], stats: dict[str, PlayerStats], hero: str, villain: str,
               lines: Optional[list[Line]] = None) -> Plan:
    v, h = stats[villain], stats[hero]
    lines = lines if lines is not None else villain_lines(hands, villain, hero)
    plan = Plan(profile(v))
    plan.items += _preflop(v, h, summarize(decisions(hands, hero)))
    plan.items += _betting(hands, hero, villain, lines)
    plan.items += _facing(hands, hero, villain, lines)
    plan.items += _to_test(lines)
    return plan


def plan_text(plan: Plan) -> str:
    out = ["PLAN DE JEU", f"Profil : {plan.profile}"]
    for phase, items in plan.by_phase().items():
        if not items:
            continue
        out.append(f"\n{phase}")
        for i, it in enumerate(items, 1):
            out.append(f"  {i}. {it.action}")
            out.append(f"     ↳ {it.why} [{it.confidence}]")
    return "\n".join(out)
