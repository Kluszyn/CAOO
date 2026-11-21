from datetime import date

import pytest

from caoo.kadry import MockKadry
from caoo.kryteria import termin_ustalenia
from caoo.reguly import wczytaj_wersje
from caoo.ustawienia import Ustawienia

UST = Ustawienia.ze_srodowiska()
KADRY = MockKadry.z_pliku(UST.kadry)
(REGULY,) = wczytaj_wersje(UST.reguly)


def test_kryteria_obowiazkowe_arkusza_z_par_4(klient):
    b = klient.get("/api/pracownicy/K-002/kryteria").json()
    assert b["arkusz"] == "B" and b["regula_obowiazkowych"] == "kryteria.obowiazkowe-b"
    assert [k["id"] for k in b["obowiazkowe"]] == [
        "zarzadzanie-ludzmi", "podejmowanie-decyzji", "organizacja-pracy", "wspolpraca", "komunikacja"]
    a = klient.get("/api/pracownicy/K-001/kryteria").json()
    assert len(a["obowiazkowe"]) == 6
    assert all(k["adnotacje"][0]["kategoria"] == "PRZEPIS" for k in a["obowiazkowe"])
    assert len(b["dostepne_dodatkowe"]) == 14 - 5


def test_propozycje_maja_oznaczenie_i_uzasadnienie(klient):
    c = klient.get("/api/pracownicy/K-006/kryteria").json()
    (p,) = c["propozycje"]
    assert p["id"] == "myslenie-analityczne" and p["oznaczenie"] == "propozycja"
    assert p["uzasadnienie"] and len(p["zadania"]) == 3
    assert all(x["id"] not in {k["id"] for k in c["obowiazkowe"]} for x in c["propozycje"])
    assert len(c["propozycje"]) <= c["limit_dodatkowych"] == 3


def test_termin_30_dni_ze_sladem_i_przesunieciem_z_niedzieli(klient):
    t = klient.get("/api/pracownicy/K-006/kryteria").json()["termin"]
    assert t["data_zdarzenia"] == "2024-09-20" and t["regula"] == "termin.kryteria-30"
    assert t["termin"] == "2024-10-21"  # 20.10.2024 to niedziela
    assert [k["regula"] for k in t["slad"]] == ["termin.kryteria-30", "termin.dzien-wolny"]
    assert all(k["adnotacje"] for k in t["slad"])


def test_termin_7_dni_po_ocenie_negatywnej(klient):
    t = klient.get("/api/pracownicy/K-004/kryteria").json()["termin"]
    assert (t["data_zdarzenia"], t["termin"], t["regula"]) == ("2026-06-02", "2026-06-09", "termin.kryteria-7")


def test_termin_bez_poprzedniej_oceny_liczony_od_zatrudnienia():
    p = KADRY.profil("K-003").model_copy(update={"poprzednie_oceny": []})
    t, braki = termin_ustalenia(p, REGULY)
    assert braki == [] and t.data_zdarzenia == date(2019, 4, 1) and "zatrudnienie" in t.zdarzenie


def test_kryteria_tylko_dla_oceny_okresowej(klient):
    assert klient.get("/api/pracownicy/K-005/kryteria").status_code == 409  # pierwsza ocena
    assert klient.get("/api/pracownicy/K-007/kryteria").status_code == 409  # nie można ustalić


@pytest.mark.parametrize("dodatkowe, fragment", [
    (["kreatywnosc", "negocjowanie", "myslenie-analityczne", "wystapienia-publiczne"], "Najwyżej 3"),
    (["rzetelnosc"], "obowiązkowe dla arkusza C"),
    (["nieznane"], "nie występuje w wykazie"),
    (["kreatywnosc", "kreatywnosc"], "więcej niż raz"),
])
def test_api_blokuje_niepoprawny_wybor(klient, dodatkowe, fragment):
    r = klient.post("/api/pracownicy/K-003/cykle", json={"dodatkowe": dodatkowe})
    assert r.status_code == 422
    assert any(fragment in b for b in r.json()["detail"]["bledy"])


def test_cykl_i_potwierdzenie_ocenianego_z_data(klient):
    r = klient.post("/api/pracownicy/K-006/cykle", json={"dodatkowe": ["myslenie-analityczne"]})
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["arkusz"] == "C" and [k["id"] for k in c["dodatkowe"]] == ["myslenie-analityczne"]
    assert c["termin"]["termin"] == "2024-10-21" and c["wersja_regul"] == "przepisy-2016-v1"
    assert c["data_potwierdzenia"] is None
    assert klient.post("/api/pracownicy/K-006/cykle", json={}).status_code == 409  # drugi w toku

    r = klient.post(f"/api/cykle/{c['id']}/potwierdzenie", json={"data": "2024-10-18"})
    assert r.status_code == 200
    assert (r.json()["potwierdzil"], r.json()["data_potwierdzenia"]) == ("Jolanta Jakościowa", "2024-10-18")
    assert klient.get(f"/api/cykle/{c['id']}").json()["data_potwierdzenia"] == "2024-10-18"
    assert klient.post(f"/api/cykle/{c['id']}/potwierdzenie", json={}).status_code == 409


def test_regula_potwierdzenia_ma_cytat_par_6():
    (a,) = REGULY.regula("kryteria.potwierdzenie").adnotacje
    assert a.lokalizator == "§ 6 ust. 2" and "potwierdza podpisem" in a.cytat_doslowny
