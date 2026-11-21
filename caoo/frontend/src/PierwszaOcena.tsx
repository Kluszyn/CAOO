import { useEffect, useState } from "react";
import {
  pobierzBraki, pobierzPierwszaOcene,
  type ElementPierwszejOceny, type PanelBrakow as Panel, type PytanieDoOrganizatorow, type SciezkaPierwszejOceny, type SourceRef,
} from "./api";

const data = (iso: string) => iso.split("-").reverse().join(".");

function Adnotacja({ a }: { a: SourceRef }) {
  const klasa = a.kategoria === "PRZEPIS" ? "przepis" : a.kategoria === "BRAK_ŹRÓDŁA" ? "brak" : "";
  return (
    <div className="adnotacja">
      <div className="zrodlo">
        <span className={`kategoria ${klasa}`}>{a.kategoria}</span>
        {a.lokalizator}{a.strona && <> · s. {a.strona}</>}
      </div>
      {a["cytat_dosłowny"] && <blockquote>{a["cytat_dosłowny"]}</blockquote>}
      {a.uzasadnienie && <p className="opis">{a.uzasadnienie}</p>}
    </div>
  );
}

function Element({ e }: { e: ElementPierwszejOceny }) {
  const brak = e.status === "BRAK_ŹRÓDŁA";
  return (
    <div className={`element ${brak ? "brak" : ""}`}>
      <div className="wiersz-lewy">
        <span className={brak ? "status-brak" : "status-zrodlo"}>{brak ? "BRAK_ŹRÓDŁA" : "źródło"}</span>
        <strong>{e.nazwa}</strong>
      </div>
      <p className="opis">{e.opis}</p>
      {e.adnotacje.map((a, i) => <Adnotacja key={i} a={a} />)}
    </div>
  );
}

function Pytanie({ p }: { p: PytanieDoOrganizatorow }) {
  return (
    <div className="element">
      <p><strong>Pytanie do organizatorów:</strong> {p.tresc}</p>
      <p className="opis">Dotyczy: {p.dotyczy.join(", ")}</p>
    </div>
  );
}

/** Ścieżka pierwszej oceny (#19) – osobny proces z art. 37–38, z elementami BRAK_ŹRÓDŁA. */
export default function PierwszaOcena({ id }: { id: string }) {
  const [s, setS] = useState<SciezkaPierwszejOceny | null>(null);
  const [blad, setBlad] = useState<string | null>(null);
  useEffect(() => {
    pobierzPierwszaOcene(id).then(setS).catch((e: Error) => setBlad(e.message));
  }, [id]);
  if (blad) return <p className="blad">{blad}</p>;
  if (!s) return null;
  const braki = s.sekcje.flatMap((x) => x.elementy).filter((e) => e.status === "BRAK_ŹRÓDŁA").length;
  return (
    <div className="pierwsza">
      <h3>Ścieżka pierwszej oceny w służbie cywilnej</h3>
      <p className="opis">{s.komunikat}</p>
      <p className="braki">
        Brak właściwego wzoru arkusza i rozporządzenia o pierwszej ocenie – poniżej szkielet. Elementów bez źródła: {braki}.
      </p>
      {s.termin && (
        <div>
          <p>Okno terminu: od <strong>{data(s.termin.najwczesniej)}</strong> do <strong>{data(s.termin.najpozniej)}</strong>
            <span className="opis"> · umowa do {data(s.termin.koniec_umowy)}</span></p>
          <details className="zrodla">
            <summary>Ślad obliczenia</summary>
            <ol className="slad">{s.termin.slad.map((k, i) => <li key={i}>{k.opis} <span className="opis">({k.lokalizator})</span></li>)}</ol>
          </details>
        </div>
      )}
      <p>Oceniający: {s.oceniajacy.przelozony ?? "brak przełożonego w danych"} <span className="opis">· {s.oceniajacy.opis}</span></p>
      {s.sekcje.map((x) => (
        <div key={x.id} className="sekcja">
          <h3>{x.nazwa}</h3>
          {x.elementy.map((e) => <Element key={e.id} e={e} />)}
        </div>
      ))}
      <p className="opis">Konfiguracja {s.wersja}. Pytania do organizatorów są też w zakładce „Panel braków”.</p>
    </div>
  );
}

/** Panel braków: elementy bez źródła w materiałach i pytania do organizatorów. */
export function PanelBrakow() {
  const [b, setB] = useState<Panel | null>(null);
  const [blad, setBlad] = useState<string | null>(null);
  useEffect(() => {
    pobierzBraki().then(setB).catch((e: Error) => setBlad(e.message));
  }, []);
  if (blad) return <p className="blad">{blad}</p>;
  if (!b) return null;
  return (
    <>
      <section>
        <h2>Pytania do organizatorów</h2>
        {b.pytania_do_organizatorow.map((p) => <Pytanie key={p.id} p={p} />)}
      </section>
      <section>
        <h2>Braki źródeł</h2>
        <table>
          <thead><tr><th>Element</th><th>Miejsce</th><th>Czego brakuje</th></tr></thead>
          <tbody>
            {b.braki_zrodel.map((x, i) => (
              <tr key={`${x.id}-${i}`}><td><span className="status-brak">BRAK_ŹRÓDŁA</span> {x.nazwa}</td><td>{x.miejsce}</td><td>{x.uzasadnienie}</td></tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}
