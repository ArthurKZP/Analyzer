"""Détection du format et chargement des historiques."""
from __future__ import annotations

import functools
import hashlib
import io
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

from .. import store
from ..models import Hand
from . import betclic, unibet, winamax

PARSERS = [betclic, winamax, unibet]
EXTENSIONS = {".txt", ".log", ".hh"}
MAX_ZIP_FILES = 20_000         # historiques dans une archive
MAX_ZIP_SIZE = 1024 ** 3       # 1 Go une fois décompressée (garde-fou contre les archives piégées)
MAX_ZIP_DEPTH = 2              # archives dans l'archive


def parse_text(text: str, on_hands: Optional[Callable[[int], None]] = None) -> list[Hand]:
    """Les mains d'un historique (ValueError : format non reconnu) ; on_hands(n) : n mains lues, toutes les 100."""
    for parser in PARSERS:
        if parser.looks_like(text):
            if on_hands is None:
                return list(parser.parse(text))
            hands = []
            for hand in parser.parse(text):
                hands.append(hand)
                if len(hands) % 100 == 0:
                    on_hands(len(hands))
            return hands
    raise ValueError("Format d'historique non reconnu (sites supportés : Betclic, Winamax, Unibet).")


def count_hands(text: str) -> int:
    """Combien de mains compte un historique, vite (sans le lire) : l'avancement d'un import ; 0 si format inconnu."""
    for parser in PARSERS:
        if parser.looks_like(text):
            return parser.count_hands(text)
    return 0


def decode(data: bytes) -> str:
    """Texte d'un historique : UTF-8 (avec ou sans BOM), UTF-16 avec BOM, sinon Windows-1252."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


@dataclass
class ZipContent:
    """Les historiques d'une archive (chemin dans l'archive, texte), les autres fichiers laissés de côté et ceux
    qui ne se lisent pas (chiffrés, compression inconnue)."""
    files: list[tuple[str, str]] = field(default_factory=list)
    ignored: int = 0
    unreadable: list[str] = field(default_factory=list)


def read_zip(data: bytes, prefix: str = "", content: Optional[ZipContent] = None, depth: int = 0,
             budget: Optional[list[int]] = None) -> ZipContent:
    """Les historiques (.txt, .log, .hh) d'une archive zip, dans tous ses dossiers et dans les archives qu'elle
    contient. ValueError si l'archive est illisible ou trop grosse."""
    content = content if content is not None else ZipContent()
    budget = budget if budget is not None else [MAX_ZIP_SIZE]
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError) as exc:
        raise ValueError("archive zip illisible") from exc
    with archive:
        for info in archive.infolist():
            path = info.filename.replace("\\", "/")
            base = path.rsplit("/", 1)[-1]
            if info.is_dir() or path.startswith("__MACOSX/") or base.startswith("."):
                continue  # dossiers, métadonnées de macOS, fichiers cachés
            suffix = Path(base).suffix.lower()
            nested = suffix == ".zip" and depth < MAX_ZIP_DEPTH
            if suffix not in EXTENSIONS and not nested:
                content.ignored += 1
                continue
            if len(content.files) >= MAX_ZIP_FILES:
                raise ValueError(f"plus de {MAX_ZIP_FILES} historiques dans l'archive : découpe-la")
            if info.file_size > budget[0]:
                raise ValueError(f"archive trop volumineuse une fois décompressée ({MAX_ZIP_SIZE // 1024 ** 3} Go maximum)")
            try:
                raw = archive.read(info)
            except (RuntimeError, NotImplementedError, zipfile.BadZipFile, zlib.error, OSError):
                content.unreadable.append(prefix + path)  # chiffré, compression non prise en charge, abîmé
                continue
            budget[0] -= len(raw)
            if nested:
                try:
                    read_zip(raw, f"{prefix}{path}/", content, depth + 1, budget)
                except ValueError:
                    content.unreadable.append(prefix + path)
            else:
                content.files.append((prefix + path, decode(raw)))
    return content


def _iter_files(paths: Iterable[str | Path]) -> Iterable[Path]:
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            yield from sorted(p for p in path.rglob("*")
                              if p.is_file() and p.suffix.lower() in EXTENSIONS | {".zip"})
        elif path.is_file():
            yield path
        else:
            raise FileNotFoundError(f"Introuvable : {path}")


def _texts(file: Path) -> Iterable[str]:
    if file.suffix.lower() != ".zip":
        yield file.read_text(encoding="utf-8-sig", errors="replace")
        return
    try:
        yield from (text for _, text in read_zip(file.read_bytes()).files)
    except ValueError:
        return


@functools.lru_cache(maxsize=1)
def _code_version() -> str:
    """Empreinte du code de lecture : le changer relit tous les fichiers (cache des mains lues, base de données)."""
    root = Path(__file__).parent
    digest = hashlib.sha1()
    for source in sorted(root.glob("*.py")) + [root.parent / "models.py"]:
        digest.update(source.read_bytes())
    return digest.hexdigest()[:12]


def _parse_file(file: Path) -> list[Hand]:
    out: list[Hand] = []
    for text in _texts(file):
        try:
            out += parse_text(text)
        except ValueError:
            continue
    return out


def _cached_file(file: Path) -> list[Hand]:
    """Les mains d'un fichier, relues seulement s'il a changé (taille, date) ou si le code de lecture a changé."""
    global _CODE
    try:
        stat = file.stat()
    except OSError:
        return []
    if _CODE is None:
        _CODE = _code_version()
    key = str(file.resolve())
    stamp = [_CODE, stat.st_size, stat.st_mtime_ns]
    known = store.get("mains", key)
    if known is not None and known[0] == stamp:
        return known[1]
    hands = _parse_file(file)
    store.put("mains", key, (stamp, hands))
    return hands


_CODE = None


def load_hands(paths: Iterable[str | Path]) -> list[Hand]:
    """Charge fichiers, dossiers et archives zip, dédoublonne par Hand ID et trie chronologiquement. Les mains lues
    sont gardées par fichier (analyzer.store) : un fichier déjà lu et inchangé ne se relit pas."""
    hands: dict[str, Hand] = {}
    for file in _iter_files(paths):
        for hand in _cached_file(file):
            hands.setdefault(f"{hand.site}:{hand.hand_id}", hand)
    return sorted(hands.values(), key=lambda h: (h.date, h.hand_id))
