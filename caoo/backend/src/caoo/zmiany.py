"""Stan zmian stanowiska, oceniającego i terminu w cyklu oceny V1."""

from datetime import date
from typing import Annotated, Literal

from caoo_core.zrodla import SourceRef
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from .kryteria import Kryterium, TerminKryteriow
from .metodyka import Odpowiedz


Arkusz = Literal["A", "B", "C"]
Nazwa = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
Uzasadnienie = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)]


class ZgloszenieStanowiska(BaseModel):
    data: date
    stanowisko: Nazwa
    istotna_zmiana: bool
    arkusz: Arkusz | None = None


class ZapisKryteriow(BaseModel):
    arkusz: str
    obowiazkowe: list[Kryterium]
    dodatkowe: list[Kryterium]
    punkty: dict[str, int]
    oczekiwania: dict[str, str]
    uzasadnienie: str | None
    potwierdzenie: date | None
    potwierdzil: str | None
    wersja_metodyki: str | None = None
    odpowiedzi: dict[str, dict[int, Odpowiedz]] = Field(default_factory=dict)
    uzasadnienia_odstepstw: dict[str, str] = Field(default_factory=dict)


class ZmianaStanowiska(BaseModel):
    data: date
    stanowisko: str
    arkusz: Arkusz
    tryb: Literal["ODNOTOWANO", "PONOWNE_KRYTERIA", "OCENA_PRZY_ZMIANIE"]
    regula: str | None = None
    termin_kryteriow: TerminKryteriow | None = None
    ocena_od: date | None = None
    ocena_do: date | None = None
    ustalono_kryteria: date | None = None
    poprzednie_kryteria: ZapisKryteriow | None = None


class PonowneKryteria(BaseModel):
    data: date
    dodatkowe: list[str] = Field(default_factory=list)


class ZgloszenieOceniajacego(BaseModel):
    data: date
    nowy_oceniajacy: Nazwa


class ZmianaOceniajacego(BaseModel):
    data: date
    poprzedni: str | None
    aktualny: str


class ZlozenieWniosku(BaseModel):
    data: date
    proponowany_termin: date
    uzasadnienie: Uzasadnienie = ""


class DecyzjaWniosku(BaseModel):
    data: date
    uwzglednij: bool


class WniosekZmianyTerminu(ZlozenieWniosku):
    id: int
    status: Literal["OCZEKUJE", "UWZGLEDNIONY", "ODRZUCONY"] = "OCZEKUJE"
    data_decyzji: date | None = None


class ZawiadomienieZmianyTerminu(BaseModel):
    model_config = ConfigDict(frozen=True)

    wniosek_id: int
    data: date
    oceniany: str
    jednostka: str
    oceniajacy: str
    poprzedni_termin: date
    nowy_termin: date
    wersja_regul: str
    adnotacje: list[SourceRef]
