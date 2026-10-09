"""La mémoire d'une grosse base de mains et le ramasse-miettes de Python (gc).

Des dizaines de milliers de mains font des millions d'objets en mémoire (mains, actions, places). Le ramasse-miettes
les parcourt tous à chacun de ses passages complets : pendant un chargement, où ces passages se rapprochent à mesure que
les objets s'accumulent, il prend plus de la moitié du temps ; ensuite, il ralentit chaque calcul.

loading() : pendant un chargement, le ramasse-miettes attend ; à la fin, ce qui est en mémoire (les mains lues) est
mis à l'écart de ses passages (gc.freeze), après avoir rendu les objets laissés par le chargement d'avant (gc.unfreeze,
puis un passage complet ; rien à rendre au premier chargement, ni quand le chargement n'a fait qu'ajouter des mains :
state.release = False).

Les objets mis à l'écart restent libérés normalement quand plus rien ne s'y réfère ; seuls ceux qui se tiennent entre
eux (un cycle) attendent le prochain chargement qui remplace les mains. Après un import qui ajoute des mains, un passage
coûterait une demi-seconde avec 140 000 mains pour rien : les pages calculées n'en laissent pas.
"""
from __future__ import annotations

import gc
import threading
from contextlib import contextmanager
from typing import Iterator

_lock = threading.Lock()
_loading = 0
_was_enabled = True
_release = False  # un des chargements en cours remplace des mains : ce qu'il laisse sera rendu


class Loading:
    """L'état d'un chargement : release, rendre ce que le chargement d'avant laisse (vrai par défaut)."""

    def __init__(self) -> None:
        self.release = True


@contextmanager
def loading() -> Iterator[Loading]:
    """Le ramasse-miettes en pause le temps d'un chargement (plusieurs à la fois : jusqu'à la fin du dernier), puis
    les objets en mémoire mis à l'écart de ses passages."""
    global _loading, _was_enabled, _release
    with _lock:
        if _loading == 0:
            _was_enabled = gc.isenabled()
            gc.disable()
        _loading += 1
    state = Loading()
    try:
        yield state
    finally:
        with _lock:
            _release = _release or state.release
            _loading -= 1
            if _loading == 0:
                if _release and gc.get_freeze_count():  # ce que laisse un chargement d'avant (mains remplacées)
                    gc.unfreeze()
                    gc.collect()
                _release = False
                gc.freeze()
                if _was_enabled:
                    gc.enable()
