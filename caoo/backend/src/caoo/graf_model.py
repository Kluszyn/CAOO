"""Schemat grafu i kontrakt katalogu oraz danych pojedynczej oceny."""

from datetime import date
from itertools import pairwise
from pathlib import Path
from typing import Literal

import yaml
from caoo_core.zrodla import SourceRef
from pydantic import BaseModel, Field, model_validator

Status = Literal["potwierdzone", "dopasowane", "propozycja"]


class SchematGrafu(BaseModel):
    wersja: str
    obowiazuje_od: date
    typy_wezlow: list[str]
    relacje: dict[str, list[tuple[str, str]]]

    @model_validator(mode="after")
    def poprawne_typy_relacji(self):
        typy = set(self.typy_wezlow)
        if len(typy) != len(self.typy_wezlow):
            raise ValueError("Powtórzone typy węzłów w schemacie.")
        for typ, pary in self.relacje.items():
            if not pary or any(z not in typy or do not in typy for z, do in pary):
                raise ValueError(f"Relacja {typ} używa typu węzła spoza schematu.")
        return self


class DzialanieKatalogowe(BaseModel):
    id: str
    nazwa: str
    adnotacje: list[SourceRef] = Field(min_length=1)


class KonfiguracjaGrafu(BaseModel):
    schemat: SchematGrafu
    wersja_katalogu: str
    poziom_roboczy: int
    slowa_uzupelniajace: dict[str, list[str]]
    slowa_pomijane_w_zaleceniach: list[str]
    dzialania: list[DzialanieKatalogowe]


class Szczegoly(BaseModel):
    status: Status
    zrodlo: str
    uzasadnienie: str
    adnotacje: list[SourceRef] = Field(default_factory=list)
    data_zrodla: date | None = None
    zatwierdzil: str | None = None
    data_zatwierdzenia: date | None = None


class Wezel(Szczegoly):
    id: str
    typ: str
    nazwa: str
    wartosc: int | None = None
    katalog_id: str | None = None
    nieprzypisany: bool = False


class Relacja(Szczegoly):
    typ: str
    z: str
    do: str


class KatalogGrafu(BaseModel):
    schemat: SchematGrafu
    wersja_katalogu: str
    wersja_regul: str
    kompetencje: list[Wezel]
    poziomy: list[Wezel]
    dzialania: list[Wezel]

    def wezly(self) -> list[Wezel]:
        return [*self.kompetencje, *self.poziomy, *self.dzialania]


class Sciezka(BaseModel):
    wezly: tuple[str, str, str, str, str, str, str]


class PropozycjaRozwoju(Szczegoly):
    kompetencja_id: str
    podstawy: list[str]


class GrafOceny(BaseModel):
    cykl_id: int
    katalog: KatalogGrafu
    wezly: list[Wezel]
    relacje: list[Relacja]
    sciezki: list[Sciezka]
    propozycje: list[PropozycjaRozwoju]


def wczytaj_konfiguracje_grafu(plik: Path) -> KonfiguracjaGrafu:
    return KonfiguracjaGrafu.model_validate(yaml.safe_load(plik.read_text(encoding="utf-8")))


def waliduj_graf(
    schemat: SchematGrafu,
    wezly: list[Wezel],
    relacje: list[Relacja],
    sciezki: list[Sciezka] | None = None,
) -> None:
    """Sprawdza dane względem przekazanego schematu, w tym kierunek i końce relacji."""
    indeks = {}
    for w in wezly:
        if w.typ not in schemat.typy_wezlow:
            raise ValueError(f"Typ węzła {w.typ} jest poza schematem {schemat.wersja}.")
        if w.id in indeks:
            raise ValueError(f"Powtórzony identyfikator węzła {w.id}.")
        indeks[w.id] = w.typ
    for w in wezly:
        if w.katalog_id is not None and indeks.get(w.katalog_id) != w.typ:
            raise ValueError(f"Nieprawidłowe odwołanie do katalogu w węźle {w.id}.")
    pary = set()
    for r in relacje:
        if r.z not in indeks or r.do not in indeks:
            raise ValueError(f"Relacja {r.typ} wskazuje nieistniejący węzeł.")
        if (indeks[r.z], indeks[r.do]) not in schemat.relacje.get(r.typ, []):
            raise ValueError(f"Relacja {r.typ} jest niedozwolona dla {indeks[r.z]} → {indeks[r.do]}.")
        pary.add((r.z, r.do))
    for s in sciezki or []:
        if any(para not in pary for para in pairwise(s.wezly)):
            raise ValueError("Ścieżka zawiera kroki bez dozwolonej relacji.")
