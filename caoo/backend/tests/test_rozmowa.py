"""Pytania do rozmowy oceniającej i notatki (#24, V1-17)."""

from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from caoo_core.zrodla.cli import main as waliduj

from caoo.app import utworz_aplikacje

ETYKIETA = "funkcja aplikacji — nie wymóg rozporządzenia"


@pytest.fixture(scope="module")
def klient(ustawienia, tmp_path_factory):
    """Własna baza: inne testy zatwierdzają cykle tych samych osób."""
    ust = replace(ustawienia, baza=tmp_path_factory.mktemp("rozmowa") / "rozmowa.db")
    with TestClient(utworz_aplikacje(ust)) as k:
        r = k.post("/api/auth/login", json={"login": "admin", "hasło": "demo"})
        k.headers.update({"Authorization": f"Bearer {r.json()['token']}"})
        yield k


@pytest.fixture(scope="module")
def cykl(klient):
    """K-002: naczelnik z zaleceniami z poprzedniej oceny."""
    r = klient.post("/api/pracownicy/K-002/cykle", json={"dodatkowe": []})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _rozmowa(klient, cid):
    r = klient.get(f"/api/cykle/{cid}/rozmowa")
    assert r.status_code == 200, r.text
    return r.json()


def test_propozycje_z_regul_maja_uzasadnienie_i_powiazanie(klient, cykl):
    r = _rozmowa(klient, cykl)
    assert r["rodzaj"] == "PYTANIA_DO_ROZMOWY"
    prop = r["propozycje"]
    assert prop and all(p["rodzaj"] == "PYTANIE_DO_ROZMOWY" for p in prop)
    for p in prop:
        assert p["uzasadnienie"].strip() and p["powiazanie"]["typ"] and p["adnotacje"], p
    typy = {p["powiazanie"]["typ"] for p in prop}
    assert {"przepis", "zadanie", "wymaganie", "poprzednia_ocena"} <= typy
    zadania = [p for p in prop if p["powiazanie"]["typ"] == "zadanie"]
    assert len(zadania) == 3 and "Planowanie i rozdział zadań w wydziale" in zadania[0]["tresc"]
    zalecenia = {p["powiazanie"]["opis"] for p in prop if p["powiazanie"]["typ"] == "poprzednia_ocena"}
    assert zalecenia == {"Warsztaty z delegowania zadań", "Coaching z bezpośrednim przełożonym"}


def test_etykieta_funkcji_aplikacji_a_zakres_z_par_10(klient, cykl):
    prop = _rozmowa(klient, cykl)["propozycje"]
    przepis = [p for p in prop if p["powiazanie"]["typ"] == "przepis"]
    assert [p["powiazanie"]["opis"] for p in przepis] == ["§ 10 pkt 1", "§ 10 pkt 2", "§ 10 pkt 3"]
    assert all(p["etykieta"] is None and p["adnotacje"][0]["kategoria"] == "PRZEPIS" for p in przepis)
    funkcje = [p for p in prop if p["powiazanie"]["typ"] != "przepis"]
    assert funkcje and all(p["etykieta"] == ETYKIETA for p in funkcje)
    assert all(p["adnotacje"][0]["kategoria"] == "FUNKCJA_APLIKACJI" for p in funkcje)


def test_niska_i_wysoka_ocena_uruchamiaja_pytania(klient, cykl):
    ids = [k["id"] for k in klient.get(f"/api/cykle/{cykl}/ocena").json()["kryteria"]]
    assert klient.put(f"/api/cykle/{cykl}/ocena", json={"punkty": {ids[0]: 1, ids[1]: 9}}).status_code == 200
    prop = _rozmowa(klient, cykl)["propozycje"]
    k0 = [p for p in prop if p["powiazanie"].get("kryterium_id") == ids[0]]
    k1 = [p for p in prop if p["powiazanie"].get("kryterium_id") == ids[1]]
    assert {p["szablon"] for p in k0} == {"pyt.niska.trudnosci", "pyt.niska.wsparcie"}
    assert {p["szablon"] for p in k1} == {"pyt.wysoka"}
    # Historyjka 68: obciążenie i wsparcie jako pytanie, nie wniosek
    wsparcie = next(p for p in k0 if p["szablon"] == "pyt.niska.wsparcie")
    assert wsparcie["tresc"].rstrip().endswith("?") and "Czy obciążenie" in wsparcie["tresc"]


def test_wybor_edycja_i_wlasne_pytania(klient, cykl):
    prop = _rozmowa(klient, cykl)["propozycje"]
    pierwsza = prop[0]
    body = {"pytania": [
        {"id": "p1", "z_propozycji": pierwsza["id"], "tresc": pierwsza["tresc"] + " (zmienione)"},
        {"id": "w1", "tresc": "Jak ocenia Pan współpracę z innymi wydziałami?"},
    ]}
    r = klient.put(f"/api/cykle/{cykl}/rozmowa/pytania", json=body)
    assert r.status_code == 200, r.text
    assert [q["id"] for q in r.json()["pytania"]] == ["p1", "w1"]
    assert r.json()["pytania"][0]["tresc"].endswith("(zmienione)") and r.json()["pytania"][1]["z_propozycji"] is None
    assert _rozmowa(klient, cykl)["pytania"] == r.json()["pytania"]

    zle = klient.put(f"/api/cykle/{cykl}/rozmowa/pytania",
                     json={"pytania": [{"id": "x", "z_propozycji": "nie-ma", "tresc": "?"}]})
    assert zle.status_code == 422
    dubel = klient.put(f"/api/cykle/{cykl}/rozmowa/pytania",
                       json={"pytania": [{"id": "a", "tresc": "1"}, {"id": "a", "tresc": "2"}]})
    assert dubel.status_code == 422


def test_notatki_nie_zmieniaja_punktacji(klient, cykl):
    przed = klient.get(f"/api/cykle/{cykl}/ocena").json()
    kid = przed["kryteria"][0]["id"]
    r = klient.post(f"/api/cykle/{cykl}/rozmowa/notatki",
                    json={"tresc": "Pracownik wskazał brak wsparcia w Q3 – dowód do kryterium.", "kryterium_id": kid})
    assert r.status_code == 201, r.text
    assert r.json()["kryterium_id"] == kid and r.json()["zapisano"]
    po = klient.get(f"/api/cykle/{cykl}/ocena").json()
    assert [k["punkty"] for k in po["kryteria"]] == [k["punkty"] for k in przed["kryteria"]]
    assert po["wynik"] == przed["wynik"]
    notatki = _rozmowa(klient, cykl)["notatki"]
    assert notatki[-1]["tresc"].startswith("Pracownik wskazał")
    assert "nie zmieniają punktacji" in _rozmowa(klient, cykl)["zasada_notatek"]


def test_pytania_do_rozmowy_oddzielone_od_oceny(klient, cykl):
    ocena = klient.get(f"/api/cykle/{cykl}/ocena").json()
    assert "pytania" not in ocena and "propozycje" not in ocena and "notatki" not in ocena


def test_bledy(klient, cykl):
    assert klient.get("/api/cykle/999999/rozmowa").status_code == 404
    r = klient.post(f"/api/cykle/{cykl}/rozmowa/notatki", json={"tresc": "x", "kryterium_id": "nie-ma"})
    assert r.status_code == 422


def test_cytaty_konfiguracji_rozmowy_sa_w_korpusie(ustawienia):
    konf = ustawienia.manifest.parent
    assert waliduj(["--manifest", str(ustawienia.manifest), str(konf / "rozmowa.yaml")]) == 0
