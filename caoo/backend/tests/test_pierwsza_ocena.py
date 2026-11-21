"""Pierwsza ocena jako osobny proces z oznaczonymi brakami (#19, V1-12)."""

from datetime import date

import pytest
from caoo_core.zrodla.cli import main as waliduj

from caoo.kadry import MockKadry
from caoo.pierwsza_ocena import KonfiguracjaPierwszejOceny, sciezka_pierwszej_oceny
from caoo.ustawienia import Ustawienia

UST = Ustawienia.ze_srodowiska()
KADRY = MockKadry.z_pliku(UST.kadry)
KONF = KonfiguracjaPierwszejOceny.z_pliku(UST.pierwsza_ocena)

BRAK = "BRAK_ŹRÓDŁA"


def _sciezka(klient, pid="K-005"):
    r = klient.get(f"/api/pracownicy/{pid}/pierwsza-ocena")
    assert r.status_code == 200, r.text
    return r.json()


def _elementy(s):
    return {e["id"]: e for sekcja in s["sekcje"] for e in sekcja["elementy"]}


# --- dostęp ze ścieżki wyboru procesu (V1-4) ----------------------------------------


def test_sciezka_dostepna_dla_osoby_wskazanej_przez_wybor_procesu(klient):
    assert klient.get("/api/pracownicy/K-005/proces").json()["proces"] == "PIERWSZA"
    s = _sciezka(klient)
    assert s["pracownik_id"] == "K-005" and s["proces"] == "PIERWSZA"
    assert s["wersja"] == KONF.wersja


@pytest.mark.parametrize("pid", ["K-003", "K-007"])
def test_sciezka_niedostepna_gdy_wybor_procesu_nie_wskazal_pierwszej_oceny(klient, pid):
    r = klient.get(f"/api/pracownicy/{pid}/pierwsza-ocena")
    assert r.status_code == 409
    assert "pierwszej ocen" in r.json()["detail"]


def test_nieznany_pracownik_404(klient):
    assert klient.get("/api/pracownicy/X-404/pierwsza-ocena").status_code == 404


# --- elementy z podstawą i bez podstawy ---------------------------------------------


def test_kazdy_element_ma_adnotacje_i_status_wynikajacy_z_adnotacji(klient):
    for e in _elementy(_sciezka(klient)).values():
        assert e["adnotacje"], e["id"]
        kategorie = {a["kategoria"] for a in e["adnotacje"]}
        assert (e["status"] == BRAK) == (BRAK in kategorie), e["id"]


@pytest.mark.parametrize("eid", [
    "pierwsza.tryb",
    "pierwsza.wzor-arkusza",
    "pierwsza.schemat-pol",
    "pierwsza.formularz-sprawozdania",
    "pierwsza.warunki-wyniku",
    "pierwsza.skala",
])
def test_element_bez_podstawy_w_materialach_ma_brak_zrodla(klient, eid):
    e = _elementy(_sciezka(klient))[eid]
    assert e["status"] == BRAK
    brak = next(a for a in e["adnotacje"] if a["kategoria"] == BRAK)
    assert brak["uzasadnienie"]


@pytest.mark.parametrize("eid, lokalizator", [
    ("pierwsza.termin", "art. 38 ust. 1"),
    ("pierwsza.oceniajacy", "art. 38 ust. 2"),
    ("pierwsza.postawa", "art. 38 ust. 2 pkt 1"),
    ("pierwsza.egzamin", "art. 38 ust. 2 pkt 2"),
    ("pierwsza.sprawozdanie", "art. 38 ust. 2 pkt 3"),
    ("pierwsza.skutek", "art. 38 ust. 3"),
])
def test_element_z_ustawy_ma_przepis(klient, eid, lokalizator):
    e = _elementy(_sciezka(klient))[eid]
    assert e["status"] == "ŹRÓDŁO"
    assert any(a["kategoria"] == "PRZEPIS" and a["lokalizator"] == lokalizator for a in e["adnotacje"])


def test_zasady_oceny_okresowej_nie_sa_kopiowane(klient):
    s = _sciezka(klient)
    assert "arkusz" not in s and "kryteria" not in s
    for e in _elementy(s).values():
        for a in e["adnotacje"]:
            # Rozporządzenie o ocenach okresowych nie jest podstawą żadnego elementu pierwszej oceny.
            assert a.get("doc_id") != "rozp-oceny-okresowe", e["id"]


def test_cytaty_konfiguracji_pierwszej_oceny_sa_w_korpusie():
    assert waliduj(["--manifest", str(UST.manifest), str(UST.pierwsza_ocena)]) == 0


# --- termin i oceniający (czysta funkcja) -------------------------------------------


def test_okno_terminu_z_art_38_ust_1():
    p = KADRY.profil("K-005")  # nawiązanie 1.06.2026, umowa 12 miesięcy (art. 35 ust. 3)
    t = sciezka_pierwszej_oceny(p, KONF).termin
    assert t.nawiazanie == date(2026, 6, 1)
    assert t.najwczesniej == date(2027, 2, 1)
    assert t.koniec_umowy == date(2027, 5, 31)
    assert t.najpozniej == date(2027, 4, 30)
    assert len(t.slad) == 3


def test_oceniajacy_to_bezposredni_przelozony_w_porozumieniu_z_kierujacym_komorka():
    p = KADRY.profil("K-005")
    o = sciezka_pierwszej_oceny(p, KONF).oceniajacy
    przelozony = KADRY.profil(p.przelozony_id)
    assert o.przelozony_id == przelozony.id
    assert o.przelozony == f"{przelozony.imie} {przelozony.nazwisko}"
    assert o.komorka == p.komorka


# --- panel braków -------------------------------------------------------------------


def test_panel_brakow_pokazuje_pytanie_do_organizatorow_o_schemat_pol(klient):
    r = klient.get("/api/braki")
    assert r.status_code == 200, r.text
    b = r.json()
    pytania = {p["id"]: p for p in b["pytania_do_organizatorow"]}
    pyt = pytania["pytanie.schemat-pol-pierwszej-oceny"]
    assert "schemat pól" in pyt["tresc"]
    assert pyt["adnotacje"] and pyt["dotyczy"] == ["pierwsza.schemat-pol"]


def test_panel_brakow_wymienia_wszystkie_elementy_bez_zrodla(klient):
    b = klient.get("/api/braki").json()
    w_panelu = {x["id"] for x in b["braki_zrodel"]}
    bez_zrodla = {e["id"] for e in _elementy(_sciezka(klient)).values() if e["status"] == BRAK}
    assert bez_zrodla <= w_panelu
    assert "proces.pierwsza" in w_panelu  # brak rozporządzenia – adnotacja reguły z #11
    assert all(x["uzasadnienie"] for x in b["braki_zrodel"])


@pytest.mark.parametrize("nawiazanie, najwczesniej, koniec, najpozniej", [
    # Okres miesięczny liczony od dnia nawiązania kończy się dzień przed dniem odpowiadającym;
    # gdy takiego dnia nie ma – ostatniego dnia miesiąca. „Po upływie” = dzień następny.
    (date(2026, 3, 31), date(2026, 12, 1), date(2027, 3, 30), date(2027, 2, 28)),
    (date(2026, 6, 30), date(2027, 3, 1), date(2027, 6, 29), date(2027, 5, 29)),
    (date(2028, 2, 29), date(2028, 10, 29), date(2029, 2, 28), date(2029, 1, 28)),
])
def test_okno_terminu_na_koncu_miesiaca(nawiazanie, najwczesniej, koniec, najpozniej):
    p = KADRY.profil("K-005")
    p = p.model_copy(update={"zatrudnienie": p.zatrudnienie.model_copy(update={"data_nawiazania": nawiazanie})})
    t = sciezka_pierwszej_oceny(p, KONF).termin
    assert (t.najwczesniej, t.koniec_umowy, t.najpozniej) == (najwczesniej, koniec, najpozniej)
