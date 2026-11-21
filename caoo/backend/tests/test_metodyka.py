import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from caoo.app import utworz_aplikacje
from caoo.metodyka import ETYKIETA, Odpowiedz, propozycja, wczytaj_metodyki
from caoo.reguly import wczytaj_wersje
from conftest import zaloguj_jako


@pytest.mark.parametrize(("wartosci", "oczekiwane"), [
    (["zawsze", "zwykle", "brak_danych"], (None, 2)),
    (["nigdy", "zwykle", "zawsze", "brak_danych", "brak_danych"], (7, 3)),
    (["nigdy", "rzadko", "zwykle", "zawsze"], (3, 4)),
    (["nigdy", "rzadko", "czasem", "zwykle", "zawsze"], (5, 5)),
])
def test_mediana_pomija_brak_danych(wartosci, oczekiwane):
    odpowiedzi = {i: Odpowiedz(wartosc=o) for i, o in enumerate(wartosci, 1)}
    assert propozycja(odpowiedzi) == oczekiwane


def test_kazde_kryterium_ma_piec_roznych_pytan(ustawienia):
    (reguly,) = wczytaj_wersje(ustawienia.reguly)
    pytania = wczytaj_metodyki(ustawienia.metodyki, ustawienia.domyslna_metodyka)[ustawienia.domyslna_metodyka].pytania
    assert pytania.keys() == {k["id"] for k in reguly.kryteria["wykaz"]}
    assert all(len(set(zestaw)) == 5 for zestaw in pytania.values())


@pytest.fixture
def klient_metodyki(ustawienia, tmp_path):
    with TestClient(utworz_aplikacje(replace(ustawienia, baza=tmp_path / "metodyka.db"))) as klient:
        zaloguj_jako(klient, "oceniajacy")
        yield klient


def test_pytania_dowod_i_decyzja_oceniajacego_w_api(klient_metodyki):
    klient = klient_metodyki
    start = klient.post("/api/pracownicy/K-004/cykle", json={"dodatkowe": ["kreatywnosc"]})
    assert start.status_code == 201, start.text
    c = start.json()
    assert c["wersja_metodyki"] == "robocza-v1"
    sciezka = f"/api/cykle/{c['id']}/ocena"

    # Cykl zapisany przed dodaniem pytań nie ma jeszcze nowych pól.
    starszy = {k: v for k, v in c.items() if k not in {"wersja_metodyki", "odpowiedzi", "uzasadnienia_odstepstw"}}
    with klient.app.state.baza as baza:
        baza.execute("UPDATE cykle SET dane = ? WHERE id = ?", (json.dumps(starszy), c["id"]))

    def metodyka(stan):
        return next(k["metodyka"] for k in stan["kryteria"] if k["id"] == "komunikacja")

    przed = klient.get(sciezka).json()
    assert przed["wersja_metodyki"] == "robocza-v1"
    assert all(len(k["metodyka"]["pytania"]) == 5 for k in przed["kryteria"])
    assert metodyka(przed)["etykieta"] == ETYKIETA
    assert klient.put(sciezka, json={"odpowiedzi": {"komunikacja": {"6": {"wartosc": "zawsze"}}}}).status_code == 422

    pierwsze = klient.put(sciezka, json={"odpowiedzi": {"komunikacja": {
        "1": {"wartosc": "zwykle", "dowod": "Notatka po spotkaniu zespołu"},
        "2": {"wartosc": "czasem"},
        "3": {"wartosc": "brak_danych"},
    }}}).json()
    assert metodyka(pierwsze)["liczba_odpowiedzi"] == 2
    assert metodyka(pierwsze)["propozycja"] is None

    dane = klient.put(sciezka, json={"odpowiedzi": {"komunikacja": {"4": {"wartosc": "zawsze"}}}}).json()
    assert metodyka(dane)["propozycja"] == 7 and dane["wynik"] is None
    odpowiedzi = metodyka(klient.get(sciezka).json())["pytania"]
    assert odpowiedzi[0]["odpowiedz"] == {"wartosc": "zwykle", "dowod": "Notatka po spotkaniu zespołu"}
    historia = klient.get(f"/api/cykle/{c['id']}/historia").json()
    assert historia[-1]["autor"] == "oceniajacy"
    zapisane_odpowiedzi = historia[-1]["roznica"]["odpowiedzi"]["po"]
    assert zapisane_odpowiedzi["komunikacja"]["1"] == odpowiedzi[0]["odpowiedz"]

    przyjeta = klient.put(sciezka, json={"punkty": {"komunikacja": 7}}).json()
    assert przyjeta["wynik"]["srednia"] == "7.00"
    assert metodyka(przyjeta)["uzasadnienie_odstepstwa"] is None

    zla = klient.put(sciezka, json={"punkty": {"komunikacja": 5}, "uzasadnienia_odstepstw": {"komunikacja": "  "}})
    assert zla.status_code == 422 and "wymaga uzasadnienia" in zla.json()["detail"]["bledy"][0]
    assert klient.get(sciezka).json()["wynik"]["srednia"] == "7.00"

    uzasadnienie = "Część ustaleń wymagała ponownego doprecyzowania."
    zmieniona = klient.put(sciezka, json={
        "punkty": {"komunikacja": 5}, "uzasadnienia_odstepstw": {"komunikacja": uzasadnienie},
    }).json()
    assert zmieniona["wynik"]["srednia"] == "5.00"
    assert metodyka(zmieniona)["uzasadnienie_odstepstwa"] == uzasadnienie

    aktualna = klient.put(sciezka, json={"odpowiedzi": {"komunikacja": {
        "1": {"wartosc": "rzadko"}, "4": {"wartosc": "rzadko"},
    }}}).json()
    assert metodyka(aktualna)["propozycja"] == 3
    assert metodyka(aktualna)["uzasadnienie_odstepstwa"] is None
    assert aktualna["wynik"]["srednia"] == "5.00"
    assert any("uzasadnij wybraną punktację" in b for b in aktualna["bledy"])

    wyczyszczona = klient.put(sciezka, json={"odpowiedzi": {"komunikacja": dict.fromkeys(("1", "2", "3", "4"))}})
    assert wyczyszczona.status_code == 200, wyczyszczona.text
    assert metodyka(wyczyszczona.json())["propozycja"] is None
    assert klient.get(f"/api/cykle/{c['id']}").json()["odpowiedzi"] == {}
