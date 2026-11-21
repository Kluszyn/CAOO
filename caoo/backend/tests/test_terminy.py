from datetime import date

import pytest

from caoo.kadry import MockKadry, Nieobecnosc
from caoo.reguly import wczytaj_wersje
from caoo.swieta import dni_wolne
from caoo.terminy import termin_oceny
from caoo.ustawienia import Ustawienia


USTAWIENIA = Ustawienia.ze_srodowiska()
KADRY = MockKadry.z_pliku(USTAWIENIA.kadry)
(REGULY,) = wczytaj_wersje(USTAWIENIA.reguly)


def _profil(data_oceny: date, *nieobecnosci: Nieobecnosc):
    p = KADRY.profil("K-006")
    ocena = p.poprzednie_oceny[0].model_copy(update={"data_sporzadzenia": data_oceny})
    return p.model_copy(update={"poprzednie_oceny": [ocena], "nieobecnosci": list(nieobecnosci)})


def test_scenariusz_demo_laczy_weekend_i_przesuwa_termin_w_api(klient):
    r = klient.get("/api/pracownicy/K-006/proces")
    assert r.status_code == 200, r.text
    wynik = r.json()["termin_oceny"]
    assert wynik["termin_bazowy"] == "2026-09-20"
    assert wynik["termin"] == "2026-11-27"
    assert wynik["okresy_polaczone"] == [{"od": "2026-02-02", "do": "2026-04-10", "dni_doliczone": 68}]
    kroki = {k["regula"]: k for k in wynik["slad"]}
    assert "Połączono okres" in kroki["termin.ciaglosc-nieobecnosci"]["opis"]
    assert kroki["termin.nieobecnosc-81-7"]["adnotacje"][0]["lokalizator"] == "art. 81 ust. 7"
    assert "Źródło spoza materiałów" in kroki["termin.swieta-demo"]["opis"]


@pytest.mark.parametrize(("poczatek", "bazowy", "po_przesunieciu"), [
    (date(2024, 2, 29), date(2026, 2, 28), date(2026, 3, 2)),
    (date(2026, 2, 28), date(2028, 2, 28), date(2028, 2, 28)),
    (date(2024, 12, 24), date(2026, 12, 24), date(2026, 12, 28)),
])
def test_termin_24_miesiace_z_koncem_miesiaca_weekendem_i_swietem(poczatek, bazowy, po_przesunieciu):
    wynik = termin_oceny(_profil(poczatek), REGULY)
    assert wynik.termin_bazowy == bazowy
    assert wynik.termin == po_przesunieciu


def test_krotkie_nieusprawiedliwione_i_spoznione_okresy_nie_przesuwaja_terminu():
    p = _profil(
        date(2024, 12, 4),
        Nieobecnosc(od=date(2026, 1, 31), do=date(2026, 2, 28), rodzaj="urlop", usprawiedliwiona=True),
        Nieobecnosc(od=date(2026, 3, 3), do=date(2026, 5, 7), rodzaj="nieusprawiedliwiona", usprawiedliwiona=False),
        Nieobecnosc(od=date(2026, 12, 7), do=date(2027, 2, 12), rodzaj="urlop", usprawiedliwiona=True),
    )
    wynik = termin_oceny(p, REGULY)
    assert wynik.termin == wynik.termin_bazowy == date(2026, 12, 4)
    assert [o.dni_doliczone for o in wynik.okresy_polaczone] == [0]


def test_przesuniecie_pozwala_uwzglednic_kolejna_dluga_nieobecnosc():
    p = _profil(
        date(2024, 4, 7),
        Nieobecnosc(od=date(2026, 1, 1), do=date(2026, 2, 8), rodzaj="urlop", usprawiedliwiona=True),
        Nieobecnosc(od=date(2026, 4, 15), do=date(2026, 5, 22), rodzaj="urlop", usprawiedliwiona=True),
    )
    wynik = termin_oceny(p, REGULY)
    assert [o.dni_doliczone for o in wynik.okresy_polaczone] == [39, 38]
    assert wynik.termin == date(2026, 6, 23)


def test_wyjatki_administratora_zmieniaja_termin():
    p = _profil(date(2024, 12, 24))
    dodatkowy_wolny = REGULY.model_copy(update={"terminy": {**REGULY.terminy, "dni_wolne": ["2026-12-28"]}})
    odwolane_swieto = REGULY.model_copy(update={"terminy": {**REGULY.terminy, "dni_robocze": ["2026-12-24"]}})
    assert termin_oceny(p, dodatkowy_wolny).termin == date(2026, 12, 29)
    assert termin_oceny(p, odwolane_swieto).termin == date(2026, 12, 24)


def test_kalendarz_swiat_ruchomych_i_wigilii():
    swieta = dni_wolne({}, 2024, 2026)
    assert {date(2026, 4, 5), date(2026, 4, 6), date(2026, 5, 24), date(2026, 6, 4)} <= swieta
    assert date(2024, 12, 24) not in swieta
    assert date(2025, 12, 24) in swieta


@pytest.mark.parametrize("pid", ["K-004", "K-005", "K-007"])
def test_inne_sciezki_i_brak_danych_nie_dostaja_terminu_24_miesiecy(klient, pid):
    r = klient.get(f"/api/pracownicy/{pid}/proces")
    assert r.status_code == 200
    assert r.json()["termin_oceny"] is None
