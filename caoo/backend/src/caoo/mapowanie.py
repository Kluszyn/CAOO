"""Mapowanie opisu stanowiska do katalogu kompetencji przez LLM (#21, V1-14).

Każde zadanie i wymaganie z opisu stanowiska to osobne zapytanie do łańcucha ``LlmProvider``
(Anthropic na żywo → odpowiedzi zapisane). Katalog dozwolonych ID to kompetencje z grafu (#20).
``caoo_core.llm.wywolaj`` odrzuca odpowiedź z ID spoza katalogu i pyta następnego dostawcę;
bez poprawnej odpowiedzi element jest „nieprzypisany – wymaga weryfikacji”. Wynik to zawsze
„propozycja – wymaga zatwierdzenia” z trybem „na żywo / zapisane”. Oceniający zatwierdza, odrzuca
albo koryguje; decyzja zapisuje osobę i datę oraz wpis w historii elementu.

    uv run python -m caoo.mapowanie   # przelicza konfiguracja/mapowanie-zapisane.json
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

import yaml
from fastapi import APIRouter, HTTPException, Request
from caoo_core.llm import BrakOdpowiedzi, Cytat, Dziennik, LlmProvider, Odpowiedz, Zapytanie, wywolaj, z_konfiguracji
from caoo_core.llm.dostawca import klucz_zapytania
from caoo_core.zrodla import Fragment, SourceRef
from pydantic import BaseModel, Field

from .kadry import Profil

TypElementu = Literal["zadanie", "wymaganie"]
OPERACJA = "mapowanie_osp"


# --- konfiguracja -------------------------------------------------------------------


class _Regula(BaseModel):
    id: str
    adnotacje: list[SourceRef]


class KonfiguracjaMapowania(BaseModel):
    wersja_promptu: str
    llm: dict
    etykiety: dict
    regula: _Regula

    @classmethod
    def z_pliku(cls, plik: str | Path) -> KonfiguracjaMapowania:
        return cls.model_validate(yaml.safe_load(Path(plik).read_text(encoding="utf-8")))

    def dostawcy(self, katalog_aplikacji: Path) -> list[LlmProvider]:
        """Łańcuch dostawców; ścieżki plików zapisanych odpowiedzi względem ``caoo/``."""
        dostawcy = []
        for d in self.llm["dostawcy"]:
            d = dict(d)
            if "plik" in d:
                d["plik"] = str(katalog_aplikacji / d["plik"])
            dostawcy.append(d)
        return z_konfiguracji({**self.llm, "dostawcy": dostawcy})


# --- model --------------------------------------------------------------------------


class Element(BaseModel):
    id: str
    typ: TypElementu
    tekst: str


class WpisHistorii(BaseModel):
    czas: datetime
    osoba: str
    decyzja: str
    przed: list[str]
    po: list[str]


class Mapowanie(BaseModel):
    element: Element
    propozycja: list[str]
    """ID kompetencji z katalogu zaproponowane przez LLM (pusta lista = nieprzypisane)."""
    tryb: Literal["NA_ŻYWO", "ZAPISANE"] | None
    dostawca: str | None
    odrzuceni: list[str] = Field(default_factory=list)
    status: Literal["PROPOZYCJA", "ZATWIERDZONE", "ODRZUCONE", "SKORYGOWANE"] = "PROPOZYCJA"
    kompetencje: list[str] = Field(default_factory=list)
    """Powiązania po decyzji oceniającego (puste przed decyzją)."""
    zatwierdzil: str | None = None
    data_zatwierdzenia: date | None = None
    historia: list[WpisHistorii] = Field(default_factory=list)


class WidokMapowania(Mapowanie):
    etykieta: str
    """„propozycja — wymaga zatwierdzenia”, „nieprzypisane — wymaga weryfikacji” albo decyzja."""
    zrodlo_odpowiedzi: str | None
    """„na żywo” / „zapisane” / None (żaden dostawca nie dał poprawnej odpowiedzi)."""
    nazwy: dict[str, str]


class StanMapowania(BaseModel):
    pracownik_id: str
    wersja_promptu: str
    elementy: list[WidokMapowania]
    regula: _Regula


class Decyzja(BaseModel):
    decyzja: Literal["ZATWIERDZ", "ODRZUC", "KORYGUJ"]
    kompetencje: list[str] = Field(default_factory=list, max_length=14)
    """Tylko przy KORYGUJ: poprawione powiązania (ID z katalogu)."""
    data: date | None = None


# --- zapytanie ----------------------------------------------------------------------


def elementy(p: Profil) -> list[Element]:
    """Te same identyfikatory co w grafie (#20): ``zadanie:1``, ``wymaganie:1``…"""
    return [
        *(Element(id=f"zadanie:{i}", typ="zadanie", tekst=t) for i, t in enumerate(p.opis_stanowiska.zadania, 1)),
        *(Element(id=f"wymaganie:{i}", typ="wymaganie", tekst=t) for i, t in enumerate(p.opis_stanowiska.wymagania, 1)),
    ]


def zapytanie(p: Profil, e: Element, katalog: dict[str, str], wersja_promptu: str) -> Zapytanie:
    fragmenty = [
        Fragment(id=f"osp:{p.id}:{e.id}", doc_id="opis-stanowiska", tekst=e.tekst),
        *(Fragment(id=f"katalog:{kid}", doc_id="katalog-kompetencji", tekst=f"{kid}: {nazwa}")
          for kid, nazwa in sorted(katalog.items())),
    ]
    return Zapytanie(
        operacja=OPERACJA,
        prompt_wersja=wersja_promptu,
        pytanie=(f"Element opisu stanowiska ({e.typ}): „{e.tekst}”. Wskaż kompetencje z katalogu, "
                 "których dotyczy. Zwróć tylko ID z katalogu; jeśli żadna nie pasuje – pustą listę."),
        fragmenty=fragmenty,
        katalog=sorted(katalog),
    )


def zaproponuj(p: Profil, e: Element, katalog: dict[str, str], konf: KonfiguracjaMapowania,
               dostawcy: list[LlmProvider], dziennik: Dziennik) -> Mapowanie:
    try:
        w = wywolaj(zapytanie(p, e, katalog, konf.wersja_promptu), dostawcy, dziennik)
    except BrakOdpowiedzi as b:
        return Mapowanie(element=e, propozycja=[], tryb=None, dostawca=None,
                         odrzuceni=[x.dostawca for x in b.wpisy])
    return Mapowanie(element=e, propozycja=list(dict.fromkeys(w.odpowiedz.wybor)), tryb=w.tryb.value,
                     dostawca=w.dostawca, odrzuceni=w.odrzuceni)


def widok(m: Mapowanie, katalog: dict[str, str], konf: KonfiguracjaMapowania) -> WidokMapowania:
    et = konf.etykiety
    if m.status != "PROPOZYCJA":
        etykieta = {"ZATWIERDZONE": "zatwierdzone", "ODRZUCONE": "odrzucone", "SKORYGOWANE": "skorygowane"}[m.status]
        etykieta += f" – {m.zatwierdzil}, {m.data_zatwierdzenia}"
    else:
        etykieta = et["propozycja"] if m.propozycja else et["nieprzypisane"]
    ids = {*m.propozycja, *m.kompetencje}
    return WidokMapowania(
        **m.model_dump(), etykieta=etykieta,
        zrodlo_odpowiedzi=et["tryby"].get(m.tryb) if m.tryb else None,
        nazwy={k: katalog[k] for k in ids if k in katalog},
    )


# --- zapis --------------------------------------------------------------------------


def _tabela(baza: sqlite3.Connection) -> None:
    with baza:
        baza.execute(
            "CREATE TABLE IF NOT EXISTS mapowania_v1 (pracownik_id TEXT NOT NULL, element_id TEXT NOT NULL,"
            " dane TEXT NOT NULL, PRIMARY KEY (pracownik_id, element_id))"
        )


def _wczytaj(baza: sqlite3.Connection, pid: str) -> dict[str, Mapowanie]:
    _tabela(baza)
    return {e: Mapowanie.model_validate_json(d) for e, d in
            baza.execute("SELECT element_id, dane FROM mapowania_v1 WHERE pracownik_id = ?", (pid,)).fetchall()}


def _zapisz(baza: sqlite3.Connection, pid: str, m: Mapowanie) -> None:
    with baza:
        baza.execute(
            "INSERT INTO mapowania_v1 (pracownik_id, element_id, dane) VALUES (?, ?, ?) "
            "ON CONFLICT(pracownik_id, element_id) DO UPDATE SET dane = excluded.dane",
            (pid, m.element.id, m.model_dump_json()),
        )


# --- API ----------------------------------------------------------------------------


def router(konf: KonfiguracjaMapowania, dziennik: Dziennik, auth) -> APIRouter:
    from .dostep import pracownik as _pracownik

    """Łańcuch dostawców: ``app.state.dostawcy_mapowania`` (podmieniany w testach)."""
    r = APIRouter(prefix="/api/pracownicy", tags=["mapowanie opisu stanowiska przez LLM (#21)"])

    def kontekst(request: Request, pid: str, uz, akcja: str) -> tuple[Profil, dict[str, str]]:
        p = _pracownik(request, uz, pid, akcja)
        katalog = next(iter(request.app.state.katalogi_grafu.values()))
        return p, {k.id.split(":", 1)[1]: k.nazwa for k in katalog.kompetencje}

    def stan(request: Request, p: Profil, katalog: dict[str, str], odswiez: bool = False) -> StanMapowania:
        baza = request.app.state.baza
        zapisane = _wczytaj(baza, p.id)
        wynik = []
        for e in elementy(p):
            m = zapisane.get(e.id)
            if m is None or (odswiez and m.status == "PROPOZYCJA"):
                m = zaproponuj(p, e, katalog, konf, request.app.state.dostawcy_mapowania, dziennik)
                _zapisz(baza, p.id, m)
            wynik.append(widok(m, katalog, konf))
        return StanMapowania(pracownik_id=p.id, wersja_promptu=konf.wersja_promptu, elementy=wynik, regula=konf.regula)

    @r.get("/{pracownik_id}/mapowanie")
    def odczyt(pracownik_id: str, request: Request, uz=auth.wymagaj("pracownicy.czytaj")) -> StanMapowania:
        """Propozycje powiązań zadań i wymagań z kompetencjami katalogu (przy pierwszym odczycie
        pyta LLM). Każda: „propozycja – wymaga zatwierdzenia” i „na żywo / zapisane”."""
        return stan(request, *kontekst(request, pracownik_id, uz, "pracownicy.czytaj"))

    @r.post("/{pracownik_id}/mapowanie/odswiez")
    def odswiez(pracownik_id: str, request: Request, uz=auth.wymagaj("cykl.prowadz")) -> StanMapowania:
        """Ponowne zapytanie LLM dla elementów bez decyzji (decyzje oceniającego zostają)."""
        return stan(request, *kontekst(request, pracownik_id, uz, "cykl.prowadz"), odswiez=True)

    @r.post("/{pracownik_id}/mapowanie/{typ}/{nr}/decyzja")
    def decyzja(
        pracownik_id: str, typ: TypElementu, nr: int, body: Decyzja, request: Request,
        uz=auth.wymagaj("cykl.prowadz"),
    ) -> WidokMapowania:
        """Zatwierdzenie, odrzucenie albo korekta powiązań; zapisuje osobę, datę i wpis w historii."""
        p, katalog = kontekst(request, pracownik_id, uz, "cykl.prowadz")
        baza = request.app.state.baza
        stan(request, p, katalog)  # upewnij się, że propozycja istnieje
        m = _wczytaj(baza, p.id).get(f"{typ}:{nr}")
        if m is None:
            raise HTTPException(404, "Nie ma takiego elementu opisu stanowiska")
        if body.decyzja == "KORYGUJ":
            spoza = [k for k in body.kompetencje if k not in katalog]
            if spoza:
                raise HTTPException(422, {"komunikat": "Korekta tylko do ID z katalogu.", "spoza_katalogu": spoza})
            po, status = list(dict.fromkeys(body.kompetencje)), "SKORYGOWANE"
        elif body.decyzja == "ZATWIERDZ":
            if not m.propozycja:
                raise HTTPException(409, "Brak propozycji do zatwierdzenia – element nieprzypisany; użyj KORYGUJ.")
            po, status = list(m.propozycja), "ZATWIERDZONE"
        else:
            po, status = [], "ODRZUCONE"
        dzien = body.data or date.today()
        wpis = WpisHistorii(czas=datetime.now(UTC), osoba=uz.login, decyzja=body.decyzja,
                            przed=m.kompetencje or m.propozycja, po=po)
        m = m.model_copy(update={"status": status, "kompetencje": po, "zatwierdzil": uz.login,  # z zalogowanego konta
                                 "data_zatwierdzenia": dzien, "historia": [*m.historia, wpis]})
        _zapisz(baza, p.id, m)
        return widok(m, katalog, konf)

    return r


# --- odpowiedzi zapisane dla demo ---------------------------------------------------


def odpowiedzi_demo(kadry, katalog: dict[str, str], konf: KonfiguracjaMapowania, zrodlo: Path) -> dict:
    """Plik dla dostawcy ``zapisane`` z ręcznie przygotowanego źródła ``mapowanie-demo.yaml``."""
    demo = yaml.safe_load(zrodlo.read_text(encoding="utf-8"))
    wynik: dict = {"_opis": "Odpowiedzi zapasowe przygotowane ręcznie (mapowanie-demo.yaml), nie przez model."}
    for pid, typy in demo.items():
        p = kadry.profil(pid)
        for e in elementy(p):
            ids = typy[e.typ][int(e.id.split(":")[1]) - 1]
            spoza = [i for i in ids if i not in katalog]
            if spoza:
                raise ValueError(f"{pid} {e.id}: ID spoza katalogu {spoza}")
            z = zapytanie(p, e, katalog, konf.wersja_promptu)
            # Core wymaga cytatu: podstawą wyboru jest dosłownie element opisu stanowiska.
            cytat = Cytat(fragment_id=f"osp:{p.id}:{e.id}", cytat=e.tekst)
            wynik[klucz_zapytania(z)] = Odpowiedz(cytaty=[cytat], wybor=ids, tresc=None).model_dump()
    return wynik


if __name__ == "__main__":  # pragma: no cover - narzędzie
    from .graf import zbuduj_katalog
    from .graf_model import wczytaj_konfiguracje_grafu
    from .kadry import MockKadry
    from .reguly import wczytaj_wersje
    from .ustawienia import KATALOG_APLIKACJI, Ustawienia

    u = Ustawienia.ze_srodowiska()
    (w, *_) = wczytaj_wersje(u.reguly)
    kat = zbuduj_katalog(w, wczytaj_konfiguracje_grafu(u.graf))
    konf = KonfiguracjaMapowania.z_pliku(u.mapowanie)
    dane = odpowiedzi_demo(MockKadry.z_pliku(u.kadry), {k.id.split(":", 1)[1]: k.nazwa for k in kat.kompetencje},
                           konf, KATALOG_APLIKACJI / "konfiguracja" / "mapowanie-demo.yaml")
    cel = KATALOG_APLIKACJI / "konfiguracja" / "mapowanie-zapisane.json"
    cel.write_text(json.dumps(dane, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"zapisano {cel} ({len(dane) - 1} odpowiedzi)", file=sys.stderr)
