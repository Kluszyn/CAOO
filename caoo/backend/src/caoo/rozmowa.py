"""Pytania do rozmowy oceniającej i notatki (#24, V1-17; spec #2, historyjki 62–68).

Szablony uruchamiane regułami (zadanie, wymaganie, poprzednie zalecenie, niska i wysoka ocena
kryterium) oraz zakres rozmowy z § 10 rozp. 2016. Każde pytanie ma uzasadnienie i powiązanie.
Oceniający wybiera, edytuje i dodaje pytania oraz zapisuje notatki. Notatki są dowodami
i nie zmieniają punktacji (nie dotykają ``Cykl.oceny``). Bez LLM.

Pytania do rozmowy (``rodzaj = "PYTANIE_DO_ROZMOWY"``) są oddzielone od pytań pomocniczych
do oceny kryteriów (#14) – osobny endpoint, osobny typ i osobne miejsce zapisu.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import yaml
from fastapi import APIRouter, HTTPException, Request
from caoo_core.zrodla import SourceRef
from pydantic import BaseModel, Field

from .cykl import Cykl, _wczytaj
from .kadry import Profil

TypPowiazania = Literal["zadanie", "wymaganie", "kryterium", "poprzednia_ocena", "przepis", "wlasne"]


# --- konfiguracja -------------------------------------------------------------------


class _Zakres(BaseModel):
    id: str
    tresc: str
    uzasadnienie: str
    adnotacje: list[SourceRef]


class _Szablon(BaseModel):
    id: str
    regula: Literal["zadanie_stanowiska", "wymaganie_stanowiska", "poprzednie_zalecenie", "niska_ocena", "wysoka_ocena"]
    tresc: str
    uzasadnienie: str


class _Funkcja(BaseModel):
    id: str
    adnotacje: list[SourceRef]


class KonfiguracjaRozmowy(BaseModel):
    wersja: str
    etykieta_funkcji: str
    limity: dict[str, int]
    zakres_par_10: list[_Zakres]
    szablony: list[_Szablon]
    funkcja: _Funkcja

    @classmethod
    def z_pliku(cls, plik: str | Path) -> KonfiguracjaRozmowy:
        return cls.model_validate(yaml.safe_load(Path(plik).read_text(encoding="utf-8")))


# --- model --------------------------------------------------------------------------


class Powiazanie(BaseModel):
    typ: TypPowiazania
    opis: str | None = None
    """Zadanie, wymaganie, nazwa kryterium, zalecenie albo lokalizator przepisu."""
    kryterium_id: str | None = None


class PropozycjaPytania(BaseModel):
    id: str
    rodzaj: Literal["PYTANIE_DO_ROZMOWY"] = "PYTANIE_DO_ROZMOWY"
    szablon: str
    tresc: str
    uzasadnienie: str
    powiazanie: Powiazanie
    etykieta: str | None
    """„funkcja aplikacji — nie wymóg rozporządzenia” albo None dla zakresu z § 10."""
    adnotacje: list[SourceRef]


class PytanieWybrane(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    tresc: str = Field(min_length=1, max_length=1000)
    z_propozycji: str | None = None
    """Id propozycji, z której pytanie pochodzi; None = pytanie dodane przez oceniającego."""


class Notatka(BaseModel):
    id: int
    tresc: str
    pytanie_id: str | None = None
    kryterium_id: str | None = None
    """Notatka może być dowodem do kryterium – nie zmienia jego punktów."""
    zapisano: datetime


class NowaNotatka(BaseModel):
    tresc: str = Field(min_length=1, max_length=4000)
    pytanie_id: str | None = None
    kryterium_id: str | None = None


class ZapisPytan(BaseModel):
    pytania: list[PytanieWybrane] = Field(max_length=60)


class Rozmowa(BaseModel):
    cykl_id: int
    wersja: str
    rodzaj: Literal["PYTANIA_DO_ROZMOWY"] = "PYTANIA_DO_ROZMOWY"
    propozycje: list[PropozycjaPytania]
    pytania: list[PytanieWybrane]
    notatki: list[Notatka]
    zasada_notatek: str = "Notatki są dowodami z rozmowy i nie zmieniają punktacji."


# --- reguły -------------------------------------------------------------------------


def propozycje(cykl: Cykl, profil: Profil, konf: KonfiguracjaRozmowy) -> list[PropozycjaPytania]:
    wynik: list[PropozycjaPytania] = []
    etykieta = konf.etykieta_funkcji
    adn_funkcji = konf.funkcja.adnotacje
    szablon = {s.regula: [x for x in konf.szablony if x.regula == s.regula] for s in konf.szablony}

    def dodaj(s: _Szablon, tresc: str, powiazanie: Powiazanie) -> None:
        wynik.append(
            PropozycjaPytania(
                id=f"{s.id}#{len(wynik) + 1}", szablon=s.id, tresc=tresc, uzasadnienie=s.uzasadnienie,
                powiazanie=powiazanie, etykieta=etykieta, adnotacje=adn_funkcji,
            )
        )

    # Zakres z § 10 – wymóg przepisu, bez etykiety funkcji aplikacji.
    for z in konf.zakres_par_10:
        wynik.append(
            PropozycjaPytania(
                id=z.id, szablon=z.id, tresc=z.tresc, uzasadnienie=z.uzasadnienie,
                powiazanie=Powiazanie(typ="przepis", opis=z.adnotacje[0].lokalizator),
                etykieta=None, adnotacje=z.adnotacje,
            )
        )

    for zadanie in profil.opis_stanowiska.zadania[: konf.limity.get("zadania", 3)]:
        for s in szablon.get("zadanie_stanowiska", []):
            dodaj(s, s.tresc.format(zadanie=zadanie.rstrip(".")), Powiazanie(typ="zadanie", opis=zadanie))
    for wym in profil.opis_stanowiska.wymagania[: konf.limity.get("wymagania", 2)]:
        for s in szablon.get("wymaganie_stanowiska", []):
            dodaj(s, s.tresc.format(wymaganie=wym.rstrip(".")), Powiazanie(typ="wymaganie", opis=wym))

    if profil.poprzednie_oceny:
        ostatnia = max(profil.poprzednie_oceny, key=lambda o: o.data_sporzadzenia)
        for zal in ostatnia.zalecenia:
            for s in szablon.get("poprzednie_zalecenie", []):
                dodaj(
                    s, s.tresc.format(zalecenie=zal, data=ostatnia.data_sporzadzenia.isoformat()),
                    Powiazanie(typ="poprzednia_ocena", opis=zal),
                )

    nazwy = {k.id: k.nazwa for k in [*cykl.obowiazkowe, *cykl.dodatkowe]}
    for kid, pkt in cykl.oceny.items():
        nazwa = nazwy.get(kid, kid)
        if pkt <= konf.limity.get("niska_ocena_do", 3):
            regula = "niska_ocena"
        elif pkt >= konf.limity.get("wysoka_ocena_od", 7):
            regula = "wysoka_ocena"
        else:
            continue
        for s in szablon.get(regula, []):
            dodaj(s, s.tresc.format(kryterium=nazwa), Powiazanie(typ="kryterium", opis=nazwa, kryterium_id=kid))
    return wynik


# --- zapis --------------------------------------------------------------------------


def utworz_tabele(baza: sqlite3.Connection) -> None:
    with baza:
        baza.execute("CREATE TABLE IF NOT EXISTS rozmowy_v1 (cykl_id INTEGER PRIMARY KEY, pytania TEXT NOT NULL)")
        baza.execute(
            "CREATE TABLE IF NOT EXISTS notatki_rozmowy_v1 (id INTEGER PRIMARY KEY, cykl_id INTEGER NOT NULL,"
            " dane TEXT NOT NULL)"
        )


def _pytania(baza: sqlite3.Connection, cykl_id: int) -> list[PytanieWybrane]:
    w = baza.execute("SELECT pytania FROM rozmowy_v1 WHERE cykl_id = ?", (cykl_id,)).fetchone()
    return [PytanieWybrane.model_validate(p) for p in json.loads(w[0])] if w else []


def _notatki(baza: sqlite3.Connection, cykl_id: int) -> list[Notatka]:
    wiersze = baza.execute(
        "SELECT id, dane FROM notatki_rozmowy_v1 WHERE cykl_id = ? ORDER BY id", (cykl_id,)
    ).fetchall()
    return [Notatka(id=i, **json.loads(d)) for i, d in wiersze]


# --- API ----------------------------------------------------------------------------


def router(konf: KonfiguracjaRozmowy, auth) -> APIRouter:
    from .dostep import pracownik as _pracownik

    r = APIRouter(prefix="/api/cykle", tags=["pytania do rozmowy i notatki (#24)"])

    def kontekst(request: Request, cykl_id: int, uz) -> tuple[Cykl, Profil]:
        baza = request.app.state.baza
        utworz_tabele(baza)
        c = _wczytaj(baza, cykl_id)
        if c is None:
            raise HTTPException(404, "Nie ma takiego cyklu")
        p = _pracownik(request, uz, c.pracownik_id, "rozmowa.prowadz")
        return c, p

    def widok(request: Request, c: Cykl, p: Profil) -> Rozmowa:
        baza = request.app.state.baza
        return Rozmowa(
            cykl_id=c.id, wersja=konf.wersja, propozycje=propozycje(c, p, konf),
            pytania=_pytania(baza, c.id), notatki=_notatki(baza, c.id),
        )

    @r.get("/{cykl_id}/rozmowa")
    def odczyt(cykl_id: int, request: Request, uz=auth.wymagaj("rozmowa.prowadz")) -> Rozmowa:
        """Propozycje pytań do rozmowy (z uzasadnieniem i powiązaniem), pytania wybrane przez
        oceniającego i notatki. Oddzielone od pytań pomocniczych do oceny kryteriów."""
        return widok(request, *kontekst(request, cykl_id, uz))

    @r.put("/{cykl_id}/rozmowa/pytania")
    def zapisz_pytania(
        cykl_id: int, body: ZapisPytan, request: Request, uz=auth.wymagaj("rozmowa.prowadz")
    ) -> Rozmowa:
        """Lista pytań oceniającego: wybrane z propozycji (``z_propozycji``), zmienione albo własne."""
        c, p = kontekst(request, cykl_id, uz)
        znane = {x.id for x in propozycje(c, p, konf)}
        bledy = [f"Nie ma propozycji {q.z_propozycji}." for q in body.pytania
                 if q.z_propozycji is not None and q.z_propozycji not in znane]
        ids = [q.id for q in body.pytania]
        if len(ids) != len(set(ids)):
            bledy.append("Identyfikatory pytań muszą być unikalne.")
        if bledy:
            raise HTTPException(422, {"komunikat": "Niepoprawna lista pytań.", "bledy": bledy})
        baza = request.app.state.baza
        with baza:
            baza.execute(
                "INSERT INTO rozmowy_v1 (cykl_id, pytania) VALUES (?, ?) "
                "ON CONFLICT(cykl_id) DO UPDATE SET pytania = excluded.pytania",
                (c.id, json.dumps([q.model_dump() for q in body.pytania], ensure_ascii=False)),
            )
        return widok(request, c, p)

    @r.post("/{cykl_id}/rozmowa/notatki", status_code=201)
    def dodaj_notatke(
        cykl_id: int, body: NowaNotatka, request: Request, uz=auth.wymagaj("rozmowa.prowadz")
    ) -> Notatka:
        """Notatka z rozmowy – dowód (opcjonalnie do pytania lub kryterium). Punkty się nie zmieniają."""
        c, _ = kontekst(request, cykl_id, uz)
        kryteria = {k.id for k in [*c.obowiazkowe, *c.dodatkowe]}
        if body.kryterium_id is not None and body.kryterium_id not in kryteria:
            raise HTTPException(422, f"Kryterium {body.kryterium_id} nie należy do tego cyklu.")
        dane = {**body.model_dump(), "zapisano": datetime.now(UTC).isoformat()}
        baza = request.app.state.baza
        with baza:
            cur = baza.execute(
                "INSERT INTO notatki_rozmowy_v1 (cykl_id, dane) VALUES (?, ?)",
                (c.id, json.dumps(dane, ensure_ascii=False)),
            )
        return Notatka(id=cur.lastrowid, **dane)

    return r
