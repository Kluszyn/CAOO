import json
from datetime import timedelta
from pathlib import Path

import pytest

from caoo.kadry import ZNACZNIK_DEMO, MockKadry, kontrakt_openapi
from caoo.ustawienia import KATALOG_APLIKACJI, Ustawienia

KONTRAKT = KATALOG_APLIKACJI / "backend" / "kontrakt" / "kadry-openapi.json"
SEKCJE = [
    "imie", "nazwisko", "stanowisko", "komorka", "jednostka", "zatrudnienie", "przelozony",
    "zastepstwa", "opis_stanowiska", "poprzednie_oceny", "nieobecnosci", "uwagi", "mianowanie",
]


def _lista(klient):
    return klient.get("/api/kadry/pracownicy").json()


def _profil(klient, pid):
    return klient.get(f"/api/kadry/pracownicy/{pid}").json()


def test_lista_ma_znacznik_demo_i_co_najmniej_5_osob(klient):
    lista = _lista(klient)
    assert lista["demo"] is True and lista["znacznik"] == ZNACZNIK_DEMO == "dane demonstracyjne"
    assert len(lista["pracownicy"]) >= 5
    assert all(p["demo"] is True for p in lista["pracownicy"])


def test_przypadki_z_ticketu(klient):
    profile = [_profil(klient, p["id"]) for p in _lista(klient)["pracownicy"]]
    assert any(p["kierownik_urzedu"] for p in profile), "osoba na arkusz A"
    assert any(p["liczba_podleglych"] > 0 and not p["kierownik_urzedu"] for p in profile), "osoba na arkusz B"
    assert any(p["liczba_podleglych"] == 0 for p in profile), "osoba na arkusz C"
    assert any(p["pierwsza_praca_w_sluzbie_cywilnej"] and not p["poprzednie_oceny"] for p in profile), "pierwsza ocena"

    def najdluzsza_ciagla(n):  # dni wolne (weekend) nie przerywają ciągłości
        okresy = sorted((x["od"], x["do"]) for x in n if x["usprawiedliwiona"])
        from datetime import date
        best, start, koniec = 0, None, None
        for od, do in okresy:
            od, do = date.fromisoformat(od), date.fromisoformat(do)
            if koniec and (od - koniec) <= timedelta(days=3):
                koniec = max(koniec, do)
            else:
                start, koniec = od, do
            best = max(best, (koniec - start).days + 1)
        return best

    assert any(najdluzsza_ciagla(p["nieobecnosci"]) > 31 for p in profile), "długa nieobecność"


def test_profil_ma_wszystkie_sekcje_i_znacznik(klient):
    p = _profil(klient, "K-002")
    for sekcja in SEKCJE:
        assert sekcja in p, sekcja
    assert p["znacznik"] == "dane demonstracyjne" and p["demo"] is True
    assert p["przelozony"] == "Marek Graniczny"
    assert p["zastepstwa"][0]["zastepca"] == "Tomasz Radczyk"
    assert p["poprzednie_oceny"][0]["zalecenia"]
    assert p["mianowanie"]["stopien"] == "radca"
    assert any(u["rodzaj"] == "nagana" for u in _profil(klient, "K-004")["uwagi"])


def test_stanowiska_z_przykladowych_arkuszy(klient):
    przyklady = KATALOG_APLIKACJI.parent / "materiały" / "5. Przykładowe wypełnione formularze oceny"
    for p in _lista(klient)["pracownicy"]:
        assert (przyklady / _profil(klient, p["id"])["zrodlo_przykladu"]).is_file(), p["id"]


def test_nieznany_pracownik_404(klient):
    assert klient.get("/api/kadry/pracownicy/K-999").status_code == 404


def test_kontrakt_openapi_jest_aktualny(klient):
    """Po zmianie modeli: uv run python -m caoo.kadry (zapisuje kontrakt/kadry-openapi.json)."""
    zapisany = json.loads(KONTRAKT.read_text(encoding="utf-8"))
    assert zapisany == kontrakt_openapi(klient.app.openapi())
    assert set(zapisany["paths"]) == {"/api/kadry/pracownicy", "/api/kadry/pracownicy/{pracownik_id}"}


def test_dane_bez_znacznika_demo_sa_odrzucane(tmp_path):
    plik = tmp_path / "k.yaml"
    plik.write_text(Ustawienia.ze_srodowiska().kadry.read_text(encoding="utf-8").replace("demo: true", "demo: false"))
    with pytest.raises(ValueError, match="demo"):
        MockKadry.z_pliku(plik)
