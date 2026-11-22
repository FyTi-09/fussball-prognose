/* Fußball-Prognose – Oberfläche und Live-Teil.
 *
 * Die schweren Berechnungen macht Python (run.py) und legt sie in data/*.json ab.
 * Dieses Skript zeigt sie an und ergänzt während der Spiele den Live-Teil:
 * Es holt alle 60 Sekunden die Spielstände von OpenLigaDB und rechnet
 * Live-Siegchancen, Live-Tabelle und Live-Tabellenprognose direkt im Browser.
 */
"use strict";

const API = "https://api.openligadb.de";
const LIVE_INTERVALL = 60_000;
const LIVE_SIMULATIONEN = 4000;
const ZUSTAND = {
  index: null, daten: {}, treffer: null, backtest: null,
  liga: null, ansicht: "spieltag", spieltag: {}, team: null,
  offen: new Set(), liveVerlauf: {}, liveTimer: null, liveSim: {}, liveStand: null,
};
const $ = (s, el = document) => el.querySelector(s);
const inhalt = $("#inhalt");

/* ---------- Hilfsfunktionen ---------- */
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pct = (x, d = 0) => (x == null ? "–" : (x * 100).toFixed(d).replace(".", ",") + " %");
const zahl = (x, d = 1) => (x == null ? "–" : Number(x).toFixed(d).replace(".", ","));
const sicherUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : "#");
const TAGE = ["So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"];
function datum(iso) {
  const d = new Date(iso);
  return `${TAGE[d.getDay()]} ${String(d.getDate()).padStart(2, "0")}.${String(d.getMonth() + 1).padStart(2, "0")}. ` +
    `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}
const tendenz = (h, g) => (h > g ? 0 : h === g ? 1 : 2);
const daten = () => ZUSTAND.daten[ZUSTAND.liga];
const team = (id) => daten().teams[id] || { name: "?", kurz: "?", logo: "" };
const logo = (t) => (t.logo ? `<img src="${esc(t.logo)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : "");

async function ladeJson(pfad) {
  const r = await fetch(pfad, { cache: "no-cache" });
  if (!r.ok) throw new Error(pfad + ": " + r.status);
  return r.json();
}

/* ---------- Mathe: Ergebnis-Matrix wie in Python ---------- */
function poissonPmf(k, l) {
  let p = Math.exp(-l);
  for (let i = 1; i <= k; i++) p *= l / i;
  return p;
}
function matrix(lam, mu, rho = 0, n = 10) {
  const m = [];
  let s = 0;
  for (let i = 0; i <= n; i++) {
    m.push([]);
    for (let j = 0; j <= n; j++) {
      let v = poissonPmf(i, lam) * poissonPmf(j, mu);
      if (i === 0 && j === 0) v *= Math.max(1 - lam * mu * rho, 1e-9);
      if (i === 0 && j === 1) v *= Math.max(1 + lam * rho, 1e-9);
      if (i === 1 && j === 0) v *= Math.max(1 + mu * rho, 1e-9);
      if (i === 1 && j === 1) v *= Math.max(1 - rho, 1e-9);
      m[i].push(v); s += v;
    }
  }
  return m.map((z) => z.map((v) => v / s));
}
function tendenzAus(m) {
  const t = [0, 0, 0];
  m.forEach((z, i) => z.forEach((v, j) => { t[tendenz(i, j)] += v; }));
  return t;
}
function passeAn(m, ziel) {
  const alt = tendenzAus(m);
  return m.map((z, i) => z.map((v, j) => { const k = tendenz(i, j); return alt[k] > 0 ? v * ziel[k] / alt[k] : v; }));
}

/* ---------- Live ---------- */
function geschaetzteMinute(anstoss) {
  const min = (Date.now() - new Date(anstoss).getTime()) / 60000;
  if (min < 0) return 0;
  if (min <= 47) return min;
  if (min <= 62) return 45;               // Halbzeitpause
  return Math.min(min - 17, 93);
}
function liveWahrscheinlichkeit(sp) {
  const p = sp.prognose;
  if (!p || !sp.erg) return null;
  const minute = geschaetzteMinute(sp.anstoss);
  const rest = Math.max(0, 90 - minute) / 90;
  const m = matrix(p.lam * rest, p.mu * rest, 0, 8);
  const t = [0, 0, 0];
  m.forEach((z, i) => z.forEach((v, j) => { t[tendenz(sp.erg[0] + i, sp.erg[1] + j)] += v; }));
  return { p: t, minute };
}
function istLiveFenster(sp) {
  const start = new Date(sp.anstoss).getTime();
  return !sp.beendet && Date.now() >= start && Date.now() <= start + 150 * 60000;
}
function standAus(m) {
  const res = (m.matchResults || []).slice().sort((a, b) => a.resultOrderID - b.resultOrderID);
  const end = res.filter((r) => r.resultTypeID === 2);
  const r = end.length ? end[end.length - 1] : null;
  if (m.matchIsFinished && r) return [r.pointsTeam1, r.pointsTeam2];
  const tore = m.goals || [];
  if (tore.length) { const g = tore[tore.length - 1]; return [g.scoreTeam1 ?? 0, g.scoreTeam2 ?? 0]; }
  if (r) return [r.pointsTeam1, r.pointsTeam2];
  return Date.now() >= new Date(m.matchDateTimeUTC || m.matchDateTime).getTime() ? [0, 0] : null;
}

async function liveAktualisieren() {
  const d = daten();
  if (!d) return;
  const spieltage = new Set([d.aktueller_spieltag]);
  d.spiele.forEach((s) => { if (istLiveFenster(s)) spieltage.add(s.st); });
  let geaendert = false;
  for (const st of spieltage) {
    let liste;
    try { liste = await ladeJson(`${API}/getmatchdata/${d.liga}/${d.saison}/${st}`); } catch { continue; }
    for (const m of liste) {
      const sp = d.spiele.find((s) => s.id === m.matchID);
      if (!sp) continue;
      const erg = standAus(m);
      const beendet = !!m.matchIsFinished;
      const neueTore = (m.goals || []).map((g) => ({ minute: g.matchMinute, name: g.goalGetterName, heim: g.scoreTeam1, gast: g.scoreTeam2, elfmeter: g.isPenalty, eigentor: g.isOwnGoal }));
      if (JSON.stringify(erg) !== JSON.stringify(sp.erg) || beendet !== sp.beendet) geaendert = true;
      sp.erg = erg; sp.beendet = beendet; sp.tore = neueTore;
      sp.live = !beendet && erg != null && Date.now() >= new Date(sp.anstoss).getTime();
      if (sp.live) {
        const lw = liveWahrscheinlichkeit(sp);
        if (lw) (ZUSTAND.liveVerlauf[sp.id] ||= []).push({ min: lw.minute, p: lw.p });
      }
    }
  }
  const nochLive = d.spiele.some((s) => s.live || istLiveFenster(s));
  if (geaendert || nochLive) {
    ZUSTAND.liveSim[d.liga] = simuliere(d);
    ZUSTAND.liveStand = new Date();
  }
  zeichne();
  clearTimeout(ZUSTAND.liveTimer);
  if (nochLive) ZUSTAND.liveTimer = setTimeout(liveAktualisieren, LIVE_INTERVALL);
  else {
    // nächste Prüfung kurz vor dem nächsten Anstoß
    const naechster = d.spiele.filter((s) => !s.beendet && new Date(s.anstoss) > Date.now())
      .map((s) => new Date(s.anstoss).getTime()).sort((a, b) => a - b)[0];
    if (naechster && naechster - Date.now() < 6 * 3600e3) {
      ZUSTAND.liveTimer = setTimeout(liveAktualisieren, Math.max(naechster - Date.now() + 60000, 30000));
    }
  }
}

/* Live-Tabelle (laufende Spiele zählen mit ihrem aktuellen Stand) */
function tabelleJetzt(d) {
  const t = {};
  Object.keys(d.teams).forEach((id) => { t[id] = { id: +id, sp: 0, s: 0, u: 0, n: 0, tore: 0, gegentore: 0, pkt: 0, live: false }; });
  for (const s of d.spiele) {
    if (!s.erg || !(s.beendet || s.live)) continue;
    const h = t[s.heim], g = t[s.gast], [a, b] = s.erg;
    if (!h || !g) continue;
    h.sp++; g.sp++; h.tore += a; h.gegentore += b; g.tore += b; g.gegentore += a;
    if (s.live) { h.live = g.live = true; }
    if (a > b) { h.s++; g.n++; h.pkt += 3; } else if (a < b) { g.s++; h.n++; g.pkt += 3; } else { h.u++; g.u++; h.pkt++; g.pkt++; }
  }
  const liste = Object.values(t).sort((x, y) => y.pkt - x.pkt || (y.tore - y.gegentore) - (x.tore - x.gegentore) || y.tore - x.tore);
  liste.forEach((z, i) => { z.platz = i + 1; });
  return liste;
}

/* Live-Tabellenprognose: Restsaison im Browser simulieren */
function simuliere(d) {
  const ids = Object.keys(d.teams).map(Number);
  const idx = Object.fromEntries(ids.map((id, i) => [id, i]));
  const T = ids.length, N = LIVE_SIMULATIONEN;
  const pkt0 = new Float64Array(T), tor0 = new Float64Array(T), geg0 = new Float64Array(T);
  // laufende Spiele NICHT als fertig zählen: Basis nur aus beendeten
  for (const s of d.spiele) {
    if (!s.beendet || !s.erg) continue;
    const h = idx[s.heim], g = idx[s.gast], [a, b] = s.erg;
    tor0[h] += a; geg0[h] += b; tor0[g] += b; geg0[g] += a;
    if (a > b) pkt0[h] += 3; else if (a < b) pkt0[g] += 3; else { pkt0[h]++; pkt0[g]++; }
  }
  // Verteilungen der offenen Spiele vorbereiten (kumulativ)
  const offen = [];
  for (const s of d.spiele) {
    if (s.beendet || !s.prognose) continue;
    const p = s.prognose;
    let m, plusH = 0, plusG = 0;
    if (s.live && s.erg) {
      const rest = Math.max(0, 90 - geschaetzteMinute(s.anstoss)) / 90;
      m = matrix(p.lam * rest, p.mu * rest, 0, 8);
      [plusH, plusG] = s.erg;
    } else {
      m = passeAn(matrix(p.lam, p.mu, p.rho, 8), p.p);
    }
    const k = m.length, kum = new Float64Array(k * k);
    let acc = 0;
    for (let i = 0; i < k; i++) for (let j = 0; j < k; j++) { acc += m[i][j]; kum[i * k + j] = acc; }
    offen.push({ h: idx[s.heim], g: idx[s.gast], k, kum, plusH, plusG });
  }
  const platzZaehler = ids.map(() => new Float64Array(T));
  const pktSumme = new Float64Array(T);
  const pkt = new Float64Array(T), tor = new Float64Array(T), geg = new Float64Array(T);
  const ordnung = ids.map((_, i) => i);
  const schluessel = new Float64Array(T);
  for (let n = 0; n < N; n++) {
    pkt.set(pkt0); tor.set(tor0); geg.set(geg0);
    for (const o of offen) {
      const r = Math.random() * o.kum[o.kum.length - 1];
      let lo = 0, hi = o.kum.length - 1;
      while (lo < hi) { const mid = (lo + hi) >> 1; if (o.kum[mid] < r) lo = mid + 1; else hi = mid; }
      const a = Math.floor(lo / o.k) + o.plusH, b = (lo % o.k) + o.plusG;
      tor[o.h] += a; geg[o.h] += b; tor[o.g] += b; geg[o.g] += a;
      if (a > b) pkt[o.h] += 3; else if (a < b) pkt[o.g] += 3; else { pkt[o.h]++; pkt[o.g]++; }
    }
    for (let i = 0; i < T; i++) schluessel[i] = pkt[i] * 1e7 + (tor[i] - geg[i] + 500) * 1e4 + tor[i] * 10 + Math.random();
    ordnung.sort((x, y) => schluessel[y] - schluessel[x]);
    for (let r = 0; r < T; r++) platzZaehler[ordnung[r]][r]++;
    for (let i = 0; i < T; i++) pktSumme[i] += pkt[i];
  }
  const erg = {};
  ids.forEach((id, i) => {
    const plaetze = Array.from(platzZaehler[i], (c) => c / N);
    const zonen = {};
    for (const [name, pl] of Object.entries(d.zonen)) zonen[name] = pl.reduce((s, p) => s + plaetze[p - 1], 0);
    erg[id] = {
      punkte_schnitt: pktSumme[i] / N,
      platz_schnitt: plaetze.reduce((s, p, r) => s + p * (r + 1), 0),
      plaetze, zonen,
      punkte_p10: null, punkte_p90: null,
    };
  });
  return erg;
}

/* ---------- Zeichnen ---------- */
function balken(p) {
  return `<div class="balken" role="img" aria-label="Heimsieg ${pct(p[0])}, Remis ${pct(p[1])}, Gastsieg ${pct(p[2])}">
    <div class="h" style="width:${p[0] * 100}%"></div><div class="x" style="width:${p[1] * 100}%"></div><div class="a" style="width:${p[2] * 100}%"></div></div>
    <div class="prozente"><span>${pct(p[0])}</span><span class="m">Remis ${pct(p[1])}</span><span>${pct(p[2])}</span></div>`;
}

function sparkline(verlauf) {
  if (!verlauf || verlauf.length < 2) return "";
  const W = 300, H = 46;
  const x = (m) => (m / 93) * W;
  const lin = (k) => verlauf.map((v, i) => `${i ? "L" : "M"}${x(v.min).toFixed(1)},${(H - v.p[k] * H).toFixed(1)}`).join("");
  return `<svg class="live-verlauf" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Verlauf der Siegchancen">
    <line x1="0" x2="${W}" y1="${H / 2}" y2="${H / 2}" stroke="var(--border)" stroke-width="1"/>
    <path d="${lin(0)}" fill="none" stroke="var(--heim)" stroke-width="2" vector-effect="non-scaling-stroke"/>
    <path d="${lin(2)}" fill="none" stroke="var(--gast)" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>`;
}

function kiBox(ki, sp) {
  if (!ki) return "";
  const infos = (ki.infos || []).map((i) => {
    const wer = i.team === "heim" ? team(sp.heim).kurz : i.team === "gast" ? team(sp.gast).kurz : "";
    const quellen = (i.quellen || []).map((q) => `<a class="quelle" href="${esc(sicherUrl(q.link))}" target="_blank" rel="noopener noreferrer nofollow" title="${esc(q.titel)}">${esc(q.quelle || "Quelle")}</a>`).join(", ");
    return `<li>${wer ? `<b>${esc(wer)}:</b> ` : ""}${esc(i.text)} <span class="muted">(${quellen})</span></li>`;
  }).join("");
  return `<div class="ki-box"><h4>KI-Einschätzung <span class="badge">${esc(ki.anbieter || "KI")}</span></h4>
    ${ki.vorschau ? `<div>${esc(ki.vorschau)}</div>` : ""}
    ${ki.tipp ? `<div style="margin-top:6px">KI-Tipp: <b>${ki.tipp[0]}:${ki.tipp[1]}</b>${ki.p ? ` <span class="muted small">(${pct(ki.p[0])} / ${pct(ki.p[1])} / ${pct(ki.p[2])})</span>` : ""}</div>` : ""}
    ${infos ? `<ul>${infos}</ul>` : `<div class="muted small">Keine belegten Meldungen gefunden.</div>`}
    <div class="muted small" style="margin-top:4px">Automatisch erstellt, Stand ${esc(datum(ki.stand))}. Nur Aussagen mit Quelle werden angezeigt.</div></div>`;
}

function spielKarte(sp) {
  const h = team(sp.heim), g = team(sp.gast);
  const p = sp.prognose;
  const vorher = sp.prognose_vorher;
  const live = sp.live ? liveWahrscheinlichkeit(sp) : null;
  let badges = "";
  if (sp.live) badges += `<span class="badge live">Live ~${Math.round(live?.minute ?? 0)}'</span>`;
  else if (sp.beendet) badges += `<span class="badge">Beendet</span>`;
  if (sp.ki) badges += ` <span class="badge">KI</span>`;
  let mitte;
  if (sp.erg && (sp.beendet || sp.live)) mitte = `<div class="ergebnis">${sp.erg[0]}:${sp.erg[1]}</div>`;
  else if (p) mitte = `<div class="ergebnis muted">–:–</div>`;
  else mitte = `<div class="ergebnis muted">–:–</div>`;

  let unten = "";
  if (live) {
    unten += balken(live.p) + `<div class="tipp" style="margin-top:6px">Vor dem Spiel: Heimsieg ${pct(p.p[0])} · Tipp <b>${p.tipp[0]}:${p.tipp[1]}</b></div>`;
  } else if (p && !sp.beendet) {
    const tipps = [`Tipp <b>${p.tipp[0]}:${p.tipp[1]}</b>`];
    if (p.tipp_kicktipp && (p.tipp_kicktipp[0] !== p.tipp[0] || p.tipp_kicktipp[1] !== p.tipp[1])) {
      tipps.push(`<span title="Tipp mit den meisten erwarteten Kicktipp-Punkten">Kicktipp <b>${p.tipp_kicktipp[0]}:${p.tipp_kicktipp[1]}</b></span>`);
    }
    if (sp.ki?.tipp) tipps.push(`KI <b>${sp.ki.tipp[0]}:${sp.ki.tipp[1]}</b>`);
    unten += balken(p.p) + `<div class="tipp" style="margin-top:6px">${tipps.join(" · ")}</div>`;
  } else if (vorher && sp.erg) {
    const ep = vorher.endprognose;
    const treffer = (t) => {
      if (!t) return "";
      const ok = tendenz(t[0], t[1]) === tendenz(sp.erg[0], sp.erg[1]);
      const exakt = t[0] === sp.erg[0] && t[1] === sp.erg[1];
      return `<span class="badge ${ok ? "ok" : "falsch"}">${exakt ? "exakt ✓" : ok ? "Tendenz ✓" : "✗"}</span>`;
    };
    unten += (ep?.p ? balken(ep.p) : "") +
      `<div class="tipp" style="margin-top:6px">Prognose vorher: <b>${ep.tipp[0]}:${ep.tipp[1]}</b> ${treffer(ep.tipp)}` +
      (vorher.ki?.tipp ? ` · KI <b>${vorher.ki.tipp[0]}:${vorher.ki.tipp[1]}</b> ${treffer(vorher.ki.tipp)}` : "") + `</div>`;
  }

  // Details
  const teile = [];
  if (live && ZUSTAND.liveVerlauf[sp.id]?.length > 1) {
    teile.push(`<div><h4>Verlauf der Siegchancen (seit Öffnen der Seite)</h4>${sparkline(ZUSTAND.liveVerlauf[sp.id])}</div>`);
  }
  if (sp.tore?.length) {
    teile.push(`<div><h4>Tore</h4><div class="tore-liste">${sp.tore.map((t) => `${t.minute ?? "?"}' ${esc(t.name || "?")} (${t.heim}:${t.gast})${t.elfmeter ? " Elfmeter" : ""}${t.eigentor ? " Eigentor" : ""}`).join(" · ")}</div></div>`);
  }
  if (p) {
    teile.push(`<div><h4>Wahrscheinlichste Ergebnisse</h4><div class="chips">${p.top.map((t) => `<span class="chip">${t.e} · ${pct(t.p)}</span>`).join("")}</div>
      <div class="small muted" style="margin-top:6px">Erwartete Tore ${zahl(p.lam, 2)} : ${zahl(p.mu, 2)}</div></div>`);
    const markte = [["Über 2,5 Tore", p.ueber25], ["Beide treffen", p.beide_treffen],
      [`${h.kurz} zu Null`, p.zu_null?.[0]], [`${g.kurz} zu Null`, p.zu_null?.[1]]].filter(([, w]) => w != null);
    teile.push(`<div><h4>Tor-Wahrscheinlichkeiten</h4><div class="chips">${markte.map(([n, w]) => `<span class="chip">${esc(n)} · ${pct(w)}</span>`).join("")}</div></div>`);
    if (p.tipp_kicktipp) {
      const gleich = p.tipp_kicktipp[0] === p.tipp[0] && p.tipp_kicktipp[1] === p.tipp[1];
      teile.push(`<div><h4>Für Kicktipp</h4><div class="grund"><span>Bester Tipp <b>${p.tipp_kicktipp[0]}:${p.tipp_kicktipp[1]}</b>${gleich ? " (gleich dem Haupttipp)" : ""}</span><span class="wert">Ø ${zahl(p.kicktipp_erw, 2)} Pkt.</span></div>
        ${!gleich && p.kicktipp_erw_tipp != null ? `<div class="grund small muted"><span>Haupttipp ${p.tipp[0]}:${p.tipp[1]}</span><span class="wert">Ø ${zahl(p.kicktipp_erw_tipp, 2)} Pkt.</span></div>` : ""}
        <div class="small muted">Erwartete Punkte bei 4 / 3 / 2 für exakt / Tordifferenz / Tendenz (bei Remis keine Differenz-Punkte).</div></div>`);
    }
    let quellen = `<div class="grund"><span>Modell allein</span><span class="wert">${pct(p.p_modell[0])} / ${pct(p.p_modell[1])} / ${pct(p.p_modell[2])}</span></div>`;
    if (p.p_markt) quellen += `<div class="grund"><span>Buchmacher</span><span class="wert">${pct(p.p_markt[0])} / ${pct(p.p_markt[1])} / ${pct(p.p_markt[2])}</span></div>`;
    if (sp.ki?.p) quellen += `<div class="grund"><span>KI</span><span class="wert">${pct(sp.ki.p[0])} / ${pct(sp.ki.p[1])} / ${pct(sp.ki.p[2])}</span></div>`;
    teile.push(`<div><h4>Heim / Remis / Gast nach Quelle</h4>${quellen}</div>`);
    const gruende = (p.begruendung || []).map((g) => `<div class="grund"><span>${esc(g.text)}</span><span class="wert">${g.heimsieg_pp > 0 ? "+" : ""}${zahl(g.heimsieg_pp)} %-Pkt. Heimsieg</span></div>`).join("");
    const info = sp.info ? `<div class="small muted">Ruhetage vor dem Spiel: ${esc(h.kurz)} ${zahl(sp.info.ruhe_heim, 0)}, ${esc(g.kurz)} ${zahl(sp.info.ruhe_gast, 0)}</div>` : "";
    teile.push(`<div><h4>Begründung</h4>${gruende || `<div class="small">Reines Modell aus Angriffs- und Abwehrstärke, Heimvorteil und Liga-Niveau.</div>`}${info}</div>`);
  }
  if (sp.h2h) {
    if (sp.h2h.length) {
      const bilanz = [0, 0, 0];   // Siege Heimteam dieses Spiels, Remis, Siege Gastteam
      sp.h2h.forEach((x) => {
        const [a, b] = x.heim === sp.heim ? x.erg : [x.erg[1], x.erg[0]];
        bilanz[tendenz(a, b)]++;
      });
      const zeilen = sp.h2h.map((x) => `<div class="grund small"><span>${esc(x.datum.split("-").reverse().join("."))}${x.pokal ? " · Pokal" : ""} · ${esc(team(x.heim).kurz)} – ${esc(team(x.gast).kurz)}</span><span class="wert"><b>${x.erg[0]}:${x.erg[1]}</b></span></div>`).join("");
      teile.push(`<div><h4>Direkter Vergleich (letzte ${sp.h2h.length})</h4><div class="small" style="margin-bottom:4px">${esc(h.kurz)} ${bilanz[0]} ${bilanz[0] === 1 ? "Sieg" : "Siege"} · ${bilanz[1]} Remis · ${esc(g.kurz)} ${bilanz[2]} ${bilanz[2] === 1 ? "Sieg" : "Siege"}</div>${zeilen}</div>`);
    } else {
      teile.push(`<div><h4>Direkter Vergleich</h4><div class="small muted">Keine Duelle in den letzten Jahren.</div></div>`);
    }
  }
  const ausf = [[h, sp.heim], [g, sp.gast]].filter(([t]) => t.ausfaelle?.length);
  if (ausf.length) {
    teile.push(`<div><h4>Ausfälle (API-Football)</h4>${ausf.map(([t]) => `<div><b>${esc(t.kurz)}:</b> ${t.ausfaelle.map((a) => `${esc(a.name)}${a.grund ? ` (${esc(a.grund)})` : ""}`).join(", ")}</div>`).join("")}</div>`);
  }
  if (sp.ki) teile.push(kiBox(sp.ki, sp));

  const offen = ZUSTAND.offen.has(sp.id) ? " open" : "";
  return `<article class="card spiel" data-id="${sp.id}">
    <div class="kopf"><span class="small muted">${datum(sp.anstoss)}</span><span>${badges}</span></div>
    <div class="paarung">
      <div class="team">${logo(h)}<span>${esc(h.kurz)}</span></div>
      <div class="mitte">${mitte}</div>
      <div class="team gast"><span>${esc(g.kurz)}</span>${logo(g)}</div>
    </div>
    ${unten}
    ${teile.length ? `<details class="mehr"${offen}><summary>Details</summary><div class="mehr-inhalt">${teile.join("")}</div></details>` : ""}
  </article>`;
}

/* Auffällige Spiele eines Spieltags (nur noch nicht beendete mit Prognose) */
function highlights(spiele) {
  const offen = spiele.filter((s) => s.prognose && !s.beendet);
  if (offen.length < 3) return "";
  const name = (s) => `${team(s.heim).kurz} – ${team(s.gast).kurz}`;
  const elo = (s) => (team(s.heim).elo || 0) + (team(s.gast).elo || 0);
  const favorit = (s) => Math.max(s.prognose.p[0], s.prognose.p[2]);
  const karten = [];
  const top = offen.reduce((a, b) => (elo(b) > elo(a) ? b : a));
  karten.push(["Spitzenspiel", name(top), `Elo zusammen ${elo(top)}`, top.id]);
  const klar = offen.reduce((a, b) => (favorit(b) > favorit(a) ? b : a));
  const klarHeim = klar.prognose.p[0] >= klar.prognose.p[2];
  karten.push(["Klarster Favorit", team(klarHeim ? klar.heim : klar.gast).kurz, `${pct(favorit(klar))} Siegchance gegen ${team(klarHeim ? klar.gast : klar.heim).kurz}`, klar.id]);
  const knapp = offen.reduce((a, b) => (favorit(b) < favorit(a) ? b : a));
  karten.push(["Offenstes Spiel", name(knapp), `${pct(knapp.prognose.p[0])} / ${pct(knapp.prognose.p[1])} / ${pct(knapp.prognose.p[2])}`, knapp.id]);
  const mitMarkt = offen.filter((s) => s.prognose.p_markt);
  if (mitMarkt.length) {
    const abw = (s) => Math.max(...[0, 1, 2].map((k) => Math.abs(s.prognose.p_modell[k] - s.prognose.p_markt[k])));
    const x = mitMarkt.reduce((a, b) => (abw(b) > abw(a) ? b : a));
    if (abw(x) >= 0.05) {
      const k = [0, 1, 2].reduce((a, b) => (Math.abs(x.prognose.p_modell[b] - x.prognose.p_markt[b]) > Math.abs(x.prognose.p_modell[a] - x.prognose.p_markt[a]) ? b : a));
      const was = ["Heimsieg", "Remis", "Gastsieg"][k];
      karten.push(["Modell gegen Buchmacher", name(x), `${was}: Modell ${pct(x.prognose.p_modell[k])}, Buchmacher ${pct(x.prognose.p_markt[k])}`, x.id]);
    }
  }
  return `<div class="highlights">${karten.map(([t, n, z, id]) => `<button class="card highlight" data-spring="${id}"><div class="label">${esc(t)}</div><div class="name">${esc(n)}</div><div class="small muted">${esc(z)}</div></button>`).join("")}</div>`;
}

function zeichneSpieltag() {
  const d = daten();
  const st = ZUSTAND.spieltag[d.liga] ?? d.aktueller_spieltag;
  const spiele = d.spiele.filter((s) => s.st === st).sort((a, b) => a.anstoss.localeCompare(b.anstoss) || a.id - b.id);
  const live = spiele.some((s) => s.live);
  inhalt.innerHTML = `
    <div class="spieltag-nav">
      <button class="icon-btn" data-st="${st - 1}" ${st <= 1 ? "disabled" : ""} aria-label="Vorheriger Spieltag">‹ ${st - 1 >= 1 ? st - 1 : ""}</button>
      <h2>${st}. Spieltag ${live ? '<span class="badge live">Live</span>' : ""}</h2>
      <button class="icon-btn" data-st="${st + 1}" ${st >= d.spieltage ? "disabled" : ""} aria-label="Nächster Spieltag">${st + 1 <= d.spieltage ? st + 1 : ""} ›</button>
    </div>
    <div class="legende"><span><i style="background:var(--heim)"></i>Heimsieg</span><span><i style="background:var(--remis)"></i>Remis</span><span><i style="background:var(--gast)"></i>Gastsieg</span></div>
    ${highlights(spiele)}
    <div class="spiele">${spiele.map(spielKarte).join("") || '<div class="leer card">Keine Spiele.</div>'}</div>`;
  inhalt.querySelectorAll("[data-spring]").forEach((b) => b.addEventListener("click", () => {
    const karte = inhalt.querySelector(`.spiel[data-id="${b.dataset.spring}"]`);
    if (!karte) return;
    const det = karte.querySelector("details.mehr");
    if (det) det.open = true;
    karte.scrollIntoView({ behavior: "smooth", block: "start" });
  }));
  inhalt.querySelectorAll("[data-st]").forEach((b) => b.addEventListener("click", () => {
    ZUSTAND.spieltag[d.liga] = +b.dataset.st; zeichne();
  }));
  inhalt.querySelectorAll("details.mehr").forEach((det) => det.addEventListener("toggle", () => {
    const id = +det.closest(".spiel").dataset.id;
    det.open ? ZUSTAND.offen.add(id) : ZUSTAND.offen.delete(id);
  }));
}

function zonenKlasse(d, platz) {
  const z = d.zonen;
  const oben = new Set([...(z["Aufstieg"] || []), ...(z["Champions League"] || []), ...(z["Meister"] || [])]);
  const mitte = new Set([...(z["Relegation"] || []), ...(z["Relegation oben"] || []), ...(z["Relegation unten"] || [])]);
  const unten = new Set(z["Abstieg"] || []);
  if (unten.has(platz)) return "zone-unten";
  if (mitte.has(platz)) return "zone-mitte";
  if (oben.has(platz)) return "zone-oben";
  return "";
}
const heat = (p) => `background: rgba(var(--seq-rgb), ${Math.min(0.85, p * 0.9).toFixed(3)}); color: ${p > 0.55 ? "#fff" : "inherit"}`;

/* Restprogramm: Ø Elo der verbleibenden Gegner (höher = schwerer) */
function restprogramm(d) {
  const erg = {};
  Object.keys(d.teams).forEach((id) => { erg[id] = { n: 0, summe: 0 }; });
  for (const s of d.spiele) {
    if (s.beendet) continue;
    if (erg[s.heim]) { erg[s.heim].n++; erg[s.heim].summe += team(s.gast).elo || 0; }
    if (erg[s.gast]) { erg[s.gast].n++; erg[s.gast].summe += team(s.heim).elo || 0; }
  }
  const liste = Object.entries(erg).filter(([, v]) => v.n).map(([id, v]) => [id, v.summe / v.n]);
  liste.sort((a, b) => b[1] - a[1]);
  liste.forEach(([id, w], i) => { erg[id].schnitt = w; erg[id].rang = i + 1; });
  return erg;
}

/* Formtabelle: nur die letzten 5 beendeten Spiele jedes Teams */
function formtabelle(d, anzahl = 5) {
  const spiele = d.spiele.filter((s) => s.beendet && s.erg).sort((a, b) => b.anstoss.localeCompare(a.anstoss));
  return Object.keys(d.teams).map((id) => {
    id = +id;
    const letzte = spiele.filter((s) => s.heim === id || s.gast === id).slice(0, anzahl);
    const z = { id, sp: letzte.length, pkt: 0, tore: 0, gegentore: 0, form: [] };
    letzte.slice().reverse().forEach((s) => {
      const heim = s.heim === id, [a, b] = heim ? s.erg : [s.erg[1], s.erg[0]];
      z.tore += a; z.gegentore += b;
      const e = a > b ? "S" : a === b ? "U" : "N";
      z.pkt += { S: 3, U: 1, N: 0 }[e];
      z.form.push({ e, txt: `${s.st}. ST ${heim ? "gegen" : "bei"} ${team(heim ? s.gast : s.heim).kurz} ${a}:${b}` });
    });
    return z;
  }).sort((x, y) => y.pkt - x.pkt || (y.tore - y.gegentore) - (x.tore - x.gegentore) || y.tore - x.tore);
}

function zeichneTabelle() {
  const d = daten();
  const rest = restprogramm(d);
  const tab = tabelleJetzt(d);
  const liveAktiv = d.spiele.some((s) => s.live);
  const sim = ZUSTAND.liveSim[d.liga] || d.simulation;
  const simLive = !!ZUSTAND.liveSim[d.liga] && liveAktiv;
  const zonen = Object.keys(d.zonen);
  const T = Object.keys(d.teams).length;
  const sortiert = Object.keys(sim).map(Number).sort((a, b) => sim[a].platz_schnitt - sim[b].platz_schnitt);

  const zeilenJetzt = tab.map((z) => `<tr class="${zonenKlasse(d, z.platz)}">
    <td>${z.platz}</td><td class="l teamzelle">${logo(team(z.id))}<span>${esc(team(z.id).kurz)}</span>${z.live ? ' <span class="badge live">Live</span>' : ""}</td>
    <td>${z.sp}</td><td>${z.s}</td><td>${z.u}</td><td>${z.n}</td><td>${z.tore}:${z.gegentore}</td><td>${z.tore - z.gegentore > 0 ? "+" : ""}${z.tore - z.gegentore}</td><td><b>${z.pkt}</b></td></tr>`).join("");

  const zeilenProg = sortiert.map((id, i) => {
    const s = sim[id];
    const spanne = s.punkte_p10 != null ? `<span class="muted small"> (${s.punkte_p10}–${s.punkte_p90})</span>` : "";
    const verteilung = `<div class="pos-heat" style="grid-template-columns: repeat(${T}, 1fr)" title="Platzverteilung 1 bis ${T}">${s.plaetze.map((p, r) => `<div style="${heat(Math.min(1, p * 2.2))}" data-tip="Platz ${r + 1}: ${pct(p, 1)}"></div>`).join("")}</div>`;
    return `<tr class="${zonenKlasse(d, i + 1)}"><td>${i + 1}</td>
      <td class="l teamzelle">${logo(team(id))}<span>${esc(team(id).kurz)}</span></td>
      <td>${zahl(s.punkte_schnitt, 1)}${spanne}</td>
      ${zonen.map((z) => `<td class="heat" style="${heat(s.zonen[z])}">${s.zonen[z] < 0.001 ? "–" : s.zonen[z] > 0.999 ? "✓" : pct(s.zonen[z], s.zonen[z] < 0.1 ? 1 : 0)}</td>`).join("")}
      ${restZelle(id)}
      <td class="l">${verteilung}</td></tr>`;
  }).join("");

  function restZelle(id) {
    const r = rest[id];
    if (!r?.n) return `<td class="muted">–</td>`;
    const schwer = r.rang <= Math.ceil(T / 4), leicht = r.rang > T - Math.ceil(T / 4);
    return `<td data-tip="${esc(`${r.n} Spiele, ${r.rang}. schwerstes Restprogramm von ${T}`)}">${Math.round(r.schnitt)}${schwer ? ' <span class="badge falsch">schwer</span>' : leicht ? ' <span class="badge ok">leicht</span>' : ""}</td>`;
  }

  const form = formtabelle(d);
  const zeilenForm = form.map((z, i) => `<tr><td>${i + 1}</td>
    <td class="l teamzelle">${logo(team(z.id))}<span>${esc(team(z.id).kurz)}</span></td>
    <td class="l"><div class="form" style="margin:0">${z.form.map((f) => `<span class="${f.e}" data-tip="${esc(f.txt)}">${f.e}</span>`).join("")}</div></td>
    <td>${z.tore}:${z.gegentore}</td><td><b>${z.pkt}</b></td></tr>`).join("");

  inhalt.innerHTML = `
    <div class="abschnitt"><h3>Tabellenprognose ${simLive ? '<span class="badge live">Live</span>' : ""}</h3>
      <span class="small muted">${simLive ? `Live neu simuliert (${LIVE_SIMULATIONEN.toLocaleString("de")} Durchläufe), ${ZUSTAND.liveStand.toLocaleTimeString("de")}` : `10.000 simulierte Restsaisons, Stand ${datum(d.stand)}`}</span></div>
    <p class="hinweis">Wahrscheinlichkeit für jede Zone am Saisonende. Rechts die Verteilung über alle Plätze (links = Platz 1); dunkler = wahrscheinlicher.</p>
    <div class="card tabelle-wrap"><table class="t">
      <thead><tr><th>#</th><th class="l">Team</th><th>Ø Pkt.</th>${zonen.map((z) => `<th>${esc(z)}</th>`).join("")}<th title="Durchschnittliche Elo der restlichen Gegner">Restprogramm</th><th class="l">Plätze 1–${T}</th></tr></thead>
      <tbody>${zeilenProg}</tbody></table></div>
    <p class="hinweis small" style="margin-top:6px">Restprogramm: durchschnittlicher Elo-Wert der verbleibenden Gegner. „schwer“ / „leicht“ = oberstes / unterstes Viertel der Liga.</p>
    <div class="abschnitt"><h3>Aktuelle Tabelle ${liveAktiv ? '<span class="badge live">Live</span>' : ""}</h3>
      <span class="small muted">${liveAktiv ? "laufende Spiele mit aktuellem Stand" : ""}</span></div>
    <div class="card tabelle-wrap"><table class="t">
      <thead><tr><th>#</th><th class="l">Team</th><th>Sp</th><th>S</th><th>U</th><th>N</th><th>Tore</th><th>Diff</th><th>Pkt</th></tr></thead>
      <tbody>${zeilenJetzt}</tbody></table></div>
    <div class="abschnitt"><h3>Formtabelle</h3><span class="small muted">nur die letzten 5 Spiele</span></div>
    <div class="card tabelle-wrap"><table class="t">
      <thead><tr><th>#</th><th class="l">Team</th><th class="l">Form</th><th>Tore</th><th>Pkt</th></tr></thead>
      <tbody>${zeilenForm}</tbody></table></div>`;
  bindeTooltips(inhalt);
}

function eloChart(t) {
  const v = t.elo_verlauf;
  if (!v.length) return '<div class="muted small">Noch keine Spiele in dieser Saison.</div>';
  const W = 600, H = 220, L = 44, R = 10, O = 12, U = 26;
  const werte = v.map((x) => x.elo);
  let min = Math.min(...werte), max = Math.max(...werte);
  const pad = Math.max(15, (max - min) * 0.15); min -= pad; max += pad;
  const x = (i) => L + (v.length === 1 ? (W - L - R) / 2 : (i / (v.length - 1)) * (W - L - R));
  const y = (e) => O + (1 - (e - min) / (max - min)) * (H - O - U);
  const ticks = []; const schritt = Math.max(10, Math.round((max - min) / 4 / 10) * 10);
  for (let e = Math.ceil(min / schritt) * schritt; e <= max; e += schritt) ticks.push(e);
  const pfad = v.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.elo).toFixed(1)}`).join("");
  return `<div class="chart-box"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Elo-Verlauf von ${esc(t.name)}">
    ${ticks.map((e) => `<line x1="${L}" x2="${W - R}" y1="${y(e)}" y2="${y(e)}" stroke="var(--border)" stroke-width="1"/><text x="${L - 6}" y="${y(e) + 4}" text-anchor="end" font-size="11" fill="var(--text-3)">${e}</text>`).join("")}
    ${v.map((p, i) => (i % Math.ceil(v.length / 10) === 0 || i === v.length - 1) ? `<text x="${x(i)}" y="${H - 6}" text-anchor="middle" font-size="11" fill="var(--text-3)">${p.st}</text>` : "").join("")}
    <path d="${pfad}" fill="none" stroke="var(--heim)" stroke-width="2" stroke-linejoin="round"/>
    ${v.map((p, i) => `<circle cx="${x(i)}" cy="${y(p.elo)}" r="4" fill="var(--heim)" stroke="var(--surface)" stroke-width="2"/>
      <rect x="${x(i) - (W - L - R) / Math.max(v.length, 1) / 2}" y="${O}" width="${(W - L - R) / Math.max(v.length, 1)}" height="${H - O - U}" fill="transparent"
        data-tip="${esc(`${p.st}. Spieltag: ${p.heim ? "gegen" : "bei"} ${team(p.geg).kurz} ${p.tore} (${p.e}) · Elo ${Math.round(p.elo)}`)}"/>`).join("")}
  </svg></div><div class="small muted">Elo-Wert nach jedem Spiel (x-Achse: Spieltag). Fahre über die Punkte für Details.</div>`;
}

function zeichneTeams() {
  const d = daten();
  const ids = Object.keys(d.teams).map(Number).sort((a, b) => d.teams[b].elo - d.teams[a].elo);
  const form = (t) => t.elo_verlauf.slice(-5).map((v) => `<span class="${v.e}" title="${esc(`${v.st}. ST ${v.tore}`)}">${v.e}</span>`).join("");
  let detail = "";
  if (ZUSTAND.team && d.teams[ZUSTAND.team]) {
    const t = d.teams[ZUSTAND.team];
    const relA = t.angriff - d.liga_staerke.angriff, relD = t.abwehr - d.liga_staerke.abwehr;
    const proz = (x) => `${x >= 0 ? "+" : ""}${Math.round((Math.exp(x) - 1) * 100)} %`;
    const offen = d.spiele.filter((s) => !s.beendet && (s.heim === ZUSTAND.team || s.gast === ZUSTAND.team)).slice(0, 5);
    detail = `<section class="card detail">
      <div class="zeile">${logo(t)}<h3>${esc(t.name)}</h3><button class="icon-btn" id="team-zu">Schließen</button></div>
      <div class="staerke"><span>Elo <b>${t.elo}</b></span><span>Angriff ${proz(relA)} vs. Liga-Schnitt</span><span>Abwehr ${proz(relD)} (weniger Gegentore)</span></div>
      ${eloChart(t)}
      ${t.ausfaelle?.length ? `<div style="margin-top:10px"><b>Ausfälle:</b> ${t.ausfaelle.map((a) => esc(a.name)).join(", ")}</div>` : ""}
      ${offen.length ? `<div style="margin-top:12px"><b>Nächste Spiele</b>${offen.map((s) => {
        const heim = s.heim === ZUSTAND.team, geg = team(heim ? s.gast : s.heim);
        const p = s.prognose?.p; const eigen = p ? (heim ? p[0] : p[2]) : null;
        return `<div class="grund small"><span>${datum(s.anstoss)} · ${heim ? "gegen" : "bei"} ${esc(geg.kurz)}</span><span class="wert">Siegchance ${pct(eigen)}</span></div>`;
      }).join("")}</div>` : ""}
    </section>`;
  }
  inhalt.innerHTML = `${detail}
    <p class="hinweis">Sortiert nach Elo-Wert (gemeinsame Skala über alle drei Ligen; Bundesliga-Schnitt etwa 1500). Klick auf ein Team für den Verlauf.</p>
    <div class="team-grid">${ids.map((id) => {
      const t = d.teams[id];
      return `<button class="card team-karte" data-team="${id}"><div class="zeile">${logo(t)}<span class="name">${esc(t.kurz)}</span><span class="elo">${t.elo}</span></div>
        <div class="form">${form(t)}</div></button>`;
    }).join("")}</div>`;
  inhalt.querySelectorAll("[data-team]").forEach((b) => b.addEventListener("click", () => {
    ZUSTAND.team = +b.dataset.team; zeichne(); window.scrollTo({ top: 0, behavior: "smooth" });
  }));
  $("#team-zu")?.addEventListener("click", () => { ZUSTAND.team = null; zeichne(); });
  bindeTooltips(inhalt);
}

function kennzahlTabelle(zeilen) {
  const spalten = [["n", "Spiele", (x) => x], ["tendenz", "Tendenz", (x) => pct(x, 1)], ["differenz", "Tordiff.", (x) => pct(x, 1)],
    ["exakt", "Exakt", (x) => pct(x, 1)], ["punkte_schnitt", "Ø Punkte", (x) => zahl(x, 2)], ["brier", "Brier", (x) => zahl(x, 3)]];
  return `<div class="card tabelle-wrap"><table class="t"><thead><tr><th class="l">Variante</th>${spalten.map((s) => `<th>${s[1]}</th>`).join("")}</tr></thead>
    <tbody>${zeilen.filter(([, k]) => k && k.n).map(([name, k]) => `<tr><td class="l">${name}</td>${spalten.map(([f, , fn]) => `<td>${k[f] == null ? "–" : fn(k[f])}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
}

/* Horizontale Balken: eine Kennzahl, mehrere Tipper. Grundregel grau als Messlatte. */
function vergleichBalken(zeilen, feld, format, max) {
  const gueltig = zeilen.filter(([, k]) => k && k.n && k[feld] != null);
  if (!gueltig.length) return "";
  const oben = max ?? Math.max(...gueltig.map(([, k]) => k[feld])) * 1.08;
  return `<div class="vbalken">${gueltig.map(([name, k, art]) => `
    <div class="vb-zeile" data-tip="${esc(`${name}: Tendenz ${pct(k.tendenz, 1)} · Tordifferenz ${pct(k.differenz, 1)} · exakt ${pct(k.exakt, 1)} · Ø ${zahl(k.punkte_schnitt, 2)} Pkt. · ${k.n} Spiele`)}">
      <span class="vb-name">${esc(name)}</span>
      <span class="vb-spur"><span class="vb-wert ${art || ""}" style="width:${(k[feld] / oben * 100).toFixed(1)}%"></span></span>
      <span class="vb-zahl">${format(k[feld])}</span>
    </div>`).join("")}</div>`;
}

function zeichneTreffer() {
  const d = daten();
  const tq = ZUSTAND.treffer, bt = ZUSTAND.backtest;
  const tl = tq?.je_liga?.[d.liga] || {};
  const bl = bt?.je_liga?.[d.liga];
  const echt = tl.endprognose?.n ? tl : null;
  const saisons = bt ? bt.saisons.map((s) => `${s}/${String((s + 1) % 100).padStart(2, "0")}`).join(", ") : "";

  // 1) Auf einen Blick: echte Prognosen, sobald genug Spiele da sind, sonst Rückwärtstest
  const basisEcht = echt && echt.endprognose.n >= 30;
  const haupt = basisEcht ? echt.endprognose : bl?.modell;
  const regel = basisEcht ? echt.grundregel : bl?.grundregel;
  let blick = "";
  if (haupt?.n) {
    const vorsprung = regel ? Math.round((haupt.tendenz - regel.tendenz) * 100) : null;
    const jedes = haupt.exakt > 0 ? Math.round(1 / haupt.exakt) : null;
    blick = `<div class="kpis">
      <div class="card kpi"><div class="label">Tendenz richtig</div><div class="wert">${pct(haupt.tendenz)}</div>
        <div class="vergleich">${vorsprung != null ? `${vorsprung >= 0 ? "+" : ""}${vorsprung} ${Math.abs(vorsprung) === 1 ? "Prozentpunkt" : "Prozentpunkte"} gegenüber „immer 2:1“` : ""}</div></div>
      <div class="card kpi"><div class="label">Kicktipp-Punkte pro Spiel</div><div class="wert">${zahl(haupt.punkte_schnitt, 2)}</div>
        <div class="vergleich">${regel ? `immer 2:1 tippen: ${zahl(regel.punkte_schnitt, 2)}` : ""}</div></div>
      <div class="card kpi"><div class="label">Ergebnis exakt</div><div class="wert">${pct(haupt.exakt)}</div>
        <div class="vergleich">${jedes ? `etwa jedes ${jedes}. Spiel` : ""}</div></div>
    </div>
    <p class="small muted" style="margin:8px 0 0">Grundlage: ${basisEcht ? `${haupt.n} echte, vor Anpfiff gespeicherte Prognosen` : `Rückwärtstest über ${haupt.n} Spiele (${saisons})`}.</p>`;
  }

  // 2) Wer tippt am besten?
  const tipper = (k) => [
    ["Angezeigter Tipp", k.endprognose || k.modell, "haupt"],
    ["Kicktipp-Tipp", k.kicktipp],
    ["Tipprunde-Tipp", k.tipprunde],
    ["Rechenmodell allein", k.endprognose ? k.modell : k.nur_modell_ohne_einfluesse],
    ["Buchmacher", k.markt || k.nur_markt],
    ["KI-Tipp", k.ki],
    ["Immer 2:1", k.grundregel, "regel"],
  ];
  const vergleich = (k) => `
    <div class="zwei-spalten">
      <div class="card" style="padding:12px 14px"><h4 class="mini">Tendenz richtig</h4>${vergleichBalken(tipper(k), "tendenz", (x) => pct(x, 1), 1)}</div>
      <div class="card" style="padding:12px 14px"><h4 class="mini">Kicktipp-Punkte pro Spiel</h4>${vergleichBalken(tipper(k), "punkte_schnitt", (x) => zahl(x, 2), 4)}</div>
    </div>`;

  // 3) Letzte echte Spiele dieser Liga
  const letzte = (tq?.letzte || []).filter((x) => x.liga === d.liga).slice(0, 12);
  const urteil = (t, e) => {
    if (!t) return '<span class="muted">–</span>';
    const ok = tendenz(t[0], t[1]) === tendenz(e[0], e[1]), exakt = t[0] === e[0] && t[1] === e[1];
    const diff = ok && t[0] !== t[1] && t[0] - t[1] === e[0] - e[1];   // bei Remis keine Differenz-Punkte
    const kl = exakt ? "exakt" : diff ? "diff" : ok ? "ok" : "falsch";
    return `<span class="tipp-urteil ${kl}" title="${exakt ? "exakt (4 Pkt.)" : diff ? "Tordifferenz (3 Pkt.)" : ok ? "Tendenz (2 Pkt.)" : "falsch (0 Pkt.)"}">${t[0]}:${t[1]}</span>`;
  };
  const letzteHtml = letzte.length ? `<div class="card tabelle-wrap"><table class="t">
      <thead><tr><th>ST</th><th class="l">Spiel</th><th>Ergebnis</th><th>Tipp</th><th>KI</th></tr></thead>
      <tbody>${letzte.map((x) => `<tr><td>${x.spieltag}</td><td class="l">${esc(team(x.heim).kurz)} – ${esc(team(x.gast).kurz)}</td>
        <td><b>${x.ergebnis[0]}:${x.ergebnis[1]}</b></td><td>${urteil(x.tipps.endprognose, x.ergebnis)}</td><td>${urteil(x.tipps.ki, x.ergebnis)}</td></tr>`).join("")}</tbody></table></div>
      <div class="legende" style="margin-top:8px"><span><i class="tu exakt"></i>exakt</span><span><i class="tu diff"></i>Tordifferenz</span><span><i class="tu ok"></i>Tendenz</span><span><i class="tu falsch"></i>falsch</span></div>` : "";

  // 4) Ligen im Vergleich (Rückwärtstest)
  const ligenHtml = bt ? `<div class="card tabelle-wrap"><table class="t">
      <thead><tr><th class="l">Liga</th><th>Tendenz</th><th>Ø Punkte</th><th>Exakt</th><th>Tendenz bei „immer 2:1“</th></tr></thead>
      <tbody>${ZUSTAND.index.ligen.map((l) => { const k = bt.je_liga[l.id]; if (!k) return ""; return `<tr class="${l.id === d.liga ? "aktiv" : ""}">
        <td class="l">${esc(l.name)}</td><td>${pct(k.modell.tendenz, 1)}</td><td>${zahl(k.modell.punkte_schnitt, 2)}</td><td>${pct(k.modell.exakt, 1)}</td><td class="muted">${pct(k.grundregel.tendenz, 1)}</td></tr>`; }).join("")}</tbody></table></div>` : "";

  // 5) Technik, eingeklappt
  let technik = "";
  if (bt) {
    const e = bt.einfluesse;
    const NAMEN_E = { ruhetage: "Ruhetage", nichts_eigen: "„nichts mehr zu spielen“ (eigene Tore)", nichts_gegner: "„nichts mehr zu spielen“ (Gegentore)" };
    const getestet = Object.entries(e.getestet).sort((a, b) => a[1] - b[1]).map(([k, v]) => `<div class="grund small"><span>${k === "ohne" ? "ohne Zusatz-Einflüsse" : k.split("+").map((x) => NAMEN_E[x] || x).join(" + ")}</span><span class="wert">${zahl(v, 4)}</span></div>`).join("");
    const gew = Object.entries(e.quoten_gewichte).map(([k, v]) => `<div class="grund small"><span>${Math.round(k * 100)} % Quoten${+k === e.quoten_gewicht ? " ✓" : ""}</span><span class="wert">${zahl(v, 4)}</span></div>`).join("");
    technik = `
      <details class="mehr card technik"><summary>Alle Kennzahlen des Rückwärtstests</summary><div class="mehr-inhalt">
        ${kennzahlTabelle([["Angezeigter Tipp", bl?.modell], ["Kicktipp-Tipp", bl?.kicktipp], ["Tipprunde-Tipp", bl?.tipprunde], ["Modell ohne Einflüsse/Quoten", bl?.nur_modell_ohne_einfluesse], ["Nur Buchmacher", bl?.nur_markt], ["Immer 2:1", bl?.grundregel]])}
        <div class="small muted">Tordiff. = Tendenz und Tordifferenz richtig. Brier = Güte der Prozentangaben, 0 = perfekt, kleiner = besser.</div></div></details>
      <details class="mehr card technik"><summary>Wie sich das Modell selbst einstellt</summary><div class="mehr-inhalt">
        <div class="small">Jede Woche prüft der Rückwärtstest, welche Zusatz-Einflüsse und wie viel Buchmacher-Anteil die Prognosen verbessern. Übernommen wird nur, was den Log-Loss um mindestens 0,001 senkt (kleiner = besser). Stand ${esc(datum(bt.stand))}.</div>
        <div><h4>Spielplan-Einflüsse</h4>${getestet}<div class="small" style="margin-top:4px">Aktiv: <b>${e.aktiv.length ? e.aktiv.map((x) => NAMEN_E[x] || x).join(", ") : "keine"}</b></div></div>
        <div><h4>Anteil Buchmacherquoten (${e.spiele_mit_quoten} Spiele, 1./2. BL)</h4>${gew}<div class="small" style="margin-top:4px">Gewählt: <b>${Math.round(e.quoten_gewicht * 100)} %</b> (höchstens 50 %)</div></div>
      </div></details>`;
  }

  inhalt.innerHTML = `
    <div class="abschnitt"><h3>Auf einen Blick</h3><span class="small muted">${esc(d.name)}</span></div>
    ${blick || '<div class="card leer">Noch keine Auswertung vorhanden.</div>'}

    <div class="abschnitt"><h3>Echte Prognosen</h3><span class="small muted">vor Anpfiff eingefroren</span></div>
    ${echt ? vergleich(echt) + (letzteHtml ? `<h4 class="mini" style="margin:14px 0 6px">Letzte Spiele</h4>${letzteHtml}` : "")
      : `<div class="card hinweis-karte">Noch keine abgeschlossenen Spiele mit gespeicherter Prognose. Ab jetzt wird vor jedem Anpfiff festgehalten, was das Tool tippt – hier erscheint dann Spiel für Spiel, was getroffen wurde.</div>`}

    ${bl ? `<div class="abschnitt"><h3>Rückwärtstest</h3><span class="small muted">${saisons}, nur mit dem Wissen vor jedem Spiel</span></div>
    ${vergleich(bl)}
    <div class="abschnitt"><h3>Ligen im Vergleich</h3><span class="small muted">angezeigter Tipp im Rückwärtstest</span></div>
    ${ligenHtml}` : ""}

    <div style="display:grid;gap:10px;margin-top:22px">${technik}</div>
    <p class="small muted" style="margin-top:12px">Kicktipp-Punkte: 4 exakt, 3 richtige Tordifferenz (nicht bei Remis), 2 richtige Tendenz. Fahre über einen Balken für alle Werte.</p>`;
  bindeTooltips(inhalt);
}

/* ---------- Tipprunde: Punkteregel, Tippzettel, Bonusfragen ---------- */
const REGEL_VORLAGEN = {
  kicktipp: { name: "Kicktipp (4 / 3 / 2)", exakt: 4, diff: 3, tendenz: 2, remisDiff: false },
  klassisch: { name: "Klassisch (3 / 2 / 1)", exakt: 3, diff: 2, tendenz: 1, remisDiff: true },
  einfach: { name: "Einfach (3 exakt / 1 Tendenz)", exakt: 3, diff: 1, tendenz: 1, remisDiff: true },
};
function ladeRegel() {
  try {
    const r = JSON.parse(localStorage.getItem("fp-regel") || "null");
    if (r && [r.exakt, r.diff, r.tendenz].every((x) => Number.isFinite(x))) {
      // gespeicherte Vorlage immer auf den aktuellen Stand bringen
      return REGEL_VORLAGEN[r.vorlage] ? { ...r, ...REGEL_VORLAGEN[r.vorlage] } : r;
    }
  } catch { /* egal */ }
  return { ...REGEL_VORLAGEN.kicktipp, vorlage: "kicktipp", modus: "punkte" };
}
function speichereRegel(r) { try { localStorage.setItem("fp-regel", JSON.stringify(r)); } catch { /* egal */ } }

function punkteFuer(a, b, i, j, r) {
  if (a === i && b === j) return r.exakt;
  if (tendenz(a, b) !== tendenz(i, j)) return 0;
  if (a - b === i - j && (a !== b || r.remisDiff)) return r.diff;
  return r.tendenz;
}

/* Bester Tipp für eine Punkteregel. modus "punkte": Buchmacher voll nutzen (wenn vorhanden). */
function besterTipp(sp, r) {
  const p = sp.prognose;
  const markt = r.modus === "punkte" && p.p_markt;
  const ziel = markt ? p.p_markt : p.p;
  const m = passeAn(matrix(p.lam, p.mu, p.rho, 10), ziel);
  let best = null;
  for (let a = 0; a <= 5; a++) {
    for (let b = 0; b <= 5; b++) {
      let e = 0;
      for (let i = 0; i < m.length; i++) for (let j = 0; j < m.length; j++) e += m[i][j] * punkteFuer(a, b, i, j, r);
      if (!best || e > best.erw + 1e-12) best = { tipp: [a, b], erw: e };
    }
  }
  best.p = ziel;
  best.quelle = markt ? "Buchmacher" : "Modell";
  best.sicher = ziel[tendenz(best.tipp[0], best.tipp[1])];
  return best;
}

function dauerBis(ms) {
  const min = Math.round(ms / 60000);
  if (min < 60) return `${min} Min.`;
  const std = Math.floor(min / 60);
  if (std < 48) return `${std} Std. ${min % 60} Min.`;
  return `${Math.floor(std / 24)} Tagen ${std % 24} Std.`;
}

async function kopiere(text, knopf) {
  let ok = false;
  try { await navigator.clipboard.writeText(text); ok = true; } catch {
    const ta = document.createElement("textarea");
    ta.value = text; document.body.appendChild(ta); ta.select();
    try { ok = document.execCommand("copy"); } catch { /* egal */ }
    ta.remove();
  }
  const alt = knopf.textContent;
  knopf.textContent = ok ? "Kopiert ✓" : "Kopieren nicht möglich";
  setTimeout(() => { knopf.textContent = alt; }, 1800);
}

function bonusfragen(d) {
  const sim = ZUSTAND.liveSim[d.liga] || d.simulation;
  const karten = Object.entries(d.zonen).map(([zone, plaetze]) => {
    const anzahl = zone === "Meister" ? 1 : plaetze.length;
    const liste = Object.keys(sim).map((id) => [id, sim[id].zonen[zone] || 0]).sort((a, b) => b[1] - a[1]);
    const tipp = liste.slice(0, anzahl);
    return `<div class="card bonus"><div class="label">${esc(zone)}${anzahl > 1 ? ` <span class="muted">(${anzahl} Teams)</span>` : ""}</div>
      ${tipp.map(([id, w]) => `<div class="grund"><span class="teamzelle-klein">${logo(team(id))}<b>${esc(team(id).kurz)}</b></span><span class="wert">${pct(w, w < 0.1 ? 1 : 0)}</span></div>`).join("")}
</div>`;
  });
  karten.push(`<div class="card bonus" id="torjaeger"><div class="label">Torschützenkönig</div><div class="small muted">lädt …</div></div>`);
  return `<div class="bonus-grid">${karten.join("")}</div>`;
}

async function ladeTorjaeger(d) {
  const ziel = document.getElementById("torjaeger");
  if (!ziel) return;
  try {
    const liste = (ZUSTAND.torjaeger ||= {})[d.liga] ||= await ladeJson(`${API}/getgoalgetters/${d.liga}/${d.saison}`);
    const gespielt = Math.max(1, d.spiele.filter((s) => s.beendet).length / (Object.keys(d.teams).length / 2));
    const faktor = d.spieltage / gespielt;
    const top = liste.slice().sort((a, b) => b.goalCount - a.goalCount).slice(0, 3);
    if (!document.body.contains(ziel)) return;
    ziel.innerHTML = `<div class="label">Torschützenkönig</div>
      ${top.map((t) => `<div class="grund"><span><b>${esc(t.goalGetterName)}</b></span><span class="wert">${t.goalCount} Tore</span></div>`).join("")}
      <div class="small muted" style="margin-top:4px">Hochrechnung für ${esc(top[0]?.goalGetterName || "")}: ~${Math.round((top[0]?.goalCount || 0) * faktor)} Tore</div>`;
  } catch {
    ziel.innerHTML = `<div class="label">Torschützenkönig</div><div class="small muted">Torschützen gerade nicht abrufbar.</div>`;
  }
}

function zeichneTipprunde() {
  const d = daten();
  const r = ladeRegel();
  // Standard: erster Spieltag, in dem noch ein Spiel nicht angepfiffen ist
  const ersterOffener = Math.min(...d.spiele.filter((s) => s.prognose && new Date(s.anstoss) > Date.now()).map((s) => s.st), d.spieltage);
  const st = (ZUSTAND.tippSt ||= {})[d.liga] ?? ersterOffener;
  const spiele = d.spiele.filter((s) => s.st === st).sort((a, b) => a.anstoss.localeCompare(b.anstoss) || a.id - b.id);
  const offen = spiele.filter((s) => s.prognose && !s.beendet && !s.live);
  const tipps = new Map(offen.map((s) => [s.id, besterTipp(s, r)]));
  const summe = [...tipps.values()].reduce((x, t) => x + t.erw, 0);
  const mitMarkt = [...tipps.values()].filter((t) => t.quelle === "Buchmacher").length;
  const naechster = offen.map((s) => new Date(s.anstoss).getTime()).filter((t) => t > Date.now()).sort((a, b) => a - b)[0];

  const zeilen = offen.map((s) => {
    const t = tipps.get(s.id);
    return `<div class="tz-zeile" data-tip="${esc(`Heim ${pct(t.p[0])} · Remis ${pct(t.p[1])} · Gast ${pct(t.p[2])} (${t.quelle}) · im Schnitt ${zahl(t.erw, 2)} Punkte`)}">
      <span class="tz-zeit">${datum(s.anstoss)}</span>
      <span class="tz-paar"><span class="tz-team">${logo(team(s.heim))}${esc(team(s.heim).kurz)}</span><span class="muted">–</span><span class="tz-team">${logo(team(s.gast))}${esc(team(s.gast).kurz)}</span></span>
      <span class="tz-tipp">${t.tipp[0]}:${t.tipp[1]}</span></div>`;
  }).join("");
  const schonGespielt = spiele.length - offen.length;

  const text = `${d.name} – ${st}. Spieltag\n` + offen.map((s) => `${team(s.heim).kurz} – ${team(s.gast).kurz} ${tipps.get(s.id).tipp.join(":")}`).join("\n");
  const vorlageOpt = Object.entries(REGEL_VORLAGEN).map(([k, v]) => `<option value="${k}" ${r.vorlage === k ? "selected" : ""}>${esc(v.name)}</option>`).join("") +
    `<option value="eigen" ${r.vorlage === "eigen" ? "selected" : ""}>Eigene Regel</option>`;
  const regelName = r.vorlage !== "eigen" && REGEL_VORLAGEN[r.vorlage] ? REGEL_VORLAGEN[r.vorlage].name : `Eigene (${r.exakt} / ${r.diff} / ${r.tendenz})`;

  inhalt.innerHTML = `
    <div class="spieltag-nav">
      <button class="icon-btn" data-st="${st - 1}" ${st <= 1 ? "disabled" : ""} aria-label="Vorheriger Spieltag">‹ ${st - 1 >= 1 ? st - 1 : ""}</button>
      <h2>${st}. Spieltag</h2>
      <button class="icon-btn" data-st="${st + 1}" ${st >= d.spieltage ? "disabled" : ""} aria-label="Nächster Spieltag">${st + 1 <= d.spieltage ? st + 1 : ""} ›</button>
    </div>
    ${offen.length ? `
    <div class="tz-kopf">
      <div><div class="tz-schluss">Tippschluss ${naechster ? `in ${dauerBis(naechster - Date.now())}` : ""}</div>
        <div class="small muted">${naechster ? `erster Anpfiff ${datum(new Date(naechster).toISOString())}` : ""}</div></div>
      <button class="knopf" id="tz-kopieren">Tipps kopieren</button>
    </div>
    <div class="card tz-liste">${zeilen}</div>
    <p class="small muted tz-fuss">${schonGespielt ? `${schonGespielt} ${schonGespielt === 1 ? "Spiel" : "Spiele"} schon angepfiffen, nicht aufgeführt. ` : ""}Erwartet: ${zahl(summe, 1)} Punkte.${mitMarkt < offen.length ? ` Quoten bei ${mitMarkt} von ${offen.length} Spielen.` : ""}</p>`
    : `<div class="card leer">Alle Spiele dieses Spieltags sind schon angepfiffen.</div>`}

    <details class="mehr card regel"><summary>Punkteregel: <b>${esc(regelName)}</b> · ändern</summary>
      <div class="regel-inhalt">
        <div class="regel-zeile">
          <label>Vorlage <select id="r-vorlage">${vorlageOpt}</select></label>
          <label>Exakt <input type="number" min="0" max="20" id="r-exakt" value="${r.exakt}"></label>
          <label>Tordifferenz <input type="number" min="0" max="20" id="r-diff" value="${r.diff}"></label>
          <label>Tendenz <input type="number" min="0" max="20" id="r-tendenz" value="${r.tendenz}"></label>
        </div>
        <label class="haken"><input type="checkbox" id="r-remis" ${r.remisDiff ? "checked" : ""}> Tordifferenz-Punkte auch bei Remis</label>
        <label class="haken"><input type="checkbox" id="r-modus" ${r.modus !== "anzeige" ? "checked" : ""}> Buchmacherquoten voll nutzen (bringt im Rückwärtstest die meisten Punkte)</label>
        <div class="small muted">Die Tipps werden für genau diese Regel berechnet. Die Einstellung bleibt in diesem Browser gespeichert.</div>
      </div>
    </details>

    <div class="abschnitt"><h3>Bonusfragen</h3><span class="small muted">Saisonende, aus 10.000 Simulationen</span></div>
    ${bonusfragen(d)}`;

  // Bedienung
  const neu = (aenderung) => {
    const r2 = { ...ladeRegel(), ...aenderung };
    // passt die Regel zu einer Vorlage, diese anzeigen
    const treffer = Object.entries(REGEL_VORLAGEN).find(([, v]) => v.exakt === r2.exakt && v.diff === r2.diff && v.tendenz === r2.tendenz && v.remisDiff === r2.remisDiff);
    if (aenderung.vorlage !== "eigen" || treffer) r2.vorlage = treffer ? treffer[0] : "eigen";
    speichereRegel(r2); ZUSTAND.regelOffen = true; zeichne();
  };
  $("#r-vorlage").addEventListener("change", (e) => {
    const v = e.target.value;
    neu(v === "eigen" ? { vorlage: "eigen" } : { ...REGEL_VORLAGEN[v], vorlage: v });
  });
  [["#r-exakt", "exakt"], ["#r-diff", "diff"], ["#r-tendenz", "tendenz"]].forEach(([sel, feld]) => $(sel).addEventListener("change", (e) => {
    const w = Math.max(0, Math.min(20, Math.round(+e.target.value || 0)));
    neu({ [feld]: w, vorlage: "eigen" });
  }));
  $("#r-remis").addEventListener("change", (e) => neu({ remisDiff: e.target.checked, vorlage: "eigen" }));
  $("#r-modus").addEventListener("change", (e) => neu({ modus: e.target.checked ? "punkte" : "anzeige" }));
  const det = inhalt.querySelector("details.regel");
  if (ZUSTAND.regelOffen) det.open = true;
  det.addEventListener("toggle", () => { ZUSTAND.regelOffen = det.open; });
  inhalt.querySelectorAll("[data-st]").forEach((b) => b.addEventListener("click", () => { ZUSTAND.tippSt[d.liga] = +b.dataset.st; zeichne(); }));
  $("#tz-kopieren")?.addEventListener("click", (e) => kopiere(text, e.currentTarget));
  bindeTooltips(inhalt);
  ladeTorjaeger(d);
}

function zeichne() {
  if (!daten()) return;
  document.querySelectorAll("#ligen button").forEach((b) => b.setAttribute("aria-selected", b.dataset.l === ZUSTAND.liga));
  document.querySelectorAll("#ansichten button").forEach((b) => b.setAttribute("aria-selected", b.dataset.a === ZUSTAND.ansicht));
  ({ spieltag: zeichneSpieltag, tabelle: zeichneTabelle, teams: zeichneTeams, treffer: zeichneTreffer, tipprunde: zeichneTipprunde })[ZUSTAND.ansicht]();
}

/* Tooltips für Elemente mit data-tip */
function bindeTooltips(root) {
  const tip = $("#tooltip");
  const touch = matchMedia("(hover: none)").matches;
  root.querySelectorAll("[data-tip]").forEach((el) => {
    if (touch) {
      // Handy: Antippen zeigt den Hinweis über dem Element, nächstes Antippen schließt ihn
      el.addEventListener("click", (e) => {
        e.stopPropagation();
        const r = el.getBoundingClientRect();
        tip.textContent = el.dataset.tip; tip.style.display = "block";
        const halb = tip.offsetWidth / 2;
        const x = Math.min(Math.max(r.left + r.width / 2, halb + 8), document.documentElement.clientWidth - halb - 8);
        tip.style.left = x + scrollX + "px"; tip.style.top = r.top + scrollY - 6 + "px";
        clearTimeout(tip._t); tip._t = setTimeout(() => { tip.style.display = "none"; }, 4000);
      });
      return;
    }
    el.addEventListener("mouseenter", () => { tip.textContent = el.dataset.tip; tip.style.display = "block"; });
    el.addEventListener("mousemove", (e) => { tip.style.left = e.pageX + "px"; tip.style.top = e.pageY - 8 + "px"; });
    el.addEventListener("mouseleave", () => { tip.style.display = "none"; });
  });
}

/* ---------- Start ---------- */
function speichereWahl() {
  try { localStorage.setItem("fp-wahl", JSON.stringify({ liga: ZUSTAND.liga, ansicht: ZUSTAND.ansicht })); } catch { /* egal */ }
}
async function wechsleLiga(liga) {
  ZUSTAND.liga = liga; ZUSTAND.team = null; speichereWahl();
  if (!ZUSTAND.daten[liga]) {
    inhalt.innerHTML = '<div class="leer">lädt …</div>';
    ZUSTAND.daten[liga] = await ladeJson(`data/${liga}.json`);
  }
  zeichne();
  liveAktualisieren();
}

async function start() {
  // als App installierbar und offline nutzbar (zuletzt geladener Stand)
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
  document.addEventListener("click", () => { const t = $("#tooltip"); if (t) t.style.display = "none"; });
  // Hell/Dunkel
  try { const th = localStorage.getItem("fp-theme"); if (th) document.documentElement.dataset.theme = th; } catch { /* egal */ }
  $("#theme").addEventListener("click", () => {
    const dunkel = document.documentElement.dataset.theme
      ? document.documentElement.dataset.theme === "dark"
      : matchMedia("(prefers-color-scheme: dark)").matches;
    document.documentElement.dataset.theme = dunkel ? "light" : "dark";
    try { localStorage.setItem("fp-theme", document.documentElement.dataset.theme); } catch { /* egal */ }
  });
  try {
    ZUSTAND.index = await ladeJson("data/index.json");
  } catch (e) {
    inhalt.innerHTML = `<div class="card leer">Keine Daten gefunden. Bitte zuerst <code>python run.py</code> ausführen.</div>`;
    return;
  }
  ladeJson("data/trefferquote.json").then((x) => { ZUSTAND.treffer = x; if (ZUSTAND.ansicht === "treffer") zeichne(); }).catch(() => {});
  ladeJson("data/backtest.json").then((x) => { ZUSTAND.backtest = x; if (ZUSTAND.ansicht === "treffer") zeichne(); }).catch(() => {});
  $("#stand").textContent = `Stand ${datum(ZUSTAND.index.stand)} · Saison ${ZUSTAND.index.saison}/${String((ZUSTAND.index.saison + 1) % 100).padStart(2, "0")}`;
  $("#ligen").innerHTML = ZUSTAND.index.ligen.map((l) => `<button data-l="${l.id}" role="tab">${esc(l.name)}</button>`).join("");
  $("#ligen").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) wechsleLiga(b.dataset.l); });
  $("#ansichten").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    ZUSTAND.ansicht = b.dataset.a; speichereWahl(); zeichne();
    if (matchMedia("(max-width: 560px)").matches) scrollTo({ top: 0 });
  });
  let wahl = {};
  try { wahl = JSON.parse(localStorage.getItem("fp-wahl") || "{}"); } catch { /* egal */ }
  if (wahl.ansicht) ZUSTAND.ansicht = wahl.ansicht;
  const liga = ZUSTAND.index.ligen.some((l) => l.id === wahl.liga) ? wahl.liga : ZUSTAND.index.ligen[0].id;
  await wechsleLiga(liga);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) liveAktualisieren(); });
}
start();
