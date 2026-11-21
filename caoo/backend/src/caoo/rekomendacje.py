"""Rekomendacje i plan rozwoju (#23, V1-16; spec #2).

Na grafie kompetencji z #20: z luk (poziom roboczy wymagany − poziom zaobserwowany) reguła dobiera
działania z katalogu. Przy rekomendacji widać zadanie, kompetencję, dowody, poziom oczekiwany
i zaobserwowany; każdy element ma oznaczenie „z danych / z reguły / propozycja” (status węzła).
Uwzględnia mocne strony i realizację zaleceń z poprzedniej oceny. Oceniający ustala termin, osobę
wspierającą i miernik; treść wniosków do IPRZ pisze człowiek. Niski wynik nie generuje wniosków
o przeciążeniu – najwyżej pytanie do rozmowy. Bez LLM.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Literal

import yaml
from fastapi import APIRouter, HTTPException, Request
from caoo_core.zrodla import SourceRef
from pydantic import BaseModel, Field

from .cykl import Cykl, _wczytaj
from .graf import zbuduj_graf
from .graf_model import GrafOceny, Wezel
from .kadry import Profil

Oznaczenie = Literal["z danych", "z reguły", "propozycja"]


# --- konfiguracja -------------------------------------------------------------------


class _Dobor(BaseModel):
    luka_od: int
    dzialania: list[str]


class _Regula(BaseModel):
    id: str
    adnotacje: list[SourceRef]


class KonfiguracjaRekomendacji(BaseModel):
    wersja: str
    oznaczenia: dict[str, Oznaczenie]
    dobor_wedlug_luki: list[_Dobor]
    dobor_wedlug_kompetencji: dict[str, list[str]] = {}
    mocna_strona_od: int
    pytanie_przy_niskim_wyniku: str
    struktura_wnioskow_iprz: list[str]
    regula: _Regula

    @classmethod
    def z_pliku(cls, plik: str | Path) -> KonfiguracjaRekomendacji:
        return cls.model_validate(yaml.safe_load(Path(plik).read_text(encoding="utf-8")))


# --- model --------------------------------------------------------------------------


class Element(BaseModel):
    """Element rekomendacji z oznaczeniem pochodzenia (kryterium #23)."""

    nazwa: str
    oznaczenie: Oznaczenie
    zrodlo: str
    wartosc: int | None = None
    adnotacje: list[SourceRef] = Field(default_factory=list)


class Rekomendacja(BaseModel):
    id: str
    kompetencja: Element
    zadania: list[Element]
    dowody: list[Element]
    poziom_oczekiwany: Element
    poziom_zaobserwowany: Element
    luka: int
    dzialania: list[Element]
    oznaczenie: Oznaczenie = "propozycja"
    """Rekomendacja jako całość jest propozycją do decyzji oceniającego."""
    pytanie_do_rozmowy: str | None = None
    """Przy niskim wyniku: pytanie o obciążenie i wsparcie – nie wniosek."""


class MocnaStrona(BaseModel):
    kompetencja: Element
    poziom_zaobserwowany: Element
    propozycja: Element


class RealizacjaZalecenia(BaseModel):
    zalecenie: Element
    kompetencje: list[str]
    biezace_punkty: dict[str, int | None]
    status: Literal["DO_OMOWIENIA"] = "DO_OMOWIENIA"
    """Realizację ocenia człowiek w rozmowie – system nie rozstrzyga."""


class Porownanie(BaseModel):
    poprzednia_data: date
    poprzednia_srednia: str
    biezaca_srednia: str | None
    ostrzezenie: str | None


class Rekomendacje(BaseModel):
    cykl_id: int
    wersja: str
    rekomendacje: list[Rekomendacja]
    mocne_strony: list[MocnaStrona]
    realizacja_zalecen: list[RealizacjaZalecenia]
    porownanie: Porownanie | None
    struktura_wnioskow_iprz: list[str]
    tresc_wnioskow_iprz: None = None
    """Zawsze None: treść wniosków do IPRZ pisze człowiek (PUT /plan-rozwoju)."""
    regula: _Regula


class PozycjaPlanu(BaseModel):
    kompetencja_id: str = Field(min_length=1, max_length=80)
    dzialanie_id: str = Field(min_length=1, max_length=80)
    termin: date
    osoba_wspierajaca: str = Field(min_length=1, max_length=200)
    miernik: str = Field(min_length=1, max_length=500)


class PlanRozwoju(BaseModel):
    pozycje: list[PozycjaPlanu] = Field(default_factory=list, max_length=30)
    wnioski_iprz: str | None = Field(default=None, max_length=6000)
    """Treść wniosków do IPRZ – pisze oceniający."""


# --- logika -------------------------------------------------------------------------


def _element(w: Wezel, konf: KonfiguracjaRekomendacji) -> Element:
    return Element(nazwa=w.nazwa, oznaczenie=konf.oznaczenia[w.status], zrodlo=w.zrodlo,
                   wartosc=w.wartosc, adnotacje=w.adnotacje)


def _srednia(punkty: list[int]) -> str | None:
    if not punkty:
        return None
    return str((Decimal(sum(punkty)) / len(punkty)).quantize(Decimal("0.01"), ROUND_HALF_UP))


def rekomendacje(c: Cykl, p: Profil, graf: GrafOceny, konf: KonfiguracjaRekomendacji) -> Rekomendacje:
    wezly = {w.id: w for w in [*graf.katalog.wezly(), *graf.wezly]}
    kompetencje = {k.id.split(":", 1)[1]: k for k in graf.katalog.kompetencje}
    dzialania = {d.id.split(":", 1)[1]: d for d in graf.katalog.dzialania}
    podstawy = {pr.kompetencja_id.split(":", 1)[1]: pr.podstawy for pr in graf.propozycje}
    zadania_dla: dict[str, list[str]] = {}
    for r in graf.relacje:
        if r.typ == "odpowiada" and r.z.startswith("zadanie:") and not r.z.endswith(":brak"):
            zadania_dla.setdefault(r.do.split(":", 1)[1], []).append(r.z)

    wynik_rek: list[Rekomendacja] = []
    mocne: list[MocnaStrona] = []
    for kid, k in kompetencje.items():
        luka_w = wezly.get(f"luka:{kid}")
        aktualny = wezly.get(f"aktualny:{kid}")
        wymagany = wezly.get(f"wymagany:{kid}")
        if luka_w is None or aktualny is None or wymagany is None:
            continue  # kompetencja nie jest w tym cyklu ani w zadaniach
        punkty = c.oceny.get(kid)
        if punkty is not None and punkty >= konf.mocna_strona_od:
            mocne.append(MocnaStrona(
                kompetencja=_element(k, konf), poziom_zaobserwowany=_element(aktualny, konf),
                propozycja=Element(nazwa="Dzielenie się wiedzą i doświadczeniem w zespole (np. jako osoba wspierająca)",
                                   oznaczenie="propozycja", zrodlo="Reguła rekomendacji – mocna strona"),
            ))
        if not luka_w.wartosc:
            continue
        ids: list[str] = []
        for d in konf.dobor_wedlug_luki:
            if luka_w.wartosc >= d.luka_od:
                ids = list(d.dzialania)
                break
        ids += [x for x in konf.dobor_wedlug_kompetencji.get(kid, []) if x not in ids]
        dz = [Element(nazwa=dzialania[x].nazwa, oznaczenie="z reguły", zrodlo=f"Katalog działań · reguła {konf.regula.id}",
                      adnotacje=dzialania[x].adnotacje) for x in ids if x in dzialania]
        dowody = [_element(wezly[x], konf) for x in podstawy.get(kid, []) if x.startswith("dowod:") and x in wezly]
        wynik_rek.append(Rekomendacja(
            id=f"rek:{kid}", kompetencja=_element(k, konf),
            zadania=[_element(wezly[z], konf) for z in zadania_dla.get(kid, [])],
            dowody=dowody, poziom_oczekiwany=_element(wymagany, konf),
            poziom_zaobserwowany=_element(aktualny, konf), luka=luka_w.wartosc, dzialania=dz,
            pytanie_do_rozmowy=konf.pytanie_przy_niskim_wyniku.format(kompetencja=k.nazwa) if punkty is not None else None,
        ))

    # Realizacja zaleceń z poprzedniej oceny: dowody (zalecenia) i kompetencje, do których je dopasowano.
    realizacja = []
    for w in graf.wezly:
        if w.typ != "dowod" or w.id.endswith(":brak"):
            continue
        kids = [kid for kid, ids in podstawy.items() if w.id in ids]
        realizacja.append(RealizacjaZalecenia(
            zalecenie=_element(w, konf), kompetencje=[kompetencje[x].nazwa for x in kids],
            biezace_punkty={kompetencje[x].nazwa: c.oceny.get(x) for x in kids},
        ))

    porownanie = None
    if p.poprzednie_oceny:
        ost = max(p.poprzednie_oceny, key=lambda o: o.data_sporzadzenia)
        powody = ["poprzednia ocena w danych kadrowych nie ma listy kryteriów, więc nie da się potwierdzić, "
                  "że oba okresy oceniano tymi samymi kryteriami"]
        if c.dodatkowe:
            powody.append("bieżący cykl ma kryteria dodatkowe: " + ", ".join(k.nazwa for k in c.dodatkowe))
        ponowienie = getattr(c, "ostatnie_ponowienie_kryteriow", None)  # #16; starsze bazy go nie mają
        if ponowienie is not None and ponowienie() is not None:
            powody.append("w bieżącym okresie ponownie ustalono kryteria (zmiana stanowiska, § 9)")
        porownanie = Porownanie(
            poprzednia_data=ost.data_sporzadzenia, poprzednia_srednia=f"{ost.srednia:.2f}",
            biezaca_srednia=_srednia(list(c.oceny.values())),
            ostrzezenie="Porównanie orientacyjne – " + "; ".join(powody) + ".",
        )

    return Rekomendacje(
        cykl_id=c.id, wersja=konf.wersja, rekomendacje=wynik_rek, mocne_strony=mocne,
        realizacja_zalecen=realizacja, porownanie=porownanie,
        struktura_wnioskow_iprz=konf.struktura_wnioskow_iprz, regula=konf.regula,
    )


# --- zapis planu --------------------------------------------------------------------


def _tabela(baza: sqlite3.Connection) -> None:
    with baza:
        baza.execute("CREATE TABLE IF NOT EXISTS plany_rozwoju_v1 (cykl_id INTEGER PRIMARY KEY, dane TEXT NOT NULL)")


def _plan(baza: sqlite3.Connection, cykl_id: int) -> PlanRozwoju:
    _tabela(baza)
    w = baza.execute("SELECT dane FROM plany_rozwoju_v1 WHERE cykl_id = ?", (cykl_id,)).fetchone()
    return PlanRozwoju.model_validate_json(w[0]) if w else PlanRozwoju()


# --- API ----------------------------------------------------------------------------


def router(konf: KonfiguracjaRekomendacji, auth) -> APIRouter:
    from .dostep import pracownik as _pracownik

    r = APIRouter(prefix="/api/cykle", tags=["rekomendacje i plan rozwoju (#23)"])

    def kontekst(request: Request, cykl_id: int, uz, akcja: str) -> tuple[Cykl, Profil, GrafOceny]:
        c = _wczytaj(request.app.state.baza, cykl_id)
        if c is None:
            raise HTTPException(404, "Nie ma takiego cyklu")
        p = _pracownik(request, uz, c.pracownik_id, akcja)
        w = next((x for x in request.app.state.wersje if x.wersja == c.wersja_regul), None)
        if w is None:
            raise HTTPException(409, f"Brak wersji reguł {c.wersja_regul}, z którą rozpoczęto cykl.")
        graf = zbuduj_graf(c, p, w, request.app.state.konfiguracja_grafu, request.app.state.katalogi_grafu[w.wersja])
        return c, p, graf

    @r.get("/{cykl_id}/rekomendacje")
    def odczyt(cykl_id: int, request: Request, uz=auth.wymagaj("ocena.czytaj")) -> Rekomendacje:
        """Rekomendacje z luk w grafie: zadanie, kompetencja, dowody, poziom oczekiwany i zaobserwowany,
        działania z katalogu; mocne strony; realizacja zaleceń; ostrzeżenie przy porównaniu okresów."""
        return rekomendacje(*kontekst(request, cykl_id, uz, "ocena.czytaj"), konf)

    @r.get("/{cykl_id}/plan-rozwoju")
    def plan(cykl_id: int, request: Request, uz=auth.wymagaj("ocena.czytaj")) -> PlanRozwoju:
        kontekst(request, cykl_id, uz, "ocena.czytaj")
        return _plan(request.app.state.baza, cykl_id)

    @r.put("/{cykl_id}/plan-rozwoju")
    def zapisz_plan(
        cykl_id: int, body: PlanRozwoju, request: Request, uz=auth.wymagaj("cykl.prowadz")
    ) -> PlanRozwoju:
        """Plan oceniającego: działanie, termin, osoba wspierająca i miernik; wnioski do IPRZ pisze człowiek."""
        c, _, graf = kontekst(request, cykl_id, uz, "cykl.prowadz")
        kompetencje = {k.id.split(":", 1)[1] for k in graf.katalog.kompetencje}
        dzialania = {d.id.split(":", 1)[1] for d in graf.katalog.dzialania}
        bledy = [f"Nieznana kompetencja {x.kompetencja_id}." for x in body.pozycje if x.kompetencja_id not in kompetencje]
        bledy += [f"Działania {x.dzialanie_id} nie ma w katalogu." for x in body.pozycje if x.dzialanie_id not in dzialania]
        if bledy:
            raise HTTPException(422, {"komunikat": "Niepoprawny plan rozwoju.", "bledy": bledy})
        baza = request.app.state.baza
        _tabela(baza)
        with baza:
            baza.execute(
                "INSERT INTO plany_rozwoju_v1 (cykl_id, dane) VALUES (?, ?) "
                "ON CONFLICT(cykl_id) DO UPDATE SET dane = excluded.dane",
                (c.id, body.model_dump_json()),
            )
        return body

    return r
