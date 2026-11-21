"""Uruchomienie: ``uv run caoo`` (port z CAOO_PORT, domyślnie 8001)."""

import uvicorn

from .ustawienia import Ustawienia


def main() -> None:
    uvicorn.run("caoo.app:app", host="127.0.0.1", port=Ustawienia.ze_srodowiska().port)


if __name__ == "__main__":
    main()
