"""Walidacja i eksport danych demonstracyjnych z przykładowych arkuszy (#18)."""

from __future__ import annotations

import argparse
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from difflib import get_close_matches
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from typing import Literal
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

from fastapi import APIRouter, HTTPException, Request
from caoo_core.skala import KonfiguracjaSkali, oblicz_wynik
from caoo_core.zrodla import SourceRef, normalizuj
from pydantic import BaseModel, Field
from pypdf.errors import PdfReadError

from .arkusze_zrodlowe import BrakPolArkusza, Pole, odczytaj_docx, odczytaj_pdf
from .korpus_aplikacji import zaladuj
from .reguly import WersjaRegul, wczytaj_wersje, wersja_na_dzien
from .ustawienia import Ustawienia


FOLDER_PRZYKLADOW = "5. Przykładowe wypełnione formularze oceny"
Wersja = Literal["przed", "po"]
_NUMER = re.compile(r"^(\d+)\)")
_TYP_ARKUSZA = re.compile(r"\barkusz (?:oceny )?([ABC])\b", re.IGNORECASE)
_ZAPIS_DATY = re.compile(r"(\d{1,2})\s*([./-])\s*(\d{1,2})\s*\2\s*(\d{4})")
_ZAPIS_SREDNIEJ = re.compile(r"\d+(?:[,.]\d+)?")


class UwagaImportu(BaseModel):
    kod: str
    poziom: Literal["blad", "ostrzezenie"]
    lokalizator: str
    komunikat: str


class ZmianaArkusza(BaseModel):
    id: str
    pole: str
    lokalizator: str
    przed: tuple[str, ...]
    po: tuple[str, ...]
    wybrana: Wersja | None


class DataArkusza(BaseModel):
    lokalizator: str
    zapis: str
    data: date | None


class KryteriumArkusza(BaseModel):
    lokalizator: str
    nazwa: str
    punkty: int | None


class RaportArkusza(BaseModel):
    plik: str
    wersja_regul: str
    demo: Literal[True] = True
    gotowy: bool = False
    liczba_sledzonych_zmian: int = 0
    daty: list[DataArkusza] = Field(default_factory=list)
    kryteria: list[KryteriumArkusza] = Field(default_factory=list)
    zmiany: list[ZmianaArkusza] = Field(default_factory=list)
    uwagi: list[UwagaImportu] = Field(default_factory=list)
    srednia_wpisana: str | None = None
    srednia_przeliczona: str | None = None
    poziom_wpisany: str | None = None
    poziom_przeliczony: str | None = None
    wynik_wpisany: str | None = None
    wynik_przeliczony: str | None = None
    podstawa_wyniku: SourceRef | None = None

    def uwaga(
        self, kod: str, lokalizator: str, komunikat: str,
        poziom: Literal["blad", "ostrzezenie"] = "blad",
    ) -> None:
        self.uwagi.append(UwagaImportu(kod=kod, poziom=poziom, lokalizator=lokalizator, komunikat=komunikat))


class ZapytanieImportu(BaseModel):
    plik: str
    wybory: dict[str, Wersja] = Field(default_factory=dict)


def _nazwa(tekst: str) -> str:
    return re.sub(r"\s*/\s*", "/", normalizuj(tekst)).casefold()


def _data(zapis: str) -> date | None:
    m = _ZAPIS_DATY.fullmatch(zapis)
    if m is not None:
        try:
            return date(int(m[4]), int(m[3]), int(m[1]))
        except ValueError:
            pass
    return None


def pliki_do_importu(katalog: Path) -> list[str]:
    if not katalog.is_dir():
        return []

    def klucz(nazwa: str) -> tuple[int, str]:
        m = _NUMER.match(nazwa)
        return (int(m[1]) if m else 0, nazwa.casefold())

    return sorted(
        (p.name for p in katalog.iterdir() if p.is_file() and not p.is_symlink()
         and p.suffix.lower() in {".docx", ".pdf"}),
        key=klucz,
    )


def analizuj_arkusz(katalog: Path, zapytanie: ZapytanieImportu, wersja: WersjaRegul) -> RaportArkusza:
    if zapytanie.plik not in pliki_do_importu(katalog):
        raise FileNotFoundError("Nie ma takiego arkusza w folderze przykładów.")
    plik = katalog / zapytanie.plik
    raport = RaportArkusza(plik=plik.name, wersja_regul=wersja.wersja)
    skala = wersja.konfiguracja_skali()
    try:
        pola = (
            odczytaj_docx(plik) if plik.suffix.lower() == ".docx"
            else odczytaj_pdf(plik, [p.poziom.casefold() for p in skala.progi])
        )
    except BrakPolArkusza as e:
        raport.uwaga("NIECZYTELNY_ARKUSZ", "dokument", str(e))
        return raport
    except (OSError, BadZipFile, ParseError, KeyError, PdfReadError):
        raport.uwaga("NIECZYTELNY_ARKUSZ", "dokument", "Nie udało się odczytać pliku formularza.")
        return raport

    raport.liczba_sledzonych_zmian = pola.liczba_zmian

    def wybierz(rodzaj: str, nazwa: str, pole: Pole) -> tuple[str, ...] | None:
        if pole.przed == pole.po:
            return pole.po
        id = f"{rodzaj}:{pole.lokalizator}"
        wybor = zapytanie.wybory.get(id)
        raport.zmiany.append(ZmianaArkusza(
            id=id, pole=nazwa, lokalizator=pole.lokalizator,
            przed=pole.przed, po=pole.po, wybrana=wybor,
        ))
        return None if wybor is None else (pole.przed if wybor == "przed" else pole.po)

    for pole in pola.daty:
        for zapis in wybierz("data", "Data", pole) or ():
            data = _data(zapis)
            raport.daty.append(DataArkusza(lokalizator=pole.lokalizator, zapis=zapis, data=data))
            if data is None:
                raport.uwaga("BLEDNA_DATA", pole.lokalizator, f"Data „{zapis}” wymaga korekty.")

    wykaz = {_nazwa(k["nazwa"]): k for k in wersja.kryteria["wykaz"]}
    znalezione = set()
    for ocena in pola.oceny:
        pole = ocena.zaznaczenia
        dopasowanie = wykaz.get(_nazwa(ocena.nazwa))
        if dopasowanie is not None:
            if dopasowanie["id"] in znalezione:
                raport.uwaga("POWTORZONE_KRYTERIUM", pole.lokalizator, f"Kryterium „{ocena.nazwa}” występuje więcej niż raz.")
            znalezione.add(dopasowanie["id"])
        elif ocena.nazwa:
            podpowiedzi = get_close_matches(_nazwa(ocena.nazwa), wykaz, n=1, cutoff=0.65)
            wskazowka = (
                f" W wykazie najbliższa nazwa to „{wykaz[podpowiedzi[0]]['nazwa'].capitalize()}”."
                if podpowiedzi else ""
            )
            raport.uwaga(
                "NAZWA_KRYTERIUM", pole.lokalizator,
                f"„{ocena.nazwa}” różni się od nazw w wykazie kryteriów.{wskazowka}", "ostrzezenie",
            )
        else:
            raport.uwaga("BRAK_NAZWY_KRYTERIUM", pole.lokalizator, "Zaznaczono ocenę w wierszu bez nazwy kryterium.")
        zaznaczone = wybierz("punkty", ocena.nazwa or "Punkty kryterium", pole)
        punkty = int(zaznaczone[0]) if zaznaczone is not None and len(zaznaczone) == 1 else None
        if zaznaczone is not None and punkty not in skala.dozwolone_oceny:
            raport.uwaga("ZAZNACZENIA_KRYTERIUM", pole.lokalizator, "Kryterium musi mieć dokładnie jedną ocenę z dozwolonej skali.")
            punkty = None
        raport.kryteria.append(KryteriumArkusza(lokalizator=pole.lokalizator, nazwa=ocena.nazwa, punkty=punkty))

    typ = _TYP_ARKUSZA.search(plik.name)
    obowiazkowe = wersja.kryteria["obowiazkowe"].get(typ[1].upper(), {}) if typ else {}
    brakujace = set(obowiazkowe.get("kryteria", [])) - znalezione
    if brakujace:
        nazwy = ", ".join(k["nazwa"].capitalize() for k in wykaz.values() if k["id"] in brakujace)
        raport.uwaga("BRAK_KRYTERIOW", "tabela ocen", f"Brak kryteriów obowiązkowych. {nazwy}.")

    srednie = wybierz("srednia", "Średnia wpisana", pola.srednia)
    if srednie is not None:
        zapis = srednie[0] if srednie else ""
        if zapis and _ZAPIS_SREDNIEJ.fullmatch(zapis):
            raport.srednia_wpisana = zapis
        else:
            raport.uwaga("SREDNIA_W_ARKUSZU", pola.srednia.lokalizator, "Średnia wpisana w arkuszu wymaga korekty.")

    for klucz, etykieta, pole in (
        ("poziom", "Ogólny poziom", pola.poziom), ("wynik", "Wynik oceny", pola.wynik),
    ):
        wartosci = wybierz(klucz, etykieta, pole)
        if wartosci is None:
            continue
        if len(wartosci) == 1:
            setattr(raport, f"{klucz}_wpisany", wartosci[0])
        else:
            raport.uwaga("ZAZNACZENIA_WYNIKU", pole.lokalizator, f"Pole „{etykieta}” wymaga jednego zaznaczenia.")

    if not raport.kryteria:
        raport.uwaga("BRAK_OCEN_CZASTKOWYCH", "tabela ocen", "Arkusz nie zawiera kryteriów z punktami.")
    elif not brakujace and all(k.punkty is not None for k in raport.kryteria):
        _sprawdz_wynik(raport, skala, pola.srednia.lokalizator, pola.wynik.lokalizator)

    if zapytanie.wybory.keys() - {z.id for z in raport.zmiany}:
        raise ValueError("Wybór dotyczy pola, które nie występuje w tym arkuszu.")
    raport.gotowy = all(z.wybrana is not None for z in raport.zmiany) and not any(
        u.poziom == "blad" for u in raport.uwagi
    )
    return raport


def _sprawdz_wynik(
    raport: RaportArkusza, skala: KonfiguracjaSkali, lokalizator_sredniej: str, lokalizator_wyniku: str
) -> None:
    wynik = oblicz_wynik([k.punkty for k in raport.kryteria if k.punkty is not None], skala)
    wpisana = Decimal(raport.srednia_wpisana.replace(",", ".")) if raport.srednia_wpisana else None
    dokladnosc = Decimal(1).scaleb(wpisana.as_tuple().exponent) if wpisana is not None else Decimal("0.01")
    wyliczona = wynik.srednia.quantize(dokladnosc, rounding=ROUND_HALF_UP)
    raport.srednia_przeliczona = format(wyliczona, "f").replace(".", ",")
    raport.poziom_przeliczony = wynik.poziom
    raport.wynik_przeliczony = wynik.wynik.casefold()
    raport.podstawa_wyniku = wynik.regula_rozstrzygajaca.zrodlo
    if wpisana is not None and wpisana != wyliczona:
        raport.uwaga(
            "ROZBIEZNOSC_SREDNIEJ", lokalizator_sredniej,
            f"Wpisano {raport.srednia_wpisana}, a z ocen cząstkowych wynika {raport.srednia_przeliczona}.",
            "ostrzezenie",
        )
    if raport.wynik_wpisany is not None and raport.wynik_wpisany != raport.wynik_przeliczony:
        raport.uwaga(
            "ROZBIEZNOSC_WYNIKU", lokalizator_wyniku,
            f"Wynik w arkuszu to „{raport.wynik_wpisany}”, a zgodnie z § 11 to „{raport.wynik_przeliczony}”.",
            "ostrzezenie",
        )
    if raport.poziom_wpisany is not None and _nazwa(raport.poziom_wpisany) != _nazwa(wynik.poziom or ""):
        raport.uwaga(
            "ROZBIEZNOSC_POZIOMU", lokalizator_wyniku,
            f"Wpisany poziom to „{raport.poziom_wpisany}”, a obliczony to „{wynik.poziom}”.", "ostrzezenie",
        )


def router(auth) -> APIRouter:
    r = APIRouter(prefix="/api/import/arkusze", tags=["import przykładów"])

    @r.get("")
    def lista(request: Request, uz=auth.wymagaj("import.arkusze")) -> list[str]:
        return pliki_do_importu(request.app.state.korpus.katalog_zrodel / FOLDER_PRZYKLADOW)

    @r.post("/raport")
    def raport(body: ZapytanieImportu, request: Request, uz=auth.wymagaj("import.arkusze")) -> RaportArkusza:
        katalog = request.app.state.korpus.katalog_zrodel / FOLDER_PRZYKLADOW
        wersja = wersja_na_dzien(request.app.state.wersje, date.today())
        try:
            return analizuj_arkusz(katalog, body, wersja)
        except FileNotFoundError as e:
            raise HTTPException(404, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(422, detail=str(e)) from e

    return r


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plik", help="Nazwa DOCX lub PDF z folderu przykładów.")
    parser.add_argument("--wybor", action="append", default=[], metavar="ID=przed|po")
    parser.add_argument("--zapisz", type=Path, metavar="RAPORT.json")
    args = parser.parse_args()
    ust = Ustawienia.ze_srodowiska()
    katalog = zaladuj(ust.manifest).katalog_zrodel / FOLDER_PRZYKLADOW
    wersja = wersja_na_dzien(wczytaj_wersje(ust.reguly), date.today())
    try:
        raport = analizuj_arkusz(katalog, ZapytanieImportu(
            plik=args.plik, wybory=dict(w.split("=", 1) for w in args.wybor)
        ), wersja)
    except (FileNotFoundError, ValueError) as e:
        parser.error(str(e))
    tresc = raport.model_dump_json(indent=2, by_alias=True)
    if args.zapisz is None:
        print(tresc)
        return
    if args.zapisz.suffix.lower() != ".json" or not raport.gotowy:
        parser.error("Zapis wymaga nowego pliku JSON i raportu gotowego do importu.")
    tymczasowy = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=args.zapisz.parent, delete=False) as wyjscie:
            tymczasowy = Path(wyjscie.name)
            wyjscie.write(tresc + "\n")
        os.link(tymczasowy, args.zapisz)
    except OSError as e:
        parser.error(str(e))
    finally:
        if tymczasowy is not None:
            tymczasowy.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
