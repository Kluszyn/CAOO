# Cyfrowy Asystent Ocen Okresowych (CAOO)

Projekt na HackNation 2026. Asystent prowadzi przełożonego i ocenianego przez ocenę okresową w służbie cywilnej. Każda reguła, termin i kryterium ma adnotację źródłową z cytatem z przepisów.

## Struktura repozytorium

| Katalog | Zawartość |
|---|---|
| `caoo/` | Aplikacja: API w FastAPI (`backend/`), interfejs w React + Vite (`frontend/`) i konfiguracja (`konfiguracja/`) |
| `caoo-core/` | Wspólne mechanizmy: korpus źródeł, walidator adnotacji, kalendarz terminów, silnik skali, auth, `LlmProvider`, PDF |
| `materiały/` | Materiały źródłowe (przepisy, wzory arkuszy). Tylko do odczytu |
| `hipopotam/` | Statyczna strona z maskotką |
| `docs/` | Dokumentacja dla agentów i decyzje projektowe |

## Wymagania

- Python 3.11 lub nowszy
- [uv](https://docs.astral.sh/uv/) do zarządzania środowiskiem Pythona
- Node.js 20 lub nowszy z npm

Instalacja uv na macOS lub Linuksie:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## Uruchomienie

Aplikacja potrzebuje dwóch terminali: jeden na API, drugi na interfejs.

**Terminal 1: API** (http://127.0.0.1:8001, dokumentacja OpenAPI pod `/docs`)

```sh
cd caoo/backend
uv sync
uv run caoo
```

Pierwszy start trwa kilkanaście sekund, bo API buduje korpus z pełnym tekstem ustawy. Baza SQLite powstaje w `caoo/backend/dane/caoo.db`.

**Terminal 2: interfejs** (http://127.0.0.1:5173)

```sh
cd caoo/frontend
npm ci
npm run dev
```

Vite przekazuje zapytania `/api` do API na porcie 8001. Otwórz http://127.0.0.1:5173 w przeglądarce.

**Opcjonalnie: strona z maskotką**

```sh
cd hipopotam
python3 -m http.server 8080
```

Strona będzie pod http://127.0.0.1:8080.

## Konfiguracja i plik `.env`

Aplikacja działa bez żadnych zmiennych środowiskowych. Bez klucza API funkcje oparte na LLM (mapowanie, dowody, aktualizacja przepisów) korzystają z zapisanych odpowiedzi albo z trybu ekstrakcyjnego, bez modelu.

Aby włączyć model na żywo, utwórz plik `.env` w katalogu głównym repozytorium:

```sh
ANTHROPIC_API_KEY=twoj-klucz
```

Aplikacja nie wczytuje `.env` sama. Przed `uv run caoo` załaduj go do powłoki:

```sh
set -a; source ../../.env; set +a
uv run caoo
```

Plik `.env` jest w `.gitignore`. Nie dodawaj go do repozytorium.

Pozostałe zmienne nadpisują ustawienia domyślne:

| Zmienna | Domyślnie | Znaczenie |
|---|---|---|
| `CAOO_PORT` | `8001` | Port API |
| `CAOO_API` | `http://127.0.0.1:8001` | Adres API, do którego Vite przekazuje `/api` |
| `CAOO_DANE` | `caoo/backend/dane` | Katalog danych |
| `CAOO_BAZA` | `<CAOO_DANE>/caoo.db` | Plik bazy SQLite |
| `CAOO_MANIFEST` | `caoo/konfiguracja/korpus.yaml` | Manifest źródeł korpusu |
| `CAOO_REGULY` | `caoo/konfiguracja/reguly` | Katalog wersji reguł |
| `CAOO_KADRY` | `caoo/konfiguracja/kadry.yaml` | Fikcyjne dane kadrowe (demo) |
| `CAOO_PDF_FONT` | — | Font TTF z polskimi znakami do generowania PDF |

Pełna lista jest w [caoo/backend/src/caoo/ustawienia.py](caoo/backend/src/caoo/ustawienia.py).

## Testy

```sh
# core
cd caoo-core && uv sync && uv run pytest

# backend aplikacji
cd caoo/backend && uv sync && uv run pytest

# kontrola typów i build interfejsu
cd caoo/frontend && npm run build
```

## Więcej informacji

- [caoo/README.md](caoo/README.md): funkcje aplikacji i endpointy API
- [caoo-core/README.md](caoo-core/README.md): moduły core
- [AGENTS.md](AGENTS.md): zasady pracy zespołu, branche i tickety

## Licencja

Projekt jest udostępniony na licencji BSD 3-Clause. Szczegóły w pliku [LICENSE](LICENSE).
