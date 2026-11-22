"""Tabellenprognose, Rückwärtstest ohne Blick in die Zukunft, eingefrorene Prognosen."""
from datetime import datetime, timedelta, timezone

import numpy as np

from prognose import archiv, config
from prognose.backtest import _bloecke
from prognose.daten import Datenbestand, Spiel
from prognose.einfluesse import Einfluesse
from prognose.modell import ergebnis_matrix
from prognose.simulation import simuliere, tabelle
from prognose.vorhersage import trainiere

T0 = datetime(2025, 8, 1, 18, tzinfo=timezone.utc)


def spiel(i, h, g, th=None, tg=None, tage=0, liga="bl1", saison=2025):
    return Spiel(id=i, liga=liga, saison=saison, spieltag=1, anstoss=T0 + timedelta(days=tage),
                 heim_id=h, gast_id=g, tore_heim=th, tore_gast=tg, beendet=th is not None)


def test_tabelle_punkte_und_reihenfolge():
    t = tabelle([spiel(1, 1, 2, 2, 0), spiel(2, 3, 1, 1, 1), spiel(3, 2, 3, 0, 3)], [1, 2, 3])
    pkt = {z["id"]: z["pkt"] for z in t}
    assert pkt == {1: 4, 2: 0, 3: 4}
    assert t[0]["id"] == 3   # gleiche Punkte, bessere Tordifferenz (+3 gegen +2)


def test_uneinholbarer_meister_immer_platz_1():
    # Team 1 hat 9 Punkte Vorsprung, nur noch 1 Spiel offen
    spiele = [spiel(1, 1, 2, 3, 0), spiel(2, 1, 3, 3, 0), spiel(3, 1, 2, 3, 0, tage=7),
              spiel(4, 2, 3, None, None, tage=14)]
    m = ergebnis_matrix(1.4, 1.1, 0.0)
    erg = simuliere("bl1", spiele, {4: m}, n=2000)
    assert erg[1]["zonen"]["Meister"] == 1.0


def test_bloecke_trennen_wochen():
    # Fr, Sa, So, Mo derselben Runde gehören zusammen, Di beginnt eine neue Woche
    fr = datetime(2025, 8, 22, 18, tzinfo=timezone.utc)
    spiele = [spiel(i, 1, 2, 1, 0) for i in range(5)]
    for s, d in zip(spiele, [0, 1, 2, 3, 4]):
        s.anstoss = fr + timedelta(days=d)
    bl = _bloecke(spiele)
    assert [len(b) for b in bl] == [4, 1]


def test_training_sieht_keine_zukunft():
    """Ergebnisse nach dem Stichtag dürfen das Modell nicht verändern."""
    spiele = []
    for k in range(60):
        h, g = (1, 2) if k % 2 else (2, 1)
        spiele.append(spiel(k, h, g, 2 if h == 1 else 0, 0 if h == 1 else 2, tage=k * 3))
    stichtag = T0 + timedelta(days=100)
    zukunft = [spiel(1000 + k, 2, 1, 6, 0, tage=101 + k) for k in range(20)]
    leer = Einfluesse([], [], [])
    a, _ = trainiere(Datenbestand(spiele, [], {}), leer, stichtag)
    b, _ = trainiere(Datenbestand(spiele + zukunft, [], {}), leer, stichtag)
    assert a.erwartete_tore(1, 2, "bl1") == b.erwartete_tore(1, 2, "bl1")
    lam, mu = a.erwartete_tore(1, 2, "bl1")
    assert lam > mu   # Team 1 hat alles gewonnen


def test_archiv_friert_nach_anpfiff_ein(tmp_path, monkeypatch):
    monkeypatch.setattr(archiv, "DATEI", tmp_path / "prognosen.json")
    monkeypatch.setattr(config, "AKTUELLE_SAISON", 2025)
    nun = T0
    monkeypatch.setattr(archiv, "jetzt", lambda: nun)
    s = spiel(7, 1, 2, tage=1)
    best = Datenbestand([s], [], {})
    prog = lambda tipp: {7: {"p": [0.5, 0.3, 0.2], "p_modell": [0.5, 0.3, 0.2], "tipp": tipp,  # noqa: E731
                             "tipp_modell": tipp, "tipp_kicktipp": tipp}}
    archiv.aktualisiere(best, prog([2, 1]), {})
    assert archiv.lade()["7"]["varianten"]["endprognose"]["tipp"] == [2, 1]
    nun = T0 + timedelta(days=2)   # nach Anpfiff: neue Prognose darf nichts überschreiben
    archiv.aktualisiere(best, prog([0, 3]), {})
    assert archiv.lade()["7"]["varianten"]["endprognose"]["tipp"] == [2, 1]


def test_archiv_speichert_tipprunde_tipp(tmp_path, monkeypatch):
    monkeypatch.setattr(archiv, "DATEI", tmp_path / "prognosen.json")
    monkeypatch.setattr(config, "AKTUELLE_SAISON", 2025)
    monkeypatch.setattr(archiv, "jetzt", lambda: T0)
    best = Datenbestand([spiel(7, 1, 2, tage=1)], [], {})
    p = {7: {"p": [0.5, 0.3, 0.2], "p_modell": [0.5, 0.3, 0.2], "p_markt": [0.6, 0.25, 0.15],
             "tipp": [2, 1], "tipp_modell": [2, 1], "tipp_kicktipp": [2, 1], "tipp_tipprunde": [2, 0]}}
    archiv.aktualisiere(best, p, {})
    v = archiv.lade()["7"]["varianten"]["tipprunde"]
    assert v == {"p": [0.6, 0.25, 0.15], "tipp": [2, 0]}
