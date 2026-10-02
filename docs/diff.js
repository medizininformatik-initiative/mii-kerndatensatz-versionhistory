// MII Kerndatensatz Release-Diff
// Gesamtliste der Aenderungen zwischen zwei Staenden der Complete-BOM

const STATUS = [
  ["removed", "entfallen"],
  ["renamed", "umbenannt"],
  ["changed", "geändert"],
  ["new", "neu"],
  ["unchanged", "unverändert"],
];
const STATUS_LABEL = Object.fromEntries(STATUS);
const MODULE_STATE = {
  new: "neu im Zielstand",
  dropped: "nicht mehr in der BOM",
  "not-indexed": "nicht im Index",
};

let DIFF = null;
// Unveraenderte Profile sind die Mehrheit des Rauschens — standardmaessig aus
const FILTER = { module: "", q: "", tighter: false,
                 status: new Set(["removed", "renamed", "changed", "new"]) };

async function load() {
  const res = await fetch("release-diff.json?v=aebf2baa");
  DIFF = await res.json();

  const title = `Complete ${DIFF.baseline.version} → ${DIFF.target.version}`;
  document.getElementById("diff-title").textContent = title;
  document.getElementById("diff-subtitle").textContent =
    `Gesamter KDS: ${DIFF.baseline.version} (stabil) gegen ${DIFF.target.version}`;

  const t = DIFF.totals;
  const kpis = [
    [DIFF.modules.length, "Module"],
    [t.changed, "Profile geändert"],
    [t.renamed, "umbenannt"],
    [t.new, "neu"],
    [t.removed, "entfallen"],
    [t.tighter, "Verschärfungen"],
  ];
  document.getElementById("diff-kpis").innerHTML = kpis.map(([n, l]) =>
    `<div class="kpi"><span class="kpi-num">${n}</span><span class="kpi-label">${l}</span></div>`).join("");

  const sel = document.getElementById("f-module");
  sel.innerHTML = `<option value="">Alle Module</option>` + DIFF.modules.map(m =>
    `<option value="${m.short}">${m.short}</option>`).join("");

  document.getElementById("f-status").innerHTML = STATUS.map(([k, l]) =>
    `<button type="button" class="chip ${k}" data-status="${k}">${l} <strong>${t[k]}</strong></button>`).join("");

  const h = decodeURIComponent(window.location.hash.slice(1));
  if (DIFF.modules.some(m => m.short === h)) FILTER.module = h;

  sel.addEventListener("change", () => setModule(sel.value));
  document.getElementById("f-search").addEventListener("input", e => {
    FILTER.q = e.target.value.trim().toLowerCase();
    drawList();
  });
  document.getElementById("f-tighter").addEventListener("change", e => {
    FILTER.tighter = e.target.checked;
    drawList();
  });
  document.getElementById("f-status").addEventListener("click", e => {
    const b = e.target.closest("[data-status]");
    if (!b) return;
    const k = b.dataset.status;
    FILTER.status.has(k) ? FILTER.status.delete(k) : FILTER.status.add(k);
    drawList();
  });
  document.getElementById("module-table").addEventListener("click", e => {
    const row = e.target.closest("[data-module]");
    if (!row) return;
    setModule(FILTER.module === row.dataset.module ? "" : row.dataset.module);
    document.getElementById("diff-list-section").scrollIntoView({ behavior: "smooth" });
  });
  // Inhalt eines Profils erst beim Aufklappen rendern: die Liste hat mehrere
  // hundert Eintraege mit zusammen tausenden Elementzeilen.
  document.getElementById("diff-list").addEventListener("toggle", e => {
    const d = e.target;
    if (!d.open || !d.classList.contains("diff-profile") || d.dataset.done) return;
    const m = DIFF.modules[+d.dataset.m];
    d.querySelector(".diff-body").innerHTML = renderProfileBody(m, m.profiles[+d.dataset.p]);
    d.dataset.done = "1";
  }, true);

  drawModuleTable();
  drawList();
}

function setModule(short) {
  FILTER.module = short;
  document.getElementById("f-module").value = short;
  history.replaceState(null, "", short ? "#" + encodeURIComponent(short) : window.location.pathname);
  drawModuleTable();
  drawList();
}

function versionCell(m, side) {
  const used = m[side], pin = m[side + "_pin"];
  if (!pin) return `<span class="muted">—</span>`;
  if (!used) return `<span class="muted">${escapeHtml(pin)}</span>`;
  return `<code>${escapeHtml(used)}</code>` + (used !== pin
    ? ` <span class="fd-kind" title="Die BOM pinnt ${escapeHtml(pin)}; dieser Stand ist nicht indexiert">statt ${escapeHtml(pin)}</span>`
    : "");
}

function drawModuleTable() {
  const num = (n, cls) => `<td class="num ${n ? cls : "zero"}">${n}</td>`;
  const rows = DIFF.modules.map(m => {
    const c = m.counts;
    const state = MODULE_STATE[m.state]
      ? ` <span class="fd-kind">${MODULE_STATE[m.state]}</span>` : "";
    const note = m.notes.length
      ? `<div class="diff-note">${m.notes.map(escapeHtml).join(" · ")}</div>` : "";
    return `<tr data-module="${m.short}" class="${FILTER.module === m.short ? "selected" : ""}">
      <td><strong>${m.short}</strong>${state}${note}</td>
      <td>${versionCell(m, "baseline")}</td>
      <td>${versionCell(m, "target")}</td>
      ${num(c.removed, "removed")}${num(c.renamed, "renamed")}${num(c.changed, "changed")}
      ${num(c.new, "new")}${num(c.unchanged, "unchanged")}${num(c.tighter, "tighter")}
    </tr>`;
  }).join("");
  document.getElementById("module-table").innerHTML = `
    <thead><tr>
      <th>Modul</th><th>${escapeHtml(DIFF.baseline.version)}</th><th>${escapeHtml(DIFF.target.version)}</th>
      <th class="num">entfallen</th><th class="num">umbenannt</th><th class="num">geändert</th>
      <th class="num">neu</th><th class="num">unverändert</th><th class="num">Verschärfungen</th>
    </tr></thead><tbody>${rows}</tbody>`;
}

function matches(r) {
  if (!FILTER.status.has(r.status)) return false;
  if (FILTER.tighter && !(r.n && r.n.tighter)) return false;
  if (!FILTER.q) return true;
  // Suchtext je Profil einmal aufbauen: Name, URLs und alle Element-IDs
  if (!r._hay) {
    const d = r.diff || {};
    r._hay = [r.name, r.old_name, r.url, r.old_url, r.resource_type,
      ...(d.changed || []).map(e => e.id), ...(d.removed || []).map(e => e.id),
      ...(d.added || []).map(e => e.id),
      ...(d.moved || []).flatMap(mv => [mv.from, mv.to]),
    ].filter(Boolean).join(" ").toLowerCase();
  }
  return r._hay.includes(FILTER.q);
}

function summaryCounts(r) {
  if (!r.n) return `<span class="muted">${r.n_elements} Elemente im Differential</span>`;
  const parts = [];
  if (r.n.added) parts.push(`<span class="cnt added">+${r.n.added}</span>`);
  if (r.n.removed) parts.push(`<span class="cnt removed">−${r.n.removed}</span>`);
  if (r.n.changed) parts.push(`<span class="cnt changed">~${r.n.changed}</span>`);
  if (r.n.moved) parts.push(`<span class="cnt moved">↳${r.n.moved}</span>`);
  if (r.n.tighter) parts.push(`<span class="fd-flag">${r.n.tighter} verschärft</span>`);
  return parts.join(" ");
}

function drawList() {
  document.querySelectorAll("#f-status .chip").forEach(b =>
    b.classList.toggle("on", FILTER.status.has(b.dataset.status)));

  let html = "", shown = 0;
  DIFF.modules.forEach((m, mi) => {
    if (FILTER.module && m.short !== FILTER.module) return;
    const rows = [];
    m.profiles.forEach((r, pi) => {
      if (!matches(r)) return;
      const renamed = r.old_name ? `<span class="muted">vorher ${escapeHtml(r.old_name)}</span>` : "";
      rows.push(`<details class="diff-profile" data-m="${mi}" data-p="${pi}">
        <summary>
          <span class="pill status-${r.status}">${STATUS_LABEL[r.status]}</span>
          <span class="diff-name">${escapeHtml(r.name)}</span>
          <span class="muted">${escapeHtml(r.resource_type)}</span>
          ${renamed}
          ${r.rename_unconfirmed ? '<span class="fd-kind">Zuordnung unbestätigt</span>' : ""}
          <span class="diff-counts">${summaryCounts(r)}</span>
        </summary>
        <div class="diff-body"></div>
      </details>`);
    });
    if (!rows.length) return;
    shown += rows.length;
    html += `<div class="diff-module">
      <h3>${m.short} <span class="muted">${escapeHtml(m.baseline || "—")} → ${escapeHtml(m.target || "—")}</span></h3>
      ${rows.join("")}</div>`;
  });
  document.getElementById("diff-list").innerHTML = html ||
    `<div class="empty-state"><strong>Keine Treffer</strong><p>Filter anpassen.</p></div>`;
  document.getElementById("f-count").textContent = `${shown} Profile`;
}

function renderProfileBody(m, r) {
  let html = `<div class="detail-subtitle">${escapeHtml(r.url)}</div>`;
  if (r.old_url) {
    html += `<div class="successor-hint">Canonical geändert: <code>${escapeHtml(r.old_url)}</code>
      &rarr; <code>${escapeHtml(r.url)}</code>. Für Instanzen breaking:
      <code>meta.profile</code> auf die alte URL validiert nicht mehr.
      ${r.rename_unconfirmed ? `<span class="fd-kind">Zuordnung über Namensähnlichkeit ${r.rename_unconfirmed}, nicht kuratiert</span>` : ""}
    </div>`;
  }
  if (r.old_module) {
    html += `<div class="successor-hint">Modulwechsel: ${escapeHtml(r.old_module)} &rarr; ${escapeHtml(m.short)}</div>`;
  }
  if (r.base) {
    html += `<div class="fd-element"><code class="fd-id">baseDefinition</code>
      <div class="fd-row"><div class="fd-vals">
        <span class="fd-old">${escapeHtml(r.base.from)}</span><span class="fd-arrow">&rarr;</span>
        <span class="fd-new">${escapeHtml(r.base.to)}</span></div></div></div>`;
  }
  if (r.governance) {
    html += `<div class="successor-hint">Governance-Übergang nach ISiK 6:
      <code>${escapeHtml(r.governance.target_url)}</code>
      <span class="fd-kind">nur URL-Zuordnung</span></div>`;
  }
  if (r.successor) {
    html += `<div class="successor-hint">Inhalt vermutlich aufgegangen in
      <strong>${escapeHtml(r.successor.module)}</strong> / ${escapeHtml(r.successor.name)}
      <span class="fd-kind">${r.successor.confirmed ? "kuratiert" : "unbestätigt"}</span></div>`;
  }
  if (r.status === "new") {
    html += `<div class="field-diff-empty">Im Ausgangsstand nicht vorhanden.
      ${r.n_elements} Elemente im Differential.</div>`;
  } else if (r.status === "removed") {
    html += `<div class="field-diff-empty">Im Zielstand nicht mehr vorhanden.</div>`;
  } else if (r.diff) {
    html += `<div class="field-diff">${renderFieldDiff(r.diff)}</div>`;
  } else if (!r.base) {
    html += `<div class="field-diff-empty">Keine strukturelle Änderung.</div>`;
  }
  if (r.bare) {
    html += `<div class="field-diff-empty">Nicht als Änderung gezählt: ${r.bare.length}
      Element(e) ohne eigene Einschränkung kamen ins Differential oder fielen heraus
      (${r.bare.map(i => `<code>${escapeHtml(i)}</code>`).join(", ")}) —
      ein Artefakt des Build-Werkzeugs, ohne Wirkung auf Instanzen.</div>`;
  }
  // Verlauf der Einzelschritte liegt im Subway-Explorer
  html += `<div class="diff-link"><a href="index.html#${encodeURIComponent(m.short)}/${encodeURIComponent(r.name)}">Versionsverlauf im Subway-Explorer</a></div>`;
  return html;
}

load();
