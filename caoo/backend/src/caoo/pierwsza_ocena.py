"""Pierwsza ocena w służbie cywilnej jako osobny proces (#19, V1-12; art. 37–38 ustawy).

Treść ścieżki (sekcje, elementy, adnotacje, pytania do organizatorów) jest w
`konfiguracja/pierwsza-ocena.yaml`. Kod tylko ją składa z profilem pracownika: liczy okno
terminu z art. 38 ust. 1 i wskazuje oceniającego. Status elementu wynika z adnotacji:
BRAK_ŹRÓDŁA, gdy któraś ma tę kategorię. Zasady oceny okresowej nie są tu używane.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Literal

import yaml
from fastapi import APIRouter, HTTPException, Request
from caoo_core.kalendarz import RegulaTerminu, wyznacz_termin
from caoo_core.slad import Regula
from caoo_core.zrodla import Kategoria, SourceRef
from pydantic import BaseModel, computed_field

from .kadry import Profil
from .proces import wybierz
from .reguly import RegulaV1, wersja_na_dzien

StatusElementu = Literal["ŹRÓDŁO", "BRAK_ŹRÓDŁA"]


# --- konfiguracja -------------------------------------------------------------------


class Element(BaseModel):
    id: str
    nazwa: str
    opis: str
    adnotacje: list[SourceRef]

    @computed_field
    @property
    def status(self) -> StatusElementu:
        return "BRAK_ŹRÓDŁA" if any(a.kategoria == Kategoria.BRAK_ZRODLA for a in self.adnotacje) else "ŹRÓDŁO"


class Sekcja(BaseModel):
    id: str
    nazwa: str
    elementy: list[Element]


class PytanieDoOrganizatorow(BaseModel):
    id: str
    tresc: str
    dotyczy: list[str]
    adnotacje: list[SourceRef]


class _Termin(BaseModel):
    od_nawiazania_miesiecy: int
    umowa_miesiecy: int
    przed_koncem_umowy_miesiecy: int


class KonfiguracjaPierwszejOceny(BaseModel):
    wersja: str
    termin: _Termin
    sekcje: list[Sekcja]
    pytania_do_organizatorow: list[PytanieDoOrganizatorow]

    @classmethod
    def z_pliku(cls, plik: str | Path) -> KonfiguracjaPierwszejOceny:
        k = cls.model_validate(yaml.safe_load(Path(plik).read_text(encoding="utf-8")))
        ids = {e.id for e in k.elementy()}
        if len(ids) != len(k.elementy()):
            raise ValueError("powtórzone id elementu pierwszej oceny")
        for p in k.pytania_do_organizatorow:
            if nieznane := set(p.dotyczy) - ids:
                raise ValueError(f"{p.id}: nieznane elementy {sorted(nieznane)}")
        termin = k.element("pierwsza.termin")
        _adnotacja(termin, "art. 38 ust. 1"), _adnotacja(termin, "art. 35 ust. 3")
        return k

    def elementy(self) -> list[Element]:
        return [e for s in self.sekcje for e in s.elementy]

    def element(self, element_id: str) -> Element:
        return next(e for e in self.elementy() if e.id == element_id)


# --- ścieżka dla pracownika ---------------------------------------------------------


class KrokSladu(BaseModel):
    opis: str
    lokalizator: str


class OknoTerminu(BaseModel):
    nawiazanie: date
    najwczesniej: date
    koniec_umowy: date
    najpozniej: date
    slad: list[KrokSladu]


class Oceniajacy(BaseModel):
    przelozony_id: str | None
    przelozony: str | None
    komorka: str
    opis: str


class SciezkaPierwszejOceny(BaseModel):
    pracownik_id: str
    proces: Literal["PIERWSZA"] = "PIERWSZA"
    wersja: str
    komunikat: str
    termin: OknoTerminu | None
    oceniajacy: Oceniajacy
    sekcje: list[Sekcja]
    pytania_do_organizatorow: list[PytanieDoOrganizatorow]


def _adnotacja(e: Element, lokalizator: str) -> SourceRef:
    """Adnotacja elementu po lokalizatorze – ślad nie zależy od kolejności w YAML."""
    for a in e.adnotacje:
        if a.lokalizator == lokalizator:
            return a
    raise ValueError(f"{e.id}: brak adnotacji z lokalizatorem {lokalizator!r}")


def _okno(nawiazanie: date, k: KonfiguracjaPierwszejOceny) -> OknoTerminu:
    el = k.element("pierwsza.termin")
    art38, art35 = _adnotacja(el, "art. 38 ust. 1"), _adnotacja(el, "art. 35 ust. 3")
    reg = Regula(id=el.id, zrodlo=art38)
    reg_umowy = Regula(id=el.id, zrodlo=art35)
    t = k.termin

    def koniec_okresu(miesiecy: int, regula: Regula) -> date:
        """Ostatni dzień okresu liczonego od dnia nawiązania (dzień nawiązania wliczony):
        dzień przed dniem odpowiadającym, a gdy takiego dnia nie ma – ostatni dzień miesiąca."""
        odpowiadajacy = wyznacz_termin(nawiazanie, RegulaTerminu(miesiecy, "miesiace", regula)).data
        if odpowiadajacy.day != nawiazanie.day:  # core przyciął do końca miesiąca
            return odpowiadajacy
        return odpowiadajacy - timedelta(days=1)

    po_8 = koniec_okresu(t.od_nawiazania_miesiecy, reg)
    od = po_8 + timedelta(days=1)
    koniec = koniec_okresu(t.umowa_miesiecy, reg_umowy)
    do = wyznacz_termin(koniec, RegulaTerminu(-t.przed_koncem_umowy_miesiecy, "miesiace", reg)).data
    return OknoTerminu(
        nawiazanie=nawiazanie, najwczesniej=od, koniec_umowy=koniec, najpozniej=do,
        slad=[
            KrokSladu(opis=f"{t.od_nawiazania_miesiecy} miesięcy od nawiązania stosunku pracy {nawiazanie} upływa {po_8}; "
                           f"najwcześniej {od}.",
                      lokalizator=art38.lokalizator),
            KrokSladu(opis=f"Umowa na {t.umowa_miesiecy} miesięcy od {nawiazanie} trwa do {koniec}.",
                      lokalizator=art35.lokalizator),
            KrokSladu(opis=f"Koniec umowy {koniec} − {t.przed_koncem_umowy_miesiecy} miesiąc = {do} (najpóźniej).",
                      lokalizator=art38.lokalizator),
        ],
    )


def sciezka_pierwszej_oceny(p: Profil, k: KonfiguracjaPierwszejOceny) -> SciezkaPierwszejOceny:
    """Ścieżka pierwszej oceny dla osoby, którą wybór procesu (#11) wskazał jako PIERWSZA."""
    return SciezkaPierwszejOceny(
        pracownik_id=p.id,
        wersja=k.wersja,
        komunikat=(
            "Pierwsza ocena w służbie cywilnej – osobny proces (art. 37–38). Elementy bez podstawy "
            "w materiałach mają oznaczenie BRAK_ŹRÓDŁA; zasady oceny okresowej nie są stosowane."
        ),
        termin=_okno(p.zatrudnienie.data_nawiazania, k) if p.zatrudnienie else None,
        oceniajacy=Oceniajacy(
            przelozony_id=p.przelozony_id,
            przelozony=p.przelozony,
            komorka=p.komorka,
            opis=f"Bezpośredni przełożony w porozumieniu z kierującym komórką: {p.komorka}.",
        ),
        sekcje=k.sekcje,
        pytania_do_organizatorow=k.pytania_do_organizatorow,
    )


# --- panel braków -------------------------------------------------------------------


class BrakZrodla(BaseModel):
    id: str
    nazwa: str
    miejsce: str
    uzasadnienie: str


class PanelBrakow(BaseModel):
    braki_zrodel: list[BrakZrodla]
    pytania_do_organizatorow: list[PytanieDoOrganizatorow]


def panel_brakow(reguly: list[RegulaV1], wersja_regul: str, k: KonfiguracjaPierwszejOceny) -> PanelBrakow:
    """Każdy element z adnotacją BRAK_ŹRÓDŁA (reguły wersji i pierwsza ocena) i pytania."""
    braki: list[BrakZrodla] = []
    zrodla = [(r.id, r.nazwa or r.id, f"reguły {wersja_regul}", r.adnotacje) for r in reguly]
    zrodla += [(e.id, e.nazwa, f"pierwsza ocena {k.wersja}", e.adnotacje) for e in k.elementy()]
    for eid, nazwa, miejsce, adnotacje in zrodla:
        for a in adnotacje:
            if a.kategoria == Kategoria.BRAK_ZRODLA:  # ta sama reguła co Element.status
                braki.append(BrakZrodla(id=eid, nazwa=nazwa, miejsce=miejsce, uzasadnienie=a.uzasadnienie or ""))
    return PanelBrakow(braki_zrodel=braki, pytania_do_organizatorow=k.pytania_do_organizatorow)


# --- API ----------------------------------------------------------------------------


def router(k: KonfiguracjaPierwszejOceny, auth) -> APIRouter:
    from .dostep import pracownik as _pracownik

    r = APIRouter(tags=["pierwsza ocena"])

    @r.get(
        "/api/pracownicy/{pracownik_id}/pierwsza-ocena",
        responses={404: {"description": "Nie ma takiego pracownika"},
                   409: {"description": "Wybór procesu nie wskazał pierwszej oceny"}},
    )
    def pierwsza(
        pracownik_id: str, request: Request, dzien: date | None = None, uz=auth.wymagaj("pracownicy.czytaj")
    ) -> SciezkaPierwszejOceny:
        """Ścieżka pierwszej oceny (art. 37–38) z elementami BRAK_ŹRÓDŁA (#19)."""
        p = _pracownik(request, uz, pracownik_id, "pracownicy.czytaj")
        wybor = wybierz(p, wersja_na_dzien(request.app.state.wersje, dzien or date.today()))
        if wybor.proces != "PIERWSZA":
            raise HTTPException(
                409, f"Wybór procesu nie wskazał pierwszej oceny ({wybor.proces}): {wybor.komunikat}"
            )
        return sciezka_pierwszej_oceny(p, k)

    @r.get("/api/braki")
    def braki(request: Request, dzien: date | None = None) -> PanelBrakow:
        """Panel braków: elementy bez źródła w materiałach i pytania do organizatorów."""
        w = wersja_na_dzien(request.app.state.wersje, dzien or date.today())
        return panel_brakow(w.reguly, w.wersja, k)

    return r
