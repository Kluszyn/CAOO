"""Termin cyklu oceny okresowej, nieobecności i ślad obliczenia (#15)."""

from datetime import date, timedelta

from caoo_core.kalendarz import Okres, RegulaTerminu, polacz_nieobecnosci, wyznacz_termin
from caoo_core.slad import KrokObliczenia
from pydantic import BaseModel

from .kadry import Profil
from .reguly import KrokTerminu, WersjaRegul
from .swieta import dni_wolne


class OkresNieobecnosci(BaseModel):
    od: date
    do: date
    dni_doliczone: int


class TerminOceny(BaseModel):
    data_zdarzenia: date
    zdarzenie: str
    termin_bazowy: date
    termin: date
    wersja_regul: str
    okresy_polaczone: list[OkresNieobecnosci]
    slad: list[KrokTerminu]


def termin_oceny(p: Profil, w: WersjaRegul) -> TerminOceny | None:
    """Wylicz termin 24-miesięcznego cyklu dla znanego zdarzenia początkowego."""
    if "cykl_oceny" not in w.terminy or p.zatrudnienie is None:
        return None
    ostatnia = max(p.poprzednie_oceny, key=lambda o: o.data_sporzadzenia, default=None)
    if ostatnia is not None:
        # Ponowna ocena po negatywnej ma osobny przebieg (#17).
        if ostatnia.wynik == "negatywna":
            return None
        data_zdarzenia = ostatnia.data_sporzadzenia
        zdarzenie = "sporządzenie poprzedniej oceny na piśmie"
    elif p.zatrudnienie.rodzaj == "mianowanie" and p.mianowanie is not None:
        data_zdarzenia = p.mianowanie.data
        zdarzenie = "mianowanie"
    else:
        data_zdarzenia = p.zatrudnienie.data_nawiazania
        zdarzenie = "nawiązanie stosunku pracy"

    bazowy = wyznacz_termin(data_zdarzenia, w.regula_terminu("cykl_oceny"))
    slad = list(bazowy.slad)
    swieta = w.regula("termin.swieta-demo")
    slad.append(KrokObliczenia(w.regula_core(swieta.id), swieta.opis))

    poczatek_cyklu = data_zdarzenia + timedelta(days=1)
    okresy = [
        Okres(max(n.od, poczatek_cyklu), n.do)
        for n in p.nieobecnosci
        if n.usprawiedliwiona and n.do >= poczatek_cyklu
    ]
    ostatni_rok = max((o.do.year for o in okresy), default=data_zdarzenia.year)
    polaczone = polacz_nieobecnosci(
        okresy,
        regula=w.regula_core("termin.ciaglosc-nieobecnosci"),
        dni_wolne=dni_wolne(w.terminy, data_zdarzenia.year, ostatni_rok),
    )
    slad.extend(polaczone.slad)

    prog = w.regula_terminu("prog_nieobecnosci")
    regula_roboczego = w.regula_core("termin.dzien-wolny")
    przesuniecie = RegulaTerminu(0, "dni", regula_roboczego, regula_roboczego)

    def dzien_roboczy(data: date):
        return wyznacz_termin(data, przesuniecie, dni_wolne=dni_wolne(w.terminy, data.year, data.year + 1))

    termin = bazowy.data
    roboczy = dzien_roboczy(termin)
    uwzglednione = []
    for okres in polaczone.okresy:
        if okres.od > roboczy.data:
            break
        granica_miesiaca = wyznacz_termin(okres.od, prog).data
        doliczone = 0
        if okres.do > granica_miesiaca:
            doliczone = (okres.do - okres.od).days + 1
            przed = termin
            termin += timedelta(days=doliczone)
            roboczy = dzien_roboczy(termin)
            opis = (
                f"Nieobecność {okres.od}–{okres.do} przekracza miesiąc. "
                f"Doliczono {doliczone} dni: od {przed} do {termin}."
            )
        else:
            opis = f"Nieobecność {okres.od}–{okres.do} nie przekracza miesiąca. Doliczono 0 dni."
        uwzglednione.append(OkresNieobecnosci(od=okres.od, do=okres.do, dni_doliczone=doliczone))
        slad.append(KrokObliczenia(prog.regula, opis))

    # Pierwszy krok core to przesunięcie o zero dni. Potrzebny jest tylko ewentualny skok dnia wolnego.
    slad.extend(roboczy.slad[1:])
    return TerminOceny(
        data_zdarzenia=data_zdarzenia, zdarzenie=zdarzenie,
        termin_bazowy=bazowy.data, termin=roboczy.data, wersja_regul=w.wersja,
        okresy_polaczone=uwzglednione, slad=[w.krok_terminu(krok) for krok in slad],
    )
