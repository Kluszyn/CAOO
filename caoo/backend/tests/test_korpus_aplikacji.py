from pathlib import Path

from caoo_core.zrodla import StatusEkstrakcji


def _fragmenty(korpus_aplikacji, doc_id):
    return [f for f in korpus_aplikacji.korpus.fragmenty if f.doc_id == doc_id]


def test_ustawa_rozporzadzenie_i_poradnik_z_lokalizatorami_i_stronami(korpus_aplikacji):
    rozp = {f.lokalizator for f in _fragmenty(korpus_aplikacji, "rozp-oceny-okresowe")}
    assert {"§ 3 ust. 1", "§ 7", "§ 11 ust. 1"} <= rozp
    ustawa = {f.lokalizator for f in _fragmenty(korpus_aplikacji, "ustawa-sc")}
    assert any(l and l.startswith("art. 81") for l in ustawa)
    assert any(l and l.startswith("art. 83") for l in ustawa)
    for doc in ("rozp-oceny-okresowe", "ustawa-sc", "poradnik-oceny-okresowe"):
        fr = _fragmenty(korpus_aplikacji, doc)
        assert fr and all(f.strona for f in fr), doc


def test_wzory_pdf_bez_tekstu_sa_oznaczone(korpus_aplikacji):
    statusy = {w.doc_id: w for w in korpus_aplikacji.korpus.raport}
    for doc in ("wzor-a", "wzor-b", "wzor-c"):
        assert statusy[doc].status is StatusEkstrakcji.BRAK_TEKSTU
        assert "brak tekstu" in statusy[doc].komunikat


def test_kazdy_plik_z_materialow_jest_zrodlem_albo_pominiety(korpus_aplikacji):
    zrodla = {d.plik for d in korpus_aplikacji.dokumenty.values()}
    nieznane = [
        p for p in korpus_aplikacji.katalog_zrodel.rglob("*")
        if p.is_file() and p.name not in {".DS_Store", "Thumbs.db"}
        and p.resolve() not in zrodla and korpus_aplikacji.pominiety(p) is None
    ]
    assert nieznane == []


def test_przyklady_i_pliki_tymczasowe_sa_pominiete(korpus_aplikacji):
    k = korpus_aplikacji.katalog_zrodel
    assert korpus_aplikacji.pominiety(next((k / "5. Przykładowe wypełnione formularze oceny").glob("*.docx")))
    assert korpus_aplikacji.pominiety(next(k.rglob("~$*")))
    assert korpus_aplikacji.pominiety(k / "1. Akty prawne/3. Ustawa o służbie cywilnej - cała.pdf") is None
