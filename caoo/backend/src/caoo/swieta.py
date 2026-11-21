"""Demonstracyjny kalendarz świąt i wyjątki z konfiguracji V1."""

from datetime import date, timedelta
from functools import cache


def _wielkanoc(rok: int) -> date:
    """Gregoriański algorytm Meeusa–Jonesa–Butchera."""
    a = rok % 19
    b, c = divmod(rok, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    miesiac, dzien = divmod(h + l - 7 * m + 114, 31)
    return date(rok, miesiac, dzien + 1)


@cache
def _swieta_roku(rok: int) -> frozenset[date]:
    stale = [(1, 1), (1, 6), (5, 1), (5, 3), (8, 15), (11, 1), (11, 11), (12, 25), (12, 26)]
    if rok >= 2025:
        stale.append((12, 24))
    wielkanoc = _wielkanoc(rok)
    ruchome = {wielkanoc + timedelta(days=przesuniecie) for przesuniecie in (0, 1, 49, 60)}
    return frozenset(ruchome | {date(rok, miesiac, dzien) for miesiac, dzien in stale})


def dni_wolne(terminy: dict, od: int, do: int) -> frozenset[date]:
    """Kalendarz dla lat od–do i wszystkie wyjątki zapisane przez administratora."""
    swieta = frozenset(d for rok in range(od, do + 1) for d in _swieta_roku(rok))
    dodane = {date.fromisoformat(str(d)) for d in terminy.get("dni_wolne", [])}
    usuniete = {date.fromisoformat(str(d)) for d in terminy.get("dni_robocze", [])}
    return (swieta | dodane) - usuniete
