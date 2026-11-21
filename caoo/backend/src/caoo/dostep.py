"""Role, uprawnienia i historia zmian (#10, V1-3).

Mechanizm z ``caoo_core.auth`` (konta demo, akcje ról, reguły dostępu, dziennik zmian).
Wersja dokłada tylko rekord pracownika do reguł: pole ``oceniajacy`` to bezpośredni przełożony
i osoby, które go zastępują w danym dniu (``Profil.zastepstwa``) – dzięki temu oceniający
widzi także osoby, które ocenia w zastępstwie.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

from fastapi import HTTPException, Request
from caoo_core.auth import Autoryzacja, Uzytkownik

from .kadry import MockKadry, Profil


def oceniajacy(kadry: MockKadry, p: Profil, dzien: date) -> list[str]:
    """Kto ocenia osobę w danym dniu: przełożony i jego zastępcy (bez samej osoby)."""
    if p.przelozony_id is None:
        return []
    wynik = [p.przelozony_id]
    przelozony = kadry.profil(p.przelozony_id)
    if przelozony is not None:
        wynik += [z.zastepca_id for z in przelozony.zastepstwa if z.od <= dzien <= z.do]
    return [x for x in dict.fromkeys(wynik) if x != p.id]


def dzis(request: Request) -> date:
    f: Callable[[], date] | None = getattr(request.app.state, "dzis", None)
    return f() if f else date.today()


def rekord(request: Request, p: Profil) -> dict:
    return {"id": p.id, "oceniajacy": oceniajacy(request.app.state.kadry, p, dzis(request))}


def pracownik(request: Request, uz: Uzytkownik, pracownik_id: str, akcja: str) -> Profil:
    """Profil, jeśli użytkownik ma akcję i dostęp do tej osoby; inaczej 404/403."""
    p = request.app.state.kadry.profil(pracownik_id)
    if p is None:
        raise HTTPException(404, "Nie ma takiego pracownika")
    auth: Autoryzacja = request.app.state.auth
    auth.sprawdz(uz, "pracownik", rekord(request, p), akcja)
    return p


def widoczni(request: Request, uz: Uzytkownik, akcja: str) -> set[str]:
    auth: Autoryzacja = request.app.state.auth
    kadry: MockKadry = request.app.state.kadry
    return {
        s.id for s in kadry.lista().pracownicy
        if (p := kadry.profil(s.id)) is not None and auth.moze(uz, "pracownik", rekord(request, p), akcja)
    }
