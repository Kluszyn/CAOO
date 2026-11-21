"""Zatwierdzone wersje, zapis doręczenia i terminy odwołania."""

from datetime import date, timedelta
from typing import Literal

from caoo_core.kalendarz import wyznacz_termin
from caoo_core.slad import KrokObliczenia
from pydantic import BaseModel, Field

from .arkusz_pdf import TrescArkusza
from .metodyka import Odpowiedz
from .reguly import KrokTerminu, WersjaRegul
from .swieta import dni_wolne

PowodWersji = Literal["PIERWSZA", "KOREKTA", "ZMIANA_PO_ODWOLANIU", "PONOWNE_SPORZADZENIE"]
Rozstrzygniecie = Literal["NIEUWZGLEDNIONE", "ZMIANA_OCENY", "PONOWNE_SPORZADZENIE"]
StatusWersji = Literal["ZATWIERDZONA", "WYDRUKOWANA", "PODPISANA_I_DORECZONA"]


class Doreczenie(BaseModel):
    data_zapoznania: date
    rejestrujacy: str
    zarejestrowano: date
    ma_skan: bool = False


class Odwolanie(BaseModel):
    data_wniesienia: date
    data_rozpatrzenia: date | None = None
    rozpatrzyl: str | None = None
    rozstrzygniecie: Rozstrzygniecie | None = None


class WersjaOceny(BaseModel):
    tresc: TrescArkusza
    powod: PowodWersji
    hash: str
    data_zatwierdzenia: date
    data_wydruku: date | None = None
    doreczenie: Doreczenie | None = None
    odwolanie: Odwolanie | None = None
    wersja_metodyki: str | None = None
    odpowiedzi: dict[str, dict[int, Odpowiedz]] = Field(default_factory=dict)
    uzasadnienia_odstepstw: dict[str, str] = Field(default_factory=dict)

    @property
    def status(self) -> StatusWersji:
        if self.doreczenie:
            return "PODPISANA_I_DORECZONA"
        return "WYDRUKOWANA" if self.data_wydruku else "ZATWIERDZONA"


class DataTerminu(BaseModel):
    data_odniesienia: date
    data: date
    slad: list[KrokTerminu]


class StanWersji(BaseModel):
    numer: int
    powod: PowodWersji
    status: StatusWersji
    hash: str
    data_sporzadzenia: date
    data_zatwierdzenia: date
    data_wydruku: date | None
    doreczenie: Doreczenie | None
    odwolanie: Odwolanie | None
    termin_odwolania: DataTerminu | None = None
    termin_rozpatrzenia: DataTerminu | None = None
    ponowna_ocena: DataTerminu | None = None


def termin(data: date, w: WersjaRegul, nazwa: str) -> DataTerminu:
    regula = w.regula_terminu(nazwa)
    wolne = dni_wolne(w.terminy, data.year, data.year + 1) if regula.przesuniecie_dnia_wolnego else frozenset()
    wynik = wyznacz_termin(data, regula, dni_wolne=wolne)
    dzien, slad = wynik.data, list(wynik.slad)
    if nazwa == "ponowna_ocena":
        dzien += timedelta(days=1)
        slad.append(KrokObliczenia(regula.regula, f"Ponowna ocena po upływie 6 miesięcy: od {dzien}."))
    elif len(slad) > 1:
        opis_swiat = w.regula("termin.swieta-demo")
        slad.append(KrokObliczenia(w.regula_core(opis_swiat.id), opis_swiat.opis))
    return DataTerminu(data_odniesienia=data, data=dzien, slad=[w.krok_terminu(k) for k in slad])


def stan_wersji(v: WersjaOceny, w: WersjaRegul) -> StanWersji:
    stan = StanWersji(
        numer=v.tresc.numer, powod=v.powod, status=v.status, hash=v.hash,
        data_sporzadzenia=v.tresc.data_sporzadzenia, data_zatwierdzenia=v.data_zatwierdzenia,
        data_wydruku=v.data_wydruku, doreczenie=v.doreczenie, odwolanie=v.odwolanie,
    )
    if v.doreczenie:
        zapoznanie = v.doreczenie.data_zapoznania
        if v.powod != "ZMIANA_PO_ODWOLANIU" and "odwolanie" in w.terminy:
            stan.termin_odwolania = termin(zapoznanie, w, "odwolanie")
        if v.tresc.wynik == "NEGATYWNA" and "ponowna_ocena" in w.terminy:
            stan.ponowna_ocena = termin(zapoznanie, w, "ponowna_ocena")
    if v.odwolanie and "rozpatrzenie_odwolania" in w.terminy:
        stan.termin_rozpatrzenia = termin(v.odwolanie.data_wniesienia, w, "rozpatrzenie_odwolania")
    return stan
