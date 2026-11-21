"""Przejścia obiegu oceny i pobieranie PDF oraz skanu konkretnej wersji."""

import binascii
import sqlite3
from base64 import b64decode
from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, StringConstraints

from .arkusz_pdf import generuj_arkusz
from .baza import ZapisCyklu
from .cykl import Cykl, _zapisz
from .obieg_oceny import Doreczenie, Odwolanie, PowodWersji, Rozstrzygniecie, WersjaOceny, stan_wersji
from .dostep import pracownik as _pracownik
from .ocena_cyklu import pobierz_cykl, reguly_cyklu

LIMIT_SKANU = 5 * 1024 * 1024
Osoba = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
FORMATY_SKANU = (
    (b"%PDF-", "application/pdf", "pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png", "png"),
    (b"\xff\xd8\xff", "image/jpeg", "jpg"),
)


class RejestracjaDoreczenia(BaseModel):
    data_zapoznania: date
    rejestrujacy: Osoba
    skan_base64: str | None = None


class WniesienieOdwolania(BaseModel):
    data_wniesienia: date


class RozpatrzenieOdwolania(BaseModel):
    data_rozpatrzenia: date
    rozpatrzyl: Osoba
    rozstrzygniecie: Rozstrzygniecie


def utworz_tabele_plikow(baza: sqlite3.Connection) -> None:
    with baza:
        baza.execute(
            "CREATE TABLE IF NOT EXISTS pliki_ocen ("
            "cykl_id INTEGER NOT NULL, numer INTEGER NOT NULL, pdf BLOB NOT NULL, "
            "skan BLOB, typ_skanu TEXT, PRIMARY KEY (cykl_id, numer))"
        )


def _aktualna_wersja(c: Cykl, numer: int) -> WersjaOceny:
    if not c.zatwierdzona or numer != c.numer_wersji_oceny or not c.wersje_oceny:
        raise HTTPException(409, "Operacja wymaga aktualnej, zatwierdzonej wersji oceny.")
    return c.wersje_oceny[-1]


def _robocza(c: Cykl, powod: PowodWersji) -> None:
    c.numer_wersji_oceny += 1
    c.powod_wersji_oceny = powod
    c.zatwierdzona = False
    c.data_zatwierdzenia = None
    c.data_sporzadzenia = None
    if powod == "PONOWNE_SPORZADZENIE":
        c.oceny = {}
        c.odpowiedzi = {}
        c.uzasadnienia_odstepstw = {}
        c.uzasadnienie = None
        c.data_rozmowy_oceniajacej = None
        c.wnioski_rozwojowe = ""


def _plik(dane: bytes, typ: str, nazwa: str) -> Response:
    return Response(dane, media_type=typ, headers={
        "Content-Disposition": f'attachment; filename="{nazwa}"',
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    })


def _skan(zakodowany: str) -> tuple[bytes, str]:
    if len(zakodowany) > 4 * ((LIMIT_SKANU + 2) // 3):
        raise HTTPException(422, "Skan może mieć najwyżej 5 MB.")
    try:
        dane = b64decode(zakodowany, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(422, "Nieprawidłowy zapis skanu.") from exc
    if len(dane) > LIMIT_SKANU:
        raise HTTPException(422, "Skan może mieć najwyżej 5 MB.")
    for naglowek, typ, _ in FORMATY_SKANU:
        if dane.startswith(naglowek):
            return dane, typ
    raise HTTPException(422, "Dołącz skan w formacie PDF, PNG albo JPG.")


def _pdf(dane: bytes, cykl_id: int, numer: int) -> Response:
    return _plik(dane, "application/pdf", f"ocena-{cykl_id}-wersja-{numer}.pdf")


def _cykl(request: Request, baza: sqlite3.Connection, uz, cykl_id: int, akcja: str) -> Cykl:
    """Cykl, jeśli użytkownik ma akcję i dostęp do ocenianej osoby (#10); inaczej 404/403."""
    c = pobierz_cykl(baza, cykl_id)
    _pracownik(request, uz, c.pracownik_id, akcja)
    return c


def router(auth) -> APIRouter:
    r = APIRouter(prefix="/api/cykle/{cykl_id}/ocena/wersje/{numer}", tags=["wydruk, doręczenie i odwołanie"])

    @r.post("/nowa", status_code=204)
    def nowa_wersja(cykl_id: int, numer: int, baza: ZapisCyklu, request: Request, uz=auth.wymagaj("cykl.prowadz")) -> None:
        c = _cykl(request, baza, uz, cykl_id, "cykl.prowadz")
        v = _aktualna_wersja(c, numer)
        if v.doreczenie:
            raise HTTPException(409, "Doręczoną ocenę można zmienić po uwzględnieniu odwołania.")
        powod = c.powod_wersji_oceny if c.powod_wersji_oceny in (
            "ZMIANA_PO_ODWOLANIU", "PONOWNE_SPORZADZENIE",
        ) else "KOREKTA"
        _robocza(c, powod)
        _zapisz(baza, c)

    @r.post("/wydruk", response_class=Response)
    def wydruk(cykl_id: int, numer: int, baza: ZapisCyklu, request: Request, uz=auth.wymagaj("cykl.prowadz")) -> Response:
        c = _cykl(request, baza, uz, cykl_id, "cykl.prowadz")
        v = _aktualna_wersja(c, numer)
        rekord = baza.execute(
            "SELECT pdf FROM pliki_ocen WHERE cykl_id = ? AND numer = ?", (cykl_id, numer),
        ).fetchone()
        if rekord:
            return _pdf(rekord[0], cykl_id, numer)
        try:
            pdf = generuj_arkusz(v.tresc, v.hash)
        except FileNotFoundError as exc:
            raise HTTPException(503, "Brak czcionki PDF. Sprawdź ustawienie CAOO_PDF_FONT.") from exc
        baza.execute("INSERT INTO pliki_ocen (cykl_id, numer, pdf) VALUES (?, ?, ?)", (cykl_id, numer, pdf))
        v.data_wydruku = date.today()
        _zapisz(baza, c)
        return _pdf(pdf, cykl_id, numer)

    @r.get("/pdf", response_class=Response)
    def pobierz_pdf(cykl_id: int, numer: int, request: Request, uz=auth.wymagaj("ocena.czytaj")) -> Response:
        _cykl(request, request.app.state.baza, uz, cykl_id, "ocena.czytaj")
        rekord = request.app.state.baza.execute(
            "SELECT pdf FROM pliki_ocen WHERE cykl_id = ? AND numer = ?", (cykl_id, numer),
        ).fetchone()
        if rekord is None:
            raise HTTPException(404, "Nie wygenerowano wydruku tej wersji.")
        return _pdf(rekord[0], cykl_id, numer)

    @r.post("/doreczenie", status_code=204)
    def dorecz(cykl_id: int, numer: int, body: RejestracjaDoreczenia, baza: ZapisCyklu, request: Request, uz=auth.wymagaj("ocena.obieg")) -> None:
        skan = _skan(body.skan_base64) if body.skan_base64 is not None else None
        c = _cykl(request, baza, uz, cykl_id, "ocena.obieg")
        v = _aktualna_wersja(c, numer)
        if not v.data_wydruku or v.doreczenie:
            raise HTTPException(409, "Rejestracja jest dostępna raz po wydrukowaniu wersji.")
        dzis = date.today()
        if not max(v.tresc.data_sporzadzenia, v.data_wydruku) <= body.data_zapoznania <= dzis:
            raise HTTPException(422, "Data zapoznania powinna przypadać między wydrukiem oceny a dzisiaj.")
        if skan:
            baza.execute(
                "UPDATE pliki_ocen SET skan = ?, typ_skanu = ? WHERE cykl_id = ? AND numer = ?",
                (*skan, cykl_id, numer),
            )
        v.doreczenie = Doreczenie(
            data_zapoznania=body.data_zapoznania, rejestrujacy=body.rejestrujacy,
            zarejestrowano=dzis, ma_skan=skan is not None,
        )
        _zapisz(baza, c)

    @r.get("/skan", response_class=Response)
    def pobierz_skan(cykl_id: int, numer: int, request: Request, uz=auth.wymagaj("ocena.czytaj")) -> Response:
        _cykl(request, request.app.state.baza, uz, cykl_id, "ocena.czytaj")
        rekord = request.app.state.baza.execute(
            "SELECT skan, typ_skanu FROM pliki_ocen WHERE cykl_id = ? AND numer = ? AND skan IS NOT NULL",
            (cykl_id, numer),
        ).fetchone()
        if rekord is None:
            raise HTTPException(404, "Do tej wersji nie dołączono skanu.")
        dane, typ = rekord
        rozszerzenie = next(ext for _, mime, ext in FORMATY_SKANU if mime == typ)
        return _plik(dane, typ, f"skan-oceny-{cykl_id}-wersja-{numer}.{rozszerzenie}")

    @r.post("/odwolanie", status_code=204)
    def odwolaj(cykl_id: int, numer: int, body: WniesienieOdwolania, request: Request, baza: ZapisCyklu, uz=auth.wymagaj("ocena.obieg")) -> None:
        c = _cykl(request, baza, uz, cykl_id, "ocena.obieg")
        v = _aktualna_wersja(c, numer)
        w = reguly_cyklu(c, request.app.state.wersje)
        termin = stan_wersji(v, w).termin_odwolania
        if not termin or v.odwolanie:
            raise HTTPException(409, "Wniesienie odwołania dla tej wersji jest niedostępne.")
        if not termin.data_odniesienia <= body.data_wniesienia <= min(termin.data, date.today()):
            raise HTTPException(422, f"Data wniesienia musi mieścić się w terminie odwołania: do {termin.data}.")
        v.odwolanie = Odwolanie(data_wniesienia=body.data_wniesienia)
        _zapisz(baza, c)

    @r.post("/rozpatrzenie", status_code=204)
    def rozpatrz(cykl_id: int, numer: int, body: RozpatrzenieOdwolania, baza: ZapisCyklu, request: Request, uz=auth.wymagaj("ocena.obieg")) -> None:
        c = _cykl(request, baza, uz, cykl_id, "ocena.obieg")
        v = _aktualna_wersja(c, numer)
        o = v.odwolanie
        if not o or o.rozstrzygniecie:
            raise HTTPException(409, "Rozpatrzyć można odwołanie oczekujące na decyzję.")
        if not o.data_wniesienia <= body.data_rozpatrzenia <= date.today():
            raise HTTPException(422, "Data rozpatrzenia powinna przypadać między wniesieniem a dzisiaj.")
        o.data_rozpatrzenia = body.data_rozpatrzenia
        o.rozpatrzyl = body.rozpatrzyl
        o.rozstrzygniecie = body.rozstrzygniecie
        if o.rozstrzygniecie == "ZMIANA_OCENY":
            _robocza(c, "ZMIANA_PO_ODWOLANIU")
        elif o.rozstrzygniecie == "PONOWNE_SPORZADZENIE":
            _robocza(c, "PONOWNE_SPORZADZENIE")
        _zapisz(baza, c)

    return r
