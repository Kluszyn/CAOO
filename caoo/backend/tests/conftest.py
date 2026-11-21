from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from caoo.app import utworz_aplikacje
from caoo.korpus_aplikacji import zaladuj
from caoo.ustawienia import Ustawienia


@pytest.fixture(scope="session")
def ustawienia(tmp_path_factory) -> Ustawienia:
    return replace(Ustawienia.ze_srodowiska(), baza=tmp_path_factory.mktemp("dane") / "test.db")


@pytest.fixture(scope="session")
def korpus_aplikacji(ustawienia):
    return zaladuj(ustawienia.manifest)


@pytest.fixture(scope="session")
def klient(ustawienia):
    with TestClient(utworz_aplikacje(ustawienia)) as k:
        zaloguj_jako(k, "admin")  # API wymaga roli (#10); testy funkcji działają jako administrator
        yield k


def zaloguj_jako(klient, login: str) -> dict:
    """Loguje kontem demo z role.yaml i ustawia nagłówek domyślny klienta."""
    r = klient.post("/api/auth/login", json={"login": login, "hasło": "demo"})
    assert r.status_code == 200, r.text
    naglowek = {"Authorization": f"Bearer {r.json()['token']}"}
    klient.headers.update(naglowek)
    return naglowek
