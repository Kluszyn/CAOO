"""Baza SQLite aplikacji. Tabele dokładają kolejne tickety."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Annotated

from fastapi import Depends, Request

WERSJA_SCHEMATU = "1"


def polacz(plik: str | Path) -> sqlite3.Connection:
    plik = Path(plik)
    plik.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(plik, check_same_thread=False)
    conn.execute("CREATE TABLE IF NOT EXISTS meta (klucz TEXT PRIMARY KEY, wartosc TEXT NOT NULL)")
    conn.execute(
        "INSERT OR IGNORE INTO meta (klucz, wartosc) VALUES ('wersja_aplikacji', 'v1'), "
        "('wersja_schematu', ?)",
        (WERSJA_SCHEMATU,),
    )
    conn.commit()
    return conn


def _transakcja_cyklu(request: Request) -> Iterator[sqlite3.Connection]:
    """Jedno połączenie i blokada zapisu na cały odczyt, walidację i zapis cyklu."""
    plik = request.app.state.ustawienia.baza
    with closing(sqlite3.connect(plik, check_same_thread=False)) as baza, baza:
        baza.execute("BEGIN IMMEDIATE")
        yield baza


ZapisCyklu = Annotated[sqlite3.Connection, Depends(_transakcja_cyklu)]
