import { useEffect, useState } from "react";
import KalendarzOcen, { SladTerminu } from "./KalendarzOcen";
import Kryteria from "./Kryteria";
import PierwszaOcena from "./PierwszaOcena";
import { pobierzPracownikow, pobierzProces, pobierzProfil, type ListaPracownikow, type Profil, type WyborProcesu } from "./api";

const RODZAJ: Record<string, string> = {
  umowa_nieokreslony: "umowa na czas nieokreślony",
  umowa_okreslony: "umowa na czas określony",
  mianowanie: "mianowanie",
};
const data = (iso: string) => iso.split("-").reverse().join(".");

function Demo({ tekst }: { tekst: string }) {
  return <span className="demo">{tekst}</span>;
}

function Sekcja({ tytul, children }: { tytul: string; children: React.ReactNode }) {
  return (
    <div className="sekcja">
      <h3>{tytul}</h3>
      {children}
    </div>
  );
}

const NAZWA_PROCESU: Record<WyborProcesu["proces"], string> = {
  OKRESOWA: "Ocena okresowa",
  PIERWSZA: "Pierwsza ocena w służbie cywilnej (osobny proces)",
  NIE_MOZNA_USTALIC: "Nie można ustalić",
};

function RodzajOceny({ id, odswiezenie }: { id: string; odswiezenie: number }) {
  const [w, setW] = useState<WyborProcesu | null>(null);
  const [pierwsza, setPierwsza] = useState(false);
  useEffect(() => {
    setPierwsza(false);
    let aktywne = true;
    pobierzProces(id).then((wynik) => { if (aktywne) setW(wynik); }).catch(() => { if (aktywne) setW(null); });
    return () => { aktywne = false; };
  }, [id, odswiezenie]);
  if (!w) return null;
  return (
    <Sekcja tytul="Rodzaj oceny i arkusz">
      <p>
        <strong className={w.proces === "NIE_MOZNA_USTALIC" ? "blad" : ""}>{NAZWA_PROCESU[w.proces]}</strong>
        {w.arkusz && <> · <strong>arkusz {w.arkusz}</strong></>}
        <span className="opis"> · reguły {w.wersja_regul}</span>
      </p>
      {w.termin_oceny && (
        <div>
          <p>Termin oceny okresowej <strong><time dateTime={w.termin_oceny.termin}>{data(w.termin_oceny.termin)}</time></strong></p>
          <SladTerminu wynik={w.termin_oceny} />
        </div>
      )}
      <p className="opis">{w.komunikat}</p>
      {w.braki.length > 0 && (
        <div className="braki">
          Brakujące dane:
          <ul>{w.braki.map((b) => <li key={b}>{b}</li>)}</ul>
        </div>
      )}
      {w.przeslanki.map((x, i) => (
        <div key={i} className="przeslanka">
          <span className={x.spelniona === true ? "ok-tekst" : x.spelniona === false ? "opis" : "blad"}>
            {x.spelniona === true ? "✓" : x.spelniona === false ? "✗" : "?"}
          </span>{" "}
          {x.opis}
          {x.adnotacje.map((a, j) => (
            <div key={j} className="adnotacja">
              <div className="zrodlo">
                <span className={`kategoria ${a.kategoria === "PRZEPIS" ? "przepis" : ""}`}>{a.kategoria}</span>
                {a.lokalizator}{a.strona && <> · s. {a.strona}</>}
              </div>
              {a["cytat_dosłowny"] && <blockquote>{a["cytat_dosłowny"]}</blockquote>}
              {a.uzasadnienie && <p className="opis">{a.uzasadnienie}</p>}
            </div>
          ))}
        </div>
      ))}
      {w.proces === "PIERWSZA" && (
        pierwsza
          ? <PierwszaOcena id={id} />
          : <button onClick={() => setPierwsza(true)}>Przejdź do ścieżki pierwszej oceny</button>
      )}
    </Sekcja>
  );
}

function ProfilPracownika({ p, onWroc }: { p: Profil; onWroc: () => void }) {
  const [odswiezenieProcesu, ustawOdswiezenieProcesu] = useState(0);
  return (
    <section>
      <div className="wiersz">
        <button className="link" onClick={onWroc}>← lista pracowników</button>
        <Demo tekst={p.znacznik} />
      </div>
      <h2>{p.imie} {p.nazwisko}</h2>
      <p className="opis">{p.stanowisko} · {p.komorka} · {p.jednostka}</p>

      <RodzajOceny id={p.id} odswiezenie={odswiezenieProcesu} />
      <Kryteria id={p.id} onZmianaTerminu={() => ustawOdswiezenieProcesu((n) => n + 1)} />

      <Sekcja tytul="Zatrudnienie">
        {p.zatrudnienie
          ? <p>{RODZAJ[p.zatrudnienie.rodzaj]} od {data(p.zatrudnienie.data_nawiazania)}; na stanowisku od {data(p.zatrudnienie.data_na_stanowisku)}</p>
          : <p className="blad">Brak podstawy zatrudnienia w danych kadrowych.</p>}
        <p>{p.korpus === "urzednik" ? "urzędnik służby cywilnej" : p.korpus === "pracownik" ? "pracownik służby cywilnej" : "korpus: brak danych"}
          {p.mianowanie && <> · mianowanie {data(p.mianowanie.data)}, stopień: {p.mianowanie.stopien}</>}
          {p.pierwsza_praca_w_sluzbie_cywilnej && <> · pierwsza praca w służbie cywilnej</>}</p>
      </Sekcja>

      <Sekcja tytul="Przełożony i zastępstwa">
        <p>Przełożony: {p.przelozony ?? "brak (kierownik urzędu)"} · podległych: {p.liczba_podleglych ?? "brak danych"}</p>
        {p.zastepstwa.length === 0 ? <p className="opis">Brak zastępstw.</p> : p.zastepstwa.map((z, i) => (
          <p key={i}>Zastępuje: {z.zastepca} ({data(z.od)}–{data(z.do)}, {z.powod})</p>
        ))}
      </Sekcja>

      <Sekcja tytul="Opis stanowiska">
        <p>{p.opis_stanowiska.cel}</p>
        <p className="opis">Zadania:</p>
        <ul>{p.opis_stanowiska.zadania.map((z) => <li key={z}>{z}</li>)}</ul>
        <p className="opis">Wymagania:</p>
        <ul>{p.opis_stanowiska.wymagania.map((w) => <li key={w}>{w}</li>)}</ul>
      </Sekcja>

      <Sekcja tytul="Poprzednie oceny i zalecenia">
        {p.poprzednie_oceny.length === 0 ? <p className="opis">Brak poprzednich ocen.</p> : p.poprzednie_oceny.map((o, i) => (
          <div key={i}>
            <p><strong className={o.wynik === "negatywna" ? "blad" : "ok-tekst"}>{o.wynik}</strong> · {o.poziom} · średnia {o.srednia.toFixed(2)} ·
              sporządzona {data(o.data_sporzadzenia)}, zapoznanie {data(o.data_zapoznania)}</p>
            {o.zalecenia.length > 0 && <ul>{o.zalecenia.map((z) => <li key={z}>{z}</li>)}</ul>}
          </div>
        ))}
      </Sekcja>

      <Sekcja tytul="Nieobecności">
        {p.nieobecnosci.length === 0 ? <p className="opis">Brak.</p> : p.nieobecnosci.map((n, i) => (
          <p key={i}>{data(n.od)}–{data(n.do)}: {n.rodzaj}{n.usprawiedliwiona ? "" : " (nieusprawiedliwiona)"}</p>
        ))}
      </Sekcja>

      <Sekcja tytul="Uwagi i nagany">
        {p.uwagi.length === 0 ? <p className="opis">Brak.</p> : p.uwagi.map((u, i) => (
          <p key={i}><strong>{u.rodzaj}</strong> ({data(u.data)}): {u.tresc}</p>
        ))}
      </Sekcja>

      <p className="opis">Stanowisko wyprowadzone z przykładowego arkusza: {p.zrodlo_przykladu}. Osoba i zdarzenia są fikcyjne.</p>
    </section>
  );
}

export default function Pracownicy() {
  const [lista, setLista] = useState<ListaPracownikow | null>(null);
  const [profil, setProfil] = useState<Profil | null>(null);
  const [blad, setBlad] = useState<string | null>(null);

  useEffect(() => {
    pobierzPracownikow().then(setLista).catch(() => setBlad("Brak połączenia z API (port 8001)."));
  }, []);

  if (blad) return <p className="blad">{blad}</p>;
  if (profil) return <ProfilPracownika p={profil} onWroc={() => setProfil(null)} />;
  if (!lista) return null;
  const otworzProfil = (id: string) => pobierzProfil(id).then(setProfil).catch(() => setBlad("Nie udało się otworzyć profilu pracownika."));
  return (
    <>
      <KalendarzOcen pracownicy={lista.pracownicy} otworzProfil={otworzProfil} />
      <section>
        <div className="wiersz">
          <h2>Pracownicy · {lista.jednostka}</h2>
          <Demo tekst={lista.znacznik} />
        </div>
        <table>
          <thead><tr><th>Pracownik</th><th>Stanowisko</th><th>Komórka</th><th></th></tr></thead>
          <tbody>
            {lista.pracownicy.map((p) => (
              <tr key={p.id}>
                <td>{p.imie} {p.nazwisko}</td><td>{p.stanowisko}</td><td>{p.komorka}</td>
                <td><button className="link" onClick={() => otworzProfil(p.id)}>Profil</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}
