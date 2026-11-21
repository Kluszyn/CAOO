"""Wybór procesu (pierwsza ocena / ocena okresowa) i arkusza A/B/C (#11).

Czysta funkcja: dane z API kadrowego + wersja reguł → wybór z przesłankami. Każda przesłanka
niesie adnotacje reguły. Gdy danych brakuje, wynik to „nie można ustalić” z listą pól – bez
zgadywania. Pierwsza ocena to osobny proces (art. 37), nie „pierwsza ocena okresowa”.
"""

from __future__ import annotations

from typing import Literal

from caoo_core.zrodla import SourceRef
from pydantic import BaseModel

from .kadry import Profil
from .reguly import WersjaRegul
from .terminy import TerminOceny, termin_oceny

RodzajProcesu = Literal["OKRESOWA", "PIERWSZA", "NIE_MOZNA_USTALIC"]


class Przeslanka(BaseModel):
    opis: str
    spelniona: bool | None
    """None: brak danych do sprawdzenia."""
    regula: str
    adnotacje: list[SourceRef]


class WyborProcesu(BaseModel):
    proces: RodzajProcesu
    arkusz: Literal["A", "B", "C"] | None
    przeslanki: list[Przeslanka]
    braki: list[str]
    komunikat: str
    wersja_regul: str
    termin_oceny: TerminOceny | None = None


def wybierz(p: Profil, reguly: WersjaRegul) -> WyborProcesu:
    przeslanki: list[Przeslanka] = []

    def dodaj(opis: str, spelniona: bool | None, regula: str) -> None:
        przeslanki.append(
            Przeslanka(opis=opis, spelniona=spelniona, regula=regula, adnotacje=reguly.regula(regula).adnotacje)
        )

    def wynik(proces: RodzajProcesu, komunikat: str, arkusz=None, braki=()) -> WyborProcesu:
        return WyborProcesu(
            proces=proces, arkusz=arkusz, przeslanki=przeslanki, braki=list(braki),
            komunikat=komunikat, wersja_regul=reguly.wersja,
            termin_oceny=termin_oceny(p, reguly) if proces == "OKRESOWA" else None,
        )

    z = p.zatrudnienie
    if z is None:
        dodaj("Podstawa zatrudnienia (umowa lub mianowanie) jest znana", None, "proces.okresowa")
        return wynik(
            "NIE_MOZNA_USTALIC",
            "Nie można ustalić rodzaju oceny: brak podstawy zatrudnienia w danych kadrowych.",
            braki=["zatrudnienie.rodzaj (umowa na czas nieokreślony / określony / mianowanie)",
                   "zatrudnienie.data_nawiazania"],
        )

    okresowa = z.rodzaj in ("mianowanie", "umowa_nieokreslony")
    dodaj(
        "Urzędnik służby cywilnej (mianowanie) albo umowa na czas nieokreślony",
        okresowa,
        "proces.okresowa",
    )
    if okresowa:
        return _arkusz(p, dodaj, wynik)

    # umowa na czas określony
    if p.pierwsza_praca_w_sluzbie_cywilnej is None:
        dodaj("Pierwsza praca w służbie cywilnej (umowa z art. 35 ust. 3)", None, "proces.pierwsza")
        return wynik(
            "NIE_MOZNA_USTALIC",
            "Nie można ustalić rodzaju oceny: umowa na czas określony, ale brak informacji, "
            "czy to pierwsza praca w służbie cywilnej.",
            braki=["pierwsza_praca_w_sluzbie_cywilnej"],
        )
    dodaj("Pierwsza praca w służbie cywilnej (umowa z art. 35 ust. 3)", p.pierwsza_praca_w_sluzbie_cywilnej, "proces.pierwsza")
    if p.pierwsza_praca_w_sluzbie_cywilnej:
        return wynik(
            "PIERWSZA",
            "Pierwsza ocena w służbie cywilnej – osobny proces (art. 37–38), bez arkusza A/B/C.",
        )
    dodaj("Zatrudnienie w celu zastępstwa – pierwsza ocena możliwa, nie obowiązkowa", None, "proces.zastepstwo")
    return wynik(
        "NIE_MOZNA_USTALIC",
        "Nie można ustalić rodzaju oceny: umowa na czas określony, która nie jest pierwszą pracą "
        "w służbie cywilnej. Potrzebna informacja o celu zatrudnienia (zastępstwo, art. 37 ust. 2).",
        braki=["cel zatrudnienia na czas określony (zastępstwo?)"],
    )


def _arkusz(p: Profil, dodaj, wynik) -> WyborProcesu:
    if p.kierownik_urzedu:
        dodaj("Stanowisko kierownika urzędu", True, "arkusz.a")
        return wynik("OKRESOWA", "Ocena okresowa na arkuszu A.", arkusz="A")
    dodaj("Stanowisko kierownika urzędu", False, "arkusz.a")
    if p.liczba_podleglych is None:
        dodaj("Bezpośrednie kierowanie pracownikami", None, "arkusz.b")
        return wynik(
            "OKRESOWA",
            "Ocena okresowa, ale nie można ustalić arkusza: brak liczby podległych pracowników.",
            braki=["liczba_podleglych"],
        )
    kieruje = p.liczba_podleglych > 0
    dodaj(f"Bezpośrednie kierowanie pracownikami (podległych: {p.liczba_podleglych})", kieruje, "arkusz.b")
    if kieruje:
        return wynik("OKRESOWA", "Ocena okresowa na arkuszu B.", arkusz="B")
    dodaj("Inne stanowisko niż kierownik urzędu i kierujący pracownikami", True, "arkusz.c")
    return wynik("OKRESOWA", "Ocena okresowa na arkuszu C.", arkusz="C")
