"""Die Webseite rechnet Tipps im Browser neu (Tab Tipprunde). Sie muss dasselbe
ergeben wie Python – sonst tippst du etwas anderes, als der Test gemessen hat."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from prognose.bewertung import kicktipp_punkte
from prognose.modell import ergebnis_matrix, tendenz
from prognose.vorhersage import passe_matrix_an, waehle_kicktipp

APP = Path(__file__).resolve().parent.parent / "site" / "app.js"
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node nicht installiert")


def _js_funktionen() -> str:
    """Schneidet die reinen Rechenfunktionen aus app.js heraus."""
    src = APP.read_text(encoding="utf-8")
    teile = []
    for name in ("poissonPmf", "matrix", "tendenzAus", "passeAn", "punkteFuer"):
        m = re.search(rf"^function {name}\(.*?^}}", src, re.S | re.M)
        assert m, f"{name} nicht in app.js gefunden"
        teile.append(m.group(0))
    tend = re.search(r"^const tendenz = .*?;$", src, re.M).group(0)
    vorl = re.search(r"^const REGEL_VORLAGEN = \{.*?^\};", src, re.S | re.M).group(0)
    bt = re.search(r"^function besterTipp\(.*?^}", src, re.S | re.M).group(0)
    return "\n".join([tend, vorl, *teile, bt])


def _node(code: str):
    out = subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


FAELLE = [(1.6, 1.1, -0.06, None), (0.9, 1.9, -0.04, None), (1.3, 1.2, -0.05, [0.62, 0.22, 0.16]),
          (1.2, 1.2, 0.0, [0.30, 0.34, 0.36])]


@pytest.mark.parametrize("lam, mu, rho, markt", FAELLE)
def test_tipprunde_gleich_python(lam, mu, rho, markt):
    m = ergebnis_matrix(lam, mu, rho)
    p = tendenz(m)
    prog = {"lam": lam, "mu": mu, "rho": rho, "p": list(p), "p_markt": markt}
    js = _js_funktionen() + f"""
      const r = {{...REGEL_VORLAGEN.kicktipp, modus: "punkte"}};
      console.log(JSON.stringify(besterTipp({{prognose: {json.dumps(prog)}}}, r)));"""
    erg = _node(js)
    ziel = markt if markt else p
    py_tipp, py_erw = waehle_kicktipp(passe_matrix_an(m, ziel))
    assert erg["tipp"] == list(py_tipp)
    assert erg["erw"] == pytest.approx(py_erw, abs=1e-9)


def test_punkteregel_js_gleich_python():
    js = _js_funktionen() + """
      const r = REGEL_VORLAGEN.kicktipp, aus = [];
      for (let a=0;a<5;a++) for (let b=0;b<5;b++) for (let i=0;i<5;i++) for (let j=0;j<5;j++)
        aus.push([a,b,i,j,punkteFuer(a,b,i,j,r)]);
      console.log(JSON.stringify(aus));"""
    for a, b, i, j, pkt in _node(js):
        assert pkt == kicktipp_punkte((a, b), i, j), (a, b, i, j)


@pytest.mark.parametrize("markt", [None, (0.55, 0.25, 0.20), (0.20, 0.27, 0.53)])
def test_gespeicherter_tipprunde_tipp_gleich_seite(markt):
    """Was im Archiv als 'tipprunde' gemessen wird, muss genau der Tipp sein,
    den der Tab Tipprunde anzeigt."""
    import numpy as np
    from datetime import datetime, timezone
    from prognose.daten import Spiel
    from prognose.einfluesse import Einfluesse
    from prognose.modell import Modell
    from prognose.vorhersage import prognose

    mod = Modell(team_index={1: 0, 2: 1}, team_liga=np.array([0, 0]), basis=0.2,
                 heimvorteil=np.array([0.25, 0.2, 0.2]), angriff=np.array([0.2, -0.1]),
                 abwehr=np.array([0.1, -0.15]), liga_angriff=np.zeros(3), liga_abwehr=np.zeros(3),
                 beta=np.array([]), rho=-0.05, merkmale=[])
    s = Spiel(1, "bl1", 2026, 1, datetime(2026, 12, 1, tzinfo=timezone.utc), 1, 2, None, None, False)
    p = prognose(mod, s, Einfluesse([], [], []), markt, 0.5, mit_begruendung=False)
    js = _js_funktionen() + f"""
      const r = {{...REGEL_VORLAGEN.kicktipp, modus: "punkte"}};
      console.log(JSON.stringify(besterTipp({{prognose: {json.dumps(p)}}}, r)));"""
    assert _node(js)["tipp"] == p["tipp_tipprunde"]
