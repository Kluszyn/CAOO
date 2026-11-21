"""Role, uprawnienia w API i historia zmian (#10, V1-3).

Konta: oceniany = K-004, oceniajacy = K-002, zastepca = K-003, kierownik = K-001.

Struktura demo (kadry.yaml): K-001 ocenia K-002; K-002 ocenia K-003…K-007;
K-003 zastępuje K-002 od 2026-07-01 do 2026-07-21.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest
from fastapi.testclient import TestClient

from caoo.app import utworz_aplikacje

PODWLADNI_K002 = {"K-003", "K-004", "K-005", "K-006", "K-007"}


@pytest.fixture(scope="module")
def api(ustawienia, tmp_path_factory):
    ust = replace(ustawienia, baza=tmp_path_factory.mktemp("role") / "role.db")
    with TestClient(utworz_aplikacje(ust)) as k:
        k.app.state.dzis = lambda: date(2026, 11, 21)
        tokeny: dict[str, dict] = {}

        def h(login: str) -> dict:
            if login not in tokeny:
                r = k.post("/api/auth/login", json={"login": login, "hasło": "demo"})
                assert r.status_code == 200, r.text
                tokeny[login] = {"Authorization": f"Bearer {r.json()['token']}"}
            return tokeny[login]

        yield k, h


def _widoczni(k, h, login) -> set[str]:
    r = k.get("/api/kadry/pracownicy", headers=h(login))
    assert r.status_code == 200, r.text
    return {p["id"] for p in r.json()["pracownicy"]}


# ---------------------------------------------------------------- oceniany


def test_oceniany_widzi_tylko_wlasny_profil_i_oceny(api):
    k, h = api
    assert _widoczni(k, h, "oceniany") == {"K-004"}
    assert k.get("/api/kadry/pracownicy/K-004", headers=h("oceniany")).status_code == 200
    for cudzy in ("K-006", "K-002", "K-001"):
        assert k.get(f"/api/kadry/pracownicy/{cudzy}", headers=h("oceniany")).status_code == 403
        assert k.get(f"/api/pracownicy/{cudzy}/cykle", headers=h("oceniany")).status_code == 403
        assert k.get(f"/api/pracownicy/{cudzy}/proces", headers=h("oceniany")).status_code == 403

    wlasny = k.post("/api/pracownicy/K-004/cykle", json={"dodatkowe": []}, headers=h("oceniajacy"))
    cudzy = k.post("/api/pracownicy/K-006/cykle", json={"dodatkowe": []}, headers=h("oceniajacy"))
    assert wlasny.status_code == 201 and cudzy.status_code == 201, (wlasny.text, cudzy.text)
    w, c = wlasny.json()["id"], cudzy.json()["id"]
    assert k.get(f"/api/cykle/{w}/ocena", headers=h("oceniany")).status_code == 200
    assert k.get(f"/api/cykle/{c}/ocena", headers=h("oceniany")).status_code == 403
    assert k.get(f"/api/cykle/{c}", headers=h("oceniany")).status_code == 403
    # Oceniany nie zapisuje oceny ani nie widzi notatek z rozmowy
    assert k.put(f"/api/cykle/{w}/ocena", json={"punkty": {}}, headers=h("oceniany")).status_code == 403
    assert k.get(f"/api/cykle/{w}/rozmowa", headers=h("oceniany")).status_code == 403
    # ... ale potwierdza własny cykl (§ 6 ust. 2), cudzego nie
    assert k.post(f"/api/cykle/{c}/potwierdzenie", json={}, headers=h("oceniany")).status_code == 403
    assert k.post(f"/api/cykle/{w}/potwierdzenie", json={}, headers=h("oceniany")).status_code == 200


# ---------------------------------------------------------------- oceniający i zastępstwo


def test_oceniajacy_widzi_tylko_osoby_ktore_ocenia(api):
    k, h = api
    assert _widoczni(k, h, "oceniajacy") == PODWLADNI_K002
    assert _widoczni(k, h, "kierownik") == {"K-002"}
    assert k.get("/api/kadry/pracownicy/K-001", headers=h("oceniajacy")).status_code == 403
    assert k.get("/api/kadry/pracownicy/K-002", headers=h("oceniajacy")).status_code == 403  # nie siebie
    assert k.post("/api/pracownicy/K-002/cykle", json={"dodatkowe": []}, headers=h("oceniajacy")).status_code == 403


def test_oceniajacy_w_zastepstwie_tylko_w_okresie_zastepstwa(api):
    k, h = api
    # Poza okresem zastępstwa radca K-003 nikogo nie ocenia
    assert _widoczni(k, h, "zastepca") == set()
    assert k.get("/api/kadry/pracownicy/K-004", headers=h("zastepca")).status_code == 403
    # W okresie zastępstwa (1–21.07.2026) widzi podwładnych K-002, ale nie siebie
    k.app.state.dzis = lambda: date(2026, 7, 10)
    try:
        assert _widoczni(k, h, "zastepca") == PODWLADNI_K002 - {"K-003"}
        assert k.get("/api/kadry/pracownicy/K-004", headers=h("zastepca")).status_code == 200
        assert k.get("/api/kadry/pracownicy/K-003", headers=h("zastepca")).status_code == 403
        # Zastępowany nadal widzi swoich podwładnych
        assert _widoczni(k, h, "oceniajacy") == PODWLADNI_K002
    finally:
        k.app.state.dzis = lambda: date(2026, 11, 21)


def test_kadry_i_administrator(api):
    k, h = api
    wszyscy = _widoczni(k, h, "admin")
    assert _widoczni(k, h, "kadry") == wszyscy and {"K-001", "K-002"} <= wszyscy
    cykl = k.post("/api/pracownicy/K-003/cykle", json={"dodatkowe": []}, headers=h("oceniajacy")).json()["id"]
    assert k.get(f"/api/cykle/{cykl}/ocena", headers=h("kadry")).status_code == 200
    assert k.put(f"/api/cykle/{cykl}/ocena", json={"punkty": {}}, headers=h("kadry")).status_code == 403


# ---------------------------------------------------------------- historia zmian


def test_zmiana_oceny_w_historii_z_autorem_i_wersja_regul(api):
    k, h = api
    r = k.post("/api/pracownicy/K-002/cykle", json={"dodatkowe": []}, headers=h("kierownik"))
    assert r.status_code == 201, r.text
    cykl = r.json()
    url = f"/api/cykle/{cykl['id']}"
    kid = k.get(f"{url}/ocena", headers=h("kierownik")).json()["kryteria"][0]["id"]
    assert k.put(f"{url}/ocena", json={"punkty": {kid: 5}}, headers=h("kierownik")).status_code == 200
    assert k.put(f"{url}/ocena", json={"punkty": {kid: 7}, "oczekiwania": {kid: "Terminowe raporty."}},
                 headers=h("admin")).status_code == 200

    historia = k.get(f"{url}/historia", headers=h("kierownik")).json()
    assert [w["autor"] for w in historia] == ["kierownik", "kierownik", "admin"]
    assert all(w["wersja_regul"] == cykl["wersja_regul"] for w in historia)
    assert "obowiazkowe" in historia[0]["roznica"]  # rozpoczęcie cyklu
    assert historia[1]["roznica"]["oceny"] == {"przed": {}, "po": {kid: 5}}
    assert historia[2]["roznica"]["oceny"]["po"] == {kid: 7}
    assert historia[2]["roznica"]["oczekiwania"]["po"] == {kid: "Terminowe raporty."}
    # Zapis bez zmian nie dodaje wpisu
    k.put(f"{url}/ocena", json={"punkty": {kid: 7}}, headers=h("admin"))
    assert len(k.get(f"{url}/historia", headers=h("admin")).json()) == 3
    # Historii cudzej oceny nie widać
    assert k.get(f"{url}/historia", headers=h("oceniany")).status_code == 403


# ---------------------------------------------------------------- bez roli


def test_bez_roli_401_a_dane_publiczne_dostepne(api):
    k, _ = api
    assert k.get("/api/kadry/pracownicy").status_code == 401
    assert k.get("/api/pracownicy/K-004/proces").status_code == 401
    assert k.get("/api/cykle/1/ocena").status_code == 401
    assert k.get("/api/zdrowie").status_code == 200
    assert k.get("/api/reguly").status_code == 200
    assert k.post("/api/auth/login", json={"login": "oceniany", "hasło": "zle"}).status_code == 401


# ---------------------------------------------------------------- zmiany w cyklu (#16) i import (#18)


def test_zmiany_w_cyklu_tylko_dla_prowadzacego(api):
    k, h = api
    cykl = k.get("/api/pracownicy/K-004/cykle", headers=h("oceniany")).json()[0]["id"]  # cykl z testu ocenianego
    zmiana = {"nowy_oceniajacy": "Nowa osoba testowa", "data": "2026-11-20"}
    # Oceniany i kadry nie zmieniają oceniającego; cudzy oceniający też nie
    for login in ("oceniany", "kadry", "kierownik"):
        r = k.post(f"/api/cykle/{cykl}/zmiany-oceniajacego", json=zmiana, headers=h(login))
        assert r.status_code == 403, (login, r.text)
    assert k.post(f"/api/cykle/{cykl}/zmiany-oceniajacego", json=zmiana, headers=h("oceniajacy")).status_code == 200
    # Ponowne kryteria: odczyt dla ocenianego, zapis tylko dla prowadzącego
    assert k.get(f"/api/cykle/{cykl}/ponowne-kryteria", headers=h("kierownik")).status_code == 403
    assert k.get(f"/api/cykle/{cykl}/ponowne-kryteria", headers=h("oceniany")).status_code != 403
    assert k.post(f"/api/cykle/{cykl}/ponowne-kryteria", json={"data": "2026-11-21", "dodatkowe": []},
                  headers=h("oceniany")).status_code == 403


def test_import_arkuszy_tylko_administrator(api):
    k, h = api
    for login in ("oceniany", "oceniajacy", "kadry"):
        assert k.get("/api/import/arkusze", headers=h(login)).status_code == 403
    assert k.get("/api/import/arkusze", headers=h("admin")).status_code == 200
    assert k.get("/api/import/arkusze").status_code == 401


def test_sciezka_pierwszej_oceny_wymaga_dostepu_a_panel_brakow_publiczny(api):
    """#19: K-005 (pierwsza ocena) – widzi jej oceniający i kadry, nie widzi oceniany K-004."""
    k, h = api
    url = "/api/pracownicy/K-005/pierwsza-ocena"
    assert k.get(url).status_code == 401
    assert k.get(url, headers=h("oceniany")).status_code == 403
    assert k.get(url, headers=h("oceniajacy")).status_code == 200
    assert k.get(url, headers=h("kadry")).status_code == 200
    assert k.get("/api/braki").status_code == 200  # braki w regułach, bez danych osobowych


def test_obieg_oceny_wymaga_roli_i_dostepu(api):
    """#17 po #10: wydruk prowadzi oceniający; doręczenie i odwołanie rejestruje oceniający albo kadry."""
    k, h = api
    cykl = k.get("/api/pracownicy/K-004/cykle", headers=h("oceniany")).json()[0]["id"]
    url = f"/api/cykle/{cykl}/ocena/wersje/1"
    doreczenie = {"data_zapoznania": "2026-11-21", "rejestrujacy": "Osoba testowa"}
    assert k.get(f"{url}/pdf").status_code == 401
    assert k.get(f"{url}/pdf", headers=h("kierownik")).status_code == 403  # nie ocenia K-004
    for login in ("oceniany", "kadry", "kierownik"):
        assert k.post(f"{url}/wydruk", headers=h(login)).status_code == 403, login
    for login in ("oceniany", "kierownik"):
        assert k.post(f"{url}/doreczenie", json=doreczenie, headers=h(login)).status_code == 403, login
        assert k.post(f"{url}/odwolanie", json={"data_wniesienia": "2026-11-21"}, headers=h(login)).status_code == 403
    # Uprawnieni przechodzą kontrolę dostępu (dalej decyduje stan wersji)
    assert k.post(f"{url}/doreczenie", json=doreczenie, headers=h("kadry")).status_code != 403
    assert k.post(f"{url}/wydruk", headers=h("oceniajacy")).status_code != 403
