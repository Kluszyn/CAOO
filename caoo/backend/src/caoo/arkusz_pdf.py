"""Wydruk arkusza A, B lub C według załączników 2–4 do rozporządzenia."""

from contextlib import contextmanager
from datetime import date
from typing import Literal

from caoo_core.pdf import DokumentPdf, Komorka, Pole, Stopka
from pydantic import BaseModel
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, PageBreak

WERSJA_FORMULARZA = "2016.470"
ZALACZNIKI = {"A": 2, "B": 3, "C": 4}


class DaneOsoby(BaseModel):
    imie: str = ""
    nazwisko: str = ""
    stanowisko: str = ""
    komorka: str = ""
    pelna_nazwa: str = ""


class PozycjaArkusza(BaseModel):
    nazwa: str
    punkty: int
    oczekiwania: str | None


class PrzedzialSkali(BaseModel):
    poziom: str
    minimum: int
    maksimum: int
    domkniety_od_dolu: bool


class PoprzedniWynik(BaseModel):
    wynik: str
    poziom: str
    data_sporzadzenia: date


class TrescArkusza(BaseModel):
    cykl_id: int
    pracownik_id: str
    numer: int
    arkusz: Literal["A", "B", "C"]
    wersja_formularza: str = WERSJA_FORMULARZA
    wersja_regul: str
    jednostka: str
    oceniany: DaneOsoby
    oceniajacy: DaneOsoby
    urzednik: bool
    poprzedni_wynik: PoprzedniWynik | None
    obowiazkowe: list[PozycjaArkusza]
    dodatkowe: list[PozycjaArkusza]
    skala: list[int]
    przedzialy: list[PrzedzialSkali]
    termin_sporzadzenia: date | None
    data_ustalenia_kryteriow: date
    data_potwierdzenia_kryteriow: date | None
    data_rozmowy_oceniajacej: date | None
    data_sporzadzenia: date
    srednia: str
    poziom: str | None
    wynik: Literal["POZYTYWNA", "NEGATYWNA"]
    uzasadnienie: str | None
    wnioski_rozwojowe: str
    dni_na_odwolanie: int | None


class _ArkuszPdf(DokumentPdf):
    def podzial_strony(self) -> None:
        self._elementy.append(PageBreak())

    @contextmanager
    def razem(self):
        poczatek = len(self._elementy)
        yield
        self._elementy[poczatek:] = [KeepTogether(self._elementy[poczatek:])]


def _data(dzien: date | None) -> str:
    return dzien.strftime("%d.%m.%Y") if dzien else "........................"


def _osoba(doc: DokumentPdf, etykieta: str, osoba: DaneOsoby, arkusz: str) -> None:
    pola = ([["Imię i nazwisko", osoba.pelna_nazwa]] if osoba.pelna_nazwa
            else [["Imię", osoba.imie], ["Nazwisko", osoba.nazwisko]])
    if arkusz != "A":
        pola.append(["Komórka organizacyjna", osoba.komorka])
    pola.append(["Stanowisko pracy", osoba.stanowisko])
    doc.tabela([[etykieta, ""], *pola], [57 * mm, 110 * mm])


def _wiersze_punktow(
    kryteria: list[PozycjaArkusza], skala: list[int], ile: int,
) -> list[list[Komorka]]:
    wiersze: list[list[Komorka]] = [
        [k.nazwa, *(Pole(k.punkty == p) for p in skala)] for k in kryteria
    ]
    wiersze.extend([["", *["" for _ in skala]] for _ in range(ile - len(kryteria))])
    return wiersze


def generuj_arkusz(t: TrescArkusza, skrot: str) -> bytes:
    doc = _ArkuszPdf(Stopka(
        wersja=str(t.numer), hash=skrot,
        tekst=f"Arkusz {t.arkusz} · formularz {t.wersja_formularza} · reguły {t.wersja_regul} · do podpisu odręcznego",
    ))
    doc.naglowek(f"ARKUSZ {t.arkusz} | Załącznik nr {ZALACZNIKI[t.arkusz]} (Dz.U. 2016 poz. 470)", 2)
    doc.naglowek("OCENA OKRESOWA URZĘDNIKA SŁUŻBY CYWILNEJ LUB PRACOWNIKA SŁUŻBY CYWILNEJ")
    doc.naglowek("CZĘŚĆ I", 2)
    doc.akapit(f"Nazwa urzędu: {t.jednostka}")
    _osoba(doc, "DANE OCENIANEGO", t.oceniany, t.arkusz)
    _osoba(doc, "DANE OCENIAJĄCEGO", t.oceniajacy, t.arkusz)
    doc.naglowek("KRYTERIA OCENY I TERMIN SPORZĄDZENIA OCENY", 2)
    dodatkowe = [k.nazwa for k in t.dodatkowe] + [""] * (len(t.obowiazkowe) - len(t.dodatkowe))
    doc.tabela([
        ["Kryteria obowiązkowe", "Kryteria dodatkowe wybrane przez oceniającego (nie więcej niż 3)"],
        *[[k.nazwa, d] for k, d in zip(t.obowiazkowe, dodatkowe)],
    ], [86 * mm, 81 * mm])
    doc.akapit(f"Termin sporządzenia oceny: {_data(t.termin_sporzadzenia)}")
    doc.akapit("Potwierdzamy przeprowadzenie rozmowy (§ 5 ust. 1) oraz zapoznanie "
               "ocenianego z kryteriami oceny i terminem sporządzenia oceny.")
    with doc.razem():
        doc.akapit(f"Miejscowość: ........................    Data: {_data(t.data_ustalenia_kryteriow)}")
        doc.podpis("podpis oceniającego")
        doc.akapit(f"Miejscowość: ........................    Data: {_data(t.data_potwierdzenia_kryteriow)}")
        doc.podpis("podpis ocenianego")

    doc.podzial_strony()
    doc.naglowek("CZĘŚĆ II", 2)
    doc.akapit(f"Data przeprowadzenia rozmowy oceniającej: {_data(t.data_rozmowy_oceniajacej)}")
    doc.akapit(f"Okres, w którym oceniany podlegał ocenie: od {_data(t.data_potwierdzenia_kryteriow)} "
               f"do {_data(t.data_sporzadzenia)}")
    szerokosci = [69 * mm, *[98 * mm / len(t.skala) for _ in t.skala]]
    doc.tabela([
        ["Kryteria obowiązkowe\nOcena cząstkowa", *[f"{p} pkt\n{s.poziom}" for p, s in zip(t.skala, t.przedzialy)]],
        *_wiersze_punktow(t.obowiazkowe, t.skala, len(t.obowiazkowe)),
    ], szerokosci)
    doc.tabela([
        ["Kryteria dodatkowe\nOcena cząstkowa", *[str(p) for p in t.skala]],
        *_wiersze_punktow(t.dodatkowe, t.skala, 3),
    ], szerokosci)
    doc.akapit(f"Średnia arytmetyczna ocen cząstkowych: {t.srednia.replace('.', ',')}")
    doc.naglowek("PRZYZNANIE OCENY", 2)
    doc.tabela([
        ["Znak X", "Średnia punktów", "Ogólny poziom spełnienia kryteriów"],
        *[[Pole(t.poziom == p.poziom),
           f"{'od' if p.domkniety_od_dolu else 'powyżej'} {p.minimum} do {p.maksimum}", p.poziom]
          for p in t.przedzialy],
    ], [20 * mm, 58 * mm, 89 * mm])
    doc.tabela([
        ["Ocena okresowa w służbie cywilnej", "POZYTYWNA", "NEGATYWNA"],
        ["Wynik", Pole(t.wynik == "POZYTYWNA"), Pole(t.wynik == "NEGATYWNA")],
    ], [89 * mm, 39 * mm, 39 * mm])

    doc.podzial_strony()
    doc.naglowek("UZASADNIENIE PRZYZNANEJ OCENY OKRESOWEJ", 2)
    doc.akapit(t.uzasadnienie or ".......................................................................................................")
    doc.naglowek("WNIOSKI DOTYCZĄCE INDYWIDUALNEGO PROGRAMU ROZWOJU ZAWODOWEGO", 2)
    doc.akapit(t.wnioski_rozwojowe or ".......................................................................................................\n"
               ".......................................................................................................")
    if t.urzednik:
        poprzedni = t.poprzedni_wynik
        doc.naglowek("DANE DOTYCZĄCE POPRZEDNIEJ OCENY OKRESOWEJ URZĘDNIKA", 2)
        doc.tabela([
            ["Ocena", "Poziom", "Data sporządzenia"],
            [poprzedni.wynik, poprzedni.poziom, _data(poprzedni.data_sporzadzenia)] if poprzedni else ["", "", ""],
        ], [43 * mm, 79 * mm, 45 * mm])
    doc.tabela([
        ["Dołączono wniosek o przyznanie kolejnego stopnia służbowego", "TAK", "NIE", "NIE DOTYCZY"],
        ["Wniosek", Pole(), Pole(t.urzednik), Pole(not t.urzednik)],
    ], [98 * mm, 17 * mm, 17 * mm, 35 * mm])
    with doc.razem():
        _osoba(doc, "DANE OCENIAJĄCEGO", t.oceniajacy, t.arkusz)
        doc.akapit(f"Miejscowość: ........................    Data sporządzenia: {_data(t.data_sporzadzenia)}")
        doc.podpis("podpis oceniającego")
        doc.akapit("Przeprowadzono ze mną rozmowę oceniającą. Zapoznałem(-łam) się z oceną okresową "
                   "i wnioskami dotyczącymi indywidualnego programu rozwoju zawodowego.")
        doc.akapit(f"Miejscowość: ........................    Data zapoznania: {_data(None)}\n"
                   f"Imię i nazwisko ocenianego: {t.oceniany.imie} {t.oceniany.nazwisko}")
        doc.podpis("podpis ocenianego")
        doc.naglowek("Pouczenie", 2)
        if t.dni_na_odwolanie is None:
            doc.akapit("Ocena zmieniona po uwzględnieniu sprzeciwu (art. 83 ust. 4 ustawy o służbie cywilnej).")
        else:
            doc.akapit(f"Art. 83 ust. 1 ustawy o służbie cywilnej: sprzeciw wnosi się w ciągu "
                       f"{t.dni_na_odwolanie} dni od zapoznania z oceną do dyrektora generalnego urzędu "
                       "lub do kierownika urzędu, gdy w urzędzie nie tworzy się stanowiska dyrektora generalnego.")
    return doc.bajty()
