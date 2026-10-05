import json
import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from analyzer.app import coach_chat
from analyzer.app.coach_chat import Coach, CoachError
from analyzer.theory import coach, exploit, postflop
from analyzer.theory.studyspots import StudySpot
from tests.test_coach import BOARD, make_query


class Block(SimpleNamespace):
    pass


class Response:
    """Réponse factice au format du SDK : contenu, raison d'arrêt, usage, to_dict()."""

    def __init__(self, blocks, stop_reason, usage=(1000, 200)):
        self.content = [Block(**b) for b in blocks]
        self._blocks = blocks
        self.stop_reason = stop_reason
        self.usage = SimpleNamespace(input_tokens=usage[0], output_tokens=usage[1], cache_read_input_tokens=0,
                                     cache_creation_input_tokens=0)

    def to_dict(self):
        return {"content": [dict(b) for b in self._blocks]}


class FakeClient:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))
        # liste des modèles de l'API, du plus récent au plus ancien
        self.models = SimpleNamespace(list=lambda limit: [SimpleNamespace(id=m) for m in
                                                          ("modele-rapide-2", "modele-opus-2", "modele-opus-1")])

    def create(self, **kwargs):
        self.calls.append(json.loads(json.dumps(kwargs, default=str)))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class APIError(Exception):
    """Erreur du service au format du SDK : code HTTP et corps JSON."""

    def __init__(self, status, message):
        super().__init__(f"Error code: {status}")
        self.status_code = status
        self.body = {"type": "error", "error": {"type": "invalid_request_error", "message": message}}


def tool_use(name, args, ident="t1"):
    return Response([{"type": "thinking", "thinking": "", "signature": "sig"},
                     {"type": "tool_use", "id": ident, "name": name, "input": args}], "tool_use")


def answer(text):
    return Response([{"type": "text", "text": text}], "end_turn")


class FakeSession:
    """Session ouverte : le petit arbre de test_coach, et la meilleure réponse (tout en dernière action)."""

    def __init__(self, query):
        self.query = query
        self.profile = None
        self.asked = []

    def ask(self, request):
        self.asked.append(request)
        self.profile = request.get("profile")
        return {"node": self.query(request["path"])}

    def exploit(self, path, profile, player):
        self.asked.append({"path": path, "profile": profile, "exploit": player})
        self.profile = profile
        node = self.query(path)
        na = len(node["actions"])
        rows = [[r[0], r[1], 2.5, 2.0, 0.5] + [float(a == na - 1) for a in range(na)] + [2.0] * na
                for r in node["hands"][player]] if node.get("player") == player else []
        return {"node": node, "exploit": {"avg": [2.5, 2.0, 0.5], "hands": rows},
                "profile": {"situations": {k: {"solver": 0.3, "profile": 0.4} for k in profile["tilts"]}}}


class FakeSolves:
    """La session d'une étude, sur le petit arbre de test_coach."""

    def __init__(self):
        self.query = make_query()
        self.session = FakeSession(self.query)

    def live_session(self, ident):
        return self.session

    def node(self, spot, path):
        return {"node": self.query(path), "live": True}


class FakeLibrary:
    def __init__(self):
        self.solves = FakeSolves()
        self.hands = []

    def opponents(self):
        return [{"name": "Villain", "hands": 300}, {"name": "Fish", "hands": 40}]

    def kinds(self):
        return {"Villain": {"kind": "reg"}, "Fish": {"kind": "rec"}}

    def _spot(self, ident):
        if not ident.startswith("spot:srp:"):
            raise KeyError(ident)
        return SimpleNamespace(ident=ident, family="srp", board=list(BOARD))


def wait(c, conv_id):
    deadline = time.time() + 10
    while c.view(conv_id)["state"] == "running" and time.time() < deadline:
        time.sleep(0.02)
    return c.view(conv_id)


class CoachChatTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"ANALYZER_HOME": self.tmp.name})
        self.env.start()
        spot = StudySpot("srp", list(BOARD), plan={})
        key = postflop.study_key(spot.request())
        coach.save_plan(coach.extract(make_query(), spot, key))
        studies = {spot.ident: {"id": spot.ident, "key": key, "family": "srp", "board": BOARD, "texture": "King high"}}
        self.studies = mock.patch.object(coach.studyspots, "spot_studies", lambda: studies)
        self.studies.start()

    def tearDown(self):
        self.studies.stop()
        self.env.stop()
        self.tmp.cleanup()

    def test_tool_loop(self):
        client = FakeClient([tool_use("plan_de_jeu", {"famille": "srp"}), answer("**Range bet** sur K72 :\n- mise tout")])
        c = Coach(FakeLibrary(), client_factory=lambda: client)
        view = wait(c, c.ask(None, "Résume le plan en SRP", None)["id"])
        self.assertEqual(view["state"], "idle")
        self.assertEqual([m["role"] for m in view["messages"]], ["user", "coach"])
        self.assertIn("lit le plan de jeu (srp)", view["messages"][1]["steps"][0])
        first = client.calls[0]
        self.assertEqual((first["model"], first["fallbacks"], first["betas"]), ("modele-opus-2", "default",
                                                                                 ["server-side-fallback-2026-07-01"]))
        self.assertEqual((view["model"], c.status()["model"]), ("modele-opus-2", "modele-opus-2"))
        self.assertEqual((first["output_config"], first["cache_control"]), ({"effort": "high"}, {"type": "ephemeral"}))
        self.assertNotIn("tool_choice", first)  # pas d'appel forcé : refusé par le modèle
        second = client.calls[1]["messages"]
        self.assertEqual([m["role"] for m in second], ["user", "assistant", "user"])
        self.assertEqual(second[1]["content"][0]["type"], "thinking")  # réflexion renvoyée telle quelle
        result = second[2]["content"][0]
        self.assertEqual(result["tool_use_id"], "t1")
        self.assertIn("categories", json.loads(result["content"]))
        self.assertGreater(view["cost"], 0)

    def test_node_and_hand_tools(self):
        client = FakeClient([
            tool_use("strategie_noeud", {"spot": "spot:srp:Ks7d2c", "ligne": ["bet", "call", "Ah", "bet"]}),
            tool_use("strategie_main", {"spot": "spot:srp:Ks7d2c", "ligne": ["bet"], "main": "AhAd"}, "t2"),
            tool_use("strategie_noeud", {"spot": "spot:srp:Ks7d2c", "ligne": ["fold"]}, "t3"),
            answer("ok"),
        ])
        c = Coach(FakeLibrary(), client_factory=lambda: client)
        wait(c, c.ask(None, "Pourquoi ?", None)["id"])
        node = json.loads(client.calls[1]["messages"][-1]["content"][0]["content"])
        # le check forcé de la BB au flop et à la turn est traversé : la ligne lisible le montre
        self.assertEqual(node["ligne"], ["check (forcé)", "mise 33 % (1,6 bb)", "call", "Ah", "check (forcé)",
                                         "mise 33 % (1,6 bb)"])
        self.assertEqual((node["joueur"], node["actions"]), ("BB", ["fold", "call", "relance à 5 bb"]))
        self.assertIn("par_famille", node)
        hand = json.loads(client.calls[2]["messages"][-1]["content"][0]["content"])
        self.assertEqual((hand["main"], hand["joueur"], hand["famille"]), ("AhAd", "BB", "Overpair"))
        self.assertEqual(hand["strategie"]["relance à 5 bb"], 1.0)
        self.assertEqual(hand["ev_par_action_bb"], {"fold": 1.0, "call": 1.0, "relance à 5 bb": 2.0})
        error = client.calls[3]["messages"][-1]["content"][0]
        self.assertTrue(error["is_error"])
        self.assertIn("BTN a le choix entre check, mise 33 %", error["content"])

    def test_exploit_tool(self):
        profile = {"family": "srp", "role": "defenseur", "hands": 120, "bridge": {"villain": 0, "aggressor": 1,
                                                                                  "tilts": {"fold_flop": 0.6}},
                   "rows": [{"key": "fold_flop", "label": "Fold face à la c-bet", "opps": 100, "observed": 0.2,
                             "solver": 0.3, "flops": 24, "kept": 0.22, "reading": "défend plus face à la c-bet",
                             "confidence": "solide"}]}
        lib = FakeLibrary()
        c = Coach(lib)
        with mock.patch.object(exploit, "profile", return_value=profile) as built:
            text, error = c._run_tool("exploiter", {"spot": "spot:srp:Ks7d2c"})
        self.assertFalse(error, text)
        data = json.loads(text)
        # au flop, c'est le bouton qui décide (après le check forcé) : l'élève est le bouton, l'adversaire la BB
        self.assertEqual((data["adversaire"], data["heros"], data["adversaire_joue"]),
                         ("Villain", "BTN", "BB, face à l'initiative"))
        self.assertEqual(built.call_args.args[1:], (["Villain"], "srp", "defenseur"))
        self.assertEqual(data["noeud"]["exploit_pct"], {"check": 0, "mise 33 % (1,6 bb)": 100})
        self.assertEqual(data["profil"][0]["retenu_pct"], 22)
        self.assertEqual(data["style"], ["défend plus face à la c-bet"])
        self.assertEqual(data["sur_ce_flop"], [{"situation": "Fold face à la c-bet", "solveur_pct": 30, "profil_pct": 40}])
        self.assertNotIn("prudence", data)
        # la ligne suivante se lit sous le profil en place (pas de retour à l'arbre du solveur entre deux appels)
        lib.solves.session.asked.clear()
        with mock.patch.object(exploit, "profile", return_value=profile) as built:
            text, error = c._run_tool("exploiter", {"spot": "spot:srp:Ks7d2c", "ligne": ["bet"], "heros": "BB",
                                                    "adversaire": "récréatifs"})
        self.assertFalse(error, text)
        self.assertEqual(built.call_args.args[1:], (["Fish"], "srp", "agresseur"))
        self.assertTrue(all(q.get("profile") == profile["bridge"] for q in lib.solves.session.asked))
        data = json.loads(text)
        self.assertEqual((data["adversaire"], data["heros"], data["noeud"]["joueur"]), ("les récréatifs", "BB", "BB"))

        empty = dict(profile, bridge=dict(profile["bridge"], tilts={}))
        with mock.patch.object(exploit, "profile", return_value=empty):
            text, error = c._run_tool("exploiter", {"spot": "spot:srp:Ks7d2c"})
        self.assertTrue(error)
        self.assertIn("Pas assez de données sur Villain", text)
        text, error = c._run_tool("exploiter", {"spot": "spot:srp:Ks7d2c", "adversaire": "Inconnu"})
        self.assertTrue(error)
        self.assertIn("Adversaires : Villain, Fish", text)

    def test_bluffs_tool(self):
        from analyzer import bluffs
        from analyzer.stats import Ratio
        lib = FakeLibrary()
        lib.hero = "Hero"
        freq = bluffs.Freq("srp", bluffs.SPOT["cbet_turn"], "over", Ratio(9, 10), solver=0.5, verdict=("plus", "à confirmer"))
        pattern = bluffs.Pattern("fréquences", "turn", "plus", "à confirmer", "2e barrel : il mise bien plus", "90 %",
                                 "Paie plus large.", 1.0)
        report = bluffs.Report(["Villain", "Fish"], 10, 4, [freq], [], [], [], [pattern])
        with mock.patch.object(bluffs, "analyze", return_value=report) as built:
            text, error = Coach(lib)._run_tool("bluffs_adversaire", {"adversaire": "réguliers"})
        self.assertFalse(error, text)
        self.assertEqual(built.call_args.args[1:], (["Villain"], "Hero"))  # le groupe des réguliers
        data = json.loads(text)
        self.assertEqual((data["adversaire"], data["patterns"][0]["conseil"]), ("les réguliers", "Paie plus large."))
        self.assertEqual(data["frequences_par_carte"][0], {"situation": "2e barrel", "pot": "SRP", "carte": "Overcard",
                                                            "lui_pct": 90, "occasions": 10, "solveur_pct": 50,
                                                            "ecart": "plus", "confiance": "à confirmer"})

    def test_refusal_and_setup_errors(self):
        refused = Response([], "refusal")
        client = FakeClient([refused])
        c = Coach(FakeLibrary(), client_factory=lambda: client)
        conv = c.ask(None, "Question", None)["id"]
        view = wait(c, conv)
        self.assertEqual(view["state"], "error")
        self.assertIn("décliné", view["error"])
        self.assertEqual(c._conversations[conv].transcript, [])  # le tour refusé ne reste pas

        def missing():
            raise CoachError(coach_chat.SETUP)
        c2 = Coach(FakeLibrary(), client_factory=missing)
        view = wait(c2, c2.ask(None, "Question", None)["id"])
        self.assertIn("ANTHROPIC_API_KEY", view["error"])
        self.assertIn("ANTHROPIC_API_KEY", Coach._explain(TypeError("Could not resolve authentication method.")))

    def test_service_errors_say_why(self):
        credit = APIError(400, "Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing.")
        with mock.patch("sys.stderr"):
            self.assertIn("n'a plus de crédit", Coach._explain(credit))
            self.assertEqual(Coach._explain(APIError(400, "messages.0: bad")),
                             "Le service a refusé la demande (400) : messages.0: bad")
            self.assertIn("momentanément indisponible (529)", Coach._explain(APIError(529, "Overloaded")))
            self.assertIn("ANALYZER_COACH_MODEL", Coach._explain(APIError(404, "model: not found")))
            client = FakeClient([credit])
            c = Coach(FakeLibrary(), client_factory=lambda: client)
            view = wait(c, c.ask(None, "Question", None)["id"])
        self.assertIn("console.anthropic.com", view["error"])

    def test_without_fallbacks_when_refused(self):
        # Le relais de modèle n'est pas ouvert à ce compte : la question repart sans, et la suite aussi.
        client = FakeClient([APIError(400, "fallbacks: not available for this organization"), answer("Réponse."),
                             answer("Deuxième.")])
        c = Coach(FakeLibrary(), client_factory=lambda: client)
        with mock.patch("sys.stderr"):
            conv = c.ask(None, "Question", None)["id"]
            self.assertEqual(wait(c, conv)["messages"][-1]["text"], "Réponse.")
        self.assertEqual(("fallbacks" in client.calls[0], "fallbacks" in client.calls[1]), (True, False))
        wait(c, c.ask(conv, "Encore", None)["id"])
        self.assertNotIn("fallbacks", client.calls[2])

    def test_line_parsing_and_context(self):
        c = Coach(FakeLibrary())
        self.assertEqual(c._kind("bet 75"), ("bet", 75.0))
        self.assertEqual(c._kind("Relance"), ("raise", None))
        self.assertEqual(c._kind("tapis"), ("allin", None))
        path, node, shown = c._resolve("spot:srp:Ks7d2c", ["check", "check", "Ah"])
        self.assertEqual((node["player"], shown), (0, ["check", "check", "Ah"]))  # la BB peut mener à la turn
        for bad in (["Zz9"], ["check", "check", "Ks"], ["check", "check", "check"]):
            with self.assertRaises(CoachError):
                c._resolve("spot:srp:Ks7d2c", bad)
        with self.assertRaises(CoachError):
            c._resolve("spot:autre:Ks7d2c", [])
        text = c._question("Et ici ?", {"spot": "spot:srp:Ks7d2c", "path": [{"type": "action", "index": 0},
                                                                          {"type": "action", "index": 1}],
                                        "main": "AKs"})
        self.assertIn('ligne ["check", "bet 33"]', text)
        self.assertIn("main sélectionnée AKs", text)
        self.assertEqual(c._question("Seul", None), "Seul")


class CoachRoutesTest(unittest.TestCase):
    def test_routes(self):
        import http.client
        import shutil
        import threading
        from pathlib import Path

        from analyzer.app.library import Library
        from analyzer.app.server import start
        tmp = tempfile.TemporaryDirectory()
        shutil.copy(Path(__file__).parent / "fixtures" / "betclic_sample.txt", Path(tmp.name) / "s.txt")
        with mock.patch.dict(os.environ, {"ANALYZER_HOME": str(Path(tmp.name) / "home"), "ANTHROPIC_API_KEY": ""}):
            lib = Library(tmp.name)
            server = start(lib, port=0)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                def request(method, path, body=None):
                    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=30)
                    conn.request(method, path, body=json.dumps(body) if body is not None else None,
                                 headers={"Content-Type": "application/json"} if body is not None else {})
                    resp = conn.getresponse()
                    data = resp.read()
                    conn.close()
                    return resp.status, json.loads(data) if data else None
                status, data = request("GET", "/api/coach")
                self.assertEqual((status, data["model"], data["key"]), (200, coach_chat.MODEL, False))
                self.assertEqual(request("GET", "/api/coach/inconnue")[0], 404)
                for bad in ({"text": ""}, {"text": "x" * 5000}, {"text": "ok", "context": {"spot": 3}},
                            {"text": "ok", "context": {"spot": "spot:srp:KsKd4c", "path": "x"}}, ["texte"]):
                    self.assertEqual(request("POST", "/api/coach/message", bad)[0], 400, bad)
                    self.assertEqual(request("POST", "/api/coach/texte", bad)[0], 400, bad)
                # la question à coller dans Claude (coach branché par MCP), avec ce que l'élève regarde
                status, data = request("POST", "/api/coach/texte", {"text": "Que faire ici ?", "context": {
                    "spot": "spot:srp:KsKd4c", "path": [], "main": "AhAd"}})
                self.assertEqual(status, 200)
                self.assertTrue(data["text"].startswith("[L'élève regarde dans l'explorateur : spot spot:srp:KsKd4c"))
                self.assertIn("main sélectionnée AhAd", data["text"])
                self.assertTrue(data["text"].endswith("Que faire ici ?"))
                for name in ("coach.js", "coach.css"):
                    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=30)
                    conn.request("GET", "/static/" + name)
                    self.assertEqual(conn.getresponse().status, 200)
                    conn.close()
            finally:
                server.shutdown()
                server.server_close()
                lib.solves.shutdown()
                tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
