import { useEffect, useRef, useState } from "react";
import { pobierzOcene, type Cykl, type StanOceny } from "./api";
import type { GrafOceny, WezelGrafu } from "./grafTypes";

const ETAPY = ["Wymaganie", "Zadanie", "Kompetencja", "Poziom wymagany", "Dowód lub wynik", "Poziom aktualny", "Luka"];
const STATUS = { potwierdzone: "Potwierdzone", dopasowane: "Dopasowane", propozycja: "Propozycja" };
const NIEPRZYPISANY = "nieprzypisany — wymaga weryfikacji";
const data = (iso: string) => iso.slice(0, 10).split("-").reverse().join(".");

function ZrodloWezla({ wezel }: { wezel: WezelGrafu }) {
  return (
    <div className="graf-szczegoly" aria-live="polite">
      <h4>{wezel.nazwa}</h4>
      <dl>
        <dt>Status</dt><dd>{STATUS[wezel.status]}{wezel.nieprzypisany && ` · ${NIEPRZYPISANY}`}</dd>
        <dt>Źródło</dt><dd>{wezel.zrodlo}{wezel.data_zrodla && ` · ${data(wezel.data_zrodla)}`}</dd>
        <dt>Uzasadnienie</dt><dd>{wezel.uzasadnienie}</dd>
        <dt>Zatwierdzający</dt><dd>{wezel.zatwierdzil ?? "Brak zapisu"}{wezel.data_zatwierdzenia && ` · ${data(wezel.data_zatwierdzenia)}`}</dd>
      </dl>
      {wezel.adnotacje.map((z, i) => (
        <div className="adnotacja" key={i}>
          <div className="zrodlo">
            <span className="kategoria">{z.kategoria}</span>
            {z.lokalizator}{z.strona && ` · s. ${z.strona}`}
          </div>
          {z["cytat_dosłowny"] && <blockquote>{z["cytat_dosłowny"]}</blockquote>}
          {z.uzasadnienie && <p className="opis">{z.uzasadnienie}</p>}
        </div>
      ))}
    </div>
  );
}

function WidokGrafu({ graf }: { graf: GrafOceny }) {
  const [sciezka, setSciezka] = useState(0);
  const [wybrany, setWybrany] = useState<string | null>(null);
  const panelSzczegolow = useRef<HTMLDivElement>(null);
  const { katalog } = graf;
  const grupyKatalogu = [
    { tytul: "Kompetencje", wpisy: katalog.kompetencje },
    { tytul: "Poziomy", wpisy: katalog.poziomy },
    { tytul: "Działania rozwojowe", wpisy: katalog.dzialania },
  ];
  const wpisyKatalogu = grupyKatalogu.flatMap((g) => g.wpisy);
  const wezly = new Map([...graf.wezly, ...wpisyKatalogu].map((w) => [w.id, w]));
  const indeks = Math.min(sciezka, Math.max(graf.sciezki.length - 1, 0));
  const aktualna = graf.sciezki[indeks];
  const szczegoly = (wybrany ? wezly.get(wybrany) : undefined) ?? wezly.get(aktualna?.wezly[0] ?? "stanowisko");
  const niedopasowane = graf.wezly.filter((w) => w.nieprzypisany && !w.id.endsWith(":brak"));

  const linkWezla = (id: string) => (
    <button className="link" type="button" onClick={() => {
      setWybrany(id);
      panelSzczegolow.current?.focus();
    }}>{wezly.get(id)?.nazwa ?? id}</button>
  );

  return (
    <div className="graf-tresc">
      <p className="opis">{katalog.schemat.wersja} · {katalog.wersja_katalogu} · {katalog.wersja_regul}</p>
      <p>Stanowisko {linkWezla("stanowisko")}</p>
      {aktualna && (
        <>
          <label htmlFor={`sciezka-grafu-${graf.cykl_id}`}>Ścieżka kompetencji</label>
          <select id={`sciezka-grafu-${graf.cykl_id}`} value={indeks} onChange={(e) => {
            setSciezka(Number(e.target.value));
            setWybrany(null);
          }}>
            {graf.sciezki.map((s, i) => (
              <option key={s.wezly.join("|")} value={i}>
                {wezly.get(s.wezly[2])?.nazwa} · {wezly.get(s.wezly[1])?.nazwa} · {wezly.get(s.wezly[0])?.nazwa}
              </option>
            ))}
          </select>
          <ol className="graf-sciezka" aria-label="Kroki ścieżki kompetencji">
            {aktualna.wezly.map((id, i) => {
              const w = wezly.get(id);
              return (
                <li key={id}>
                  <button className={`graf-wezel ${w?.status ?? ""}`} type="button"
                    aria-pressed={szczegoly?.id === id} onClick={() => setWybrany(id)}>
                    <span>{ETAPY[i]}</span>
                    <strong>{w?.nazwa}</strong>
                  </button>
                </li>
              );
            })}
          </ol>
        </>
      )}
      {szczegoly && <div ref={panelSzczegolow} tabIndex={-1}><ZrodloWezla wezel={szczegoly} /></div>}
      {graf.propozycje.length > 0 && (
        <div className="sekcja">
          <h3>Kompetencje do rozważenia w rozwoju</h3>
          <ul className="graf-propozycje">
            {graf.propozycje.map((p) => (
              <li key={p.kompetencja_id}>
                {linkWezla(p.kompetencja_id)} <span className="demo">propozycja</span>
                <p className="opis">{p.uzasadnienie}</p>
                <ul>{p.podstawy.map((id) => <li key={id}>{linkWezla(id)}</li>)}</ul>
              </li>
            ))}
          </ul>
        </div>
      )}
      {niedopasowane.length > 0 && (
        <details className="sekcja">
          <summary>Elementy do weryfikacji ({niedopasowane.length})</summary>
          <ul>{niedopasowane.map((w) => <li key={w.id}>{linkWezla(w.id)} <span className="opis">{NIEPRZYPISANY}</span></li>)}</ul>
        </details>
      )}
      <details className="sekcja">
        <summary>Katalog kompetencji, poziomów i działań</summary>
        <div className="graf-katalog">
          {grupyKatalogu.map(({ tytul, wpisy }) => (
            <div key={tytul}>
              <h3>{tytul}</h3>
              <ul>{wpisy.map((w) => <li key={w.id}>{linkWezla(w.id)}</li>)}</ul>
            </div>
          ))}
        </div>
      </details>
    </div>
  );
}

export default function GrafKompetencji({ cyklId, odswiezenie }: { cyklId: number; odswiezenie: Cykl | StanOceny }) {
  const [otwarty, setOtwarty] = useState(false);
  const [graf, setGraf] = useState<GrafOceny | null>(null);
  const [blad, setBlad] = useState<string | null>(null);

  useEffect(() => {
    if (!otwarty) return;
    let nieaktualne = false;
    setBlad(null);
    pobierzOcene(cyklId, true).then((s) => {
      if (!nieaktualne) {
        setGraf(s.graf);
        if (!s.graf) setBlad("Graf jest niedostępny dla tej oceny.");
      }
    }).catch((e) => {
      if (!nieaktualne) setBlad(e instanceof Error ? e.message : String(e));
    });
    return () => { nieaktualne = true; };
  }, [cyklId, otwarty, odswiezenie]);

  return (
    <details className="graf-kompetencji" onToggle={(e) => setOtwarty(e.currentTarget.open)}>
      <summary>Graf kompetencji</summary>
      {otwarty && (blad ? <p className="blad" role="alert">{blad}</p> : graf ? <WidokGrafu graf={graf} /> : <p className="opis">Ładowanie grafu…</p>)}
    </details>
  );
}
