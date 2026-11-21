"""Pytania pomocnicze i propozycje punktów w roboczej metodyce V1."""

from pathlib import Path
from statistics import median_low
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, Field, StringConstraints

ETYKIETA = "robocza metodyka — nie jest wymogiem rozporządzenia"
PUNKTY_ODPOWIEDZI = {"nigdy": 1, "rzadko": 3, "czasem": 5, "zwykle": 7, "zawsze": 9}
OdpowiedzSlowna = Literal["nigdy", "rzadko", "czasem", "zwykle", "zawsze", "brak_danych"]
PiecPytan = Annotated[list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]],
                      Field(min_length=5, max_length=5)]


class Odpowiedz(BaseModel):
    wartosc: OdpowiedzSlowna
    dowod: str | None = None


class Metodyka(BaseModel):
    wersja: str
    pytania: dict[str, PiecPytan]


class PytanieOceny(BaseModel):
    id: int
    tresc: str
    odpowiedz: Odpowiedz | None


class MetodykaKryterium(BaseModel):
    etykieta: str = ETYKIETA
    pytania: list[PytanieOceny]
    propozycja: int | None
    liczba_odpowiedzi: int
    uzasadnienie_odstepstwa: str | None


def wczytaj_metodyki(katalog: Path, domyslna: str) -> dict[str, Metodyka]:
    wersje = {}
    for plik in sorted(katalog.glob("*.yaml")):
        metodyka = Metodyka.model_validate(yaml.safe_load(plik.read_text(encoding="utf-8")))
        if metodyka.wersja in wersje:
            raise ValueError(f"Powtórzona wersja metodyki: {metodyka.wersja}")
        wersje[metodyka.wersja] = metodyka
    if domyslna not in wersje:
        raise ValueError(f"Brak domyślnej metodyki: {domyslna}")
    return wersje


def propozycja(odpowiedzi: dict[int, Odpowiedz]) -> tuple[int | None, int]:
    """Dolna mediana utrzymuje propozycję na skali 1/3/5/7/9 także przy czterech odpowiedziach."""
    punktowane = [PUNKTY_ODPOWIEDZI[o.wartosc] for o in odpowiedzi.values() if o.wartosc != "brak_danych"]
    return (median_low(punktowane) if len(punktowane) >= 3 else None, len(punktowane))


def dla_kryterium(
    metodyka: Metodyka, kryterium_id: str, odpowiedzi: dict[int, Odpowiedz], uzasadnienie: str | None,
) -> MetodykaKryterium | None:
    pytania = metodyka.pytania.get(kryterium_id)
    if pytania is None:
        return None
    punkty, liczba = propozycja(odpowiedzi)
    return MetodykaKryterium(
        pytania=[PytanieOceny(id=i, tresc=tresc, odpowiedz=odpowiedzi.get(i)) for i, tresc in enumerate(pytania, 1)],
        propozycja=punkty,
        liczba_odpowiedzi=liczba,
        uzasadnienie_odstepstwa=uzasadnienie,
    )
