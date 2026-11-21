"""Ocena kryteriów i jawne obliczenie wyniku według § 11 (#13).

Oceniający przyznaje 1/3/5/7/9 pkt każdemu kryterium cyklu. Wynik liczy silnik skali
z caoo-core na konfiguracji `skala` z wersji reguł cyklu – po każdej zmianie od nowa.
Dla porównania liczona jest też średnia formułą zautomatyzowanego Excela (dzielnik 5).
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from caoo_core.pdf import hash_tresci
from caoo_core.skala import oblicz_wynik
from caoo_core.zrodla import SourceRef, normalizuj
from pydantic import BaseModel, Field

from .arkusz_pdf import DaneOsoby, PoprzedniWynik, PozycjaArkusza, PrzedzialSkali, TrescArkusza
from .baza import ZapisCyklu
from .cykl import Cykl, _wczytaj, _zapisz
from .graf import zbuduj_graf
from .graf_model import GrafOceny
from .kadry import MockKadry, Profil
from .metodyka import Metodyka, MetodykaKryterium, Odpowiedz, dla_kryterium, propozycja
from .obieg_oceny import PowodWersji, StanWersji, StatusWersji, WersjaOceny, stan_wersji, termin
from .reguly import WersjaRegul
from .terminy import termin_oceny

DZIELNIK_EXCELA = 5


class KryteriumOceny(BaseModel):
    id: str
    nazwa: str
    rodzaj: Literal["obowiązkowe", "dodatkowe"]
    opis: str | None = Field(description="Opis kryterium z zał. 1 rozporządzenia.")
    adnotacje: list[SourceRef]
    oczekiwania: str | None
    punkty: int | None
    metodyka: MetodykaKryterium | None


class RegulaWyniku(BaseModel):
    id: str
    nazwa: str | None
    adnotacje: list[SourceRef]


class KrokWyniku(BaseModel):
    regula: str
    opis: str


class Wynik(BaseModel):
    kompletny: bool
    """False: nie wszystkie kryteria mają punkty – wynik dotyczy ocenionych."""
    srednia: str
    poziom: str | None
    wynik: str
    regula_rozstrzygajaca: RegulaWyniku
    slad: list[KrokWyniku]


class PorownanieExcela(BaseModel):
    srednia_par_11: str
    srednia_excel: str
    dzielnik_excel: int
    liczba_ocen: int
    rozni_sie: bool
    rozbieznosc: str = "excel.dzielnik-sredniej"


class StanOceny(BaseModel):
    cykl_id: int
    wersja_regul: str
    wersja_metodyki: str | None
    kryteria: list[KryteriumOceny]
    wynik: Wynik | None
    uzasadnienie: str | None
    bledy: list[str]
    """Co blokuje zatwierdzenie (brak punktów, brak uzasadnienia oceny negatywnej)."""
    porownanie_excela: PorownanieExcela | None
    zatwierdzona: bool
    data_zatwierdzenia: date | None
    data_sporzadzenia: date | None
    data_rozmowy_oceniajacej: date | None
    wnioski_rozwojowe: str
    numer_wersji: int
    powod_wersji: PowodWersji
    status: StatusWersji | Literal["ROBOCZA"]
    historia: list[StanWersji]
    graf: GrafOceny | None = None


class ZapisOceny(BaseModel):
    punkty: dict[str, int | None] = Field(default_factory=dict, description="Id kryterium → 1/3/5/7/9; null usuwa.")
    oczekiwania: dict[str, str | None] = Field(default_factory=dict)
    uzasadnienie: str | None = None
    wnioski_rozwojowe: str | None = None
    data_sporzadzenia: date | None = None
    data_rozmowy_oceniajacej: date | None = None
    numer_wersji: int | None = None
    zatwierdz: bool = False
    odpowiedzi: dict[str, dict[int, Odpowiedz | None]] = Field(default_factory=dict)
    uzasadnienia_odstepstw: dict[str, str] = Field(default_factory=dict)


_NAGLOWEK = re.compile(r"Dziennik Ustaw – \d+ – Poz\. \d+")


def opisy_zalacznika(tekst_rozporzadzenia: str, w: WersjaRegul) -> dict[str, str]:
    """Opisy kryteriów z zał. 1 (tekst między „N. NAZWA −” a następną pozycją)."""
    t = normalizuj(tekst_rozporzadzenia)
    start = t.find("KRYTERIUM OPIS")
    wykaz = w.kryteria["wykaz"]
    pozycje = []
    for nr, k in enumerate(wykaz, start=1):
        znacznik = f"{nr}. {k['nazwa']} −"
        i = t.find(znacznik, start)
        if i < 0:
            return {}
        pozycje.append((k["id"], i, i + len(znacznik)))
        start = i + len(znacznik)
    koniec_zal = t.find("Załącznik nr 2", start)
    opisy = {}
    for (kid, _, od), nast in zip(pozycje, [*pozycje[1:], (None, koniec_zal if koniec_zal > 0 else len(t), 0)]):
        surowy = t[od : nast[1]]
        opisy[kid] = "− " + _NAGLOWEK.sub("", surowy).replace("  ", " ").strip()
    return opisy


def _fmt(x: Decimal) -> str:
    return str(x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def stan_oceny(c: Cykl, w: WersjaRegul, opisy: dict[str, str], metodyka: Metodyka | None = None) -> StanOceny:
    kryteria = [
        KryteriumOceny(
            id=k.id, nazwa=k.nazwa, rodzaj=rodzaj, opis=opisy.get(k.id), adnotacje=k.adnotacje,
            oczekiwania=c.oczekiwania.get(k.id), punkty=c.oceny.get(k.id),
            metodyka=(
                dla_kryterium(metodyka, k.id, c.odpowiedzi.get(k.id, {}), c.uzasadnienia_odstepstw.get(k.id))
                if metodyka is not None else None
            ),
        )
        for rodzaj, lista in (("obowiązkowe", c.obowiazkowe), ("dodatkowe", c.dodatkowe))
        for k in lista
    ]
    punkty = [k.punkty for k in kryteria if k.punkty is not None]
    brakujace = [k.nazwa for k in kryteria if k.punkty is None]
    wynik = porownanie = None
    bledy = []
    if brakujace:
        bledy.append("Brak punktów: " + ", ".join(brakujace) + ".")
    for k in kryteria:
        m = k.metodyka
        if m and m.propozycja is not None and k.punkty not in (None, m.propozycja) and not m.uzasadnienie_odstepstwa:
            bledy.append(f"{k.nazwa}: przyjmij propozycję albo uzasadnij wybraną punktację.")
    if punkty:
        r = oblicz_wynik(punkty, w.konfiguracja_skali())
        reg = w.regula(r.regula_rozstrzygajaca.id)
        wynik = Wynik(
            kompletny=not brakujace,
            srednia=_fmt(r.srednia),
            poziom=r.poziom,
            wynik=r.wynik,
            regula_rozstrzygajaca=RegulaWyniku(id=reg.id, nazwa=reg.nazwa, adnotacje=reg.adnotacje),
            slad=[KrokWyniku(regula=k.regula.id, opis=k.opis) for k in r.slad],
        )
        if not brakujace:
            excel = Decimal(sum(punkty)) / DZIELNIK_EXCELA
            porownanie = PorownanieExcela(
                srednia_par_11=_fmt(r.srednia), srednia_excel=_fmt(excel), dzielnik_excel=DZIELNIK_EXCELA,
                liczba_ocen=len(punkty), rozni_sie=excel != r.srednia,
            )
        if r.wynik == "NEGATYWNA" and not (c.uzasadnienie and c.uzasadnienie.strip()):
            bledy.append("Uzasadnienie jest wymagane przy ocenie negatywnej (§ 11 ust. 3).")
    historia = [stan_wersji(v, w) for v in reversed(c.wersje_oceny)]
    status = (historia[0].status if historia else "ZATWIERDZONA") if c.zatwierdzona else "ROBOCZA"
    return StanOceny(
        cykl_id=c.id, wersja_regul=c.wersja_regul, kryteria=kryteria, wynik=wynik,
        wersja_metodyki=metodyka.wersja if metodyka is not None else c.wersja_metodyki,
        uzasadnienie=c.uzasadnienie, bledy=bledy, porownanie_excela=porownanie,
        zatwierdzona=c.zatwierdzona, data_zatwierdzenia=c.data_zatwierdzenia,
        data_sporzadzenia=c.data_sporzadzenia or c.data_zatwierdzenia,
        data_rozmowy_oceniajacej=c.data_rozmowy_oceniajacej, wnioski_rozwojowe=c.wnioski_rozwojowe,
        numer_wersji=c.numer_wersji_oceny, powod_wersji=c.powod_wersji_oceny, status=status, historia=historia,
    )


def _blad_zapisu(bledy: list[str]) -> HTTPException:
    return HTTPException(422, {"komunikat": "Niepoprawne dane oceny.", "bledy": bledy})


def _zastosuj_zapis(c: Cykl, w: WersjaRegul, metodyka: Metodyka, body: ZapisOceny) -> Cykl:
    kryteria = {k.id: k.nazwa for k in [*c.obowiazkowe, *c.dodatkowe]}
    zmieniane = body.punkty.keys() | body.odpowiedzi.keys() | body.uzasadnienia_odstepstw.keys()
    dozwolone = set(w.skala["dozwolone_oceny"])
    bledy = [f"Kryterium {k} nie należy do tego cyklu." for k in sorted((zmieniane | body.oczekiwania.keys()) - kryteria.keys())]
    bledy += [f"{k}: {v} pkt – dozwolone {sorted(dozwolone)}." for k, v in body.punkty.items()
              if v is not None and v not in dozwolone]
    bledy += [f"Kryterium {k} nie ma pytania o numerze {p}." for k, odpowiedzi in body.odpowiedzi.items()
              for p in odpowiedzi if not 1 <= p <= len(metodyka.pytania.get(k, []))]
    if bledy:
        raise _blad_zapisu(bledy)

    oceny = dict(c.oceny)
    for k, punkty in body.punkty.items():
        if punkty is None:
            oceny.pop(k, None)
        else:
            oceny[k] = punkty

    odpowiedzi = dict(c.odpowiedzi)
    for k, zmiany in body.odpowiedzi.items():
        pytania = {**odpowiedzi.get(k, {}), **zmiany}
        zapisane = {i: o for i, o in pytania.items() if o is not None}
        if zapisane:
            odpowiedzi[k] = zapisane
        else:
            odpowiedzi.pop(k, None)

    odstepstwa = dict(c.uzasadnienia_odstepstw)
    for k in sorted(zmieniane):
        proponowane, _ = propozycja(odpowiedzi.get(k, {}))
        poprzednie, _ = propozycja(c.odpowiedzi.get(k, {}))
        punkty = oceny.get(k)
        if (proponowane, punkty) != (poprzednie, c.oceny.get(k)):
            odstepstwa.pop(k, None)
        if proponowane is None or punkty in (None, proponowane):
            continue
        uzasadnienie = body.uzasadnienia_odstepstw.get(k, "").strip()
        if uzasadnienie:
            odstepstwa[k] = uzasadnienie
        elif (k in body.punkty or k in body.uzasadnienia_odstepstw) and not odstepstwa.get(k):
            bledy.append(f"{kryteria[k]}: wybór punktów innych niż propozycja wymaga uzasadnienia.")
    if bledy:
        raise _blad_zapisu(bledy)

    pola_arkusza = {"data_sporzadzenia", "data_rozmowy_oceniajacej", "wnioski_rozwojowe"}
    dane = body.model_dump(exclude_unset=True, include=pola_arkusza)
    if "wnioski_rozwojowe" in dane:
        dane["wnioski_rozwojowe"] = dane["wnioski_rozwojowe"] or ""
    oczekiwania = c.oczekiwania | body.oczekiwania

    return c.model_copy(update=dane | {
        "oceny": oceny,
        "oczekiwania": {k: v for k, v in oczekiwania.items() if v},
        "uzasadnienie": body.uzasadnienie if body.uzasadnienie is not None else c.uzasadnienie,
        "wersja_metodyki": metodyka.wersja,
        "odpowiedzi": odpowiedzi,
        "uzasadnienia_odstepstw": odstepstwa,
    })


def pobierz_cykl(baza: sqlite3.Connection, cykl_id: int) -> Cykl:
    c = _wczytaj(baza, cykl_id)
    if c is None:
        raise HTTPException(404, "Nie ma takiego cyklu")
    return c


def reguly_cyklu(c: Cykl, wersje: list[WersjaRegul]) -> WersjaRegul:
    w = next((x for x in wersje if x.wersja == c.wersja_regul), None)
    if w is None:
        raise HTTPException(409, f"Brak wersji reguł {c.wersja_regul}, z którą rozpoczęto cykl.")
    return w


def _kontekst(
    request: Request, c: Cykl,
) -> tuple[WersjaRegul, dict[str, str], Metodyka]:
    w = reguly_cyklu(c, request.app.state.wersje)
    wersja_metodyki = c.wersja_metodyki or request.app.state.ustawienia.domyslna_metodyka
    metodyka = request.app.state.metodyki.get(wersja_metodyki)
    if metodyka is None:
        raise HTTPException(409, f"Brak wersji metodyki {wersja_metodyki} przypisanej do cyklu.")
    tekst = request.app.state.korpus.korpus.tekst_dokumentu("rozp-oceny-okresowe")
    return w, opisy_zalacznika(tekst, w), metodyka


def _dane_osoby(p: Profil | None) -> DaneOsoby:
    if p is None:
        return DaneOsoby()
    return DaneOsoby(imie=p.imie, nazwisko=p.nazwisko, komorka=p.komorka, stanowisko=p.stanowisko)


def zamroz_arkusz(c: Cykl, stan: StanOceny, w: WersjaRegul, kadry: MockKadry) -> WersjaOceny:
    """Utrwal wszystkie dane do wydruku, także po zmianie profilu lub konfiguracji."""
    assert stan.wynik is not None and stan.wynik.kompletny and c.data_zatwierdzenia is not None
    profil = kadry.profil(c.pracownik_id)
    przelozony = kadry.profil(profil.przelozony_id) if profil and profil.przelozony_id else None
    sporzadzono = c.data_sporzadzenia or c.data_zatwierdzenia
    zmiana_oceniajacego = next((z for z in reversed(c.zmiany_oceniajacego) if z.data <= sporzadzono), None)
    oceniajacy = DaneOsoby(pelna_nazwa=zmiana_oceniajacego.aktualny) if zmiana_oceniajacego else _dane_osoby(przelozony)
    oceniany = _dane_osoby(profil)
    aktualne_stanowisko = next((
        z for z in reversed(c.zmiany_stanowiska) if z.data <= sporzadzono and z.tryb != "OCENA_PRZY_ZMIANIE"
    ), None)
    if aktualne_stanowisko:
        oceniany.stanowisko = aktualne_stanowisko.stanowisko
    ponowne_kryteria = c.ostatnie_ponowienie_kryteriow()
    ustalono = (ponowne_kryteria.ustalono_kryteria if ponowne_kryteria else None) or c.rozpoczeto.date()
    ostatnia = max(profil.poprzednie_oceny, key=lambda o: o.data_sporzadzenia, default=None) if profil else None
    z_wykazu = {
        k.id: PozycjaArkusza(nazwa=k.nazwa, punkty=c.oceny[k.id], oczekiwania=k.oczekiwania)
        for k in stan.kryteria
    }
    termin_sporzadzenia = c.termin_oceny
    if termin_sporzadzenia is None and c.wersje_oceny:
        termin_sporzadzenia = c.wersje_oceny[0].tresc.termin_sporzadzenia
    if termin_sporzadzenia is None and profil:
        if ostatnia and ostatnia.wynik == "negatywna" and "ponowna_ocena" in w.terminy:
            termin_sporzadzenia = termin(ostatnia.data_zapoznania, w, "ponowna_ocena").data
        else:
            termin_profilu = termin_oceny(profil, w)
            termin_sporzadzenia = termin_profilu.termin if termin_profilu else None
    tresc = TrescArkusza(
        cykl_id=c.id, pracownik_id=c.pracownik_id, numer=c.numer_wersji_oceny, arkusz=c.arkusz,
        wersja_regul=c.wersja_regul, jednostka=profil.jednostka if profil else kadry.jednostka,
        oceniany=oceniany, oceniajacy=oceniajacy,
        urzednik=bool(profil and profil.korpus == "urzednik"),
        poprzedni_wynik=PoprzedniWynik.model_validate(ostatnia.model_dump()) if ostatnia else None,
        obowiazkowe=[z_wykazu[k.id] for k in c.obowiazkowe],
        dodatkowe=[z_wykazu[k.id] for k in c.dodatkowe], skala=list(w.skala["dozwolone_oceny"]),
        przedzialy=[
            PrzedzialSkali(
                poziom=p["poziom"], minimum=p["minimum"], maksimum=p["maksimum"],
                domkniety_od_dolu=p.get("domkniety_od_dolu", True),
            )
            for p in w.skala["progi"]
        ],
        termin_sporzadzenia=termin_sporzadzenia, data_ustalenia_kryteriow=ustalono,
        data_potwierdzenia_kryteriow=c.data_potwierdzenia,
        data_rozmowy_oceniajacej=c.data_rozmowy_oceniajacej,
        data_sporzadzenia=sporzadzono,
        srednia=stan.wynik.srednia, poziom=stan.wynik.poziom, wynik=stan.wynik.wynik,
        uzasadnienie=c.uzasadnienie, wnioski_rozwojowe=c.wnioski_rozwojowe,
        dni_na_odwolanie=(
            None if c.powod_wersji_oceny == "ZMIANA_PO_ODWOLANIU"
            else w.terminy.get("odwolanie", {}).get("liczba", 7)
        ),
    )
    return WersjaOceny(
        tresc=tresc, powod=c.powod_wersji_oceny, hash=hash_tresci(tresc.model_dump(mode="json")),
        data_zatwierdzenia=c.data_zatwierdzenia,
        wersja_metodyki=c.wersja_metodyki, odpowiedzi=c.odpowiedzi,
        uzasadnienia_odstepstw=c.uzasadnienia_odstepstw,
    )


def uzupelnij_zatwierdzone(baza: sqlite3.Connection, wersje: list[WersjaRegul], kadry: MockKadry) -> None:
    """Uzupełnij migawkę dla ocen zatwierdzonych przed dodaniem obiegu."""
    rekordy = baza.execute(
        "SELECT dane FROM cykle WHERE json_extract(dane, '$.zatwierdzona') = 1 "
        "AND COALESCE(json_array_length(dane, '$.wersje_oceny'), 0) = 0",
    ).fetchall()
    for (dane,) in rekordy:
        c = Cykl.model_validate_json(dane)
        w = next((x for x in wersje if x.wersja == c.wersja_regul), None)
        if w is not None and c.data_zatwierdzenia is not None:
            stan = stan_oceny(c, w, {})
            if stan.wynik and stan.wynik.kompletny:
                c.wersje_oceny = [zamroz_arkusz(c, stan, w, kadry)]
                _zapisz(baza, c)


class WpisHistorii(BaseModel):
    czas: str
    autor: str
    wersja_regul: str
    roznica: dict


def _migawka(c: Cykl) -> dict:
    """Dane oceny śledzone w historii (#10)."""
    return c.model_dump(mode="json", include={
        "oceny", "oczekiwania", "uzasadnienie", "zatwierdzona", "data_zatwierdzenia", "data_sporzadzenia",
        "data_rozmowy_oceniajacej", "wnioski_rozwojowe", "numer_wersji_oceny", "wersja_metodyki",
        "odpowiedzi", "uzasadnienia_odstepstw",
    })


def router(auth) -> APIRouter:
    from .dostep import pracownik as _pracownik

    r = APIRouter(prefix="/api/cykle", tags=["ocena kryteriów i wynik (§ 11)"])

    @r.get("/{cykl_id}/ocena")
    def odczyt(
        cykl_id: int, request: Request, z_grafem: bool = False, uz=auth.wymagaj("ocena.czytaj")
    ) -> StanOceny:
        """Kryteria z opisem z zał. 1 i oczekiwaniami, punkty, wynik § 11 ze śladem i regułą rozstrzygającą."""
        c = pobierz_cykl(request.app.state.baza, cykl_id)
        profil = _pracownik(request, uz, c.pracownik_id, "ocena.czytaj")
        w, opisy, metodyka = _kontekst(request, c)
        stan = stan_oceny(c, w, opisy, metodyka)
        if z_grafem:
            zatwierdzil = None
            if c.zatwierdzona:
                wpisy = request.app.state.dziennik.historia("cykl", str(c.id))
                zatwierdzil = next((
                    wpis.autor for wpis in reversed(wpisy)
                    if wpis.roznica.get("zatwierdzona", {}).get("po") is True
                ), None)
            stan.graf = zbuduj_graf(
                c, profil, w, request.app.state.konfiguracja_grafu, request.app.state.katalogi_grafu[w.wersja],
                zatwierdzil=zatwierdzil,
            )
        return stan

    @r.get("/{cykl_id}/historia")
    def historia(cykl_id: int, request: Request, uz=auth.wymagaj("ocena.czytaj")) -> list[WpisHistorii]:
        """Historia zmian cyklu i oceny: kto, kiedy, co (różnica pól) i według której wersji reguł."""
        c = pobierz_cykl(request.app.state.baza, cykl_id)
        _pracownik(request, uz, c.pracownik_id, "ocena.czytaj")
        return [
            WpisHistorii(czas=str(w.czas), autor=w.autor, wersja_regul=w.wersja_regul, roznica=w.roznica)
            for w in request.app.state.dziennik.historia("cykl", str(cykl_id))
        ]

    @r.put("/{cykl_id}/ocena")
    def zapis(
        cykl_id: int, body: ZapisOceny, request: Request, baza: ZapisCyklu, uz=auth.wymagaj("cykl.prowadz")
    ) -> StanOceny:
        """Zapis odpowiedzi, punktów, oczekiwań i uzasadnień. `zatwierdz` wymaga
        kompletu punktów i – przy ocenie negatywnej – uzasadnienia (§ 11 ust. 3)."""
        c = pobierz_cykl(baza, cykl_id)
        _pracownik(request, uz, c.pracownik_id, "cykl.prowadz")
        w, opisy, metodyka = _kontekst(request, c)
        nowy = zapisz_ocene(c, body, w, opisy, request.app.state.kadry, metodyka)
        _zapisz(baza, nowy)
        request.app.state.dziennik.zapisz(uz.login, "cykl", str(c.id), _migawka(c), _migawka(nowy), c.wersja_regul)
        return stan_oceny(nowy, w, opisy, metodyka)

    return r


def zapisz_ocene(
    c: Cykl, body: ZapisOceny, w: WersjaRegul, opisy: dict[str, str], kadry: MockKadry, metodyka: Metodyka,
) -> Cykl:
    oczekiwana_wersja = body.numer_wersji if body.numer_wersji is not None else 1
    if oczekiwana_wersja != c.numer_wersji_oceny:
        raise HTTPException(409, "Wersja oceny się zmieniła. Odśwież arkusz przed zapisem.")
    if c.zatwierdzona:
        raise HTTPException(409, "Ocena jest zatwierdzona. Zmiana wymaga nowej wersji.")
    zmiana = c.ostatnie_ponowienie_kryteriow()
    if zmiana is not None and (zmiana.ustalono_kryteria is None or c.data_potwierdzenia is None):
        raise HTTPException(409, "Przed oceną ustal ponownie kryteria i potwierdź zapoznanie.")
    nowy = _zastosuj_zapis(c, w, metodyka, body)
    dzis = date.today()
    data_sporzadzenia = nowy.data_sporzadzenia or (dzis if body.zatwierdz else None)
    if data_sporzadzenia and (
        data_sporzadzenia > dzis or (c.data_potwierdzenia and data_sporzadzenia < c.data_potwierdzenia)
    ):
        raise HTTPException(422, "Data sporządzenia powinna przypadać między potwierdzeniem kryteriów a dzisiaj.")
    if nowy.data_rozmowy_oceniajacej and nowy.data_rozmowy_oceniajacej > (data_sporzadzenia or dzis):
        raise HTTPException(422, "Rozmowa oceniająca powinna odbyć się najpóźniej w dniu sporządzenia oceny.")
    if data_sporzadzenia and c.powod_wersji_oceny in ("ZMIANA_PO_ODWOLANIU", "PONOWNE_SPORZADZENIE"):
        decyzja = next((v.odwolanie for v in reversed(c.wersje_oceny) if v.odwolanie), None)
        if decyzja and decyzja.data_rozpatrzenia and data_sporzadzenia < decyzja.data_rozpatrzenia:
            raise HTTPException(422, "Data sporządzenia nie może poprzedzać rozpatrzenia odwołania.")
    if body.zatwierdz:
        stan = stan_oceny(nowy, w, opisy, metodyka)
        if stan.bledy:
            raise HTTPException(422, {"komunikat": "Nie można zatwierdzić oceny.", "bledy": stan.bledy})
        nowy.zatwierdzona = True
        nowy.data_zatwierdzenia = dzis
        nowy.data_sporzadzenia = data_sporzadzenia or dzis
        nowy.wersje_oceny = [*c.wersje_oceny, zamroz_arkusz(nowy, stan, w, kadry)]
    return nowy
