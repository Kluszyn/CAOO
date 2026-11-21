"""API aplikacji."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date

from fastapi import FastAPI, HTTPException, Request
from caoo_core.llm import DziennikJsonl
from caoo_core.skala import oblicz_wynik
from caoo_core.zrodla import SourceRef, waliduj_adnotacje
from pydantic import BaseModel

import yaml
from caoo_core.auth import Autoryzacja, Dziennik, KonfiguracjaRol
from .dostep import pracownik as dostep_pracownik
from . import aktualizacja
from .api_zmian import router as router_zmian
from caoo_core.llm import DziennikJsonl
from .dowody import KonfiguracjaDowodow
from .dowody import dostawcy as dostawcy_dowodow
from .dowody import router as router_dowodow
from .mapowanie import KonfiguracjaMapowania
from .mapowanie import router as router_mapowania
from .ustawienia import KATALOG_APLIKACJI
from .rekomendacje import KonfiguracjaRekomendacji
from .rekomendacje import router as router_rekomendacji
from .baza import polacz
from .cykl import router as router_cyklu
from .cykl import w_toku
from .cykl import utworz_tabele
from .graf import zbuduj_katalog
from .graf_model import KatalogGrafu, wczytaj_konfiguracje_grafu
from .import_arkuszy import router as router_importu
from .pierwsza_ocena import KonfiguracjaPierwszejOceny
from .pierwsza_ocena import router as router_pierwszej_oceny
from .kadry import MockKadry
from .kadry import router as router_kadr
from .korpus_aplikacji import zaladuj
from .metodyka import wczytaj_metodyki
from .obieg_api import router as router_obiegu
from .obieg_api import utworz_tabele_plikow
from .ocena_cyklu import router as router_oceny
from .ocena_cyklu import uzupelnij_zatwierdzone
from .proces import WyborProcesu, wybierz
from .reguly import KrokTerminu, RegulaV1, wczytaj_wersje, wersja_na_dzien
from .rozmowa import KonfiguracjaRozmowy
from .rozmowa import router as router_rozmowy
from .ustawienia import Ustawienia


class AdnotacjaPanelu(BaseModel):
    adnotacja: SourceRef
    dokument: str | None
    plik: str | None
    cytat_w_korpusie: bool | None


class RegulaPanelu(BaseModel):
    id: str
    nazwa: str | None
    opis: str
    adnotacje: list[AdnotacjaPanelu]


class RozbieznoscPanelu(RegulaPanelu):
    rozbieznosc: dict


class WersjaPanelu(BaseModel):
    wersja: str
    obowiazuje_od: date
    plik: str
    data_obowiazywania: RegulaPanelu
    reguly: list[RegulaPanelu]
    rozbieznosci: list[RozbieznoscPanelu] = []


class ZapytanieSkali(BaseModel):
    oceny: list[int]
    dzien: date | None = None


def utworz_aplikacje(ustawienia: Ustawienia | None = None) -> FastAPI:
    ust = ustawienia or Ustawienia.ze_srodowiska()

    @asynccontextmanager
    async def cykl(app: FastAPI):
        app.state.ustawienia = ust
        app.state.baza = polacz(ust.baza)
        utworz_tabele(app.state.baza)
        utworz_tabele_plikow(app.state.baza)
        app.state.korpus = zaladuj(ust.manifest)
        # Wersje z YAML + opublikowane w aplikacji (#25); akty opublikowane trafiają do korpusu.
        aktualizacja.utworz_tabele(app.state.baza)
        app.state.wersje = aktualizacja.wczytaj_opublikowane(
            app.state.baza, wczytaj_wersje(ust.reguly), app.state.korpus,
        )
        app.state.konfiguracja_grafu = wczytaj_konfiguracje_grafu(ust.graf)
        app.state.katalogi_grafu = {
            wersja.wersja: zbuduj_katalog(wersja, app.state.konfiguracja_grafu) for wersja in app.state.wersje
        }
        app.state.metodyki = wczytaj_metodyki(ust.metodyki, ust.domyslna_metodyka)
        app.state.kadry = MockKadry.z_pliku(ust.kadry)
        with app.state.baza as baza:
            uzupelnij_zatwierdzone(baza, app.state.wersje, app.state.kadry)
        app.state.auth = auth
        app.state.dziennik = Dziennik(ust.baza)
        yield
        app.state.baza.close()

    app = FastAPI(title="Cyfrowy Asystent Ocen Okresowych", lifespan=cykl)
    # Role i uprawnienia (#10): konta demo z role.yaml, egzekwowane w API.
    auth = Autoryzacja(KonfiguracjaRol.model_validate(yaml.safe_load(ust.role.read_text(encoding="utf-8"))))
    app.include_router(auth.router(), prefix="/api", tags=["role (przełącznik)"])
    app.include_router(router_kadr(auth))
    app.include_router(router_cyklu(auth))
    app.include_router(router_oceny(auth))
    app.include_router(router_zmian(auth))
    app.include_router(router_rozmowy(KonfiguracjaRozmowy.z_pliku(ust.rozmowa), auth))
    app.include_router(router_importu(auth))
    app.include_router(router_pierwszej_oceny(KonfiguracjaPierwszejOceny.z_pliku(ust.pierwsza_ocena), auth))
    app.include_router(router_obiegu(auth))
    konf_mapowania = KonfiguracjaMapowania.z_pliku(ust.mapowanie)
    app.state.dostawcy_mapowania = konf_mapowania.dostawcy(KATALOG_APLIKACJI)
    app.include_router(router_mapowania(
        konf_mapowania, DziennikJsonl(ust.log_llm or ust.baza.with_name("llm.jsonl")), auth,
    ))
    app.include_router(router_rekomendacji(KonfiguracjaRekomendacji.z_pliku(ust.rekomendacje), auth))
    app.include_router(aktualizacja.router(
        aktualizacja.KonfiguracjaAktualizacji.z_pliku(ust.aktualizacja), auth,
        DziennikJsonl(ust.baza.parent / "llm-aktualizacja.jsonl"),
    ))
    konf_dowodow = KonfiguracjaDowodow.z_pliku(ust.dowody)
    app.state.dostawcy_dowodow = dostawcy_dowodow(konf_dowodow)
    app.include_router(router_dowodow(
        konf_dowodow, DziennikJsonl(ust.log_llm or ust.baza.with_name("llm.jsonl")), auth,
    ))

    def do_panelu(request: Request, r) -> RegulaPanelu:
        k = request.app.state.korpus
        adnotacje = []
        for ref in r.adnotacje:
            dok = k.dokumenty.get(ref.doc_id) if ref.doc_id else None
            adnotacje.append(
                AdnotacjaPanelu(
                    adnotacja=ref,
                    dokument=dok.nazwa if dok else None,
                    plik=dok.plik.name if dok else None,
                    cytat_w_korpusie=(
                        None if ref.cytat_doslowny is None else not waliduj_adnotacje(ref, k.korpus, r.id)
                    ),
                )
            )
        return RegulaPanelu(id=r.id, nazwa=r.nazwa, opis=r.opis, adnotacje=adnotacje)

    @app.get("/api/zdrowie")
    def zdrowie(request: Request) -> dict:
        k = request.app.state.korpus
        return {
            "wersja": "v1",
            "port": request.app.state.ustawienia.port,
            "dokumenty": len(k.dokumenty),
            "fragmenty": len(k.korpus.fragmenty),
            "wersje_regul": [w.wersja for w in request.app.state.wersje],
        }

    @app.get("/api/zrodla")
    def zrodla(request: Request) -> dict:
        k = request.app.state.korpus
        raport = {w.doc_id: w for w in k.korpus.raport}
        return {
            "dokumenty": [
                {
                    "doc_id": d.doc_id,
                    "nazwa": d.nazwa,
                    "plik": d.plik.name,
                    "kategoria": d.kategoria.value,
                    "status": raport[d.doc_id].status.value,
                    "fragmenty": len(raport[d.doc_id].fragmenty),
                    "strony_bez_tekstu": raport[d.doc_id].strony_bez_tekstu,
                    "komunikat": raport[d.doc_id].komunikat,
                }
                for d in k.dokumenty.values()
            ],
            "pominiete": [{"wzorzec": p.wzorzec, "powod": p.powod} for p in k.pominiete],
        }

    @app.get("/api/reguly")
    def reguly(request: Request) -> list[WersjaPanelu]:
        """Panel źródeł: wersje reguł, a w każdej regule kategoria, dokument i cytat."""
        return [
            WersjaPanelu(
                wersja=w.wersja,
                obowiazuje_od=w.obowiazuje_od,
                plik=w.plik,
                data_obowiazywania=do_panelu(request, w.adnotacje_wersji),
                reguly=[do_panelu(request, r) for r in w.reguly],
                rozbieznosci=[
                    RozbieznoscPanelu(
                        **do_panelu(request, RegulaV1.model_validate({k: v for k, v in r.items() if k != "rozbieżność"})).model_dump(),
                        rozbieznosc=r["rozbieżność"],
                    )
                    for r in w.rozbieznosci
                ],
            )
            for w in request.app.state.wersje
        ]

    @app.get("/api/pracownicy/{pracownik_id}/proces", tags=["wybór procesu"])
    def wybor_procesu(
        pracownik_id: str, request: Request, dzien: date | None = None, uz=auth.wymagaj("pracownicy.czytaj")
    ) -> WyborProcesu:
        """Rodzaj oceny (pierwsza / okresowa) i arkusz A/B/C z przesłankami i cytatami (#11)."""
        p = dostep_pracownik(request, uz, pracownik_id, "pracownicy.czytaj")
        w = wersja_na_dzien(request.app.state.wersje, dzien or date.today())
        wybor = wybierz(p, w)
        cykl = w_toku(request.app.state.baza, pracownik_id)
        if wybor.termin_oceny is not None and cykl and cykl.zawiadomienia and dzien is None:
            z = cykl.zawiadomienia[-1]
            wybor.termin_oceny.termin = z.nowy_termin
            wybor.termin_oceny.slad.append(KrokTerminu(
                regula="termin.zawiadomienie-8",
                opis=f"Uwzględniono wniosek. Termin zmieniono z {z.poprzedni_termin} na {z.nowy_termin}.",
                adnotacje=z.adnotacje,
            ))
        return wybor

    @app.get("/api/graf/katalog")
    def katalog_grafu(request: Request) -> KatalogGrafu:
        wersja = wersja_na_dzien(request.app.state.wersje, date.today())
        return request.app.state.katalogi_grafu[wersja.wersja]

    @app.post("/api/skala/oblicz")
    def oblicz(request: Request, body: ZapytanieSkali) -> dict:
        """Wynik według § 11 (silnik skali z caoo-core) dla wersji reguł z danego dnia."""
        try:
            w = wersja_na_dzien(request.app.state.wersje, body.dzien or date.today())
            wynik = oblicz_wynik(body.oceny, w.konfiguracja_skali())
        except (LookupError, ValueError) as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        return {
            "wersja_regul": w.wersja,
            "srednia": str(wynik.srednia),
            "poziom": wynik.poziom,
            "wynik": wynik.wynik,
            "regula_rozstrzygajaca": wynik.regula_rozstrzygajaca.id,
            "slad": [{"regula": k.regula.id, "opis": k.opis} for k in wynik.slad],
        }

    return app


app = utworz_aplikacje()
