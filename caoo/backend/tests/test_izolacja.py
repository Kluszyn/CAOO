"""Korpus aplikacji czyta wyłącznie folder „materiały”."""

from pathlib import Path

from caoo.ustawienia import KATALOG_APLIKACJI


def test_korpus_czyta_wylacznie_materialy(korpus_aplikacji):
    zrodla = (KATALOG_APLIKACJI.parent / "materiały").resolve()
    assert korpus_aplikacji.katalog_zrodel == zrodla
    for w in korpus_aplikacji.korpus.raport:
        assert Path(w.plik).resolve().is_relative_to(zrodla), w.plik
