"""Daten von OpenLigaDB laden und in ein einfaches, einheitliches Format bringen.

Abgeschlossene Spielzeiten werden einmal geladen und dann aus data/roh gelesen,
die laufende Saison wird bei jedem Lauf frisch geholt.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import requests

from . import config

API = "https://api.openligadb.de"
SITZUNG = requests.Session()
SITZUNG.headers["User-Agent"] = "fussball-prognose (privates Hobbyprojekt)"


@dataclass
class Spiel:
    id: int
    liga: str
    saison: int
    spieltag: int
    anstoss: datetime            # UTC
    heim_id: int
    gast_id: int
    tore_heim: int | None        # None = noch nicht gespielt
    tore_gast: int | None
    beendet: bool
    tore: list = field(default_factory=list)   # Torschützen (für die Seite)

    @property
    def gespielt(self) -> bool:
        return self.beendet and self.tore_heim is not None


def _hole(pfad: str, versuche: int = 3):
    for i in range(versuche):
        try:
            r = SITZUNG.get(f"{API}/{pfad}", timeout=30)
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            if i == versuche - 1:
                raise
            time.sleep(2 * (i + 1))


def _endergebnis(m: dict) -> tuple[int | None, int | None]:
    """Ergebnis nach 90 Minuten (resultTypeID 2), sonst das letzte vorhandene."""
    ergebnisse = sorted(m.get("matchResults") or [], key=lambda r: r.get("resultOrderID", 0))
    end = [r for r in ergebnisse if r.get("resultTypeID") == 2]
    r = end[-1] if end else (ergebnisse[-1] if ergebnisse else None)
    if not r:
        return None, None
    return r["pointsTeam1"], r["pointsTeam2"]


def lade_saison_roh(liga: str, saison: int, frisch: bool = False) -> list[dict]:
    """Rohdaten einer Saison, mit Zwischenspeicher für abgeschlossene Spielzeiten."""
    if (liga, saison) in _SPEICHER and not frisch:
        return _SPEICHER[(liga, saison)]
    config.ROH.mkdir(parents=True, exist_ok=True)
    datei = config.ROH / f"{liga}_{saison}.json"
    abgeschlossen = saison < config.AKTUELLE_SAISON
    if datei.exists() and abgeschlossen and not frisch:
        daten = json.loads(datei.read_text(encoding="utf-8"))
    else:
        daten = _hole(f"getmatchdata/{liga}/{saison}")
        if abgeschlossen:
            datei.write_text(json.dumps(daten, ensure_ascii=False), encoding="utf-8")
    _SPEICHER[(liga, saison)] = daten
    return daten


_SPEICHER: dict[tuple[str, int], list] = {}


def wandle(m: dict, liga: str, saison: int) -> Spiel:
    th, tg = _endergebnis(m)
    anstoss = datetime.fromisoformat(m["matchDateTimeUTC"].replace("Z", "+00:00"))
    if anstoss.tzinfo is None:
        anstoss = anstoss.replace(tzinfo=timezone.utc)
    return Spiel(
        id=m["matchID"],
        liga=liga,
        saison=saison,
        spieltag=m["group"]["groupOrderID"],
        anstoss=anstoss,
        heim_id=m["team1"]["teamId"],
        gast_id=m["team2"]["teamId"],
        tore_heim=th,
        tore_gast=tg,
        beendet=bool(m["matchIsFinished"]),
        tore=[
            {
                "minute": g.get("matchMinute"),
                "name": g.get("goalGetterName"),
                "heim": g.get("scoreTeam1"),
                "gast": g.get("scoreTeam2"),
                "elfmeter": g.get("isPenalty"),
                "eigentor": g.get("isOwnGoal"),
            }
            for g in (m.get("goals") or [])
        ],
    )


@dataclass
class Datenbestand:
    spiele: list[Spiel]                    # alle Liga-Spiele aller Saisons
    pokal: list[Spiel]                     # Pokalspiele (nur für Ruhetage)
    teams: dict[int, dict]                 # teamId -> {name, kurz, logo}


def lade_alles(saisons: list[int] | None = None) -> Datenbestand:
    if saisons is None:
        start = min(config.BACKTEST_SAISONS) - config.TRAININGS_VORJAHRE
        saisons = list(range(start, config.AKTUELLE_SAISON + 1))
    spiele, pokal, teams = [], [], {}

    def merke_team(t: dict):
        tid = t["teamId"]
        if tid not in teams or not teams[tid].get("logo"):
            teams[tid] = {
                "name": t.get("teamName") or "",
                "kurz": t.get("shortName") or t.get("teamName") or "",
                "logo": t.get("teamIconUrl") or "",
            }

    for saison in saisons:
        for liga in config.LIGEN:
            for m in lade_saison_roh(liga, saison):
                merke_team(m["team1"]); merke_team(m["team2"])
                spiele.append(wandle(m, liga, saison))
        try:
            for m in lade_saison_roh(config.POKAL, saison):
                pokal.append(wandle(m, config.POKAL, saison))
        except requests.RequestException:
            pass  # Pokal ist nur ein Zusatz

    # Teams der laufenden Saison bekommen die aktuellsten Namen/Logos
    for liga in config.LIGEN:
        for m in lade_saison_roh(liga, config.AKTUELLE_SAISON):
            for t in (m["team1"], m["team2"]):
                teams[t["teamId"]] = {
                    "name": t.get("teamName") or "",
                    "kurz": t.get("shortName") or t.get("teamName") or "",
                    "logo": t.get("teamIconUrl") or "",
                }
    spiele.sort(key=lambda s: s.anstoss)
    pokal.sort(key=lambda s: s.anstoss)
    return Datenbestand(spiele, pokal, teams)


def jetzt() -> datetime:
    return datetime.now(timezone.utc)
