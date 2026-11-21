import { useEffect, useState } from "react";
import { pobierzProces, type PracownikSkrot, type TerminOceny } from "./api";

const dataPl = (iso: string) => iso.split("-").reverse().join(".");
const dzienUtc = (iso: string) => Date.parse(`${iso}T00:00:00Z`) / 86_400_000;

export function SladTerminu({ wynik }: { wynik: TerminOceny }) {
  return (
    <details className="zrodla">
      <summary>Ślad obliczenia</summary>
      <p>Podstawa to {wynik.zdarzenie} z dnia {dataPl(wynik.data_zdarzenia)}.</p>
      <p>Termin bez przesunięć to <strong>{dataPl(wynik.termin_bazowy)}</strong>.</p>
      {wynik.okresy_polaczone.length > 0 && (
        <ul className="slad">
          {wynik.okresy_polaczone.map((o) => (
            <li key={o.od}>{dataPl(o.od)}–{dataPl(o.do)} · doliczono {o.dni_doliczone} dni</li>
          ))}
        </ul>
      )}
      <ol className="slad">
        {wynik.slad.map((krok, i) => (
          <li key={`${krok.regula}-${i}`}>
            {krok.opis}
            {krok.adnotacje.map((a, j) => (
              <div className="adnotacja" key={j}>
                <span className={`kategoria ${a.kategoria === "PRZEPIS" ? "przepis" : ""}`}>{a.kategoria}</span>{" "}
                {a.lokalizator ?? (a.doc_id === "liczenie-terminow" ? "Liczenie terminów" : a.doc_id)}
                {a["cytat_dosłowny"] && <blockquote>{a["cytat_dosłowny"]}</blockquote>}
              </div>
            ))}
          </li>
        ))}
      </ol>
    </details>
  );
}

interface PozycjaKalendarza {
  pracownik: PracownikSkrot;
  wynik: TerminOceny;
}

export default function KalendarzOcen({ pracownicy, otworzProfil }: {
  pracownicy: PracownikSkrot[];
  otworzProfil: (id: string) => void;
}) {
  const [od, setOd] = useState(() => new Date().toLocaleDateString("sv-SE", { timeZone: "Europe/Warsaw" }));
  const [dni, setDni] = useState("90");
  const [pozycje, setPozycje] = useState<PozycjaKalendarza[] | null>(null);
  const [blad, setBlad] = useState(false);

  useEffect(() => {
    let aktywny = true;
    Promise.all(pracownicy.map(async (pracownik) => {
      const { termin_oceny } = await pobierzProces(pracownik.id);
      return termin_oceny ? [{ pracownik, wynik: termin_oceny }] : [];
    })).then((wyniki) => {
      if (aktywny) setPozycje(wyniki.flat());
    }).catch(() => {
      if (aktywny) setBlad(true);
    });
    return () => { aktywny = false; };
  }, [pracownicy]);

  const poprawneDaty = od !== "" && /^\d+$/.test(dni);
  const nadchodzace = poprawneDaty && pozycje ? pozycje.filter(({ wynik }) => {
    const roznica = dzienUtc(wynik.termin) - dzienUtc(od);
    return roznica >= 0 && roznica <= Number(dni);
  }).sort((a, b) => a.wynik.termin.localeCompare(b.wynik.termin)) : [];

  return (
    <section aria-labelledby="kalendarz-ocen">
      <h2 id="kalendarz-ocen">Kalendarz ocen okresowych</h2>
      <p className="opis">Cykl 24-miesięczny. Wykaz świąt jest demonstracyjny i pochodzi spoza materiałów.</p>
      <div className="wiersz-lewy">
        <label>Od dnia <input type="date" value={od} onChange={(e) => setOd(e.target.value)} /></label>
        <label>Liczba dni <input type="number" min={0} step={1} value={dni} onChange={(e) => setDni(e.target.value)} /></label>
      </div>
      {!poprawneDaty && <p className="blad" role="status">Podaj datę i liczbę pełnych dni od zera.</p>}
      {blad && <p className="blad" role="alert">Nie udało się pobrać terminów ocen.</p>}
      {!blad && pozycje === null && <p className="opis" role="status">Wczytywanie terminów…</p>}
      {poprawneDaty && pozycje !== null && nadchodzace.length === 0 && (
        <p className="opis" role="status">Brak terminów w wybranym przedziale.</p>
      )}
      {nadchodzace.length > 0 && (
        <table>
          <thead><tr><th>Pracownik</th><th>Termin oceny</th><th>Obliczenie</th></tr></thead>
          <tbody>
            {nadchodzace.map(({ pracownik, wynik }) => (
              <tr key={pracownik.id}>
                <td><button className="link" onClick={() => otworzProfil(pracownik.id)}>{pracownik.imie} {pracownik.nazwisko}</button></td>
                <td><time dateTime={wynik.termin}>{dataPl(wynik.termin)}</time></td>
                <td><SladTerminu wynik={wynik} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
