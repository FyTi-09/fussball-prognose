"""Rückwärtstest: Wie gut hätte das Modell in vergangenen Spielzeiten getippt?

Für jede Spielwoche wird das Modell nur mit den Spielen davor trainiert und
sagt dann die Spiele dieser Woche voraus – genau wie im echten Betrieb.

Gleichzeitig entscheidet der Test, welche Einflüsse wirklich helfen:
* Jede Kombination der Spielplan-Einflüsse (Ruhetage, Motivation) wird getestet.
  Aktiv bleibt die Kombination mit der besten Log-Loss-Bewertung.
* Für die Buchmacherquoten wird das beste Mischgewicht gesucht (nur 1./2. BL,
  weil es nur dort kostenlose alte Quoten gibt).
* Für die Form wird die beste Halbwertszeit gesucht: Wie schnell verlieren alte
  Spiele an Gewicht? Kurz = aktuelle Form zählt mehr.
Ergebnis: data/einstellungen.json und data/backtest.json (run.py kopiert es auf die Seite)
"""
from __future__ import annotations

import itertools
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone


from . import config
from .bewertung import grundregel, kennzahlen
from .daten import Datenbestand
from .einfluesse import MERKMALE, Einfluesse
from .modell import ergebnis_matrix, tendenz
from .quoten import mische
from .vorhersage import passe_matrix_an, trainiere, waehle_kicktipp, waehle_tipp

GEWICHTE = [0.0, 0.2, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0]
MINDEST_VERBESSERUNG = 0.001   # Log-Loss; kleinere Unterschiede gelten als Rauschen
HALBWERTSZEITEN = [90, 120, 180, 240, 365]   # Tage; Standard steht in config


def _teste_halbwertszeit(bestand, einfl, hwz: int) -> dict:
    """Rückwärtstest nur des Modells (ohne Quoten) mit einer bestimmten Halbwertszeit."""
    alt = config.HALBWERTSZEIT_TAGE
    config.HALBWERTSZEIT_TAGE = hwz
    try:
        start, eintraege = None, []
        for saison in config.BACKTEST_SAISONS:
            spiele = [s for s in bestand.spiele if s.saison == saison and s.gespielt]
            for block in _bloecke(spiele):
                stichtag = min(s.anstoss for s in block) - timedelta(hours=1)
                modell, start = trainiere(bestand, einfl, stichtag, start)
                for s in block:
                    xh, xg = einfl.vektoren(s) if einfl.aktiv else (None, None)
                    lam, mu = modell.erwartete_tore(s.heim_id, s.gast_id, s.liga, xh, xg)
                    m = ergebnis_matrix(lam, mu, modell.rho)
                    eintraege.append({"p": list(tendenz(m)), "tipp": list(waehle_tipp(m)),
                                      "h": s.tore_heim, "g": s.tore_gast})
        return kennzahlen(eintraege)
    finally:
        config.HALBWERTSZEIT_TAGE = alt


def _bloecke(spiele):
    """Gruppiert Spiele in Wochen Dienstag–Montag (ein Bundesliga-Spieltag)."""
    gruppen = defaultdict(list)
    for s in spiele:
        tag = (s.anstoss - timedelta(days=1)).date()
        gruppen[tag.isocalendar()[:2]].append(s)
    return [gruppen[k] for k in sorted(gruppen)]


def lauf(bestand: Datenbestand, markt: dict, ausgabe: bool = True) -> dict:
    basis = Einfluesse(bestand.spiele, bestand.pokal, MERKMALE)
    teilmengen = [list(c) for r in range(len(MERKMALE) + 1)
                  for c in itertools.combinations(MERKMALE, r)]
    varianten = {"+".join(t) or "ohne": basis.mit(t) for t in teilmengen}
    starts = {k: None for k in varianten}
    datensaetze = []  # je Spiel: Ergebnis, Liga, Saison, Lambdas je Variante, Markt
    for saison in config.BACKTEST_SAISONS:
        spiele = [s for s in bestand.spiele if s.saison == saison and s.gespielt]
        bloecke = _bloecke(spiele)
        if ausgabe:
            print(f"  Saison {saison}/{(saison + 1) % 100:02d}: {len(spiele)} Spiele, {len(bloecke)} Wochen")
        for block in bloecke:
            stichtag = min(s.anstoss for s in block) - timedelta(hours=1)
            modelle = {}
            for name, einfl in varianten.items():
                modelle[name], starts[name] = trainiere(bestand, einfl, stichtag, starts[name])
            for s in block:
                d = {"liga": s.liga, "saison": s.saison, "h": s.tore_heim, "g": s.tore_gast,
                     "markt": markt.get(s.id), "var": {}}
                for name, einfl in varianten.items():
                    xh, xg = einfl.vektoren(s) if einfl.aktiv else (None, None)
                    lam, mu = modelle[name].erwartete_tore(s.heim_id, s.gast_id, s.liga, xh, xg)
                    d["var"][name] = (lam, mu, modelle[name].rho)
                datensaetze.append(d)

    def eintraege(name, gewicht=0.0, filter_fn=lambda d: True, kicktipp=False):
        aus = []
        for d in datensaetze:
            if not filter_fn(d):
                continue
            lam, mu, rho = d["var"][name]
            m = ergebnis_matrix(lam, mu, rho)
            if gewicht and d["markt"]:
                m = passe_matrix_an(m, mische(tendenz(m), d["markt"], gewicht))
            tipp = waehle_kicktipp(m)[0] if kicktipp else waehle_tipp(m)
            aus.append({"p": list(tendenz(m)), "tipp": list(tipp),
                        "h": d["h"], "g": d["g"]})
        return aus

    # 1) beste Kombination der Spielplan-Einflüsse
    bewertung_var = {name: kennzahlen(eintraege(name)) for name in varianten}
    beste = min(bewertung_var, key=lambda k: bewertung_var[k]["logloss"])
    # Nur übernehmen, wenn die Verbesserung mehr als Zufallsrauschen ist
    if bewertung_var["ohne"]["logloss"] - bewertung_var[beste]["logloss"] < MINDEST_VERBESSERUNG:
        beste = "ohne"
    aktiv = [] if beste == "ohne" else beste.split("+")

    # 2) bestes Quoten-Gewicht (nur Spiele, für die es Quoten gibt)
    mit_markt = lambda d: d["markt"] is not None  # noqa: E731
    gewicht_bewertung = {
        str(w): kennzahlen(eintraege(beste, w, mit_markt)) for w in GEWICHTE
    }
    if sum(1 for d in datensaetze if d["markt"]) >= 300:
        erlaubt = [w for w in GEWICHTE if w <= config.MAX_QUOTEN_GEWICHT]
        bestes_gewicht = min(erlaubt, key=lambda w: gewicht_bewertung[str(w)]["logloss"])
        if gewicht_bewertung["0.0"]["logloss"] - gewicht_bewertung[str(bestes_gewicht)]["logloss"] \
                < MINDEST_VERBESSERUNG:
            bestes_gewicht = 0.0
    else:
        bestes_gewicht = 0.0

    # 2b) Form: beste Halbwertszeit (nur übernehmen, wenn klar besser als der Standard)
    if ausgabe:
        print("  Form (Halbwertszeit) …")
    hwz_bewertung = {}
    for hwz in HALBWERTSZEITEN:
        if hwz == config.HALBWERTSZEIT_TAGE:
            hwz_bewertung[hwz] = bewertung_var[beste]
        else:
            hwz_bewertung[hwz] = _teste_halbwertszeit(bestand, varianten[beste], hwz)
    beste_hwz = min(hwz_bewertung, key=lambda h: hwz_bewertung[h]["logloss"])
    standard = hwz_bewertung.get(config.HALBWERTSZEIT_TAGE)
    if standard and standard["logloss"] - hwz_bewertung[beste_hwz]["logloss"] < MINDEST_VERBESSERUNG:
        beste_hwz = config.HALBWERTSZEIT_TAGE

    # 3) Endauswertung je Liga mit der gewählten Einstellung
    je_liga = {}
    for liga in config.LIGEN:
        f = lambda d, l=liga: d["liga"] == l  # noqa: E731
        final = eintraege(beste, bestes_gewicht, f)
        je_liga[liga] = {
            "modell": kennzahlen(final),
            "kicktipp": kennzahlen(eintraege(beste, bestes_gewicht, f, kicktipp=True)),
            # Tab Tipprunde: Buchmacher voll (wo vorhanden), Kicktipp-optimaler Tipp
            "tipprunde": kennzahlen(eintraege(beste, 1.0, f, kicktipp=True)),
            "nur_modell_ohne_einfluesse": kennzahlen(eintraege("ohne", 0.0, f)),
            "grundregel": grundregel(final),
        }
        if config.LIGEN[liga].get("football_data"):
            je_liga[liga]["nur_markt"] = kennzahlen(
                eintraege(beste, 1.0, lambda d, l=liga: d["liga"] == l and d["markt"] is not None))
    gesamt = eintraege(beste, bestes_gewicht)
    ergebnis = {
        "stand": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "saisons": config.BACKTEST_SAISONS,
        "gesamt": {"modell": kennzahlen(gesamt), "grundregel": grundregel(gesamt)},
        "je_liga": je_liga,
        "einfluesse": {
            "getestet": {k: v.get("logloss") for k, v in bewertung_var.items()},
            "aktiv": aktiv,
            "quoten_gewichte": {k: v.get("logloss") for k, v in gewicht_bewertung.items()},
            "quoten_gewicht": bestes_gewicht,
            "spiele_mit_quoten": sum(1 for d in datensaetze if d["markt"]),
            "halbwertszeiten": {str(h): v.get("logloss") for h, v in hwz_bewertung.items()},
            "halbwertszeit": beste_hwz,
        },
    }
    einstellungen = {"merkmale": aktiv, "quoten_gewicht": bestes_gewicht, "halbwertszeit": beste_hwz,
                     "stand": ergebnis["stand"]}
    config.DATEN.mkdir(parents=True, exist_ok=True)
    (config.DATEN / "einstellungen.json").write_text(
        json.dumps(einstellungen, indent=2, ensure_ascii=False), encoding="utf-8")
    # in data/ (wird committet), run.py kopiert es bei jedem Lauf auf die Seite
    (config.DATEN / "backtest.json").write_text(
        json.dumps(ergebnis, ensure_ascii=False), encoding="utf-8")
    return ergebnis


