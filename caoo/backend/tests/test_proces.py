import pytest

from caoo.kadry import MockKadry
from caoo.proces import wybierz
from caoo.reguly import wczytaj_wersje
from caoo.ustawienia import Ustawienia

UST = Ustawienia.ze_srodowiska()
KADRY = MockKadry.z_pliku(UST.kadry)
(REGULY,) = wczytaj_wersje(UST.reguly)


def _proces(klient, pid):
    r = klient.get(f"/api/pracownicy/{pid}/proces")
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize("pid, arkusz, opis", [
    ("K-003", "C", "czas nieokreślony, bez podległych"),
    ("K-002", "B", "kieruje zespołem"),
    ("K-001", "A", "kierownik urzędu"),
])
def test_ocena_okresowa_i_arkusz(klient, pid, arkusz, opis):
    w = _proces(klient, pid)
    assert (w["proces"], w["arkusz"]) == ("OKRESOWA", arkusz), opis
    assert w["braki"] == [] and w["wersja_regul"] == "przepisy-2016-v1"


def test_nowo_zatrudniona_to_pierwsza_ocena_osobny_proces(klient):
    w = _proces(klient, "K-005")
    assert w["proces"] == "PIERWSZA" and w["arkusz"] is None
    assert "osobny proces" in w["komunikat"]
    p = {x["regula"]: x for x in w["przeslanki"]}
    assert p["proces.okresowa"]["spelniona"] is False and p["proces.pierwsza"]["spelniona"] is True
    kategorie = {a["kategoria"] for a in p["proces.pierwsza"]["adnotacje"]}
    assert {"PRZEPIS", "BRAK_ŹRÓDŁA"} <= kategorie  # brak rozporządzenia o pierwszej ocenie


def test_brak_podstawy_zatrudnienia_nie_mozna_ustalic(klient):
    w = _proces(klient, "K-007")
    assert w["proces"] == "NIE_MOZNA_USTALIC" and w["arkusz"] is None
    assert any("zatrudnienie.rodzaj" in b for b in w["braki"])
    assert "Nie można ustalić" in w["komunikat"]


def test_kazda_przeslanka_ma_source_ref(klient):
    for p in KADRY.lista().pracownicy:
        for x in _proces(klient, p.id)["przeslanki"]:
            assert x["adnotacje"], (p.id, x["regula"])
            for a in x["adnotacje"]:
                if a["kategoria"] == "PRZEPIS":
                    assert a["cytat_dosłowny"] and a["lokalizator"], (p.id, x["regula"])


def _zmien(pid, **pola):
    return KADRY.profil(pid).model_copy(update=pola)


def test_umowa_okreslona_bez_informacji_o_pierwszej_pracy():
    p = _zmien("K-005", pierwsza_praca_w_sluzbie_cywilnej=None)
    w = wybierz(p, REGULY)
    assert w.proces == "NIE_MOZNA_USTALIC" and w.braki == ["pierwsza_praca_w_sluzbie_cywilnej"]


def test_umowa_okreslona_nie_pierwsza_praca_wymaga_informacji_o_zastepstwie():
    w = wybierz(_zmien("K-005", pierwsza_praca_w_sluzbie_cywilnej=False), REGULY)
    assert w.proces == "NIE_MOZNA_USTALIC"
    assert w.przeslanki[-1].regula == "proces.zastepstwo" and w.przeslanki[-1].spelniona is None


def test_brak_liczby_podleglych_okresowa_bez_arkusza():
    w = wybierz(_zmien("K-003", liczba_podleglych=None), REGULY)
    assert (w.proces, w.arkusz, w.braki) == ("OKRESOWA", None, ["liczba_podleglych"])


def test_nieznany_pracownik_404(klient):
    assert klient.get("/api/pracownicy/K-999/proces").status_code == 404
