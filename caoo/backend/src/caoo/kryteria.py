"""Kryteria oceny i termin ich ustalenia (#12).

Czyste funkcje: profil z API kadrowego + wersja reguł → kryteria obowiązkowe arkusza (§ 4),
propozycje kryteriów dodatkowych z zadań stanowiska (oznaczone „propozycja”), walidacja wyboru
(najwyżej 3, § 5 ust. 2) i termin ustalenia kryteriów (§ 7) liczony kalendarzem z caoo-core.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from caoo_core.kalendarz import wyznacz_termin
from caoo_core.zrodla import SourceRef
from pydantic import BaseModel

from .kadry import Profil
from .reguly import KrokTerminu, WersjaRegul
from .swieta import dni_wolne


class Kryterium(BaseModel):
    id: str
    nazwa: str
    adnotacje: list[SourceRef]


class PropozycjaKryterium(Kryterium):
    oznaczenie: Literal["propozycja"] = "propozycja"
    uzasadnienie: str
    zadania: list[str]
    regula: str


class TerminKryteriow(BaseModel):
    data_zdarzenia: date
    zdarzenie: str
    termin: date
    regula: str
    slad: list[KrokTerminu]


class KryteriaDoUstalenia(BaseModel):
    arkusz: Literal["A", "B", "C"]
    obowiazkowe: list[Kryterium]
    regula_obowiazkowych: str
    dostepne_dodatkowe: list[Kryterium]
    propozycje: list[PropozycjaKryterium]
    limit_dodatkowych: int
    regula_limitu: str
    termin: TerminKryteriow | None
    braki: list[str]
    wersja_regul: str


class BladWyboru(ValueError):
    pass


def wykaz_kryteriow(w: WersjaRegul) -> dict[str, Kryterium]:
    return {k["id"]: Kryterium.model_validate(k) for k in w.kryteria["wykaz"]}


def termin_ustalenia(p: Profil, w: WersjaRegul) -> tuple[TerminKryteriow | None, list[str]]:
    """§ 7: 30 dni od poprzedniej oceny / zatrudnienia na czas nieokreślony, 7 dni po negatywnej."""
    if p.poprzednie_oceny:
        ostatnia = max(p.poprzednie_oceny, key=lambda o: o.data_sporzadzenia)
        nazwa = "kryteria_po_negatywnej" if ostatnia.wynik == "negatywna" else "kryteria_po_ocenie"
        zdarzenie = f"sporządzenie poprzedniej oceny ({ostatnia.wynik})"
        dzien = ostatnia.data_sporzadzenia
    elif p.zatrudnienie is not None:
        nazwa = "kryteria_po_ocenie"
        zdarzenie = "zatrudnienie (brak poprzedniej oceny)"
        dzien = p.zatrudnienie.data_nawiazania
    else:
        return None, ["zatrudnienie.data_nawiazania albo poprzednia ocena"]

    konf = w.regula_terminu(nazwa)
    wynik = wyznacz_termin(
        dzien, konf, dni_wolne=dni_wolne(w.terminy, dzien.year, dzien.year + 1)
    )
    return (
        TerminKryteriow(
            data_zdarzenia=dzien,
            zdarzenie=zdarzenie,
            termin=wynik.data,
            regula=konf.regula.id,
            slad=[w.krok_terminu(k) for k in wynik.slad],
        ),
        [],
    )


def propozycje(p: Profil, wykluczone: set[str], w: WersjaRegul) -> list[PropozycjaKryterium]:
    """Kryteria dodatkowe, których słowa kluczowe pasują do zadań stanowiska (najwyżej limit)."""
    wykaz = wykaz_kryteriow(w)
    k = w.kryteria
    trafienia = []
    for kid, slowa in k["slowa_kluczowe"].items():
        if kid in wykluczone:
            continue
        zadania = [
            z for z in p.opis_stanowiska.zadania if any(s.lower() in z.lower() for s in slowa)
        ]
        if zadania:
            trafienia.append((len(zadania), kid, zadania))
    trafienia.sort(key=lambda t: (-t[0], t[1]))
    return [
        PropozycjaKryterium(
            **wykaz[kid].model_dump(),
            uzasadnienie=f"Pasuje do {n} zadań z opisu stanowiska.",
            zadania=zadania,
            regula=k["regula_propozycji"],
        )
        for n, kid, zadania in trafienia[: k["limit_dodatkowych"]]
    ]


def do_ustalenia(p: Profil, arkusz: Literal["A", "B", "C"], w: WersjaRegul) -> KryteriaDoUstalenia:
    wykaz = wykaz_kryteriow(w)
    ob = w.kryteria["obowiazkowe"][arkusz]
    obowiazkowe = [wykaz[k] for k in ob["kryteria"]]
    ids_ob = set(ob["kryteria"])
    termin, braki = termin_ustalenia(p, w)
    return KryteriaDoUstalenia(
        arkusz=arkusz,
        obowiazkowe=obowiazkowe,
        regula_obowiazkowych=ob["regula"],
        dostepne_dodatkowe=[k for kid, k in wykaz.items() if kid not in ids_ob],
        propozycje=propozycje(p, ids_ob, w),
        limit_dodatkowych=w.kryteria["limit_dodatkowych"],
        regula_limitu=w.kryteria["regula_limitu"],
        termin=termin,
        braki=braki,
        wersja_regul=w.wersja,
    )


def sprawdz_wybor(dodatkowe: list[str], arkusz: Literal["A", "B", "C"], w: WersjaRegul) -> list[str]:
    """Błędy wyboru kryteriów dodatkowych; pusta lista = poprawny."""
    bledy = []
    wykaz = wykaz_kryteriow(w)
    limit = w.kryteria["limit_dodatkowych"]
    ob = set(w.kryteria["obowiazkowe"][arkusz]["kryteria"])
    if len(dodatkowe) > limit:
        bledy.append(f"Najwyżej {limit} kryteria dodatkowe (§ 5 ust. 2); wybrano {len(dodatkowe)}.")
    if len(set(dodatkowe)) != len(dodatkowe):
        bledy.append("Kryterium dodatkowe wybrano więcej niż raz.")
    for k in dodatkowe:
        if k not in wykaz:
            bledy.append(f"Kryterium {k} nie występuje w wykazie (zał. 1).")
        elif k in ob:
            bledy.append(f"Kryterium {k} jest obowiązkowe dla arkusza {arkusz}.")
    return bledy
