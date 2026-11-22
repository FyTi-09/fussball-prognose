"""Eingefrorene Prognosen: Was hat das Tool VOR dem Anpfiff gesagt?

Bei jedem Lauf wird für jedes noch nicht angepfiffene Spiel die aktuelle
Prognose gespeichert (spätere Läufe überschreiben frühere). Sobald das Spiel
angepfiffen ist, bleibt der letzte Stand für immer stehen. So entsteht eine
ehrliche Trefferquote ohne Rückschau-Effekte.

Varianten, die gegeneinander antreten:
* endprognose – was die Seite anzeigt (Modell, ggf. mit Quoten gemischt)
* modell      – das reine Rechenmodell
* markt       – nur Buchmacherquoten (wenn vorhanden)
* ki          – der eigene Tipp der KI
* kicktipp    – Tipp mit den meisten erwarteten Kicktipp-Punkten
* tipprunde   – der Tipp aus dem Tab Tipprunde (Kicktipp-Regel, Buchmacher voll
                genutzt) – also das, was man tatsächlich abgibt
"""
from __future__ import annotations

import json

from . import config
from .bewertung import grundregel, kennzahlen
from .daten import Datenbestand, jetzt

DATEI = config.DATEN / "archiv" / "prognosen.json"
VARIANTEN = ["endprognose", "kicktipp", "tipprunde", "modell", "markt", "ki"]


def lade() -> dict[str, dict]:
    if DATEI.exists():
        return json.loads(DATEI.read_text(encoding="utf-8"))
    return {}


def speichere(archiv: dict):
    DATEI.parent.mkdir(parents=True, exist_ok=True)
    DATEI.write_text(json.dumps(archiv, ensure_ascii=False, indent=0), encoding="utf-8")


def aktualisiere(bestand: Datenbestand, prognosen: dict[int, dict], ki: dict[str, dict]) -> dict:
    archiv = lade()
    nun = jetzt()
    for s in bestand.spiele:
        if s.saison != config.AKTUELLE_SAISON or s.id not in prognosen or s.anstoss <= nun:
            continue
        p = prognosen[s.id]
        alt = archiv.get(str(s.id), {})
        varianten = {
            "endprognose": {"p": p["p"], "tipp": p["tipp"]},
            "modell": {"p": p["p_modell"], "tipp": p["tipp_modell"]},
        }
        if p.get("tipp_kicktipp"):
            varianten["kicktipp"] = {"p": p["p"], "tipp": p["tipp_kicktipp"]}
        if p.get("tipp_tipprunde"):
            varianten["tipprunde"] = {"p": p.get("p_markt") or p["p"], "tipp": p["tipp_tipprunde"]}
        if p.get("p_markt"):
            varianten["markt"] = {"p": p["p_markt"], "tipp": p.get("tipp_markt")}
        k = ki.get(str(s.id))
        if k and k.get("tipp"):
            varianten["ki"] = {"p": k.get("p"), "tipp": k["tipp"]}
        elif alt.get("varianten", {}).get("ki"):
            varianten["ki"] = alt["varianten"]["ki"]
        archiv[str(s.id)] = {
            "liga": s.liga, "saison": s.saison, "spieltag": s.spieltag,
            "anstoss": s.anstoss.isoformat(), "heim": s.heim_id, "gast": s.gast_id,
            "gespeichert": nun.isoformat(timespec="minutes"), "varianten": varianten,
        }
    speichere(archiv)
    return archiv


def auswertung(bestand: Datenbestand, archiv: dict) -> dict:
    """Trefferquote der eingefrorenen Prognosen je Variante und Liga."""
    ergebnisse = {str(s.id): s for s in bestand.spiele if s.gespielt}
    zeilen = []
    for sid, e in archiv.items():
        s = ergebnisse.get(sid)
        if not s:
            continue
        zeilen.append((e, s))
    erg = {"n": len(zeilen), "je_liga": {}, "gesamt": {}}

    def sammle(filter_fn):
        aus = {}
        basis = []
        for v in VARIANTEN:
            eintraege = [{"p": e["varianten"][v].get("p"), "tipp": e["varianten"][v]["tipp"],
                          "h": s.tore_heim, "g": s.tore_gast}
                         for e, s in zeilen if filter_fn(e) and v in e["varianten"]
                         and e["varianten"][v].get("tipp")]
            if eintraege:
                aus[v] = kennzahlen(eintraege)
            if v == "endprognose":
                basis = eintraege
        if basis:
            aus["grundregel"] = grundregel(basis)
        return aus

    erg["gesamt"] = sammle(lambda e: True)
    for liga in config.LIGEN:
        erg["je_liga"][liga] = sammle(lambda e, l=liga: e["liga"] == l)
    # die letzten Spiele zum Durchsehen
    zeilen.sort(key=lambda x: x[1].anstoss, reverse=True)
    erg["letzte"] = [{
        "id": s.id, "liga": s.liga, "spieltag": s.spieltag, "heim": s.heim_id, "gast": s.gast_id,
        "ergebnis": [s.tore_heim, s.tore_gast],
        "tipps": {v: e["varianten"][v]["tipp"] for v in VARIANTEN
                  if v in e["varianten"] and e["varianten"][v].get("tipp")},
    } for e, s in zeilen[:120]]
    return erg
