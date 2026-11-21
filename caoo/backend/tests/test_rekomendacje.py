"""Rekomendacje i plan rozwoju (#23, V1-16)."""

from __future__ import annotations

import re
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from caoo_core.zrodla.cli import main as waliduj

from caoo.app import utworz_aplikacje

OZNACZENIA = {"z danych", "z reguły", "propozycja"}


@pytest.fixture(scope="module")
def api(ustawienia, tmp_path_factory):
    ust = replace(ustawienia, baza=tmp_path_factory.mktemp("rekomendacje") / "r.db")
    with TestClient(utworz_aplikacje(ust)) as k:
        token = k.post("/api/auth/login", json={"login": "admin", "hasło": "demo"}).json()["token"]
        k.headers.update({"Authorization": f"Bearer {token}"})  # API wymaga roli (#10)
        c = k.post("/api/pracownicy/K-002/cykle", json={"dodatkowe": []})
        assert c.status_code == 201, c.text
        cykl = c.json()
        ids = [x["id"] for x in cykl["obowiazkowe"]]  # zarządzanie ludźmi, decyzje, organizacja, …
        wersja = k.get(f"/api/cykle/{cykl['id']}/ocena").json()["numer_wersji"]  # #17: zapis z numerem wersji
        r = k.put(f"/api/cykle/{cykl['id']}/ocena",
                  json={"punkty": {ids[0]: 1, ids[1]: 3, ids[2]: 9}, "numer_wersji": wersja})
        assert r.status_code == 200, r.text
        yield k, cykl["id"], ids


def _rek(api):
    k, cid, _ = api
    r = k.get(f"/api/cykle/{cid}/rekomendacje")
    assert r.status_code == 200, r.text
    return r.json()


def test_rekomendacje_z_luk_z_kontekstem(api):
    r = _rek(api)
    zl = next(x for x in r["rekomendacje"] if x["id"] == "rek:zarzadzanie-ludzmi")
    assert zl["luka"] == 4
    assert zl["poziom_oczekiwany"]["wartosc"] == 5 and zl["poziom_zaobserwowany"]["wartosc"] == 1
    assert [z["nazwa"] for z in zl["zadania"]] == ["Ocena pracowników wydziału"]
    assert [d["nazwa"] for d in zl["dowody"]] == ["Warsztaty z delegowania zadań"]
    assert {d["nazwa"] for d in zl["dzialania"]} == {"Coaching z przełożonym", "Warsztaty"}
    assert all(d["adnotacje"] for d in zl["dzialania"])  # działania z katalogu mają źródło
    # luka 2 → łagodniejsze działania
    pd = next(x for x in r["rekomendacje"] if x["id"] == "rek:podejmowanie-decyzji")
    assert pd["luka"] == 2 and {d["nazwa"] for d in pd["dzialania"]} == {"Szkolenie", "Samokształcenie"}
    # bez luki – bez rekomendacji
    assert not any(x["id"] == "rek:organizacja-pracy" for x in r["rekomendacje"])


def test_kazdy_element_ma_oznaczenie_z_danych_z_reguly_propozycja(api):
    r = _rek(api)
    for x in r["rekomendacje"]:
        assert x["oznaczenie"] == "propozycja"
        elementy = [x["kompetencja"], x["poziom_oczekiwany"], x["poziom_zaobserwowany"],
                    *x["zadania"], *x["dowody"], *x["dzialania"]]
        assert all(e["oznaczenie"] in OZNACZENIA for e in elementy)
        # #20: poziom zaobserwowany jest „z danych” dopiero po zatwierdzeniu oceny; ta jest robocza
        assert x["poziom_zaobserwowany"]["oznaczenie"] == "propozycja"
        assert x["poziom_oczekiwany"]["oznaczenie"] == "propozycja"  # poziom roboczy z konfiguracji
        assert all(d["oznaczenie"] == "z reguły" for d in x["dzialania"])


def test_mocne_strony_i_realizacja_zalecen(api):
    r = _rek(api)
    assert [m["kompetencja"]["nazwa"] for m in r["mocne_strony"]] == ["Organizacja pracy i orientacja na osiąganie celów"]
    zal = {z["zalecenie"]["nazwa"]: z for z in r["realizacja_zalecen"]}
    assert set(zal) == {"Warsztaty z delegowania zadań", "Coaching z bezpośrednim przełożonym"}
    assert zal["Warsztaty z delegowania zadań"]["kompetencje"] == ["Zarządzanie ludźmi"]
    assert zal["Warsztaty z delegowania zadań"]["biezace_punkty"] == {"Zarządzanie ludźmi": 1}
    assert all(z["status"] == "DO_OMOWIENIA" for z in zal.values())  # bez automatycznego werdyktu


def test_ostrzezenie_przy_porownaniu_okresow(api):
    p = _rek(api)["porownanie"]
    assert p["poprzednia_srednia"] == "5.40" and p["biezaca_srednia"] == "4.33"
    assert p["ostrzezenie"].startswith("Porównanie orientacyjne") and "kryteri" in p["ostrzezenie"]


def test_tresc_iprz_pisze_czlowiek(api):
    k, cid, ids = api
    r = _rek(api)
    assert r["tresc_wnioskow_iprz"] is None and r["struktura_wnioskow_iprz"]
    assert k.get(f"/api/cykle/{cid}/plan-rozwoju").json() == {"pozycje": [], "wnioski_iprz": None}
    plan = {
        "pozycje": [{"kompetencja_id": "zarzadzanie-ludzmi", "dzialanie_id": "coaching", "termin": "2027-03-31",
                     "osoba_wspierajaca": "Marek Graniczny", "miernik": "Delegowanie 3 zadań miesięcznie"}],
        "wnioski_iprz": "Rozwijać delegowanie zadań we współpracy z kierownikiem urzędu.",
    }
    r = k.put(f"/api/cykle/{cid}/plan-rozwoju", json=plan)
    assert r.status_code == 200, r.text
    assert k.get(f"/api/cykle/{cid}/plan-rozwoju").json() == plan
    zle = {**plan, "pozycje": [{**plan["pozycje"][0], "dzialanie_id": "nie-ma"}]}
    assert k.put(f"/api/cykle/{cid}/plan-rozwoju", json=zle).status_code == 422
    bez_miernika = {"pozycje": [{**plan["pozycje"][0], "miernik": ""}]}
    assert k.put(f"/api/cykle/{cid}/plan-rozwoju", json=bez_miernika).status_code == 422


def test_niski_wynik_bez_wnioskow_o_przeciazeniu(api):
    r = _rek(api)
    teksty = []
    for x in r["rekomendacje"]:
        teksty += [d["nazwa"] for d in x["dzialania"]] + [x["kompetencja"]["nazwa"]]
        assert x["pytanie_do_rozmowy"].rstrip().endswith("?")  # tylko pytanie
    teksty += [m["propozycja"]["nazwa"] for m in r["mocne_strony"]]
    assert not any(re.search(r"przeciąż|obciąż", t, re.I) for t in teksty)


def test_bledy_i_cytaty(api, ustawienia):
    k, _, _ = api
    assert k.get("/api/cykle/999999/rekomendacje").status_code == 404
    konf = ustawienia.manifest.parent
    assert waliduj(["--manifest", str(ustawienia.manifest), str(konf / "rekomendacje.yaml")]) == 0


def test_uprawnienia(api):
    """#10: odczyt dla osób z dostępem, plan zapisuje tylko oceniający."""
    k, cid, _ = api
    def h(login):
        t = k.post("/api/auth/login", json={"login": login, "hasło": "demo"}).json()["token"]
        return {"Authorization": f"Bearer {t}"}
    url = f"/api/cykle/{cid}/rekomendacje"
    assert k.get(url, headers={"Authorization": ""}).status_code == 401
    assert k.get(url, headers=h("kierownik")).status_code == 200   # K-001 ocenia K-002
    assert k.get(url, headers=h("oceniajacy")).status_code == 403  # K-002 nie widzi siebie jako ocenianego
    assert k.get(url, headers=h("kadry")).status_code == 200
    plan = {"pozycje": [], "wnioski_iprz": "x"}
    assert k.put(f"/api/cykle/{cid}/plan-rozwoju", json=plan, headers=h("kadry")).status_code == 403
    assert k.put(f"/api/cykle/{cid}/plan-rozwoju", json=plan, headers=h("kierownik")).status_code == 200
