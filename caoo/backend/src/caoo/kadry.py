"""Mock API kadrowego (#9): kontrakt (modele OpenAPI), dane demo i router.

Dane pochodzą z `konfiguracja/kadry.yaml`, są fikcyjne i zawsze oznaczone `demo: true`.
Kontrakt jest zapisany w `kontrakt/kadry-openapi.json` (test pilnuje, że jest aktualny).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

ZNACZNIK_DEMO = "dane demonstracyjne"


class Zatrudnienie(BaseModel):
    rodzaj: Literal["umowa_nieokreslony", "umowa_okreslony", "mianowanie"] = Field(
        description="Podstawa zatrudnienia."
    )
    data_nawiazania: date = Field(description="Data nawiązania stosunku pracy w służbie cywilnej.")
    data_na_stanowisku: date


class Mianowanie(BaseModel):
    data: date
    stopien: str = Field(description="Stopień służbowy urzędnika służby cywilnej.")


class Zastepstwo(BaseModel):
    zastepca_id: str
    zastepca: str | None = Field(default=None, description="Imię i nazwisko (uzupełnia API).")
    od: date
    do: date
    powod: str


class OpisStanowiska(BaseModel):
    cel: str
    zadania: list[str]
    wymagania: list[str]


class PoprzedniaOcena(BaseModel):
    data_sporzadzenia: date
    data_zapoznania: date = Field(description="Od tej daty biegną terminy odwołania i ponownej oceny.")
    wynik: Literal["pozytywna", "negatywna"]
    poziom: str
    srednia: float
    zalecenia: list[str] = Field(description="Wnioski i zalecenia rozwojowe z tej oceny.")


class Nieobecnosc(BaseModel):
    od: date
    do: date
    rodzaj: str
    usprawiedliwiona: bool


class Uwaga(BaseModel):
    data: date
    rodzaj: Literal["uwaga", "nagana"]
    tresc: str


class PracownikSkrot(BaseModel):
    """Pozycja listy pracowników."""

    id: str
    imie: str
    nazwisko: str
    stanowisko: str
    komorka: str
    przelozony_id: str | None
    demo: Literal[True] = True


class Profil(PracownikSkrot):
    """Pełny profil pracownika z API kadrowego."""

    jednostka: str
    kierownik_urzedu: bool
    liczba_podleglych: int | None
    korpus: Literal["urzednik", "pracownik"] | None = Field(description="Urzędnik albo pracownik służby cywilnej.")
    zatrudnienie: Zatrudnienie | None = Field(description="None: brak podstawy zatrudnienia w danych.")
    mianowanie: Mianowanie | None
    pierwsza_praca_w_sluzbie_cywilnej: bool | None = Field(
        default=False, description="None: brak informacji (umowa na czas określony wymaga jej do wyboru procesu)."
    )
    przelozony: str | None = Field(default=None, description="Imię i nazwisko (uzupełnia API).")
    zastepstwa: list[Zastepstwo]
    opis_stanowiska: OpisStanowiska
    poprzednie_oceny: list[PoprzedniaOcena]
    nieobecnosci: list[Nieobecnosc]
    uwagi: list[Uwaga]
    zrodlo_przykladu: str = Field(description="Przykładowy arkusz, z którego wyprowadzono stanowisko.")
    znacznik: str = ZNACZNIK_DEMO


class ListaPracownikow(BaseModel):
    demo: Literal[True] = True
    znacznik: str = ZNACZNIK_DEMO
    jednostka: str
    pracownicy: list[PracownikSkrot]


class MockKadry:
    def __init__(self, jednostka: str, profile: list[Profil]) -> None:
        self.jednostka = jednostka
        self._po_id = {p.id: p for p in profile}
        if len(self._po_id) != len(profile):
            raise ValueError("powtórzone id pracownika w danych kadrowych")
        for p in profile:
            for powiazany in [p.przelozony_id, *(z.zastepca_id for z in p.zastepstwa)]:
                if powiazany is not None and powiazany not in self._po_id:
                    raise ValueError(f"{p.id}: nieznany pracownik {powiazany}")

    @classmethod
    def z_pliku(cls, plik: str | Path) -> MockKadry:
        d = yaml.safe_load(Path(plik).read_text(encoding="utf-8"))
        if d.get("demo") is not True:
            raise ValueError("dane kadrowe muszą mieć `demo: true`")
        profile = [Profil.model_validate({**p, "jednostka": d["jednostka"]}) for p in d["pracownicy"]]
        return cls(d["jednostka"], profile)

    def _nazwa(self, pracownik_id: str | None) -> str | None:
        p = self._po_id.get(pracownik_id) if pracownik_id else None
        return f"{p.imie} {p.nazwisko}" if p else None

    def lista(self) -> ListaPracownikow:
        return ListaPracownikow(
            jednostka=self.jednostka,
            pracownicy=[PracownikSkrot.model_validate(p.model_dump()) for p in self._po_id.values()],
        )

    def profil(self, pracownik_id: str) -> Profil | None:
        p = self._po_id.get(pracownik_id)
        if p is None:
            return None
        return p.model_copy(
            update={
                "przelozony": self._nazwa(p.przelozony_id),
                "zastepstwa": [
                    z.model_copy(update={"zastepca": self._nazwa(z.zastepca_id)}) for z in p.zastepstwa
                ],
            }
        )


def router(auth) -> APIRouter:
    from .dostep import pracownik as _pracownik, widoczni as _widoczni

    r = APIRouter(prefix="/api/kadry", tags=["API kadrowe (mock, dane demonstracyjne)"])

    @r.get("/pracownicy")
    def lista(request: Request, uz=auth.wymagaj("pracownicy.czytaj")) -> ListaPracownikow:
        """Lista pracowników jednostki, które rola widzi (#10). Dane fikcyjne (`demo: true`)."""
        wszyscy = request.app.state.kadry.lista()
        ids = _widoczni(request, uz, "pracownicy.czytaj")
        return wszyscy.model_copy(update={"pracownicy": [p for p in wszyscy.pracownicy if p.id in ids]})

    @r.get("/pracownicy/{pracownik_id}", responses={404: {"description": "Nie ma takiego pracownika"}})
    def profil(pracownik_id: str, request: Request, uz=auth.wymagaj("pracownicy.czytaj")) -> Profil:
        """Profil: dane, stanowisko, zatrudnienie, mianowanie i stopień, przełożony i zastępstwa,
        opis stanowiska, poprzednie oceny i zalecenia, nieobecności, uwagi i nagany."""
        return _pracownik(request, uz, pracownik_id, "pracownicy.czytaj")

    return r


def kontrakt_openapi(openapi: dict) -> dict:
    """Wycinek specyfikacji OpenAPI aplikacji z samym API kadrowym (ścieżki i schematy)."""
    sciezki = {k: v for k, v in openapi["paths"].items() if k.startswith("/api/kadry")}
    wszystkie = openapi.get("components", {}).get("schemas", {})
    potrzebne: set[str] = set()

    def zbierz(wezel) -> None:
        if isinstance(wezel, dict):
            ref = wezel.get("$ref", "")
            if ref.startswith("#/components/schemas/"):
                nazwa = ref.rsplit("/", 1)[1]
                if nazwa not in potrzebne:
                    potrzebne.add(nazwa)
                    zbierz(wszystkie[nazwa])
            for v in wezel.values():
                zbierz(v)
        elif isinstance(wezel, list):
            for v in wezel:
                zbierz(v)

    zbierz(sciezki)
    return {
        "openapi": openapi["openapi"],
        "info": {"title": "Mock API kadrowego (dane demonstracyjne)", "version": "1"},
        "paths": sciezki,
        "components": {"schemas": {k: wszystkie[k] for k in sorted(potrzebne)}},
    }


if __name__ == "__main__":  # uv run python -m caoo.kadry
    import json

    from .app import app

    plik = Path(__file__).resolve().parents[2] / "kontrakt" / "kadry-openapi.json"
    plik.write_text(json.dumps(kontrakt_openapi(app.openapi()), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"zapisano {plik}")
