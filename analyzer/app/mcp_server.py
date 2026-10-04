"""Le coach dans ton abonnement Claude : un serveur MCP local qui donne à l'application Claude (Claude Desktop) ou à
Claude Code les outils du coach intégré (plan de jeu, études, stratégie d'un nœud ou d'une main, écarts et bluffs
d'un adversaire, node-lock). La conversation tourne dans ton abonnement : pas de clé API, pas de coût par question.

  python -m analyzer mcp --config     la configuration à copier (Claude Desktop, Claude Code)
  python -m analyzer mcp              le serveur, lancé par l'application Claude

Protocole : MCP sur stdio, JSON-RPC 2.0, un message par ligne, poignée de main « initialize ». Les clients récents
essaient d'abord le protocole sans poignée de main (server/discover) et reviennent à initialize quand le serveur ne
le connaît pas. Seul le protocole passe sur la sortie standard : tout le reste (messages d'Analyzer, programmes
lancés) est renvoyé sur la sortie d'erreur.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional, TextIO

from .coach_chat import SYSTEM, TOOLS

PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")  # poignée de main initialize
NAME = "analyzer"
VERSION = "1.0"
PROGRESS_EVERY = 5.0  # secondes entre deux notifications de progression d'un outil lent

PROMPT = {"name": "coach", "description": "Le coach de poker d'Analyzer : sa façon de répondre et ses outils.",
          "arguments": [{"name": "question", "description": "ta question (facultatif)", "required": False}]}


class ProtocolError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


class Server:
    """Répond aux messages MCP. library_factory est appelé au premier outil (chargement des mains)."""

    def __init__(self, library_factory: Callable, write: Callable[[dict], None]):
        self._factory = library_factory
        self._library = None
        self._lock = threading.Lock()
        self.write = write

    @property
    def coach(self):
        with self._lock:
            if self._library is None:
                self._library = self._factory()
            return self._library.coach

    def close(self) -> None:
        if self._library is not None:
            self._library.solves.shutdown()
            self._library.backups.shutdown()

    # --- messages -----------------------------------------------------------------------------------
    def handle(self, message: dict) -> None:
        """Traite un message ; un appel d'outil se fait à part, pour répondre aux autres pendant ce temps."""
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            self._error(message.get("id") if isinstance(message, dict) else None, -32600, "Requête invalide")
            return
        if "id" not in message:  # notification (initialized, cancelled...) : pas de réponse
            return
        if message.get("method") == "tools/call":
            threading.Thread(target=self._reply, args=(message,), daemon=True, name="mcp-outil").start()
        else:
            self._reply(message)

    def _reply(self, message: dict) -> None:
        mid = message["id"]
        params = message.get("params") or {}
        try:
            if not isinstance(params, dict):
                raise ProtocolError(-32602, "Paramètres invalides")
            result = self._dispatch(message.get("method"), params)
        except ProtocolError as exc:
            self._error(mid, exc.code, str(exc))
            return
        except Exception as exc:  # noqa: BLE001 — le client reçoit l'erreur, le serveur continue
            self._error(mid, -32603, f"Erreur interne : {exc}")
            return
        self.write({"jsonrpc": "2.0", "id": mid, "result": result})

    def _error(self, mid, code: int, message: str) -> None:
        self.write({"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}})

    def _dispatch(self, method, params: dict) -> dict:
        if method == "initialize":
            asked = params.get("protocolVersion")
            return {"protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[-1],
                    "capabilities": {"tools": {"listChanged": False}, "prompts": {"listChanged": False}},
                    "serverInfo": {"name": NAME, "title": "Analyzer — coach de poker HU", "version": VERSION},
                    "instructions": SYSTEM}
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": [{"name": t["name"], "description": t["description"], "inputSchema": t["input_schema"],
                               "annotations": {"readOnlyHint": True, "openWorldHint": False}} for t in TOOLS]}
        if method == "tools/call":
            return self._call(params)
        if method == "prompts/list":
            return {"prompts": [PROMPT]}
        if method == "prompts/get":
            if params.get("name") != PROMPT["name"]:
                raise ProtocolError(-32602, f"Prompt inconnu : {params.get('name')}")
            question = (params.get("arguments") or {}).get("question") or ""
            text = SYSTEM + ("\n\n" + question if question else "")
            return {"description": PROMPT["description"],
                    "messages": [{"role": "user", "content": {"type": "text", "text": text}}]}
        raise ProtocolError(-32601, f"Méthode inconnue : {method}")

    def _call(self, params: dict) -> dict:
        name, args = params.get("name"), params.get("arguments") or {}
        if name not in {t["name"] for t in TOOLS}:
            raise ProtocolError(-32602, f"Outil inconnu : {name}")
        token = (params.get("_meta") or {}).get("progressToken")
        done = threading.Event()
        if token is not None:  # un outil lent (ouverture d'une étude, node-lock) donne signe de vie
            threading.Thread(target=self._progress, args=(token, done), daemon=True).start()
        try:
            content, error = self.coach._run_tool(name, args)
        finally:
            done.set()
        return {"content": [{"type": "text", "text": content}], "isError": bool(error)}

    def _progress(self, token, done: threading.Event) -> None:
        step = 0
        while not done.wait(PROGRESS_EVERY):
            step += 1
            self.write({"jsonrpc": "2.0", "method": "notifications/progress",
                        "params": {"progressToken": token, "progress": step,
                                   "message": "Analyzer consulte le solveur…"}})


def serve(library_factory: Callable, stdin: TextIO, stdout: TextIO) -> None:
    """Lit les messages ligne par ligne jusqu'à la fin de stdin."""
    lock = threading.Lock()

    def write(message: dict) -> None:
        with lock:
            stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
            stdout.flush()

    server = Server(library_factory, write)
    try:
        for line in stdin:
            if not line.strip():
                continue
            try:
                message = json.loads(line)
            except ValueError:
                write({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "JSON invalide"}})
                continue
            server.handle(message)
    finally:
        server.close()


def _protocol_streams() -> tuple[TextIO, TextIO]:
    """La sortie standard réservée au protocole : le descripteur 1 est redirigé vers la sortie d'erreur (pour les
    print et les programmes lancés), le protocole écrit sur une copie de l'original."""
    out = os.fdopen(os.dup(1), "w", encoding="utf-8", newline="\n")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return open(sys.stdin.fileno(), "r", encoding="utf-8", closefd=False), out


def config(folder: str, hero: Optional[str]) -> str:
    """La configuration à copier pour Claude Desktop et la commande pour Claude Code, avec les chemins de cette
    machine."""
    root = Path(__file__).resolve().parents[2]
    hands = Path(folder).resolve()
    args = ["-m", "analyzer", "mcp", "--dossier", str(hands)] + (["--hero", hero] if hero else [])
    desktop = {"mcpServers": {NAME: {"command": sys.executable, "args": args, "env": {"PYTHONPATH": str(root)}}}}
    quote = (lambda s: '"' + s + '"') if os.name == "nt" else shlex.quote
    code = " ".join(["claude", "mcp", "add", "--transport", "stdio", "--scope", "user", "--env",
                     quote(f"PYTHONPATH={root}"), NAME, "--", quote(sys.executable)] + [quote(a) for a in args])
    return (
        "Claude Desktop : Réglages > Développeur > Modifier la configuration, puis ajoute dans "
        "claude_desktop_config.json (ou fusionne avec tes « mcpServers ») :\n\n"
        + json.dumps(desktop, ensure_ascii=False, indent=2)
        + "\n\nRedémarre Claude Desktop : les outils d'Analyzer apparaissent dans la conversation.\n\n"
        "Claude Code (terminal) :\n\n" + code + "\n")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m analyzer mcp",
                                     description="Serveur MCP du coach, pour l'application Claude ou Claude Code.")
    parser.add_argument("-d", "--dossier", default="hands", help="dossier des historiques (défaut : hands/)")
    parser.add_argument("--hero", help="ton pseudo (détecté automatiquement via le tag Hero)")
    parser.add_argument("--config", action="store_true", help="affiche la configuration à copier, sans lancer le serveur")
    args = parser.parse_args(argv)
    if args.config:
        print(config(args.dossier, args.hero))
        return 0
    stdin, stdout = _protocol_streams()

    def library():
        from .library import Library
        start = time.time()
        lib = Library(args.dossier, args.hero)
        print(f"Analyzer : {len(lib.hands)} mains chargées en {time.time() - start:.1f} s", file=sys.stderr, flush=True)
        return lib
    serve(library, stdin, stdout)
    return 0
