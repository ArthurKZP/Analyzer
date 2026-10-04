import json
import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from analyzer.app import coach_chat
from analyzer.app.coach_chat import Coach, CoachError
from analyzer.theory import coach, postflop
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

    def create(self, **kwargs):
        self.calls.append(json.loads(json.dumps(kwargs, default=str)))
        return self.script.pop(0)


def tool_use(name, args, ident="t1"):
    return Response([{"type": "thinking", "thinking": "", "signature": "sig"},
                     {"type": "tool_use", "id": ident, "name": name, "input": args}], "tool_use")


def answer(text):
    return Response([{"type": "text", "text": text}], "end_turn")


class FakeSolves:
    """La session d'une étude, sur le petit arbre de test_coach."""

    def __init__(self):
        self.query = make_query()

    def live_session(self, ident):
        return object()

    def node(self, spot, path):
        return {"node": self.query(path), "live": True}


class FakeLibrary:
    def __init__(self):
        self.solves = FakeSolves()

    def _spot(self, ident):
        if not ident.startswith("spot:srp:"):
            raise KeyError(ident)
        return SimpleNamespace(ident=ident)


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
        self.assertEqual((first["model"], first["fallbacks"], first["betas"]), ("claude-opus-5-5", "default",
                                                                                 ["server-side-fallback-2026-07-01"]))
        self.assertEqual((first["output_config"], first["cache_control"]), ({"effort": "high"}, {"type": "ephemeral"}))
        self.assertNotIn("tool_choice", first)  # pas d'appel forcé : refusé par le modèle
        second = client.calls[1]["messages"]
        self.assertEqual([m["role"] for m in second], ["user", "assistant", "user"])
        self.assertEqual(second[1]["content"][0]["type"], "thinking")  # réflexion renvoyée telle quelle
        result = second[2]["content"][0]
        self.assertEqual(result["tool_use_id"], "t1")
        self.assertIn("schemas", json.loads(result["content"]))
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
                self.assertEqual((status, data["model"], data["key"]), (200, "claude-opus-5-5", False))
                self.assertEqual(request("GET", "/api/coach/inconnue")[0], 404)
                for bad in ({"text": ""}, {"text": "x" * 5000}, {"text": "ok", "context": {"spot": 3}},
                            {"text": "ok", "context": {"spot": "spot:srp:KsKd4c", "path": "x"}}, ["texte"]):
                    self.assertEqual(request("POST", "/api/coach/message", bad)[0], 400, bad)
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
