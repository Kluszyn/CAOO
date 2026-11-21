"""Obsługa § 8–9 i wniosków o zmianę terminu cyklu oceny okresowej."""

from datetime import date
import sqlite3

from fastapi import APIRouter, HTTPException, Request, Response
from caoo_core.kalendarz import WynikTerminu, wyznacz_termin
from caoo_core.pdf import DokumentPdf, Stopka, hash_tresci

from .baza import ZapisCyklu
from .cykl import Cykl, _wczytaj, _zapisz
from .kadry import Profil
from .kryteria import KryteriaDoUstalenia, TerminKryteriow, do_ustalenia, sprawdz_wybor
from .reguly import WersjaRegul
from .swieta import dni_wolne
from .terminy import TerminOceny, termin_oceny
from .zmiany import (
    DecyzjaWniosku,
    PonowneKryteria,
    WniosekZmianyTerminu,
    ZapisKryteriow,
    ZawiadomienieZmianyTerminu,
    ZgloszenieOceniajacego,
    ZgloszenieStanowiska,
    ZlozenieWniosku,
    ZmianaOceniajacego,
    ZmianaStanowiska,
)


def _cykl(baza: sqlite3.Connection, id: int) -> Cykl:
    c = _wczytaj(baza, id)
    if c is None:
        raise HTTPException(404, "Nie ma takiego cyklu.")
    return c


def _dane_cyklu(
    request: Request, baza: sqlite3.Connection, id: int,
) -> tuple[Cykl, Profil, WersjaRegul]:
    c = _cykl(baza, id)
    if c.status != "W_TOKU" or c.zatwierdzona:
        raise HTTPException(409, "Zmiany wprowadza się w cyklu z oceną roboczą.")
    p = request.app.state.kadry.profil(c.pracownik_id)
    if p is None:
        raise HTTPException(409, "Brakuje profilu ocenianego.")
    w = next((w for w in request.app.state.wersje if w.wersja == c.wersja_regul), None)
    if w is None or "kryteria_po_zmianie" not in w.terminy:
        raise HTTPException(409, "Przypięta wersja reguł nie zawiera zasad zmiany cyklu.")
    return c, p, w


def _termin(w: WersjaRegul, data: date, nazwa: str) -> WynikTerminu:
    wolne = dni_wolne(w.terminy, max(1, data.year - 1), min(date.max.year, data.year + 1))
    try:
        return wyznacz_termin(data, w.regula_terminu(nazwa), dni_wolne=wolne)
    except (OverflowError, ValueError) as e:
        raise HTTPException(422, "Data wykracza poza zakres obliczeń terminu.") from e


def _stan_kryteriow(c: Cykl) -> ZapisKryteriow:
    return ZapisKryteriow(
        arkusz=c.arkusz, obowiazkowe=c.obowiazkowe, dodatkowe=c.dodatkowe,
        punkty=c.oceny, oczekiwania=c.oczekiwania, uzasadnienie=c.uzasadnienie,
        potwierdzenie=c.data_potwierdzenia, potwierdzil=c.potwierdzil,
        wersja_metodyki=c.wersja_metodyki, odpowiedzi=c.odpowiedzi,
        uzasadnienia_odstepstw=c.uzasadnienia_odstepstw,
    )


def _oczekujace_kryteria(c: Cykl) -> ZmianaStanowiska:
    z = c.ostatnie_ponowienie_kryteriow()
    if z is None or z.ustalono_kryteria is not None:
        raise HTTPException(409, "Brak zmiany oczekującej na ponowne ustalenie kryteriów.")
    return z


def _aktualny_oceniajacy(c: Cykl, p: Profil) -> str | None:
    return c.zmiany_oceniajacego[-1].aktualny if c.zmiany_oceniajacego else p.przelozony


def _podstawa_terminu(p: Profil, w: WersjaRegul) -> TerminOceny:
    obliczony = termin_oceny(p, w)
    if obliczony is None:
        raise HTTPException(409, "Wniosek dotyczy podstawowego cyklu oceny okresowej.")
    return obliczony


def _pdf(z: ZawiadomienieZmianyTerminu) -> bytes:
    stopka = Stopka(z.wersja_regul, hash_tresci(z.model_dump(mode="json")), "Kopia do arkusza oceny okresowej")
    doc = DokumentPdf(stopka)
    doc.naglowek("Zawiadomienie o zmianie terminu oceny okresowej")
    doc.tabela([
        ["Pole", "Dane"],
        ["Jednostka", z.jednostka],
        ["Oceniany", z.oceniany],
        ["Oceniający", z.oceniajacy],
        ["Data zawiadomienia", z.data.strftime("%d.%m.%Y")],
        ["Dotychczasowy termin", z.poprzedni_termin.strftime("%d.%m.%Y")],
        ["Nowy termin sporządzenia oceny na piśmie", z.nowy_termin.strftime("%d.%m.%Y")],
    ], szerokosci=[185, 275])
    doc.akapit(
        "Podstawa prawna to art. 81 ust. 8 pkt 4 ustawy o służbie cywilnej "
        "oraz § 8 rozporządzenia w sprawie ocen okresowych."
    )
    doc.podpis("Podpis oceniającego")
    return doc.bajty()


def router(auth) -> APIRouter:
    from .dostep import pracownik as _pracownik

    def _dostep(request: Request, uz, id: int, akcja: str) -> None:
        _pracownik(request, uz, _cykl(request.app.state.baza, id).pracownik_id, akcja)

    r = APIRouter(prefix="/api/cykle", tags=["zmiany w cyklu oceny okresowej"])

    @r.post("/{id}/zmiany-stanowiska")
    def stanowisko(id: int, body: ZgloszenieStanowiska, request: Request, baza: ZapisCyklu, uz=auth.wymagaj("cykl.prowadz")) -> Cykl:
        _dostep(request, uz, id, "cykl.prowadz")
        c, p, w = _dane_cyklu(request, baza, id)
        poczatek = max((o.data_sporzadzenia for o in p.poprzednie_oceny), default=c.rozpoczeto.date())
        if body.data < poczatek:
            raise HTTPException(422, "Data zmiany musi przypadać w bieżącym cyklu oceny.")
        if c.zmiany_stanowiska and body.data < c.zmiany_stanowiska[-1].data:
            raise HTTPException(422, "Data zmiany nie może poprzedzać ostatniej zapisanej zmiany stanowiska.")
        granica = _termin(w, poczatek, "zmiana_prog").data
        z = ZmianaStanowiska(
            data=body.data, stanowisko=body.stanowisko, arkusz=body.arkusz or c.arkusz, tryb="ODNOTOWANO",
        )
        if body.istotna_zmiana and body.data <= granica:
            wynik = _termin(w, body.data, "kryteria_po_zmianie")
            z.tryb = "PONOWNE_KRYTERIA"
            z.regula = "termin.ponowne-kryteria-9-1"
            z.termin_kryteriow = TerminKryteriow(
                data_zdarzenia=body.data, zdarzenie="istotna zmiana stanowiska", termin=wynik.data,
                regula=z.regula, slad=[w.krok_terminu(k) for k in wynik.slad],
            )
            z.poprzednie_kryteria = _stan_kryteriow(c)
        elif body.istotna_zmiana:
            z.tryb = "OCENA_PRZY_ZMIANIE"
            z.regula = "termin.okno-oceny-9-2"
            z.ocena_od = _termin(w, body.data, "ocena_przy_zmianie_od").data
            z.ocena_do = _termin(w, body.data, "ocena_przy_zmianie_do").data
        c.zmiany_stanowiska.append(z)
        _zapisz(baza, c)
        return c

    @r.get("/{id}/ponowne-kryteria")
    def kryteria(id: int, request: Request, uz=auth.wymagaj("ocena.czytaj")) -> KryteriaDoUstalenia:
        _dostep(request, uz, id, "ocena.czytaj")
        c, p, w = _dane_cyklu(request, request.app.state.baza, id)
        z = _oczekujace_kryteria(c)
        u = do_ustalenia(p, z.arkusz, w)
        return u.model_copy(update={"termin": z.termin_kryteriow, "braki": [], "propozycje": []})

    @r.post("/{id}/ponowne-kryteria")
    def ustal_kryteria(id: int, body: PonowneKryteria, request: Request, baza: ZapisCyklu, uz=auth.wymagaj("cykl.prowadz")) -> Cykl:
        _dostep(request, uz, id, "cykl.prowadz")
        c, p, w = _dane_cyklu(request, baza, id)
        z = _oczekujace_kryteria(c)
        if body.data < z.data:
            raise HTTPException(422, "Nowe kryteria można ustalić od dnia zmiany stanowiska.")
        bledy = sprawdz_wybor(body.dodatkowe, z.arkusz, w)
        if bledy:
            raise HTTPException(422, {"bledy": bledy})
        u = do_ustalenia(p, z.arkusz, w)
        wykaz = {k.id: k for k in u.dostepne_dodatkowe}
        z.ustalono_kryteria = body.data
        c = c.model_copy(update={
            "arkusz": z.arkusz, "obowiazkowe": u.obowiazkowe,
            "dodatkowe": [wykaz[k] for k in body.dodatkowe], "termin": z.termin_kryteriow,
            "potwierdzil": None, "data_potwierdzenia": None, "oceny": {}, "oczekiwania": {}, "uzasadnienie": None,
            "odpowiedzi": {}, "uzasadnienia_odstepstw": {},
        })
        _zapisz(baza, c)
        return c

    @r.post("/{id}/zmiany-oceniajacego")
    def oceniajacy(id: int, body: ZgloszenieOceniajacego, request: Request, baza: ZapisCyklu, uz=auth.wymagaj("cykl.prowadz")) -> Cykl:
        _dostep(request, uz, id, "cykl.prowadz")
        c, p, _ = _dane_cyklu(request, baza, id)
        if c.zmiany_oceniajacego and body.data < c.zmiany_oceniajacego[-1].data:
            raise HTTPException(422, "Data zmiany nie może poprzedzać ostatniej zapisanej zmiany oceniającego.")
        poprzedni = _aktualny_oceniajacy(c, p)
        if poprzedni == body.nowy_oceniajacy:
            raise HTTPException(422, "Podaj inną osobę oceniającą.")
        c.zmiany_oceniajacego.append(ZmianaOceniajacego(data=body.data, poprzedni=poprzedni, aktualny=body.nowy_oceniajacy))
        _zapisz(baza, c)
        return c

    @r.post("/{id}/wnioski-zmiany-terminu", status_code=201)
    def wniosek(id: int, body: ZlozenieWniosku, request: Request, baza: ZapisCyklu, uz=auth.wymagaj("cykl.wniosek")) -> Cykl:
        _dostep(request, uz, id, "cykl.wniosek")
        c, p, w = _dane_cyklu(request, baza, id)
        termin = _podstawa_terminu(p, w)
        obecny = c.termin_oceny or termin.termin
        granica = _termin(w, termin.data_zdarzenia, "zmiana_prog").data
        if body.data <= granica:
            raise HTTPException(422, f"Art. 81 ust. 8 wymaga upływu ponad 6 miesięcy. Granica to {granica}.")
        if body.proponowany_termin < body.data or body.proponowany_termin == obecny:
            raise HTTPException(422, "Proponowany termin musi się zmienić i przypadać od dnia złożenia wniosku.")
        c.wnioski_zmiany_terminu.append(WniosekZmianyTerminu(
            id=len(c.wnioski_zmiany_terminu) + 1, **body.model_dump(),
        ))
        c.termin_oceny = obecny
        _zapisz(baza, c)
        return c

    @r.post("/{id}/wnioski-zmiany-terminu/{wniosek_id}/decyzja")
    def decyzja(id: int, wniosek_id: int, body: DecyzjaWniosku, request: Request, baza: ZapisCyklu, uz=auth.wymagaj("cykl.prowadz")) -> Cykl:
        _dostep(request, uz, id, "cykl.prowadz")
        c, p, w = _dane_cyklu(request, baza, id)
        wniosek = next((w for w in c.wnioski_zmiany_terminu if w.id == wniosek_id), None)
        if wniosek is None:
            raise HTTPException(404, "Brak wniosku w tym cyklu.")
        if wniosek.status != "OCZEKUJE":
            raise HTTPException(409, "Wniosek został już rozpatrzony.")
        if body.data < wniosek.data or (body.uwzglednij and body.data > wniosek.proponowany_termin):
            raise HTTPException(422, "Data decyzji nie może poprzedzać wniosku. Przy uwzględnieniu musi mieścić się w nowym terminie.")
        if body.uwzglednij:
            poprzedni = c.termin_oceny or _podstawa_terminu(p, w).termin
            if poprzedni == wniosek.proponowany_termin:
                raise HTTPException(422, "Wnioskowany termin jest już aktualny.")
            c.zawiadomienia.append(ZawiadomienieZmianyTerminu(
                wniosek_id=wniosek.id, data=body.data, oceniany=f"{p.imie} {p.nazwisko}", jednostka=p.jednostka,
                oceniajacy=_aktualny_oceniajacy(c, p) or c.rozpoczal, poprzedni_termin=poprzedni,
                nowy_termin=wniosek.proponowany_termin, wersja_regul=c.wersja_regul,
                adnotacje=w.regula("termin.zawiadomienie-8").adnotacje,
            ))
            c.termin_oceny = wniosek.proponowany_termin
        wniosek.data_decyzji = body.data
        wniosek.status = "UWZGLEDNIONY" if body.uwzglednij else "ODRZUCONY"
        _zapisz(baza, c)
        return c

    @r.get("/{id}/zawiadomienia/{wniosek_id}/pdf")
    def kopia_zawiadomienia(id: int, wniosek_id: int, request: Request, uz=auth.wymagaj("ocena.czytaj")) -> Response:
        _dostep(request, uz, id, "ocena.czytaj")
        c = _cykl(request.app.state.baza, id)
        z = next((z for z in c.zawiadomienia if z.wniosek_id == wniosek_id), None)
        if z is None:
            raise HTTPException(404, "Brak zawiadomienia w tym cyklu.")
        return Response(
            _pdf(z), media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="zawiadomienie-{id}-{wniosek_id}.pdf"'},
        )

    return r
