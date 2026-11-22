"""Ein kompletter Lauf: Daten holen, rechnen, Seite aktualisieren.

Aufruf:
    python run.py              normaler Lauf (Rückwärtstest nur, wenn älter als 7 Tage)
    python run.py --backtest   Rückwärtstest erzwingen
    python run.py --ohne-ki    KI-Teil auslassen
    python run.py --nur-ki     nur den KI-Teil ausführen (z. B. lokal mit Ollama)
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from datetime import datetime, timedelta, timezone

from prognose import archiv, backtest, config, daten, elo, ki, quoten, simulation, verletzungen
from prognose.einfluesse import MERKMALE, Einfluesse
from prognose.modell import ergebnis_matrix
from prognose.vorhersage import passe_matrix_an, prognose, trainiere


def lade_einstellungen() -> dict | None:
    datei = config.DATEN / "einstellungen.json"
    if datei.exists():
        return json.loads(datei.read_text(encoding="utf-8"))
    return None


def schreibe(name: str, inhalt) -> None:
    config.SEITE_DATEN.mkdir(parents=True, exist_ok=True)
    (config.SEITE_DATEN / name).write_text(
        json.dumps(inhalt, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def direkter_vergleich(bestand, s, anzahl: int = 6) -> list[dict]:
    """Die letzten Duelle der beiden Teams (Liga und Pokal), neuestes zuerst."""
    paar = {s.heim_id, s.gast_id}
    duelle = [x for x in bestand.spiele + bestand.pokal
              if x.gespielt and {x.heim_id, x.gast_id} == paar and x.anstoss < s.anstoss]
    duelle.sort(key=lambda x: x.anstoss, reverse=True)
    return [{"datum": x.anstoss.date().isoformat(), "heim": x.heim_id, "gast": x.gast_id,
             "erg": [x.tore_heim, x.tore_gast], "pokal": x.liga == config.POKAL}
            for x in duelle[:anzahl]]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backtest", action="store_true")
    ap.add_argument("--ohne-ki", action="store_true")
    ap.add_argument("--nur-ki", action="store_true")
    args = ap.parse_args()
    t0 = time.time()

    print("1) Daten laden …")
    bestand = daten.lade_alles()
    nun = daten.jetzt()
    print(f"   {len(bestand.spiele)} Liga-Spiele, {len(bestand.teams)} Teams")

    print("2) Quoten laden …")
    markt = quoten.historisch(bestand)
    aktuell = quoten.aktuell_odds_api(bestand)
    markt.update(aktuell)   # The Odds API hat Vorrang (aktueller, auch 3. Liga)
    print(f"   {len(markt)} Spiele mit Quoten ({len(aktuell)} aktuell über The Odds API)")

    einst = lade_einstellungen()
    veraltet = (einst is None or datetime.fromisoformat(einst["stand"])
                < nun - timedelta(days=7))
    if (args.backtest or veraltet) and not args.nur_ki:
        print("3) Rückwärtstest (entscheidet, welche Einflüsse helfen) …")
        backtest.lauf(bestand, markt)
        einst = lade_einstellungen()
    print(f"   Aktive Einflüsse: {einst['merkmale'] or 'keine'}, Quoten-Gewicht: {einst['quoten_gewicht']}")

    print("4) Modell anpassen …")
    einfl = Einfluesse(bestand.spiele, bestand.pokal, MERKMALE).mit(einst["merkmale"])
    modell, _ = trainiere(bestand, einfl, nun)

    saison_spiele = [s for s in bestand.spiele if s.saison == config.AKTUELLE_SAISON]
    prognosen = {}
    matrizen = {}
    for s in saison_spiele:
        if s.gespielt:
            continue
        p = prognose(modell, s, einfl, markt.get(s.id), einst["quoten_gewicht"])
        prognosen[s.id] = p
        # für die Simulation: Matrix passend zu den (ggf. gemischten) Prozenten
        m = ergebnis_matrix(p["lam"], p["mu"], p["rho"])
        matrizen[s.id] = passe_matrix_an(m, tuple(p["p"]))

    print("5) KI-Teil …")
    if args.ohne_ki:
        ki_daten = ki.lade_gespeichert()
    else:
        ki_daten = ki.lauf(bestand, prognosen)
    if args.nur_ki:
        print(f"Fertig (nur KI) in {time.time() - t0:.0f} s")
        return 0

    print("6) Verletzungen (API-Football, optional) …")
    ausfaelle = verletzungen.lade(bestand)

    print("7) Archiv und Trefferquote …")
    arch = archiv.aktualisiere(bestand, prognosen, ki_daten)
    schreibe("trefferquote.json", archiv.auswertung(bestand, arch))

    print("8) Elo und Simulation …")
    elo_werte, elo_verlauf = elo.berechne(bestand.spiele)
    index = {"stand": nun.isoformat(timespec="minutes"), "saison": config.AKTUELLE_SAISON,
             "ligen": [], "einstellungen": einst,
             "ki_aktiv": bool(config.GEMINI_API_KEY) or ki.ollama_erreichbar(),
             "quellen": {"odds_api": bool(config.ODDS_API_KEY),
                         "api_football": bool(config.API_FOOTBALL_KEY)}}
    for liga, info in config.LIGEN.items():
        spiele = [s for s in saison_spiele if s.liga == liga]
        if not spiele:
            continue
        sim = simulation.simuliere(liga, spiele, matrizen)
        team_ids = sorted({t for s in spiele for t in (s.heim_id, s.gast_id)})
        tab = simulation.tabelle(spiele, team_ids)
        offen = [s for s in spiele if not s.gespielt]
        akt_spieltag = min((s.spieltag for s in offen), default=max(s.spieltag for s in spiele))
        teams = {}
        for t in team_ids:
            verlauf = [v for v in elo_verlauf.get(t, []) if v["saison"] == config.AKTUELLE_SAISON]
            a, d = modell.staerke(t, liga)
            teams[t] = {
                **bestand.teams[t],
                "elo": round(elo_werte.get(t, 0)),
                "elo_verlauf": [{"st": v["spieltag"], "elo": v["elo"], "e": v["ergebnis"],
                                 "tore": v["tore"], "geg": v["gegner"], "heim": v["heim"]}
                                for v in verlauf],
                "angriff": round(float(a), 3), "abwehr": round(float(d), 3),
                "ausfaelle": ausfaelle.get(t, []),
            }
        spiele_aus = []
        for s in spiele:
            eintrag = {"id": s.id, "st": s.spieltag, "anstoss": s.anstoss.isoformat(),
                       "heim": s.heim_id, "gast": s.gast_id, "beendet": s.beendet,
                       "erg": [s.tore_heim, s.tore_gast] if s.tore_heim is not None else None,
                       "tore": s.tore}
            if s.id in prognosen:
                eintrag["prognose"] = prognosen[s.id]
                eintrag["info"] = einfl.beschreibung(s)
                eintrag["h2h"] = direkter_vergleich(bestand, s)
            elif str(s.id) in arch:
                eintrag["prognose_vorher"] = arch[str(s.id)]["varianten"]
            if str(s.id) in ki_daten:
                eintrag["ki"] = ki_daten[str(s.id)]
            spiele_aus.append(eintrag)
        schreibe(f"{liga}.json", {
            "liga": liga, "name": info["name"], "kurz": info["kurz"],
            "saison": config.AKTUELLE_SAISON, "stand": index["stand"],
            "zonen": info["zonen"], "aktueller_spieltag": akt_spieltag,
            "spieltage": max(s.spieltag for s in spiele),
            "teams": teams, "spiele": spiele_aus, "tabelle": tab, "simulation": sim,
            "liga_staerke": {"angriff": round(float(modell.liga_angriff[list(config.LIGEN).index(liga)]), 3),
                             "abwehr": round(float(modell.liga_abwehr[list(config.LIGEN).index(liga)]), 3)},
        })
        index["ligen"].append({"id": liga, "name": info["name"], "kurz": info["kurz"]})
        print(f"   {info['name']}: Spieltag {akt_spieltag}, {len(offen)} offene Spiele")
    schreibe("index.json", index)
    if (config.DATEN / "backtest.json").exists():
        shutil.copy(config.DATEN / "backtest.json", config.SEITE_DATEN / "backtest.json")
    print(f"Fertig in {time.time() - t0:.0f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
