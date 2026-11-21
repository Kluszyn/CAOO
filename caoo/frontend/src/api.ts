import type { GrafOceny } from "./grafTypes";

export interface SourceRef {
  doc_id?: string | null;
  lokalizator?: string | null;
  strona?: number | null;
  "cytat_dosłowny"?: string | null;
  kategoria: string;
  uzasadnienie?: string | null;
  "zatwierdził"?: string | null;
}

export interface RegulaPanelu {
  id: string;
  nazwa: string | null;
  opis: string;
  adnotacje: { adnotacja: SourceRef; dokument: string | null; plik: string | null; cytat_w_korpusie: boolean | null }[];
}

export interface WersjaRegul {
  wersja: string;
  obowiazuje_od: string;
  plik: string;
  data_obowiazywania: RegulaPanelu;
  reguly: RegulaPanelu[];
  rozbieznosci: (RegulaPanelu & { rozbieznosc: { opis: string; uzasadnienie?: string; zastosowana: number | null } })[];
}

export interface Zrodla {
  dokumenty: { doc_id: string; nazwa: string; kategoria: string; status: string; fragmenty: number; komunikat: string | null }[];
  pominiete: { wzorzec: string; powod: string }[];
}

async function json<T>(r: Response): Promise<T> {
  if (!r.ok) throw new Error(`API ${r.status}`);
  return r.json() as Promise<T>;
}

// --- Role (#10): przełącznik roli w nagłówku, konta demo z konfiguracja/role.yaml ------------

export const ROLE = [
  { login: "oceniajacy", nazwa: "oceniający – naczelnik (K-002)" },
  { login: "zastepca", nazwa: "oceniający w zastępstwie – radca (K-003)" },
  { login: "kierownik", nazwa: "oceniający – kierownik urzędu (K-001)" },
  { login: "oceniany", nazwa: "oceniany – główny specjalista (K-004)" },
  { login: "kadry", nazwa: "kadry / zarządzający" },
  { login: "admin", nazwa: "administrator" },
] as const;

const KLUCZ_ROLI = "caoo-rola";
let token: string | null = null;

export const zapisanaRola = (): string => sessionStorage.getItem(KLUCZ_ROLI) ?? ROLE[0].login;

export async function ustawRole(login: string): Promise<void> {
  const r = await fetch("/api/auth/login", {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ login, "hasło": "demo" }),
  });
  token = (await json<{ token: string }>(r)).token;
  sessionStorage.setItem(KLUCZ_ROLI, login);
}

/** fetch z nagłówkiem roli – uprawnienia egzekwuje API. */
const zapytaj = (url: string, init: RequestInit = {}) =>
  fetch(url, { ...init, headers: { ...(init.headers as Record<string, string> | undefined), ...(token ? { Authorization: `Bearer ${token}` } : {}) } });

/** Plik z API (np. PDF) z tokenem roli – zwykły link nie wysyła nagłówka. */
export async function otworzPlik(url: string): Promise<void> {
  const r = await zapytaj(url);
  if (!r.ok) throw new Error(`API ${r.status}`);
  window.open(URL.createObjectURL(await r.blob()), "_blank");
}

export const pobierzReguly = () => zapytaj("/api/reguly").then((r) => json<WersjaRegul[]>(r));
export const pobierzZrodla = () => zapytaj("/api/zrodla").then((r) => json<Zrodla>(r));

export type WersjaPolaArkusza = "przed" | "po";

export interface ZmianaArkusza {
  id: string;
  pole: string;
  lokalizator: string;
  przed: string[];
  po: string[];
  wybrana: WersjaPolaArkusza | null;
}

export interface RaportArkusza {
  plik: string;
  wersja_regul: string;
  demo: true;
  gotowy: boolean;
  liczba_sledzonych_zmian: number;
  zmiany: ZmianaArkusza[];
  daty: { lokalizator: string; zapis: string; data: string | null }[];
  kryteria: { lokalizator: string; nazwa: string; punkty: number | null }[];
  uwagi: { kod: string; poziom: "blad" | "ostrzezenie"; lokalizator: string; komunikat: string }[];
  srednia_wpisana: string | null;
  srednia_przeliczona: string | null;
  poziom_wpisany: string | null;
  poziom_przeliczony: string | null;
  wynik_wpisany: string | null;
  wynik_przeliczony: string | null;
  podstawa_wyniku: SourceRef | null;
}

export const pobierzArkusze = (signal: AbortSignal) =>
  zapytaj("/api/import/arkusze", { signal }).then(jsonLubBlad<string[]>);

export const sprawdzArkusz = (plik: string, wybory: Record<string, WersjaPolaArkusza>, signal: AbortSignal) =>
  zapytaj("/api/import/arkusze/raport", {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ plik, wybory }), signal,
  }).then(jsonLubBlad<RaportArkusza>);

export interface PracownikSkrot {
  id: string;
  imie: string;
  nazwisko: string;
  stanowisko: string;
  komorka: string;
  przelozony_id: string | null;
  demo: true;
}

export interface Profil extends PracownikSkrot {
  jednostka: string;
  kierownik_urzedu: boolean;
  liczba_podleglych: number | null;
  korpus: "urzednik" | "pracownik" | null;
  zatrudnienie: { rodzaj: string; data_nawiazania: string; data_na_stanowisku: string } | null;
  mianowanie: { data: string; stopien: string } | null;
  pierwsza_praca_w_sluzbie_cywilnej: boolean | null;
  przelozony: string | null;
  zastepstwa: { zastepca_id: string; zastepca: string | null; od: string; do: string; powod: string }[];
  opis_stanowiska: { cel: string; zadania: string[]; wymagania: string[] };
  poprzednie_oceny: {
    data_sporzadzenia: string; data_zapoznania: string; wynik: string; poziom: string; srednia: number; zalecenia: string[];
  }[];
  nieobecnosci: { od: string; do: string; rodzaj: string; usprawiedliwiona: boolean }[];
  uwagi: { data: string; rodzaj: string; tresc: string }[];
  zrodlo_przykladu: string;
  znacznik: string;
}

export interface ListaPracownikow {
  demo: true;
  znacznik: string;
  jednostka: string;
  pracownicy: PracownikSkrot[];
}

export const pobierzPracownikow = () => zapytaj("/api/kadry/pracownicy").then((r) => json<ListaPracownikow>(r));
export const pobierzProfil = (id: string) => zapytaj(`/api/kadry/pracownicy/${id}`).then((r) => json<Profil>(r));

export interface WyborProcesu {
  proces: "OKRESOWA" | "PIERWSZA" | "NIE_MOZNA_USTALIC";
  arkusz: "A" | "B" | "C" | null;
  przeslanki: { opis: string; spelniona: boolean | null; regula: string; adnotacje: SourceRef[] }[];
  braki: string[];
  komunikat: string;
  wersja_regul: string;
  termin_oceny: TerminOceny | null;
}

export const pobierzProces = (id: string) => zapytaj(`/api/pracownicy/${id}/proces`).then((r) => json<WyborProcesu>(r));

export interface Kryterium { id: string; nazwa: string; adnotacje: SourceRef[] }
export interface PropozycjaKryterium extends Kryterium { oznaczenie: "propozycja"; uzasadnienie: string; zadania: string[]; regula: string }
export interface TerminKryteriow {
  data_zdarzenia: string; zdarzenie: string; termin: string; regula: string;
  slad: { regula: string; opis: string; adnotacje: SourceRef[] }[];
}
export interface TerminOceny {
  data_zdarzenia: string;
  zdarzenie: string;
  termin_bazowy: string;
  termin: string;
  wersja_regul: string;
  okresy_polaczone: { od: string; do: string; dni_doliczone: number }[];
  slad: TerminKryteriow["slad"];
}
export interface KryteriaDoUstalenia {
  arkusz: string; obowiazkowe: Kryterium[]; regula_obowiazkowych: string; dostepne_dodatkowe: Kryterium[];
  propozycje: PropozycjaKryterium[]; limit_dodatkowych: number; regula_limitu: string;
  termin: TerminKryteriow | null; braki: string[]; wersja_regul: string;
}
export interface Cykl {
  id: number; pracownik_id: string; arkusz: string; obowiazkowe: Kryterium[]; dodatkowe: Kryterium[];
  termin: TerminKryteriow | null; rozpoczal: string; rozpoczeto: string; wersja_regul: string;
  potwierdzil: string | null; data_potwierdzenia: string | null; status: string;
  termin_oceny: string | null;
  zmiany_stanowiska: ZmianaStanowiska[];
  zmiany_oceniajacego: { data: string; poprzedni: string | null; aktualny: string }[];
  wnioski_zmiany_terminu: WniosekZmianyTerminu[];
  zawiadomienia: { wniosek_id: number; data: string; poprzedni_termin: string; nowy_termin: string }[];
  zatwierdzona: boolean;
}

export interface ZmianaStanowiska {
  data: string;
  stanowisko: string;
  arkusz: "A" | "B" | "C";
  tryb: "ODNOTOWANO" | "PONOWNE_KRYTERIA" | "OCENA_PRZY_ZMIANIE";
  termin_kryteriow: TerminKryteriow | null;
  ocena_od: string | null;
  ocena_do: string | null;
  ustalono_kryteria: string | null;
}

export interface WniosekZmianyTerminu {
  id: number;
  data: string;
  proponowany_termin: string;
  uzasadnienie: string;
  status: "OCZEKUJE" | "UWZGLEDNIONY" | "ODRZUCONY";
  data_decyzji: string | null;
}

async function sprawdz(r: Response): Promise<Response> {
  if (r.ok) return r;
  const d = await r.json().catch(() => null);
  const det = d?.detail;
  const walidacja = Array.isArray(det) ? det.map((x) => x.msg).join(" ") : null;
  throw new Error(typeof det === "string" ? det : det?.bledy?.join(" ") ?? det?.komunikat ?? walidacja ?? `API ${r.status}`);
}

async function jsonLubBlad<T>(r: Response): Promise<T> {
  return (await sprawdz(r)).json() as Promise<T>;
}

export const pobierzKryteria = (id: string) => zapytaj(`/api/pracownicy/${id}/kryteria`).then((r) => jsonLubBlad<KryteriaDoUstalenia>(r));
export const pobierzCykle = (id: string) => zapytaj(`/api/pracownicy/${id}/cykle`).then((r) => json<Cykl[]>(r));
export const rozpocznijCykl = (id: string, dodatkowe: string[]) =>
  zapytaj(`/api/pracownicy/${id}/cykle`, {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ dodatkowe }),
  }).then((r) => jsonLubBlad<Cykl>(r));
export const potwierdzCykl = (cyklId: number, data: string) =>
  zapytaj(`/api/cykle/${cyklId}/potwierdzenie`, {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ data }),
  }).then((r) => jsonLubBlad<Cykl>(r));

function zmienCykl(id: number, operacja: string, body: object): Promise<Cykl> {
  return zapytaj("/api/cykle/" + id + "/" + operacja, {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
  }).then(jsonLubBlad<Cykl>);
}

export const zmienStanowisko = (id: number, body: {
  data: string; stanowisko: string; istotna_zmiana: boolean; arkusz: ZmianaStanowiska["arkusz"];
}) => zmienCykl(id, "zmiany-stanowiska", body);
export const zmienOceniajacego = (id: number, data: string, nowy_oceniajacy: string) =>
  zmienCykl(id, "zmiany-oceniajacego", { data, nowy_oceniajacy });
export const pobierzPonowneKryteria = (id: number, signal: AbortSignal) =>
  zapytaj("/api/cykle/" + id + "/ponowne-kryteria", { signal }).then(jsonLubBlad<KryteriaDoUstalenia>);
export const zapiszPonowneKryteria = (id: number, data: string, dodatkowe: string[]) =>
  zmienCykl(id, "ponowne-kryteria", { data, dodatkowe });
export const zlozWniosek = (id: number, data: string, proponowany_termin: string, uzasadnienie: string) =>
  zmienCykl(id, "wnioski-zmiany-terminu", { data, proponowany_termin, uzasadnienie });
export const rozpatrzWniosek = (id: number, wniosek: number, data: string, uwzglednij: boolean) =>
  zmienCykl(id, "wnioski-zmiany-terminu/" + wniosek + "/decyzja", { data, uwzglednij });

export type StatusWersji = "ROBOCZA" | "ZATWIERDZONA" | "WYDRUKOWANA" | "PODPISANA_I_DORECZONA";
export type PowodWersji = "PIERWSZA" | "KOREKTA" | "ZMIANA_PO_ODWOLANIU" | "PONOWNE_SPORZADZENIE";
export type Rozstrzygniecie = "NIEUWZGLEDNIONE" | "ZMIANA_OCENY" | "PONOWNE_SPORZADZENIE";

export interface TerminWersji {
  data_odniesienia: string;
  data: string;
  slad: TerminKryteriow["slad"];
}

export interface WersjaOceny {
  numer: number;
  status: Exclude<StatusWersji, "ROBOCZA">;
  powod: PowodWersji;
  hash: string;
  data_sporzadzenia: string;
  data_zatwierdzenia: string;
  data_wydruku: string | null;
  doreczenie: { data_zapoznania: string; rejestrujacy: string; zarejestrowano: string; ma_skan: boolean } | null;
  odwolanie: {
    data_wniesienia: string;
    data_rozpatrzenia: string | null;
    rozpatrzyl: string | null;
    rozstrzygniecie: Rozstrzygniecie | null;
  } | null;
  termin_odwolania: TerminWersji | null;
  termin_rozpatrzenia: TerminWersji | null;
  ponowna_ocena: TerminWersji | null;
}

export interface OdpowiedzPytania {
  wartosc: "nigdy" | "rzadko" | "czasem" | "zwykle" | "zawsze" | "brak_danych";
  dowod: string | null;
}

export interface MetodykaKryterium {
  etykieta: string;
  pytania: { id: number; tresc: string; odpowiedz: OdpowiedzPytania | null }[];
  propozycja: number | null;
  liczba_odpowiedzi: number;
  uzasadnienie_odstepstwa: string | null;
}

export interface KryteriumOceny {
  id: string;
  nazwa: string;
  rodzaj: string;
  opis: string | null;
  adnotacje: SourceRef[];
  oczekiwania: string | null;
  punkty: number | null;
  metodyka: MetodykaKryterium | null;
}

export interface StanOceny {
  cykl_id: number;
  wersja_regul: string;
  wersja_metodyki: string;
  kryteria: KryteriumOceny[];
  wynik: {
    kompletny: boolean; srednia: string; poziom: string | null; wynik: string;
    regula_rozstrzygajaca: { id: string; nazwa: string | null; adnotacje: SourceRef[] };
    slad: { regula: string; opis: string }[];
  } | null;
  uzasadnienie: string | null;
  bledy: string[];
  porownanie_excela: { srednia_par_11: string; srednia_excel: string; dzielnik_excel: number; liczba_ocen: number; rozni_sie: boolean; rozbieznosc: string } | null;
  zatwierdzona: boolean;
  data_zatwierdzenia: string | null;
  data_sporzadzenia: string | null;
  data_rozmowy_oceniajacej: string | null;
  wnioski_rozwojowe: string;
  numer_wersji: number;
  powod_wersji: PowodWersji;
  status: StatusWersji;
  historia: WersjaOceny[];
  graf: GrafOceny | null;
}

export interface ZmianyOceny {
  punkty?: Record<string, number | null>;
  oczekiwania?: Record<string, string>;
  uzasadnienie?: string;
  data_sporzadzenia?: string | null;
  data_rozmowy_oceniajacej?: string | null;
  wnioski_rozwojowe?: string;
  zatwierdz?: boolean;
  odpowiedzi?: Record<string, Record<number, OdpowiedzPytania | null>>;
  uzasadnienia_odstepstw?: Record<string, string>;
}

export interface ZapisOceny extends ZmianyOceny {
  numer_wersji: number;
}

export const pobierzOcene = (cyklId: number, zGrafem = false) =>
  zapytaj(`/api/cykle/${cyklId}/ocena${zGrafem ? "?z_grafem=true" : ""}`).then(jsonLubBlad<StanOceny>);
export const zapiszOcene = (cyklId: number, body: ZapisOceny) =>
  zapytaj(`/api/cykle/${cyklId}/ocena`, {
    method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
  }).then((r) => jsonLubBlad<StanOceny>(r));

const adresWersji = (cyklId: number, wersja: number) => `/api/cykle/${cyklId}/ocena/wersje/${wersja}`;
export const adresPlikuOceny = (cyklId: number, wersja: number, plik: "pdf" | "skan") =>
  `${adresWersji(cyklId, wersja)}/${plik}`;

function operacjaWersji(cyklId: number, wersja: number, operacja: string, dane?: object) {
  return zapytaj(`${adresWersji(cyklId, wersja)}/${operacja}`, {
    method: "POST",
    headers: dane ? { "content-type": "application/json" } : undefined,
    body: dane ? JSON.stringify(dane) : undefined,
  }).then(sprawdz);
}

export const nowaWersjaOceny = (id: number, numer: number) => operacjaWersji(id, numer, "nowa");
export const wydrukOceny = (id: number, numer: number) => operacjaWersji(id, numer, "wydruk").then((r) => r.blob());
export const doreczOcene = (id: number, numer: number, dane: { data_zapoznania: string; rejestrujacy: string; skan_base64?: string }) =>
  operacjaWersji(id, numer, "doreczenie", dane);
export const wniesOdwolanie = (id: number, numer: number, data_wniesienia: string) =>
  operacjaWersji(id, numer, "odwolanie", { data_wniesienia });
export const rozpatrzOdwolanie = (
  id: number, numer: number, dane: { data_rozpatrzenia: string; rozpatrzyl: string; rozstrzygniecie: Rozstrzygniecie },
) => operacjaWersji(id, numer, "rozpatrzenie", dane);

export interface ElementPierwszejOceny {
  id: string; nazwa: string; opis: string; adnotacje: SourceRef[]; status: "ŹRÓDŁO" | "BRAK_ŹRÓDŁA";
}
export interface PytanieDoOrganizatorow { id: string; tresc: string; dotyczy: string[]; adnotacje: SourceRef[] }
export interface SciezkaPierwszejOceny {
  pracownik_id: string;
  proces: "PIERWSZA";
  wersja: string;
  komunikat: string;
  termin: {
    nawiazanie: string; najwczesniej: string; koniec_umowy: string; najpozniej: string;
    slad: { opis: string; lokalizator: string }[];
  } | null;
  oceniajacy: { przelozony_id: string | null; przelozony: string | null; komorka: string; opis: string };
  sekcje: { id: string; nazwa: string; elementy: ElementPierwszejOceny[] }[];
  pytania_do_organizatorow: PytanieDoOrganizatorow[];
}
export interface PanelBrakow {
  braki_zrodel: { id: string; nazwa: string; miejsce: string; uzasadnienie: string }[];
  pytania_do_organizatorow: PytanieDoOrganizatorow[];
}

export const pobierzPierwszaOcene = (id: string) =>
  zapytaj(`/api/pracownicy/${id}/pierwsza-ocena`).then((r) => jsonLubBlad<SciezkaPierwszejOceny>(r));
export const pobierzBraki = () => zapytaj("/api/braki").then((r) => jsonLubBlad<PanelBrakow>(r));

// --- Aktualizacja przepisów (#25) ---
export interface CytatZrodla { fragment_id: string; doc_id: string; lokalizator: string | null; cytat: string }
export interface PropozycjaZmiany {
  parametr: string; rodzaj: string; nazwa: string; zmienia: boolean | null;
  cytaty_aktu: CytatZrodla[]; cytaty_przed: CytatZrodla[];
  wartosc_przed: string; wartosc_po: string | null; tryb: "NA_ŻYWO" | "ZAPISANE" | "EKSTRAKCYJNE" | null;
  dostawca: string | null; etykieta: string; niejasnosci: string[];
}
export interface AktZmieniajacy {
  id: number; nazwa: string; doc_id: string; wgral: string; wgrano: string;
  status: "WGRANY" | "PRZEANALIZOWANY" | "OPUBLIKOWANY";
  analiza: {
    doc_id: string; wersja_bazowa: string; propozycje: PropozycjaZmiany[];
    nieobslugiwane: { fragment_id: string; tekst: string; flaga: string }[];
    data_wejscia: DataZAktu | null;
  } | null;
  decyzje: Record<string, { przyjeta: boolean; wartosc_po: string | null; osoba: string; data: string }>;
  publikacja: { wersja: string; obowiazuje_od: string; osoba: string; data: string } | null;
}
export interface DataZAktu { data: string; cytat: string; fragment_id: string }
export interface WynikKrotki { srednia: string; poziom: string | null; wynik: string }
export interface Zestawienie {
  wersja_bazowa: string;
  zmiany: { parametr: string; nazwa: string; przed: string; po: string; cytaty_przed: CytatZrodla[]; cytaty_aktu: CytatZrodla[]; tryb: string | null }[];
  nieobslugiwane: { fragment_id: string; tekst: string; flaga: string }[];
  scenariusze: { id: string; opis: string; oceny: number[]; przed: WynikKrotki; po: WynikKrotki; zmiana: boolean }[];
  obszary: { id: string; nazwa: string; zmieniony: boolean; wplyw: string }[];
  data_wejscia: DataZAktu | null;
  oceny: { cykl_id: number; pracownik_id: string; wersja_regul: string; zakonczona: boolean; wynik_wlasny: WynikKrotki | null; wynik_po: WynikKrotki | null; niejasne: boolean; powody: string[] }[];
}
export interface ZadanieDecyzyjne {
  id: number; cykl_id: number; pracownik_id: string; akt_id: number; opis: string; powody: string[]; opcje: string[];
  status: "OTWARTE" | "ROZSTRZYGNIETE"; wybor: string | null; uzasadnienie: string | null; rozstrzygnal: string | null;
}

const wyslij = (url: string, method: string, dane?: object) =>
  zapytaj(url, {
    method, headers: dane ? { "content-type": "application/json" } : undefined, body: dane ? JSON.stringify(dane) : undefined,
  });

export const pobierzAktDemo = () => zapytaj("/api/przepisy/akt-demo").then((r) => jsonLubBlad<{ nazwa: string; tekst: string }>(r));
export const pobierzAkty = () => zapytaj("/api/przepisy/akty").then((r) => jsonLubBlad<AktZmieniajacy[]>(r));
export const wgrajAkt = (nazwa: string, tekst: string) => wyslij("/api/przepisy/akty", "POST", { nazwa, tekst }).then((r) => jsonLubBlad<AktZmieniajacy>(r));
export const analizujAkt = (id: number) => wyslij(`/api/przepisy/akty/${id}/analiza`, "POST").then((r) => jsonLubBlad<AktZmieniajacy>(r));
export const zdecyduj = (id: number, parametr: string, przyjeta: boolean, wartosc_po: string | null) =>
  wyslij(`/api/przepisy/akty/${id}/propozycje/${parametr}`, "PUT", { przyjeta, wartosc_po }).then((r) => jsonLubBlad<AktZmieniajacy>(r));
export const pobierzZestawienie = (id: number) => zapytaj(`/api/przepisy/akty/${id}/zestawienie`).then((r) => jsonLubBlad<Zestawienie>(r));
export const opublikuj = (
  id: number, obowiazuje_od: string, wersja: string, potwierdzam_nieobslugiwane: boolean, uzasadnienie_daty: string,
) =>
  wyslij(`/api/przepisy/akty/${id}/publikacja`, "POST", {
    obowiazuje_od, wersja: wersja || null, potwierdzam_nieobslugiwane, uzasadnienie_daty: uzasadnienie_daty || null,
  })
    .then((r) => jsonLubBlad<AktZmieniajacy>(r));
export const pobierzZadania = () => zapytaj("/api/przepisy/zadania").then((r) => jsonLubBlad<ZadanieDecyzyjne[]>(r));
export const rozstrzygnijZadanie = (id: number, wersja: string, uzasadnienie: string) =>
  wyslij(`/api/przepisy/zadania/${id}`, "POST", { wersja, uzasadnienie }).then((r) => jsonLubBlad<ZadanieDecyzyjne>(r));
export const rozstrzygnijZbiorczo = (ids: number[], wersja: string, uzasadnienie: string) =>
  wyslij("/api/przepisy/zadania/zbiorczo", "POST", { ids, wersja, uzasadnienie }).then((r) => jsonLubBlad<ZadanieDecyzyjne[]>(r));

// --- Rozmowa oceniająca i notatki (#24) ---
export interface PytanieRozmowy { id: string; tresc: string; z_propozycji: string | null }
export interface NotatkaRozmowy {
  id: number; tresc: string; pytanie_id: string | null; kryterium_id: string | null; zapisano: string;
}
export interface StanRozmowy {
  cykl_id: number; wersja: string; rodzaj: "PYTANIA_DO_ROZMOWY";
  propozycje: {
    id: string; rodzaj: "PYTANIE_DO_ROZMOWY"; szablon: string; tresc: string; uzasadnienie: string;
    powiazanie: { typ: "zadanie" | "wymaganie" | "kryterium" | "poprzednia_ocena" | "przepis" | "wlasne"; opis: string | null; kryterium_id: string | null };
    etykieta: string | null; adnotacje: SourceRef[];
  }[];
  pytania: PytanieRozmowy[]; notatki: NotatkaRozmowy[]; zasada_notatek: string;
}

async function wynikRozmowy<T>(r: Response): Promise<T> {
  if (r.status === 403) throw new Error("Nie masz uprawnień do prowadzenia rozmowy dla tego pracownika.");
  if (r.status === 401) throw new Error("Sesja wygasła. Wybierz ponownie rolę, aby się zalogować.");
  return jsonLubBlad<T>(r);
}

export const pobierzRozmowe = (cyklId: number, signal?: AbortSignal) =>
  zapytaj(`/api/cykle/${cyklId}/rozmowa`, { signal }).then(wynikRozmowy<StanRozmowy>);
export const zapiszPytaniaRozmowy = (cyklId: number, pytania: PytanieRozmowy[], signal?: AbortSignal) =>
  zapytaj(`/api/cykle/${cyklId}/rozmowa/pytania`, {
    method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ pytania }), signal,
  }).then(wynikRozmowy<StanRozmowy>);
export const dodajNotatkeRozmowy = (
  cyklId: number, dane: { tresc: string; pytanie_id: string | null; kryterium_id: string | null }, signal?: AbortSignal,
) => zapytaj(`/api/cykle/${cyklId}/rozmowa/notatki`, {
  method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(dane), signal,
}).then(wynikRozmowy<NotatkaRozmowy>);

