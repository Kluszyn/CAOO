"""Korpus: manifest dokumentów z folderu „materiały” i lista plików pominiętych."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml
from caoo_core.zrodla import Kategoria, Korpus, OpisDokumentu


@dataclass(frozen=True)
class Dokument:
    doc_id: str
    nazwa: str
    plik: Path
    kategoria: Kategoria
    doc_version: str | None = None


@dataclass(frozen=True)
class Pominiety:
    wzorzec: str
    powod: str


@dataclass
class KorpusV1:
    korpus: Korpus
    dokumenty: dict[str, Dokument]
    katalog_zrodel: Path
    pominiete: list[Pominiety] = field(default_factory=list)

    def pominiety(self, plik: Path) -> Pominiety | None:
        """Reguła z `pominiete`, która obejmuje plik (ścieżka względem folderu źródeł)."""
        wzgledna = plik.resolve().relative_to(self.katalog_zrodel)
        return next((p for p in self.pominiete if wzgledna.match(p.wzorzec)), None)


def zaladuj(manifest: str | Path) -> KorpusV1:
    sciezka = Path(manifest)
    dane = yaml.safe_load(sciezka.read_text(encoding="utf-8")) or {}
    baza = sciezka.parent

    dokumenty: dict[str, Dokument] = {}
    for p in dane.get("dokumenty") or []:
        d = Dokument(
            doc_id=p["doc_id"],
            nazwa=p.get("nazwa", p["doc_id"]),
            plik=(baza / p["plik"]).resolve(),
            kategoria=Kategoria(p.get("kategoria", "OBJAŚNIENIE_MATERIAŁU")),
            doc_version=None if p.get("doc_version") is None else str(p["doc_version"]),
        )
        if d.doc_id in dokumenty:
            raise ValueError(f"powtórzone doc_id w manifeście: {d.doc_id}")
        dokumenty[d.doc_id] = d

    korpus = Korpus.z_plikow(
        OpisDokumentu(doc_id=d.doc_id, plik=d.plik, doc_version=d.doc_version)
        for d in dokumenty.values()
    )
    katalog_zrodel = (baza / dane["katalog_zrodel"]).resolve()
    return KorpusV1(
        korpus=korpus,
        dokumenty=dokumenty,
        katalog_zrodel=katalog_zrodel,
        pominiete=[Pominiety(**p) for p in dane.get("pominiete") or []],
    )
