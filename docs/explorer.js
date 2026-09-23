// MII Kerndatensatz Version History Explorer
// Subway-Map-Style visualization of FHIR profile compatibility over versions

const CATEGORY_ORDER = [
  "identical",
  "canonical-only",
  "elements-added",
  "elements-removed",
  "modified",
  "mixed",
  "sole-version",
];

const SEVERITY_LABEL = {
  expected: "expected — major bump, SemVer ok",
  normal: "normal — ballot/pre-release phase",
  transition: "transition — consolidation (legacy → base)",
  neutral: "dep-driven — external dependency bumped",
  concerning: "concerning — minor bump with breaking change",
  problematic: "problematic — patch bump with breaking change",
};

let DATA = null;
let SELECTED_MODULE = null;
let SELECTED_STATION = null;  // { profileUrl, version }
let SELECTED_SEGMENT = null;  // { profileUrl, from, to }

// ── Initialization ────────────────────────────────────────────────

async function load() {
  const res = await fetch("data.json?v=ebbda4c1");
  DATA = await res.json();

  // Header meta
  const genDate = (DATA.generated_at || "").slice(0, 10);
  document.getElementById("header-meta").innerHTML =
    `<div>Daten generiert: <strong>${genDate}</strong></div>
     <div><a href="https://github.com/medizininformatik-initiative/mii-kerndatensatz-versionhistory" target="_blank">github</a> · <a href="data.json" target="_blank">data.json</a></div>`;

  // KPIs
  const totalReleases = DATA.modules.reduce((s, m) => s + m.n_versions, 0);
  const totalGroups = DATA.profiles.reduce((s, p) => s + p.groups.length, 0);
  const totalBreaking = DATA.profiles.reduce(
    (s, p) => s + p.transitions.filter(t => t.breaking).length, 0
  );
  document.getElementById("kpi-modules").textContent = DATA.n_modules;
  document.getElementById("kpi-releases").textContent = totalReleases;
  document.getElementById("kpi-profiles").textContent = DATA.n_profiles;
  document.getElementById("kpi-groups").textContent = totalGroups;
  document.getElementById("kpi-breaking").textContent = totalBreaking;

  drawModulePicker();
  handleHashChange();
  window.addEventListener("hashchange", handleHashChange);
}

// ── Hash routing ──────────────────────────────────────────────────

function handleHashChange() {
  const h = window.location.hash.slice(1); // strip "#"
  if (!h) {
    selectModule(null);
    return;
  }
  const parts = h.split("/").map(decodeURIComponent);
  const [mod, profileName, version] = parts;

  if (mod && DATA.modules.find(m => m.short === mod)) {
    selectModule(mod, { skipHash: true });
    if (profileName) {
      const p = DATA.profiles.find(
        x => x.module === mod && x.name === profileName
      );
      if (p) {
        if (version) {
          showStationDetail(p.url, version);
        } else {
          showProfileDetail(p.url);
        }
      }
    }
  }
}

function setHash(mod, profileName, version) {
  let h = "";
  if (mod) {
    h = encodeURIComponent(mod);
    if (profileName) {
      h += "/" + encodeURIComponent(profileName);
      if (version) {
        h += "/" + encodeURIComponent(version);
      }
    }
  }
  const newHash = h ? "#" + h : "";
  if (window.location.hash !== newHash) {
    history.replaceState(null, "", newHash || window.location.pathname);
  }
}

// ── Module picker ─────────────────────────────────────────────────

function drawModulePicker() {
  const container = document.getElementById("module-grid");
  container.innerHTML = "";

  const maxVersions = Math.max(...DATA.modules.map(m => m.n_versions));
  const sorted = DATA.modules.slice().sort((a, b) => {
    // Legacy zum Ende
    if (a.is_legacy !== b.is_legacy) return a.is_legacy ? 1 : -1;
    return b.n_profiles - a.n_profiles;
  });

  for (const m of sorted) {
    const card = document.createElement("div");
    card.className = "module-card" + (m.is_legacy ? " legacy" : "");
    if (SELECTED_MODULE === m.short) card.classList.add("selected");
    card.dataset.module = m.short;

    const nameClass = m.is_legacy ? "module-card-name legacy" : "module-card-name";
    card.innerHTML = `
      <div class="${nameClass}">${m.short}</div>
      <div class="module-card-meta">
        <span>${m.n_profiles} Profile</span>
        <span>${m.n_versions} Vers.</span>
      </div>
      <div class="module-card-bar">
        <div class="module-card-bar-fill" style="width:${(m.n_versions / maxVersions * 100).toFixed(0)}%"></div>
      </div>
    `;
    card.addEventListener("click", () => selectModule(m.short));
    container.appendChild(card);
  }
}

// ── Module selection → subway map ─────────────────────────────────

function selectModule(short, opts = {}) {
  SELECTED_MODULE = short;
  SELECTED_STATION = null;
  SELECTED_SEGMENT = null;

  // Update picker highlight
  document.querySelectorAll(".module-card").forEach(c => {
    c.classList.toggle("selected", c.dataset.module === short);
  });

  const section = document.getElementById("subway-section");
  if (!short) {
    section.classList.add("hidden");
    if (!opts.skipHash) setHash(null);
    return;
  }

  section.classList.remove("hidden");
  if (!opts.skipHash) setHash(short);

  const m = DATA.modules.find(x => x.short === short);
  document.getElementById("subway-title").textContent = short + (m.is_legacy ? " (legacy)" : "");
  const matStr = m.maturity
    ? ` · Maturity: <strong>${m.maturity.level}</strong> (${m.maturity.avg.toFixed(1)}/5)`
    : "";
  document.getElementById("subway-meta").innerHTML =
    `${m.package_id} · ${m.n_profiles} Profile · ${m.n_versions} Versionen (${m.first_version} → ${m.latest_version})${matStr}`;

  drawSubwayMap(short);
  resetDetailPanel();
}

// ── Subway map drawing ────────────────────────────────────────────

function parseSemverKey(v) {
  return v.split(/[.\-]/).map(p => {
    const n = parseInt(p, 10);
    return isNaN(n) ? p : n;
  });
}

function compareVersions(a, b) {
  const ka = parseSemverKey(a);
  const kb = parseSemverKey(b);
  for (let i = 0; i < Math.max(ka.length, kb.length); i++) {
    const x = ka[i], y = kb[i];
    if (x === undefined) return -1;
    if (y === undefined) return 1;
    if (typeof x === typeof y) {
      if (x < y) return -1;
      if (x > y) return 1;
    } else {
      return typeof x === "number" ? -1 : 1;
    }
  }
  return 0;
}

function drawSubwayMap(moduleShort) {
  const svg = document.getElementById("subway-map");
  svg.innerHTML = "";

  const profiles = DATA.profiles.filter(p => p.module === moduleShort);
  if (profiles.length === 0) {
    svg.setAttribute("viewBox", "0 0 800 80");
    svg.innerHTML = '<text x="40" y="40" fill="#888" font-size="13">Keine Profile in diesem Modul.</text>';
    return;
  }

  // Collect all versions across all profiles, sorted
  const versionSet = new Set();
  profiles.forEach(p => {
    p.groups.forEach(g => g.versions.forEach(v => versionSet.add(v)));
  });
  const versions = Array.from(versionSet).sort(compareVersions);

  // Sort profiles: those with more versions / less recent changes first
  profiles.sort((a, b) => {
    if (b.n_versions !== a.n_versions) return b.n_versions - a.n_versions;
    return a.name.localeCompare(b.name);
  });

  // Layout parameters
  const LEFT = 240;                // profile label column
  const RIGHT = 40;
  const ROW_H = 42;
  const TOP = 60;                  // space for version headers
  const BOTTOM = 20;

  // Compute dynamic column width
  const availableW = Math.max(800, versions.length * 60) - LEFT - RIGHT;
  const COL_W = Math.max(40, availableW / Math.max(1, versions.length - 1 || 1));

  const singleCol = versions.length === 1;
  const W = Math.max(LEFT + (versions.length - 1) * COL_W + RIGHT + 20, singleCol ? 720 : 0);
  const H = TOP + profiles.length * ROW_H + BOTTOM;

  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("preserveAspectRatio", "xMinYMid meet");

  // Position of each version on the x-axis
  const versionX = {};
  versions.forEach((v, i) => {
    versionX[v] = singleCol ? LEFT + (W - LEFT - RIGHT) / 2 : LEFT + i * COL_W;
  });

  // ── Background: year separators + version labels ──────────────
  let currentYear = null;
  versions.forEach((v, i) => {
    const year = versionYear(v);
    const x = versionX[v];

    // Year separator line
    if (year !== currentYear) {
      currentYear = year;
      svg.appendChild(svgEl("line", {
        x1: x - COL_W / 2, y1: 20, x2: x - COL_W / 2, y2: H - 10,
        stroke: "#e5e7eb", "stroke-dasharray": "2,4"
      }));
      svg.appendChild(svgEl("text", {
        x: x - COL_W / 2 + 6, y: 14,
        "font-size": 10, fill: "#9ca3af", "font-weight": "600"
      }, year.toString()));
    }

    // Version label (vertical)
    const label = svgEl("text", {
      x: x, y: TOP - 10,
      "text-anchor": "end",
      "font-size": 9,
      fill: "#6b7280",
      transform: `rotate(-55 ${x} ${TOP - 10})`,
    }, v);
    svg.appendChild(label);
  });

  // ── Draw each profile as a subway line ────────────────────────
  profiles.forEach((p, rowIdx) => {
    const y = TOP + rowIdx * ROW_H + ROW_H / 2;

    // Profile label
    const lbl = svgEl("text", {
      x: LEFT - 14, y: y + 4,
      "text-anchor": "end",
      class: "profile-label",
    });
    lbl.textContent = truncateName(p.name, 34);
    lbl.setAttribute("title", p.name);
    // Make clickable
    lbl.style.cursor = "pointer";
    lbl.addEventListener("click", () => showProfileDetail(p.url));
    svg.appendChild(lbl);

    // Map each version present to its x coordinate
    const versionsInProfile = [];
    p.groups.forEach(g => {
      g.versions.forEach(v => {
        versionsInProfile.push({ v, group: g });
      });
    });
    versionsInProfile.sort((a, b) => compareVersions(a.v, b.v));

    // Map transitions by from→to
    const txByKey = {};
    p.transitions.forEach(t => {
      txByKey[`${t.from}|${t.to}`] = t;
    });

    // Draw line segments between consecutive versions
    for (let i = 0; i < versionsInProfile.length - 1; i++) {
      const a = versionsInProfile[i];
      const b = versionsInProfile[i + 1];
      const tx = txByKey[`${a.v}|${b.v}`];
      const cat = tx ? tx.cat : "identical";

      const x1 = versionX[a.v];
      const x2 = versionX[b.v];

      const line = svgEl("line", {
        x1: x1, y1: y, x2: x2, y2: y,
        class: "subway-line " + cat,
      });
      const titleEl = svgEl("title", {});
      titleEl.textContent = `${a.v} → ${b.v}: ${cat}`
        + (tx && tx.breaking ? " (breaking)" : "")
        + "\nKlick zeigt die Detailänderungen";
      line.appendChild(titleEl);
      svg.appendChild(line);

      // Unsichtbare, breitere Trefferflaeche — eine 2px-Linie ist mit der
      // Maus kaum zu treffen, die Detailansicht haengt aber genau daran.
      const hit = svgEl("line", {
        x1: x1, y1: y, x2: x2, y2: y, class: "subway-hit",
      });
      hit.appendChild(titleEl.cloneNode(true));
      hit.addEventListener("click", e => {
        e.stopPropagation();
        showSegmentDetail(p.url, a.v, b.v);
      });
      svg.appendChild(hit);
    }

    // Draw stations
    versionsInProfile.forEach(({ v, group }, i) => {
      const cx = versionX[v];

      // Determine if this is an interchange (previous transition was breaking)
      const prevTx = i > 0 ? txByKey[`${versionsInProfile[i - 1].v}|${v}`] : null;
      const nextTx = i < versionsInProfile.length - 1 ? txByKey[`${v}|${versionsInProfile[i + 1].v}`] : null;
      const isBreakingIn = prevTx && prevTx.breaking;
      const isBreakingOut = nextTx && nextTx.breaking;
      const isInterchange = isBreakingIn || isBreakingOut;

      let station;
      const isSelected = SELECTED_STATION && SELECTED_STATION.profileUrl === p.url && SELECTED_STATION.version === v;

      if (isInterchange) {
        // Interchange marker - bigger, filled by severity
        const severity = (prevTx && prevTx.severity) || (nextTx && nextTx.severity) || "expected";
        station = svgEl("circle", {
          cx: cx, cy: y, r: 8,
          class: "subway-interchange severity-" + severity,
        });
      } else {
        station = svgEl("circle", {
          cx: cx, cy: y, r: 5,
          class: "subway-station" + (isSelected ? " selected" : ""),
        });
      }

      station.style.cursor = "pointer";
      station.addEventListener("click", e => {
        e.stopPropagation();
        showStationDetail(p.url, v);
      });

      const titleEl = svgEl("title", {});
      let titleText = `${p.name}\n${v}`;
      if (isBreakingIn) titleText += `\n⚠ breaking change from ${prevTx.from}`;
      titleEl.textContent = titleText;
      station.appendChild(titleEl);

      svg.appendChild(station);
    });

    // Governance-Terminal: Profil nach ISiK 6 uebergegangen
    if (p.governance && versionsInProfile.length) {
      const last = versionsInProfile[versionsInProfile.length - 1];
      const x0 = versionX[last.v];
      const seg = svgEl("line", {
        x1: x0, y1: y, x2: x0 + 46, y2: y,
        class: "subway-line governance-migration",
      });
      const t1 = svgEl("title", {});
      t1.textContent = `Governance-Übergang nach ISiK 6\n${p.governance.target_url}\nStruktur-Jaccard ${p.governance.jaccard || "?"} — reine URL-Zuordnung, keine Konformitätsaussage`;
      seg.appendChild(t1);
      svg.appendChild(seg);
      // MII->ISiK-Uebergabe-Marker: Pfeilspitze + Badge (eigenes Symbol, kein gematik-Logo)
      const ax = x0 + 46;
      const arrow = svgEl("path", {
        d: `M ${ax} ${y - 5} L ${ax + 7} ${y} L ${ax} ${y + 5} Z`,
        class: "governance-arrow",
      });
      svg.appendChild(arrow);
      const badge = svgEl("rect", {
        x: ax + 9, y: y - 9, width: 44, height: 18, rx: 9,
        class: "governance-badge",
      });
      const t2 = svgEl("title", {});
      t2.textContent = `Governance-Übergang nach ISiK 6\n${p.governance.target_url}\nStruktur-Jaccard ${p.governance.jaccard || "?"} — reine URL-Zuordnung, keine Konformitätsaussage`;
      badge.appendChild(t2);
      svg.appendChild(badge);
      const lbl = svgEl("text", {
        x: ax + 31, y: y + 3.5, class: "governance-label", "text-anchor": "middle",
      });
      lbl.textContent = "ISiK 6";
      svg.appendChild(lbl);
    }
  });
}

function svgEl(tag, attrs, text) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) {
    el.setAttribute(k, v);
  }
  if (text !== undefined) el.textContent = text;
  return el;
}

function truncateName(name, maxLen) {
  if (name.length <= maxLen) return name;
  return name.slice(0, maxLen - 1) + "…";
}

function versionYear(v) {
  if (v.startsWith("0.0")) return 2019;
  if (v.startsWith("0.9")) return 2020;
  if (v.startsWith("1.0")) return 2021;
  if (v.startsWith("2.0")) return 2022;
  if (v.startsWith("2024")) return 2024;
  if (v.startsWith("2025")) return 2025;
  if (v.startsWith("2026")) return 2026;
  return 2021;
}

// ── Detail panel ──────────────────────────────────────────────────

function resetDetailPanel() {
  document.getElementById("detail-panel").innerHTML = `
    <div class="empty-state">
      <strong>Klick auf eine Station oder ein Segment</strong>
      <p>um Details zu dieser Version oder zum Übergang zu sehen.</p>
    </div>
  `;
}

function showProfileDetail(profileUrl) {
  const p = DATA.profiles.find(x => x.url === profileUrl);
  if (!p) return;

  SELECTED_STATION = null;
  SELECTED_SEGMENT = null;
  drawSubwayMap(p.module);
  setHash(p.module, p.name);

  const panel = document.getElementById("detail-panel");
  let html = `
    <div class="detail-title">${escapeHtml(p.name)}</div>
    <div class="detail-subtitle">${escapeHtml(p.url)}</div>
    <div class="detail-row"><span class="label">Resource Type</span><span class="value">${p.resource_type}</span></div>
    <div class="detail-row"><span class="label">Versionen</span><span class="value">${p.n_versions}</span></div>
    <div class="detail-row"><span class="label">Erste Version</span><span class="value">${p.first_seen}</span></div>
    <div class="detail-row"><span class="label">Letzte Version</span><span class="value">${p.last_seen}</span></div>
    <div class="detail-row"><span class="label">Kompatibilitätsgruppen</span><span class="value">${p.groups.length}</span></div>

    <div class="detail-section-title">Kompatibilitätsgruppen</div>
  `;

  p.groups.forEach(g => {
    html += `
      <div style="padding:8px 0;border-bottom:1px solid var(--border);">
        <div style="display:flex;justify-content:space-between;align-items:center;">
          <span class="pill ${g.category}">Gruppe ${g.idx}</span>
          <span style="font-size:11px;color:var(--muted);">${g.versions.length} Version(en)</span>
        </div>
        <div class="version-list" style="margin-top:4px;">
          ${g.versions.map(v => `<span class="version-tag">${v}</span>`).join("")}
        </div>
      </div>
    `;
  });

  panel.innerHTML = html;
}

function showStationDetail(profileUrl, version) {
  const p = DATA.profiles.find(x => x.url === profileUrl);
  if (!p) return;

  SELECTED_STATION = { profileUrl, version };
  SELECTED_SEGMENT = null;
  drawSubwayMap(p.module);
  setHash(p.module, p.name, version);

  // Find the group this version belongs to
  const group = p.groups.find(g => g.versions.includes(version));

  // Find incoming and outgoing transitions
  const incomingTx = p.transitions.find(t => t.to === version);
  const outgoingTx = p.transitions.find(t => t.from === version);

  const panel = document.getElementById("detail-panel");
  let html = `
    <div class="detail-title">${escapeHtml(p.name)}</div>
    <div class="detail-subtitle">Version <strong style="color:var(--fg)">${version}</strong></div>
    ${p.successor ? `<div class="successor-hint">
      Inhalt vermutlich aufgegangen in <strong>${escapeHtml(p.successor.module)}</strong> /
      ${escapeHtml(p.successor.name)}
      <span class="fd-kind">${p.successor.evidence === "inherited" ? "erbt davon" : "Ähnlichkeit " + (p.successor.score || "?")}</span>
      ${p.successor.confirmed ? '<span class="fd-flag" style="background:#10b981">kuratiert</span>'
        : '<span class="fd-kind">unbestätigt</span>'}
    </div>` : ""}
  `;

  if (group) {
    html += `
      <div class="detail-row">
        <span class="label">Kompatibilitätsgruppe</span>
        <span class="value"><span class="pill ${group.category}">Gruppe ${group.idx}</span></span>
      </div>
      <div class="detail-row">
        <span class="label">Aggregierbar mit</span>
        <span class="value">${group.versions.length} Version(en)</span>
      </div>
      <div class="version-list" style="margin-top:8px;">
        ${group.versions.map(v => `<span class="version-tag" style="${v === version ? 'background:var(--accent-light);color:var(--accent);font-weight:600' : ''}">${v}</span>`).join("")}
      </div>
    `;
  }

  if (incomingTx) {
    html += `<div class="detail-section-title">Änderung von ${incomingTx.from}</div>`;
    html += renderTransition(incomingTx);
  }

  if (outgoingTx) {
    html += `<div class="detail-section-title">Änderung zu ${outgoingTx.to}</div>`;
    html += renderTransition(outgoingTx);
  }

  panel.innerHTML = html;
}

// Feld-Diffs werden erst bei Bedarf nachgeladen (148 KB, nicht im Erststart)
let FIELD_DIFFS = null;
let FIELD_DIFFS_PENDING = null;

function loadFieldDiffs() {
  if (FIELD_DIFFS) return Promise.resolve(FIELD_DIFFS);
  if (!FIELD_DIFFS_PENDING) {
    FIELD_DIFFS_PENDING = fetch("field-diffs.json?v=ebbda4c1")
      .then(r => r.ok ? r.json() : {})
      .catch(() => ({}))
      .then(d => { FIELD_DIFFS = d; return d; });
  }
  return FIELD_DIFFS_PENDING;
}

const KIND_LABEL = {
  cardinality: "Kardinalität", ms: "Must Support", type: "Typ",
  binding: "Binding", value: "Wert", slicing: "Slicing",
};

function renderElementList(ids, cls, label) {
  if (!ids || !ids.length) return "";
  // Eintraege sind entweder blanke IDs (aelteres Format) oder {id, props}
  const items = ids.map(i => {
    const id = typeof i === "string" ? i : i.id;
    const props = (typeof i === "string" ? [] : (i.props || []));
    const detail = props.length
      ? props.map(t => `<span class="fd-kind">${escapeHtml(t)}</span>`).join(" ")
      : (cls === "added"
          ? `<span class="fd-kind empty-el" title="Das Element steht im Differential, legt aber keine Kardinalität, kein Must Support, kein Binding und keine Invariante fest — meist ein leerer Container aus dem Build">ohne Constraints</span>`
          : "");
    return `<li><code>${escapeHtml(id)}</code>${detail ? " " + detail : ""}</li>`;
  }).join("");
  const open = ids.length <= 12 ? " open" : "";
  return `<details class="el-list ${cls}"${open}>
    <summary>${label} <strong>${ids.length}</strong></summary>
    <ul>${items}</ul></details>`;
}

function renderFieldDiff(entry) {
  if (!entry) {
    return `<div class="field-diff-empty">Für diesen Übergang liegen keine
      Detaildaten vor.</div>`;
  }
  // Altes Format (nur Liste) weiter unterstuetzen
  const changed = Array.isArray(entry) ? entry : (entry.changed || []);
  const removed = Array.isArray(entry) ? [] : (entry.removed || []);
  const added = Array.isArray(entry) ? [] : (entry.added || []);

  const moved = Array.isArray(entry) ? [] : (entry.moved || []);
  let out = "";
  if (moved.length) {
    const items = moved.map(m => `<li>
      <code class="mv-from">${escapeHtml(m.from)}</code>
      <span class="mv-arrow">↳</span>
      <code class="mv-to">${escapeHtml(m.to)}</code>
      <span class="fd-kind">${m.children} Unterelemente</span>
      <span class="fd-kind">${m.target_is_new ? "Ziel neu" : "Ziel bestand bereits"}</span>
    </li>`).join("");
    out += `<details class="el-list moved" open>
      <summary>Verschoben <strong>${moved.length}</strong>
        <span class="fd-kind">automatisierbar abbildbar</span></summary>
      <ul>${items}</ul></details>`;
  }
  out += renderElementList(removed, "removed", "Entfernte Elemente")
       + renderElementList(added, "added", "Neue Elemente");

  if (!changed.length) {
    return out + (removed.length || added.length || moved.length ? "" :
      `<div class="field-diff-empty">Keine Detailangaben vorhanden.</div>`);
  }
  const rows = changed.map(el => {
    const fields = el.fields.map(f => `
      <tr class="${f.tighter ? "tighter" : ""}">
        <td class="fd-field">${escapeHtml(f.field)}
          <span class="fd-kind">${escapeHtml(KIND_LABEL[f.kind] || f.kind)}</span>
          ${f.tighter ? '<span class="fd-flag" title="Verschärfung — bestehende Instanzen können ungültig werden">verschärft</span>' : ""}</td>
        <td class="fd-old">${escapeHtml(f.from)}</td>
        <td class="fd-arrow">&rarr;</td>
        <td class="fd-new">${escapeHtml(f.to)}</td>
      </tr>`).join("");
    return `<div class="fd-element"><code class="fd-id">${escapeHtml(el.id)}</code>
      <table class="fd-table">${fields}</table></div>`;
  }).join("");
  const nTighter = changed.reduce((a, el) => a + el.fields.filter(f => f.tighter).length, 0);
  const head = `<div class="detail-row"><span class="label">Geänderte Elemente</span>
    <span class="value">${changed.length}${nTighter ? ` · <strong>${nTighter} Verschärfung(en)</strong>` : ""}</span></div>`;
  return out + head + rows;
}

function showSegmentDetail(profileUrl, fromVer, toVer) {
  const p = DATA.profiles.find(x => x.url === profileUrl);
  if (!p) return;

  const tx = p.transitions.find(t => t.from === fromVer && t.to === toVer);
  if (!tx) return;

  SELECTED_STATION = null;
  SELECTED_SEGMENT = { profileUrl, from: fromVer, to: toVer };
  drawSubwayMap(p.module);
  setHash(p.module, p.name, `${fromVer}...${toVer}`);

  const panel = document.getElementById("detail-panel");
  let html = `
    <div class="detail-title">${escapeHtml(p.name)}</div>
    <div class="detail-subtitle">${fromVer} &rarr; ${toVer}</div>
    ${renderTransition(tx)}
    <div class="detail-section-title">Was sich geändert hat</div>
    <div id="field-diff-body" class="field-diff-loading">lädt …</div>
  `;
  panel.innerHTML = html;

  loadFieldDiffs().then(diffs => {
    // Panel koennte inzwischen weitergeklickt sein
    if (!SELECTED_SEGMENT || SELECTED_SEGMENT.profileUrl !== profileUrl
        || SELECTED_SEGMENT.from !== fromVer || SELECTED_SEGMENT.to !== toVer) return;
    const body = document.getElementById("field-diff-body");
    if (!body) return;
    let changed = (diffs[profileUrl] || {})[`${fromVer}|${toVer}`];
    if (!changed && p.aka) {
      for (const alt of p.aka) {
        changed = (diffs[alt] || {})[`${fromVer}|${toVer}`];
        if (changed) break;
      }
    }
    body.className = "field-diff";
    body.innerHTML = renderFieldDiff(changed);
  });
}

function renderTransition(tx) {
  let html = `
    <div class="detail-row">
      <span class="label">Kategorie</span>
      <span class="value"><span class="pill ${tx.cat}">${tx.cat}</span></span>
    </div>
    <div class="detail-row">
      <span class="label">Strukturell breaking</span>
      <span class="value">${tx.breaking ? '<span class="pill breaking">ja</span>' : "nein"}</span>
    </div>
    ${tx.instance_breaking ? `
    <div class="detail-row">
      <span class="label">Für Instanzen breaking</span>
      <span class="value"><span class="pill breaking">ja</span>
        <div class="hint">Die Canonical-URL hat sich geändert: Instanzen mit
        <code>meta.profile</code> auf die alte URL validieren nicht mehr, ebenso
        brechen abgeleitete Profile und Bindings auf die alte URL.
        <code>id</code> und <code>name</code> allein wären unkritisch —
        instanzrelevant ist nur die URL.</div></span>
    </div>` : ""}
    <div class="detail-row">
      <span class="label">Elemente</span>
      <span class="value">+${tx.n_add} / −${tx.n_rem} / ~${tx.n_mod}</span>
    </div>
  `;
  if (tx.severity) {
    html += `
      <div class="detail-row">
        <span class="label">Severity</span>
        <span class="value"><span class="severity-badge severity-${tx.severity}">${SEVERITY_LABEL[tx.severity] || tx.severity}</span></span>
      </div>
    `;
  }
  if (tx.reason) {
    html += `
      <div class="detail-row">
        <span class="label">Grund</span>
        <span class="value" style="font-family:ui-monospace,monospace;font-size:10px;">${tx.reason}</span>
      </div>
    `;
  }
  return html;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

load();
