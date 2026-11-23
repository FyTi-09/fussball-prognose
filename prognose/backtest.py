"""Rückwärtstest: Wie gut hätte das Modell in vergangenen Spielzeiten getippt?

Für jede Spielwoche wird das Modell nur mit den Spielen davor trainiert und
sagt dann die Spiele dieser Woche voraus – genau wie im echten Betrieb.

Gleichzeitig entscheidet der Test, welche Einflüsse wirklich helfen:
* Einflüsse (Ruhetage, Motivation, Form, Elo, Heimstärke, Auf-/Absteiger) werden
  schrittweise ausgewählt: Runde für Runde kommt der Einfluss dazu, der die
  Prognosen am meisten verbessert – solange die Verbesserung kein Zufall ist.
* Für die Buchmacherquoten wird das beste Mischgewicht gesucht (nur 1./2. BL,
  weil es nur dort kostenlose alte Quoten gibt).
* Für die Form wird die beste Halbwertszeit gesucht: Wie schnell verlieren alte
  Spiele an Gewicht? Kurz = aktuelle Form zählt mehr.
Ergebnis: data/einstellungen.json und data/backtest.json (run.py kopiert es auf die Seite)
"""
from __future__ import annotations

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
    wochen = []   # je Woche: (Stichtag, Spiele)
    for saison in config.BACKTEST_SAISONS:
        spiele = [s for s in bestand.spiele if s.saison == saison and s.gespielt]
        bloecke = _bloecke(spiele)
        if ausgabe:
            print(f"  Saison {saison}/{(saison + 1) % 100:02d}: {len(spiele)} Spiele, {len(bloecke)} Wochen")
        wochen += [(min(s.anstoss for s in b) - timedelta(hours=1), b) for b in bloecke]
    datensaetze = [{"liga": s.liga, "saison": s.saison, "h": s.tore_heim, "g": s.tore_gast,
                    "markt": markt.get(s.id), "var": {}} for _, b in wochen for s in b]

    def rechne(merkmale: tuple) -> str:
        """Rückwärtstest einer Auswahl von Einflüssen; Ergebnis landet in datensaetze."""
        name = "+".join(merkmale) or "ohne"
        if name in datensaetze[0]["var"]:
            return name
        einfl, start, i = basis.mit(list(merkmale)), None, 0
        for stichtag, block in wochen:
            modell, start = trainiere(bestand, einfl, stichtag, start)
            for s in block:
                xh, xg = einfl.vektoren(s) if einfl.aktiv else (None, None)
                lam, mu = modell.erwartete_tore(s.heim_id, s.gast_id, s.liga, xh, xg)
                datensaetze[i]["var"][name] = (lam, mu, modell.rho)
                i += 1
        return name

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

    # 1) Einflüsse schrittweise auswählen (vorwärts): immer den besten dazunehmen
    bewertung_var = {}

    def bewerte(merkmale):
        name = rechne(tuple(m for m in MERKMALE if m in merkmale))
        if name not in bewertung_var:
            bewertung_var[name] = kennzahlen(eintraege(name))
            if ausgabe:
                print(f"    {name}: Log-Loss {bewertung_var[name]['logloss']:.4f}")
        return name

    aktiv: list[str] = []
    aktuell = bewerte(aktiv)
    while True:
        kandidaten = {m: bewerte(aktiv + [m]) for m in MERKMALE if m not in aktiv}
        if not kandidaten:
            break
        m_best = min(kandidaten, key=lambda m: bewertung_var[kandidaten[m]]["logloss"])
        gewinn = bewertung_var[aktuell]["logloss"] - bewertung_var[kandidaten[m_best]]["logloss"]
        if gewinn < MINDEST_VERBESSERUNG:
            break
        aktiv.append(m_best)
        aktuell = kandidaten[m_best]
        if ausgabe:
            print(f"  + {m_best} (besser um {gewinn:.4f})")
    beste = aktuell
    aktiv = [m for m in MERKMALE if m in aktiv]
    # Wirkung jedes einzelnen Einflusses allein (für die Anzeige)
    einzeln = {m: round(bewertung_var["ohne"]["logloss"] - bewertung_var[bewerte([m])]["logloss"], 4)
               for m in MERKMALE}
    varianten = {beste: basis.mit(aktiv)}

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
            "einzeln": einzeln,
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


