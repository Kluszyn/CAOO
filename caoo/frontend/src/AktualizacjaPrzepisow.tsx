import { useCallback, useEffect, useState } from "react";
import {
  analizujAkt, opublikuj, pobierzAktDemo, pobierzAkty, pobierzZadania, pobierzZestawienie, rozstrzygnijZadanie, rozstrzygnijZbiorczo,
  wgrajAkt, zdecyduj,
  type AktZmieniajacy, type CytatZrodla, type WynikKrotki, type ZadanieDecyzyjne, type Zestawienie,
} from "./api";

const TRYB: Record<string, string> = { NA_ŻYWO: "na żywo", ZAPISANE: "zapisane", EKSTRAKCYJNE: "ekstrakcyjne" };

function Cytaty({ tytul, cytaty }: { tytul: string; cytaty: CytatZrodla[] }) {
  if (cytaty.length === 0) return null;
  return (
    <div className="adnotacja">
      <div className="zrodlo">{tytul}</div>
      {cytaty.map((c) => <blockquote key={c.fragment_id + c.cytat}>{c.cytat}</blockquote>)}
    </div>
  );
}

const Wynik = ({ w }: { w: WynikKrotki | null }) =>
  w ? <span className={w.wynik === "NEGATYWNA" ? "blad" : "ok-tekst"}>{w.wynik} · {w.poziom} · {w.srednia}</span> : <span className="opis">niekompletna</span>;

function Zadania({ odswiez }: { odswiez: number }) {
  const [zadania, setZadania] = useState<ZadanieDecyzyjne[]>([]);
  const [uzasadnienia, setUzasadnienia] = useState<Record<number, string>>({});
  const [blad, setBlad] = useState<string | null>(null);
  const wczytaj = useCallback(() => { pobierzZadania().then(setZadania).catch((e: Error) => setBlad(e.message)); }, []);
  useEffect(wczytaj, [wczytaj, odswiez]);
  const [zbiorcze, setZbiorcze] = useState("");
  if (zadania.length === 0) return null;
  const otwarte = zadania.filter((z) => z.status === "OTWARTE");
  const wspolne = otwarte.length > 1 && otwarte.every((z) => z.opcje.join() === otwarte[0].opcje.join()) ? otwarte[0].opcje : [];
  return (
    <section>
      <h2>Zadania decyzyjne – oceny w toku</h2>
      {blad && <p className="blad">{blad}</p>}
      {wspolne.length > 0 && (
        <div className="wiersz-lewy">
          <input className="oczekiwania" placeholder={`Uzasadnienie dla wszystkich otwartych (${otwarte.length})`} value={zbiorcze}
            onChange={(e) => setZbiorcze(e.target.value)} />
          {wspolne.map((o) => (
            <button key={o} onClick={() => rozstrzygnijZbiorczo(otwarte.map((z) => z.id), o, zbiorcze).then(wczytaj).catch((e: Error) => setBlad(e.message))}>
              Wszystkie: {o}
            </button>
          ))}
        </div>
      )}
      {zadania.map((z) => (
        <div key={z.id} className="element">
          <p><strong>Cykl {z.cykl_id}</strong> · {z.pracownik_id} · <span className={z.status === "OTWARTE" ? "status-brak" : "status-zrodlo"}>{z.status === "OTWARTE" ? "otwarte" : "rozstrzygnięte"}</span></p>
          <p className="opis">{z.opis}</p>
          <ul>{z.powody.map((p) => <li key={p}>{p}</li>)}</ul>
          {z.status === "OTWARTE" ? (
            <div className="wiersz-lewy">
              <input className="oczekiwania" placeholder="Uzasadnienie wyboru (wymagane)" value={uzasadnienia[z.id] ?? ""}
                onChange={(e) => setUzasadnienia({ ...uzasadnienia, [z.id]: e.target.value })} />
              {z.opcje.map((o) => (
                <button key={o} onClick={() => rozstrzygnijZadanie(z.id, o, uzasadnienia[z.id] ?? "").then(wczytaj).catch((e: Error) => setBlad(e.message))}>
                  Stosuj {o}
                </button>
              ))}
            </div>
          ) : <p>Wybrano <strong>{z.wybor}</strong> ({z.rozstrzygnal}): {z.uzasadnienie}</p>}
        </div>
      ))}
    </section>
  );
}

/** Aktualizacja przepisów v1 → v2 (#25): wgranie aktu, analiza LLM, przed/po, publikacja, zadania. */
export default function AktualizacjaPrzepisow({ onOpublikowano }: { onOpublikowano: () => void }) {
  const [akty, setAkty] = useState<AktZmieniajacy[]>([]);
  const [tekst, setTekst] = useState("");
  const [nazwa, setNazwa] = useState("");
  const [akt, setAkt] = useState<AktZmieniajacy | null>(null);
  const [zest, setZest] = useState<Zestawienie | null>(null);
  const [wartosci, setWartosci] = useState<Record<string, string>>({});
  const [od, setOd] = useState("2027-01-01");
  const [wersja, setWersja] = useState("przepisy-demo-v2");
  const [blad, setBlad] = useState<string | null>(null);
  const [zajete, setZajete] = useState(false);
  const [odswiez, setOdswiez] = useState(0);
  const [potwierdzam, setPotwierdzam] = useState(false);
  const [uzasadnienieDaty, setUzasadnienieDaty] = useState("");

  useEffect(() => {
    pobierzAkty().then((a) => { setAkty(a); setAkt(a.at(-1) ?? null); }).catch((e: Error) => setBlad(e.message));
  }, []);
  useEffect(() => {
    if (akt?.analiza?.data_wejscia) setOd(akt.analiza.data_wejscia.data);
  }, [akt?.analiza?.data_wejscia?.data]);
  useEffect(() => {
    setZest(null);
    if (akt && Object.values(akt.decyzje).some((d) => d.przyjeta)) pobierzZestawienie(akt.id).then(setZest).catch((e: Error) => setBlad(e.message));
  }, [akt]);

  const wykonaj = (p: Promise<AktZmieniajacy>) => {
    setBlad(null); setZajete(true);
    p.then((a) => { setAkt(a); setAkty((x) => [...x.filter((y) => y.id !== a.id), a]); })
      .catch((e: Error) => setBlad(e.message)).finally(() => setZajete(false));
  };

  return (
    <>
      <section>
        <h2>Akt zmieniający</h2>
        <p className="opis">Samo wgranie nie zmienia aktywnych reguł. Zmiany wchodzą dopiero po publikacji nowej wersji.</p>
        {blad && <p className="blad">{blad}</p>}
        {akty.length > 0 && (
          <p>Wgrane: {akty.map((a) => (
            <button key={a.id} className="link" onClick={() => setAkt(a)}>{a.nazwa} ({a.status}) </button>
          ))}</p>
        )}
        <div className="wiersz-lewy">
          <button onClick={() => pobierzAktDemo().then((d) => { setTekst(d.tekst); setNazwa(d.nazwa); }).catch((e: Error) => setBlad(e.message))}>
            Wczytaj akt demo (fikcyjny)
          </button>
          <span className="demo">dane demonstracyjne</span>
        </div>
        <input className="oczekiwania" placeholder="Nazwa aktu" value={nazwa} onChange={(e) => setNazwa(e.target.value)} />
        <textarea className="akt" rows={8} placeholder="Treść aktu zmieniającego" value={tekst} onChange={(e) => setTekst(e.target.value)} />
        <button className="glowny" disabled={zajete || !tekst || !nazwa} onClick={() => wykonaj(wgrajAkt(nazwa, tekst))}>Wgraj akt</button>
      </section>

      {akt && (
        <section>
          <div className="wiersz">
            <h2>{akt.nazwa}</h2>
            <span className="opis">{akt.status} · wgrał {akt.wgral} {akt.wgrano}</span>
          </div>
          {akt.status !== "OPUBLIKOWANY" && (
            <button className="glowny" disabled={zajete} onClick={() => wykonaj(analizujAkt(akt.id))}>
              {akt.analiza ? "Analizuj ponownie (LLM)" : "Analizuj zmiany (LLM)"}
            </button>
          )}
          {akt.analiza?.propozycje.map((p) => {
            const d = akt.decyzje[p.parametr];
            const wartosc = wartosci[p.parametr] ?? d?.wartosc_po ?? p.wartosc_po ?? "";
            return (
              <div key={p.parametr} className={`element ${p.zmienia === null ? "brak" : ""}`}>
                <div className="wiersz-lewy">
                  <strong>{p.nazwa}</strong>
                  <span className="demo">{p.etykieta}</span>
                  {p.tryb && <span className="znacznik">{TRYB[p.tryb]}</span>}
                </div>
                <p>
                  {p.zmienia === null ? "Brak odpowiedzi – oceń ręcznie." : p.zmienia ? "Akt zmienia parametr." : "Akt nie zmienia parametru."}
                  {" "}Przed: <strong>{p.wartosc_przed}</strong> · po:{" "}
                  <input aria-label={`Nowa wartość: ${p.nazwa}`} size={5} value={wartosc} disabled={akt.status === "OPUBLIKOWANY"}
                    onChange={(e) => setWartosci({ ...wartosci, [p.parametr]: e.target.value })} />
                </p>
                {p.niejasnosci.length > 0 && <div className="braki">Niejasności:<ul>{p.niejasnosci.map((n) => <li key={n}>{n}</li>)}</ul></div>}
                <Cytaty tytul="Akt zmieniający" cytaty={p.cytaty_aktu} />
                <Cytaty tytul={`Obecne przepisy (${akt.analiza!.wersja_bazowa})`} cytaty={p.cytaty_przed} />
                {akt.status !== "OPUBLIKOWANY" && (
                  <div className="wiersz-lewy">
                    <button disabled={zajete} onClick={() => wykonaj(zdecyduj(akt.id, p.parametr, true, wartosc || null))}>Przyjmij</button>
                    <button disabled={zajete} onClick={() => wykonaj(zdecyduj(akt.id, p.parametr, false, null))}>Odrzuć</button>
                  </div>
                )}
                {d && <p className="opis">Decyzja: {d.przyjeta ? `przyjęta (${d.wartosc_po ?? p.wartosc_po})` : "odrzucona"} · {d.osoba} {d.data}</p>}
              </div>
            );
          })}
          {akt.analiza && akt.analiza.nieobslugiwane.length > 0 && (
            <div className="braki">
              Przepisy aktu bez parametru w konfiguracji:
              <ul>{akt.analiza.nieobslugiwane.map((n) => <li key={n.fragment_id}><span className="status-brak">{n.flaga}</span> {n.tekst}</li>)}</ul>
            </div>
          )}
        </section>
      )}

      {akt && zest && (
        <section>
          <h2>Zestawienie przed / po i wpływ</h2>
          <table>
            <thead><tr><th>Parametr</th><th>{zest.wersja_bazowa}</th><th>po zmianie</th></tr></thead>
            <tbody>{zest.zmiany.map((z) => <tr key={z.parametr}><td>{z.nazwa}</td><td>{z.przed}</td><td><strong>{z.po}</strong></td></tr>)}</tbody>
          </table>
          <h3 className="odstep">Obszary aplikacji</h3>
          <table>
            <thead><tr><th>Obszar</th><th>Zmiana</th><th>Czego dotyczy</th></tr></thead>
            <tbody>{zest.obszary.map((o) => (
              <tr key={o.id} className={o.zmieniony ? "uwaga" : ""}><td>{o.nazwa}</td><td>{o.zmieniony ? "zmienia się" : "bez zmian"}</td><td>{o.wplyw}</td></tr>
            ))}</tbody>
          </table>
          <h3 className="odstep">Scenariusze kontrolne</h3>
          <table>
            <thead><tr><th>Scenariusz</th><th>Oceny</th><th>Przed</th><th>Po</th></tr></thead>
            <tbody>{zest.scenariusze.map((s) => (
              <tr key={s.id} className={s.zmiana ? "uwaga" : ""}><td>{s.opis}</td><td>{s.oceny.join(", ")}</td><td><Wynik w={s.przed} /></td><td><Wynik w={s.po} /></td></tr>
            ))}</tbody>
          </table>
          <h3 className="odstep">Oceny</h3>
          {zest.oceny.length === 0 ? <p className="opis">Brak ocen w systemie.</p> : <table>
            <thead><tr><th>Cykl</th><th>Stan</th><th>Według własnej wersji</th><th>Po zmianie</th><th>Przejście</th></tr></thead>
            <tbody>{zest.oceny.map((o) => (
              <tr key={o.cykl_id} className={o.niejasne ? "uwaga" : ""}>
                <td>{o.cykl_id} · {o.pracownik_id}</td>
                <td>{o.zakonczona ? "zakończona" : "w toku"} ({o.wersja_regul})</td>
                <td><Wynik w={o.wynik_wlasny} /></td>
                <td>{o.zakonczona ? <span className="opis">bez zmian – odtwarzana według {o.wersja_regul}</span> : <Wynik w={o.wynik_po} />}</td>
                <td>{o.niejasne ? <span className="status-brak">niejasne – zadanie decyzyjne</span> : "jasne"}{o.powody.length > 0 && <p className="opis">{o.powody.join(" ")}</p>}</td>
              </tr>
            ))}</tbody>
          </table>}
          {akt.status !== "OPUBLIKOWANY" ? (
            <div className="wiersz-lewy">
              <label>Wersja <input value={wersja} onChange={(e) => setWersja(e.target.value)} /></label>
              <label>Obowiązuje od <input type="date" value={od} onChange={(e) => setOd(e.target.value)} /></label>
              {zest.data_wejscia && <span className="opis">z aktu: „{zest.data_wejscia.cytat}”</span>}
              {zest.data_wejscia && od !== zest.data_wejscia.data && (
                <input className="oczekiwania" placeholder="Uzasadnienie innej daty niż w akcie (wymagane)" value={uzasadnienieDaty}
                  onChange={(e) => setUzasadnienieDaty(e.target.value)} />
              )}
              {zest.nieobslugiwane.length > 0 && (
                <label><input type="checkbox" checked={potwierdzam} onChange={(e) => setPotwierdzam(e.target.checked)} />{" "}
                  Rozumiem, że przepisy oznaczone „wymaga zmiany aplikacji” nie wejdą do tej wersji</label>
              )}
              <button className="glowny" disabled={zajete || (zest.nieobslugiwane.length > 0 && !potwierdzam)}
                onClick={() => wykonaj(opublikuj(akt.id, od, wersja, potwierdzam, uzasadnienieDaty).then((a) => { onOpublikowano(); setOdswiez((x) => x + 1); return a; }))}>
                Opublikuj wersję
              </button>
            </div>
          ) : (
            <p className="ok-tekst">Opublikowano {akt.publikacja?.wersja}, obowiązuje od {akt.publikacja?.obowiazuje_od} ({akt.publikacja?.osoba}).</p>
          )}
        </section>
      )}

      <Zadania odswiez={odswiez} />
    </>
  );
}
