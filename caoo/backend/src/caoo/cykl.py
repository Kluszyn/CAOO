"""Cykl oceny okresowej – ustalenie kryteriów i potwierdzenie przez ocenianego (#12).

Zapis w SQLite. Każdy cykl pamięta wersję reguł, z którą go rozpoczęto.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .baza import ZapisCyklu
from .kryteria import KryteriaDoUstalenia, Kryterium, TerminKryteriow, do_ustalenia, sprawdz_wybor
from .metodyka import Odpowiedz
from .obieg_oceny import PowodWersji, WersjaOceny
from .proces import WyborProcesu, wybierz
from .reguly import wersja_na_dzien
from .zmiany import WniosekZmianyTerminu, ZawiadomienieZmianyTerminu, ZmianaOceniajacego, ZmianaStanowiska


class StartCyklu(BaseModel):
    dodatkowe: list[str] = Field(default_factory=list, description="Id kryteriów dodatkowych z wykazu (najwyżej 3).")
    rozpoczal: str = Field(default="oceniający", description="Kto rozpoczyna (role w #10).")


class Potwierdzenie(BaseModel):
    data: date | None = Field(default=None, description="Data potwierdzenia; domyślnie dziś.")


class Cykl(BaseModel):
    id: int
    pracownik_id: str
    arkusz: str
    obowiazkowe: list[Kryterium]
    dodatkowe: list[Kryterium]
    termin: TerminKryteriow | None
    rozpoczal: str
    rozpoczeto: datetime
    wersja_regul: str
    potwierdzil: str | None = None
    data_potwierdzenia: date | None = None
    status: str
    oceny: dict[str, int] = Field(default_factory=dict, description="Punkty kryteriów (#13).")
    oczekiwania: dict[str, str] = Field(default_factory=dict)
    uzasadnienie: str | None = None
    zatwierdzona: bool = False
    data_zatwierdzenia: date | None = None
    wersja_metodyki: str | None = None
    odpowiedzi: dict[str, dict[int, Odpowiedz]] = Field(default_factory=dict)
    uzasadnienia_odstepstw: dict[str, str] = Field(default_factory=dict)
    termin_oceny: date | None = None
    zmiany_stanowiska: list[ZmianaStanowiska] = Field(default_factory=list)
    zmiany_oceniajacego: list[ZmianaOceniajacego] = Field(default_factory=list)
    wnioski_zmiany_terminu: list[WniosekZmianyTerminu] = Field(default_factory=list)
    zawiadomienia: list[ZawiadomienieZmianyTerminu] = Field(default_factory=list)
    data_sporzadzenia: date | None = None
    data_rozmowy_oceniajacej: date | None = None
    wnioski_rozwojowe: str = ""
    numer_wersji_oceny: int = 1
    powod_wersji_oceny: PowodWersji = "PIERWSZA"
    wersje_oceny: list[WersjaOceny] = Field(default_factory=list)

    def ostatnie_ponowienie_kryteriow(self) -> ZmianaStanowiska | None:
        return next((z for z in reversed(self.zmiany_stanowiska) if z.tryb == "PONOWNE_KRYTERIA"), None)


def utworz_tabele(baza: sqlite3.Connection) -> None:
    with baza:
        baza.execute(
            "CREATE TABLE IF NOT EXISTS cykle (id INTEGER PRIMARY KEY, pracownik_id TEXT NOT NULL,"
            " dane TEXT NOT NULL)"
        )


def _zapisz(baza: sqlite3.Connection, c: Cykl) -> None:
    with baza:
        baza.execute(
            "INSERT OR REPLACE INTO cykle (id, pracownik_id, dane) VALUES (?, ?, ?)",
            (c.id, c.pracownik_id, c.model_dump_json()),
        )


def _wczytaj(baza: sqlite3.Connection, cykl_id: int) -> Cykl | None:
    w = baza.execute("SELECT dane FROM cykle WHERE id = ?", (cykl_id,)).fetchone()
    return Cykl.model_validate_json(w[0]) if w else None


def w_toku(baza: sqlite3.Connection, pracownik_id: str) -> Cykl | None:
    w = baza.execute(
        "SELECT dane FROM cykle WHERE pracownik_id = ? AND json_extract(dane, '$.status') = 'W_TOKU'",
        (pracownik_id,),
    ).fetchone()
    return Cykl.model_validate_json(w[0]) if w else None


def router(auth) -> APIRouter:
    from .dostep import pracownik as _pracownik

    r = APIRouter(prefix="/api", tags=["cykl oceny: kryteria i termin (§ 4–7)"])

    def profil_i_wybor(request: Request, pracownik_id: str):
        p = request.app.state.kadry.profil(pracownik_id)
        if p is None:
            raise HTTPException(404, "Nie ma takiego pracownika")
        w = wersja_na_dzien(request.app.state.wersje, date.today())
        wybor: WyborProcesu = wybierz(p, w)
        if wybor.proces != "OKRESOWA" or wybor.arkusz is None:
            raise HTTPException(
                409,
                {"komunikat": "Kryteria § 4–5 dotyczą oceny okresowej z ustalonym arkuszem.", "wybor": wybor.model_dump()},
            )
        return p, w, wybor

    @r.get("/pracownicy/{pracownik_id}/kryteria")
    def kryteria(pracownik_id: str, request: Request, uz=auth.wymagaj("ocena.czytaj")) -> KryteriaDoUstalenia:
        """Kryteria obowiązkowe arkusza, propozycje dodatkowych i termin ustalenia kryteriów."""
        _pracownik(request, uz, pracownik_id, "ocena.czytaj")
        p, w, wybor = profil_i_wybor(request, pracownik_id)
        return do_ustalenia(p, wybor.arkusz, w)

    @r.post("/pracownicy/{pracownik_id}/cykle", status_code=201)
    def rozpocznij(
        pracownik_id: str, body: StartCyklu, request: Request, baza: ZapisCyklu, uz=auth.wymagaj("cykl.prowadz")
    ) -> Cykl:
        """Rozpoczęcie cyklu: zatwierdzenie kryteriów dodatkowych (najwyżej 3) i termin § 7."""
        _pracownik(request, uz, pracownik_id, "cykl.prowadz")
        p, w, wybor = profil_i_wybor(request, pracownik_id)
        bledy = sprawdz_wybor(body.dodatkowe, wybor.arkusz, w)
        if bledy:
            raise HTTPException(422, {"komunikat": "Niepoprawny wybór kryteriów dodatkowych.", "bledy": bledy})
        if w_toku(baza, pracownik_id):
            raise HTTPException(409, "Ten pracownik ma już cykl oceny w toku.")
        u = do_ustalenia(p, wybor.arkusz, w)
        wykaz = {k.id: k for k in [*u.obowiazkowe, *u.dostepne_dodatkowe]}
        (nowe_id,) = baza.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM cykle").fetchone()
        c = Cykl(
            id=nowe_id,
            pracownik_id=pracownik_id,
            arkusz=wybor.arkusz,
            obowiazkowe=u.obowiazkowe,
            dodatkowe=[wykaz[k] for k in body.dodatkowe],
            termin=u.termin,
            termin_oceny=wybor.termin_oceny.termin if wybor.termin_oceny else None,
            rozpoczal=body.rozpoczal,
            rozpoczeto=datetime.now(UTC),
            wersja_regul=w.wersja,
            wersja_metodyki=request.app.state.ustawienia.domyslna_metodyka,
            status="W_TOKU",
        )
        _zapisz(baza, c)
        request.app.state.dziennik.zapisz(
            uz.login, "cykl", str(c.id), None,
            {"rozpoczeto": c.rozpoczeto, "obowiazkowe": [k.id for k in c.obowiazkowe],
             "dodatkowe": [k.id for k in c.dodatkowe], "status": c.status},
            c.wersja_regul,
        )
        return c

    @r.get("/cykle/{cykl_id}")
    def jeden(cykl_id: int, request: Request, uz=auth.wymagaj("ocena.czytaj")) -> Cykl:
        c = _wczytaj(request.app.state.baza, cykl_id)
        if c is None:
            raise HTTPException(404, "Nie ma takiego cyklu")
        _pracownik(request, uz, c.pracownik_id, "ocena.czytaj")
        return c

    @r.get("/pracownicy/{pracownik_id}/cykle")
    def lista(pracownik_id: str, request: Request, uz=auth.wymagaj("ocena.czytaj")) -> list[Cykl]:
        _pracownik(request, uz, pracownik_id, "ocena.czytaj")
        return [
            Cykl.model_validate_json(d)
            for (d,) in request.app.state.baza.execute(
                "SELECT dane FROM cykle WHERE pracownik_id = ? ORDER BY id", (pracownik_id,)
            )
        ]

    @r.post("/cykle/{cykl_id}/potwierdzenie")
    def potwierdz(
        cykl_id: int, body: Potwierdzenie, request: Request, baza: ZapisCyklu, uz=auth.wymagaj("cykl.potwierdz")
    ) -> Cykl:
        """Oceniany potwierdza rozmowę i zapoznanie z kryteriami i terminem (§ 6 ust. 2)."""
        c = _wczytaj(baza, cykl_id)
        if c is None:
            raise HTTPException(404, "Nie ma takiego cyklu")
        _pracownik(request, uz, c.pracownik_id, "cykl.potwierdz")
        zmiana = c.ostatnie_ponowienie_kryteriow()
        data_potwierdzenia = body.data or date.today()
        if zmiana is not None and (zmiana.ustalono_kryteria is None or data_potwierdzenia < zmiana.ustalono_kryteria):
            raise HTTPException(409, "Potwierdzenie wymaga ponownego ustalenia kryteriów przed tą datą.")
        if c.data_potwierdzenia is not None:
            raise HTTPException(409, f"Cykl potwierdzono już {c.data_potwierdzenia.isoformat()}.")
        if c.zatwierdzona:
            raise HTTPException(409, "Nie można zmienić potwierdzenia po zatwierdzeniu oceny.")
        p = request.app.state.kadry.profil(c.pracownik_id)
        c = c.model_copy(
            update={
                "potwierdzil": f"{p.imie} {p.nazwisko}" if p else c.pracownik_id,
                "data_potwierdzenia": data_potwierdzenia,
            }
        )
        _zapisz(baza, c)
        request.app.state.dziennik.zapisz(
            uz.login, "cykl", str(c.id), {"data_potwierdzenia": None},
            {"potwierdzil": c.potwierdzil, "data_potwierdzenia": c.data_potwierdzenia}, c.wersja_regul,
        )
        return c

    return r


def zrzut(c: Cykl) -> dict:
    return json.loads(c.model_dump_json())
