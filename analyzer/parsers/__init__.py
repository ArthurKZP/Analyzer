"""Détection du format et chargement des historiques."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from ..models import Hand
from . import betclic, unibet, winamax

PARSERS = [betclic, winamax, unibet]
EXTENSIONS = {".txt", ".log", ".hh"}


def parse_text(text: str) -> list[Hand]:
    for parser in PARSERS:
        if parser.looks_like(text):
            return list(parser.parse(text))
    raise ValueError("Format d'historique non reconnu (sites supportés : Betclic, Winamax, Unibet).")


def _iter_files(paths: Iterable[str | Path]) -> Iterable[Path]:
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            yield from sorted(p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in EXTENSIONS)
        elif path.is_file():
            yield path
        else:
            raise FileNotFoundError(f"Introuvable : {path}")


def load_hands(paths: Iterable[str | Path]) -> list[Hand]:
    """Charge fichiers et dossiers, dédoublonne par Hand ID et trie chronologiquement."""
    hands: dict[str, Hand] = {}
    for file in _iter_files(paths):
        text = file.read_text(encoding="utf-8-sig", errors="replace")
        try:
            parsed = parse_text(text)
        except ValueError:
            continue
        for hand in parsed:
            hands.setdefault(f"{hand.site}:{hand.hand_id}", hand)
    return sorted(hands.values(), key=lambda h: (h.date, h.hand_id))
