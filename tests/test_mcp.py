import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from analyzer.app import mcp_server
from analyzer.app.coach_chat import SYSTEM, TOOLS

ROOT = Path(__file__).resolve().parents[1]


class FakeCoach:
    def __init__(self, delay=0.0):
        self.delay = delay
        self.calls = []

    def _run_tool(self, name, args):
        self.calls.append((name, args))
        time.sleep(self.delay)
        return (json.dumps({"outil": name, "args": args}), False) if name != "plan_de_jeu" else ("Famille inconnue.", True)


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.out = []
        self.built = 0
        self.coach = FakeCoach()

        def factory():
            self.built += 1
            return SimpleNamespace(coach=self.coach, solves=mock.Mock(), backups=mock.Mock())
        self.server = mcp_server.Server(factory, self.out.append)

    def reply(self, mid, timeout=5.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            for m in self.out:
                if m.get("id") == mid:
                    return m
            time.sleep(0.01)
        self.fail(f"pas de réponse à {mid}")

    def test_handshake_and_lists(self):
        self.server.handle({"jsonrpc": "2.0", "id": 0, "method": "server/discover", "params": {}})
        self.assertEqual(self.reply(0)["error"]["code"], -32601)  # le client revient à initialize
        self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t"}}})
        init = self.reply(1)["result"]
        self.assertEqual((init["protocolVersion"], init["serverInfo"]["name"], init["instructions"]),
                         ("2025-06-18", "analyzer", SYSTEM))
        self.assertIn("tools", init["capabilities"])
        self.server.handle({"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {"protocolVersion": "2099-01-01"}})
        self.assertEqual(self.reply(2)["result"]["protocolVersion"], "2025-11-25")
        count = len(self.out)
        self.server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertEqual(len(self.out), count)  # une notification n'a pas de réponse
        self.server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
        tools = self.reply(3)["result"]["tools"]
        self.assertEqual([t["name"] for t in tools], [t["name"] for t in TOOLS])
        self.assertTrue(all(t["inputSchema"]["type"] == "object" for t in tools))
        self.server.handle({"jsonrpc": "2.0", "id": 4, "method": "prompts/get",
                            "params": {"name": "coach", "arguments": {"question": "Et ici ?"}}})
        text = self.reply(4)["result"]["messages"][0]["content"]["text"]
        self.assertTrue(text.startswith(SYSTEM) and text.endswith("Et ici ?"))
        self.server.handle({"jsonrpc": "2.0", "id": 5, "method": "ping"})
        self.assertEqual(self.reply(5)["result"], {})
        self.assertEqual(self.built, 0)  # les mains ne se chargent qu'au premier outil

    def test_tool_calls(self):
        self.server.handle({"jsonrpc": "2.0", "id": "a", "method": "tools/call",
                            "params": {"name": "liste_etudes", "arguments": {"famille": "srp"}}})
        result = self.reply("a")["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(json.loads(result["content"][0]["text"]), {"outil": "liste_etudes", "args": {"famille": "srp"}})
        self.server.handle({"jsonrpc": "2.0", "id": "b", "method": "tools/call", "params": {"name": "plan_de_jeu"}})
        self.assertTrue(self.reply("b")["result"]["isError"])  # erreur de l'outil : rendue au modèle
        self.server.handle({"jsonrpc": "2.0", "id": "c", "method": "tools/call", "params": {"name": "inexistant"}})
        self.assertEqual(self.reply("c")["error"]["code"], -32602)
        self.assertEqual(self.built, 1)

    def test_progress_while_a_tool_runs(self):
        self.coach.delay = 0.3
        with mock.patch.object(mcp_server, "PROGRESS_EVERY", 0.05):
            self.server.handle({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                                "params": {"name": "exploiter", "arguments": {}, "_meta": {"progressToken": "p1"}}})
            self.server.handle({"jsonrpc": "2.0", "id": 10, "method": "ping"})
            self.assertEqual(self.reply(10)["result"], {})  # le serveur répond pendant l'outil
            self.reply(9)
        progress = [m for m in self.out if m.get("method") == "notifications/progress"]
        self.assertTrue(progress)
        self.assertEqual(progress[0]["params"]["progressToken"], "p1")

    def test_serve_and_config(self):
        stdin = io.StringIO('{"jsonrpc": "2.0", "id": 1, "method": "ping"}\n\nnot json\n')
        stdout = io.StringIO()
        mcp_server.serve(lambda: None, stdin, stdout)
        lines = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual([m.get("result", m.get("error", {}).get("code")) for m in lines], [{}, -32700])
        text = mcp_server.config("hands", None)
        snippet = json.loads(text[text.index("{"):text.index("Redémarre")])
        server = snippet["mcpServers"]["analyzer"]
        self.assertEqual(server["args"][:3], ["-m", "analyzer", "mcp"])
        self.assertEqual(server["env"]["PYTHONPATH"], str(ROOT))
        self.assertIn("claude mcp add --transport stdio --scope user", text)


class ProcessTest(unittest.TestCase):
    def test_stdio_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, ANALYZER_HOME=tmp, PYTHONPATH=str(ROOT))
            messages = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}},
                        {"jsonrpc": "2.0", "method": "notifications/initialized"},
                        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                         "params": {"name": "liste_etudes", "arguments": {}}}]
            proc = subprocess.Popen([sys.executable, "-m", "analyzer", "mcp", "--dossier", tmp], cwd=tmp, env=env,
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                    encoding="utf-8")
            replies = []
            reader = threading.Thread(target=lambda: replies.extend(proc.stdout), daemon=True)
            reader.start()
            for m in messages:
                proc.stdin.write(json.dumps(m) + "\n")
            proc.stdin.flush()
            deadline = time.time() + 60
            while len(replies) < 2 and time.time() < deadline:
                time.sleep(0.05)
            proc.stdin.close()
            proc.wait(timeout=30)
            reader.join(timeout=5)
            proc.stdout.close()
            proc.stderr.close()
        out = [json.loads(line) for line in replies]  # rien d'autre que le protocole sur la sortie
        self.assertEqual(out[0]["result"]["protocolVersion"], "2025-11-25")
        self.assertEqual(json.loads(out[1]["result"]["content"][0]["text"]), {"etudes": [], "nombre": 0})


if __name__ == "__main__":
    unittest.main()
