"""Ustawienia aplikacji. Każde można nadpisać zmienną środowiskową."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

KATALOG_APLIKACJI = Path(__file__).resolve().parents[3]  # caoo/
PORT_DOMYSLNY = 8001


@dataclass(frozen=True)
class Ustawienia:
    port: int
    manifest: Path
    reguly: Path
    kadry: Path
    rozmowa: Path
    pierwsza_ocena: Path
    role: Path
    aktualizacja: Path
    baza: Path
    metodyki: Path = KATALOG_APLIKACJI / "konfiguracja" / "metodyki"
    domyslna_metodyka: str = "robocza-v1"
    graf: Path = KATALOG_APLIKACJI / "konfiguracja" / "graf.yaml"
    mapowanie: Path = KATALOG_APLIKACJI / "konfiguracja" / "mapowanie.yaml"
    dowody: Path = KATALOG_APLIKACJI / "konfiguracja" / "dowody.yaml"
    log_llm: Path | None = None
    """Log wywołań LLM (#21); domyślnie obok bazy."""
    rekomendacje: Path = KATALOG_APLIKACJI / "konfiguracja" / "rekomendacje.yaml"

    @classmethod
    def ze_srodowiska(cls) -> Ustawienia:
        konf = KATALOG_APLIKACJI / "konfiguracja"
        dane = Path(os.environ.get("CAOO_DANE", KATALOG_APLIKACJI / "backend" / "dane"))
        return cls(
            port=int(os.environ.get("CAOO_PORT", PORT_DOMYSLNY)),
            manifest=Path(os.environ.get("CAOO_MANIFEST", konf / "korpus.yaml")),
            reguly=Path(os.environ.get("CAOO_REGULY", konf / "reguly")),
            kadry=Path(os.environ.get("CAOO_KADRY", konf / "kadry.yaml")),
            rozmowa=Path(os.environ.get("CAOO_ROZMOWA", konf / "rozmowa.yaml")),
            pierwsza_ocena=Path(os.environ.get("CAOO_PIERWSZA_OCENA", konf / "pierwsza-ocena.yaml")),
            aktualizacja=Path(os.environ.get("CAOO_AKTUALIZACJA", konf / "aktualizacja.yaml")),
            role=Path(os.environ.get("CAOO_ROLE", konf / "role.yaml")),
            baza=Path(os.environ.get("CAOO_BAZA", dane / "caoo.db")),
            metodyki=Path(os.environ.get("CAOO_METODYKI", konf / "metodyki")),
            domyslna_metodyka=os.environ.get("CAOO_METODYKA", "robocza-v1"),
            graf=Path(os.environ.get("CAOO_GRAF", konf / "graf.yaml")),
            rekomendacje=Path(os.environ.get("CAOO_REKOMENDACJE", konf / "rekomendacje.yaml")),
        )
