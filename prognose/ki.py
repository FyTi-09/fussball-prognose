"""KI-Teil: Nachrichten sammeln, auswerten, eigenen Tipp und Vorschau schreiben.

Ablauf je Liga und Spieltag:
1. Das Skript sucht über Google News (RSS, kostenlos) pro Verein nach aktuellen
   Meldungen zu Verletzungen, Sperren, Trainer usw.
2. Eine KI bekommt die nummerierten Schlagzeilen und die Modell-Prognose und
   antwortet mit strukturiertem JSON: Infos (mit Quellennummern), eigener Tipp,
   kurze Vorschau.
3. Infos ohne gültige Quellennummer werden verworfen – so stammen alle Links
   garantiert aus echten Suchtreffern und nicht aus der Fantasie der KI.

KI-Anbieter: zuerst Google Gemini (kostenloser Schlüssel), sonst lokal Ollama.
Ohne beides wird der KI-Teil einfach übersprungen.
"""
from __future__ import annotations

import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import requests

from . import config
from .daten import Datenbestand, Spiel, jetzt

KI_ORDNER = config.DATEN / "ki"
SITZUNG = requests.Session()
SITZUNG.headers["User-Agent"] = "Mozilla/5.0 (fussball-prognose, privates Hobbyprojekt)"
SUCHWOERTER = "(Verletzung OR verletzt OR fehlt OR Ausfall OR gesperrt OR Sperre OR Trainer OR Aufstellung)"


# --- 1. Nachrichten --------------------------------------------------------
def nachrichten(teamname: str, max_anzahl: int = 8) -> list[dict]:
    q = f'"{teamname}" {SUCHWOERTER} when:7d'
    url = f"https://news.google.com/rss/search?q={quote(q)}&hl=de&gl=DE&ceid=DE:de"
    try:
        r = SITZUNG.get(url, timeout=20)
        r.raise_for_status()
        wurzel = ET.fromstring(r.content)
    except (requests.RequestException, ET.ParseError):
        return []
    artikel = []
    for item in wurzel.iter("item"):
        titel = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        quelle = (item.findtext("source") or "").strip()
        try:
            datum = parsedate_to_datetime(item.findtext("pubDate") or "")
        except (TypeError, ValueError):
            datum = None
        if titel and link:
            artikel.append({"titel": titel, "link": link, "quelle": quelle,
                            "datum": datum.isoformat() if datum else None})
        if len(artikel) >= max_anzahl:
            break
    return artikel


# --- 2. KI-Anbieter ----------------------------------------------------------
def _gemini(prompt: str) -> str | None:
    if not config.GEMINI_API_KEY:
        return None
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{config.GEMINI_MODELL}:generateContent")
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.3}}
    for versuch in range(3):
        try:
            r = SITZUNG.post(url, json=body, timeout=120,
                             headers={"x-goog-api-key": config.GEMINI_API_KEY})
            if r.status_code == 429:          # Gratis-Limit erreicht
                print("  Gemini: Limit erreicht")
                return None
            r.raise_for_status()
            teile = r.json()["candidates"][0]["content"]["parts"]
            return "".join(t.get("text", "") for t in teile)
        except (requests.RequestException, KeyError, IndexError, ValueError) as e:
            print(f"  Gemini-Fehler: {e}")
            time.sleep(5 * (versuch + 1))
    return None


def ollama_erreichbar() -> bool:
    try:
        return SITZUNG.get(f"{config.OLLAMA_URL}/api/tags", timeout=3).ok
    except requests.RequestException:
        return False


def _ollama(prompt: str) -> str | None:
    if not ollama_erreichbar():
        return None
    try:
        r = SITZUNG.post(f"{config.OLLAMA_URL}/api/chat", timeout=900, json={
            "model": config.OLLAMA_MODELL, "stream": False, "format": "json", "think": False,
            "options": {"temperature": 0.3, "num_ctx": 16384},
            "messages": [{"role": "user", "content": prompt}],
        })
        r.raise_for_status()
        return r.json()["message"]["content"]
    except (requests.RequestException, KeyError, ValueError) as e:
        print(f"  Ollama-Fehler: {e}")
        return None


def frage_ki(prompt: str) -> tuple[str | None, str | None]:
    for name, fn in (("Gemini", _gemini), ("Ollama", _ollama)):
        antwort = fn(prompt)
        if antwort:
            return antwort, name
    return None, None


def verfuegbar() -> bool:
    return bool(config.GEMINI_API_KEY) or ollama_erreichbar()


# --- 3. Prompt und Auswertung ------------------------------------------------
def _prompt(liga_name: str, eintraege: list[dict]) -> str:
    teile = [
        f"Du bist ein nüchterner Fußball-Analyst für die {liga_name}. Unten stehen die "
        "nächsten Spiele mit der Prognose eines statistischen Modells und nummerierten "
        "aktuellen Schlagzeilen zu beiden Vereinen.\n",
        "Aufgaben je Spiel:\n"
        "1. infos: Wichtige Fakten aus den Schlagzeilen (Verletzungen, Sperren, Trainer, "
        "Stimmung). JEDE Info braucht mindestens eine Quellennummer aus der Liste dieses "
        "Spiels. Erfinde nichts; was nicht in den Schlagzeilen steht, gibt es nicht. "
        "Ignoriere Schlagzeilen, die nicht zu diesem Verein gehören. Leere Liste ist erlaubt.\n"
        "2. tipp: dein eigener Ergebnistipp, z. B. \"2:1\". Nutze die Modellprognose als "
        "Ausgangspunkt und weiche nur ab, wenn die Infos es rechtfertigen.\n"
        "3. wahrscheinlichkeiten: deine Einschätzung [Heimsieg, Remis, Gastsieg] in Prozent, Summe 100.\n"
        "4. vorschau: 2–3 Sätze auf Deutsch, sachlich, ohne Wett-Empfehlung.\n",
        "Antworte NUR mit JSON in genau dieser Form:\n"
        '{"spiele":[{"id":123,"infos":[{"team":"heim","art":"verletzung","text":"...","quellen":[1]}],'
        '"tipp":"2:1","wahrscheinlichkeiten":[45,27,28],"vorschau":"..."}]}\n'
        'Erlaubte Werte: team = "heim" | "gast"; art = "verletzung" | "sperre" | "trainer" | '
        '"stimmung" | "sonstiges".\n',
    ]
    for e in eintraege:
        p = e["prognose"]["p"]
        teile.append(
            f"\n### Spiel id={e['id']}: {e['heim']} (heim) gegen {e['gast']} (gast), "
            f"{e['anstoss']}\nModell: Heimsieg {p[0]*100:.0f} %, Remis {p[1]*100:.0f} %, "
            f"Gastsieg {p[2]*100:.0f} %, Tipp {e['prognose']['tipp'][0]}:{e['prognose']['tipp'][1]}\n"
            "Schlagzeilen:")
        for nr, a in enumerate(e["artikel"], 1):
            teile.append(f"[{nr}] ({a['team']}) {a['titel']}")
        if not e["artikel"]:
            teile.append("(keine)")
    return "\n".join(teile)


def _parse_json(text: str) -> dict | None:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
    return None


def _pruefe(antwort: dict, eintraege: list[dict], anbieter: str) -> dict[int, dict]:
    nach_id = {e["id"]: e for e in eintraege}
    erg = {}
    for sp in antwort.get("spiele", []) if isinstance(antwort, dict) else []:
        try:
            sid = int(sp.get("id"))
        except (TypeError, ValueError):
            continue
        e = nach_id.get(sid)
        if not e:
            continue
        infos = []
        for info in sp.get("infos") or []:
            nummern = [n for n in (info.get("quellen") or []) if isinstance(n, int)
                       and 1 <= n <= len(e["artikel"])]
            if not nummern or not info.get("text"):
                continue      # ohne echte Quelle -> verwerfen
            infos.append({
                "team": info.get("team") if info.get("team") in ("heim", "gast") else None,
                "art": info.get("art") or "sonstiges",
                "text": str(info["text"])[:300],
                "quellen": [{"titel": e["artikel"][n - 1]["titel"],
                             "link": e["artikel"][n - 1]["link"],
                             "quelle": e["artikel"][n - 1]["quelle"]} for n in nummern],
            })
        tipp = None
        m = re.match(r"^\s*(\d{1,2})\s*[:\-]\s*(\d{1,2})\s*$", str(sp.get("tipp") or ""))
        if m:
            tipp = [int(m.group(1)), int(m.group(2))]
        p = sp.get("wahrscheinlichkeiten")
        if isinstance(p, list) and len(p) == 3 and all(isinstance(x, (int, float)) for x in p) and sum(p) > 0:
            p = [round(x / sum(p), 4) for x in p]
        else:
            p = None
        erg[sid] = {
            "infos": infos, "tipp": tipp, "p": p,
            "vorschau": str(sp.get("vorschau") or "")[:800],
            "anbieter": anbieter,
            "stand": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        }
    return erg


# --- 4. Hauptfunktion --------------------------------------------------------
def lade_gespeichert() -> dict[str, dict]:
    erg = {}
    if KI_ORDNER.exists():
        for datei in KI_ORDNER.glob("*.json"):
            try:
                inhalt = json.loads(datei.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            # Cloud- und PC-Läufe schreiben getrennte Dateien; der neueste Stand gewinnt
            for sid, eintrag in inhalt.items():
                if sid not in erg or eintrag.get("stand", "") > erg[sid].get("stand", ""):
                    erg[sid] = eintrag
    return erg


def lauf(bestand: Datenbestand, prognosen: dict[int, dict], erneuern_nach_h: float = 12) -> dict:
    """Erzeugt KI-Einschätzungen für die Spiele der nächsten Tage. Gibt alle gespeicherten zurück."""
    gespeichert = lade_gespeichert()
    if not verfuegbar():
        print("  KI: weder Gemini-Schlüssel noch Ollama verfügbar – übersprungen")
        return gespeichert
    KI_ORDNER.mkdir(parents=True, exist_ok=True)
    nun = jetzt()
    bis = nun + timedelta(days=config.KI_VORLAUF_TAGE)
    for liga, info in config.LIGEN.items():
        offen: list[Spiel] = [s for s in bestand.spiele if s.liga == liga
                              and s.saison == config.AKTUELLE_SAISON
                              and not s.beendet and nun < s.anstoss <= bis and s.id in prognosen]
        faellig = []
        for s in offen:
            alt = gespeichert.get(str(s.id))
            if alt:
                alter = nun - datetime.fromisoformat(alt["stand"])
                if alter < timedelta(hours=erneuern_nach_h):
                    continue
            faellig.append(s)
        if not faellig:
            continue
        print(f"  KI: {info['name']} – {len(faellig)} Spiele")
        cache_news: dict[int, list] = {}
        eintraege = []
        for s in faellig:
            artikel = []
            for rolle, tid in (("heim", s.heim_id), ("gast", s.gast_id)):
                if tid not in cache_news:
                    cache_news[tid] = nachrichten(bestand.teams[tid]["name"])
                    time.sleep(1)  # freundlich zu Google News
                for a in cache_news[tid][:6]:
                    artikel.append({**a, "team": rolle})
            eintraege.append({
                "id": s.id, "heim": bestand.teams[s.heim_id]["name"],
                "gast": bestand.teams[s.gast_id]["name"],
                "anstoss": s.anstoss.strftime("%d.%m.%Y %H:%M UTC"),
                "prognose": prognosen[s.id], "artikel": artikel,
            })
        antwort, anbieter = frage_ki(_prompt(info["name"], eintraege))
        if not antwort:
            print("  KI: keine Antwort")
            continue
        daten = _parse_json(antwort)
        neu = _pruefe(daten or {}, eintraege, anbieter)
        print(f"  KI ({anbieter}): {len(neu)} Spiele ausgewertet")
        gespeichert.update({str(k): v for k, v in neu.items()})
        spieltag = faellig[0].spieltag
        quelle = "pc" if anbieter == "Ollama" else "cloud"
        datei = KI_ORDNER / f"{liga}_{config.AKTUELLE_SAISON}_{spieltag:02d}_{quelle}.json"
        bisher = {}
        if datei.exists():
            try:
                bisher = json.loads(datei.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        bisher.update({str(k): v for k, v in neu.items()})
        datei.write_text(json.dumps(bisher, ensure_ascii=False, indent=1), encoding="utf-8")
    return gespeichert
