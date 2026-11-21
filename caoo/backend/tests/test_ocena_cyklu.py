from decimal import Decimal

import pytest


def _cykl(klient, pid, dodatkowe=()):
    r = klient.post(f"/api/pracownicy/{pid}/cykle", json={"dodatkowe": list(dodatkowe)})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _ocen(klient, cid, punkty, **reszta):
    return klient.put(f"/api/cykle/{cid}/ocena", json={"punkty": punkty, **reszta})


@pytest.fixture(scope="module")
def cykl_a(klient):
    """K-001 (arkusz A, 6 kryteriów obowiązkowych)."""
    return _cykl(klient, "K-001")


def test_kryteria_maja_opis_z_zal_1_i_oczekiwania(klient, cykl_a):
    stan = klient.get(f"/api/cykle/{cykl_a}/ocena").json()
    assert len(stan["kryteria"]) == 6 and stan["wynik"] is None
    kom = next(k for k in stan["kryteria"] if k["id"] == "komunikacja")
    assert kom["opis"].startswith("− Koncentrowanie się na wypowiedzi rozmówcy")
    r = _ocen(klient, cykl_a, {}, oczekiwania={"komunikacja": "Jasne komunikaty dla zespołu."}).json()
    assert next(k for k in r["kryteria"] if k["id"] == "komunikacja")["oczekiwania"] == "Jasne komunikaty dla zespołu."


def test_srednia_powyzej_6_i_jedno_1_pkt_to_negatywna_z_regula(klient, cykl_a):
    ids = [k["id"] for k in klient.get(f"/api/cykle/{cykl_a}/ocena").json()["kryteria"]]
    punkty = dict.fromkeys(ids, 9) | {ids[-1]: 1}  # 9,9,9,9,9,1 → średnia 7,67
    stan = _ocen(klient, cykl_a, punkty).json()
    w = stan["wynik"]
    assert w["kompletny"] and Decimal(w["srednia"]) > 6 and w["poziom"] == "powyżej oczekiwań"
    assert w["wynik"] == "NEGATYWNA"
    assert w["regula_rozstrzygajaca"]["id"] == "wynik.negatywna-1pkt"
    assert "znacznie poniżej oczekiwań" in w["regula_rozstrzygajaca"]["nazwa"]
    assert w["regula_rozstrzygajaca"]["adnotacje"][0]["lokalizator"] == "§ 11 ust. 1 pkt 4"
    assert w["slad"]


def test_wynik_przeliczany_po_kazdej_zmianie(klient, cykl_a):
    ids = [k["id"] for k in klient.get(f"/api/cykle/{cykl_a}/ocena").json()["kryteria"]]
    assert _ocen(klient, cykl_a, {ids[-1]: 9}).json()["wynik"]["wynik"] == "POZYTYWNA"
    assert _ocen(klient, cykl_a, {ids[-1]: 1}).json()["wynik"]["wynik"] == "NEGATYWNA"
    czesciowy = _ocen(klient, cykl_a, {ids[0]: None}).json()
    assert czesciowy["wynik"]["kompletny"] is False and any("Brak punktów" in b for b in czesciowy["bledy"])
    _ocen(klient, cykl_a, {ids[0]: 9})


def test_brak_uzasadnienia_przy_negatywnej_to_blad_walidacji(klient, cykl_a):
    r = _ocen(klient, cykl_a, {}, zatwierdz=True)
    assert r.status_code == 422
    assert any("§ 11 ust. 3" in b for b in r.json()["detail"]["bledy"])
    ok = _ocen(klient, cykl_a, {}, uzasadnienie="Rażące naruszenie standardów w jednym kryterium.", zatwierdz=True)
    assert ok.status_code == 200 and ok.json()["zatwierdzona"] and ok.json()["bledy"] == []
    assert _ocen(klient, cykl_a, {}).status_code == 409  # po zatwierdzeniu bez zmian


def test_niedozwolone_punkty_i_obce_kryterium(klient):
    cid = _cykl(klient, "K-002")
    r = _ocen(klient, cid, {"komunikacja": 4, "kreatywnosc": 5})
    assert r.status_code == 422
    bledy = " ".join(r.json()["detail"]["bledy"])
    assert "4 pkt" in bledy and "kreatywnosc" in bledy


def test_regresja_excel_dzielnik_5(klient):
    """3 kryteria dodatkowe: Excel dzieli sumę 8 ocen przez 5, § 11 – przez 8."""
    cid = _cykl(klient, "K-003", ["myslenie-analityczne", "kreatywnosc", "negocjowanie"])
    ids = [k["id"] for k in klient.get(f"/api/cykle/{cid}/ocena").json()["kryteria"]]
    assert len(ids) == 8
    punkty = dict.fromkeys(ids[:5], 5) | dict.fromkeys(ids[5:], 9)  # suma 52
    stan = _ocen(klient, cid, punkty).json()
    p = stan["porownanie_excela"]
    assert (p["srednia_par_11"], p["srednia_excel"], p["dzielnik_excel"], p["liczba_ocen"]) == ("6.50", "10.40", 5, 8)
    assert p["rozni_sie"] is True and p["rozbieznosc"] == "excel.dzielnik-sredniej"
    assert stan["wynik"]["srednia"] == "6.50" and stan["wynik"]["poziom"] == "powyżej oczekiwań"


def test_rozbieznosc_excela_w_panelu_zrodel(klient):
    (w,) = klient.get("/api/reguly").json()
    (r,) = [x for x in w["rozbieznosci"] if x["id"] == "excel.dzielnik-sredniej"]
    assert r["rozbieznosc"]["zastosowana"] == 0
    kategorie = [a["adnotacja"]["kategoria"] for a in r["adnotacje"]]
    assert kategorie == ["PRZEPIS", "OBJAŚNIENIE_MATERIAŁU", "FUNKCJA_APLIKACJI"]
    assert all(a["cytat_w_korpusie"] in (True, None) for a in r["adnotacje"])
    assert "O12" in r["opis"] and "K19:Q21" in r["opis"]
