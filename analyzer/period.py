"""Période d'analyse : toutes les mains, les N dernières (de chaque format : heads-up, tables à plusieurs), les N
derniers jours, ou d'une date à une autre.

Elle se choisit pour chaque espace (toi, chaque élève) et reste gardée dans la base (réglage « periode:<espace> ») ;
toutes les analyses de l'espace s'y limitent : bilan, Leakfinding, préflop, adversaires, rapports.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from . import db
from .models import Hand

ALL = {"kind": "all"}
MAX_HANDS = 10_000_000
MAX_DAYS = 36_500


def _int(value: object, top: int) -> int:
    if isinstance(value, bool):
        raise ValueError("Nombre invalide.")
    try:
        n = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ValueError("Nombre invalide.") from None
    if not 0 < n <= top:
        raise ValueError("Nombre hors limites.")
    return n


def _day(value: object) -> Optional[str]:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        raise ValueError("Date invalide (aaaa-mm-jj).") from None


def clean(raw: object) -> dict:
    """La période demandée, vérifiée : {"kind": "all"}, {"kind": "last", "n"}, {"kind": "days", "days"} ou
    {"kind": "range", "from", "to"} (dates aaaa-mm-jj, l'une des deux peut manquer) ; ValueError sinon."""
    if not isinstance(raw, dict):
        raise ValueError("Période invalide.")
    kind = raw.get("kind") or "all"
    if kind == "all":
        return dict(ALL)
    if kind == "last":
        return {"kind": "last", "n": _int(raw.get("n"), MAX_HANDS)}
    if kind == "days":
        return {"kind": "days", "days": _int(raw.get("days"), MAX_DAYS)}
    if kind == "range":
        start, end = _day(raw.get("from")), _day(raw.get("to"))
        if start is None and end is None:
            raise ValueError("Choisis au moins une date.")
        if start and end and start > end:
            start, end = end, start
        return {"kind": "range", "from": start, "to": end}
    raise ValueError("Période inconnue.")


def _key(space: str) -> str:
    return f"periode:{space}"


def load(space: str) -> dict:
    """La période gardée pour cet espace (toutes les mains si rien, ou si le réglage ne se lit plus)."""
    try:
        return clean(db.current().setting(_key(space), ALL))
    except ValueError:
        return dict(ALL)


def save(space: str, period: dict) -> None:
    db.current().set_setting(_key(space), period)


def bounds(period: dict, today: Optional[date] = None) -> tuple[Optional[date], Optional[date]]:
    """Les jours de début et de fin (compris) d'une période en dates ; (None, None) sinon."""
    if period["kind"] == "days":
        today = today or date.today()
        return today - timedelta(days=period["days"] - 1), today
    if period["kind"] == "range":
        return tuple(date.fromisoformat(d) if d else None for d in (period["from"], period["to"]))  # type: ignore
    return None, None


def select(hands: list[Hand], period: dict, today: Optional[date] = None) -> list[Hand]:
    """Les mains de la période, dans leur ordre (celui des dates) ; « last » : les N dernières de cette liste."""
    if period["kind"] == "all":
        return hands
    if period["kind"] == "last":
        return hands[-period["n"]:]
    start, end = bounds(period, today)
    return [h for h in hands if (start is None or h.date.date() >= start) and (end is None or h.date.date() <= end)]


def _num(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def _fr(day: str) -> str:
    return date.fromisoformat(day).strftime("%d/%m/%Y")


def label(period: dict) -> str:
    """« Toutes les mains », « 1 000 dernières mains », « 30 derniers jours », « Du 01/09/2026 au 30/09/2026 »…"""
    kind = period["kind"]
    if kind == "last":
        return "La dernière main" if period["n"] == 1 else f"{_num(period['n'])} dernières mains"
    if kind == "days":
        return "Aujourd'hui" if period["days"] == 1 else f"{_num(period['days'])} derniers jours"
    if kind == "range":
        start, end = period["from"], period["to"]
        if start and end:
            return f"Le {_fr(start)}" if start == end else f"Du {_fr(start)} au {_fr(end)}"
        return f"Depuis le {_fr(start)}" if start else f"Jusqu'au {_fr(end)}"
    return "Toutes les mains"


def describe(period: dict) -> str:
    """La période en une phrase, pour l'en-tête d'un rapport (rien pour toutes les mains)."""
    if period["kind"] == "all":
        return ""
    if period["kind"] == "last":
        return f"Période : {label(period).lower()} de chaque format (heads-up, tables à plusieurs)."
    return f"Période : {label(period)[0].lower() + label(period)[1:]}."
