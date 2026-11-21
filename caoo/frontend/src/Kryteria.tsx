import { useEffect, useState } from "react";
import GrafKompetencji from "./GrafKompetencji";
import Ocena from "./Ocena";
import ZmianyCyklu, { ostatniePonowienie } from "./ZmianyCyklu";
import {
  pobierzCykle, pobierzKryteria, potwierdzCykl, rozpocznijCykl,
  type Cykl, type Kryterium, type KryteriaDoUstalenia, type SourceRef, type TerminKryteriow,
} from "./api";

const data = (iso: string) => iso.slice(0, 10).split("-").reverse().join(".");

function Zrodla({ adnotacje }: { adnotacje: SourceRef[] }) {
  return (
    <details className="zrodla">
      <summary>źródło</summary>
      {adnotacje.map((a, i) => (
        <div key={i} className="adnotacja">
          <div className="zrodlo">
            <span className={`kategoria ${a.kategoria === "PRZEPIS" ? "przepis" : ""}`}>{a.kategoria}</span>
            {a.lokalizator}
          </div>
          {a["cytat_dosłowny"] && <blockquote>{a["cytat_dosłowny"]}</blockquote>}
          {a.uzasadnienie && <p className="opis">{a.uzasadnienie}</p>}
        </div>
      ))}
    </details>
  );
}

function Termin({ t }: { t: TerminKryteriow }) {
  return (
    <div>
      <p>Termin ustalenia kryteriów: <strong>{data(t.termin)}</strong> <span className="opis">(od: {t.zdarzenie}, {data(t.data_zdarzenia)})</span></p>
      <ol className="slad">
        {t.slad.map((k, i) => <li key={i}>{k.opis} <Zrodla adnotacje={k.adnotacje} /></li>)}
      </ol>
    </div>
  );
}

function Lista({ kryteria }: { kryteria: Kryterium[] }) {
  return <ul>{kryteria.map((k) => <li key={k.id}>{k.nazwa} <Zrodla adnotacje={k.adnotacje} /></li>)}</ul>;
}

function TrwajacyCykl({ c, onZmiana }: { c: Cykl; onZmiana: (c: Cykl) => void }) {
  const [dzien, setDzien] = useState(new Date().toISOString().slice(0, 10));
  const [blad, setBlad] = useState<string | null>(null);
  const ponowienie = ostatniePonowienie(c);
  return (
    <div>
      <p><strong>Cykl oceny w toku</strong> · arkusz {c.arkusz} · rozpoczęty {data(c.rozpoczeto)} · reguły {c.wersja_regul}</p>
      <p className="opis">Kryteria obowiązkowe:</p>
      <Lista kryteria={c.obowiazkowe} />
      <p className="opis">Kryteria dodatkowe ({c.dodatkowe.length}):</p>
      {c.dodatkowe.length ? <Lista kryteria={c.dodatkowe} /> : <p className="opis">brak</p>}
      {c.termin && <Termin t={c.termin} />}
      <ZmianyCyklu cykl={c} onZmiana={onZmiana} />
      {ponowienie && !ponowienie.ustalono_kryteria ? (
        <p className="opis">Ocena czeka na ponowne ustalenie kryteriów.</p>
      ) : c.data_potwierdzenia ? (
        <>
          <p className="ok-tekst">Oceniany potwierdził rozmowę i zapoznanie z kryteriami: {c.potwierdzil}, {data(c.data_potwierdzenia)}</p>
          <Ocena cyklId={c.id} />
        </>
      ) : (
        <>
          <div className="wiersz-lewy">
            <span>Potwierdzenie ocenianego (§ 6 ust. 2):</span>
            <input type="date" value={dzien} onChange={(e) => setDzien(e.target.value)} />
            <button onClick={() => potwierdzCykl(c.id, dzien).then(onZmiana).catch((e) => setBlad(String(e.message)))}>
              Potwierdź zapoznanie
            </button>
          </div>
          <GrafKompetencji key={c.id} cyklId={c.id} odswiezenie={c} />
        </>
      )}
      {blad && <p className="blad">{blad}</p>}
    </div>
  );
}

export default function Kryteria({ id, onZmianaTerminu }: { id: string; onZmianaTerminu: () => void }) {
  const [k, setK] = useState<KryteriaDoUstalenia | null>(null);
  const [cykl, setCykl] = useState<Cykl | null>(null);
  const [wybrane, setWybrane] = useState<string[]>([]);
  const [blad, setBlad] = useState<string | null>(null);
  const [niedostepne, setNiedostepne] = useState(false);

  useEffect(() => {
    pobierzKryteria(id).then((d) => { setK(d); setWybrane(d.propozycje.map((p) => p.id)); }).catch(() => setNiedostepne(true));
    pobierzCykle(id).then((l) => setCykl(l.find((c) => c.status === "W_TOKU") ?? null)).catch(() => undefined);
  }, [id]);

  if (niedostepne) return null; // pierwsza ocena albo nie można ustalić – patrz „Rodzaj oceny”
  if (!k) return null;

  const przelacz = (kid: string) =>
    setWybrane((w) => (w.includes(kid) ? w.filter((x) => x !== kid) : w.length >= k.limit_dodatkowych ? w : [...w, kid]));
  const propozycje = new Map(k.propozycje.map((p) => [p.id, p]));
  const pozostale = k.dostepne_dodatkowe.filter((d) => !propozycje.has(d.id));

  return (
    <div className="sekcja">
      <h3>Kryteria oceny i termin (§ 4–7)</h3>
      {cykl ? <TrwajacyCykl c={cykl} onZmiana={(nowy) => {
        if (nowy.termin_oceny !== cykl.termin_oceny) onZmianaTerminu();
        setCykl(nowy);
      }} /> : (
        <>
          <p className="opis">Kryteria obowiązkowe arkusza {k.arkusz}:</p>
          <Lista kryteria={k.obowiazkowe} />
          <p className="opis">Kryteria dodatkowe – wybrano {wybrane.length} z {k.limit_dodatkowych}:</p>
          {k.propozycje.map((p) => (
            <label key={p.id} className="wybor">
              <input type="checkbox" checked={wybrane.includes(p.id)} onChange={() => przelacz(p.id)}
                disabled={!wybrane.includes(p.id) && wybrane.length >= k.limit_dodatkowych} />
              {p.nazwa} <span className="demo">propozycja</span>
              <span className="opis"> {p.uzasadnienie} ({p.zadania.join("; ")})</span>
            </label>
          ))}
          <details>
            <summary className="opis">Pozostałe kryteria z wykazu (zał. 1)</summary>
            {pozostale.map((d) => (
              <label key={d.id} className="wybor">
                <input type="checkbox" checked={wybrane.includes(d.id)} onChange={() => przelacz(d.id)}
                  disabled={!wybrane.includes(d.id) && wybrane.length >= k.limit_dodatkowych} />
                {d.nazwa}
              </label>
            ))}
          </details>
          {k.termin ? <Termin t={k.termin} /> : <p className="blad">Brak danych do terminu: {k.braki.join(", ")}</p>}
          <button onClick={() => rozpocznijCykl(id, wybrane).then(setCykl).catch((e) => setBlad(String(e.message)))}>
            Zatwierdź kryteria i rozpocznij cykl
          </button>
          {blad && <p className="blad">{blad}</p>}
        </>
      )}
    </div>
  );
}
