"""Zusätzliche Einflüsse, die aus dem Spielplan berechenbar sind.

* Ruhetage: Tage seit dem letzten Pflichtspiel (Liga oder Pokal), Unterschied
  zwischen beiden Teams.
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

MERKMALE = ["ruhetage", "nichts_eigen", "nichts_gegner"]
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
        self.aktiv = [m for m in MERKMALE if m in aktiv]

    def mit(self, aktiv: list[str]) -> "Einfluesse":
        """Kopie mit anderer Auswahl aktiver Merkmale (ohne Neuberechnung)."""
        neu = object.__new__(Einfluesse)
        neu.ruhe, neu.motiv = self.ruhe, self.motiv
        neu.aktiv = [m for m in MERKMALE if m in aktiv]
        return neu

    def vektoren(self, s: Spiel) -> tuple[np.ndarray, np.ndarray]:
        rh, rg = self.ruhe.get(s.id, (STANDARD_RUHE, STANDARD_RUHE))
        nh, ng = self.motiv.get(s.id, (False, False))
        diff = float(np.clip((min(rh, 10) - min(rg, 10)) / 7, -1, 1))
        werte_h = {"ruhetage": diff, "nichts_eigen": float(nh), "nichts_gegner": float(ng)}
        werte_g = {"ruhetage": -diff, "nichts_eigen": float(ng), "nichts_gegner": float(nh)}
        return (np.array([werte_h[m] for m in self.aktiv]),
                np.array([werte_g[m] for m in self.aktiv]))

    def beschreibung(self, s: Spiel) -> dict:
        rh, rg = self.ruhe.get(s.id, (STANDARD_RUHE, STANDARD_RUHE))
        nh, ng = self.motiv.get(s.id, (False, False))
        return {"ruhe_heim": round(rh, 1), "ruhe_gast": round(rg, 1),
                "nichts_heim": nh, "nichts_gast": ng}
