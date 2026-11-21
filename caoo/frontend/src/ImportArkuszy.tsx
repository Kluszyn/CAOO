import { useEffect, useState } from "react";
import { pobierzArkusze, sprawdzArkusz, type RaportArkusza, type WersjaPolaArkusza, type ZmianaArkusza } from "./api";

const OPCJE = { przed: "Przed zmianą", po: "Po zmianie" } as const;

function PoleZmiany({ zmiana, wartosc, wybierz }: {
  zmiana: ZmianaArkusza;
  wartosc: WersjaPolaArkusza | undefined;
  wybierz: (opcja: WersjaPolaArkusza) => void;
}) {
  return (
    <fieldset>
      <legend>{zmiana.pole} · {zmiana.lokalizator}</legend>
      {(Object.keys(OPCJE) as WersjaPolaArkusza[]).map((opcja) => (
        <label className="wybor" key={opcja}>
          <input type="radio" name={zmiana.id} checked={wartosc === opcja} onChange={() => wybierz(opcja)} />
          {OPCJE[opcja]} · {zmiana[opcja].filter(Boolean).join(", ") || "puste pole"}
        </label>
      ))}
    </fieldset>
  );
}

function ZapisRaportu({ raport }: { raport: RaportArkusza }) {
  const pobierz = () => {
    const url = URL.createObjectURL(new Blob([JSON.stringify(raport, null, 2)], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = raport.plik.replace(/\.[^.]+$/, ".json");
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  };
  return <button className="glowny" onClick={pobierz}>Pobierz dane demo (JSON)</button>;
}

function Podsumowanie({ raport }: { raport: RaportArkusza }) {
  const wiersze = [
    ["Średnia", raport.srednia_wpisana, raport.srednia_przeliczona],
    ["Ogólny poziom", raport.poziom_wpisany, raport.poziom_przeliczony],
    ["Wynik", raport.wynik_wpisany, raport.wynik_przeliczony],
  ];
  return (
    <div className="sekcja">
      <h3>Przeliczenie oceny</h3>
      <table>
        <thead><tr><th>Pole</th><th>W arkuszu</th><th>Z obliczenia</th></tr></thead>
        <tbody>{wiersze.map(([pole, wpisana, obliczona]) => (
          <tr key={pole}><th scope="row">{pole}</th><td>{wpisana ?? "—"}</td><td>{obliczona ?? "—"}</td></tr>
        ))}</tbody>
      </table>
      {raport.podstawa_wyniku?.lokalizator && (
        <p className="opis">Podstawa obliczenia · {raport.podstawa_wyniku.lokalizator} · {raport.wersja_regul}</p>
      )}
      {raport.kryteria.length > 0 && (
        <details>
          <summary>Oceny cząstkowe ({raport.kryteria.length})</summary>
          <ul>{raport.kryteria.map((k) => (
            <li key={k.lokalizator}>{k.nazwa} · {k.punkty ?? "do sprawdzenia"}</li>
          ))}</ul>
        </details>
      )}
      {raport.daty.length > 0 && (
        <details>
          <summary>Daty ({raport.daty.length})</summary>
          <ul>{raport.daty.map((d, i) => (
            <li key={i} className={d.data ? "" : "blad"}>{d.zapis} · {d.lokalizator}</li>
          ))}</ul>
        </details>
      )}
    </div>
  );
}

export default function ImportArkuszy() {
  const [pliki, setPliki] = useState<string[]>([]);
  const [plik, setPlik] = useState("");
  const [wybory, setWybory] = useState<Record<string, WersjaPolaArkusza>>({});
  const [raport, setRaport] = useState<RaportArkusza | null>(null);
  const [blad, setBlad] = useState<string | null>(null);
  const [ladowanie, setLadowanie] = useState(true);

  useEffect(() => {
    const kontroler = new AbortController();
    pobierzArkusze(kontroler.signal).then((dane) => {
      if (kontroler.signal.aborted) return;
      setPliki(dane);
      setPlik(dane[0] ?? "");
      if (dane.length === 0) setLadowanie(false);
    }).catch(() => {
      if (!kontroler.signal.aborted) {
        setBlad("Nie udało się odczytać listy arkuszy.");
        setLadowanie(false);
      }
    });
    return () => kontroler.abort();
  }, []);

  useEffect(() => {
    if (!plik) return;
    const kontroler = new AbortController();
    setLadowanie(true);
    setBlad(null);
    sprawdzArkusz(plik, wybory, kontroler.signal)
      .then((dane) => { if (!kontroler.signal.aborted) setRaport(dane); })
      .catch((e) => { if (!kontroler.signal.aborted) setBlad(String(e.message)); })
      .finally(() => { if (!kontroler.signal.aborted) setLadowanie(false); });
    return () => kontroler.abort();
  }, [plik, wybory]);

  return (
    <section>
      <h2>Import przykładowych arkuszy</h2>
      <p className="opis">Sprawdź plik, rozstrzygnij śledzone zmiany i pobierz dane demonstracyjne.</p>
      {pliki.length > 0 && (
        <label>Arkusz <select value={plik} onChange={(e) => {
          setPlik(e.target.value);
          setWybory({});
          setRaport(null);
        }}>
          {pliki.map((nazwa) => <option key={nazwa}>{nazwa}</option>)}
        </select></label>
      )}
      {ladowanie && <p role="status">Sprawdzanie arkusza…</p>}
      {blad && <p className="blad" role="alert">{blad}</p>}
      {!ladowanie && !blad && pliki.length === 0 && <p>Brak plików w folderze przykładów.</p>}
      {raport && (
        <>
          {raport.liczba_sledzonych_zmian > 0 && (
            <div className="sekcja">
              <h3>Śledzone zmiany</h3>
              <p className="opis">Wybierz wersję pól poniżej. Pozostały tekst odczytano w wersji po zmianach.</p>
              {raport.zmiany.map((z) => <PoleZmiany key={z.id} zmiana={z} wartosc={wybory[z.id]}
                wybierz={(opcja) => setWybory((stan) => ({ ...stan, [z.id]: opcja }))} />)}
            </div>
          )}
          {raport.uwagi.length > 0 && (
            <div className="braki">
              <h3>Uwagi do importu</h3>
              <ul>{raport.uwagi.map((u, i) => (
                <li className={u.poziom === "blad" ? "blad" : ""} key={i}>{u.komunikat} · {u.lokalizator}</li>
              ))}</ul>
            </div>
          )}
          <Podsumowanie raport={raport} />
          {!ladowanie && !blad && raport.gotowy && <ZapisRaportu raport={raport} />}
        </>
      )}
    </section>
  );
}
