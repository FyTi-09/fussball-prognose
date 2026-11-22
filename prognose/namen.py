"""Teamnamen aus fremden Quellen (Quoten, Verletzungen) den OpenLigaDB-Teams zuordnen.

Jede Quelle schreibt Vereine anders ("Bayern Munich", "FC Bayern München",
"Bayern"). Wir vergleichen vereinfachte Namen und nutzen zusätzlich das Datum:
Ein Spiel am selben Tag in derselben Liga mit ähnlichen Namen ist sehr
wahrscheinlich dasselbe Spiel.
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

FUELLWOERTER = {
    "fc", "sc", "sv", "vfl", "vfb", "tsg", "tsv", "fsv", "ssv", "sg", "ev", "e", "v",
    "1", "04", "05", "09", "1846", "1860", "1899", "1900", "1903", "1910", "07",
    "borussia", "eintracht", "spvgg", "rasenballsport", "rb", "bc", "sk", "ac",
    "munich", "am", "main", "of", "der", "ii", "kickers",
}
ALIAS = {
    "gladbach": "monchengladbach", "mgladbach": "monchengladbach",
    "koln": "koln", "cologne": "koln", "nurnberg": "nurnberg", "nuremberg": "nurnberg",
    "munchen": "bayern", "bayernmunchen": "bayern", "hertha": "hertha",
    "leverkusen": "leverkusen", "bielefeld": "bielefeld",
}


def vereinfache(name: str) -> str:
    n = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    n = n.replace("ü", "u").replace("ö", "o").replace("ä", "a")
    teile = [t for t in re.split(r"[^a-z0-9]+", n) if t and t not in FUELLWOERTER]
    teile = [ALIAS.get(t, t) for t in teile]
    return " ".join(teile)


def aehnlichkeit(a: str, b: str) -> float:
    va, vb = vereinfache(a), vereinfache(b)
    if not va or not vb:
        return 0.0
    if va == vb:
        return 1.0
    ta, tb = set(va.split()), set(vb.split())
    if ta & tb:
        # gemeinsames markantes Wort (z. B. "schalke", "bochum")
        return max(0.85, SequenceMatcher(None, va, vb).ratio())
    return SequenceMatcher(None, va, vb).ratio()


def ordne_spiel_zu(heim: str, gast: str, kandidaten, teams: dict[int, dict],
                   schwelle: float = 0.62):
    """Findet unter den Kandidaten-Spielen (gleiche Liga, ähnliches Datum) das passende."""
    bestes, wert = None, schwelle
    for s in kandidaten:
        nh = teams.get(s.heim_id, {})
        ng = teams.get(s.gast_id, {})
        sh = max(aehnlichkeit(heim, nh.get("name", "")), aehnlichkeit(heim, nh.get("kurz", "")))
        sg = max(aehnlichkeit(gast, ng.get("name", "")), aehnlichkeit(gast, ng.get("kurz", "")))
        w = min(sh, sg)
        if w > wert:
            bestes, wert = s, w
    return bestes
