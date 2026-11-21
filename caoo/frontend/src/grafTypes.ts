import type { SourceRef } from "./api";

interface Szczegoly {
  status: "potwierdzone" | "dopasowane" | "propozycja";
  zrodlo: string;
  uzasadnienie: string;
  adnotacje: SourceRef[];
  data_zrodla: string | null;
  zatwierdzil: string | null;
  data_zatwierdzenia: string | null;
}

export interface WezelGrafu extends Szczegoly {
  id: string;
  typ: string;
  nazwa: string;
  wartosc: number | null;
  nieprzypisany: boolean;
}

export interface GrafOceny {
  cykl_id: number;
  katalog: {
    schemat: { wersja: string; obowiazuje_od: string };
    wersja_katalogu: string;
    wersja_regul: string;
    kompetencje: WezelGrafu[];
    poziomy: WezelGrafu[];
    dzialania: WezelGrafu[];
  };
  wezly: WezelGrafu[];
  sciezki: { wezly: string[] }[];
  propozycje: (Szczegoly & { kompetencja_id: string; podstawy: string[] })[];
}
