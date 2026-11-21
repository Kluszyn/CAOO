from caoo.ustawienia import PORT_DOMYSLNY


def test_zdrowie(klient):
    r = klient.get("/api/zdrowie").json()
    assert r["wersja"] == "v1" and r["port"] == PORT_DOMYSLNY == 8001
    assert r["wersje_regul"] == ["przepisy-2016-v1"]


def test_panel_zrodel_pokazuje_regule_kategorie_i_cytat(klient):
    (w,) = klient.get("/api/reguly").json()
    assert w["obowiazuje_od"] == "2016-04-23"
    skala = next(r for r in w["reguly"] if r["id"] == "skala.oceny-czastkowe")
    (a,) = skala["adnotacje"]
    assert a["adnotacja"]["kategoria"] == "PRZEPIS"
    assert "1 punkt" in a["adnotacja"]["cytat_dosłowny"]
    assert a["dokument"].startswith("Rozporządzenie") and a["cytat_w_korpusie"] is True
    assert all(
        x["cytat_w_korpusie"] in (True, None) for r in w["reguly"] for x in r["adnotacje"]
    )


def test_zrodla_oznaczaja_brak_tekstu(klient):
    d = {x["doc_id"]: x for x in klient.get("/api/zrodla").json()["dokumenty"]}
    assert d["wzor-b"]["status"] == "BRAK_TEKSTU"
    assert d["rozp-oceny-okresowe"]["status"] == "OK"


def test_oblicz_wynik_par_11(klient):
    r = klient.post("/api/skala/oblicz", json={"oceny": [9, 9, 9, 1]}).json()
    assert r["wynik"] == "NEGATYWNA" and r["regula_rozstrzygajaca"] == "wynik.negatywna-1pkt"
    assert r["poziom"] == "powyżej oczekiwań" and r["wersja_regul"] == "przepisy-2016-v1"
    assert klient.post("/api/skala/oblicz", json={"oceny": [4]}).status_code == 422
