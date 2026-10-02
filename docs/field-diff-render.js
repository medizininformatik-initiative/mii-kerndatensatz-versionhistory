// Darstellung feld-genauer Diffs — gemeinsam genutzt vom Subway-Explorer
// (explorer.js) und vom Release-Diff (diff.js).

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
  // Kein Tabellenlayout: im schmalen Panel bleiben fuer die Werte sonst nur
  // wenige Zeichen, lange Bindings brechen zeichenweise um. Stattdessen pro
  // Feld eine Kopfzeile und darunter alt -> neu ueber die volle Breite.
  const rows = changed.map(el => {
    const fields = el.fields.map(f => `
      <div class="fd-row ${f.tighter ? "tighter" : ""}">
        <div class="fd-head">${escapeHtml(f.field)}${f.tighter
          ? ' <span class="fd-flag" title="Verschärfung — bestehende Instanzen können ungültig werden">verschärft</span>'
          : ""}</div>
        <div class="fd-vals">
          <span class="fd-old">${escapeHtml(f.from)}</span>
          <span class="fd-arrow">&rarr;</span>
          <span class="fd-new">${escapeHtml(f.to)}</span>
        </div>
      </div>`).join("");
    return `<div class="fd-element"><code class="fd-id">${escapeHtml(el.id)}</code>
      ${fields}</div>`;
  }).join("");
  const nTighter = changed.reduce((a, el) => a + el.fields.filter(f => f.tighter).length, 0);
  const head = `<div class="detail-row"><span class="label">Geänderte Elemente</span>
    <span class="value">${changed.length}${nTighter ? ` · <strong>${nTighter} Verschärfung(en)</strong>` : ""}</span></div>`;
  return out + head + rows;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}
