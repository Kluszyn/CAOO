import { useState, type FormEvent } from "react";
import {
  adresPlikuOceny, doreczOcene, otworzPlik, nowaWersjaOceny, pobierzOcene, rozpatrzOdwolanie, wniesOdwolanie, wydrukOceny,
  type PowodWersji, type Rozstrzygniecie, type StanOceny, type StatusWersji, type TerminWersji, type WersjaOceny,
} from "./api";

const STATUS: Record<StatusWersji, string> = {
  ROBOCZA: "Wersja robocza",
  ZATWIERDZONA: "Zatwierdzona",
  WYDRUKOWANA: "Wydrukowana",
  PODPISANA_I_DORECZONA: "Podpisana i doręczona",
};
const POWOD: Record<PowodWersji, string> = {
  PIERWSZA: "",
  KOREKTA: "Korekta przed doręczeniem",
  ZMIANA_PO_ODWOLANIU: "Zmiana oceny po odwołaniu",
  PONOWNE_SPORZADZENIE: "Ponowne sporządzenie po odwołaniu",
};
const ROZSTRZYGNIECIE: Record<Rozstrzygniecie, string> = {
  NIEUWZGLEDNIONE: "Nieuwzględnione",
  ZMIANA_OCENY: "Uwzględnione. Zmienić ocenę",
  PONOWNE_SPORZADZENIE: "Uwzględnione. Sporządzić ocenę ponownie",
};

const data = (iso: string) => iso.split("-").reverse().join(".");
export function dzisiaj() {
  const teraz = new Date();
  return new Date(teraz.getTime() - teraz.getTimezoneOffset() * 60_000).toISOString().slice(0, 10);
}

function Termin({ etykieta, t }: { etykieta: string; t: TerminWersji }) {
  return (
    <details className="termin-wersji">
      <summary>{etykieta} <strong>{data(t.data)}</strong></summary>
      <ol className="slad">
        {t.slad.map((k) => <li key={k.opis}>{k.opis} {k.adnotacje.map((a) => a.lokalizator).filter(Boolean).join(", ")}</li>)}
      </ol>
    </details>
  );
}

function SzczegolyWersji({ cyklId, w }: { cyklId: number; w: WersjaOceny }) {
  return (
    <>
      <dl className="dane-wersji">
        <dt>Data sporządzenia</dt><dd>{data(w.data_sporzadzenia)}</dd>
        <dt>Data zatwierdzenia</dt><dd>{data(w.data_zatwierdzenia)}</dd>
        {w.data_wydruku && <><dt>Data wydruku</dt><dd>{data(w.data_wydruku)}</dd></>}
        {w.doreczenie && <>
          <dt>Data zapoznania</dt><dd>{data(w.doreczenie.data_zapoznania)}</dd>
          <dt>Osoba rejestrująca podpis i doręczenie</dt><dd>{w.doreczenie.rejestrujacy}</dd>
          <dt>Data rejestracji</dt><dd>{data(w.doreczenie.zarejestrowano)}</dd>
        </>}
        <dt>SHA-256</dt><dd><code>{w.hash}</code></dd>
      </dl>
      <div className="akcje-wersji">
        {w.data_wydruku && <button type="button" className="link" onClick={() => void otworzPlik(adresPlikuOceny(cyklId, w.numer, "pdf"))}>Pobierz PDF wersji {w.numer}</button>}
        {w.doreczenie?.ma_skan && <button type="button" className="link" onClick={() => void otworzPlik(adresPlikuOceny(cyklId, w.numer, "skan"))}>Pobierz skan</button>}
      </div>
    </>
  );
}

function HistoriaOdwolania({ w }: { w: WersjaOceny }) {
  return (
    <>
      {w.termin_odwolania && <Termin etykieta="Termin wniesienia" t={w.termin_odwolania} />}
      {w.termin_rozpatrzenia && <Termin etykieta="Termin rozpatrzenia" t={w.termin_rozpatrzenia} />}
      {w.odwolanie && (
        <p>Wniesiono {data(w.odwolanie.data_wniesienia)}.{" "}
          {w.odwolanie.rozstrzygniecie && w.odwolanie.data_rozpatrzenia
            ? `${ROZSTRZYGNIECIE[w.odwolanie.rozstrzygniecie]}. Rozpatrzono ${data(w.odwolanie.data_rozpatrzenia)}. ${w.odwolanie.rozpatrzyl}.`
            : "Oczekuje na rozpatrzenie."}
        </p>
      )}
      {w.ponowna_ocena && <Termin etykieta="Ponowna ocena po wyniku negatywnym od" t={w.ponowna_ocena} />}
    </>
  );
}

function odczytajSkan(plik: File): Promise<string> {
  if (plik.size > 5 * 1024 * 1024) return Promise.reject(new Error("Skan może mieć najwyżej 5 MB."));
  return new Promise((resolve, reject) => {
    const czytnik = new FileReader();
    czytnik.onload = () => resolve(String(czytnik.result).split(",")[1]);
    czytnik.onerror = () => reject(new Error("Nie udało się odczytać skanu."));
    czytnik.readAsDataURL(plik);
  });
}

function pobierzWydruk(pdf: Blob, id: number, numer: number) {
  const adres = URL.createObjectURL(pdf);
  const link = document.createElement("a");
  link.href = adres;
  link.download = `ocena-${id}-wersja-${numer}.pdf`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(adres), 1_000);
}

export default function ObiegOceny({ s, onZmiana }: { s: StanOceny; onZmiana: (s: StanOceny) => void }) {
  const { cykl_id: id, numer_wersji: numer } = s;
  const w = s.historia.find((x) => x.numer === numer);
  const poprzednie = s.historia.filter((x) => x.numer !== numer);
  const dzis = dzisiaj();
  const [zajety, setZajety] = useState(false);
  const [blad, setBlad] = useState<string | null>(null);

  const wykonaj = async (akcja: () => Promise<unknown>) => {
    if (zajety) return;
    setZajety(true);
    setBlad(null);
    try {
      await akcja();
      onZmiana(await pobierzOcene(id));
    } catch (e) {
      setBlad(e instanceof Error ? e.message : String(e));
    } finally {
      setZajety(false);
    }
  };
  const rejestruj = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    void wykonaj(async () => {
      const skan = f.get("skan");
      return doreczOcene(id, numer, {
        data_zapoznania: String(f.get("zapoznanie")), rejestrujacy: String(f.get("rejestrujacy")),
        skan_base64: skan instanceof File && skan.name ? await odczytajSkan(skan) : undefined,
      });
    });
  };
  const odwolaj = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    void wykonaj(() => wniesOdwolanie(id, numer, String(f.get("wniesienie"))));
  };
  const rozpatrz = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    void wykonaj(() => rozpatrzOdwolanie(id, numer, {
      data_rozpatrzenia: String(f.get("rozpatrzenie")), rozpatrzyl: String(f.get("rozpatrzyl")),
      rozstrzygniecie: f.get("rozstrzygniecie") as Rozstrzygniecie,
    }));
  };
  const koniecOdwolania = w?.termin_odwolania?.data;
  const ostatniDzienWniesienia = koniecOdwolania && koniecOdwolania < dzis ? koniecOdwolania : dzis;

  return (
    <div className="sekcja obieg" aria-busy={zajety}>
      <h3>Obieg oceny</h3>
      <p aria-live="polite"><strong>{STATUS[s.status]} · wersja {numer}</strong>{POWOD[s.powod_wersji] && ` · ${POWOD[s.powod_wersji]}`}</p>
      {w && <SzczegolyWersji cyklId={id} w={w} />}
      {w && !w.doreczenie && (
        <div className="akcje-wersji">
          {!w.data_wydruku && <button type="button" className="glowny" disabled={zajety}
            onClick={() => void wykonaj(async () => pobierzWydruk(await wydrukOceny(id, numer), id, numer))}>Wygeneruj PDF do druku</button>}
          <button type="button" disabled={zajety} onClick={() => void wykonaj(() => nowaWersjaOceny(id, numer))}>
            Utwórz nową wersję do korekty
          </button>
        </div>
      )}
      {w?.data_wydruku && !w.doreczenie && (
        <form className="formularz-wersji" onSubmit={rejestruj}>
          <fieldset disabled={zajety}>
            <legend>Podpis i doręczenie</legend>
            <p className="opis">Zarejestruj zdarzenie po odręcznym podpisaniu wydruku i zapoznaniu pracownika z oceną.</p>
            <label>Data zapoznania<input name="zapoznanie" type="date" min={w.data_wydruku} max={dzis} defaultValue={dzis} required /></label>
            <label>Osoba rejestrująca<input name="rejestrujacy" maxLength={160} required /></label>
            <label>Skan podpisanego arkusza (opcjonalny)<input name="skan" type="file" accept="application/pdf,image/jpeg,image/png" /></label>
            <button className="glowny" type="submit">Zarejestruj podpis i doręczenie</button>
          </fieldset>
        </form>
      )}
      {w?.doreczenie && (w.termin_odwolania || w.ponowna_ocena) && (
        <div className="sekcja">
          {w.termin_odwolania && <h4>Odwołanie<sup>1</sup></h4>}
          {w.termin_odwolania && <p className="opis"><sup>1</sup> Art. 83 ustawy o służbie cywilnej używa terminu „sprzeciw”.</p>}
          <HistoriaOdwolania w={w} />
          {w.termin_odwolania && !w.odwolanie && (
            <form className="formularz-wersji" onSubmit={odwolaj}>
              <fieldset disabled={zajety}>
                <legend>Wniesienie odwołania</legend>
                <label>Data wniesienia<input name="wniesienie" type="date" min={w.doreczenie.data_zapoznania}
                  max={ostatniDzienWniesienia} defaultValue={dzis <= ostatniDzienWniesienia ? dzis : ""} required /></label>
                <button className="glowny" type="submit">Wnieś odwołanie</button>
              </fieldset>
            </form>
          )}
          {w.odwolanie && !w.odwolanie.rozstrzygniecie && (
            <form className="formularz-wersji" onSubmit={rozpatrz}>
              <fieldset disabled={zajety}>
                <legend>Decyzja w sprawie odwołania</legend>
                <label>Rozstrzygnięcie
                  <select name="rozstrzygniecie" defaultValue="" required>
                    <option value="" disabled>Wybierz rozstrzygnięcie</option>
                    {Object.entries(ROZSTRZYGNIECIE).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
                  </select>
                </label>
                <label>Data rozpatrzenia<input name="rozpatrzenie" type="date" min={w.odwolanie.data_wniesienia}
                  max={dzis} defaultValue={dzis} required /></label>
                <label>Osoba rozpatrująca<input name="rozpatrzyl" maxLength={160} required /></label>
                <button className="glowny" type="submit">Zapisz rozstrzygnięcie</button>
              </fieldset>
            </form>
          )}
        </div>
      )}
      {poprzednie.length > 0 && (
        <div className="sekcja">
          <h4>Poprzednie wersje</h4>
          {poprzednie.map((p) => (
            <details key={p.numer} className="poprzednia-wersja">
              <summary>Wersja {p.numer} · {STATUS[p.status]}</summary>
              <SzczegolyWersji cyklId={id} w={p} />
              <HistoriaOdwolania w={p} />
            </details>
          ))}
        </div>
      )}
      {blad && <p className="blad" role="alert">{blad}</p>}
    </div>
  );
}
