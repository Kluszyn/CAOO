"""Aktualizacja przepisów v1 → v2 i odtworzenie oceny (#25, V1-18)."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from caoo_core.llm import DziennikPamiec, Odpowiedz, Tryb
from caoo_core.skala import oblicz_wynik

from caoo import aktualizacja as akt
from caoo.app import utworz_aplikacje
from caoo.korpus_aplikacji import zaladuj
from caoo.reguly import wczytaj_wersje
from caoo.ustawienia import KATALOG_APLIKACJI, Ustawienia

UST = Ustawienia.ze_srodowiska()
KONF = akt.KonfiguracjaAktualizacji.z_pliku(UST.aktualizacja)
(V1,) = wczytaj_wersje(UST.reguly)
TEKST_DEMO = KONF.tekst_aktu_demo()


@pytest.fixture(scope="module")
def korpus():
    return zaladuj(UST.manifest).korpus


@pytest.fixture(scope="module")
def analiza(korpus):
    return akt.analizuj(akt.fragmenty_aktu(TEKST_DEMO), V1, korpus, KONF, KONF.dostawcy(zywe=False), DziennikPamiec())


# --- analiza aktu (czysta funkcja) --------------------------------------------------


def test_fragmenty_aktu_maja_stabilne_id_z_tresci():
    a, b = akt.fragmenty_aktu(TEKST_DEMO), akt.fragmenty_aktu(TEKST_DEMO)
    assert [f.id for f in a] == [f.id for f in b]
    assert all(f.doc_id == akt.doc_id_aktu(TEKST_DEMO) for f in a)


def test_llm_wskazuje_zmienione_parametry_z_cytatami_z_obu_wersji(analiza):
    p = {x.parametr: x for x in analiza.propozycje}
    g = p["granica-pozytywnej"]
    assert g.zmienia and g.tryb == Tryb.ZAPISANE
    assert (g.wartosc_przed, g.wartosc_po) == (Decimal("4"), Decimal("4.5"))
    assert g.cytaty_aktu and g.cytaty_przed
    assert all(c.doc_id == "rozp-oceny-okresowe" for c in g.cytaty_przed)
    limit = p["limit-dodatkowych"]
    assert (limit.wartosc_przed, limit.wartosc_po) == (Decimal("3"), Decimal("2"))
    assert all(x.etykieta == KONF.etykiety["propozycja"] for x in analiza.propozycje)


def test_przepis_bez_parametru_ma_flage_wymaga_zmiany_aplikacji(analiza):
    (n,) = analiza.nieobslugiwane
    assert "§ 10a" in n.tekst and n.flaga == "wymaga zmiany aplikacji"


def test_bez_poprawnej_odpowiedzi_llm_propozycja_do_recznej_analizy(korpus):
    class Zmyslajacy:
        nazwa, model, tryb = "atrapa", None, Tryb.NA_ZYWO

        def odpowiedz(self, z):
            return Odpowiedz.model_validate({"cytaty": [{"fragment_id": z.fragmenty[0].id, "cytat": "zmyślony cytat"}]})

    a = akt.analizuj(akt.fragmenty_aktu(TEKST_DEMO), V1, korpus, KONF, [Zmyslajacy()], DziennikPamiec())
    assert all(p.zmienia is None and p.etykieta == KONF.etykiety["brak_odpowiedzi"] for p in a.propozycje)
    assert a.nieobslugiwane and all(n.flaga == KONF.etykiety["do_sprawdzenia"] for n in a.nieobslugiwane)


def test_niejasnosc_gdy_brak_cytatu_z_jednej_wersji(korpus):
    class TylkoAkt:
        nazwa, model, tryb = "atrapa", None, Tryb.NA_ZYWO

        def odpowiedz(self, z):
            f = next(f for f in z.fragmenty if "kryteria dodatkowe" in f.tekst and f.doc_id.startswith("akt-"))
            return Odpowiedz.model_validate({"cytaty": [{"fragment_id": f.id, "cytat": f.tekst}], "wybor": z.katalog})

    a = akt.analizuj(akt.fragmenty_aktu(TEKST_DEMO), V1, korpus, KONF, [TylkoAkt()], DziennikPamiec())
    limit = next(p for p in a.propozycje if p.parametr == "limit-dodatkowych")
    assert any("obecnych przepisów" in n for n in limit.niejasnosci)


# --- czy wynik oceny w toku może zależeć od wersji ---------------------------------


@pytest.mark.parametrize("znane, brakujace, moze", [
    ([], 5, True),                 # bez punktów: średnia może wypaść między 4 a 4,5
    ([1, 9], 3, True),             # negatywna według obu, ale poziom (4,2) może być inny – trafia do arkusza
    ([1, 1, 1, 1], 1, False),      # średnia ≤ 2,6 – ten sam wynik i poziom według obu wersji
    ([9, 9, 9, 9], 1, False),      # średnia co najmniej 7,4
    ([5, 5, 3, 3], 1, True),       # 5 pkt ⇒ 4,2 – wynik zależy od wersji
    ([5, 5, 5, 3, 5], 0, False),   # komplet 4,6 – ten sam wynik
])
def test_czy_wynik_moze_zalezec_od_wersji(analiza, znane, brakujace, moze):
    v2 = akt.zastosuj(V1, analiza, {"granica-pozytywnej": Decimal("4.5")}, wersja="v2", obowiazuje_od=date(2027, 1, 1), konf=KONF)
    assert akt.wynik_moze_zalezec(znane, brakujace, V1, v2) is moze


# --- nowa wersja reguł (czysta funkcja) ---------------------------------------------


def test_zastosuj_tworzy_v2_bez_zmiany_v1(analiza):
    przed = V1.model_dump()
    v2 = akt.zastosuj(V1, analiza, {"granica-pozytywnej": Decimal("4.5"), "limit-dodatkowych": Decimal("2")},
                      wersja="przepisy-demo-v2", obowiazuje_od=date(2027, 1, 1), konf=KONF)
    assert V1.model_dump() == przed
    assert (v2.wersja, v2.obowiazuje_od, v2.kryteria["limit_dodatkowych"]) == ("przepisy-demo-v2", date(2027, 1, 1), 2)
    # średnia 4,2: według v1 pozytywna, według v2 negatywna (poniżej oczekiwań)
    oceny = [5, 5, 5, 3, 3]
    assert oblicz_wynik(oceny, V1.konfiguracja_skali()).wynik == "POZYTYWNA"
    w2 = oblicz_wynik(oceny, v2.konfiguracja_skali())
    assert (w2.wynik, w2.poziom) == ("NEGATYWNA", "poniżej oczekiwań")
    # zmienione reguły cytują akt
    assert any(a.doc_id == akt.doc_id_aktu(TEKST_DEMO) for a in v2.regula("skala.progi").adnotacje)
    assert any(a.doc_id == akt.doc_id_aktu(TEKST_DEMO) for a in v2.regula("kryteria.limit-dodatkowych").adnotacje)


def test_zapisane_odpowiedzi_sa_aktualne(korpus):
    zapisane = json.loads((KATALOG_APLIKACJI / "konfiguracja/aktualizacja/zapisane.json").read_text(encoding="utf-8"))
    assert zapisane == akt.odpowiedzi_demo(V1, korpus, KONF)


# --- API: kryteria akceptacji -------------------------------------------------------


@pytest.fixture(scope="module")
def api(ustawienia, tmp_path_factory):
    ust = replace(ustawienia, baza=tmp_path_factory.mktemp("aktualizacja") / "akt.db")
    klucz = os.environ.pop("ANTHROPIC_API_KEY", None)  # bez wywołań na żywo – odpowiedzi zapisane
    with TestClient(utworz_aplikacje(ust)) as k:
        r = k.post("/api/auth/login", json={"login": "admin", "hasło": "demo"})
        k.headers.update({"Authorization": f"Bearer {r.json()['token']}"})
        yield k
    if klucz is not None:
        os.environ["ANTHROPIC_API_KEY"] = klucz


def _ocena_zakonczona_i_w_toku(k):
    """K-003: zakończona ocena 4,2 (pozytywna według v1); K-004: ocena w toku 4,2."""
    wyniki = {}
    for pid, zatwierdz in (("K-003", True), ("K-004", False)):
        c = k.post(f"/api/pracownicy/{pid}/cykle", json={})
        assert c.status_code == 201, c.text
        cid = c.json()["id"]
        stan = k.get(f"/api/cykle/{cid}/ocena").json()
        punkty = dict(zip((x["id"] for x in stan["kryteria"]), [5, 5, 5, 3, 3]))
        body = {"punkty": punkty, "numer_wersji": 1}
        if zatwierdz:
            body |= {"zatwierdz": True, "data_sporzadzenia": date.today().isoformat()}
            assert k.post(f"/api/cykle/{cid}/potwierdzenie", json={"data": date.today().isoformat()}).status_code == 200
        r = k.put(f"/api/cykle/{cid}/ocena", json=body)
        assert r.status_code == 200, r.text
        wyniki[pid] = (cid, r.json()["wynik"])
    return wyniki


def test_kryteria_akceptacji(api):
    k = api
    reguly_przed = k.get("/api/reguly").json()
    oceny = _ocena_zakonczona_i_w_toku(k)
    (zakonczona, wynik_przed) = oceny["K-003"]
    assert wynik_przed["wynik"] == "POZYTYWNA"

    # 1. Samo wgranie dokumentu nie zmienia aktywnych reguł
    r = k.post("/api/przepisy/akty", json={"nazwa": "Akt demo", "tekst": k.get("/api/przepisy/akt-demo").json()["tekst"]})
    assert r.status_code == 201, r.text
    aid = r.json()["id"]
    assert k.get("/api/reguly").json() == reguly_przed
    a = k.post(f"/api/przepisy/akty/{aid}/analiza").json()
    assert {p["parametr"] for p in a["analiza"]["propozycje"] if p["zmienia"]} == {"granica-pozytywnej", "limit-dodatkowych"}
    assert a["analiza"]["data_wejscia"]["data"] == "2027-01-01" and "wchodzi w życie" in a["analiza"]["data_wejscia"]["cytat"]
    assert k.get("/api/reguly").json() == reguly_przed

    for p in ("granica-pozytywnej", "limit-dodatkowych"):
        assert k.put(f"/api/przepisy/akty/{aid}/propozycje/{p}", json={"przyjeta": True}).status_code == 200
    z = k.get(f"/api/przepisy/akty/{aid}/zestawienie").json()
    scen = {s["id"]: s for s in z["scenariusze"]}
    assert (scen["srednia-4-2"]["przed"]["wynik"], scen["srednia-4-2"]["po"]["wynik"]) == ("POZYTYWNA", "NEGATYWNA")
    assert {o["cykl_id"]: o["niejasne"] for o in z["oceny"]}[oceny["K-004"][0]] is True
    obszary = {o["id"]: o["zmieniony"] for o in z["obszary"]}
    assert {k: v for k, v in obszary.items() if k in ("skala", "kryteria", "terminy", "pytania-pomocnicze")} == {
        "skala": True, "kryteria": True, "terminy": False, "pytania-pomocnicze": False}
    assert "graf" in obszary
    assert k.get("/api/reguly").json() == reguly_przed

    bez_potwierdzenia = k.post(f"/api/przepisy/akty/{aid}/publikacja",
                               json={"obowiazuje_od": "2027-01-01", "wersja": "przepisy-demo-v2"})
    assert bez_potwierdzenia.status_code == 409 and "§ 10a" in bez_potwierdzenia.text
    inna_data = k.post(f"/api/przepisy/akty/{aid}/publikacja",
                       json={"obowiazuje_od": "2026-12-01", "wersja": "przepisy-demo-v2", "potwierdzam_nieobslugiwane": True})
    assert inna_data.status_code == 422 and "2027-01-01" in inna_data.text
    pub = k.post(f"/api/przepisy/akty/{aid}/publikacja",
                 json={"obowiazuje_od": "2027-01-01", "wersja": "przepisy-demo-v2",
                       "potwierdzam_nieobslugiwane": True})
    assert pub.status_code == 200, pub.text
    assert [w["wersja"] for w in k.get("/api/reguly").json()] == ["przepisy-2016-v1", "przepisy-demo-v2"]

    # 2. Zakończona ocena po publikacji v2 daje ten sam wynik co przed
    po = k.get(f"/api/cykle/{zakonczona}/ocena").json()
    assert po["wersja_regul"] == "przepisy-2016-v1" and po["wynik"] == wynik_przed

    # 3. Ocena w toku z niejasnym przejściem tworzy zadanie decyzyjne
    zadania = k.get("/api/przepisy/zadania").json()
    (zad,) = [x for x in zadania if x["cykl_id"] == oceny["K-004"][0]]
    assert zad["status"] == "OTWARTE" and set(zad["opcje"]) == {"przepisy-2016-v1", "przepisy-demo-v2"}
    assert zakonczona not in {x["cykl_id"] for x in zadania}
    bez = k.post(f"/api/przepisy/zadania/{zad['id']}", json={"wersja": "przepisy-demo-v2", "uzasadnienie": ""})
    assert bez.status_code == 422
    ok = k.post(f"/api/przepisy/zadania/{zad['id']}", json={"wersja": "przepisy-demo-v2", "uzasadnienie": "Okres oceny w większości po zmianie."})
    assert ok.status_code == 200 and ok.json()["status"] == "ROZSTRZYGNIETE"
    w_toku = k.get(f"/api/cykle/{oceny['K-004'][0]}/ocena").json()
    assert w_toku["wersja_regul"] == "przepisy-demo-v2" and w_toku["wynik"]["wynik"] == "NEGATYWNA"
    historia = k.get(f"/api/cykle/{oceny['K-004'][0]}/historia").json()
    assert any(h["roznica"].get("wersja_regul") for h in historia)

    # Cykl rozpoczęty po publikacji, przed datą obowiązywania (według v1) też dostaje zadanie
    nowy = k.post("/api/pracownicy/K-006/cykle", json={}).json()
    assert nowy["wersja_regul"] == "przepisy-2016-v1"
    zad_nowe = [x for x in k.get("/api/przepisy/zadania").json() if x["cykl_id"] == nowy["id"]]
    assert len(zad_nowe) == 1 and zad_nowe[0]["status"] == "OTWARTE"
    assert len([x for x in k.get("/api/przepisy/zadania").json() if x["cykl_id"] == nowy["id"]]) == 1  # bez duplikatów
    zb = k.post("/api/przepisy/zadania/zbiorczo",
                json={"ids": [zad_nowe[0]["id"]], "wersja": "przepisy-2016-v1", "uzasadnienie": "Ocena przed 1.01.2027."})
    assert zb.status_code == 200 and zb.json()[0]["status"] == "ROZSTRZYGNIETE"


def test_akt_wymaga_uprawnienia_administratora(api):
    k = api
    r = k.post("/api/auth/login", json={"login": "oceniajacy", "hasło": "demo"})
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    assert k.post("/api/przepisy/akty", json={"nazwa": "x", "tekst": "y"}, headers=h).status_code == 403


def test_opublikowana_wersja_i_akt_przetrwaja_restart(api, ustawienia):
    """Po test_kryteria_akceptacji: nowa aplikacja na tej samej bazie wczytuje v2 i akt do korpusu."""
    with TestClient(utworz_aplikacje(replace(ustawienia, baza=api.app.state.ustawienia.baza))) as k:
        z = k.get("/api/zdrowie").json()
        assert z["wersje_regul"] == ["przepisy-2016-v1", "przepisy-demo-v2"]
        assert akt.doc_id_aktu(TEKST_DEMO) in k.app.state.korpus.dokumenty


def test_pusty_akt_i_niespojne_wartosci_odrzucone(api):
    k = api
    assert k.post("/api/przepisy/akty", json={"nazwa": "x", "tekst": "   "}).status_code == 422
    aid = k.post("/api/przepisy/akty", json={"nazwa": "Akt demo 2", "tekst": TEKST_DEMO}).json()["id"]
    k.post(f"/api/przepisy/akty/{aid}/analiza")
    k.put(f"/api/przepisy/akty/{aid}/propozycje/granica-pozytywnej", json={"przyjeta": True, "wartosc_po": "7"})
    r = k.get(f"/api/przepisy/akty/{aid}/zestawienie")
    assert r.status_code == 422 and "granic" in r.text
    k.put(f"/api/przepisy/akty/{aid}/propozycje/granica-pozytywnej", json={"przyjeta": False})
    k.put(f"/api/przepisy/akty/{aid}/propozycje/limit-dodatkowych", json={"przyjeta": True, "wartosc_po": "2.7"})
    assert k.get(f"/api/przepisy/akty/{aid}/zestawienie").status_code == 422
