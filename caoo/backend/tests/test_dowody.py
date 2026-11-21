"""Cytaty-dowody dla kryterium przez LLM – wersja 1 (#22, V1-15). Bez prawdziwego API."""

from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from caoo_core.llm import Cytat, Odpowiedz, Tryb, Zapytanie
from caoo_core.zrodla.cli import main as waliduj

from caoo.app import utworz_aplikacje
from caoo.dowody import WszystkieFragmenty

ODRZUCONA = "propozycja odrzucona — brak pokrycia w źródłach"
NOTATKA = "Pracownik samodzielnie rozdzielił zadania w zespole i dotrzymał terminów projektu."


class Atrapa:
    """Udaje model na żywo: zwraca podany cytat z pierwszego fragmentu."""

    nazwa, model, tryb = "atrapa-na-zywo", "atrapa", Tryb.NA_ZYWO

    def __init__(self, cytat: str | None = None) -> None:
        self.cytat = cytat

    def odpowiedz(self, z: Zapytanie) -> Odpowiedz:
        f = z.fragmenty[0]
        return Odpowiedz(cytaty=[Cytat(fragment_id=f.id, cytat=self.cytat or f.tekst)], wybor=[], tresc=None)


@pytest.fixture
def api(ustawienia, tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    ust = replace(ustawienia, baza=tmp_path / "d.db", log_llm=tmp_path / "llm.jsonl")
    with TestClient(utworz_aplikacje(ust)) as k:
        token = k.post("/api/auth/login", json={"login": "admin", "hasło": "demo"}).json()["token"]
        k.headers.update({"Authorization": f"Bearer {token}"})
        c = k.post("/api/pracownicy/K-002/cykle", json={"dodatkowe": []}).json()
        kid = c["obowiazkowe"][0]["id"]  # zarządzanie ludźmi
        ocena = k.get(f"/api/cykle/{c['id']}/ocena").json()
        r = k.put(f"/api/cykle/{c['id']}/ocena", json={
            "numer_wersji": ocena["numer_wersji"], "punkty": {kid: 5},
            "odpowiedzi": {kid: {"1": {"wartosc": "zwykle", "dowod": "Przygotował plan pracy wydziału na kwartał."}}},
        })
        assert r.status_code == 200, r.text
        n = k.post(f"/api/cykle/{c['id']}/rozmowa/notatki", json={"tresc": NOTATKA, "kryterium_id": kid})
        assert n.status_code == 201, n.text
        yield k, c["id"], kid


def _propozycje(k, cid, kid):
    r = k.post(f"/api/cykle/{cid}/dowody/{kid}/propozycje")
    assert r.status_code == 200, r.text
    return r.json()


def test_cytaty_sa_doslownie_w_notatkach_i_przykladach(api):
    k, cid, kid = api
    # bez klucza: Anthropic odpada, tryb ekstrakcyjny cytuje całe fragmenty
    r = _propozycje(k, cid, kid)
    assert r["tryb"] == "EKSTRAKCYJNE" and r["etykieta"] == "propozycja — wymaga zatwierdzenia"
    zrodla = {NOTATKA, "Przygotował plan pracy wydziału na kwartał."}
    assert r["propozycje"] and all(p["cytat"] in zrodla for p in r["propozycje"])
    assert {p["rodzaj"] for p in r["propozycje"]} <= {"notatka z rozmowy", "przykład z pytania pomocniczego"}
    # na żywo: dosłowny fragment notatki przechodzi
    k.app.state.dostawcy_dowodow = [Atrapa("samodzielnie rozdzielił zadania w zespole")]
    r = _propozycje(k, cid, kid)
    assert r["zrodlo_odpowiedzi"] == "na żywo" and r["propozycje"][0]["cytat"] == "samodzielnie rozdzielił zadania w zespole"
    assert r["odrzucone"] == []


def test_cytat_bez_pokrycia_odrzucony_i_pokazany(api):
    k, cid, kid = api
    k.app.state.dostawcy_dowodow = [Atrapa("Pracownik jest liderem całego departamentu."), WszystkieFragmenty()]
    r = _propozycje(k, cid, kid)
    assert [o["dostawca"] for o in r["odrzucone"]] == ["atrapa-na-zywo"]
    assert r["odrzucone"][0]["etykieta"] == ODRZUCONA and r["odrzucone"][0]["bledy"]
    assert r["tryb"] == "EKSTRAKCYJNE"  # zastąpiony trybem ekstrakcyjnym
    assert all("liderem" not in p["cytat"] for p in r["propozycje"])
    # tylko zmyślający dostawca – brak propozycji, sama odrzucona próba
    k.app.state.dostawcy_dowodow = [Atrapa("Zmyślony cytat.")]
    r = _propozycje(k, cid, kid)
    assert r["propozycje"] == [] and r["odrzucone"][0]["etykieta"] == ODRZUCONA


def test_dowody_nie_zmieniaja_punktacji(api):
    k, cid, kid = api
    przed = k.get(f"/api/cykle/{cid}/ocena").json()
    r = k.post(f"/api/cykle/{cid}/dowody/{kid}",
               json={"fragment_id": "notatka:1", "cytat": "dotrzymał terminów projektu", "osoba": "Marek Graniczny"})
    assert r.status_code == 201, r.text
    assert r.json()["zatwierdzil"] == "admin"  # z zalogowanego konta; „osoba” z żądania jest ignorowana
    po = k.get(f"/api/cykle/{cid}/ocena").json()
    assert [x["punkty"] for x in po["kryteria"]] == [x["punkty"] for x in przed["kryteria"]]
    assert po["wynik"] == przed["wynik"]
    assert [d["cytat"] for d in k.get(f"/api/cykle/{cid}/dowody").json()] == ["dotrzymał terminów projektu"]
    # dowód tylko z dosłownym cytatem
    zle = k.post(f"/api/cykle/{cid}/dowody/{kid}", json={"fragment_id": "notatka:1", "cytat": "nie ma tego", "osoba": "X"})
    assert zle.status_code == 422 and zle.json()["detail"] == ODRZUCONA


def test_brak_zrodel_i_bledy(api):
    k, cid, kid = api
    inne = next(x["id"] for x in k.get(f"/api/cykle/{cid}").json()["obowiazkowe"] if x["id"] != kid)
    r = _propozycje(k, cid, inne)  # notatka i przykład dotyczą innego kryterium
    assert r["propozycje"] == [] and r["komunikat"].startswith("Brak notatek i przykładów")
    assert k.post(f"/api/cykle/{cid}/dowody/nie-ma/propozycje").status_code == 404
    assert k.post("/api/cykle/999999/dowody/x/propozycje").status_code == 404


def test_uprawnienia(api):
    k, cid, kid = api
    def h(login):
        t = k.post("/api/auth/login", json={"login": login, "hasło": "demo"}).json()["token"]
        return {"Authorization": f"Bearer {t}"}
    url = f"/api/cykle/{cid}/dowody/{kid}/propozycje"
    assert k.post(url, headers=h("kadry")).status_code == 403        # notatki to narzędzie oceniającego
    assert k.post(url, headers=h("kierownik")).status_code == 200    # K-001 ocenia K-002
    assert k.get(f"/api/cykle/{cid}/dowody", headers=h("kadry")).status_code == 200


def test_adnotacja_reguly(ustawienia):
    assert waliduj(["--manifest", str(ustawienia.manifest), str(ustawienia.dowody)]) == 0
