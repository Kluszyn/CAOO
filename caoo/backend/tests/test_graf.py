from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from caoo_core.zrodla import waliduj_adnotacje

from caoo.app import utworz_aplikacje
from caoo.graf import zbuduj_katalog
from caoo.graf_model import Relacja, waliduj_graf, wczytaj_konfiguracje_grafu
from caoo.reguly import wczytaj_wersje


@pytest.fixture
def katalog(ustawienia):
    (wersja,) = wczytaj_wersje(ustawienia.reguly)
    return zbuduj_katalog(wersja, wczytaj_konfiguracje_grafu(ustawienia.graf))


@pytest.fixture
def klient_grafu(ustawienia, tmp_path):
    with TestClient(utworz_aplikacje(replace(ustawienia, baza=tmp_path / "graf.db"))) as klient:
        yield klient


def test_katalog_ma_14_kryteriow_piec_poziomow_i_zrodla(katalog, korpus_aplikacji):
    assert len(katalog.kompetencje) == 14
    assert [p.wartosc for p in katalog.poziomy] == [1, 3, 5, 7, 9]
    assert len(katalog.dzialania) >= 3
    for w in katalog.wezly():
        assert w.adnotacje
        for ref in w.adnotacje:
            assert not waliduj_adnotacje(ref, korpus_aplikacji.korpus, w.id)


def test_typ_spoza_schematu_i_odwrocona_relacja_sa_odrzucane(katalog):
    kompetencja = katalog.kompetencje[0]
    nieznany = kompetencja.model_copy(update={"typ": "nieznany"})
    with pytest.raises(ValueError, match="Typ węzła nieznany jest poza schematem"):
        waliduj_graf(katalog.schemat, [nieznany], [])

    poziom = katalog.poziomy[0]
    odwrotna = Relacja(
        typ="ma_poziom_wymagany", z=poziom.id, do=kompetencja.id,
        status="propozycja", zrodlo="test", uzasadnienie="Odwrócony kierunek relacji.",
    )
    with pytest.raises(ValueError, match="jest niedozwolona"):
        waliduj_graf(katalog.schemat, [kompetencja, poziom], [odwrotna])


def test_katalog_i_graf_oceny_sa_odrebne_a_luka_zalezy_od_punktow(klient_grafu):
    klient = klient_grafu

    def naglowek(login):
        r = klient.post("/api/auth/login", json={"login": login, "hasło": "demo"})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['token']}"}

    odpowiedz = klient.get("/api/graf/katalog")
    assert odpowiedz.status_code == 200, odpowiedz.text
    katalog = odpowiedz.json()
    assert set(katalog) == {"schemat", "wersja_katalogu", "wersja_regul", "kompetencje", "poziomy", "dzialania"}
    oceniajacy = naglowek("oceniajacy")
    start = klient.post("/api/pracownicy/K-004/cykle", json={}, headers=oceniajacy)
    assert start.status_code == 201, start.text
    sciezka = f"/api/cykle/{start.json()['id']}/ocena"
    parametry = {"z_grafem": "true"}
    assert klient.get(sciezka, params=parametry).status_code == 401
    assert klient.get(sciezka, params=parametry, headers=naglowek("kierownik")).status_code == 403

    def odczytaj_graf():
        r = klient.get(sciezka, params=parametry, headers=oceniajacy)
        assert r.status_code == 200, r.text
        graf = r.json()["graf"]
        return graf, {w["id"]: w for w in graf["wezly"]}

    przed, wezly = odczytaj_graf()
    assert przed["katalog"] == katalog
    assert przed["sciezki"] and all(len(s["wezly"]) == 7 for s in przed["sciezki"])
    assert wezly["wymaganie:1"]["nieprzypisany"] is True
    assert wezly["wymagany:rzetelnosc"]["status"] == "propozycja"
    assert wezly["aktualny:rzetelnosc"]["wartosc"] is None
    assert wezly["luka:rzetelnosc"]["wartosc"] is None
    propozycja = next(p for p in przed["propozycje"] if p["kompetencja_id"] == "kompetencja:sytuacje-kryzysowe")
    assert {wezly[i]["typ"] for i in propozycja["podstawy"]} >= {"zadanie", "dowod"}
    assert all(wezly[i]["data_zrodla"] == "2026-06-02" for i in propozycja["podstawy"] if i.startswith("dowod:"))

    r = klient.put(sciezka, json={"punkty": {"rzetelnosc": 3}}, headers=oceniajacy)
    assert r.status_code == 200, r.text
    assert r.json()["graf"] is None
    _, wezly = odczytaj_graf()
    assert wezly["wynik:rzetelnosc"]["wartosc"] == 3
    assert wezly["wynik:rzetelnosc"]["status"] == "propozycja"
    assert wezly["wynik:rzetelnosc"]["zatwierdzil"] is None
    assert wezly["aktualny:rzetelnosc"]["katalog_id"] == "poziom:3"
    assert wezly["luka:rzetelnosc"]["wartosc"] == 2

    r = klient.put(sciezka, json={"punkty": {"rzetelnosc": 7}}, headers=oceniajacy)
    assert r.status_code == 200
    po, wezly = odczytaj_graf()
    assert wezly["luka:rzetelnosc"]["wartosc"] == 0
    assert "kompetencja:rzetelnosc" not in {p["kompetencja_id"] for p in po["propozycje"]}

    punkty = {k["id"]: 7 for k in r.json()["kryteria"]}
    zatwierdzenie = klient.put(sciezka, json={"punkty": punkty, "zatwierdz": True}, headers=naglowek("admin"))
    assert zatwierdzenie.status_code == 200, zatwierdzenie.text
    _, wezly = odczytaj_graf()
    for ident in ("wynik:rzetelnosc", "aktualny:rzetelnosc"):
        assert wezly[ident]["status"] == "potwierdzone"
        assert wezly[ident]["zatwierdzil"] == "admin"
        assert wezly[ident]["data_zatwierdzenia"] == zatwierdzenie.json()["data_zatwierdzenia"]
    assert klient.get("/api/graf/katalog").json() == katalog
