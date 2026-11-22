"""Verletzungen und Sperren über API-Football (optional, kostenloser Schlüssel).

Die Daten werden auf der Seite angezeigt und der KI mitgegeben. Ins
Rechenmodell fließen sie (noch) nicht, weil es dafür keine Vergangenheitsdaten
für den Rückwärtstest gibt – siehe Entscheidung "nur was nachweislich hilft".
"""
from __future__ import annotations

from datetime import datetime

import requests

from . import config
from .daten import Datenbestand
from .namen import aehnlichkeit

API = "https://v3.football.api-sports.io"


def lade(bestand: Datenbestand) -> dict[int, list[dict]]:
    """Team-ID (OpenLigaDB) -> Liste aktueller Ausfälle."""
    if not config.API_FOOTBALL_KEY:
        return {}
    erg: dict[int, list[dict]] = {}
    kopf = {"x-apisports-key": config.API_FOOTBALL_KEY}
    teams_saison = {t for s in bestand.spiele if s.saison == config.AKTUELLE_SAISON
                    for t in (s.heim_id, s.gast_id)}
    for liga, info in config.LIGEN.items():
        try:
            r = requests.get(f"{API}/injuries", headers=kopf, timeout=30,
                             params={"league": info["api_football"], "season": config.AKTUELLE_SAISON})
            r.raise_for_status()
            daten = r.json()
        except (requests.RequestException, ValueError) as e:
            print(f"  API-Football ({liga}) nicht erreichbar: {e}")
            continue
        if daten.get("errors"):
            print(f"  API-Football ({liga}): {daten['errors']}")
            continue
        jetzt = datetime.now().astimezone()
        zuordnung: dict[str, int | None] = {}
        for eintrag in daten.get("response", []):
            team_name = (eintrag.get("team") or {}).get("name", "")
            if team_name not in zuordnung:
                beste, wert = None, 0.7
                for tid in teams_saison:
                    t = bestand.teams[tid]
                    w = max(aehnlichkeit(team_name, t["name"]), aehnlichkeit(team_name, t["kurz"]))
                    if w > wert:
                        beste, wert = tid, w
                zuordnung[team_name] = beste
            tid = zuordnung[team_name]
            if tid is None:
                continue
            # nur Einträge zu Spielen ab heute (die API liefert die ganze Saison)
            try:
                datum = datetime.fromisoformat((eintrag.get("fixture") or {}).get("date", ""))
                if datum < jetzt:
                    continue
            except ValueError:
                pass
            spieler = eintrag.get("player") or {}
            neu = {"name": spieler.get("name"), "art": spieler.get("type"),
                   "grund": spieler.get("reason")}
            if neu not in erg.setdefault(tid, []):
                erg[tid].append(neu)
    return erg
