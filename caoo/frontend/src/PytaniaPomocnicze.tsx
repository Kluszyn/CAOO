import { useState } from "react";
import type { MetodykaKryterium, OdpowiedzPytania, ZmianyOceny } from "./api";

const OPCJE: Record<OdpowiedzPytania["wartosc"], string> = {
  nigdy: "Nigdy",
  rzadko: "Rzadko",
  czasem: "Czasem",
  zwykle: "Zwykle",
  zawsze: "Zawsze",
  brak_danych: "Brak danych",
};

type WyborOdpowiedzi = OdpowiedzPytania["wartosc"] | "";
type SzkicOdpowiedzi = { wartosc: WyborOdpowiedzi; dowod: string };

export default function PytaniaPomocnicze({ kryteriumId, metodyka, zablokowane, zapisz }: {
  kryteriumId: string;
  metodyka: MetodykaKryterium;
  zablokowane: boolean;
  zapisz: (zmiany: ZmianyOceny) => Promise<boolean>;
}) {
  const [zmiany, setZmiany] = useState<Record<number, SzkicOdpowiedzi>>({});
  const [zapisywanie, setZapisywanie] = useState(false);

  async function zapiszOdpowiedzi() {
    setZapisywanie(true);
    const odpowiedzi = Object.fromEntries(Object.entries(zmiany).map(([id, o]) => [
      id, o.wartosc ? { wartosc: o.wartosc, dowod: o.dowod.trim() || null } : null,
    ]));
    if (await zapisz({ odpowiedzi: { [kryteriumId]: odpowiedzi } })) setZmiany({});
    setZapisywanie(false);
  }

  return (
    <details className="pytania-pomocnicze">
      <summary>Pytania pomocnicze ({metodyka.liczba_odpowiedzi}/5 odpowiedzi z danymi)</summary>
      <p className="opis">{metodyka.etykieta}</p>
      <ol>
        {metodyka.pytania.map((p) => {
          const id = `${kryteriumId}-pytanie-${p.id}`;
          const o = zmiany[p.id] ?? { wartosc: p.odpowiedz?.wartosc ?? "", dowod: p.odpowiedz?.dowod ?? "" };
          return (
            <li key={p.id}>
              <label htmlFor={id}>{p.tresc}</label>
              <select id={id} value={o.wartosc} disabled={zablokowane || zapisywanie}
                onChange={(e) => {
                  const wartosc = e.target.value as WyborOdpowiedzi;
                  setZmiany((stare) => ({ ...stare, [p.id]: { ...o, wartosc } }));
                }}>
                <option value="">Bez odpowiedzi</option>
                {Object.entries(OPCJE).map(([wartosc, etykieta]) => <option value={wartosc} key={wartosc}>{etykieta}</option>)}
              </select>
              <label className="opis" htmlFor={`${id}-dowod`}>Dowód do odpowiedzi {p.id} (opcjonalnie)</label>
              <input id={`${id}-dowod`} value={o.dowod} placeholder="Przykład zadania lub notatka"
                disabled={zablokowane || zapisywanie || !o.wartosc}
                onChange={(e) => {
                  const dowod = e.target.value;
                  setZmiany((stare) => ({ ...stare, [p.id]: { ...o, dowod } }));
                }} />
            </li>
          );
        })}
      </ol>
      {!zablokowane && (
        <button type="button" disabled={zapisywanie || Object.keys(zmiany).length === 0} onClick={() => void zapiszOdpowiedzi()}>
          {zapisywanie ? "Zapisywanie…" : "Zapisz odpowiedzi"}
        </button>
      )}
      <p className="opis">Propozycja pojawia się po zapisaniu co najmniej 3 odpowiedzi z danymi.</p>
    </details>
  );
}
