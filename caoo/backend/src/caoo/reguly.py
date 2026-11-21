"""Wersjonowana konfiguracja reguł (pliki w `konfiguracja/reguly/`).

Każdy plik to jedna wersja przepisów z identyfikatorem i datą obowiązywania. Skala § 11
jest budowana do `KonfiguracjaSkali` z caoo-core, więc liczenie wyniku nie ma własnej logiki.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import yaml
from caoo_core.kalendarz import RegulaTerminu
from caoo_core.skala import (
    KonfiguracjaSkali,
    ProgPoziomu,
    RegulaWyniku,
    WarunekLiczbyOcen,
    WarunekSredniej,
)
from caoo_core.slad import KrokObliczenia, Regula
from caoo_core.zrodla import SourceRef
from pydantic import BaseModel


class RegulaV1(BaseModel):
    id: str
    nazwa: str | None = None
    opis: str
    adnotacje: list[SourceRef]


class KrokTerminu(BaseModel):
    regula: str
    opis: str
    adnotacje: list[SourceRef]


class WersjaRegul(BaseModel):
    wersja: str
    obowiazuje_od: date
    plik: str
    adnotacje_wersji: RegulaV1
    reguly: list[RegulaV1]
    skala: dict
    kryteria: dict = {}
    terminy: dict = {}
    rozbieznosci: list[dict] = []

    def regula(self, regula_id: str) -> RegulaV1:
        for r in self.reguly:
            if r.id == regula_id:
                return r
        raise KeyError(f"{self.wersja}: brak reguły {regula_id}")

    def regula_core(self, regula_id: str) -> Regula:
        return Regula(id=regula_id, zrodlo=self.regula(regula_id).adnotacje[0])

    def regula_terminu(self, nazwa: str) -> RegulaTerminu:
        konfiguracja = self.terminy[nazwa]
        przesuniecie = konfiguracja.get("przesuniecie")
        return RegulaTerminu(
            liczba=konfiguracja["liczba"],
            jednostka=konfiguracja["jednostka"],
            regula=self.regula_core(konfiguracja["regula"]),
            przesuniecie_dnia_wolnego=self.regula_core(przesuniecie) if przesuniecie else None,
        )

    def krok_terminu(self, krok: KrokObliczenia) -> KrokTerminu:
        return KrokTerminu(
            regula=krok.regula.id, opis=krok.opis, adnotacje=self.regula(krok.regula.id).adnotacje
        )

    def konfiguracja_skali(self) -> KonfiguracjaSkali:
        """Skala § 11 dla silnika z caoo-core; reguły niosą pierwszą adnotację."""

        def dec(x) -> Decimal | None:
            return None if x is None else Decimal(str(x))

        s = self.skala
        progi = tuple(
            ProgPoziomu(
                poziom=p["poziom"],
                regula=self.regula_core(p["regula"]),
                minimum=dec(p.get("minimum")),
                maksimum=dec(p.get("maksimum")),
                domkniety_od_dolu=p.get("domkniety_od_dolu", True),
                domkniety_od_gory=p.get("domkniety_od_gory", True),
            )
            for p in s.get("progi", [])
        )

        def warunek(w: dict):
            if "ocena" in w:
                return WarunekLiczbyOcen(ocena=w["ocena"], operator=w["operator"], liczba=w["liczba"])
            return WarunekSredniej(operator=w["srednia"], wartosc=Decimal(str(w["wartosc"])))

        rozstrzygajace = tuple(
            RegulaWyniku(
                regula=self.regula_core(r["regula"]),
                warunki=tuple(warunek(w) for w in r["warunki"]),
                wynik=r["wynik"],
            )
            for r in s.get("reguly_rozstrzygajace", [])
        )
        return KonfiguracjaSkali(
            dozwolone_oceny=tuple(s["dozwolone_oceny"]),
            regula_sredniej=self.regula_core(s["regula_sredniej"]),
            wynik_domyslny=s["wynik_domyslny"],
            regula_domyslna=self.regula_core(s["regula_domyslna"]),
            progi=progi,
            reguly_rozstrzygajace=rozstrzygajace,
        )


def wczytaj_wersje(katalog: str | Path) -> list[WersjaRegul]:
    """Wszystkie wersje z katalogu, posortowane po dacie obowiązywania."""
    wersje = []
    for plik in sorted(Path(katalog).glob("*.yaml")):
        d = yaml.safe_load(plik.read_text(encoding="utf-8"))
        wersje.append(WersjaRegul.model_validate({**d, "plik": plik.name}))
    if len({w.wersja for w in wersje}) != len(wersje):
        raise ValueError("powtórzony identyfikator wersji reguł")
    return sorted(wersje, key=lambda w: w.obowiazuje_od)


def wersja_na_dzien(wersje: list[WersjaRegul], dzien: date) -> WersjaRegul:
    """Wersja obowiązująca w danym dniu (ostatnia z `obowiazuje_od` ≤ dzień)."""
    obowiazujace = [w for w in wersje if w.obowiazuje_od <= dzien]
    if not obowiazujace:
        raise LookupError(f"żadna wersja reguł nie obowiązuje {dzien.isoformat()}")
    return obowiazujace[-1]
