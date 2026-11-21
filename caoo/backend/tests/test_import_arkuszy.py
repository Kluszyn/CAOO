from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest

from caoo.import_arkuszy import FOLDER_PRZYKLADOW, ZapytanieImportu, analizuj_arkusz
from caoo.reguly import wczytaj_wersje
from caoo.ustawienia import KATALOG_APLIKACJI, Ustawienia


KATALOG = KATALOG_APLIKACJI.parent / "materiały" / FOLDER_PRZYKLADOW
(REGULY,) = wczytaj_wersje(Ustawienia.ze_srodowiska().reguly)
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _przyklad(numer: int) -> Path:
    return next(plik for plik in KATALOG.iterdir() if plik.name.partition(")")[0].lstrip("0") == str(numer))


def _raport(numer: int, wybory=None):
    return analizuj_arkusz(KATALOG, ZapytanieImportu(plik=_przyklad(numer).name, wybory=wybory or {}), REGULY)


def test_przyklad_12_zglasza_bledna_date_i_czeka_na_korekte():
    raport = _raport(12)
    data, = [d for d in raport.daty if d.zapis == "14.046.2024"]
    assert data.data is None
    assert any(u.kod == "BLEDNA_DATA" and "14.046.2024" in u.komunikat for u in raport.uwagi)
    assert raport.gotowy is False


def test_przyklad_09_wykrywa_rozbieznosc_sredniej_i_sprawdza_wynik():
    raport = _raport(9)
    assert (raport.srednia_wpisana, raport.srednia_przeliczona) == ("3,3", "3,0")
    assert (raport.wynik_wpisany, raport.wynik_przeliczony) == ("negatywna", "negatywna")
    assert raport.podstawa_wyniku and raport.podstawa_wyniku.lokalizator == "§ 11 ust. 1 pkt 4"
    assert any(u.kod == "ROZBIEZNOSC_SREDNIEJ" and u.poziom == "ostrzezenie" for u in raport.uwagi)


@pytest.mark.parametrize("numer", [1, 2, 3, 4, 5])
def test_zmiany_sledzone_wymagaja_wyboru(numer):
    raport = _raport(numer)
    assert raport.liczba_sledzonych_zmian and raport.zmiany
    assert not raport.gotowy and all(z.wybrana is None for z in raport.zmiany)
    assert any(z.pole == "Data" and z.przed and z.po for z in raport.zmiany)
    po = _raport(numer, {z.id: "po" for z in raport.zmiany})
    assert po.gotowy and all(z.wybrana == "po" for z in po.zmiany)
    if numer == 1:
        przed = _raport(numer, {z.id: "przed" for z in raport.zmiany})
        assert any(d.zapis == "12.05.2023" for d in przed.daty)
        assert any(d.zapis == "16.05.2023" for d in po.daty)


def test_przyklad_13_zglasza_roznice_w_nazwie_kryterium():
    raport = _raport(13)
    uwaga, = [u for u in raport.uwagi if u.kod == "NAZWA_KRYTERIUM"]
    assert uwaga.poziom == "ostrzezenie"
    assert "Negocjacje" in uwaga.komunikat and "Negocjowanie" in uwaga.komunikat


@pytest.mark.parametrize(("numer", "srednia", "wynik"), [
    (6, "6", "pozytywna"), (7, "3,75", "negatywna"), (8, "5,4", "pozytywna"),
])
def test_pdf_rozpoznaje_kolumny_oceny_oraz_wynik(numer, srednia, wynik):
    raport = _raport(numer)
    assert raport.gotowy and raport.uwagi == []
    assert (raport.srednia_wpisana, raport.srednia_przeliczona) == (srednia, srednia)
    assert (raport.wynik_wpisany, raport.wynik_przeliczony) == (wynik, wynik)


def _zapisz_arkusz_z_edytowanymi_punktami(katalog: Path, *, zmiana_sledzona: bool) -> str:
    zrodlo = _przyklad(1)
    with ZipFile(zrodlo) as f:
        doc = ET.fromstring(f.read("word/document.xml"))
    wiersz = next(w for w in doc.iter(W + "tr")
                  if len(w.findall(W + "tc")) == 6
                  and "".join(w.findall(W + "tc")[0].itertext()).strip() == "Zarządzanie ludźmi")
    komorki = wiersz.findall(W + "tc")
    pierwszy = komorki[1].find(W + "p")
    tekst = ET.SubElement(pierwszy, W + "del") if zmiana_sledzona else pierwszy
    ET.SubElement(ET.SubElement(tekst, W + "r"), W + ("delText" if zmiana_sledzona else "t")).text = "X"
    if zmiana_sledzona:
        piaty = komorki[3].find(W + "p")
        piaty.clear()
        ET.SubElement(ET.SubElement(ET.SubElement(piaty, W + "ins"), W + "r"), W + "t").text = "X"
    with ZipFile(katalog / zrodlo.name, "w") as f:
        f.writestr("word/document.xml", ET.tostring(doc, encoding="utf-8"))
    return zrodlo.name


def test_zmiana_punktow_pozwala_wybrac_stan_przed_lub_po(tmp_path):
    nazwa = _zapisz_arkusz_z_edytowanymi_punktami(tmp_path, zmiana_sledzona=True)
    raport = analizuj_arkusz(tmp_path, ZapytanieImportu(plik=nazwa), REGULY)
    zmiana, = [z for z in raport.zmiany if z.id.startswith("punkty:")]
    assert (zmiana.przed, zmiana.po, raport.srednia_przeliczona) == (("1",), ("5",), None)
    for wybor, oczekiwane in (("przed", 1), ("po", 5)):
        wybory = {z.id: "po" for z in raport.zmiany} | {zmiana.id: wybor}
        wybrany = analizuj_arkusz(tmp_path, ZapytanieImportu(plik=nazwa, wybory=wybory), REGULY)
        assert wybrany.kryteria[0].punkty == oczekiwane


def test_podwojne_zaznaczenie_wstrzymuje_wynik(tmp_path):
    nazwa = _zapisz_arkusz_z_edytowanymi_punktami(tmp_path, zmiana_sledzona=False)
    raport = analizuj_arkusz(tmp_path, ZapytanieImportu(plik=nazwa), REGULY)
    assert raport.srednia_przeliczona is None
    assert any(u.kod == "ZAZNACZENIA_KRYTERIUM" for u in raport.uwagi)
    assert raport.gotowy is False


def test_api_listuje_tylko_przyklady_i_weryfikuje_wybor(klient):
    r = klient.get("/api/import/arkusze")
    assert r.status_code == 200
    assert len(r.json()) == 13 and "Thumbs.db" not in r.json()
    r = klient.post("/api/import/arkusze/raport", json={"plik": _przyklad(9).name})
    assert r.status_code == 200 and r.json()["srednia_przeliczona"] == "3,0"
    r = klient.post("/api/import/arkusze/raport", json={
        "plik": _przyklad(9).name, "wybory": {"data:nieaktualne pole": "po"},
    })
    assert r.status_code == 422
    assert klient.post("/api/import/arkusze/raport", json={"plik": "Thumbs.db"}).status_code == 404
