from dataclasses import replace
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

from caoo.app import utworz_aplikacje


@pytest.fixture
def cykl_demo(ustawienia, tmp_path):
    with TestClient(utworz_aplikacje(replace(ustawienia, baza=tmp_path / "cykle.db"))) as klient:
        token = klient.post("/api/auth/login", json={"login": "admin", "hasło": "demo"}).json()["token"]
        klient.headers.update({"Authorization": f"Bearer {token}"})  # API wymaga roli (#10)
        r = klient.post("/api/pracownicy/K-006/cykle", json={"dodatkowe": ["myslenie-analityczne"]})
        assert r.status_code == 201, r.text
        c = r.json()
        yield klient, f"/api/cykle/{c['id']}", c


def test_zmiana_oceniajacego_i_ponowne_ustalenie_kryteriow(cykl_demo):
    klient, url, pierwotny = cykl_demo
    odpowiedzi = {
        str(i): {"wartosc": "zwykle", "dowod": "Notatka ze spotkania"} for i in (1, 2, 3)
    }
    assert klient.put(f"{url}/ocena", json={
        "punkty": {"komunikacja": 5},
        "odpowiedzi": {"komunikacja": odpowiedzi},
        "uzasadnienia_odstepstw": {"komunikacja": "Ustalenia trzeba było powtórzyć."},
    }).status_code == 200
    assert klient.post(f"{url}/potwierdzenie", json={"data": "2024-09-21"}).status_code == 200

    r = klient.post(f"{url}/zmiany-oceniajacego", json={
        "nowy_oceniajacy": "Nowa osoba testowa", "data": "2025-01-20",
    })
    assert r.status_code == 200, r.text
    c = r.json()
    assert c["zmiany_oceniajacego"][0]["aktualny"] == "Nowa osoba testowa"
    for pole in ("arkusz", "obowiazkowe", "dodatkowe", "termin", "termin_oceny"):
        assert c[pole] == pierwotny[pole]
    assert c["oceny"] == {"komunikacja": 5}
    assert c["odpowiedzi"] == {"komunikacja": odpowiedzi}
    assert c["data_potwierdzenia"] == "2024-09-21"

    r = klient.post(f"{url}/zmiany-stanowiska", json={
        "data": "2025-03-20", "stanowisko": "Kierowniczka zespołu", "istotna_zmiana": True, "arkusz": "B",
    })
    assert r.status_code == 200, r.text
    c = r.json()
    z = c["zmiany_stanowiska"][0]
    assert z["tryb"] == "PONOWNE_KRYTERIA"
    assert z["termin_kryteriow"]["termin"] == "2025-04-22"
    assert z["termin_kryteriow"]["slad"][0]["adnotacje"][0]["lokalizator"] == "§ 9 ust. 1"
    assert c["termin_oceny"] == "2026-11-27"
    assert c["obowiazkowe"] == pierwotny["obowiazkowe"]
    assert klient.put(f"{url}/ocena", json={"punkty": {"komunikacja": 7}}).status_code == 409
    assert klient.post(f"{url}/potwierdzenie", json={"data": "2025-03-20"}).status_code == 409

    opcje = klient.get(f"{url}/ponowne-kryteria")
    assert opcje.status_code == 200, opcje.text
    assert opcje.json()["arkusz"] == "B"
    assert "zarzadzanie-ludzmi" in {k["id"] for k in opcje.json()["obowiazkowe"]}
    assert klient.post(f"{url}/ponowne-kryteria", json={
        "data": "2025-03-19", "dodatkowe": [],
    }).status_code == 422

    r = klient.post(f"{url}/ponowne-kryteria", json={"data": "2025-03-21", "dodatkowe": ["kreatywnosc"]})
    assert r.status_code == 200, r.text
    c = r.json()
    z = c["zmiany_stanowiska"][0]
    assert c["arkusz"] == "B"
    assert c["dodatkowe"][0]["id"] == "kreatywnosc"
    assert c["termin_oceny"] == "2026-11-27"
    assert c["data_potwierdzenia"] is None
    assert c["oceny"] == {}
    assert c["odpowiedzi"] == {}
    assert c["uzasadnienia_odstepstw"] == {}
    assert z["ustalono_kryteria"] == "2025-03-21"
    assert z["poprzednie_kryteria"]["punkty"] == {"komunikacja": 5}
    assert z["poprzednie_kryteria"]["odpowiedzi"] == {"komunikacja": odpowiedzi}
    assert klient.put(f"{url}/ocena", json={"punkty": {"komunikacja": 7}}).status_code == 409
    assert klient.post(f"{url}/potwierdzenie", json={"data": "2025-03-21"}).status_code == 200
    assert klient.put(f"{url}/ocena", json={"punkty": {"komunikacja": 7}}).status_code == 200

    r = klient.post(f"{url}/zmiany-stanowiska", json={
        "data": "2025-04-06", "stanowisko": "Kierowniczka wydziału", "istotna_zmiana": True,
    })
    assert r.status_code == 200, r.text
    pozna = r.json()["zmiany_stanowiska"][-1]
    assert (pozna["tryb"], pozna["ocena_od"], pozna["ocena_do"]) == (
        "OCENA_PRZY_ZMIANIE", "2025-03-30", "2025-05-06",
    )


def test_wniosek_granica_decyzja_termin_i_kopia_pisma(cykl_demo):
    klient, url, _ = cykl_demo
    wnioski = f"{url}/wnioski-zmiany-terminu"
    prog = klient.post(wnioski, json={"data": "2025-03-20", "proponowany_termin": "2025-04-01"})
    assert prog.status_code == 422, prog.text
    assert "ponad 6 miesięcy" in prog.json()["detail"]

    po_progu = klient.post(wnioski, json={"data": "2025-03-21", "proponowany_termin": "2025-04-01"})
    assert po_progu.status_code == 201, po_progu.text
    odrzucenie = klient.post(f"{wnioski}/1/decyzja", json={"data": "2025-03-22", "uwzglednij": False})
    assert odrzucenie.status_code == 200, odrzucenie.text
    assert odrzucenie.json()["wnioski_zmiany_terminu"][0]["status"] == "ODRZUCONY"
    assert odrzucenie.json()["termin_oceny"] == "2026-11-27"
    assert odrzucenie.json()["zawiadomienia"] == []

    nowy_oceniajacy = "Michał Testowy"
    r = klient.post(f"{url}/zmiany-oceniajacego", json={"data": "2026-11-20", "nowy_oceniajacy": nowy_oceniajacy})
    assert r.status_code == 200, r.text
    r = klient.post(wnioski, json={
        "data": "2026-11-21", "proponowany_termin": "2026-12-15", "uzasadnienie": "Organizacja spotkania.",
    })
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["termin_oceny"] == "2026-11-27"
    assert c["wnioski_zmiany_terminu"][1]["status"] == "OCZEKUJE"
    dokument = f"{url}/zawiadomienia/2/pdf"
    assert klient.get(dokument).status_code == 404

    decyzja = f"{wnioski}/2/decyzja"
    r = klient.post(decyzja, json={"data": "2026-11-20", "uwzglednij": True})
    assert r.status_code == 422, r.text
    poprawna = {"data": "2026-11-21", "uwzglednij": True}
    r = klient.post(decyzja, json=poprawna)
    assert r.status_code == 200, r.text
    c = r.json()
    assert c["termin_oceny"] == "2026-12-15"
    assert c["wnioski_zmiany_terminu"][1]["status"] == "UWZGLEDNIONY"
    assert len(c["zawiadomienia"]) == 1
    assert c["zawiadomienia"][0]["oceniajacy"] == nowy_oceniajacy
    assert klient.post(decyzja, json=poprawna).status_code == 409

    proces = klient.get("/api/pracownicy/K-006/proces").json()["termin_oceny"]
    assert proces["termin"] == "2026-12-15"
    assert proces["slad"][-1]["adnotacje"][0]["lokalizator"] == "§ 8"

    pdf = klient.get(dokument)
    assert pdf.status_code == 200, pdf.text
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.headers["content-disposition"].startswith("attachment;")
    tresc = PdfReader(BytesIO(pdf.content)).pages[0].extract_text()
    assert "Zawiadomienie o zmianie terminu" in tresc
    assert "27.11.2026" in tresc and "15.12.2026" in tresc
    assert nowy_oceniajacy in tresc
    assert "Podpis oceniającego" in tresc


def test_pytanie_o_inicjatywe_oceniajacego_jest_w_panelu_zrodel(klient):
    w, = klient.get("/api/reguly").json()
    pytanie = next(r for r in w["reguly"] if r["id"] == "termin.pytanie-zmiana-oceniajacego")
    assert "Jak udokumentować podstawę" in pytanie["opis"]
    assert pytanie["adnotacje"][0]["adnotacja"]["kategoria"] == "BRAK_ŹRÓDŁA"
