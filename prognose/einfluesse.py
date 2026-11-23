"""Zusätzliche Einflüsse, die aus dem Spielplan berechenbar sind.

* Ruhetage: Tage seit dem letzten Pflichtspiel (Liga oder Pokal), Unterschied
  zwischen beiden Teams.
* Form: Tordifferenz der letzten 5 Ligaspiele (Unterschied beider Teams).
* Elo: eigenständige Stärkezahl, reagiert schneller auf Ergebnisse als das Modell.
* Heimstärke: Ist ein Team zu Hause besser als auswärts, mehr als üblich?
* Aufsteiger / Absteiger: Schwung oder Ernüchterung nach dem Ligawechsel (Hinrunde).
* Motivation: Kann ein Team rechnerisch in keine umkämpfte Tabellenzone mehr
  kommen (Meisterschaft, Europa, Aufstieg, Relegation, Abstieg), hat es
  "nichts mehr zu spielen".

Alle Werte werden nur aus Informationen VOR dem Anstoß berechnet, damit der
Rückwärtstest ehrlich bleibt.
"""
from __future__ import annotations

from collections import defaultdict

import numpy as np

from . import config
from .daten import Spiel

MERKMALE = ["ruhetage", "nichts_eigen", "nichts_gegner",
            "form", "elo", "heimstaerke", "aufsteiger", "absteiger"]
STANDARD_RUHE = 7.0


def umkaempfte_plaetze(liga: str) -> set[int]:
    plaetze = set()
    for p in config.LIGEN[liga]["zonen"].values():
        plaetze.update(p)
    if liga == "bl1":
        plaetze.update([5, 6, 7])  # Europa-League- und Conference-Plätze
    return plaetze


def ruhetage(spiele: list[Spiel], pokal: list[Spiel]) -> dict[int, tuple[float, float]]:
    """Spiel-ID -> (Ruhetage Heim, Ruhetage Gast)."""
    alle = sorted(spiele + pokal, key=lambda s: s.anstoss)
    letztes: dict[int, object] = {}
    erg = {}
    for s in alle:
        r = []
        for t in (s.heim_id, s.gast_id):
            vorher = letztes.get(t)
            if vorher is None:
                r.append(STANDARD_RUHE)
            else:
                tage = (s.anstoss - vorher).total_seconds() / 86400
                r.append(STANDARD_RUHE if tage > 21 else tage)  # Sommer-/Winterpause
            letztes[t] = s.anstoss
        if s.liga != config.POKAL:
            erg[s.id] = (r[0], r[1])
    return erg


def nichts_zu_spielen(spiele: list[Spiel]) -> dict[int, tuple[bool, bool]]:
    """Spiel-ID -> (Heim hat nichts mehr zu spielen, Gast hat nichts mehr zu spielen)."""
    erg = {}
    gruppen: dict[tuple[str, int], list[Spiel]] = defaultdict(list)
    for s in spiele:
        gruppen[(s.liga, s.saison)].append(s)
    for (liga, saison), liste in gruppen.items():
        liste.sort(key=lambda s: s.anstoss)
        teams = {t for s in liste for t in (s.heim_id, s.gast_id)}
        spiele_gesamt = {t: 0 for t in teams}
        for s in liste:
            spiele_gesamt[s.heim_id] += 1
            spiele_gesamt[s.gast_id] += 1
        punkte = {t: 0 for t in teams}
        gespielt = {t: 0 for t in teams}
        zonen = umkaempfte_plaetze(liga)
        i = 0
        while i < len(liste):
            # alle Spiele mit gleichem Anstoß sehen denselben Tabellenstand
            j = i
            while j < len(liste) and liste[j].anstoss == liste[i].anstoss:
                j += 1
            for s in liste[i:j]:
                flags = []
                for t in (s.heim_id, s.gast_id):
                    rest_t = spiele_gesamt[t] - gespielt[t]
                    if rest_t > 8:             # vor dem Saisonendspurt: immer motiviert
                        flags.append(False)
                        continue
                    p_max = punkte[t] + 3 * rest_t
                    p_min = punkte[t]
                    sicher_vorn = sum(1 for o in teams if o != t and punkte[o] > p_max)
                    koennen_vorbei = sum(
                        1 for o in teams if o != t
                        and punkte[o] + 3 * (spiele_gesamt[o] - gespielt[o]) >= p_min)
                    bester, schlechtester = 1 + sicher_vorn, 1 + koennen_vorbei
                    flags.append(not any(bester <= z <= schlechtester for z in zonen))
                erg[s.id] = (flags[0], flags[1])
            for s in liste[i:j]:
                if s.gespielt:
                    gespielt[s.heim_id] += 1
                    gespielt[s.gast_id] += 1
                    if s.tore_heim > s.tore_gast:
                        punkte[s.heim_id] += 3
                    elif s.tore_heim < s.tore_gast:
                        punkte[s.gast_id] += 3
                    else:
                        punkte[s.heim_id] += 1
                        punkte[s.gast_id] += 1
            i = j
    return erg


class Einfluesse:
    """Berechnet die Merkmals-Vektoren für Heim- und Gasttore eines Spiels."""

    def __init__(self, spiele: list[Spiel], pokal: list[Spiel], aktiv: list[str]):
        self.ruhe = ruhetage(spiele, pokal)
        self.motiv = nichts_zu_spielen(spiele)
        self.verlauf = verlauf_merkmale(spiele)
        self.aktiv = [m for m in MERKMALE if m in aktiv]

    def mit(self, aktiv: list[str]) -> "Einfluesse":
        """Kopie mit anderer Auswahl aktiver Merkmale (ohne Neuberechnung)."""
        neu = object.__new__(Einfluesse)
        neu.ruhe, neu.motiv, neu.verlauf = self.ruhe, self.motiv, self.verlauf
        neu.aktiv = [m for m in MERKMALE if m in aktiv]
        return neu

    def vektoren(self, s: Spiel) -> tuple[np.ndarray, np.ndarray]:
        rh, rg = self.ruhe.get(s.id, (STANDARD_RUHE, STANDARD_RUHE))
        nh, ng = self.motiv.get(s.id, (False, False))
        diff = float(np.clip((min(rh, 10) - min(rg, 10)) / 7, -1, 1))
        v = self.verlauf.get(s.id, {})
        form = float(np.clip((v.get("form_heim", 0) - v.get("form_gast", 0)) / 3, -1, 1))
        elo = float(np.clip((v.get("elo_heim", 0) - v.get("elo_gast", 0)) / 400, -1.5, 1.5))
        hs = float(np.clip(v.get("heimstaerke", 0), -1, 1))
        werte_h = {"ruhetage": diff, "nichts_eigen": float(nh), "nichts_gegner": float(ng),
                   "form": form, "elo": elo, "heimstaerke": hs,
                   "aufsteiger": v.get("auf_heim", 0), "absteiger": v.get("ab_heim", 0)}
        werte_g = {"ruhetage": -diff, "nichts_eigen": float(ng), "nichts_gegner": float(nh),
                   "form": -form, "elo": -elo, "heimstaerke": -hs,
                   "aufsteiger": v.get("auf_gast", 0), "absteiger": v.get("ab_gast", 0)}
        return (np.array([werte_h[m] for m in self.aktiv]),
                np.array([werte_g[m] for m in self.aktiv]))

    def beschreibung(self, s: Spiel) -> dict:
        rh, rg = self.ruhe.get(s.id, (STANDARD_RUHE, STANDARD_RUHE))
        nh, ng = self.motiv.get(s.id, (False, False))
        v = self.verlauf.get(s.id, {})
        return {"ruhe_heim": round(rh, 1), "ruhe_gast": round(rg, 1),
                "nichts_heim": nh, "nichts_gast": ng,
                "form_heim": round(v.get("form_heim", 0), 2), "form_gast": round(v.get("form_gast", 0), 2),
                "elo_heim": round(v.get("elo_heim", 0)), "elo_gast": round(v.get("elo_gast", 0)),
                "heimstaerke": round(v.get("heimstaerke", 0), 2),
                "aufsteiger": [v.get("auf_heim", 0) > 0, v.get("auf_gast", 0) > 0],
                "absteiger": [v.get("ab_heim", 0) > 0, v.get("ab_gast", 0) > 0]}


# ---------------------------------------------------------------------------
# Weitere Einflüsse (alle nur aus Spielen VOR dem Anstoß berechnet)
# ---------------------------------------------------------------------------
FORM_SPIELE = 5            # Form = Tordifferenz der letzten 5 Ligaspiele
HEIM_ALPHA = 0.08          # Gedächtnis der Heimstärke (etwa die letzten 25 Heimspiele)
LIGA_INDEX = {l: i for i, l in enumerate(config.LIGEN)}


def verlauf_merkmale(spiele: list[Spiel]) -> dict[int, dict]:
    """Spiel-ID -> Form, Elo, persönliche Heimstärke, Auf-/Absteiger (vor dem Anstoß)."""
    from collections import deque
    from . import elo as elo_mod

    # in welcher Liga spielte ein Team in welcher Saison?
    liga_in: dict[tuple[int, int], str] = {}
    for s in spiele:
        for t in (s.heim_id, s.gast_id):
            liga_in[(t, s.saison)] = s.liga

    letzte = defaultdict(lambda: deque(maxlen=FORM_SPIELE))
    werte: dict[int, float] = {}
    saison_von: dict[int, int] = {}
    heim_gd: dict[int, list] = defaultdict(lambda: [0.0, 0])    # EWMA, Anzahl
    gast_gd: dict[int, list] = defaultdict(lambda: [0.0, 0])
    liga_heim = defaultdict(lambda: 0.35)                        # Liga-Schnitt Heim-Tordifferenz
    erg = {}
    for s in sorted(spiele, key=lambda x: x.anstoss):
        # Elo vor dem Spiel (mit Rückführung zu Saisonbeginn wie in elo.py)
        for t in (s.heim_id, s.gast_id):
            if t not in werte:
                werte[t] = elo_mod.START[s.liga]
            elif saison_von.get(t) != s.saison:
                werte[t] = ((1 - elo_mod.ZURUECK_ZUM_SCHNITT) * werte[t]
                            + elo_mod.ZURUECK_ZUM_SCHNITT * elo_mod.START[s.liga])
            saison_von[t] = s.saison

        def form(t):
            f = letzte[t]
            return sum(f) / len(f) if f else 0.0

        def heimstaerke(t):
            h, a = heim_gd[t], gast_gd[t]
            n = min(h[1], a[1])
            roh = ((h[0] - a[0]) - (liga_heim[s.liga] - (-liga_heim[s.liga]))) / 2
            return roh * n / (n + 10)          # wenige Spiele -> Richtung 0 ziehen

        def ligawechsel(t):
            vorher = liga_in.get((t, s.saison - 1))
            if not vorher or vorher == s.liga:
                return 0, 0
            abklingen = max(0.0, 1 - (s.spieltag - 1) / 17)   # wirkt in der Hinrunde
            if LIGA_INDEX[vorher] > LIGA_INDEX[s.liga]:
                return abklingen, 0
            return 0, abklingen

        auf_h, ab_h = ligawechsel(s.heim_id)
        auf_g, ab_g = ligawechsel(s.gast_id)
        erg[s.id] = {
            "form_heim": form(s.heim_id), "form_gast": form(s.gast_id),
            "elo_heim": werte[s.heim_id], "elo_gast": werte[s.gast_id],
            "heimstaerke": heimstaerke(s.heim_id),
            "auf_heim": auf_h, "auf_gast": auf_g, "ab_heim": ab_h, "ab_gast": ab_g,
        }
        if not s.gespielt:
            continue
        gd = s.tore_heim - s.tore_gast
        letzte[s.heim_id].append(gd)
        letzte[s.gast_id].append(-gd)
        for speicher, t, wert in ((heim_gd, s.heim_id, gd), (gast_gd, s.gast_id, -gd)):
            alt = speicher[t]
            alt[0] = wert if alt[1] == 0 else (1 - HEIM_ALPHA) * alt[0] + HEIM_ALPHA * wert
            alt[1] += 1
        liga_heim[s.liga] = 0.995 * liga_heim[s.liga] + 0.005 * gd
        # Elo nach dem Spiel
        rh, rg = werte[s.heim_id], werte[s.gast_id]
        e = elo_mod.erwartung(rh + elo_mod.HEIMVORTEIL, rg)
        ergebnis = 1.0 if gd > 0 else 0.5 if gd == 0 else 0.0
        faktor = 1 if abs(gd) <= 1 else (1.5 if abs(gd) == 2 else (11 + abs(gd)) / 8)
        delta = elo_mod.K * faktor * (ergebnis - e)
        werte[s.heim_id] = rh + delta
        werte[s.gast_id] = rg - delta
    return erg
