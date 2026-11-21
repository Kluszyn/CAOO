"""Aktualizacja przepisów v1 → v2 i odtworzenie oceny (#25, V1-18; spec #2, historyjki 21–26, 127).

Przebieg: wgranie aktu zmieniającego (aktywne reguły bez zmian) → analiza LLM (dla każdego
parametru z katalogu: wybór „zmienia” i dosłowne cytaty z aktu i z obecnych przepisów) →
decyzje administratora (przyjęcie, wartość) → zestawienie przed/po z wpływem na scenariusze
kontrolne i oceny → publikacja nowej wersji z datą obowiązywania. Zakończone oceny zostają przy
swojej wersji reguł; oceny w toku z niejasnym przejściem dostają zadanie decyzyjne.

Treść (parametry, pytania, wzorce, scenariusze, akt demo) jest w `konfiguracja/aktualizacja.yaml`.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import closing, contextmanager
import os
import re
import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal

import yaml
from fastapi import APIRouter, HTTPException, Request
from caoo_core.llm import (
    BrakOdpowiedzi, Dziennik, LlmProvider, Odpowiedz, Tryb, Zapytanie, klucz_zapytania, wywolaj, z_konfiguracji,
)
from caoo_core.skala import oblicz_wynik
from caoo_core.zrodla import Fragment, Kategoria, Korpus, SourceRef, StatusEkstrakcji, WynikEkstrakcji
from caoo_core.zrodla.walidator import cytat_w_tekscie, waliduj_adnotacje
from pydantic import BaseModel, Field, StringConstraints

from .cykl import Cykl, _zapisz
from .graf import zbuduj_katalog
from .korpus_aplikacji import Dokument, KorpusV1
from .ocena_cyklu import pobierz_cykl
from .reguly import RegulaV1, WersjaRegul, wersja_na_dzien

AKCJA = "przepisy.zarzadzaj"
OPERACJA = "diff_przepisow"


# --- konfiguracja -------------------------------------------------------------------


class Zastosowanie(BaseModel):
    """Gdzie nowa wartość trafia w wersji reguł (treść z konfiguracji, nie z kodu)."""

    poziom_ponizej: str | None = None
    poziom_granicy: str | None = None
    wynik: str | None = None
    nazwa_reguly: str | None = None


RodzajParametru = Literal["granica_pozytywnej", "limit_dodatkowych"]


class Parametr(BaseModel):
    id: str
    nazwa: str
    rodzaj: RodzajParametru
    reguly: list[str]
    pytanie: str
    wzorzec: str
    zastosowanie: Zastosowanie


class OdpowiedzDemo(BaseModel):
    zmienia: bool
    cytaty_aktu: list[str]
    cytaty_przed: list[str]


class Scenariusz(BaseModel):
    id: str
    opis: str
    oceny: list[int]


class DataWejscia(BaseModel):
    wzorzec: str
    miesiace: list[str]


class Obszar(BaseModel):
    id: str
    nazwa: str
    sekcja: Literal["skala", "kryteria", "terminy", "graf", "niezalezny"]
    wplyw: str


class KonfiguracjaAktualizacji(BaseModel):
    wersja_promptu: str
    akt_demo: str
    llm: dict
    etykiety: dict
    parametry: list[Parametr]
    odpowiedzi_demo: dict[str, OdpowiedzDemo]
    scenariusze: list[Scenariusz]
    data_wejscia: DataWejscia
    obszary: list[Obszar]
    regula: dict
    katalog: Path = Path(".")
    """Katalog `konfiguracja/` (ustawiany przy wczytaniu)."""

    @classmethod
    def z_pliku(cls, plik: str | Path) -> KonfiguracjaAktualizacji:
        plik = Path(plik)
        k = cls.model_validate({**yaml.safe_load(plik.read_text(encoding="utf-8")), "katalog": plik.parent})
        if nieznane := set(k.odpowiedzi_demo) - {p.id for p in k.parametry}:
            raise ValueError(f"odpowiedzi_demo dla nieznanych parametrów: {sorted(nieznane)}")
        return k

    def parametr(self, pid: str) -> Parametr:
        for p in self.parametry:
            if p.id == pid:
                return p
        raise KeyError(pid)

    def tekst_aktu_demo(self) -> str:
        return (self.katalog / self.akt_demo).read_text(encoding="utf-8")

    def dostawcy(self, zywe: bool | None = None) -> list[LlmProvider]:
        """Łańcuch z konfiguracji. Bez klucza API (albo `zywe=False`) pomija dostawcę na żywo."""
        if zywe is None:
            zywe = bool(os.environ.get("ANTHROPIC_API_KEY"))
        dostawcy = []
        for d in self.llm["dostawcy"]:
            d = dict(d)
            if d["typ"] == "anthropic" and not zywe:
                continue
            if "plik" in d:
                d["plik"] = str(self.katalog.parent / d["plik"])
            dostawcy.append(d)
        return z_konfiguracji({**self.llm, "dostawcy": dostawcy})


# --- analiza aktu -------------------------------------------------------------------


def doc_id_aktu(tekst: str) -> str:
    """Identyfikator aktu z treści – te same fragmenty dla tego samego tekstu (odpowiedzi zapisane)."""
    return "akt-" + hashlib.sha256(tekst.strip().encode("utf-8")).hexdigest()[:10]


def fragmenty_aktu(tekst: str) -> list[Fragment]:
    doc_id = doc_id_aktu(tekst)
    wiersze = [w.strip() for w in tekst.strip().splitlines() if w.strip()]
    return [Fragment(id=f"{doc_id}#{i}", doc_id=doc_id, tekst=w) for i, w in enumerate(wiersze, 1)]


class CytatZrodla(BaseModel):
    fragment_id: str
    doc_id: str
    lokalizator: str | None = None
    cytat: str


class Propozycja(BaseModel):
    parametr: str
    rodzaj: RodzajParametru
    nazwa: str
    zmienia: bool | None
    """None: brak poprawnej odpowiedzi LLM."""
    cytaty_aktu: list[CytatZrodla] = []
    cytaty_przed: list[CytatZrodla] = []
    wartosc_przed: Decimal
    wartosc_po: Decimal | None = None
    tryb: Tryb | None = None
    dostawca: str | None = None
    etykieta: str
    niejasnosci: list[str] = []
    """Braki w odpowiedzi do sprawdzenia przez człowieka (np. cytat tylko z jednej wersji)."""


class Nieobslugiwany(BaseModel):
    fragment_id: str
    tekst: str
    flaga: str


class DataZAktu(BaseModel):
    data: date
    cytat: str
    fragment_id: str


class Analiza(BaseModel):
    doc_id: str
    wersja_bazowa: str
    propozycje: list[Propozycja]
    nieobslugiwane: list[Nieobslugiwany]
    data_wejscia: DataZAktu | None = None


def data_wejscia(akt: list[Fragment], konf: KonfiguracjaAktualizacji) -> DataZAktu | None:
    """Data wejścia w życie z przepisu aktu (deterministycznie, wzorzec z konfiguracji)."""
    for f in akt:
        m = re.search(konf.data_wejscia.wzorzec, f.tekst)
        if m and m.group(2) in konf.data_wejscia.miesiace:
            d = date(int(m.group(3)), konf.data_wejscia.miesiace.index(m.group(2)) + 1, int(m.group(1)))
            return DataZAktu(data=d, cytat=m.group(0), fragment_id=f.id)
    return None


def wartosc_przed(w: WersjaRegul, p: Parametr) -> Decimal:
    if p.rodzaj == "limit_dodatkowych":
        return Decimal(str(w.kryteria["limit_dodatkowych"]))
    prog = next(x for x in w.skala["progi"] if x["poziom"] == p.zastosowanie.poziom_granicy)
    return Decimal(str(prog["minimum"]))


def _wartosc_z_cytatow(p: Parametr, cytaty: list[str]) -> Decimal | None:
    trafienia = [m for c in cytaty for m in re.findall(p.wzorzec, c)]
    return Decimal(trafienia[-1].replace(",", ".")) if trafienia else None


def fragmenty_przed(w: WersjaRegul, korpus: Korpus, p: Parametr) -> list[Fragment]:
    """Fragmenty korpusu, w których są cytaty reguł parametru (źródło „przed”)."""
    wynik: dict[str, Fragment] = {}
    for rid in p.reguly:
        for a in w.regula(rid).adnotacje:
            if a.cytat_doslowny and a.doc_id:
                for f in korpus.fragmenty:
                    if f.doc_id == a.doc_id and cytat_w_tekscie(a.cytat_doslowny, f.tekst):
                        wynik[f.id] = f
    return list(wynik.values())


def zapytanie(p: Parametr, akt: list[Fragment], przed: list[Fragment], konf: KonfiguracjaAktualizacji) -> Zapytanie:
    return Zapytanie(
        operacja=OPERACJA, prompt_wersja=konf.wersja_promptu, pytanie=p.pytanie,
        fragmenty=[*akt, *przed], katalog=[p.id],
    )


def _przepisy_zmieniajace(akt: list[Fragment]) -> list[Fragment]:
    """Punkty aktu wprowadzające zmiany (np. „1)”, „a)”), bez wierszy-nagłówków kończących się „:”."""
    return [f for f in akt if re.match(r"^(\d+|[a-z])\)", f.tekst) and not f.tekst.endswith(":")]


def analizuj(
    akt: list[Fragment], w: WersjaRegul, korpus: Korpus, konf: KonfiguracjaAktualizacji,
    dostawcy: list[LlmProvider], dziennik: Dziennik,
) -> Analiza:
    propozycje: list[Propozycja] = []
    for p in konf.parametry:
        przed = fragmenty_przed(w, korpus, p)
        z = zapytanie(p, akt, przed, konf)
        baza = dict(parametr=p.id, rodzaj=p.rodzaj, nazwa=p.nazwa, wartosc_przed=wartosc_przed(w, p))
        try:
            wynik = wywolaj(z, dostawcy, dziennik)
        except BrakOdpowiedzi:
            propozycje.append(Propozycja(**baza, zmienia=None, etykieta=konf.etykiety["brak_odpowiedzi"]))
            continue
        po_id = {f.id: f for f in z.fragmenty}
        cytaty = [
            CytatZrodla(fragment_id=c.fragment_id, doc_id=po_id[c.fragment_id].doc_id,
                        lokalizator=po_id[c.fragment_id].lokalizator, cytat=c.cytat)
            for c in wynik.odpowiedz.cytaty
        ]
        z_aktu = [c for c in cytaty if c.doc_id == akt[0].doc_id]
        z_przed = [c for c in cytaty if c.doc_id != akt[0].doc_id]
        zmienia = p.id in wynik.odpowiedz.wybor
        po = _wartosc_z_cytatow(p, [c.cytat for c in z_aktu]) if zmienia else None
        niejasnosci = []
        if zmienia and not z_aktu:
            niejasnosci.append("Brak cytatu z aktu zmieniającego.")
        if zmienia and not z_przed:
            niejasnosci.append(f"Brak cytatu z obecnych przepisów ({w.wersja}).")
        if zmienia and po is None:
            niejasnosci.append("Nowej wartości nie odczytano z cytatu – podaj ją ręcznie.")
        propozycje.append(Propozycja(
            **baza, zmienia=zmienia, cytaty_aktu=z_aktu, cytaty_przed=z_przed, wartosc_po=po,
            tryb=wynik.tryb, dostawca=wynik.dostawca, etykieta=konf.etykiety["propozycja"], niejasnosci=niejasnosci,
        ))
    pokryte = [c.cytat for x in propozycje if x.zmienia for c in x.cytaty_aktu]
    # Gdy któryś parametr nie dostał odpowiedzi, nie wiadomo, czy punkt jest obsługiwany.
    flaga = konf.etykiety["do_sprawdzenia" if any(x.zmienia is None for x in propozycje) else "wymaga_zmiany_aplikacji"]
    nieobslugiwane = [
        Nieobslugiwany(fragment_id=f.id, tekst=f.tekst, flaga=flaga)
        for f in _przepisy_zmieniajace(akt)
        if not any(cytat_w_tekscie(c, f.tekst) or cytat_w_tekscie(f.tekst, c) for c in pokryte)
    ]
    return Analiza(doc_id=akt[0].doc_id, wersja_bazowa=w.wersja, propozycje=propozycje,
                   nieobslugiwane=nieobslugiwane, data_wejscia=data_wejscia(akt, konf))


def odpowiedzi_demo(w: WersjaRegul, korpus: Korpus, konf: KonfiguracjaAktualizacji) -> dict:
    """Zawartość pliku odpowiedzi zapisanych dla aktu demo (z `odpowiedzi_demo`, nie z modelu)."""
    akt = fragmenty_aktu(konf.tekst_aktu_demo())
    wynik = {}
    for pid, o in konf.odpowiedzi_demo.items():
        p = konf.parametr(pid)
        z = zapytanie(p, akt, fragmenty_przed(w, korpus, p), konf)

        def cytat(tekst: str, fragmenty: list[Fragment]) -> dict:
            f = next(f for f in fragmenty if cytat_w_tekscie(tekst, f.tekst))
            return {"fragment_id": f.id, "cytat": tekst}

        przed = fragmenty_przed(w, korpus, p)
        odp = Odpowiedz.model_validate({
            "cytaty": [*(cytat(c, akt) for c in o.cytaty_aktu), *(cytat(c, przed) for c in o.cytaty_przed)],
            "wybor": [pid] if o.zmienia else [],
        })
        wynik[klucz_zapytania(z)] = odp.model_dump()
    return wynik


# --- nowa wersja reguł --------------------------------------------------------------


def _liczba(x: Decimal) -> int | float:
    return int(x) if x == x.to_integral_value() else float(x)


def sprawdz_wartosci(w: WersjaRegul, konf: KonfiguracjaAktualizacji, wartosci: dict[str, Decimal]) -> list[str]:
    """Spójność nowych wartości z resztą wersji (przed zestawieniem i publikacją)."""
    bledy = []
    for pid, x in wartosci.items():
        p = konf.parametr(pid)
        if p.rodzaj == "limit_dodatkowych":
            if x != x.to_integral_value() or x < 0:
                bledy.append(f"{p.nazwa}: {x} – podaj liczbę całkowitą nieujemną.")
            continue
        progi = {x_["poziom"]: x_ for x_ in w.skala["progi"]}
        od = Decimal(str(progi[p.zastosowanie.poziom_ponizej]["minimum"]))
        do = Decimal(str(progi[p.zastosowanie.poziom_granicy]["maksimum"]))
        if not od < x < do:
            bledy.append(f"{p.nazwa}: {x} – granica musi leżeć między {od} a {do}, inaczej przedziały § 11 się nakładają.")
    return bledy


def zastosuj(
    w: WersjaRegul, analiza: Analiza, wartosci: dict[str, Decimal], *, wersja: str, obowiazuje_od: date,
    konf: KonfiguracjaAktualizacji, zatwierdzil: str = "administrator",
) -> WersjaRegul:
    """Nowa wersja reguł z przyjętymi wartościami. `w` się nie zmienia."""
    v2 = w.model_copy(deep=True)
    v2.wersja, v2.obowiazuje_od = wersja, obowiazuje_od
    v2.plik = f"opublikowana w aplikacji (akt {analiza.doc_id})"
    v2.adnotacje_wersji = RegulaV1(
        id=f"wersja.{wersja}", nazwa="Data obowiązywania wersji",
        opis=f"Wersja z aktu zmieniającego {analiza.doc_id}; data obowiązywania wskazana przy publikacji.",
        adnotacje=[SourceRef(kategoria=Kategoria.DECYZJA_PROJEKTOWA, uzasadnienie=(
            f"Data obowiązywania {obowiazuje_od.isoformat()} wskazana przy publikacji wersji {wersja}."
        ), zatwierdzil=zatwierdzil)],
    )
    propozycje = {p.parametr: p for p in analiza.propozycje}
    reguly = {r.id: r for r in v2.reguly}
    for pid, x in wartosci.items():
        prop, par = propozycje[pid], konf.parametr(pid)
        z = par.zastosowanie
        if par.rodzaj == "limit_dodatkowych":
            v2.kryteria["limit_dodatkowych"] = int(x)
            if z.nazwa_reguly:
                reguly[v2.kryteria["regula_limitu"]].nazwa = z.nazwa_reguly.format(wartosc=int(x))
        else:
            for prog in v2.skala["progi"]:
                if prog["poziom"] == z.poziom_ponizej:
                    prog["maksimum"] = _liczba(x)
                if prog["poziom"] == z.poziom_granicy:
                    prog["minimum"] = _liczba(x)
            for r in v2.skala["reguly_rozstrzygajace"]:
                if r["wynik"] == z.wynik:
                    for warunek in r["warunki"]:
                        if "srednia" in warunek:
                            warunek["wartosc"] = _liczba(x)
        for rid in par.reguly:
            r = reguly[rid]
            r.opis = f"{r.opis} Zmiana {prop.wartosc_przed} → {x} (akt {analiza.doc_id})."
            r.adnotacje = [*r.adnotacje, *(
                SourceRef(kategoria=Kategoria.PRZEPIS, doc_id=analiza.doc_id, lokalizator="akt zmieniający",
                          cytat_doslowny=c.cytat)
                for c in prop.cytaty_aktu
            )]
    return v2


# --- wpływ na scenariusze i oceny ---------------------------------------------------


class WynikKrotki(BaseModel):
    srednia: str
    poziom: str | None
    wynik: str


def _wynik(oceny: list[int], w: WersjaRegul) -> WynikKrotki:
    r = oblicz_wynik(oceny, w.konfiguracja_skali())
    return WynikKrotki(srednia=str(r.srednia), poziom=r.poziom, wynik=r.wynik)


class WplywScenariusza(BaseModel):
    id: str
    opis: str
    oceny: list[int]
    przed: WynikKrotki
    po: WynikKrotki
    zmiana: bool


class WplywOceny(BaseModel):
    cykl_id: int
    pracownik_id: str
    wersja_regul: str
    zakonczona: bool
    wynik_wlasny: WynikKrotki | None
    wynik_po: WynikKrotki | None
    liczba_dodatkowych: int
    limit_po: int
    niejasne: bool
    powody: list[str]


def wplyw_scenariuszy(konf: KonfiguracjaAktualizacji, w: WersjaRegul, v2: WersjaRegul) -> list[WplywScenariusza]:
    wynik = []
    for s in konf.scenariusze:
        przed, po = _wynik(s.oceny, w), _wynik(s.oceny, v2)
        wynik.append(WplywScenariusza(**s.model_dump(), przed=przed, po=po, zmiana=przed != po))
    return wynik


def _rozklady(k: int, wartosci: list[int]) -> dict[int, list[int]]:
    """Dla każdej osiągalnej sumy k ocen z `wartosci` – jeden przykładowy zestaw (DP)."""
    sumy: dict[int, list[int]] = {0: []}
    for _ in range(k):
        sumy = {t + v: z + [v] for t, z in sumy.items() for v in wartosci}
    return sumy


def wynik_moze_zalezec(znane: list[int], brakujace: int, w: WersjaRegul, v2: WersjaRegul) -> bool:
    """Czy wynik oceny może być inny według `w` i `v2` przy jakichkolwiek brakujących punktach.

    Wynik zależy od średniej i od tego, czy jest ocena najniższa (reguła „1 pkt ⇒ negatywna”),
    więc dla każdej osiągalnej sumy wystarczą dwa zestawy: bez oceny najniższej i z nią."""
    dozwolone = sorted(set(w.skala["dozwolone_oceny"]) & set(v2.skala["dozwolone_oceny"]))
    najnizsza, reszta = dozwolone[0], dozwolone[1:]
    warianty = list(_rozklady(brakujace, reszta).values())
    if brakujace:
        warianty += [[najnizsza, *z] for z in _rozklady(brakujace - 1, dozwolone).values()]
    else:
        warianty = [[]]
    return any(_wynik([*znane, *z], w) != _wynik([*znane, *z], v2) for z in warianty)


class WplywObszaru(BaseModel):
    id: str
    nazwa: str
    zmieniony: bool
    wplyw: str


def wplyw_obszarow(
    konf: KonfiguracjaAktualizacji, w: WersjaRegul, v2: WersjaRegul, konf_grafu=None,
) -> list[WplywObszaru]:
    def zmieniony(o: Obszar) -> bool:
        if o.sekcja == "niezalezny":
            return False
        if o.sekcja == "graf":
            return konf_grafu is not None and zbuduj_katalog(w, konf_grafu) != zbuduj_katalog(v2, konf_grafu)
        return getattr(w, o.sekcja) != getattr(v2, o.sekcja)

    return [WplywObszaru(id=o.id, nazwa=o.nazwa, zmieniony=zmieniony(o), wplyw=o.wplyw) for o in konf.obszary]


def wplyw_ocen(cykle: list[Cykl], wersje: dict[str, WersjaRegul], v2: WersjaRegul) -> list[WplywOceny]:
    """Zakończone oceny – według własnej wersji (bez zmian); oceny w toku – czy przejście jest jasne."""
    wynik = []
    for c in cykle:
        wlasna = wersje[c.wersja_regul]
        ids = [k.id for k in [*c.obowiazkowe, *c.dodatkowe]]
        komplet = all(i in c.oceny for i in ids)
        oceny = [c.oceny[i] for i in ids] if komplet else []
        powody: list[str] = []
        po = _wynik(oceny, v2) if komplet else None
        wlasny = _wynik(oceny, wlasna) if komplet else None
        if c.zatwierdzona:
            powody.append(f"Ocena zakończona – odtwarzana według {c.wersja_regul}.")
            wynik.append(WplywOceny(
                cykl_id=c.id, pracownik_id=c.pracownik_id, wersja_regul=c.wersja_regul, zakonczona=True,
                wynik_wlasny=wlasny, wynik_po=None, liczba_dodatkowych=len(c.dodatkowe),
                limit_po=v2.kryteria["limit_dodatkowych"], niejasne=False, powody=powody,
            ))
            continue
        znane = [c.oceny[i] for i in ids if i in c.oceny]
        if not komplet and wynik_moze_zalezec(znane, len(ids) - len(znane), wlasna, v2):
            powody.append(f"Brakuje {len(ids) - len(znane)} z {len(ids)} ocen, a przy niektórych wartościach wynik "
                          f"według {c.wersja_regul} i {v2.wersja} byłby inny.")
        if komplet and wlasny != po:
            powody.append(f"Wynik według {c.wersja_regul}: {wlasny.wynik} ({wlasny.poziom}); według {v2.wersja}: "
                          f"{po.wynik} ({po.poziom}).")
        if len(c.dodatkowe) > v2.kryteria["limit_dodatkowych"]:
            powody.append(f"Kryteriów dodatkowych {len(c.dodatkowe)}, a nowy limit to {v2.kryteria['limit_dodatkowych']}.")
        wynik.append(WplywOceny(
            cykl_id=c.id, pracownik_id=c.pracownik_id, wersja_regul=c.wersja_regul, zakonczona=False,
            wynik_wlasny=wlasny, wynik_po=po, liczba_dodatkowych=len(c.dodatkowe),
            limit_po=v2.kryteria["limit_dodatkowych"], niejasne=bool(powody), powody=powody,
        ))
    return wynik


# --- zapis --------------------------------------------------------------------------


class Decyzja(BaseModel):
    przyjeta: bool
    wartosc_po: Decimal | None
    osoba: str
    data: date


class Publikacja(BaseModel):
    wersja: str
    obowiazuje_od: date
    osoba: str
    data: date
    nieobslugiwane: list[str] = []
    scenariusze: list["WplywScenariusza"] = []
    uzasadnienie_daty: str | None = None


class AktZmieniajacy(BaseModel):
    id: int
    nazwa: str
    tekst: str
    doc_id: str
    wgral: str
    wgrano: date
    status: Literal["WGRANY", "PRZEANALIZOWANY", "OPUBLIKOWANY"] = "WGRANY"
    analiza: Analiza | None = None
    decyzje: dict[str, Decyzja] = Field(default_factory=dict)
    publikacja: Publikacja | None = None
    wersja_regul: WersjaRegul | None = None


class ZadanieDecyzyjne(BaseModel):
    id: int
    cykl_id: int
    pracownik_id: str
    akt_id: int
    opis: str
    powody: list[str]
    opcje: list[str]
    status: Literal["OTWARTE", "ROZSTRZYGNIETE"] = "OTWARTE"
    wybor: str | None = None
    uzasadnienie: str | None = None
    rozstrzygnal: str | None = None
    data_rozstrzygniecia: date | None = None


def utworz_tabele(baza: sqlite3.Connection) -> None:
    with baza:
        baza.execute("CREATE TABLE IF NOT EXISTS akty_przepisow (id INTEGER PRIMARY KEY, dane TEXT NOT NULL)")
        baza.execute("CREATE TABLE IF NOT EXISTS zadania_przepisow (id INTEGER PRIMARY KEY, dane TEXT NOT NULL)")


def _akty(baza: sqlite3.Connection) -> list[AktZmieniajacy]:
    return [AktZmieniajacy.model_validate_json(d) for (d,) in baza.execute("SELECT dane FROM akty_przepisow ORDER BY id")]


def _akt(baza: sqlite3.Connection, aid: int) -> AktZmieniajacy:
    w = baza.execute("SELECT dane FROM akty_przepisow WHERE id = ?", (aid,)).fetchone()
    if w is None:
        raise HTTPException(404, "Nie ma takiego aktu")
    return AktZmieniajacy.model_validate_json(w[0])


def _zapisz_akt(baza: sqlite3.Connection, a: AktZmieniajacy) -> None:
    baza.execute("INSERT OR REPLACE INTO akty_przepisow (id, dane) VALUES (?, ?)", (a.id, a.model_dump_json()))


def _zadania(baza: sqlite3.Connection) -> list[ZadanieDecyzyjne]:
    return [ZadanieDecyzyjne.model_validate_json(d) for (d,) in baza.execute("SELECT dane FROM zadania_przepisow ORDER BY id")]


def _zapisz_zadanie(baza: sqlite3.Connection, z: ZadanieDecyzyjne) -> None:
    baza.execute("INSERT OR REPLACE INTO zadania_przepisow (id, dane) VALUES (?, ?)", (z.id, z.model_dump_json()))


def uzgodnij_zadania(bz: sqlite3.Connection, wersje: list[WersjaRegul]) -> None:
    """Zadanie decyzyjne dla każdej oceny w toku, która została przy starszej wersji niż opublikowana
    w aplikacji, gdy wynik może zależeć od wersji. Jedno zadanie na parę (cykl, nowa wersja)."""
    po_nazwie = {w.wersja: w for w in wersje}
    istniejace = {(z.cykl_id, z.opcje[-1]) for z in _zadania(bz)}
    cykle = [Cykl.model_validate_json(d) for (d,) in bz.execute("SELECT dane FROM cykle ORDER BY id")]
    nr = _nastepne_id(bz, "zadania_przepisow")
    for a in _akty(bz):
        v2 = a.wersja_regul
        if v2 is None:
            continue
        kandydaci = [
            c for c in cykle
            if not c.zatwierdzona and c.wersja_regul in po_nazwie and (c.id, v2.wersja) not in istniejace
            and po_nazwie[c.wersja_regul].obowiazuje_od < v2.obowiazuje_od
        ]
        for o in wplyw_ocen(kandydaci, po_nazwie, v2):
            if o.niejasne:
                _zapisz_zadanie(bz, ZadanieDecyzyjne(
                    id=nr, cykl_id=o.cykl_id, pracownik_id=o.pracownik_id, akt_id=a.id,
                    opis=(f"Ocena w toku według {o.wersja_regul}; od {v2.obowiazuje_od.isoformat()} obowiązuje "
                          f"{v2.wersja}. Akt nie rozstrzyga, które przepisy stosować do ocen w toku – wybierz wersję."),
                    powody=o.powody, opcje=[o.wersja_regul, v2.wersja],
                ))
                istniejace.add((o.cykl_id, v2.wersja))
                nr += 1


def _nastepne_id(baza: sqlite3.Connection, tabela: str) -> int:
    return baza.execute(f"SELECT COALESCE(MAX(id), 0) + 1 FROM {tabela}").fetchone()[0]  # noqa: S608 - stała nazwa


def zarejestruj_akt(k: KorpusV1, a: AktZmieniajacy) -> None:
    """Opublikowany akt staje się dokumentem korpusu – cytaty v2 są sprawdzane jak inne."""
    if a.doc_id in k.dokumenty:
        return
    k.korpus = Korpus([*k.korpus.raport, WynikEkstrakcji(
        doc_id=a.doc_id, plik=f"{a.doc_id}.txt", status=StatusEkstrakcji.OK, fragmenty=fragmenty_aktu(a.tekst),
    )])
    k.dokumenty[a.doc_id] = Dokument(doc_id=a.doc_id, nazwa=f"{a.nazwa} (akt wgrany w aplikacji)",
                                     plik=Path(f"{a.doc_id}.txt"), kategoria=Kategoria.PRZEPIS)


def wczytaj_opublikowane(baza: sqlite3.Connection, wersje: list[WersjaRegul], korpus: KorpusV1) -> list[WersjaRegul]:
    """Przy starcie: wersje z YAML + opublikowane w aplikacji (posortowane po dacie)."""
    wynik = list(wersje)
    for a in _akty(baza):
        if a.wersja_regul is not None:
            zarejestruj_akt(korpus, a)
            wynik.append(a.wersja_regul)
    return sorted(wynik, key=lambda w: w.obowiazuje_od)


# --- API ----------------------------------------------------------------------------


class Wgranie(BaseModel):
    nazwa: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    tekst: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class DecyzjaPropozycji(BaseModel):
    przyjeta: bool
    wartosc_po: Decimal | None = None


class ZadaniePublikacji(BaseModel):
    obowiazuje_od: date
    wersja: str | None = None
    potwierdzam_nieobslugiwane: bool = False
    """Akt ma przepisy bez parametru w konfiguracji – publikacja ich nie obejmie."""
    uzasadnienie_daty: str | None = None
    """Wymagane, gdy data obowiązywania różni się od daty wejścia w życie z aktu."""


class RozstrzygniecieZbiorcze(BaseModel):
    ids: list[int] = Field(min_length=1)
    wersja: str
    uzasadnienie: str


class Rozstrzygniecie(BaseModel):
    wersja: str
    uzasadnienie: str


class PrzedPo(BaseModel):
    parametr: str
    nazwa: str
    przed: Decimal
    po: Decimal
    cytaty_przed: list[CytatZrodla]
    cytaty_aktu: list[CytatZrodla]
    tryb: Tryb | None


class Zestawienie(BaseModel):
    wersja_bazowa: str
    zmiany: list[PrzedPo]
    nieobslugiwane: list[Nieobslugiwany]
    scenariusze: list[WplywScenariusza]
    obszary: list[WplywObszaru]
    oceny: list[WplywOceny]
    data_wejscia: DataZAktu | None


@contextmanager
def _transakcja(request: Request) -> Iterator[sqlite3.Connection]:
    """Własne połączenie z blokadą zapisu; commit przy wyjściu. Historię (#10) zapisujemy dopiero
    po wyjściu z bloku – dziennik ma osobne połączenie do tej samej bazy."""
    with closing(sqlite3.connect(request.app.state.ustawienia.baza, check_same_thread=False)) as bz, bz:
        bz.execute("BEGIN IMMEDIATE")
        yield bz


def router(konf: KonfiguracjaAktualizacji, auth, dziennik_llm: Dziennik) -> APIRouter:
    r = APIRouter(prefix="/api/przepisy", tags=["aktualizacja przepisów"])

    def baza(request: Request) -> sqlite3.Connection:
        return request.app.state.baza

    def wersje(request: Request) -> dict[str, WersjaRegul]:
        return {v.wersja: v for v in request.app.state.wersje}

    def wartosci(a: AktZmieniajacy) -> dict[str, Decimal | None]:
        assert a.analiza is not None
        prop = {p.parametr: p for p in a.analiza.propozycje}
        return {
            pid: d.wartosc_po if d.wartosc_po is not None else prop[pid].wartosc_po
            for pid, d in a.decyzje.items() if d.przyjeta
        }

    def kandydat(request: Request, a: AktZmieniajacy, wersja: str, od: date, osoba: str) -> WersjaRegul:
        """Nowa wersja z przyjętych zmian po sprawdzeniu spójności i źródeł; inaczej 409/422."""
        if a.analiza is None:
            raise HTTPException(409, "Najpierw uruchom analizę aktu.")
        x = wartosci(a)
        if not x:
            raise HTTPException(409, "Nie przyjęto żadnej zmiany.")
        if brak := [pid for pid, v in x.items() if v is None]:
            raise HTTPException(422, f"Podaj nową wartość dla: {', '.join(brak)}.")
        prop = {p.parametr: p for p in a.analiza.propozycje}
        if bez := [pid for pid in x if not prop[pid].cytaty_aktu]:
            raise HTTPException(422, f"Brak cytatu z aktu dla: {', '.join(bez)} – zmiana nie miałaby źródła.")
        w = wersje(request)[a.analiza.wersja_bazowa]
        if bledy := sprawdz_wartosci(w, konf, x):
            raise HTTPException(422, {"komunikat": "Niespójne wartości.", "bledy": bledy})
        v2 = zastosuj(w, a.analiza, x, wersja=wersja, obowiazuje_od=od, konf=konf, zatwierdzil=osoba)
        korpus = Korpus([*request.app.state.korpus.korpus.raport, WynikEkstrakcji(
            doc_id=a.doc_id, plik=f"{a.doc_id}.txt", status=StatusEkstrakcji.OK, fragmenty=fragmenty_aktu(a.tekst),
        )])
        bledy_zrodel = [b.komunikat for rg in v2.reguly for ref in rg.adnotacje for b in waliduj_adnotacje(ref, korpus, rg.id)]
        if bledy_zrodel:
            raise HTTPException(422, {"komunikat": "Cytaty nowej wersji bez pokrycia.", "bledy": bledy_zrodel})
        return v2

    def wszystkie_cykle(b: sqlite3.Connection) -> list[Cykl]:
        return [Cykl.model_validate_json(d) for (d,) in b.execute("SELECT dane FROM cykle ORDER BY id")]

    @r.get("/akt-demo")
    def akt_demo(uz=auth.wymagaj(AKCJA)) -> dict:
        """Fikcyjny akt zmieniający do demo (dane demonstracyjne)."""
        return {"nazwa": "Akt zmieniający – demo (fikcyjny)", "tekst": konf.tekst_aktu_demo(), "demo": True}

    @r.get("/akty")
    def lista(request: Request, uz=auth.wymagaj(AKCJA)) -> list[AktZmieniajacy]:
        return _akty(baza(request))

    @r.post("/akty", status_code=201)
    def wgraj(body: Wgranie, request: Request, uz=auth.wymagaj(AKCJA)) -> AktZmieniajacy:
        """Wgranie aktu. Aktywne reguły się nie zmieniają."""
        b = baza(request)
        with b:
            a = AktZmieniajacy(id=_nastepne_id(b, "akty_przepisow"), nazwa=body.nazwa, tekst=body.tekst,
                               doc_id=doc_id_aktu(body.tekst), wgral=uz.login, wgrano=date.today())
            _zapisz_akt(b, a)
        return a

    @r.get("/akty/{aid}")
    def odczyt(aid: int, request: Request, uz=auth.wymagaj(AKCJA)) -> AktZmieniajacy:
        return _akt(baza(request), aid)

    @r.post("/akty/{aid}/analiza")
    def analiza(aid: int, request: Request, uz=auth.wymagaj(AKCJA)) -> AktZmieniajacy:
        """LLM wskazuje zmieniane parametry z cytatami z aktu i obecnych przepisów (propozycja).
        Podstawą porównania jest wersja obowiązująca dziś."""
        b = baza(request)
        a = _akt(b, aid)
        if a.status == "OPUBLIKOWANY":
            raise HTTPException(409, "Akt jest już opublikowany.")
        a.analiza = analizuj(fragmenty_aktu(a.tekst), wersja_na_dzien(request.app.state.wersje, date.today()),
                             request.app.state.korpus.korpus, konf, konf.dostawcy(), dziennik_llm)
        a.status, a.decyzje = "PRZEANALIZOWANY", {}
        with b:
            _zapisz_akt(b, a)
        return a

    @r.put("/akty/{aid}/propozycje/{parametr}")
    def decyzja(aid: int, parametr: str, body: DecyzjaPropozycji, request: Request,
                uz=auth.wymagaj(AKCJA)) -> AktZmieniajacy:
        """Administrator przyjmuje albo odrzuca propozycję; może poprawić nową wartość."""
        b = baza(request)
        a = _akt(b, aid)
        if a.analiza is None or a.status == "OPUBLIKOWANY":
            raise HTTPException(409, "Decyzje są możliwe po analizie i przed publikacją.")
        if parametr not in {p.parametr for p in a.analiza.propozycje}:
            raise HTTPException(404, "Nie ma takiej propozycji")
        a.decyzje[parametr] = Decyzja(przyjeta=body.przyjeta, wartosc_po=body.wartosc_po, osoba=uz.login, data=date.today())
        with b:
            _zapisz_akt(b, a)
        return a

    @r.get("/akty/{aid}/zestawienie")
    def zestawienie(aid: int, request: Request, uz=auth.wymagaj(AKCJA)) -> Zestawienie:
        """Przed/po przyjętych zmian, scenariusze kontrolne i wpływ na oceny – bez publikacji."""
        b = baza(request)
        a = _akt(b, aid)
        v2 = a.wersja_regul or kandydat(request, a, "projekt", date.today(), uz.login)
        ws = wersje(request)
        w = ws[a.analiza.wersja_bazowa]
        prop = {p.parametr: p for p in a.analiza.propozycje}
        return Zestawienie(
            wersja_bazowa=w.wersja,
            zmiany=[PrzedPo(parametr=pid, nazwa=prop[pid].nazwa, przed=prop[pid].wartosc_przed, po=v,
                            cytaty_przed=prop[pid].cytaty_przed, cytaty_aktu=prop[pid].cytaty_aktu,
                            tryb=prop[pid].tryb) for pid, v in wartosci(a).items()],
            nieobslugiwane=a.analiza.nieobslugiwane,
            scenariusze=wplyw_scenariuszy(konf, w, v2),
            obszary=wplyw_obszarow(konf, w, v2, getattr(request.app.state, "konfiguracja_grafu", None)),
            oceny=wplyw_ocen(wszystkie_cykle(b), {**ws, v2.wersja: v2}, v2),
            data_wejscia=a.analiza.data_wejscia,
        )

    @r.post("/akty/{aid}/publikacja")
    def publikacja(aid: int, body: ZadaniePublikacji, request: Request, uz=auth.wymagaj(AKCJA)) -> AktZmieniajacy:
        """Publikacja nowej wersji z datą obowiązywania i zadania decyzyjne dla niejasnych przejść.
        Odczyt ocen, zapis wersji i zadań w jednej transakcji."""
        with _transakcja(request) as bz:
            a, v2 = _publikuj(aid, body, request, bz, uz)
        request.app.state.dziennik.zapisz(
            uz.login, "przepisy", v2.wersja, None,
            {"wersja": v2.wersja, "obowiazuje_od": body.obowiazuje_od.isoformat(), "akt": a.doc_id,
             "zmiany": {k: str(v) for k, v in wartosci(a).items()}},
            v2.wersja,
        )
        zarejestruj_akt(request.app.state.korpus, a)
        request.app.state.wersje = sorted([*request.app.state.wersje, v2], key=lambda v: v.obowiazuje_od)
        # Graf kompetencji (#20) ma katalog dla każdej wersji reguł – także opublikowanej teraz.
        request.app.state.katalogi_grafu[v2.wersja] = zbuduj_katalog(v2, request.app.state.konfiguracja_grafu)
        return a

    def _publikuj(aid: int, body: ZadaniePublikacji, request: Request, bz: sqlite3.Connection, uz):
        a = _akt(bz, aid)
        if a.status == "OPUBLIKOWANY":
            raise HTTPException(409, "Akt jest już opublikowany.")
        if a.analiza is None:
            raise HTTPException(409, "Najpierw uruchom analizę aktu.")
        if a.analiza.nieobslugiwane and not body.potwierdzam_nieobslugiwane:
            raise HTTPException(409, {
                "komunikat": "Akt zawiera przepisy bez parametru w konfiguracji – publikacja ich nie obejmie. "
                             "Potwierdź, że wymagają zmiany aplikacji.",
                "bledy": [n.tekst for n in a.analiza.nieobslugiwane],
            })
        ws = wersje(request)
        nazwa = body.wersja or f"{a.analiza.wersja_bazowa}-akt{a.id}"
        if nazwa in ws:
            raise HTTPException(409, f"Wersja {nazwa} już istnieje.")
        if body.obowiazuje_od <= ws[a.analiza.wersja_bazowa].obowiazuje_od:
            raise HTTPException(422, "Data obowiązywania musi być późniejsza niż wersji bazowej.")
        dw = a.analiza.data_wejscia
        uzasadnienie_daty = (body.uzasadnienie_daty or "").strip() or None
        if dw and body.obowiazuje_od != dw.data and not uzasadnienie_daty:
            raise HTTPException(422, f"Akt wchodzi w życie {dw.data.isoformat()} („{dw.cytat}”). "
                                     "Inna data obowiązywania wymaga uzasadnienia.")
        v2 = kandydat(request, a, nazwa, body.obowiazuje_od, uz.login)
        a.status, a.wersja_regul = "OPUBLIKOWANY", v2
        a.publikacja = Publikacja(
            wersja=nazwa, obowiazuje_od=body.obowiazuje_od, osoba=uz.login, data=date.today(),
            nieobslugiwane=[n.tekst for n in a.analiza.nieobslugiwane],
            scenariusze=wplyw_scenariuszy(konf, ws[a.analiza.wersja_bazowa], v2),
            uzasadnienie_daty=uzasadnienie_daty,
        )
        _zapisz_akt(bz, a)
        uzgodnij_zadania(bz, [*ws.values(), v2])
        return a, v2

    @r.get("/zadania")
    def zadania(request: Request, uz=auth.wymagaj(AKCJA)) -> list[ZadanieDecyzyjne]:
        """Zadania decyzyjne; przed odczytem uzgadniane z ocenami w toku (także rozpoczętymi po publikacji)."""
        with _transakcja(request) as bz:
            uzgodnij_zadania(bz, request.app.state.wersje)
            return _zadania(bz)

    @r.post("/zadania/zbiorczo")
    def zbiorczo(body: RozstrzygniecieZbiorcze, request: Request, uz=auth.wymagaj(AKCJA)) -> list[ZadanieDecyzyjne]:
        """Ta sama decyzja dla wielu zadań (np. wszystkie oceny rozpoczęte przed zmianą) – jedna transakcja."""
        jedno = Rozstrzygniecie(wersja=body.wersja, uzasadnienie=body.uzasadnienie)
        with _transakcja(request) as bz:
            wyniki = [_rozstrzygnij(zid, jedno, bz, uz) for zid in body.ids]
        for zad, przed in wyniki:
            request.app.state.dziennik.zapisz(
                uz.login, "cykl", str(zad.cykl_id),
                {"wersja_regul": przed}, {"wersja_regul": body.wersja, "uzasadnienie_wersji": zad.uzasadnienie},
                body.wersja,
            )
        return [zad for zad, _ in wyniki]

    @r.post("/zadania/{zid}")
    def rozstrzygnij(zid: int, body: Rozstrzygniecie, request: Request, uz=auth.wymagaj(AKCJA)) -> ZadanieDecyzyjne:
        """Człowiek wybiera wersję reguł dla oceny w toku; wybór wymaga uzasadnienia i trafia do historii."""
        with _transakcja(request) as bz:
            zad, przed = _rozstrzygnij(zid, body, bz, uz)
        request.app.state.dziennik.zapisz(
            uz.login, "cykl", str(zad.cykl_id),
            {"wersja_regul": przed}, {"wersja_regul": body.wersja, "uzasadnienie_wersji": zad.uzasadnienie},
            body.wersja,
        )
        return zad

    def _rozstrzygnij(zid: int, body: Rozstrzygniecie, bz: sqlite3.Connection, uz):
        zad = next((x for x in _zadania(bz) if x.id == zid), None)
        if zad is None:
            raise HTTPException(404, "Nie ma takiego zadania")
        if zad.status != "OTWARTE":
            raise HTTPException(409, "Zadanie jest już rozstrzygnięte.")
        if body.wersja not in zad.opcje:
            raise HTTPException(422, f"Wybierz jedną z wersji: {', '.join(zad.opcje)}.")
        if not body.uzasadnienie.strip():
            raise HTTPException(422, "Wybór wersji reguł wymaga uzasadnienia.")
        c = pobierz_cykl(bz, zad.cykl_id)
        if c.zatwierdzona:
            raise HTTPException(409, "Ocena jest już zatwierdzona – zostaje przy swojej wersji.")
        przed = c.wersja_regul
        if przed != body.wersja:
            c.wersja_regul = body.wersja
            _zapisz(bz, c)
        zad = zad.model_copy(update=dict(status="ROZSTRZYGNIETE", wybor=body.wersja, uzasadnienie=body.uzasadnienie.strip(),
                                         rozstrzygnal=uz.login, data_rozstrzygniecia=date.today()))
        _zapisz_zadanie(bz, zad)
        return zad, przed

    return r


if __name__ == "__main__":  # uv run python -m caoo.aktualizacja – odpowiedzi zapisane dla aktu demo
    import json

    from .korpus_aplikacji import zaladuj
    from .reguly import wczytaj_wersje
    from .ustawienia import KATALOG_APLIKACJI, Ustawienia

    ust = Ustawienia.ze_srodowiska()
    k = KonfiguracjaAktualizacji.z_pliku(ust.aktualizacja)
    plik = KATALOG_APLIKACJI / "konfiguracja" / "aktualizacja" / "zapisane.json"
    dane = odpowiedzi_demo(wczytaj_wersje(ust.reguly)[-1], zaladuj(ust.manifest).korpus, k)
    plik.write_text(json.dumps(dane, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"zapisano {plik}")
