"""Katalog bez danych kadrowych i projekcja grafu dla jednego cyklu oceny."""

from collections import defaultdict
from itertools import pairwise

from caoo_core.skala import oblicz_wynik
from caoo_core.zrodla import normalizuj

from .cykl import Cykl
from .graf_model import (
    GrafOceny, KatalogGrafu, KonfiguracjaGrafu, PropozycjaRozwoju,
    Relacja, Sciezka, Status, Szczegoly, Wezel, waliduj_graf,
)
from .kadry import Profil
from .kryteria import wykaz_kryteriow
from .reguly import WersjaRegul


def zbuduj_katalog(w: WersjaRegul, konf: KonfiguracjaGrafu) -> KatalogGrafu:
    wykaz = wykaz_kryteriow(w)
    skala = w.konfiguracja_skali()
    if konf.poziom_roboczy not in skala.dozwolone_oceny:
        raise ValueError("Roboczy poziom grafu musi należeć do skali ocen cząstkowych.")
    spoza_katalogu = (konf.slowa_uzupelniajace.keys() | w.kryteria["slowa_kluczowe"].keys()) - wykaz.keys()
    if spoza_katalogu:
        raise ValueError(f"Reguły dopasowania wskazują kompetencje spoza katalogu: {sorted(spoza_katalogu)}")

    katalog = KatalogGrafu(
        schemat=konf.schemat,
        wersja_katalogu=konf.wersja_katalogu,
        wersja_regul=w.wersja,
        kompetencje=[Wezel(
            id=f"kompetencja:{k.id}", typ="kompetencja", nazwa=k.nazwa.capitalize(),
            status="potwierdzone", zrodlo="Rozporządzenie w sprawie ocen okresowych, załącznik nr 1",
            uzasadnienie="Kryterium z wykazu kryteriów oceny.", adnotacje=k.adnotacje,
        ) for k in wykaz.values()],
        poziomy=[Wezel(
            id=f"poziom:{p}", typ="poziom", nazwa=f"{p} pkt · {oblicz_wynik([p], skala).poziom}",
            status="potwierdzone", zrodlo="Rozporządzenie w sprawie ocen okresowych, § 11",
            uzasadnienie="Punktacja cząstkowa z ustawionej wersji reguł.", wartosc=p,
            adnotacje=w.regula("skala.oceny-czastkowe").adnotacje,
        ) for p in skala.dozwolone_oceny],
        dzialania=[Wezel(
            id=f"dzialanie:{d.id}", typ="dzialanie", nazwa=d.nazwa, status="potwierdzone",
            zrodlo="Poradnik – oceny okresowe w służbie cywilnej",
            uzasadnienie="Przykład działania rozwojowego w poradniku.", adnotacje=d.adnotacje,
        ) for d in konf.dzialania],
    )
    waliduj_graf(katalog.schemat, katalog.wezly(), [])
    return katalog


def _dopasuj(tekst: str, slowa: dict[str, list[str]]) -> dict[str, str]:
    tekst = normalizuj(tekst).casefold()
    trafienia = {}
    for kid, hasla in slowa.items():
        wspolne = next((h for h in hasla if h and normalizuj(h).casefold() in tekst), None)
        if wspolne:
            trafienia[kid] = wspolne
    return trafienia


def _wezly_oceny(
    kid: str, c: Cykl, poziom_roboczy: int, poziomy: dict[int | None, Wezel], zatwierdzil: str | None,
) -> tuple[Wezel, Wezel, Wezel, Wezel]:
    roboczy = poziomy[poziom_roboczy]
    punktacja = c.oceny.get(kid)
    zapisany = poziomy.get(punktacja)
    zatwierdzona = zapisany is not None and c.zatwierdzona
    status: Status = "potwierdzone" if zatwierdzona else "propozycja"
    wymagany = Wezel(
        id=f"wymagany:{kid}", typ="poziom", nazwa=f"{roboczy.nazwa} (roboczy)",
        status="propozycja", zrodlo="Konfiguracja grafu i skala ocen cząstkowych",
        uzasadnienie="W opisie stanowiska brakuje liczbowego poziomu. Konfiguracja podaje cel do weryfikacji.",
        adnotacje=roboczy.adnotacje, wartosc=poziom_roboczy, katalog_id=roboczy.id,
    )
    wynik = Wezel(
        id=f"wynik:{kid}", typ="wynik", nazwa=f"Ocena cząstkowa · {punktacja} pkt" if zapisany else "Brak oceny cząstkowej",
        status=status, zrodlo=f"Bieżący cykl oceny #{c.id}",
        uzasadnienie="Punkty zapisane przez oceniającego." if zapisany else "Cykl nie zawiera punktów dla tego kryterium.",
        zatwierdzil=zatwierdzil if zatwierdzona else None,
        data_zatwierdzenia=c.data_zatwierdzenia if zatwierdzona else None,
        wartosc=punktacja if zapisany else None,
    )
    aktualny = Wezel(
        id=f"aktualny:{kid}", typ="poziom", nazwa=zapisany.nazwa if zapisany else "Poziom nieustalony",
        status=wynik.status, zrodlo=wynik.zrodlo,
        uzasadnienie="Poziom odpowiada zapisanej ocenie cząstkowej." if zapisany else "Do ustalenia poziomu potrzebna jest ocena cząstkowa.",
        zatwierdzil=wynik.zatwierdzil, data_zatwierdzenia=wynik.data_zatwierdzenia,
        wartosc=wynik.wartosc, katalog_id=zapisany.id if zapisany else None,
        adnotacje=zapisany.adnotacje if zapisany else [],
    )
    roznica = max(0, poziom_roboczy - punktacja) if zapisany and punktacja is not None else None
    luka = Wezel(
        id=f"luka:{kid}", typ="luka", nazwa=f"Luka do celu · {roznica} pkt" if roznica is not None else "Luka nieustalona",
        status="propozycja", zrodlo="Roboczy poziom wymagany i wynik bieżącej oceny",
        uzasadnienie="Różnica między poziomem roboczym a aktualnym, z minimum 0." if roznica is not None else "Obliczenie luki wymaga zapisanego poziomu aktualnego.",
        wartosc=roznica,
    )
    return wymagany, wynik, aktualny, luka


def zbuduj_graf(
    c: Cykl, p: Profil, w: WersjaRegul, konf: KonfiguracjaGrafu, katalog: KatalogGrafu, *,
    zatwierdzil: str | None = None,
) -> GrafOceny:
    kompetencje = {k.id.split(":", 1)[1]: k for k in katalog.kompetencje}
    poziomy = {k.wartosc: k for k in katalog.poziomy}
    slowa = {
        kid: [*w.kryteria["slowa_kluczowe"].get(kid, []), *konf.slowa_uzupelniajace.get(kid, [])]
        for kid in kompetencje
    }
    zrodlo_osp = "Opis stanowiska w danych kadrowych"
    wezly: dict[str, Wezel] = {
        "stanowisko": Wezel(
            id="stanowisko", typ="stanowisko", nazwa=p.stanowisko, status="potwierdzone",
            zrodlo=zrodlo_osp, uzasadnienie=p.opis_stanowiska.cel,
        ),
    }
    relacje: dict[tuple[str, str, str], Relacja] = {}
    podstawy: dict[str, list[str]] = defaultdict(list)
    sciezki = []

    def polacz(typ: str, z: str, do: str, szczegoly: Szczegoly, powod: str | None = None) -> None:
        dane = szczegoly.model_dump(include=set(Szczegoly.model_fields))
        if powod is not None:
            dane["uzasadnienie"] = powod
        relacje[typ, z, do] = Relacja(typ=typ, z=z, do=do, **dane)

    def wczytaj_elementy(typ: str, teksty: list[str], zrodlo: str, klucze: dict[str, list[str]], dzien=None):
        trafienia = {}
        for i, tekst in enumerate(teksty, 1):
            ident = f"{typ}:{i}"
            dopasowane = _dopasuj(tekst, klucze)
            status: Status = "dopasowane" if dopasowane else "potwierdzone"
            powod = "Frazy z katalogu: " + ", ".join(sorted(set(dopasowane.values()))) if dopasowane else "Brak trafienia w słowach kluczowych katalogu."
            wezel = Wezel(
                id=ident, typ=typ, nazwa=tekst, status=status, zrodlo=zrodlo,
                uzasadnienie=powod, data_zrodla=dzien, nieprzypisany=not dopasowane,
            )
            wezly[ident] = wezel
            trafienia[ident] = dopasowane
            for kid in dopasowane:
                podstawy[kid].append(ident)
            if typ in ("wymaganie", "zadanie"):
                polacz("zawiera", "stanowisko", ident, wezly["stanowisko"], "Element pochodzi z opisu stanowiska.")
        return trafienia

    wymagania = wczytaj_elementy("wymaganie", p.opis_stanowiska.wymagania, zrodlo_osp, slowa)
    zadania = wczytaj_elementy("zadanie", p.opis_stanowiska.zadania, zrodlo_osp, slowa)
    ostatnia = max(p.poprzednie_oceny, key=lambda o: o.data_sporzadzenia, default=None)
    if ostatnia:
        pominiete = set(konf.slowa_pomijane_w_zaleceniach)
        slowa_historyczne = {kid: [h for h in hasla if h not in pominiete] for kid, hasla in slowa.items()}
        wczytaj_elementy("dowod", ostatnia.zalecenia, "Zalecenia z poprzedniej oceny", slowa_historyczne, ostatnia.data_sporzadzenia)

    def brak(typ: str, etykieta: str) -> str:
        ident = f"{typ}:brak"
        wezly[ident] = Wezel(
            id=ident, typ=typ, nazwa=etykieta, status="propozycja", zrodlo=zrodlo_osp,
            uzasadnienie="Powiązanie wymaga ręcznej weryfikacji na podstawie opisu stanowiska.",
            nieprzypisany=True,
        )
        return ident

    zadania_dla = defaultdict(list)
    for zadanie, trafienia in zadania.items():
        for kid, haslo in trafienia.items():
            zadania_dla[kid].append((zadanie, f"Fraza „{haslo}” powiązała zadanie z kompetencją."))

    wybrane = {k.id for k in [*c.obowiazkowe, *c.dodatkowe]}
    minimum = konf.poziom_roboczy
    propozycje = []
    for kid, kompetencja in kompetencje.items():
        if kid in zadania_dla or kid in wybrane:
            wymagany, wynik, aktualny, luka = _wezly_oceny(kid, c, minimum, poziomy, zatwierdzil)
            koniec = [wymagany, wynik, aktualny, luka]
            wezly.update((k.id, k) for k in koniec)
            typy = ("ma_poziom_wymagany", "zestawiono_z", "wskazuje_poziom", "porownano_z_celem")
            for typ, (z, do) in zip(typy, pairwise([kompetencja, *koniec])):
                polacz(typ, z.id, do.id, do)

            wspolne = [ident for ident, trafienia in wymagania.items() if kid in trafienia]
            for zadanie, powod in zadania_dla.get(kid) or [(brak("zadanie", "Brak dopasowanego zadania"), None)]:
                polacz("odpowiada", zadanie, kompetencja.id, wezly[zadanie], powod)
                for wymaganie in wspolne or [brak("wymaganie", "Brak dopasowanego wymagania")]:
                    meta = wezly[wymaganie] if zadanie in zadania else wezly[zadanie]
                    powod_wymagania = "To samo kryterium odpowiada frazom w zadaniu i wymaganiu." if zadanie in zadania and wymaganie in wymagania else "Powiązanie zadania z wymaganiem pozostaje do ustalenia."
                    polacz("wspiera", wymaganie, zadanie, meta, powod_wymagania)
                    sciezki.append(Sciezka(wezly=(wymaganie, zadanie, kompetencja.id, *[k.id for k in koniec])))

        punkty = c.oceny.get(kid)
        if podstawy[kid] and (punkty is None or punkty < minimum):
            propozycje.append(PropozycjaRozwoju(
                kompetencja_id=kompetencja.id, podstawy=podstawy[kid], status="propozycja",
                zrodlo="Opis stanowiska lub zalecenia z ostatniej oceny",
                uzasadnienie=("Zapisany wynik jest poniżej roboczego celu." if punkty is not None else
                              "Sprawdź tę kompetencję w bieżącej ocenie na podstawie dopasowanych informacji."),
                adnotacje=kompetencja.adnotacje,
            ))

    osobne_wezly, osobne_relacje = list(wezly.values()), list(relacje.values())
    waliduj_graf(katalog.schemat, [*katalog.wezly(), *osobne_wezly], osobne_relacje, sciezki)
    return GrafOceny(
        cykl_id=c.id, katalog=katalog, wezly=osobne_wezly, relacje=osobne_relacje,
        sciezki=sciezki, propozycje=propozycje,
    )
