import { useEffect, useState, type FormEvent } from "react";
import {
  otworzPlik,
  pobierzPonowneKryteria, rozpatrzWniosek, zapiszPonowneKryteria, zlozWniosek,
  zmienOceniajacego, zmienStanowisko, type Cykl, type KryteriaDoUstalenia, type ZmianaStanowiska,
} from "./api";

const dzis = () => new Date().toLocaleDateString("sv-SE", { timeZone: "Europe/Warsaw" });
const dataPl = (iso: string) => iso.split("-").reverse().join(".");
const pole = (dane: FormData, nazwa: string) => String(dane.get(nazwa) ?? "");
const STATUS_WNIOSKU = {
  OCZEKUJE: "Do rozpatrzenia",
  UWZGLEDNIONY: "Uwzględniono",
  ODRZUCONY: "Odrzucono",
};

export function ostatniePonowienie(c: Cykl): ZmianaStanowiska | undefined {
  return [...c.zmiany_stanowiska].reverse().find((z) => z.tryb === "PONOWNE_KRYTERIA");
}

function FormularzPonowienia({ cykl, zmiana, wToku, wykonaj }: {
  cykl: number;
  zmiana: ZmianaStanowiska;
  wToku: boolean;
  wykonaj: (akcja: () => Promise<Cykl>, komunikat: string) => void;
}) {
  const [kryteria, ustawKryteria] = useState<KryteriaDoUstalenia | null>(null);
  const [wybrane, ustawWybrane] = useState<string[]>([]);
  const [blad, ustawBlad] = useState<string | null>(null);

  useEffect(() => {
    const kontroler = new AbortController();
    pobierzPonowneKryteria(cykl, kontroler.signal).then(ustawKryteria).catch((e) => {
      if (!kontroler.signal.aborted) ustawBlad(e.message);
    });
    return () => kontroler.abort();
  }, [cykl]);

  const termin = zmiana.termin_kryteriow;
  return (
    <div className="braki">
      <h3>Ponowne ustalenie kryteriów</h3>
      {termin && (
        <>
          <p>Termin ustalenia kryteriów <strong>{dataPl(termin.termin)}</strong> · § 9 ust. 1</p>
          <ul className="slad">{termin.slad.map((k) => <li key={k.regula}>{k.opis}</li>)}</ul>
        </>
      )}
      {blad && <p role="alert" className="blad">{blad}</p>}
      {kryteria && (
        <form onSubmit={(e) => {
          e.preventDefault();
          const dane = new FormData(e.currentTarget);
          wykonaj(() => zapiszPonowneKryteria(cykl, pole(dane, "data"), wybrane), "Zapisano nowe kryteria. Oceniany może potwierdzić zapoznanie.");
        }}>
          <p>Arkusz {kryteria.arkusz} · obowiązkowe kryteria</p>
          <ul>{kryteria.obowiazkowe.map((k) => <li key={k.id}>{k.nazwa}</li>)}</ul>
          <details>
            <summary>Kryteria dodatkowe ({wybrane.length}/{kryteria.limit_dodatkowych})</summary>
            {kryteria.dostepne_dodatkowe.map((k) => (
              <label className="wybor" key={k.id}>
                <input type="checkbox" checked={wybrane.includes(k.id)}
                  disabled={wToku || (!wybrane.includes(k.id) && wybrane.length >= kryteria.limit_dodatkowych)}
                  onChange={(e) => ustawWybrane((lista) => e.target.checked ? [...lista, k.id] : lista.filter((id) => id !== k.id))} />
                {k.nazwa}
              </label>
            ))}
          </details>
          <div className="wiersz-lewy">
            <label>Data ponownego ustalenia <input type="date" name="data" defaultValue={dzis()} required /></label>
            <button disabled={wToku}>Zapisz nowe kryteria</button>
          </div>
        </form>
      )}
    </div>
  );
}

export default function ZmianyCyklu({ cykl, onZmiana }: { cykl: Cykl; onZmiana: (cykl: Cykl) => void }) {
  const [wToku, ustawWToku] = useState(false);
  const [blad, ustawBlad] = useState<string | null>(null);
  const [komunikat, ustawKomunikat] = useState<string | null>(null);
  const stanowisko = cykl.zmiany_stanowiska[cykl.zmiany_stanowiska.length - 1];
  const oceniajacy = cykl.zmiany_oceniajacego[cykl.zmiany_oceniajacego.length - 1];
  const ponowienie = ostatniePonowienie(cykl);

  function wykonaj(akcja: () => Promise<Cykl>, tekst: string) {
    ustawWToku(true);
    ustawBlad(null);
    ustawKomunikat(null);
    akcja().then((nowy) => {
      onZmiana(nowy);
      ustawKomunikat(tekst);
    }).catch((e) => ustawBlad(e.message)).finally(() => ustawWToku(false));
  }

  function zapiszStanowisko(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const dane = new FormData(e.currentTarget);
    wykonaj(() => zmienStanowisko(cykl.id, {
      data: pole(dane, "data"), stanowisko: pole(dane, "stanowisko"),
      istotna_zmiana: dane.has("istotna"), arkusz: pole(dane, "arkusz") as ZmianaStanowiska["arkusz"],
    }), "Zapisano zmianę stanowiska.");
  }

  return (
    <div className="sekcja">
      {cykl.termin_oceny && <p>Termin sporządzenia oceny <strong>{dataPl(cykl.termin_oceny)}</strong></p>}
      {stanowisko && (
        <p>Aktualne stanowisko w ocenie · <strong>{stanowisko.stanowisko}</strong> · od {dataPl(stanowisko.data)}</p>
      )}
      {oceniajacy && <p>Aktualny oceniający · <strong>{oceniajacy.aktualny}</strong> · od {dataPl(oceniajacy.data)}</p>}
      {stanowisko?.ocena_od && stanowisko.ocena_do && (
        <p className="braki">Ocena dotychczasowych obowiązków od {dataPl(stanowisko.ocena_od)} do {dataPl(stanowisko.ocena_do)} · § 9 ust. 2</p>
      )}
      {ponowienie && !ponowienie.ustalono_kryteria && !cykl.zatwierdzona && (
        <FormularzPonowienia key={cykl.zmiany_stanowiska.length} cykl={cykl.id} zmiana={ponowienie} wToku={wToku} wykonaj={wykonaj} />
      )}
      {ponowienie?.ustalono_kryteria && <p>Nowe kryteria ustalono {dataPl(ponowienie.ustalono_kryteria)}.</p>}
      {!cykl.zatwierdzona && (
        <>
          <details>
            <summary>Zmiana stanowiska</summary>
            <form className="wiersz-lewy" onSubmit={zapiszStanowisko}>
              <label>Nowe stanowisko <input name="stanowisko" maxLength={160} required /></label>
              <label>Data zmiany stanowiska <input type="date" name="data" defaultValue={dzis()} required /></label>
              <label>Arkusz po zmianie <select name="arkusz" defaultValue={cykl.arkusz}>
                <option value="A">A · kierownik urzędu</option>
                <option value="B">B · kieruje pracownikami</option>
                <option value="C">C · pozostałe stanowiska</option>
              </select></label>
              <label><input type="checkbox" name="istotna" /> Istotna zmiana zakresu obowiązków</label>
              <button disabled={wToku}>Zapisz zmianę stanowiska</button>
            </form>
          </details>
          <details>
            <summary>Zmiana oceniającego</summary>
            <p className="opis">Nowa osoba ocenia według dotychczasowych kryteriów (§ 9 ust. 3).</p>
            <form className="wiersz-lewy" onSubmit={(e) => {
              e.preventDefault();
              const dane = new FormData(e.currentTarget);
              wykonaj(() => zmienOceniajacego(cykl.id, pole(dane, "data"), pole(dane, "osoba")), "Zapisano nową osobę oceniającą.");
            }}>
              <label>Nowy oceniający <input name="osoba" maxLength={160} required /></label>
              <label>Data zmiany oceniającego <input type="date" name="data" defaultValue={dzis()} required /></label>
              <button disabled={wToku}>Zapisz zmianę oceniającego</button>
            </form>
          </details>
          <details>
            <summary>Wniosek ocenianego o zmianę terminu</summary>
            <form className="wiersz-lewy" onSubmit={(e) => {
              e.preventDefault();
              const dane = new FormData(e.currentTarget);
              wykonaj(() => zlozWniosek(cykl.id, pole(dane, "data"), pole(dane, "propozycja"), pole(dane, "uzasadnienie")),
                "Wniosek zapisano do rozpatrzenia.");
            }}>
              <label>Data wniosku <input name="data" type="date" defaultValue={dzis()} required /></label>
              <label>Proponowany termin <input name="propozycja" type="date" required /></label>
              <label>Uzasadnienie (opcjonalne) <textarea name="uzasadnienie" maxLength={1000} /></label>
              <button disabled={wToku}>Złóż wniosek</button>
            </form>
          </details>
        </>
      )}
      {cykl.wnioski_zmiany_terminu.map((w) => (
        <div className="sekcja" key={w.id}>
          <p><strong>Wniosek z {dataPl(w.data)}</strong> · {STATUS_WNIOSKU[w.status]} · proponowany termin {dataPl(w.proponowany_termin)}</p>
          {w.uzasadnienie && <p>{w.uzasadnienie}</p>}
          {w.status === "OCZEKUJE" && !cykl.zatwierdzona && (
            <div className="wiersz-lewy">
              <button disabled={wToku} onClick={() => wykonaj(() => rozpatrzWniosek(cykl.id, w.id, dzis(), true), "Zmieniono termin i dołączono zawiadomienie.")}>Uwzględnij wniosek</button>
              <button disabled={wToku} onClick={() => wykonaj(() => rozpatrzWniosek(cykl.id, w.id, dzis(), false), "Wniosek odrzucono.")}>Odrzuć wniosek</button>
            </div>
          )}
        </div>
      ))}
      {cykl.zawiadomienia.length > 0 && (
        <div className="sekcja">
          <h3>Załączniki do oceny</h3>
          <ul>{cykl.zawiadomienia.map((z) => (
            <li key={z.wniosek_id}>
              <button type="button" className="link" onClick={() => void otworzPlik("/api/cykle/" + cykl.id + "/zawiadomienia/" + z.wniosek_id + "/pdf")}>
                Zawiadomienie o zmianie terminu (PDF)
              </button>{" · "}{dataPl(z.poprzedni_termin)} → {dataPl(z.nowy_termin)}
            </li>
          ))}</ul>
        </div>
      )}
      {blad && <p role="alert" className="blad">{blad}</p>}
      {komunikat && <p role="status" className="ok-tekst">{komunikat}</p>}
    </div>
  );
}
