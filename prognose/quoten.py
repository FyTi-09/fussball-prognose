"""Buchmacherquoten als zusätzliche Information.

Quellen:
* football-data.co.uk (kostenlos, ohne Schlüssel): Quoten vergangener Spiele und
  eine Datei mit den kommenden Spielen der 1. und 2. Bundesliga.
* The Odds API (kostenloser Schlüssel, optional): aktuelle Quoten aller drei Ligen.

Aus Quoten werden Wahrscheinlichkeiten: 1/Quote, dann so skaliert, dass die
Summe 1 ergibt (das entfernt die Marge des Buchmachers).
"""
from __future__ import annotations

import csv
import io
import time
from datetime import datetime, timezone

import requests

from . import config
from .daten import Datenbestand, Spiel
from .namen import ordne_spiel_zu

FD = "https://www.football-data.co.uk"
SITZUNG = requests.Session()
SITZUNG.headers["User-Agent"] = "fussball-prognose (privates Hobbyprojekt)"


def _get_text(url: str) -> str | None:
    for i in range(3):
        try:
            r = SITZUNG.get(url, timeout=40)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.content.decode("utf-8-sig", errors="replace")
        except requests.RequestException:
            time.sleep(2 * (i + 1))
    return None


def _wahrscheinlichkeiten(h, d, a) -> tuple[float, float, float] | None:
    try:
        q = [float(h), float(d), float(a)]
    except (TypeError, ValueError):
        return None
    if min(q) <= 1.0:
        return None
    inv = [1 / x for x in q]
    s = sum(inv)
    return inv[0] / s, inv[1] / s, inv[2] / s


def _datum(text: str) -> datetime | None:
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(text.strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _kandidaten(spiele: list[Spiel], liga: str, tag: datetime) -> list[Spiel]:
    return [s for s in spiele if s.liga == liga
            and abs((s.anstoss - tag).total_seconds()) < 2 * 86400]


def _zeilen_zuordnen(zeilen, liga, bestand: Datenbestand, erg: dict):
    nach_liga = [s for s in bestand.spiele if s.liga == liga]
    for z in zeilen:
        tag = _datum(z.get("Date", ""))
        if not tag:
            continue
        p = None
        for pre in ("Avg", "B365", "BW", "PS"):
            p = _wahrscheinlichkeiten(z.get(f"{pre}H"), z.get(f"{pre}D"), z.get(f"{pre}A"))
            if p:
                break
        if not p:
            continue
        s = ordne_spiel_zu(z.get("HomeTeam", ""), z.get("AwayTeam", ""),
                           _kandidaten(nach_liga, liga, tag), bestand.teams)
        if s:
            erg[s.id] = p


def historisch(bestand: Datenbestand) -> dict[int, tuple[float, float, float]]:
    """Spiel-ID -> Markt-Wahrscheinlichkeiten (Heim, Remis, Gast) für 1. und 2. BL."""
    erg: dict[int, tuple] = {}
    saisons = sorted({s.saison for s in bestand.spiele})
    config.ROH.mkdir(parents=True, exist_ok=True)
    for liga, info in config.LIGEN.items():
        code = info.get("football_data")
        if not code:
            continue
        for saison in saisons:
            kurz = f"{saison % 100:02d}{(saison + 1) % 100:02d}"
            datei = config.ROH / f"fd_{code}_{saison}.csv"
            if datei.exists() and saison < config.AKTUELLE_SAISON:
                text = datei.read_text(encoding="utf-8")
            else:
                text = _get_text(f"{FD}/mmz4281/{kurz}/{code}.csv")
                if text and saison < config.AKTUELLE_SAISON:
                    datei.write_text(text, encoding="utf-8")
            if text:
                _zeilen_zuordnen(list(csv.DictReader(io.StringIO(text))), liga, bestand, erg)
    # kommende Spiele (wird freitags und dienstags aktualisiert)
    text = _get_text(f"{FD}/fixtures.csv")
    if text:
        zeilen = list(csv.DictReader(io.StringIO(text)))
        for liga, info in config.LIGEN.items():
            if info.get("football_data"):
                _zeilen_zuordnen([z for z in zeilen if z.get("Div") == info["football_data"]],
                                 liga, bestand, erg)
    return erg


ODDS_API_SPEICHER = config.DATEN / "quoten_aktuell.json"
ODDS_API_ABSTAND_H = 8   # höchstens alle 8 Stunden abfragen: ~3 × 3 × 30 = 270 der 500 Freiabfragen/Monat


def aktuell_odds_api(bestand: Datenbestand) -> dict[int, tuple[float, float, float]]:
    """Aktuelle Quoten aller drei Ligen über The Odds API (nur mit Schlüssel).

    Die Seite läuft stündlich; damit das kostenlose Kontingent reicht, wird die API
    nur alle ODDS_API_ABSTAND_H Stunden gefragt und dazwischen der Speicher genutzt."""
    if not config.ODDS_API_KEY:
        return {}
    import json
    from datetime import timedelta
    try:
        alt = json.loads(ODDS_API_SPEICHER.read_text(encoding="utf-8"))
        stand = datetime.fromisoformat(alt["stand"])
        if datetime.now(timezone.utc) - stand < timedelta(hours=ODDS_API_ABSTAND_H):
            offen = {s.id for s in bestand.spiele if not s.gespielt}
            return {int(k): tuple(v) for k, v in alt["quoten"].items() if int(k) in offen}
    except (OSError, ValueError, KeyError):
        pass
    erg = _odds_api_abfragen(bestand)
    if erg:
        ODDS_API_SPEICHER.write_text(json.dumps(
            {"stand": datetime.now(timezone.utc).isoformat(timespec="minutes"),
             "quoten": {str(k): list(v) for k, v in erg.items()}}), encoding="utf-8")
    return erg


def _odds_api_abfragen(bestand: Datenbestand) -> dict[int, tuple[float, float, float]]:
    erg = {}
    for liga, info in config.LIGEN.items():
        try:
            r = SITZUNG.get(
                f"https://api.the-odds-api.com/v4/sports/{info['odds_api']}/odds",
                params={"apiKey": config.ODDS_API_KEY, "regions": "eu",
                        "markets": "h2h", "oddsFormat": "decimal"},
                timeout=30)
            r.raise_for_status()
            daten = r.json()
        except (requests.RequestException, ValueError) as e:
            print(f"  The Odds API ({liga}) nicht erreichbar: {e}")
            continue
        nach_liga = [s for s in bestand.spiele if s.liga == liga and not s.gespielt]
        for ev in daten:
            heim, gast = ev.get("home_team", ""), ev.get("away_team", "")
            summen = {"h": [], "d": [], "a": []}
            for bm in ev.get("bookmakers", []):
                for markt in bm.get("markets", []):
                    if markt.get("key") != "h2h":
                        continue
                    for o in markt.get("outcomes", []):
                        if o.get("name") == heim:
                            summen["h"].append(o["price"])
                        elif o.get("name") == gast:
                            summen["a"].append(o["price"])
                        elif o.get("name") == "Draw":
                            summen["d"].append(o["price"])
            if not all(summen.values()):
                continue
            p = _wahrscheinlichkeiten(*(sum(v) / len(v) for v in summen.values()))
            try:
                tag = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
            except (KeyError, ValueError):
                continue
            s = ordne_spiel_zu(heim, gast, _kandidaten(nach_liga, liga, tag), bestand.teams)
            if s and p:
                erg[s.id] = p
    return erg


def mische(p_modell, p_markt, gewicht: float):
    """Gewichtetes Mittel aus Modell- und Markt-Wahrscheinlichkeiten."""
    if not p_markt or gewicht <= 0:
        return p_modell
    return tuple((1 - gewicht) * m + gewicht * q for m, q in zip(p_modell, p_markt))
