"""Odczyt pól ocen z przykładowych formularzy A, B i C."""

from dataclasses import dataclass
from pathlib import Path
import re
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from pypdf import PdfReader


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_DATA = re.compile(r"(?<!\d)\d{1,2}\s*[./-]\s*\d{1,3}\s*[./-]\s*\d{4}(?!\d)")
_ZNAKI = {"x", "×", "☒", "☑", "✓"}
_ZNAK_PDF = re.compile(r"(?<!\S)[xX×☒☑✓](?!\S)")
_WYNIKI = ("pozytywna", "negatywna")
_ETYKIETA_SREDNIEJ = "średnia arytmetyczna z ocen cząstkowych"
_PUNKTY = (1, 3, 5, 7, 9)


@dataclass(frozen=True)
class Pole:
    lokalizator: str
    przed: tuple[str, ...]
    po: tuple[str, ...]


@dataclass(frozen=True)
class OcenaCzastkowa:
    nazwa: str
    zaznaczenia: Pole


@dataclass(frozen=True)
class PolaArkusza:
    daty: list[Pole]
    oceny: list[OcenaCzastkowa]
    srednia: Pole
    poziom: Pole
    wynik: Pole
    liczba_zmian: int = 0


@dataclass(frozen=True)
class _Wiersz:
    lokalizator: str
    przed: tuple[str, ...]
    po: tuple[str, ...]


class BrakPolArkusza(ValueError):
    """Dokument nie zawiera rozpoznawalnej tabeli ocen."""


def _tekst(e: ET.Element, przed: bool = False) -> str:
    if e.tag in ((_W + "ins", _W + "moveTo") if przed else (_W + "del", _W + "moveFrom")):
        return ""
    if e.tag in {_W + "t", _W + "delText"}:
        return e.text or ""
    if e.tag in {_W + "tab", _W + "br", _W + "cr"}:
        return " "
    return "".join(_tekst(dziecko, przed) for dziecko in e)


def _komorka(e: ET.Element, przed: bool) -> str:
    return " ".join(
        tekst for p in e.iter(_W + "p") if (tekst := " ".join(_tekst(p, przed).split()))
    )


def _wiersze(tabela: ET.Element, numer_tabeli: int) -> list[_Wiersz]:
    wynik = []
    for numer, wiersz in enumerate(tabela.findall(_W + "tr"), start=1):
        komorki = wiersz.findall(_W + "tc")
        wynik.append(_Wiersz(
            f"tabela {numer_tabeli}, wiersz {numer}",
            tuple(_komorka(k, True) for k in komorki),
            tuple(_komorka(k, False) for k in komorki),
        ))
    return wynik


def _zaznaczony(tekst: str) -> bool:
    return tekst.strip().casefold() in _ZNAKI


def _zaznaczone_punkty(wiersz: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(str(p) for p, tekst in zip(_PUNKTY, wiersz[1:]) if _zaznaczony(tekst))


def _wynik_docx(wiersze: list[_Wiersz], przed: bool) -> tuple[str, ...]:
    for w in wiersze:
        tresc = w.przed if przed else w.po
        if len(tresc) == 5 and all(nazwa in tresc[i].casefold() for i, nazwa in zip((1, 3), _WYNIKI)):
            return tuple(nazwa for i, nazwa in zip((2, 4), _WYNIKI) if _zaznaczony(tresc[i]))
    return ()


def _poziom_docx(wiersze: list[_Wiersz], przed: bool) -> tuple[str, ...]:
    wynik = []
    for w in wiersze:
        tresc = w.przed if przed else w.po
        if len(tresc) == 3 and _zaznaczony(tresc[0]):
            wynik.append(tresc[2].casefold())
    return tuple(wynik)


def odczytaj_docx(plik: Path) -> PolaArkusza:
    with ZipFile(plik) as archiwum:
        dokument = ET.fromstring(archiwum.read("word/document.xml"))
    tabele = [
        _wiersze(tabela, i)
        for i, tabela in enumerate(dokument.findall(f"{_W}body/{_W}tbl"), start=1)
    ]
    daty = []
    for i, p in enumerate(dokument.iter(_W + "p"), start=1):
        przed, po = tuple(_DATA.findall(_tekst(p, True))), tuple(_DATA.findall(_tekst(p)))
        if przed or po:
            daty.append(Pole(f"akapit {i}", przed, po))

    for i, wiersze in enumerate(tabele):
        naglowek = next((
            nr for nr, w in enumerate(wiersze)
            if len(w.po) == 6 and tuple(t.partition(" ")[0] for t in w.po[1:]) == tuple(map(str, _PUNKTY))
        ), None)
        if naglowek is not None:
            break
    else:
        raise BrakPolArkusza("Brakuje tabeli z kolumnami punktów 1, 3, 5, 7 i 9.")

    oceny = []
    for w in wiersze[naglowek + 1:]:
        if len(w.po) == 2 and w.po[0].casefold().startswith(_ETYKIETA_SREDNIEJ):
            srednia = Pole(w.lokalizator, (w.przed[1],), (w.po[1],))
            break
        if len(w.po) != 6:
            continue
        przed, po = _zaznaczone_punkty(w.przed), _zaznaczone_punkty(w.po)
        if w.po[0] or przed or po:
            oceny.append(OcenaCzastkowa(w.po[0], Pole(w.lokalizator, przed, po)))
    else:
        raise BrakPolArkusza("Brakuje wiersza średniej po ocenach cząstkowych.")

    podsumowanie = tabele[i + 1] if i + 1 < len(tabele) else []
    lokalizacja = f"tabela {i + 2}, podsumowanie"
    rewizje = {_W + nazwa for nazwa in ("ins", "del", "moveFrom", "moveTo")}
    return PolaArkusza(
        daty, oceny, srednia,
        Pole(lokalizacja, _poziom_docx(podsumowanie, True), _poziom_docx(podsumowanie, False)),
        Pole(lokalizacja, _wynik_docx(podsumowanie, True), _wynik_docx(podsumowanie, False)),
        sum(e.tag in rewizje for e in dokument.iter()),
    )


def _pole_pdf(lokalizator: str, wartosci: tuple[str, ...]) -> Pole:
    return Pole(lokalizator, wartosci, wartosci)


def _oceny_pdf(wiersze: list[str], strona: int) -> tuple[list[OcenaCzastkowa], Pole, int] | None:
    for i, wiersz in enumerate(wiersze[:-1]):
        liczby = list(re.finditer(r"\b[13579]\b", wiersz))
        if tuple(int(m[0]) for m in liczby) == _PUNKTY and "punkt" in wiersze[i + 1].casefold():
            break
    else:
        return None

    kolumny = {p: m.start() for p, m in zip(_PUNKTY, liczby)}
    granica = kolumny[1] - 4
    oceny = []
    for nr, wiersz in enumerate(wiersze[i + 2:], start=i + 2):
        lokalizator = f"s. {strona}, wiersz {nr + 1}"
        if wiersz.strip().casefold().startswith(_ETYKIETA_SREDNIEJ):
            liczba = re.search(r"\d+(?:[,.]\d+)?\s*$", wiersz)
            return oceny, _pole_pdf(lokalizator, (liczba[0].strip(),) if liczba else ("",)), nr
        nazwa = wiersz[:granica].strip()
        if nazwa.casefold().startswith(("kryteria dodatkowe", "oceniającego")):
            continue
        punkty = []
        for znak in _ZNAK_PDF.finditer(wiersz, granica):
            punkt = min(kolumny, key=lambda p: abs(znak.start() - kolumny[p]))
            if abs(znak.start() - kolumny[punkt]) > 4:
                raise BrakPolArkusza(f"Nie można przypisać zaznaczenia do kolumny na {lokalizator}.")
            punkty.append(str(punkt))
        if nazwa or punkty:
            oceny.append(OcenaCzastkowa(nazwa, _pole_pdf(lokalizator, tuple(punkty))))
    raise BrakPolArkusza("Brakuje wiersza średniej po ocenach cząstkowych.")


def _podsumowanie_pdf(wiersze: list[str], poziomy: list[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    zaznaczone_poziomy = []
    zaznaczone_wyniki = []
    for wiersz in wiersze:
        if _ZNAK_PDF.match(wiersz.lstrip()):
            tekst = " ".join(wiersz.casefold().split())
            poziom = next((p for p in sorted(poziomy, key=len, reverse=True) if tekst.endswith(p)), None)
            if poziom:
                zaznaczone_poziomy.append(poziom)
        w = wiersz.casefold()
        a, b = (w.find(nazwa) for nazwa in _WYNIKI)
        if "w służbie" in w and 0 <= a < b:
            for znak in _ZNAK_PDF.finditer(wiersz, a):
                zaznaczone_wyniki.append(_WYNIKI[int(znak.start() >= b)])
            break
    return tuple(zaznaczone_poziomy), tuple(zaznaczone_wyniki)


def odczytaj_pdf(plik: Path, poziomy: list[str]) -> PolaArkusza:
    strony = [
        (p.extract_text(extraction_mode="layout") or "").splitlines()
        for p in PdfReader(plik).pages
    ]
    daty = [
        _pole_pdf(f"s. {nr}, wiersz {i}", tuple(znalezione))
        for nr, wiersze in enumerate(strony, start=1)
        for i, wiersz in enumerate(wiersze, start=1)
        if (znalezione := _DATA.findall(wiersz))
    ]
    for nr, wiersze in enumerate(strony, start=1):
        tabela = _oceny_pdf(wiersze, nr)
        if tabela is None:
            continue
        oceny, srednia, ostatni = tabela
        poziom, wynik = _podsumowanie_pdf(wiersze[ostatni + 1:], poziomy)
        lokalizacja = f"s. {nr}, podsumowanie"
        return PolaArkusza(
            daty, oceny, srednia, _pole_pdf(lokalizacja, poziom), _pole_pdf(lokalizacja, wynik)
        )
    raise BrakPolArkusza("PDF nie zawiera rozpoznawalnego układu kolumn ocen.")
