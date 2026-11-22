"""Elo-Rating als anschaulicher Stärkewert und für die Formkurve.

Jedes Team hat eine Zahl (Schnitt in der Bundesliga etwa 1500). Nach jedem
Spiel wandern Punkte vom Verlierer zum Gewinner – mehr, wenn der Sieg
überraschend oder deutlich war. Die Werte aller drei Ligen liegen auf einer
gemeinsamen Skala, weil Aufsteiger ihre Punkte mitnehmen.
"""
from __future__ import annotations

from collections import defaultdict

from .daten import Spiel

K = 20
HEIMVORTEIL = 60
START = {"bl1": 1500, "bl2": 1350, "bl3": 1200}
ZURUECK_ZUM_SCHNITT = 0.2   # zu Saisonbeginn ein Stück Richtung Liga-Schnitt


def erwartung(ra: float, rb: float) -> float:
    return 1 / (1 + 10 ** ((rb - ra) / 400))


def berechne(spiele: list[Spiel]):
    """Gibt (aktuelle Werte, Verlauf je Team) zurück.

    Verlauf: Liste von Einträgen {saison, spieltag, datum, elo, ergebnis}.
    """
    werte: dict[int, float] = {}
    liga_von: dict[int, str] = {}
    saison_von: dict[int, int] = {}
    verlauf = defaultdict(list)
    for s in sorted(spiele, key=lambda x: x.anstoss):
        if not s.gespielt:
            continue
        for t in (s.heim_id, s.gast_id):
            if t not in werte:
                werte[t] = START[s.liga]
            elif saison_von.get(t) != s.saison:
                # neue Saison: etwas zurück zum Schnitt der (neuen) Liga
                werte[t] = (1 - ZURUECK_ZUM_SCHNITT) * werte[t] + ZURUECK_ZUM_SCHNITT * START[s.liga]
            liga_von[t] = s.liga
            saison_von[t] = s.saison
        rh, rg = werte[s.heim_id], werte[s.gast_id]
        e = erwartung(rh + HEIMVORTEIL, rg)
        ergebnis = 1.0 if s.tore_heim > s.tore_gast else 0.5 if s.tore_heim == s.tore_gast else 0.0
        diff = abs(s.tore_heim - s.tore_gast)
        faktor = 1 if diff <= 1 else (1.5 if diff == 2 else (11 + diff) / 8)
        delta = K * faktor * (ergebnis - e)
        werte[s.heim_id] = rh + delta
        werte[s.gast_id] = rg - delta
        for t, eigen, gegen, punkte_ergebnis in (
            (s.heim_id, s.tore_heim, s.tore_gast, ergebnis),
            (s.gast_id, s.tore_gast, s.tore_heim, 1 - ergebnis),
        ):
            verlauf[t].append({
                "saison": s.saison, "liga": s.liga, "spieltag": s.spieltag,
                "datum": s.anstoss.isoformat(), "elo": round(werte[t], 1),
                "ergebnis": "S" if punkte_ergebnis == 1 else ("U" if punkte_ergebnis == 0.5 else "N"),
                "tore": f"{eigen}:{gegen}",
                "gegner": s.gast_id if t == s.heim_id else s.heim_id,
                "heim": t == s.heim_id,
            })
    return werte, verlauf


def tendenz_aus_elo(ra: float, rb: float) -> float:
    """Erwartungswert (Sieg=1, Remis=0.5) – nur zur Info, nicht für die Prognose."""
    return erwartung(ra + HEIMVORTEIL, rb)


