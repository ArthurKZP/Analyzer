"""Coach conversationnel : Claude (API Anthropic) répond aux questions de stratégie en s'appuyant sur les
données d'Analyzer, qu'il consulte par des outils : plan de jeu suggéré, études résolues (stratégie d'un
nœud, d'une main), écarts des adversaires réels.

Une question = un tour : Claude appelle les outils dont il a besoin, puis répond. La conversation est
renvoyée telle quelle à chaque question (blocs de réflexion compris) : on n'y fait qu'ajouter, sauf pour
retirer un tour qui a échoué.

Le module `anthropic` est optionnel (pip install anthropic) ; la clé vient de ANTHROPIC_API_KEY, ou d'un
profil `ant auth login`. Modèle et effort : ANALYZER_COACH_MODEL, ANALYZER_COACH_EFFORT.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..theory import coach, postflop, review, studyspots

MODEL = os.environ.get("ANALYZER_COACH_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("ANALYZER_COACH_EFFORT", "high")
MAX_TOKENS = 16000
MAX_ROUNDS = 12  # appels au modèle par question, outils compris
FALLBACK_BETA = "server-side-fallback-2026-07-01"  # en cas de refus, une autre version du modèle reprend
MAX_CONVERSATIONS = 20
LOAD_TIMEOUT = 600  # secondes pour ouvrir une étude (une étude SRP prend environ une minute)
PRICES = {"input": 4.0, "output": 20.0, "cache_read": 0.2, "cache_write": 5.0}  # $ / million de jetons (Opus 5.5)

SETUP = ("Le coach utilise Claude, l'IA d'Anthropic, par son API : installe le module (pip install anthropic) "
         "puis donne ta clé API (créée sur console.anthropic.com) dans la variable d'environnement "
         "ANTHROPIC_API_KEY avant de lancer l'application (ou connecte-toi avec « ant auth login »).")

SYSTEM = """Tu es le coach de poker intégré à Analyzer, un outil d'étude du heads-up No Limit Hold'em (100 bb, salle Betclic). Ton élève est un joueur régulier qui étudie la théorie pour simplifier son jeu et mieux exploiter ses adversaires.

Tu consultes ses données avec des outils :
- plan_de_jeu : la synthèse de ses études résolues pour un type de pot (schémas de flop, règles par famille de mains, suite à la turn et à la river selon la carte, jeu de l'autre joueur).
- liste_etudes : les flops résolus disponibles, avec leur schéma de c-bet.
- strategie_noeud : la stratégie du solveur à un moment précis d'un spot résolu (fréquences de toute la range, par famille de mains, équités, EV, exemples de mains).
- strategie_main : une main précise à ce moment (sa stratégie, l'EV de chaque action, son équité).
- ecarts_adversaire : un adversaire réel, son type (régulier ou récréatif) et ses écarts de fréquence face au solveur.

Comment répondre :
- Appuie-toi sur les données : consulte les outils avant de citer une fréquence, une taille ou une EV, et n'invente pas de chiffres. Si un spot n'est pas résolu, dis-le et sers-toi du flop résolu le plus proche ou du plan de jeu.
- Explique le pourquoi avec les notions du jeu : avantage de range et de nuts, équité et sa réalisation, position, protection, blockers, polarisation, SPR, valeur et bluff.
- Une action que le solveur mélange a une EV presque égale aux autres : dis-le plutôt que de lui chercher une raison forte. Un écart d'EV de moins de 0,1 bb est négligeable.
- Privilégie des règles simples et applicables par un humain (par exemple « mise toutes tes top pairs et tes tirages couleur, checke les paires moyennes »), avec les fréquences quand elles éclairent.
- L'arbre du solveur est simplifié : une taille de mise par situation au flop et à la turn, deux à la river, et les ranges préflop de la solution HU 100 bb. Rappelle-le si une question dépend d'une taille absente.
- Contre un adversaire réel, sépare la théorie de l'exploitation : un écart « solide » justifie de s'adapter, un écart « indicatif » appelle de la prudence. Contre un récréatif, l'exploitation prime.
- Réponds en français, de façon concise et structurée (listes courtes), montants en bb, cartes notées comme A♠K♦ ou AKs.

Repères : BTN = bouton (petite blinde, en position après le flop), BB = grosse blinde (hors de position). SRP : open du BTN à 2,5 bb payé par la BB (pot 5 bb, 97,5 bb derrière) ; pot 3bet : 3bet de la BB à 11,5 bb payé (pot 23 bb) ; pot 4bet : 4bet du BTN à 26 bb payé (pot 52 bb). Un spot se nomme « spot:<famille>:<flop> », par exemple spot:srp:KsKd4c. Une ligne d'actions est une liste : "check", "bet" (ou "bet 75" pour la taille la plus proche de 75 % du pot), "call", "raise", "fold", "allin", et les cartes de turn et de river ("Qh"). En SRP et en pot 4bet, la BB ne mène pas au flop : la ligne commence par "check"."""

LINE_HELP = ("actions déjà jouées depuis le début du flop, dans l'ordre : \"check\", \"bet\" (ou \"bet 75\" pour la "
             "taille la plus proche de 75 % du pot), \"call\", \"raise\", \"fold\", \"allin\", et les cartes de turn et "
             "de river (\"Qh\"). L'outil renvoie la décision qui suit ; liste vide : la première vraie décision du flop "
             "(la c-bet du bouton en SRP et en pot 4bet, celle de la BB en pot 3bet)")
TOOLS = [
    {"name": "plan_de_jeu",
     "description": ("Plan de jeu suggéré d'un type de pot, tiré des études résolues : les flops regroupés par schéma de "
                     "c-bet (range bet, c-bet fréquente, mixte, check fréquent) avec, pour chaque schéma, le pourquoi, la "
                     "règle au flop par famille de mains, la suite à la turn et à la river selon le type de carte, et le "
                     "jeu de l'autre joueur. Consulte-le pour toute question générale de stratégie."),
     "input_schema": {"type": "object", "properties": {
         "famille": {"type": "string", "enum": ["srp", "3bet", "4bet"],
                     "description": "srp (pot simple), 3bet (pot 3bet) ou 4bet (pot 4bet)"}},
         "required": ["famille"]}},
    {"name": "liste_etudes",
     "description": ("Les flops résolus disponibles (identifiant de spot, texture, structure de couleurs, c-bet et schéma "
                     "quand ils sont lus). Consulte-le pour trouver un spot à examiner ou le flop résolu le plus proche."),
     "input_schema": {"type": "object", "properties": {
         "famille": {"type": "string", "enum": ["srp", "3bet", "4bet"], "description": "type de pot (tous si absent)"}}}},
    {"name": "strategie_noeud",
     "description": ("Stratégie du solveur à un moment précis d'un spot résolu : qui agit, pot et tapis, fréquence de chaque "
                     "action dans toute la range, puis par famille de mains (part de range, fréquences, équité et EV "
                     "moyennes), exemples de mains par action, équité des deux ranges. Consulte-le avant d'expliquer un "
                     "choix du solveur sur un flop, une turn ou une river. Ouvrir une étude peut prendre une minute."),
     "input_schema": {"type": "object", "properties": {
         "spot": {"type": "string", "description": "identifiant du spot, ex. spot:srp:KsKd4c"},
         "ligne": {"type": "array", "items": {"type": "string"}, "description": LINE_HELP}},
         "required": ["spot", "ligne"]}},
    {"name": "strategie_main",
     "description": ("Une main précise à un moment d'un spot résolu : sa famille, son équité et son rang dans la range, et, "
                     "pour le joueur qui agit, sa stratégie et l'EV de chaque action. Consulte-le pour expliquer pourquoi "
                     "le solveur joue une main d'une façon (et pour comparer deux mains, appelle-le pour chacune)."),
     "input_schema": {"type": "object", "properties": {
         "spot": {"type": "string", "description": "identifiant du spot, ex. spot:srp:KsKd4c"},
         "ligne": {"type": "array", "items": {"type": "string"}, "description": LINE_HELP},
         "main": {"type": "string", "description": "les deux cartes, ex. AhKd"},
         "joueur": {"type": "string", "enum": ["BB", "BTN"], "description": "joueur qui tient la main (par défaut celui qui agit)"}},
         "required": ["spot", "ligne", "main"]}},
    {"name": "ecarts_adversaire",
     "description": ("Un adversaire réel de l'élève : son type (régulier ou récréatif), ses stats préflop principales, et ses "
                     "écarts de fréquence face au solveur, situation par situation (tirés des mains analysées dans « Face "
                     "au solveur »), avec la façon d'en profiter. Consulte-le pour toute question d'exploitation."),
     "input_schema": {"type": "object", "properties": {
         "adversaire": {"type": "string", "description": "pseudo de l'adversaire"}},
         "required": ["adversaire"]}},
]
STEP_TEXT = {"plan_de_jeu": "lit le plan de jeu", "liste_etudes": "regarde les études résolues",
             "strategie_noeud": "consulte la stratégie du solveur", "strategie_main": "regarde une main précise",
             "ecarts_adversaire": "examine les écarts de l'adversaire"}


class CoachError(RuntimeError):
    pass


def default_client():
    try:
        import anthropic
    except ImportError as exc:
        raise CoachError(SETUP) from exc
    return anthropic.Anthropic()


def sdk_available() -> bool:
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


# --- Conversation ------------------------------------------------------------------------------------

@dataclass
class Conversation:
    id: str
    transcript: list = field(default_factory=list)  # messages envoyés à l'API, tels quels
    shown: list = field(default_factory=list)  # ce que la page affiche : {"role", "text", "steps"}
    state: str = "idle"  # idle | running | error
    steps: list = field(default_factory=list)  # consultations du tour en cours
    error: Optional[str] = None
    usage: dict = field(default_factory=lambda: dict.fromkeys(PRICES, 0))
    updated: float = field(default_factory=time.time)

    def view(self) -> dict:
        cost = sum(self.usage[k] * PRICES[k] for k in PRICES) / 1e6
        return {"id": self.id, "state": self.state, "messages": self.shown, "steps": list(self.steps),
                "error": self.error, "cost": round(cost, 4), "model": MODEL}


def _blocks(response) -> list[dict]:
    """Le contenu de la réponse en dictionnaires, à renvoyer tel quel au tour suivant. Après un relais vers un
    autre modèle (bloc « fallback »), les blocs de réflexion et d'appel d'outil qui le précèdent sont omis."""
    content = response.to_dict()["content"]
    last = max((i for i, b in enumerate(content) if b.get("type") == "fallback"), default=None)
    if last is None:
        return content
    drop = {"thinking", "redacted_thinking", "tool_use"}
    return [b for i, b in enumerate(content) if i > last or b.get("type") not in drop]


def _text(response) -> str:
    return "\n\n".join(b.text for b in response.content if b.type == "text" and b.text.strip())


class Coach:
    def __init__(self, library, client_factory: Optional[Callable] = None):
        self.library = library
        self._client_factory = client_factory or default_client
        self._conversations: dict[str, Conversation] = {}
        self._lock = threading.Lock()

    # --- interface de l'application ---------------------------------------------------------------
    def status(self) -> dict:
        key = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
        return {"sdk": sdk_available(), "key": key, "model": MODEL, "setup": SETUP}

    def view(self, conv_id: str) -> Optional[dict]:
        with self._lock:
            conv = self._conversations.get(conv_id)
        return conv.view() if conv else None

    def ask(self, conv_id: Optional[str], text: str, context: Optional[dict] = None) -> dict:
        """Pose une question (nouvelle conversation si conv_id est inconnu) ; la réponse arrive en arrière-plan."""
        with self._lock:
            conv = self._conversations.get(conv_id or "")
            if conv is None:
                conv = Conversation(uuid.uuid4().hex[:16])
                self._conversations[conv.id] = conv
                for old in sorted(self._conversations.values(), key=lambda c: c.updated)[:-MAX_CONVERSATIONS]:
                    del self._conversations[old.id]
            if conv.state == "running":
                return conv.view()
            conv.state, conv.error, conv.steps, conv.updated = "running", None, [], time.time()
            conv.shown.append({"role": "user", "text": text})
        threading.Thread(target=self._turn, args=(conv, text, context), daemon=True, name="coach").start()
        return conv.view()

    # --- un tour ---------------------------------------------------------------------------------
    def _turn(self, conv: Conversation, text: str, context: Optional[dict]) -> None:
        start = len(conv.transcript)
        try:
            client = self._client_factory()
            conv.transcript.append({"role": "user", "content": self._question(text, context)})
            answer = self._loop(client, conv)
            conv.shown.append({"role": "coach", "text": answer, "steps": list(conv.steps)})
            conv.state = "idle"
        except CoachError as exc:
            del conv.transcript[start:]  # le tour qui a échoué ne reste pas dans la conversation
            conv.state, conv.error = "error", str(exc)
        except Exception as exc:  # noqa: BLE001 — l'erreur est montrée dans la page
            del conv.transcript[start:]
            conv.state, conv.error = "error", self._explain(exc)
        finally:
            conv.updated = time.time()

    def _question(self, text: str, context: Optional[dict]) -> str:
        if not context or not isinstance(context, dict) or not context.get("spot"):
            return text
        parts = [f"spot {context['spot']}"]
        tokens = self._describe(context["spot"], context.get("path") or [])
        if tokens is not None:
            parts.append("ligne " + json.dumps(tokens, ensure_ascii=False))
        if context.get("main"):
            parts.append(f"main sélectionnée {context['main']}")
        return f"[L'élève regarde dans l'explorateur : {' · '.join(parts)}]\n\n{text}"

    def _loop(self, client, conv: Conversation) -> str:
        for _ in range(MAX_ROUNDS):
            response = client.beta.messages.create(
                model=MODEL, max_tokens=MAX_TOKENS, system=SYSTEM, tools=TOOLS, messages=conv.transcript,
                output_config={"effort": EFFORT}, cache_control={"type": "ephemeral"},
                betas=[FALLBACK_BETA], fallbacks="default",
            )
            self._count(conv, response)
            if response.stop_reason == "refusal":
                raise CoachError("Le coach a décliné cette question. Reformule-la autrement.")
            conv.transcript.append({"role": "assistant", "content": _blocks(response)})
            if response.stop_reason == "pause_turn":
                continue
            calls = [b for b in response.content if b.type == "tool_use"]
            if response.stop_reason != "tool_use" or not calls:
                answer = _text(response)
                if response.stop_reason == "max_tokens":
                    answer += "\n\n(Réponse coupée : elle dépassait la longueur maximale.)"
                return answer or "(Pas de réponse.)"
            results = []
            for call in calls:
                conv.steps.append(STEP_TEXT.get(call.name, call.name) + self._step_detail(call.input))
                content, error = self._run_tool(call.name, call.input)
                result = {"type": "tool_result", "tool_use_id": call.id, "content": content}
                if error:
                    result["is_error"] = True
                results.append(result)
            conv.transcript.append({"role": "user", "content": results})
        raise CoachError("Le coach n'a pas fini sa réponse (trop de consultations) : pose une question plus précise.")

    @staticmethod
    def _step_detail(args: dict) -> str:
        if not isinstance(args, dict):
            return ""
        bits = [str(args[k]) for k in ("famille", "adversaire", "spot", "main") if args.get(k)]
        if args.get("ligne"):
            bits.append(" ".join(map(str, args["ligne"])))
        return f" ({', '.join(bits)})" if bits else ""

    @staticmethod
    def _count(conv: Conversation, response) -> None:
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        conv.usage["input"] += getattr(usage, "input_tokens", 0) or 0
        conv.usage["output"] += getattr(usage, "output_tokens", 0) or 0
        conv.usage["cache_read"] += getattr(usage, "cache_read_input_tokens", 0) or 0
        conv.usage["cache_write"] += getattr(usage, "cache_creation_input_tokens", 0) or 0

    @staticmethod
    def _explain(exc: Exception) -> str:
        if isinstance(exc, TypeError) and "authentication" in str(exc):
            return SETUP
        try:
            import anthropic
        except ImportError:
            traceback.print_exc()
            return "Erreur inattendue du coach (détails dans le terminal)."
        if isinstance(exc, anthropic.AuthenticationError):
            return "Clé API refusée : vérifie ANTHROPIC_API_KEY. " + SETUP
        if isinstance(exc, anthropic.PermissionDeniedError):
            return "Cette clé API n'a pas accès au modèle du coach."
        if isinstance(exc, anthropic.NotFoundError):
            return f"Modèle introuvable : {MODEL} (variable ANALYZER_COACH_MODEL)."
        if isinstance(exc, anthropic.RateLimitError):
            return "Trop de demandes pour le moment : réessaie dans une minute."
        if isinstance(exc, anthropic.APIStatusError):
            return f"Le service a répondu par une erreur ({exc.status_code}) : réessaie plus tard."
        if isinstance(exc, anthropic.APIConnectionError):
            return "Pas de connexion au service : vérifie ta connexion internet."
        traceback.print_exc()
        return "Erreur inattendue du coach (détails dans le terminal)."

    # --- outils ----------------------------------------------------------------------------------
    def _run_tool(self, name: str, args) -> tuple[str, bool]:
        """(contenu, erreur ?) : une erreur est rendue au modèle, qui peut corriger son appel."""
        if not isinstance(args, dict):
            return "Arguments invalides.", True
        try:
            if name == "plan_de_jeu":
                data = self._plan(args.get("famille"))
            elif name == "liste_etudes":
                data = self._studies(args.get("famille"))
            elif name == "strategie_noeud":
                path, node, steps = self._resolve(args.get("spot"), args.get("ligne"))
                data = dict(coach.node_brief(node), spot=args["spot"], ligne=steps)
            elif name == "strategie_main":
                path, node, steps = self._resolve(args.get("spot"), args.get("ligne"))
                player = {"BB": 0, "BTN": 1}.get(args.get("joueur"))
                data = coach.combo_brief(node, str(args.get("main", "")), player)
                if data is None:
                    raise CoachError(f"La main {args.get('main')} n'est pas dans les ranges à ce moment du coup.")
                data["ligne"] = steps
            elif name == "ecarts_adversaire":
                data = self._villain(args.get("adversaire"))
            else:
                return f"Outil inconnu : {name}.", True
        except CoachError as exc:
            return str(exc), True
        except Exception:  # noqa: BLE001 — l'outil échoue, le coach le dit
            traceback.print_exc()
            return "Erreur pendant la consultation (détails dans le terminal de l'application).", True
        return json.dumps(data, ensure_ascii=False), False

    @staticmethod
    def _plan(family) -> dict:
        if family not in studyspots.FAMILIES:
            raise CoachError("Famille inconnue : srp, 3bet ou 4bet.")
        plan = coach.family_plan(family)
        if not plan["count"]:
            raise CoachError(f"Pas encore de plan pour les {plan['name']} : aucun flop résolu n'a été lu "
                             "(onglet « Plan de jeu suggéré », bouton « Préparer le plan »).")
        pct = lambda x: round(100 * x)  # noqa: E731

        def cards(rows: list[dict], defender: bool = False) -> list[dict]:
            key = "fold_pct" if defender else "mise_pct"
            return [{"carte": r["label"], key: pct(r["fold"] if defender else r["aggr"]), "regles": dict(r["rules"])}
                    for r in rows]
        groups = []
        for g in plan["groups"]:
            d = g["defense"]
            groups.append({
                "schema": g["label"], "flops": [studyspots.board_text(r["board"]) for r in g["flops"]],
                "textures": g["textures"], "cbet_pct": pct(g["cbet"]), "taille": g.get("size"), "tailles_pct": g["sizes"],
                "avantage_equite_pts": round(100 * g["eq_adv"]), "avantage_nuts_pts": round(100 * g["nut_adv"]),
                "pourquoi": g["why"], "flop": dict(g["flop_rules"]), "turn_2e_barrel": cards(g["turn"]),
                "river_3e_barrel": cards(g["river"]), "cbet_retardee": cards(g["delayed"]),
                "defense": {
                    "face_cbet": ({"fold_pct": pct(d["vs_cbet"]["fold"]), "relance_pct": pct(d["vs_cbet"]["raise"]),
                                   "regles": dict(d["vs_cbet"]["rules"])} if d["vs_cbet"] else None),
                    "face_relance": ({"fold_pct": pct(d["vs_xr"]["fold"]), "regles": dict(d["vs_xr"]["rules"])}
                                     if d["vs_xr"] else None),
                    "stab": {"mise_pct": pct(d["stab"]["aggr"]), "regles": dict(d["stab"]["rules"])} if d["stab"] else None,
                    "face_2e_barrel": cards(d["vs_barrel"], True), "face_3e_barrel": cards(d["vs_barrel3"], True),
                    "probe": cards(d["probe"])}})
        return {"famille": plan["name"], "description": plan["label"], "flops_lus": plan["count"],
                "initiative": plan["who"], "adversaire": plan["other"], "schemas": groups}

    @staticmethod
    def _studies(family) -> dict:
        out = []
        for meta in studyspots.spot_studies().values():
            if family and meta.get("family") != family:
                continue
            row = {"spot": meta["id"], "flop": studyspots.board_text(meta["board"]), "texture": meta.get("texture"),
                   "couleurs": studyspots.suit_pattern(meta["board"])}
            plan = coach.load_plan(meta["key"])
            if plan:
                r = coach.flop_row(plan)
                row.update(cbet_pct=round(100 * r["cbet"]), schema=coach.PATTERN_LABEL[r["pattern"]])
            out.append(row)
        return {"etudes": sorted(out, key=lambda r: r["spot"]), "nombre": len(out)}

    def _villain(self, name) -> dict:
        lib = self.library
        if not isinstance(name, str) or not name.strip():
            raise CoachError("Donne le pseudo de l'adversaire.")
        match = [o["name"] for o in lib.opponents() if o["name"].lower() == name.strip().lower()]
        if not match:
            known = ", ".join(o["name"] for o in lib.opponents()[:10])
            raise CoachError(f"Adversaire inconnu : {name}. Adversaires : {known}.")
        villain = match[0]
        hands = lib.hands_against(villain)
        kind = lib.kinds().get(villain, {})
        st = lib.all_stats().get(villain)
        stats = {}
        if st is not None:
            for key, label in (("sb_first.raise", "open au bouton"), ("sb_first.call", "limp au bouton"),
                               ("bb_vs_open.fold", "fold de BB face à l'open"), ("bb_vs_open.raise", "3bet face à l'open"),
                               ("sb_vs_3bet.fold", "fold face au 3bet"), ("sb_vs_3bet.raise", "4bet face au 3bet")):
                ratio = st.r(key)
                if ratio.opps:
                    stats[label] = {"pct": round(ratio.pct), "occasions": ratio.opps}
        digests, todo = review.collect(hands, lib.hero)
        deviations = []
        for group in review.by_situation(digests, "V"):
            for dev in review.deviations(group):
                deviations.append({"situation": group["label"], "famille": group["family"], "fois": group["n"],
                                   "action": dev["label"], "lui_pct": round(100 * dev["observed"]),
                                   "theorie_pct": round(100 * dev["expected"]), "confiance": dev["confidence"],
                                   "conseil": review.exploit(group, dev, True)})
        deviations.sort(key=lambda d: (d["confiance"] != "solide", -abs(d["lui_pct"] - d["theorie_pct"])))
        return {"adversaire": villain, "mains": len(hands), "type": kind.get("kind"), "type_source": kind.get("source"),
                "signaux": kind.get("reasons", []), "stats_preflop": stats,
                "mains_postflop_analysees": len(digests), "mains_a_analyser": len(todo), "ecarts": deviations[:20]}

    # --- études : ouvrir, suivre une ligne -----------------------------------------------------------
    def _node(self, spot, path: list) -> dict:
        solves = self.library.solves
        if solves.live_session(spot.ident) is None:
            view = solves.lookup(spot)
            if view["state"] in ("waiting", "running"):
                raise CoachError("Ce spot est en cours de résolution : réessaie quand il sera prêt.")
            if not view.get("study"):
                raise CoachError(f"{spot.ident} n'est pas résolu. Choisis un spot résolu (outil liste_etudes) "
                                 "ou demande à l'élève de le résoudre dans l'explorateur.")
            job = solves.start(spot, force=True)
            deadline = time.time() + LOAD_TIMEOUT
            while solves.live_session(spot.ident) is None:
                state = (solves.get(job["job"]) or {}).get("state")
                if state in ("error", "cancelled") or time.time() > deadline:
                    raise CoachError("L'étude n'a pas pu être ouverte.")
                time.sleep(0.5)
        return solves.node(spot, path)["node"]

    def _spot(self, ident):
        if not isinstance(ident, str):
            raise CoachError("Donne l'identifiant du spot, par exemple spot:srp:KsKd4c.")
        try:
            return self.library._spot(ident.strip())
        except Exception as exc:  # noqa: BLE001 — identifiant inconnu ou hors des spots couverts
            raise CoachError(f"Spot inconnu : {ident}. Exemple : spot:srp:KsKd4c.") from exc

    def _resolve(self, ident, line) -> tuple[list, dict, list[str]]:
        """Suit la ligne depuis la racine ; renvoie le chemin, le nœud et la ligne lisible."""
        spot = self._spot(ident)
        if not isinstance(line, list):
            raise CoachError("La ligne est une liste d'actions, par exemple [\"check\", \"bet\", \"call\", \"Qh\"].")
        path: list = []
        node = self._node(spot, path)
        shown: list[str] = []
        for token in line:
            token = str(token).strip()
            card = re.fullmatch(r"([2-9tjqkaTJQKA])([cdhsCDHS])", token)
            while node["type"] == "action" and len(node["actions"]) == 1 and \
                    (card or node["actions"][0]["kind"] != self._kind(token)[0]):
                path = path + [{"type": "action", "index": 0}]  # check forcé : la BB ne mène pas
                shown.append("check (forcé)")
                node = self._node(spot, path)
            if card:
                value = card.group(1).upper() + card.group(2).lower()
                if node["type"] != "chance":
                    raise CoachError(f"Pas de carte à tirer ici (« {token} ») : {self._options(node)}.")
                if value not in (node.get("cards") or []):
                    raise CoachError(f"La carte {value} n'est pas possible ici (déjà sur le board ?).")
                path = path + [{"type": "card", "card": value}]
                shown.append(value)
            else:
                index = self._find(node, token)
                path = path + [{"type": "action", "index": index}]
                shown.append(postflop.action_labels(node)[index])
            node = self._node(spot, path)
        while node["type"] == "action" and len(node["actions"]) == 1:  # la décision qui suit, pas un check forcé
            path = path + [{"type": "action", "index": 0}]
            shown.append("check (forcé)")
            node = self._node(spot, path)
        return path, node, shown

    @staticmethod
    def _kind(token: str) -> tuple[Optional[str], Optional[float]]:
        words = token.lower().replace("%", " ").split()
        if not words:
            return None, None
        aliases = {"check": "check", "x": "check", "bet": "bet", "mise": "bet", "b": "bet", "call": "call",
                   "suit": "call", "paye": "call", "c": "call", "raise": "raise", "relance": "raise", "r": "raise",
                   "fold": "fold", "couche": "fold", "f": "fold", "allin": "allin", "tapis": "allin", "shove": "allin"}
        kind = aliases.get(words[0])
        size = None
        if len(words) > 1:
            try:
                size = float(words[1].replace(",", "."))
            except ValueError:
                size = None
        return kind, size

    def _find(self, node: dict, token: str) -> int:
        if node["type"] != "action":
            raise CoachError(f"Pas d'action possible ici (« {token} ») : {self._options(node)}.")
        kind, size = self._kind(token)
        actions = node["actions"]
        if kind == "allin":
            hits = [i for i, a in enumerate(actions) if a.get("allin") and a["kind"] in ("bet", "raise")]
        elif kind in ("bet", "raise"):
            hits = [i for i, a in enumerate(actions) if a["kind"] == kind]
            if not hits:  # « bet » face à une mise : la relance, et inversement
                hits = [i for i, a in enumerate(actions) if a["kind"] in ("bet", "raise")]
        else:
            hits = [i for i, a in enumerate(actions) if a["kind"] == kind]
        if not hits:
            raise CoachError(f"Action « {token} » impossible ici : {self._options(node)}.")
        if len(hits) == 1:
            return hits[0]
        if size is not None and node.get("pot"):
            return min(hits, key=lambda i: abs(100 * actions[i]["amount"] / node["pot"] - size))
        freqs, _ = postflop.node_summary(node)
        return max(hits, key=lambda i: freqs[i])  # sans taille : la plus jouée

    @staticmethod
    def _options(node: dict) -> str:
        if node["type"] == "chance":
            return "il faut une carte (turn ou river)"
        if node["type"] != "action":
            return "le coup est fini"
        who = "BB" if node["player"] == 0 else "BTN"
        return f"{who} a le choix entre " + ", ".join(postflop.action_labels(node))

    def _describe(self, ident: str, path: list) -> Optional[list[str]]:
        """La ligne de l'explorateur en mots que le coach peut reprendre dans ses appels d'outil."""
        try:
            spot = self._spot(ident)
            if self.library.solves.live_session(spot.ident) is None:
                return None
            tokens, prefix = [], []
            for step in path:
                node = self.library.solves.node(spot, prefix)["node"]
                if step.get("type") == "card":
                    tokens.append(step["card"])
                else:
                    action = node["actions"][step["index"]]
                    if action.get("allin"):
                        tokens.append("allin")
                    elif action["kind"] in ("bet", "raise") and node.get("pot"):
                        tokens.append(f"{action['kind']} {round(100 * action['amount'] / node['pot'])}")
                    else:
                        tokens.append(action["kind"])
                prefix = prefix + [step]
            return tokens
        except Exception:  # noqa: BLE001 — sans ligne lisible, le coach a quand même le spot
            return None
