import { useEffect, useRef, useState } from "react";
import {
  dodajNotatkeRozmowy, pobierzRozmowe, zapiszPytaniaRozmowy, zapisanaRola,
  type KryteriumOceny, type PytanieRozmowy, type StanRozmowy,
} from "./api";

type Szkic = { pytania: PytanieRozmowy[]; notatka: string; pytanieId: string; kryteriumId: string };
const puste: Szkic = { pytania: [], notatka: "", pytanieId: "", kryteriumId: "" };
const bladOperacji = (e: unknown) => e instanceof Error ? e.message : String(e);

function odczytajSzkic(klucz: string): Szkic | null {
  try {
    const s = JSON.parse(sessionStorage.getItem(klucz) ?? "null");
    return s && Array.isArray(s.pytania) && s.pytania.length <= 60 && s.pytania.every((p: PytanieRozmowy) =>
      typeof p.id === "string" && typeof p.tresc === "string" && (p.z_propozycji === null || typeof p.z_propozycji === "string")) &&
      [s.notatka, s.pytanieId, s.kryteriumId].every((x) => typeof x === "string") ? s : null;
  } catch { return null; }
}

function WidokRozmowy({ cyklId, kryteria, odswiezenie }: {
  cyklId: number; kryteria: KryteriumOceny[]; odswiezenie: unknown;
}) {
  const klucz = `caoo-rozmowa:${zapisanaRola()}:${cyklId}`;
  const [otwarta, setOtwarta] = useState(false);
  const [stan, setStan] = useState<StanRozmowy | null>(null);
  const [szkic, setSzkic] = useState<Szkic>(puste);
  const [blad, setBlad] = useState<string | null>(null);
  const [komunikat, setKomunikat] = useState("");
  const [zajeta, setZajeta] = useState(false);
  const [odczyt, setOdczyt] = useState(0);
  const zywy = useRef(false);
  const zainicjowany = useRef(false);
  const blokada = useRef(false);
  const operacja = useRef<AbortController | null>(null);
  const odczytKontroler = useRef<AbortController | null>(null);

  useEffect(() => {
    zywy.current = true;
    return () => { zywy.current = false; operacja.current?.abort(); };
  }, []);

  useEffect(() => {
    if (!otwarta || blokada.current) return;
    const kontroler = new AbortController();
    odczytKontroler.current = kontroler;
    pobierzRozmowe(cyklId, kontroler.signal).then((s) => {
      if (kontroler.signal.aborted) return;
      setStan(s);
      setBlad(null);
      if (!zainicjowany.current) {
        const zachowany = odczytajSzkic(klucz);
        setSzkic(zachowany ? {
          ...zachowany,
          pytanieId: s.pytania.some((p) => p.id === zachowany.pytanieId) ? zachowany.pytanieId : "",
          kryteriumId: kryteria.some((k) => k.id === zachowany.kryteriumId) ? zachowany.kryteriumId : "",
        } : { ...puste, pytania: s.pytania });
        if (zachowany) setKomunikat("Przywrócono niezapisany szkic z tej sesji przeglądarki.");
        zainicjowany.current = true;
      } else setSzkic((w) => ({
        ...w,
        pytanieId: s.pytania.some((p) => p.id === w.pytanieId) ? w.pytanieId : "",
        kryteriumId: kryteria.some((k) => k.id === w.kryteriumId) ? w.kryteriumId : "",
      }));
    }).catch((e) => { if (!kontroler.signal.aborted) setBlad(bladOperacji(e)); });
    return () => kontroler.abort();
  }, [cyklId, klucz, otwarta, odswiezenie, odczyt]);

  const niezapisane = !!stan && JSON.stringify(szkic.pytania) !== JSON.stringify(stan.pytania);
  const maSzkic = niezapisane || !!szkic.notatka;
  useEffect(() => {
    if (!zainicjowany.current || !stan) return;
    try {
      if (maSzkic) sessionStorage.setItem(klucz, JSON.stringify(szkic));
      else sessionStorage.removeItem(klucz);
    } catch { /* Szkic pozostaje w pamięci, gdy przeglądarka blokuje magazyn sesji. */ }
  }, [klucz, szkic, maSzkic, stan]);

  useEffect(() => {
    if (!maSzkic) return;
    const ostrzez = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", ostrzez);
    return () => window.removeEventListener("beforeunload", ostrzez);
  }, [maSzkic]);

  function zmienPytania(pytania: PytanieRozmowy[]) {
    setSzkic((s) => ({ ...s, pytania }));
    setKomunikat("");
  }

  async function zapiszPytania() {
    if (blokada.current) return;
    blokada.current = true;
    odczytKontroler.current?.abort();
    setZajeta(true); setBlad(null); setKomunikat("");
    const kontroler = new AbortController(); operacja.current = kontroler;
    try {
      const s = await zapiszPytaniaRozmowy(cyklId, szkic.pytania, kontroler.signal);
      if (zywy.current) {
        setStan(s);
        setSzkic((w) => ({ ...w, pytania: s.pytania, pytanieId: s.pytania.some((p) => p.id === w.pytanieId) ? w.pytanieId : "" }));
        setKomunikat("Zapisano pytania do rozmowy.");
      }
    } catch (e) { if (zywy.current && !kontroler.signal.aborted) setBlad(bladOperacji(e)); }
    finally { blokada.current = false; if (zywy.current) setZajeta(false); }
  }

  async function zapiszNotatke() {
    if (blokada.current || !stan) return;
    if ((szkic.pytanieId && !stan.pytania.some((p) => p.id === szkic.pytanieId)) ||
        (szkic.kryteriumId && !kryteria.some((k) => k.id === szkic.kryteriumId))) {
      setBlad("Wybrane powiązanie jest nieaktualne. Odśwież listę i wybierz pytanie lub kryterium ponownie. Treść notatki pozostaje w szkicu.");
      return;
    }
    blokada.current = true;
    odczytKontroler.current?.abort();
    setZajeta(true); setBlad(null); setKomunikat("");
    const kontroler = new AbortController(); operacja.current = kontroler;
    try {
      const n = await dodajNotatkeRozmowy(cyklId, {
        tresc: szkic.notatka.trim(), pytanie_id: szkic.pytanieId || null, kryterium_id: szkic.kryteriumId || null,
      }, kontroler.signal);
      if (!n || !Number.isInteger(n.id) || n.id <= 0 || typeof n.tresc !== "string" || !n.tresc.trim() ||
          typeof n.zapisano !== "string" || !Number.isFinite(Date.parse(n.zapisano))) {
        throw new Error("API nie potwierdziło poprawnie zapisu notatki.");
      }
      if (zywy.current) {
        setStan((s) => s ? { ...s, notatki: [...s.notatki, n] } : s);
        setSzkic((s) => ({ ...s, notatka: "", pytanieId: "", kryteriumId: "" }));
        setKomunikat("Zapisano notatkę. Punktacja pozostaje bez zmian.");
      }
    } catch (e) {
      if (zywy.current && !kontroler.signal.aborted) setBlad(`${bladOperacji(e)} Przed ponownym zapisem odśwież listę notatek, aby sprawdzić, czy zapis dotarł.`);
    } finally { blokada.current = false; if (zywy.current) setZajeta(false); }
  }

  const nazwyKryteriow = new Map(kryteria.map((k) => [k.id, k.nazwa]));
  const poprawnePytania = szkic.pytania.every((p) => !!p.tresc.trim() && p.tresc.length <= 1000);
  return (
    <details className="rozmowa-oceniajaca" onToggle={(e) => setOtwarta(e.currentTarget.open)}>
      <summary>Rozmowa oceniająca i notatki</summary>
      <p className="opis">Przygotuj pytania do rozmowy, a następnie zapisz własne notatki. To osobna część od pytań pomocniczych do punktacji.</p>
      {blad && <p className="blad" role="alert">{blad}</p>}
      {komunikat && <p className="ok-tekst" role="status">{komunikat}</p>}
      {!stan && !blad && otwarta && <p className="opis">Wczytywanie rozmowy…</p>}
      <button type="button" disabled={zajeta} onClick={() => setOdczyt((n) => n + 1)}>Odśwież propozycje i notatki</button>
      {stan && <>
        <p className="opis">{stan.zasada_notatek} · Wersja pytań: {stan.wersja}</p>
        <fieldset disabled={zajeta}>
          <legend>Pytania do rozmowy</legend>
          <details className="rozmowa-propozycje">
            <summary>Propozycje pytań ({stan.propozycje.length})</summary>
            {stan.propozycje.map((p) => {
              const wybrane = szkic.pytania.some((w) => w.z_propozycji === p.id);
              return <article className="element" key={p.id}>
                <p className="tresc-arkusza"><strong>{p.tresc}</strong></p>
                <p className="opis">{p.etykieta ?? "Zakres rozmowy z przepisu"}</p>
                <p><strong>Uzasadnienie:</strong> {p.uzasadnienie}</p>
                <p><strong>Powiązanie:</strong> {p.powiazanie.opis ?? p.powiazanie.typ}</p>
                <details className="zrodla"><summary>Źródła pytania</summary>
                  {p.adnotacje.map((a, i) => <div className="adnotacja" key={i}>
                    <div className="zrodlo"><span className={`kategoria ${a.kategoria === "PRZEPIS" ? "przepis" : ""}`}>{a.kategoria}</span>
                      {a.doc_id} {a.lokalizator}{a.strona != null && ` · s. ${a.strona}`}</div>
                    {a["cytat_dosłowny"] && <blockquote>{a["cytat_dosłowny"]}</blockquote>}
                    {a.uzasadnienie && <p className="opis">{a.uzasadnienie}</p>}
                    {a["zatwierdził"] && <p className="opis">Zatwierdził: {a["zatwierdził"]}</p>}
                  </div>)}
                </details>
                <button type="button" disabled={wybrane || szkic.pytania.length >= 60} onClick={() => zmienPytania([
                  ...szkic.pytania, { id: `p-${crypto.randomUUID()}`, tresc: p.tresc, z_propozycji: p.id },
                ])}>{wybrane ? "Dodano do pytań" : "Dodaj pytanie"}</button>
              </article>;
            })}
          </details>
          <h4>Wybrane i własne pytania ({szkic.pytania.length}/60)</h4>
          {!szkic.pytania.length && <p className="opis">Dodaj propozycję albo własne pytanie.</p>}
          {szkic.pytania.map((p, i) => <div className="element" key={p.id}>
            <label htmlFor={`rozmowa-pytanie-${cyklId}-${p.id}`}>Pytanie {i + 1} · {p.z_propozycji ? "z propozycji" : "własne"}</label>
            <textarea id={`rozmowa-pytanie-${cyklId}-${p.id}`} rows={3} maxLength={1000} value={p.tresc}
              onChange={(e) => zmienPytania(szkic.pytania.map((w) => w.id === p.id ? { ...w, tresc: e.target.value } : w))} />
            {p.z_propozycji && !stan.propozycje.some((w) => w.id === p.z_propozycji) && <p className="braki">
              Ta propozycja nie jest już dostępna. Zachowaj treść jako własne pytanie, aby zapisać listę.
              <button type="button" onClick={() => zmienPytania(szkic.pytania.map((w) => w.id === p.id ? { ...w, z_propozycji: null } : w))}>Zachowaj jako własne</button>
            </p>}
            <button type="button" className="link" onClick={() => zmienPytania(szkic.pytania.filter((w) => w.id !== p.id))}>Usuń pytanie {i + 1}</button>
          </div>)}
          <div className="akcje-wersji">
            <button type="button" disabled={szkic.pytania.length >= 60} onClick={() => zmienPytania([
              ...szkic.pytania, { id: `w-${crypto.randomUUID()}`, tresc: "", z_propozycji: null },
            ])}>Dodaj własne pytanie</button>
            <button type="button" className="glowny" disabled={!niezapisane || !poprawnePytania} onClick={() => void zapiszPytania()}>Zapisz pytania do rozmowy</button>
          </div>
          {niezapisane && <p className="braki" role="status">Masz niezapisane pytania. Szkic zostaje w tej sesji przeglądarki dla tej roli i cyklu.</p>}
          {!poprawnePytania && <p className="blad">Każde pytanie musi zawierać treść (do 1000 znaków).</p>}
        </fieldset>
        <form onSubmit={(e) => { e.preventDefault(); void zapiszNotatke(); }}>
          <fieldset disabled={zajeta}>
            <legend>Nowa notatka z rozmowy</legend>
            <p className="opis">Zapisane notatki stanowią historię rozmowy. Możesz dopisywać kolejne notatki.</p>
            <label htmlFor={`notatka-rozmowy-${cyklId}`}>Treść notatki (do 4000 znaków)</label>
            <textarea id={`notatka-rozmowy-${cyklId}`} rows={4} maxLength={4000} value={szkic.notatka}
              onChange={(e) => setSzkic((s) => ({ ...s, notatka: e.target.value }))} />
            <label htmlFor={`notatka-pytanie-${cyklId}`}>Powiązanie z zapisanym pytaniem</label>
            <select id={`notatka-pytanie-${cyklId}`} value={szkic.pytanieId} onChange={(e) => setSzkic((s) => ({ ...s, pytanieId: e.target.value }))}>
              <option value="">Bez powiązania z pytaniem</option>
              {stan.pytania.map((p, i) => <option key={p.id} value={p.id}>{i + 1}. {p.tresc}</option>)}
            </select>
            <label htmlFor={`notatka-kryterium-${cyklId}`}>Powiązanie z kryterium</label>
            <select id={`notatka-kryterium-${cyklId}`} value={szkic.kryteriumId} onChange={(e) => setSzkic((s) => ({ ...s, kryteriumId: e.target.value }))}>
              <option value="">Bez powiązania z kryterium</option>
              {kryteria.map((k) => <option key={k.id} value={k.id}>{k.nazwa}</option>)}
            </select>
            <button type="submit" className="glowny" disabled={!szkic.notatka.trim() || szkic.notatka.length > 4000}>Zapisz notatkę</button>
          </fieldset>
        </form>
        <h4>Zapisane notatki ({stan.notatki.length})</h4>
        {!stan.notatki.length && <p className="opis">Brak zapisanych notatek.</p>}
        {stan.notatki.map((n) => <article className="element" key={n.id}>
          <p className="opis"><time dateTime={n.zapisano}>{new Date(n.zapisano).toLocaleString("pl-PL")}</time>
            {n.pytanie_id && ` · Pytanie: ${stan.pytania.find((p) => p.id === n.pytanie_id)?.tresc ?? "pytanie usunięte z listy"}`}
            {n.kryterium_id && ` · Kryterium: ${nazwyKryteriow.get(n.kryterium_id) ?? n.kryterium_id}`}</p>
          <p className="tresc-arkusza">{n.tresc}</p>
        </article>)}
      </>}
    </details>
  );
}

export default function Rozmowa(props: { cyklId: number; kryteria: KryteriumOceny[]; odswiezenie: unknown }) {
  return <WidokRozmowy key={`${zapisanaRola()}:${props.cyklId}`} {...props} />;
}
