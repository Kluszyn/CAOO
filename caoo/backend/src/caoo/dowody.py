"""Cytaty-dowody dla kryterium przez LLM – wersja 1 (#22, V1-15; spec #2).

Oceniający prosi przy kryterium o fragmenty notatek z rozmowy (#24) i przykładów z odpowiedzi na
pytania pomocnicze (#14), które dotyczą kryterium. LLM zwraca wyłącznie cytaty; ``caoo_core.llm``
odrzuca odpowiedź, której cytat nie występuje dosłownie w źródłach – taka próba jest pokazywana
jako „propozycja odrzucona – brak pokrycia w źródłach”. Zaakceptowany cytat zapisuje się jako
dowód przy kryterium; punktacja się nie zmienia.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException, Request
from caoo_core.llm import BrakOdpowiedzi, Cytat, Dziennik, Odpowiedz, Tryb, WpisLogu, Zapytanie, wywolaj, z_konfiguracji
from caoo_core.zrodla import Fragment, SourceRef, normalizuj
from pydantic import BaseModel, Field

from .cykl import Cykl, _wczytaj
from .rozmowa import _notatki, utworz_tabele as _tabele_rozmowy

OPERACJA = "cytaty_dowody"


class _Regula(BaseModel):
    id: str
    adnotacje: list[SourceRef]


class KonfiguracjaDowodow(BaseModel):
    wersja_promptu: str
    llm: dict
    etykiety: dict
    regula: _Regula

    @classmethod
    def z_pliku(cls, plik: str | Path) -> KonfiguracjaDowodow:
        return cls.model_validate(yaml.safe_load(Path(plik).read_text(encoding="utf-8")))


class Zrodlo(BaseModel):
    fragment_id: str
    rodzaj: str
    """„notatka z rozmowy” albo „przykład z pytania pomocniczego”."""
    tekst: str


class Propozycja(BaseModel):
    fragment_id: str
    rodzaj: str
    cytat: str


class Odrzucona(BaseModel):
    dostawca: str
    etykieta: str
    bledy: list[str]


class Propozycje(BaseModel):
    cykl_id: int
    kryterium_id: str
    propozycje: list[Propozycja]
    etykieta: str | None
    tryb: str | None
    zrodlo_odpowiedzi: str | None
    odrzucone: list[Odrzucona]
    """Każda odrzucona próba – np. cytat bez pokrycia w notatkach."""
    komunikat: str | None = None
    zasada: str = "Wskazane dowody nie zmieniają punktacji – ocenę ustala oceniający."


class Dowod(BaseModel):
    id: int
    kryterium_id: str
    fragment_id: str
    rodzaj: str
    cytat: str
    zatwierdzil: str
    zatwierdzono: datetime


class NowyDowod(BaseModel):
    fragment_id: str = Field(min_length=1, max_length=120)
    cytat: str = Field(min_length=1, max_length=4000)


class _Zbierz:
    """Dziennik przekazujący wpisy dalej i zapamiętujący próby z tego wywołania."""

    def __init__(self, dalej: Dziennik) -> None:
        self.dalej, self.wpisy = dalej, []

    def zapisz(self, wpis: WpisLogu) -> None:
        self.wpisy.append(wpis)
        self.dalej.zapisz(wpis)


def zrodla(baza: sqlite3.Connection, c: Cykl, kid: str) -> list[Zrodlo]:
    """Notatki z rozmowy (dla kryterium i ogólne) oraz przykłady z odpowiedzi na pytania pomocnicze."""
    _tabele_rozmowy(baza)
    wynik = [
        Zrodlo(fragment_id=f"notatka:{n.id}", rodzaj="notatka z rozmowy", tekst=n.tresc)
        for n in _notatki(baza, c.id) if n.kryterium_id in (None, kid)
    ]
    for nr, odp in sorted((getattr(c, "odpowiedzi", {}) or {}).get(kid, {}).items()):
        if odp is not None and odp.dowod:
            wynik.append(Zrodlo(fragment_id=f"przyklad:{kid}:{nr}", rodzaj="przykład z pytania pomocniczego", tekst=odp.dowod))
    return wynik


def zapytanie(c: Cykl, kid: str, nazwa: str, zr: list[Zrodlo], wersja_promptu: str) -> Zapytanie:
    return Zapytanie(
        operacja=OPERACJA, prompt_wersja=wersja_promptu,
        pytanie=(f"Wskaż dosłowne fragmenty, które są dowodem dla kryterium „{nazwa}”. "
                 "Zwróć wyłącznie cytaty skopiowane znak po znaku z podanych fragmentów."),
        fragmenty=[Fragment(id=z.fragment_id, doc_id=f"cykl-{c.id}", tekst=z.tekst) for z in zr],
    )


def _tabela(baza: sqlite3.Connection) -> None:
    with baza:
        baza.execute("CREATE TABLE IF NOT EXISTS dowody_v1 (id INTEGER PRIMARY KEY, cykl_id INTEGER NOT NULL, dane TEXT NOT NULL)")


def _dowody(baza: sqlite3.Connection, cykl_id: int) -> list[Dowod]:
    _tabela(baza)
    return [Dowod(id=i, **json.loads(d)) for i, d in
            baza.execute("SELECT id, dane FROM dowody_v1 WHERE cykl_id = ? ORDER BY id", (cykl_id,)).fetchall()]


def router(konf: KonfiguracjaDowodow, dziennik: Dziennik, auth) -> APIRouter:
    """Łańcuch dostawców: ``app.state.dostawcy_dowodow`` (podmieniany w testach)."""
    from .dostep import pracownik as _pracownik

    r = APIRouter(prefix="/api/cykle", tags=["cytaty-dowody przez LLM (#22)"])
    et = konf.etykiety

    def kontekst(request: Request, cykl_id: int, uz, akcja: str) -> Cykl:
        c = _wczytaj(request.app.state.baza, cykl_id)
        if c is None:
            raise HTTPException(404, "Nie ma takiego cyklu")
        _pracownik(request, uz, c.pracownik_id, akcja)
        return c

    def kryterium(c: Cykl, kid: str) -> str:
        nazwa = next((k.nazwa for k in [*c.obowiazkowe, *c.dodatkowe] if k.id == kid), None)
        if nazwa is None:
            raise HTTPException(404, f"Kryterium {kid} nie należy do tego cyklu.")
        return nazwa

    @r.post("/{cykl_id}/dowody/{kryterium_id}/propozycje")
    def propozycje(cykl_id: int, kryterium_id: str, request: Request,
                   uz=auth.wymagaj("rozmowa.prowadz")) -> Propozycje:
        """LLM wskazuje cytaty z notatek i przykładów dla kryterium. Odpowiedzi z cytatem bez
        pokrycia są odrzucane i pokazane jako „propozycja odrzucona – brak pokrycia w źródłach”."""
        c = kontekst(request, cykl_id, uz, "rozmowa.prowadz")
        nazwa = kryterium(c, kryterium_id)
        zr = zrodla(request.app.state.baza, c, kryterium_id)
        if not zr:
            return Propozycje(cykl_id=c.id, kryterium_id=kryterium_id, propozycje=[], etykieta=None, tryb=None,
                              zrodlo_odpowiedzi=None, odrzucone=[], komunikat=et["brak_zrodel"])
        rodzaj = {z.fragment_id: z.rodzaj for z in zr}
        zbierz = _Zbierz(dziennik)
        try:
            w = wywolaj(zapytanie(c, kryterium_id, nazwa, zr, konf.wersja_promptu),
                        request.app.state.dostawcy_dowodow, zbierz)
        except BrakOdpowiedzi:
            w = None
        odrzucone = [Odrzucona(dostawca=x.dostawca, etykieta=et["odrzucona"], bledy=x.bledy)
                     for x in zbierz.wpisy if not x.walidacja_ok]
        if w is None:
            return Propozycje(cykl_id=c.id, kryterium_id=kryterium_id, propozycje=[], etykieta=None, tryb=None,
                              zrodlo_odpowiedzi=None, odrzucone=odrzucone)
        prop = [Propozycja(fragment_id=x.fragment_id, rodzaj=rodzaj.get(x.fragment_id, "?"), cytat=x.cytat)
                for x in w.odpowiedz.cytaty]
        return Propozycje(
            cykl_id=c.id, kryterium_id=kryterium_id, propozycje=prop, etykieta=et["propozycja"] if prop else None,
            tryb=w.tryb.value, zrodlo_odpowiedzi=et["tryby"].get(w.tryb.value), odrzucone=odrzucone,
        )

    @r.get("/{cykl_id}/dowody")
    def lista(cykl_id: int, request: Request, uz=auth.wymagaj("ocena.czytaj")) -> list[Dowod]:
        kontekst(request, cykl_id, uz, "ocena.czytaj")
        return _dowody(request.app.state.baza, cykl_id)

    @r.post("/{cykl_id}/dowody/{kryterium_id}", status_code=201)
    def zatwierdz(cykl_id: int, kryterium_id: str, body: NowyDowod, request: Request,
                  uz=auth.wymagaj("cykl.prowadz")) -> Dowod:
        """Oceniający przyjmuje cytat jako dowód przy kryterium. Cytat musi występować dosłownie
        w źródle; punktacja kryterium się nie zmienia."""
        c = kontekst(request, cykl_id, uz, "cykl.prowadz")
        kryterium(c, kryterium_id)
        baza = request.app.state.baza
        zr = {z.fragment_id: z for z in zrodla(baza, c, kryterium_id)}
        z = zr.get(body.fragment_id)
        if z is None or normalizuj(body.cytat) not in normalizuj(z.tekst):
            raise HTTPException(422, et["odrzucona"])
        _tabela(baza)
        dane = {"kryterium_id": kryterium_id, "fragment_id": z.fragment_id, "rodzaj": z.rodzaj, "cytat": body.cytat,
                "zatwierdzil": uz.login,  # z zalogowanego konta, nie z treści żądania
                "zatwierdzono": datetime.now(UTC).isoformat()}
        with baza:
            cur = baza.execute("INSERT INTO dowody_v1 (cykl_id, dane) VALUES (?, ?)", (c.id, json.dumps(dane, ensure_ascii=False)))
        return Dowod(id=cur.lastrowid, **dane)

    return r


class WszystkieFragmenty:
    """Zapas bez modelu: zwraca wszystkie źródła kryterium (już przefiltrowane) jako cytaty –
    zawsze dosłowne. BM25 z ``Ekstrakcyjny`` nie pasuje, bo pytanie o kryterium rzadko dzieli słowa
    z notatką."""

    nazwa, model, tryb = "wszystkie_fragmenty", None, Tryb.EKSTRAKCYJNE

    def odpowiedz(self, z: Zapytanie) -> Odpowiedz:
        return Odpowiedz(cytaty=[Cytat(fragment_id=f.id, cytat=f.tekst) for f in z.fragmenty], wybor=[], tresc=None)


def dostawcy(konf: KonfiguracjaDowodow) -> list:
    """Łańcuch z konfiguracji; typ ``wszystkie_fragmenty`` jest lokalny dla tej operacji."""
    wynik = []
    for d in konf.llm["dostawcy"]:
        wynik.append(WszystkieFragmenty() if d.get("typ") == "wszystkie_fragmenty"
                     else z_konfiguracji({**konf.llm, "dostawcy": [d]})[0])
    return wynik
