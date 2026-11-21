import subprocess
from datetime import date
from pathlib import Path

import pytest
from caoo_core.skala import oblicz_wynik

from caoo.reguly import wczytaj_wersje, wersja_na_dzien
from caoo.ustawienia import KATALOG_APLIKACJI, Ustawienia

UST = Ustawienia.ze_srodowiska()


def test_wersja_regul_z_data_obowiazywania():
    (w,) = wczytaj_wersje(UST.reguly)
    assert w.wersja == "przepisy-2016-v1" and w.obowiazuje_od == date(2016, 4, 23)
    assert wersja_na_dzien([w], date(2026, 11, 21)) is w
    with pytest.raises(LookupError):
        wersja_na_dzien([w], date(2016, 4, 22))


def test_skala_par_11_ma_source_ref_z_cytatem():
    (w,) = wczytaj_wersje(UST.reguly)
    skala = w.regula("skala.oceny-czastkowe")
    (ref,) = skala.adnotacje
    assert ref.kategoria.value == "PRZEPIS" and ref.lokalizator.startswith("§ 11")
    assert "znacznie poniżej oczekiwań – 1 punkt" in ref.cytat_doslowny


@pytest.mark.parametrize("oceny, wynik, regula", [
    ([9, 9, 9, 1], "NEGATYWNA", "wynik.negatywna-1pkt"),
    ([5, 5, 5], "POZYTYWNA", "wynik.pozytywna"),
    ([5, 3, 5, 3], "NEGATYWNA", "wynik.negatywna-poziom"),
])
def test_konfiguracja_dziala_w_silniku_skali_core(oceny, wynik, regula):
    (w,) = wczytaj_wersje(UST.reguly)
    r = oblicz_wynik(oceny, w.konfiguracja_skali())
    assert (r.wynik, r.regula_rozstrzygajaca.id) == (wynik, regula)


def _waliduj(*pliki: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["caoo-waliduj", "--manifest", str(UST.manifest), *map(str, pliki)],
        capture_output=True, text=True, cwd=KATALOG_APLIKACJI / "backend",
    )


def test_walidator_przechodzi_dla_konfiguracji_wersji_1():
    r = _waliduj(*sorted((KATALOG_APLIKACJI / "konfiguracja" / "reguly").glob("*.yaml")))
    assert r.returncode == 0, r.stdout


def test_walidator_pada_przy_cytacie_spoza_korpusu(tmp_path):
    plik = tmp_path / "zla.yaml"
    plik.write_text(
        (UST.reguly / "przepisy-2016.yaml").read_text(encoding="utf-8")
        .replace("e) znacznie powyżej oczekiwań –\n          9 punktów;", "e) znacznie powyżej oczekiwań –\n          10 punktów;"),
        encoding="utf-8",
    )
    r = _waliduj(plik)
    assert r.returncode == 1
    assert "BRAK_POKRYCIA [skala.oceny-czastkowe]" in r.stdout
