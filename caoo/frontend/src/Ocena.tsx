import { useEffect, useRef, useState } from "react";
import GrafKompetencji from "./GrafKompetencji";
import ObiegOceny, { dzisiaj } from "./ObiegOceny";
import PytaniaPomocnicze from "./PytaniaPomocnicze";
import { pobierzOcene, zapiszOcene, type KryteriumOceny, type StanOceny, type ZmianyOceny } from "./api";

const PUNKTY = [1, 3, 5, 7, 9];

function PunktacjaZPropozycja({ kryterium, propozycja, zablokowana, zapisz }: {
  kryterium: KryteriumOceny;
  propozycja: number;
  zablokowana: boolean;
  zapisz: (zmiany: ZmianyOceny) => Promise<boolean>;
}) {
  const { id, punkty, metodyka } = kryterium;
  const zapisaneUzasadnienie = metodyka?.uzasadnienie_odstepstwa ?? "";
  const [szkicWyboru, setSzkicWyboru] = useState<string | null>(null);
  const [szkicUzasadnienia, setSzkicUzasadnienia] = useState<string | null>(null);
  const [zapisywanie, setZapisywanie] = useState(false);
  const wybor = szkicWyboru ?? (punkty !== null && punkty !== propozycja ? String(punkty) : "");
  const uzasadnienie = szkicUzasadnienia ?? zapisaneUzasadnienie;

  async function zapiszPunktacje(zmiany: ZmianyOceny) {
    setZapisywanie(true);
    if (await zapisz(zmiany)) {
      setSzkicWyboru(null);
      setSzkicUzasadnienia(null);
    }
    setZapisywanie(false);
  }

  return (
    <div className="punktacja-z-propozycja">
      <p><strong>Propozycja {propozycja} pkt</strong></p>
      <p className="opis">{metodyka?.etykieta}</p>
      <details>
        <summary>Jak obliczono propozycję</summary>
        <p className="opis">Nigdy 1, rzadko 3, czasem 5, zwykle 7, zawsze 9. Pomijamy odpowiedzi „Brak danych” i puste. Bierzemy środkową wartość po posortowaniu, a przy czterech odpowiedziach niższą z dwóch środkowych.</p>
      </details>
      <p>Ocena oceniającego {punkty === null ? "—" : `${punkty} pkt`}</p>
      {zapisaneUzasadnienie && <p className="opis">{zapisaneUzasadnienie}</p>}
      {!zablokowana && (
        <>
          <button type="button" disabled={zapisywanie || punkty === propozycja}
            onClick={() => void zapiszPunktacje({ punkty: { [id]: propozycja } })}>
            Przyjmij propozycję
          </button>
          <details>
            <summary>Wybierz inną punktację</summary>
            <label htmlFor={`${id}-punktacja`}>Punkty oceniającego</label>
            <select id={`${id}-punktacja`} value={wybor} disabled={zapisywanie} onChange={(e) => setSzkicWyboru(e.target.value)}>
              <option value="">Wybierz punkty</option>
              {PUNKTY.filter((p) => p !== propozycja).map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
            <label htmlFor={`${id}-odstepstwo`}>Uzasadnienie odstępstwa</label>
            <textarea id={`${id}-odstepstwo`} rows={2} value={uzasadnienie} disabled={zapisywanie}
              onChange={(e) => setSzkicUzasadnienia(e.target.value)} />
            <button type="button" disabled={zapisywanie || !wybor || !uzasadnienie.trim()}
              onClick={() => void zapiszPunktacje({ punkty: { [id]: Number(wybor) }, uzasadnienia_odstepstw: { [id]: uzasadnienie } })}>
              Zapisz ocenę z uzasadnieniem
            </button>
          </details>
          {punkty !== null && <button type="button" className="link" disabled={zapisywanie}
            onClick={() => void zapiszPunktacje({ punkty: { [id]: null } })}>Usuń punkty</button>}
        </>
      )}
    </div>
  );
}

export default function Ocena({ cyklId }: { cyklId: number }) {
  const [s, setS] = useState<StanOceny | null>(null);
  const [bladOdczytu, setBladOdczytu] = useState<string | null>(null);

  useEffect(() => {
    let aktualny = true;
    pobierzOcene(cyklId).then((x) => {
      if (aktualny) { setS(x); setBladOdczytu(null); }
    }).catch((e) => {
      if (aktualny) setBladOdczytu(String(e.message));
    });
    return () => { aktualny = false; };
  }, [cyklId]);
  if (bladOdczytu) return <p className="blad" role="alert">{bladOdczytu}</p>;
  if (!s || s.cykl_id !== cyklId) return <p className="opis">Wczytywanie oceny…</p>;
  return <WidokOceny key={`${cyklId}:${s.numer_wersji}`} s={s} onZmiana={setS} />;
}

function WidokOceny({ s, onZmiana }: { s: StanOceny; onZmiana: (s: StanOceny) => void }) {
  const [uzasadnienie, setUzasadnienie] = useState(s.uzasadnienie ?? "");
  const [wnioski, setWnioski] = useState(s.wnioski_rozwojowe);
  const [sporzadzenie, setSporzadzenie] = useState(s.data_sporzadzenia ?? "");
  const [rozmowa, setRozmowa] = useState(s.data_rozmowy_oceniajacej ?? "");
  const [blad, setBlad] = useState<string | null>(null);
  const [zatwierdzanie, setZatwierdzanie] = useState(false);
  const kolejka = useRef<Promise<boolean | void>>(Promise.resolve());
  const zapisz = (body: ZmianyOceny): Promise<boolean> => {
    if (body.zatwierdz) setZatwierdzanie(true);
    const zapis = kolejka.current.then(async () => {
      try {
        onZmiana(await zapiszOcene(s.cykl_id, { ...body, numer_wersji: s.numer_wersji }));
        setBlad(null);
        return true;
      } catch (e) {
        setBlad(e instanceof Error ? e.message : String(e));
        return false;
      } finally {
        if (body.zatwierdz) setZatwierdzanie(false);
      }
    });
    kolejka.current = zapis;
    return zapis;
  };
  const daneArkusza = {
    uzasadnienie, wnioski_rozwojowe: wnioski, data_sporzadzenia: sporzadzenie || null,
    data_rozmowy_oceniajacej: rozmowa || null,
  };
  const blokada = s.zatwierdzona || zatwierdzanie;
  const w = s.wynik;

  return (
    <div className="ocena">
      <h3>Ocena kryteriów i wynik (§ 11)</h3>
      <table>
        <thead><tr><th>Kryterium</th><th>Punkty</th></tr></thead>
        <tbody>
          {s.kryteria.map((k) => (
            <tr key={k.id}>
              <td>
                <strong>{k.nazwa}</strong> <span className="opis">({k.rodzaj})</span>
                {k.opis && <details className="zrodla"><summary>opis z zał. 1</summary><p className="opis">{k.opis}</p></details>}
                <input className="oczekiwania" placeholder="Ustalone oczekiwania (§ 5 ust. 1 pkt 3)" defaultValue={k.oczekiwania ?? ""}
                  aria-label={`Oczekiwania: ${k.nazwa}`} disabled={blokada}
                  onBlur={(e) => e.target.value !== (k.oczekiwania ?? "") && zapisz({ oczekiwania: { [k.id]: e.target.value } })} />
                {k.metodyka && <PytaniaPomocnicze kryteriumId={k.id} metodyka={k.metodyka} zablokowane={blokada} zapisz={zapisz} />}
              </td>
              <td className="punkty">
                {k.metodyka?.propozycja != null ? (
                  <PunktacjaZPropozycja key={k.metodyka.propozycja} kryterium={k} propozycja={k.metodyka.propozycja}
                    zablokowana={blokada} zapisz={zapisz} />
                ) : PUNKTY.map((p) => (
                  <button key={p} className={k.punkty === p ? "wybrany" : ""} disabled={blokada} aria-pressed={k.punkty === p}
                    onClick={() => zapisz({ punkty: { [k.id]: k.punkty === p ? null : p } })}>{p}</button>
                ))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {w && (
        <div className={`wynik ${w.wynik === "NEGATYWNA" ? "neg" : "poz"}`}>
          <p>Średnia: <strong>{w.srednia}</strong> · poziom: <strong>{w.poziom}</strong> · wynik: <strong>{w.wynik}</strong>
            {!w.kompletny && <span className="opis"> (nie wszystkie kryteria ocenione)</span>}</p>
          <p>Reguła rozstrzygająca: <strong>{w.regula_rozstrzygajaca.nazwa}</strong>{" "}
            <span className="opis">{w.regula_rozstrzygajaca.adnotacje[0]?.lokalizator}</span></p>
          <ol className="slad">{w.slad.map((k, i) => <li key={i}>{k.opis}</li>)}</ol>
        </div>
      )}
      {s.porownanie_excela?.rozni_sie && (
        <p className="braki">Zautomatyzowany Excel pokazałby średnią {s.porownanie_excela.srednia_excel} (suma {s.porownanie_excela.liczba_ocen} ocen
          dzielona przez {s.porownanie_excela.dzielnik_excel}). Według § 11: {s.porownanie_excela.srednia_par_11}. Zob. panel źródeł – rozbieżności.</p>
      )}

      {s.zatwierdzona ? (
        <>
          {s.uzasadnienie && <p className="tresc-arkusza"><strong>Uzasadnienie oceny</strong><br />{s.uzasadnienie}</p>}
          {s.wnioski_rozwojowe && <p className="tresc-arkusza"><strong>Wnioski rozwojowe</strong><br />{s.wnioski_rozwojowe}</p>}
        </>
      ) : (
        <form className="formularz-wersji" onSubmit={(e) => {
          e.preventDefault();
          const przycisk = (e.nativeEvent as SubmitEvent).submitter;
          zapisz({ ...daneArkusza, zatwierdz: przycisk?.getAttribute("value") === "zatwierdz" });
        }}>
          <fieldset disabled={blokada}>
            <legend>Dane arkusza</legend>
            <div className="akcje-wersji">
              <label>Data sporządzenia<input type="date" max={dzisiaj()} value={sporzadzenie} onChange={(e) => setSporzadzenie(e.target.value)} /></label>
              <label>Data rozmowy oceniającej<input type="date" max={sporzadzenie || dzisiaj()} value={rozmowa}
                onChange={(e) => setRozmowa(e.target.value)} /></label>
            </div>
            <p className="opis">Przy pustym polu daty sporządzenia system wpisze datę zatwierdzenia.</p>
            <label>Uzasadnienie oceny
              <textarea rows={3} placeholder="Wymagane przy ocenie negatywnej (§ 11 ust. 3)" value={uzasadnienie}
                onChange={(e) => setUzasadnienie(e.target.value)}
                onBlur={() => { if (uzasadnienie !== (s.uzasadnienie ?? "")) zapisz({ uzasadnienie }); }} />
            </label>
            <label>Wnioski rozwojowe<textarea rows={3} value={wnioski} onChange={(e) => setWnioski(e.target.value)} /></label>
            {s.bledy.length > 0 && <ul className="blad">{s.bledy.map((b) => <li key={b}>{b}</li>)}</ul>}
            <p id="podpis-odreczny" className="opis">Zatwierdź ocenę zapisuje i blokuje wersję. Podpis składa się odręcznie na wydruku.</p>
            <div className="akcje-wersji">
              <button type="submit" value="zapisz">Zapisz dane arkusza</button>
              <button type="submit" value="zatwierdz" className="glowny" aria-describedby="podpis-odreczny">Zatwierdź ocenę</button>
            </div>
          </fieldset>
        </form>
      )}
      {blad && <p className="blad" role="alert">{blad}</p>}
      <GrafKompetencji cyklId={s.cykl_id} odswiezenie={s} />
      <ObiegOceny s={s} onZmiana={onZmiana} />
    </div>
  );
}
