"""Mapowanie opisu stanowiska do katalogu przez LLM (#21, V1-14).

Bez prawdziwego API: „na żywo” udaje atrapa ``LlmProvider``; domyślny łańcuch bez klucza
przechodzi do odpowiedzi zapisanych.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from caoo_core.llm import Cytat, Odpowiedz, Tryb, Zapisane, Zapytanie
from caoo_core.zrodla.cli import main as waliduj

from caoo.app import utworz_aplikacje
from caoo.ustawienia import KATALOG_APLIKACJI


class AtrapaNaZywo:
    """Udaje model na żywo: zwraca podane ID (także spoza katalogu) z cytatem elementu."""

    nazwa = "atrapa-na-zywo"
    model = "atrapa"
    tryb = Tryb.NA_ZYWO

    def __init__(self, wybor: list[str]) -> None:
        self.wybor = wybor

    def odpowiedz(self, z: Zapytanie) -> Odpowiedz:
        f = z.fragmenty[0]
        return Odpowiedz(cytaty=[Cytat(fragment_id=f.id, cytat=f.tekst)], wybor=self.wybor, tresc=None)


@pytest.fixture
def api(ustawienia, tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)  # nigdy prawdziwe API w testach
    ust = replace(ustawienia, baza=tmp_path / "m.db", log_llm=tmp_path / "llm.jsonl")
    with TestClient(utworz_aplikacje(ust)) as k:
        token = k.post("/api/auth/login", json={"login": "admin", "hasło": "demo"}).json()["token"]
        k.headers.update({"Authorization": f"Bearer {token}"})  # API wymaga roli (#10)
        yield k, tmp_path / "llm.jsonl"


def _mapowanie(k, pid="K-002"):
    r = k.get(f"/api/pracownicy/{pid}/mapowanie")
    assert r.status_code == 200, r.text
    return {e["element"]["id"]: e for e in r.json()["elementy"]}


def test_domyslnie_odpowiedzi_zapisane_ze_znacznikami(api):
    k, _ = api
    m = _mapowanie(k)
    assert set(m) == {"zadanie:1", "zadanie:2", "zadanie:3", "wymaganie:1", "wymaganie:2"}
    z1 = m["zadanie:1"]
    assert set(z1["propozycja"]) == {"organizacja-pracy", "zarzadzanie-ludzmi"}
    assert z1["etykieta"] == "propozycja — wymaga zatwierdzenia"
    assert z1["zrodlo_odpowiedzi"] == "zapisane" and z1["tryb"] == "ZAPISANE"
    assert z1["odrzuceni"] == ["anthropic"]  # bez klucza API
    assert z1["status"] == "PROPOZYCJA" and z1["kompetencje"] == []
    # wymaganie formalne – bez kompetencji
    assert m["wymaganie:1"]["propozycja"] == [] and m["wymaganie:1"]["etykieta"] == "nieprzypisane — wymaga weryfikacji"


def test_na_zywo_ma_znacznik_na_zywo(api):
    k, _ = api
    k.app.state.dostawcy_mapowania = [AtrapaNaZywo(["komunikacja"])]
    m = _mapowanie(k, "K-003")
    assert all(e["zrodlo_odpowiedzi"] == "na żywo" and e["tryb"] == "NA_ŻYWO" for e in m.values())
    assert all(e["propozycja"] == ["komunikacja"] for e in m.values())


def test_id_spoza_katalogu_to_nieprzypisane(api):
    k, _ = api
    # Sama atrapa z ID spoza katalogu: core odrzuca odpowiedź, innego dostawcy nie ma
    k.app.state.dostawcy_mapowania = [AtrapaNaZywo(["kompetencja-zmyslona"])]
    m = _mapowanie(k, "K-004")
    for e in m.values():
        assert e["propozycja"] == [] and e["etykieta"] == "nieprzypisane — wymaga weryfikacji"
        assert e["zrodlo_odpowiedzi"] is None and e["odrzuceni"] == ["atrapa-na-zywo"]


def test_id_spoza_katalogu_przechodzi_do_zapisanych(api):
    k, log = api
    k.app.state.dostawcy_mapowania = [AtrapaNaZywo(["zarzadzanie-ludzmi", "spoza"]),
                                      Zapisane(KATALOG_APLIKACJI / "konfiguracja" / "mapowanie-zapisane.json")]
    m = _mapowanie(k, "K-005")
    e = m["zadanie:3"]  # Brakowanie dokumentacji
    assert e["propozycja"] == ["rzetelnosc"] and e["zrodlo_odpowiedzi"] == "zapisane"
    assert e["odrzuceni"] == ["atrapa-na-zywo"] and "spoza" not in e["propozycja"]
    # log: każda próba z modelem, wersją promptu, fragmentami i wynikiem walidacji
    wpisy = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
    odrzucony = next(w for w in wpisy if w["dostawca"] == "atrapa-na-zywo")
    assert not odrzucony["walidacja_ok"] and any("spoza katalogu" in b for b in odrzucony["bledy"])
    assert odrzucony["prompt_wersja"] == "mapowanie-osp-v1" and odrzucony["fragment_ids"]


def test_zatwierdzenie_zapisuje_osobe_i_date_oraz_historie(api):
    k, _ = api
    _mapowanie(k)
    url = "/api/pracownicy/K-002/mapowanie"
    r = k.post(f"{url}/zadanie/3/decyzja", json={"decyzja": "ZATWIERDZ", "osoba": "Ewa Rozwojowa", "data": "2026-11-21"})
    assert r.status_code == 200, r.text
    e = r.json()
    assert e["status"] == "ZATWIERDZONE" and e["kompetencje"] == ["zarzadzanie-ludzmi"]
    assert e["zatwierdzil"] == "admin" and e["data_zatwierdzenia"] == "2026-11-21"
    assert e["etykieta"].startswith("zatwierdzone – admin")
    # korekta: tylko ID z katalogu
    zle = k.post(f"{url}/zadanie/1/decyzja", json={"decyzja": "KORYGUJ", "kompetencje": ["zmyslona"], "osoba": "E"})
    assert zle.status_code == 422
    ok = k.post(f"{url}/zadanie/1/decyzja",
                json={"decyzja": "KORYGUJ", "kompetencje": ["zarzadzanie-ludzmi"], "osoba": "Ewa Rozwojowa"}).json()
    assert ok["status"] == "SKORYGOWANE" and ok["kompetencje"] == ["zarzadzanie-ludzmi"]
    assert set(ok["historia"][-1]["przed"]) == {"organizacja-pracy", "zarzadzanie-ludzmi"}
    assert ok["historia"][-1]["po"] == ["zarzadzanie-ludzmi"] and ok["historia"][-1]["osoba"] == "admin"
    # odrzucenie i brak propozycji do zatwierdzenia
    odrz = k.post(f"{url}/zadanie/2/decyzja", json={"decyzja": "ODRZUC", "osoba": "Ewa Rozwojowa"}).json()
    assert odrz["status"] == "ODRZUCONE" and odrz["kompetencje"] == []
    assert k.post(f"{url}/wymaganie/1/decyzja", json={"decyzja": "ZATWIERDZ", "osoba": "E"}).status_code == 409
    # odświeżenie nie nadpisuje decyzji
    k.app.state.dostawcy_mapowania = [AtrapaNaZywo(["komunikacja"])]
    po = {e["element"]["id"]: e for e in k.post(f"{url}/odswiez").json()["elementy"]}
    assert po["zadanie:3"]["status"] == "ZATWIERDZONE" and po["zadanie:3"]["kompetencje"] == ["zarzadzanie-ludzmi"]
    assert po["wymaganie:2"]["propozycja"] == ["komunikacja"]  # bez decyzji – nowa propozycja


def test_bledy(api):
    k, _ = api
    assert k.get("/api/pracownicy/K-999/mapowanie").status_code == 404
    assert k.post("/api/pracownicy/K-002/mapowanie/zadanie/99/decyzja",
                  json={"decyzja": "ODRZUC", "osoba": "E"}).status_code == 404


def test_odpowiedzi_zapisane_zgodne_ze_zrodlem(ustawienia):
    """Po zmianie mapowanie-demo.yaml: uv run python -m caoo.mapowanie."""
    from caoo.graf import zbuduj_katalog
    from caoo.graf_model import wczytaj_konfiguracje_grafu
    from caoo.kadry import MockKadry
    from caoo.mapowanie import KonfiguracjaMapowania, odpowiedzi_demo
    from caoo.reguly import wczytaj_wersje

    (w, *_) = wczytaj_wersje(ustawienia.reguly)
    kat = zbuduj_katalog(w, wczytaj_konfiguracje_grafu(ustawienia.graf))
    oczekiwane = odpowiedzi_demo(
        MockKadry.z_pliku(ustawienia.kadry), {x.id.split(":", 1)[1]: x.nazwa for x in kat.kompetencje},
        KonfiguracjaMapowania.z_pliku(ustawienia.mapowanie), KATALOG_APLIKACJI / "konfiguracja" / "mapowanie-demo.yaml",
    )
    zapisane = json.loads((KATALOG_APLIKACJI / "konfiguracja" / "mapowanie-zapisane.json").read_text(encoding="utf-8"))
    assert zapisane == json.loads(json.dumps(oczekiwane))


def test_adnotacja_reguly_mapowania(ustawienia):
    assert waliduj(["--manifest", str(ustawienia.manifest), str(ustawienia.mapowanie)]) == 0


def test_uprawnienia(api):
    """#10: propozycje widzi osoba z dostępem; decyzje podejmuje oceniający."""
    k, _ = api
    def h(login):
        t = k.post("/api/auth/login", json={"login": login, "hasło": "demo"}).json()["token"]
        return {"Authorization": f"Bearer {t}"}
    url = "/api/pracownicy/K-004/mapowanie"
    assert k.get(url, headers={"Authorization": ""}).status_code == 401
    assert k.get(url, headers=h("oceniany")).status_code == 200      # własny opis stanowiska
    assert k.get("/api/pracownicy/K-006/mapowanie", headers=h("oceniany")).status_code == 403
    decyzja = {"decyzja": "ODRZUC", "osoba": "x"}
    assert k.post(f"{url}/zadanie/1/decyzja", json=decyzja, headers=h("oceniany")).status_code == 403
    assert k.post(f"{url}/zadanie/1/decyzja", json=decyzja, headers=h("kadry")).status_code == 403
    assert k.post(f"{url}/zadanie/1/decyzja", json=decyzja, headers=h("oceniajacy")).status_code == 200
