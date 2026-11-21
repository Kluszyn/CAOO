from base64 import b64encode
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import date
from io import BytesIO
import sqlite3

from caoo_core.pdf import hash_tresci
from pypdf import PdfReader

from caoo import obieg_api, ocena_cyklu
from caoo.cykl import Cykl, _wczytaj, utworz_tabele
from caoo.kadry import MockKadry
from caoo.kryteria import do_ustalenia
from caoo.reguly import wczytaj_wersje


def test_wersje_wydruk_doreczenie_i_odwolanie(klient, monkeypatch):
    dzis = date(2024, 8, 28)

    class ZegarDemonstracyjny(date):
        @classmethod
        def today(cls):
            return dzis

    monkeypatch.setattr(obieg_api, "date", ZegarDemonstracyjny)
    monkeypatch.setattr(ocena_cyklu, "date", ZegarDemonstracyjny)
    rozpoczecie = klient.post("/api/pracownicy/K-004/cykle", json={})
    assert rozpoczecie.status_code == 201, rozpoczecie.text
    cykl_id = rozpoczecie.json()["id"]
    assert klient.post(f"/api/cykle/{cykl_id}/potwierdzenie", json={"data": "2024-08-14"}).status_code == 200
    zmiana = klient.post(f"/api/cykle/{cykl_id}/zmiany-oceniajacego", json={
        "data": "2024-08-21", "nowy_oceniajacy": "Zofia Łącka",
    })
    assert zmiana.status_code == 200, zmiana.text
    adres = f"/api/cykle/{cykl_id}/ocena"
    w1, w2, w3 = (f"{adres}/wersje/{n}" for n in (1, 2, 3))
    stan = klient.get(adres).json()
    assert stan["status"] == "ROBOCZA"
    punkty = dict.fromkeys((k["id"] for k in stan["kryteria"]), 3)
    dowod = "Notatka robocza z przygotowania raportu."
    odpowiedzi_pomocnicze = {"komunikacja": {
        str(i): {"wartosc": "czasem", "dowod": dowod if i == 1 else None} for i in (1, 2, 3)
    }}
    assert klient.put(adres, json={"odpowiedzi": odpowiedzi_pomocnicze}).status_code == 200
    odstepstwo = "Raport przekazano z istotnymi brakami."
    tresc = {
        "punkty": punkty, "uzasadnienie": "Ocena poniżej oczekiwań.",
        "uzasadnienia_odstepstw": {"komunikacja": odstepstwo},
        "data_sporzadzenia": "2024-08-28", "zatwierdz": True,
    }
    with ThreadPoolExecutor(max_workers=2) as watki:
        odpowiedzi = list(watki.map(lambda _: klient.put(adres, json=tresc), range(2)))
    assert sorted(r.status_code for r in odpowiedzi) == [200, 409]
    zatwierdzona = next(r.json() for r in odpowiedzi if r.status_code == 200)
    (wersja1,) = zatwierdzona["historia"]
    assert zatwierdzona["status"] == "ZATWIERDZONA"
    assert len(wersja1["hash"]) == 64
    assert wersja1["data_sporzadzenia"] == tresc["data_sporzadzenia"]
    assert klient.put(adres, json={"odpowiedzi": odpowiedzi_pomocnicze}).status_code == 409

    doreczenie = {"data_zapoznania": "2024-08-31", "rejestrujacy": "Anna Kowalska"}
    assert klient.post(f"{w1}/doreczenie", json=doreczenie).status_code == 409
    wydruk1 = klient.post(f"{w1}/wydruk")
    assert wydruk1.status_code == 200, wydruk1.text
    assert wydruk1.headers["content-type"] == "application/pdf"
    strony = [p.extract_text() for p in PdfReader(BytesIO(wydruk1.content)).pages]
    assert all(wersja1["hash"] in strona for strona in strony)
    assert all(dowod not in strona and odstepstwo not in strona for strona in strony)
    assert all(tekst in strony[0] for tekst in ("ARKUSZ C", "Paweł", "Zofia Łącka"))
    assert "CZĘŚĆ II" in strony[1]
    assert "Ocena poniżej oczekiwań." in strony[-1]
    assert "Data zapoznania" in strony[-1] and "podpis ocenianego" in strony[-1]
    assert klient.get(adres).json()["status"] == "WYDRUKOWANA"
    assert klient.post(f"{w1}/wydruk").content == wydruk1.content

    assert klient.post(f"{w1}/nowa").status_code == 204
    assert klient.put(adres, json={"numer_wersji": 1, "punkty": punkty}).status_code == 409
    assert klient.put(adres, json={"numer_wersji": 1, "odpowiedzi": odpowiedzi_pomocnicze}).status_code == 409
    po_korekcie = klient.get(adres).json()
    komunikacja = next(k["metodyka"] for k in po_korekcie["kryteria"] if k["id"] == "komunikacja")
    assert komunikacja["pytania"][0]["odpowiedz"]["dowod"] == dowod
    assert komunikacja["uzasadnienie_odstepstwa"] == odstepstwo
    assert klient.get(f"{w1}/pdf").content == wydruk1.content
    assert klient.post(f"{w1}/doreczenie", json=doreczenie).status_code == 409
    stan = klient.put(adres, json=tresc | {"numer_wersji": 2, "wnioski_rozwojowe": "Szkolenie."}).json()
    assert [w["numer"] for w in stan["historia"]] == [2, 1]
    assert stan["historia"][0]["hash"] != wersja1["hash"]
    assert klient.get(f"{w2}/pdf").status_code == 404
    assert klient.post(f"{w2}/doreczenie", json=doreczenie).status_code == 409
    wydruk2 = klient.post(f"{w2}/wydruk")
    assert wydruk2.status_code == 200, wydruk2.text
    dzis = date(2024, 8, 31)
    assert klient.post(f"{w2}/doreczenie", json=doreczenie | {"skan_base64": "?"}).status_code == 422
    assert klient.post(f"{w2}/doreczenie", json=doreczenie | {"data_zapoznania": "2024-08-27"}).status_code == 422
    skan = b64encode(wydruk2.content).decode()
    assert klient.post(f"{w2}/doreczenie", json=doreczenie | {"skan_base64": skan}).status_code == 204
    assert klient.post(f"{w2}/doreczenie", json=doreczenie).status_code == 409
    assert klient.get(f"{w2}/skan").content == wydruk2.content
    assert klient.get(f"{w1}/skan").status_code == 404
    assert klient.post(f"{w2}/nowa").status_code == 409
    stan = klient.get(adres).json()
    assert stan["status"] == "PODPISANA_I_DORECZONA"
    v = stan["historia"][0]
    assert v["data_sporzadzenia"] == "2024-08-28"
    assert v["termin_odwolania"]["data_odniesienia"] == "2024-08-31"
    assert v["termin_odwolania"]["data"] == "2024-09-09"  # 7 dni, potem przesunięcie z soboty
    assert v["ponowna_ocena"]["data"] == "2025-03-01"  # po upływie 6 miesięcy 28 lutego
    assert v["termin_rozpatrzenia"] is None
    dzis = date(2024, 9, 10)
    assert klient.post(f"{w2}/odwolanie", json={"data_wniesienia": "2024-09-10"}).status_code == 422
    assert klient.post(f"{w2}/odwolanie", json={"data_wniesienia": "2024-09-09"}).status_code == 204
    stan = klient.get(adres).json()
    assert stan["historia"][0]["termin_rozpatrzenia"]["data"] == "2024-09-23"

    decyzja = {"data_rozpatrzenia": "2024-09-23", "rozpatrzyl": "Dyrektor Generalny"}
    dzis = date(2024, 9, 23)
    odpowiedz = klient.post(f"{w2}/rozpatrzenie", json=decyzja | {"rozstrzygniecie": "PONOWNE_SPORZADZENIE"})
    assert odpowiedz.status_code == 204, odpowiedz.text
    stan = klient.get(adres).json()
    assert stan["status"] == "ROBOCZA" and stan["numer_wersji"] == 3
    assert stan["wynik"] is None and stan["wnioski_rozwojowe"] == ""
    komunikacja = next(k["metodyka"] for k in stan["kryteria"] if k["id"] == "komunikacja")
    assert all(p["odpowiedz"] is None for p in komunikacja["pytania"])
    assert komunikacja["uzasadnienie_odstepstwa"] is None
    zapisane_wersje = klient.get(f"/api/cykle/{cykl_id}").json()["wersje_oceny"]
    assert zapisane_wersje[0]["odpowiedzi"] == odpowiedzi_pomocnicze
    assert zapisane_wersje[0]["uzasadnienia_odstepstw"] == {"komunikacja": odstepstwo}
    assert stan["historia"][0]["odwolanie"]["rozstrzygniecie"] == "PONOWNE_SPORZADZENIE"
    dzis = date(2024, 9, 24)
    assert klient.put(adres, json={
        "punkty": dict.fromkeys(punkty, 7), "numer_wersji": 3,
        "data_sporzadzenia": "2024-09-24", "zatwierdz": True,
    }).status_code == 200
    assert klient.post(f"{w3}/wydruk").status_code == 200
    dzis = date(2024, 9, 25)
    assert klient.post(f"{w3}/doreczenie", json=doreczenie | {"data_zapoznania": "2024-09-25"}).status_code == 204
    dzis = date(2024, 9, 28)
    assert klient.post(f"{w3}/odwolanie", json={"data_wniesienia": "2024-09-28"}).status_code == 204
    dzis = date(2024, 10, 1)
    assert klient.post(f"{w3}/rozpatrzenie", json=decyzja | {
        "data_rozpatrzenia": "2024-10-01", "rozstrzygniecie": "ZMIANA_OCENY",
    }).status_code == 204
    stan = klient.get(adres).json()
    assert stan["status"] == "ROBOCZA" and stan["numer_wersji"] == 4
    assert stan["powod_wersji"] == "ZMIANA_PO_ODWOLANIU"
    assert all(k["punkty"] == 7 for k in stan["kryteria"])
    assert klient.get(f"{w1}/pdf").content == wydruk1.content
    assert klient.get(f"{w2}/pdf").content == wydruk2.content


def test_uzupelnienie_migawki_starej_zatwierdzonej_oceny(ustawienia):
    w = wczytaj_wersje(ustawienia.reguly)[0]
    kadry = MockKadry.z_pliku(ustawienia.kadry)
    kryteria = do_ustalenia(kadry.profil("K-001"), "A", w)
    stary = Cykl(
        id=1, pracownik_id="K-001", arkusz="A", obowiazkowe=kryteria.obowiazkowe,
        dodatkowe=[], termin=kryteria.termin, rozpoczal="oceniający", rozpoczeto="2024-09-01T09:00:00Z",
        wersja_regul=w.wersja, status="W_TOKU", oceny=dict.fromkeys((k.id for k in kryteria.obowiazkowe), 7),
        zatwierdzona=True, data_zatwierdzenia=date(2024, 10, 12),
    )
    with closing(sqlite3.connect(":memory:")) as baza:
        utworz_tabele(baza)
        baza.execute(
            "INSERT INTO cykle VALUES (?, ?, ?)",
            (stary.id, stary.pracownik_id, stary.model_dump_json(exclude_unset=True)),
        )
        ocena_cyklu.uzupelnij_zatwierdzone(baza, [w], kadry)
        ocena_cyklu.uzupelnij_zatwierdzone(baza, [w], kadry)
        (wersja,) = _wczytaj(baza, stary.id).wersje_oceny
        assert wersja.data_zatwierdzenia == wersja.tresc.data_sporzadzenia == stary.data_zatwierdzenia
        assert wersja.hash == hash_tresci(wersja.tresc.model_dump(mode="json"))
        assert wersja.tresc.wynik == "POZYTYWNA"
