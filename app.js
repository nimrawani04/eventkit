/* EventKit browser workspace.
 *
 * This is intentionally dependency-free. The app reads CSV files in the tab,
 * keeps the working set in memory, and only writes files when the organizer
 * clicks an export action. It is designed to work as a static Vercel deploy or
 * from a local folder.
 */

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const DEMO_REGISTRATIONS = `Full Name,Email Address,Phone,College,Notes
Alice Example,alice@example.com,+1 202 555 0100,Demo University,ready
Missing Email,,+1 202 555 0101,Demo University,needs contact
Invalid Email,bad-address,+1 202 555 0102,Demo University,check address
First Person,first@example.com,202-555-0199,Demo University,original
Exact Later,FIRST@EXAMPLE.COM,202-555-0103,Demo University,duplicate
Phone Match,phone@example.com,202 555 0199,Demo University,possible duplicate
Short Phone,short@example.com,123-4567,Demo University,too short
Domain Typo,typo@gmial.com,+1 202 555 0108,Demo University,check domain`;

const DEMO_ATTENDANCE = `email,role
alice@example.com,Participant
phone@example.com,Volunteer
unknown@example.com,Participant`;

const DEMO_SCHEDULE = `start,end,title,room,speaker
2026-05-01 09:00,2026-05-01 10:00,Opening,Hall 1,Ada
2026-05-01 10:00,2026-05-01 11:00,Workshop,Hall 1,Bea
2026-05-01 10:30,2026-05-01 11:30,Room clash,Hall 1,Chris
bad,2026-05-01 12:00,Bad time,Hall 2,`;

const state = {
  event: { name: "Untitled event", organizer: "Your organization", id: "my-event-2026", timezone: "local" },
  registrations: [],
  attendees: [],
  attendance: [],
  attendanceIssues: [],
  sessions: [],
  scheduleIssues: [],
  checkins: {},
  credentials: [],
  manifest: null,
  lastRun: null,
  activeView: "overview",
  rosterFilter: "all",
  rosterSearch: "",
  checkinSearch: "",
};

const COMMON_DOMAINS = ["gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com", "proton.me", "live.com"];

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[character]);
}

function formatNumber(value) { return new Intl.NumberFormat().format(value || 0); }
function slug(value) { return String(value || "item").normalize("NFKD").replace(/[^\w\s-]/g, "").trim().replace(/[\s_]+/g, "-").toLowerCase() || "item"; }
function cleanHeader(value) { return String(value || "").toLowerCase().replace(/[^a-z0-9]/g, ""); }
function normalizeName(value) { return String(value || "").trim().replace(/\s+/g, " "); }
function normalizeEmail(value) { return String(value || "").trim().toLowerCase(); }
function normalizePhone(value) { const raw = String(value || "").trim(); return `${raw.startsWith("+") ? "+" : ""}${raw.replace(/\D/g, "")}`; }
function hashString(value) { let hash = 2166136261; for (let i = 0; i < value.length; i += 1) { hash ^= value.charCodeAt(i); hash = Math.imul(hash, 16777619); } return (hash >>> 0).toString(16).padStart(8, "0").toUpperCase(); }
function attendeeId(email, row) { return email ? `R-${hashString(`registration\0${email}`)}` : `R-ROW-${String(row).padStart(4, "0")}`; }
function similarity(left, right) { const a = left.toLowerCase(); const b = right.toLowerCase(); const matrix = Array.from({ length: a.length + 1 }, () => Array(b.length + 1).fill(0)); for (let i = 0; i <= a.length; i += 1) matrix[i][0] = i; for (let j = 0; j <= b.length; j += 1) matrix[0][j] = j; for (let i = 1; i <= a.length; i += 1) for (let j = 1; j <= b.length; j += 1) matrix[i][j] = Math.min(matrix[i - 1][j] + 1, matrix[i][j - 1] + 1, matrix[i - 1][j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1)); const distance = matrix[a.length][b.length]; return 1 - distance / Math.max(a.length, b.length, 1); }

function parseCSV(text) {
  const source = String(text || "").replace(/^\uFEFF/, "");
  const rows = [];
  let row = [], field = "", quoted = false;
  for (let index = 0; index < source.length; index += 1) {
    const character = source[index];
    if (quoted) {
      if (character === '"' && source[index + 1] === '"') { field += '"'; index += 1; }
      else if (character === '"') quoted = false;
      else field += character;
    } else if (character === '"' && field === "") quoted = true;
    else if (character === ",") { row.push(field); field = ""; }
    else if (character === "\n") { row.push(field.replace(/\r$/, "")); if (row.some((cell) => cell.trim() !== "")) rows.push(row); row = []; field = ""; }
    else field += character;
  }
  if (field !== "" || row.length) { row.push(field.replace(/\r$/, "")); if (row.some((cell) => cell.trim() !== "")) rows.push(row); }
  if (!rows.length) return { headers: [], rows: [] };
  const headers = rows[0].map((header) => header.trim());
  return { headers, rows: rows.slice(1).map((values, index) => ({ line: index + 2, values, data: Object.fromEntries(headers.map((header, column) => [header, values[column] ?? ""])) })) };
}

function getValue(record, aliases) {
  const keys = Object.keys(record || {});
  const match = keys.find((key) => aliases.includes(cleanHeader(key)));
  return match ? record[match] : "";
}

function validEmail(value) { return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value); }

function domainSuggestion(email) {
  const domain = email.split("@")[1] || "";
  if (!domain || COMMON_DOMAINS.includes(domain)) return "";
  const closest = COMMON_DOMAINS.map((candidate) => ({ candidate, score: similarity(domain, candidate) })).sort((a, b) => b.score - a.score)[0];
  return closest && closest.score >= .8 ? closest.candidate : "";
}

function issue(code, level, row, message, suggestion, field = "") { return { code, level, row, message, suggestion, field }; }

function validateRegistrations(table) {
  const attendees = [];
  const issues = [];
  const seenEmails = new Map();
  const phoneMap = new Map();
  const institutionGroups = new Map();
  table.rows.forEach((record) => {
    const name = normalizeName(getValue(record.data, ["name", "fullname", "attendeename", "participantname"]));
    const email = normalizeEmail(getValue(record.data, ["email", "emailaddress", "mail"]));
    const phone = normalizePhone(getValue(record.data, ["phone", "phonenumber", "mobile", "telephone"]));
    const institution = normalizeName(getValue(record.data, ["institution", "college", "university", "organization", "school"]));
    const rowIssues = [];
    if (!name || !email) { rowIssues.push(issue("EK01", "error", record.line, "Name or email is missing.", "Provide both a name and an email address.", !name ? "name" : "email")); }
    if (email && !validEmail(email)) rowIssues.push(issue("EK02", "error", record.line, "Email address is not syntactically valid.", "Check the email address and enter it again.", "email"));
    if (email && seenEmails.has(email)) rowIssues.push(issue("EK03", "error", record.line, `Email is an exact duplicate of row ${seenEmails.get(email)}.`, "Keep the first registration or confirm which row to use.", "email"));
    else if (email && validEmail(email)) seenEmails.set(email, record.line);
    if (phone && (phone.replace(/\D/g, "").length < 8 || phone.replace(/\D/g, "").length > 15)) rowIssues.push(issue("EK05", "warning", record.line, `Phone has ${phone.replace(/\D/g, "").length} digits; expected between 8 and 15.`, "Check the phone number and its country code.", "phone"));
    const suggestion = domainSuggestion(email);
    if (suggestion) rowIssues.push(issue("EK06", "warning", record.line, `Email domain looks like '${suggestion}'.`, `Check the spelling; did you mean ${suggestion}?`, "email"));
    const attendee = { row: record.line, name, email, phone, institution, original: record.data, issues: rowIssues, status: rowIssues.some((item) => item.level === "error") ? "excluded" : "ok", id: attendeeId(email, record.line), attended: false, role: "Participant" };
    attendees.push(attendee);
    rowIssues.forEach((item) => issues.push(item));
    if (phone && validEmail(email)) { if (!phoneMap.has(phone)) phoneMap.set(phone, []); phoneMap.get(phone).push(attendee); }
    if (institution && name) { const key = institution.toLowerCase(); if (!institutionGroups.has(key)) institutionGroups.set(key, []); institutionGroups.get(key).push(attendee); }
  });
  phoneMap.forEach((group) => { if (group.length > 1) group.forEach((attendee) => { if (attendee.status === "ok") { const other = group.find((candidate) => candidate.email !== attendee.email); if (other && !attendee.issues.some((item) => item.code === "EK04")) { const found = issue("EK04", "warning", attendee.row, `Possible duplicate of row ${other.row}: the phone number matches a different email.`, "Confirm whether these registrations belong to the same person.", "phone"); attendee.issues.push(found); issues.push(found); } } }); });
  institutionGroups.forEach((group) => { for (let i = 0; i < group.length; i += 1) for (let j = i + 1; j < group.length; j += 1) { const left = group[i], right = group[j]; if (left.email && left.email !== right.email && similarity(left.name, right.name) >= .92) { [left, right].forEach((attendee, index) => { if (attendee.status === "ok" && !attendee.issues.some((item) => item.code === "EK04")) { const other = index ? left : right; const found = issue("EK04", "warning", attendee.row, `Possible duplicate of row ${other.row}: names are very similar at the same institution.`, "Confirm whether these registrations belong to the same person.", "name"); attendee.issues.push(found); issues.push(found); } }); } } });
  return { attendees, issues };
}

function parseLocalDate(value) { const match = String(value || "").trim().match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})$/); if (!match) return null; const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]), Number(match[4]), Number(match[5])); return Number.isNaN(date.getTime()) ? null : date; }

function validateSchedule(table) {
  const sessions = [], issues = [];
  table.rows.forEach((record) => {
    const startText = getValue(record.data, ["start", "starttime", "begins"]); const endText = getValue(record.data, ["end", "endtime", "ends"]); const start = parseLocalDate(startText); const end = parseLocalDate(endText); const session = { row: record.line, start, end, startText, endText, title: normalizeName(getValue(record.data, ["title", "session", "name"])), room: normalizeName(getValue(record.data, ["room", "location", "venue"])), speaker: normalizeName(getValue(record.data, ["speaker", "presenter", "host"])), issues: [] };
    if (!start || !end || end <= start) { const found = issue("SC01", "error", record.line, "Session time is invalid or ends before it starts.", "Use YYYY-MM-DD HH:MM with an end time after the start.", !start ? "start" : "end"); session.issues.push(found); issues.push(found); }
    else sessions.push(session);
  });
  for (let i = 0; i < sessions.length; i += 1) for (let j = i + 1; j < sessions.length; j += 1) { const left = sessions[i], right = sessions[j]; if (left.start < right.end && right.start < left.end) { if (left.room && left.room.toLowerCase() === right.room.toLowerCase()) { const a = issue("SC02", "error", left.row, `Room '${left.room}' overlaps with session on row ${right.row}.`, "Adjust one session's time or move it to another room.", "room"); const b = issue("SC02", "error", right.row, `Room '${right.room}' overlaps with session on row ${left.row}.`, "Adjust one session's time or move it to another room.", "room"); left.issues.push(a); right.issues.push(b); issues.push(a, b); } if (left.speaker && left.speaker.toLowerCase() === right.speaker.toLowerCase()) { const a = issue("SC03", "error", left.row, `Speaker '${left.speaker}' is double-booked on row ${right.row}.`, "Adjust one of the speaker's session times.", "speaker"); const b = issue("SC03", "error", right.row, `Speaker '${right.speaker}' is double-booked on row ${left.row}.`, "Adjust one of the speaker's session times.", "speaker"); left.issues.push(a); right.issues.push(b); issues.push(a, b); } } }
  return { sessions: sessions.sort((a, b) => a.start - b.start), issues };
}

function parseAttendance(table, attendees) {
  const byEmail = new Map(attendees.filter((item) => item.email).map((item) => [item.email, item])); const seen = new Set(); const attendance = []; const issues = [];
  table.rows.forEach((record) => { const email = normalizeEmail(getValue(record.data, ["email", "emailaddress", "mail"])); const role = normalizeName(getValue(record.data, ["role", "attendeetype", "type"])) || "Participant"; if (!email || !byEmail.has(email) || byEmail.get(email).status === "excluded") { issues.push(issue("EK07", "warning", record.line, email ? `Attendance email '${email}' is not eligible in the registration roster.` : "Attendance email is missing.", "Confirm the attendance email and registration status.", "email")); return; } if (seen.has(email)) issues.push(issue("EK08", "warning", record.line, `Attendance email is repeated from an earlier row.`, "Keep one attendance row or confirm the intended role.", "email")); seen.add(email); const attendee = byEmail.get(email); attendee.attended = true; attendee.role = role; attendance.push({ attendeeId: attendee.id, email, role, row: record.line }); });
  return { attendance, issues };
}

function setMetaFromInputs() { state.event.name = $("#event-name-input").value.trim() || "Untitled event"; state.event.organizer = $("#organizer-input").value.trim() || "Your organization"; state.event.id = $("#event-id-input").value.trim() || "my-event-2026"; state.event.timezone = $("#timezone-input").value; }

function setHeader() {
  const nameEl = $("#header-event-name");
  if (nameEl) nameEl.textContent = state.event.name;
  const idEl = $("#header-event-id");
  if (idEl) idEl.textContent = state.event.id.toUpperCase();
  const rosterCount = $("#nav-roster-count");
  if (rosterCount) rosterCount.textContent = formatNumber(state.attendees.length);
  const scheduleCount = $("#nav-schedule-count");
  if (scheduleCount) scheduleCount.textContent = formatNumber(state.sessions.length);
  const checkinCount = $("#nav-checkin-count");
  if (checkinCount) checkinCount.textContent = formatNumber(Object.values(state.checkins).filter(Boolean).length);
  const certCount = $("#nav-certificate-count");
  if (certCount) certCount.textContent = formatNumber(state.credentials.length);
}

function toast(message, tone = "success") { const region = $("#toast-region"); const item = document.createElement("div"); item.className = `toast ${tone}`; item.textContent = message; region.appendChild(item); setTimeout(() => item.remove(), 3400); }

function downloadFile(filename, content, type = "text/plain;charset=utf-8") { const blob = new Blob([content], { type }); const url = URL.createObjectURL(blob); const link = document.createElement("a"); link.href = url; link.download = filename; link.click(); setTimeout(() => URL.revokeObjectURL(url), 500); }

function downloadJSON(filename, value) { downloadFile(filename, JSON.stringify(value, null, 2), "application/json;charset=utf-8"); }

function csvCell(value) { const text = String(value ?? ""); return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text; }

function rosterCSV() { const header = ["row", "attendee_id", "name", "email", "phone", "institution", "status", "issues"]; const rows = state.attendees.map((item) => [item.row, item.id, item.name, item.email, item.phone, item.institution, item.status, item.issues.map((entry) => entry.code).join(",")]); return [header, ...rows].map((row) => row.map(csvCell).join(",")).join("\n") + "\n"; }

function issuesMarkdown() { const all = [...state.attendees.flatMap((item) => item.issues), ...state.attendanceIssues, ...state.scheduleIssues]; const lines = ["# EventKit review findings", "", `Event: ${state.event.name}`, "", `- Findings: ${all.length}`, `- Errors: ${all.filter((item) => item.level === "error").length}`, `- Warnings: ${all.filter((item) => item.level === "warning").length}`, "", "## Findings", ""]; if (!all.length) lines.push("No unresolved findings."); else all.forEach((item) => lines.push(`- Row ${item.row} — **${item.code}**: ${item.message} Suggested fix: ${item.suggestion}`)); return `${lines.join("\n")}\n`; }

function scheduleICS() { const lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//eventkit//Browser//EN", `X-WR-CALNAME:${state.event.name.replace(/[\\;,]/g, "\\$&")}`]; state.sessions.forEach((session, index) => { const utc = (date) => date.toISOString().replace(/[-:]/g, "").replace(/\.\d{3}/, ""); lines.push("BEGIN:VEVENT", `UID:${hashString(`${state.event.id}|${session.title}|${session.row}`)}@eventkit`, `DTSTAMP:${utc(session.start)}`, `DTSTART:${utc(session.start)}`, `DTEND:${utc(session.end)}`, `SUMMARY:${session.title.replace(/[\\;,]/g, "\\$&")}`, `LOCATION:${session.room.replace(/[\\;,]/g, "\\$&")}`, `DESCRIPTION:${session.speaker ? `Speaker: ${session.speaker}` : ""}`, "END:VEVENT"); }); lines.push("END:VCALENDAR"); return `${lines.join("\r\n")}\r\n`; }

function checkinLog() { return Object.entries(state.checkins).filter(([, checked]) => checked).map(([attendeeId], index) => JSON.stringify({ action: "check_in", attendee_id: attendeeId, event_id: state.event.id, operator: "browser operator", schema_version: 1, sequence: index + 1, device_id: "eventkit-web" })).join("\n") + (Object.values(state.checkins).some(Boolean) ? "\n" : ""); }

function getInitials(name) {
  const parts = String(name || "").trim().split(/\s+/);
  if (!parts.length || !parts[0]) return "✦";
  return ((parts[0][0] || "") + (parts[1]?.[0] || parts[0][1] || "")).toUpperCase();
}

function renderStatus(item) { return `<span class="status-badge ${item === "ok" || item === "included" ? "status-good" : item === "excluded" ? "status-danger" : "status-warning"}">${escapeHtml(item === "ok" ? "Included" : item)}</span>`; }
function renderIssueBadges(issues) { return issues.length ? issues.map((item) => `<span class="issue-badge" title="${escapeHtml(item.message)}">${escapeHtml(item.code)}</span>`).join("") : `<span class="status-badge status-good" style="font-size:8px;padding:2px 7px">None</span>`; }
function allIssues() { return [...state.attendees.flatMap((item) => item.issues), ...state.attendanceIssues, ...state.scheduleIssues]; }

function renderOverview() {
  const included = state.attendees.filter((item) => item.status === "ok").length; const excluded = state.attendees.length - included; const warnings = allIssues().filter((item) => item.level === "warning").length; const errors = allIssues().filter((item) => item.level === "error").length; const checked = Object.values(state.checkins).filter(Boolean).length; const attention = allIssues().slice(0, 5);
  if (!state.attendees.length) {
    $("#view-overview").innerHTML = `<div class="empty-onboarding"><div class="empty-orbit">✦</div><p class="eyebrow">The Atelier Workspace</p><h2>Your private event command center.</h2><p>EventKit will sanitize attendee records, resolve room and speaker clashes, operate a live offline arrival desk, and issue cryptographic credentials — private to your browser window.</p><div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap;justify-content:center;margin-top:8px"><button class="button button-gold button-glow" data-action="focus-import"><span>Import Registrations CSV</span><span class="arrow-icon">→</span></button><button class="button button-ghost" data-action="load-demo"><span>✦ Explore Demo Data</span></button></div></div>`;
    return;
  }
  const maxBar = Math.max(state.attendees.length, 1);
  const bars = [{ label: "Included", value: included }, { label: "Excluded", value: excluded }, { label: "Checked in", value: checked }, { label: "Credentials", value: state.credentials.length }];
  $("#view-overview").innerHTML = `<div class="view-heading"><div><p class="eyebrow">02 / Executive Control Room</p><h2>Operational Status</h2><p>Live health and telemetry for <strong>${escapeHtml(state.event.name)}</strong>.</p></div><div class="heading-actions"><span class="status-badge ${errors || warnings ? "status-warning" : "status-good"}">${errors || warnings ? "Review Required" : "Ready for Export"}</span><button class="button button-ghost" data-action="run-analysis">↻ Refresh analysis</button></div></div><div class="metric-grid"><article class="metric-card"><span class="metric-label">Total Registered</span><strong class="metric-value">${formatNumber(state.attendees.length)}</strong><span class="metric-detail">All source rows preserved</span></article><article class="metric-card good"><span class="metric-label">Qualified Roster</span><strong class="metric-value">${formatNumber(included)}</strong><span class="metric-detail">${excluded ? `${excluded} require human decision` : "Full roster validated"}</span></article><article class="metric-card ${errors ? "danger" : ""}"><span class="metric-label">Audit Findings</span><strong class="metric-value">${formatNumber(errors + warnings)}</strong><span class="metric-detail">${errors} errors · ${warnings} warnings</span></article><article class="metric-card blue"><span class="metric-label">Arrival Attendance</span><strong class="metric-value">${formatNumber(checked)}</strong><span class="metric-detail">${state.attendees.length ? `${Math.round(checked / Math.max(included, 1) * 100)}% check-in rate` : "Awaiting check-in"}</span></article></div><div class="overview-grid"><div class="card"><div class="card-header"><div><h3>Operational Pulse</h3><small>Real-time workspace distribution</small></div><div class="legend"><i></i> Records</div></div><div class="card-body"><div class="bar-chart">${bars.map((bar) => `<div class="bar-column"><div class="bar" style="height:${Math.max(6, bar.value / maxBar * 100)}%" title="${bar.value}"></div><span>${escapeHtml(bar.label)}</span></div>`).join("")}</div></div></div><div class="card attention-card"><div class="card-header"><div><h3>Audit & Human Review</h3><small>${allIssues().length ? "Actionable findings surfaced" : "All checks cleared"}</small></div><button class="text-button" data-view="roster">Open roster →</button></div><div class="card-body">${attention.length ? `<div class="attention-list">${attention.map((item) => `<div class="attention-item"><i class="issue-dot"></i><div><strong>${escapeHtml(item.code)} · Row ${item.row}</strong><span>${escapeHtml(item.message)}</span></div></div>`).join("")}</div>` : `<div class="clear-state"><b>✓</b><span>Every operational check passed. Your event kit is ready for production.</span></div>`}</div></div></div><div class="card" style="margin-top:20px"><div class="card-header"><div><h3>Next Best Actions</h3><small>Transition seamlessly from preparation to event day</small></div></div><div class="card-body"><div class="quick-actions"><button class="quick-action" data-view="roster"><span class="quick-action-icon">♧</span><span><strong>Review Guestlist & Roster</strong><span>${errors + warnings ? `Review ${errors + warnings} flagged records` : "Examine sanitized attendee list"}</span></span><span class="quick-arrow">→</span></button><button class="quick-action" data-view="checkin"><span class="quick-action-icon">✓</span><span><strong>Launch Arrival Desk</strong><span>Rapid search and record arrivals in offline mode</span></span><span class="quick-arrow">→</span></button><button class="quick-action" data-view="exports"><span class="quick-action-icon">↗</span><span><strong>Export Deliverables Pack</strong><span>Download verified CSVs, calendar feeds and credentials</span></span><span class="quick-arrow">→</span></button></div></div></div>`;
}

function renderRoster() {
  const search = state.rosterSearch.toLowerCase(); const rows = state.attendees.filter((item) => (state.rosterFilter === "all" || (state.rosterFilter === "included" ? item.status === "ok" : item.status === "excluded")) && (!search || `${item.name} ${item.email} ${item.institution} ${item.id}`.toLowerCase().includes(search)));
  $("#view-roster").innerHTML = `<div class="view-heading"><div><p class="eyebrow">03 / Data Quality & Guestlist</p><h2>Registration Roster</h2><p>Deterministic cleansing. Zero dropped rows. Every attendee row carries an audit trail.</p></div><div class="heading-actions"><button class="button button-ghost" data-action="download-roster"><span>↗</span> Download CSV</button></div></div><div class="card table-card"><div class="table-toolbar"><input class="toolbar-input" id="roster-search" value="${escapeHtml(state.rosterSearch)}" placeholder="Search name, email or affiliation..."><div class="filter-group"><button class="filter-button ${state.rosterFilter === "all" ? "is-active" : ""}" data-roster-filter="all">All (${state.attendees.length})</button><button class="filter-button ${state.rosterFilter === "included" ? "is-active" : ""}" data-roster-filter="included">Included (${state.attendees.filter((item) => item.status === "ok").length})</button><button class="filter-button ${state.rosterFilter === "excluded" ? "is-active" : ""}" data-roster-filter="excluded">Excluded (${state.attendees.filter((item) => item.status === "excluded").length})</button></div></div><div class="data-table-wrap"><table class="data-table"><thead><tr><th>Attendee</th><th>Contact Channel</th><th>Affiliation</th><th>Status</th><th>Audit Flags</th></tr></thead><tbody>${rows.length ? rows.map((item) => `<tr><td><div class="attendee-cell"><div class="attendee-avatar">${escapeHtml(getInitials(item.name))}</div><div><strong>${escapeHtml(item.name || "Unnamed")}</strong><small>Row ${item.row} · <code>${escapeHtml(item.id)}</code></small></div></div></td><td>${escapeHtml(item.email || "—")}<small>${escapeHtml(item.phone || "No phone")}</small></td><td>${escapeHtml(item.institution || "—")}</td><td>${renderStatus(item.status)}</td><td>${renderIssueBadges(item.issues)}</td></tr>`).join("") : `<tr><td colspan="5" class="empty-table">No attendee records match the current filter.</td></tr>`}</tbody></table></div></div>`;
  $("#roster-search")?.addEventListener("input", (event) => { state.rosterSearch = event.target.value; renderRoster(); }); $$("[data-roster-filter]").forEach((button) => button.addEventListener("click", () => { state.rosterFilter = button.dataset.rosterFilter; renderRoster(); }));
}

function dateLabel(date) { return date ? date.toLocaleDateString(undefined, { month: "short", day: "numeric" }) : "—"; }
function timeLabel(date) { return date ? date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "—"; }

function renderSchedule() {
  const clashes = state.scheduleIssues.filter((item) => item.code === "SC02" || item.code === "SC03");
  $("#view-schedule").innerHTML = `<div class="view-heading"><div><p class="eyebrow">04 / Programme Architecture</p><h2>Schedule & Conflict Watch</h2><p>Temporal alignment with instant room overlap and speaker double-booking detection.</p></div><div class="heading-actions"><button class="button button-ghost" data-action="download-ics"><span>↗</span> Export Calendar (.ics)</button></div></div>${!state.sessions.length && !state.scheduleIssues.length ? `<div class="card"><div class="timeline-empty">Import a schedule CSV in the workspace setup to visualize sessions and detect clashes.</div></div>` : `<div class="split-grid"><div class="card"><div class="card-header"><div><h3>${state.sessions.length} Programmed Sessions</h3><small>Chronologically indexed</small></div><span class="status-badge ${clashes.length ? "status-danger" : "status-good"}">${clashes.length ? `${clashes.length} conflicts` : "Conflict-Free"}</span></div><div class="card-body"><div class="schedule-list">${state.sessions.map((session) => `<div class="session-row"><div class="session-time"><b>${timeLabel(session.start)} – ${timeLabel(session.end)}</b>${dateLabel(session.start)}</div><div class="session-title"><strong>${escapeHtml(session.title || "Untitled session")}</strong><span>${escapeHtml(session.room || "No room assigned")} ${session.speaker ? `· Host: ${escapeHtml(session.speaker)}` : ""}</span></div><div class="session-check">${session.issues.length ? `<span class="status-badge status-danger">${session.issues.map((item) => item.code).join(" · ")}</span>` : `<span class="status-badge status-good">Valid</span>`}</div></div>`).join("")}</div></div></div><div class="card"><div class="card-header"><div><h3>Conflict Audit</h3><small>Actionable suggestions for overlapping sessions</small></div></div><div class="card-body">${clashes.length ? clashes.slice(0, 8).map((item) => `<div class="clash-box"><strong>${escapeHtml(item.code)} · Row ${item.row}</strong>${escapeHtml(item.message)}<br><span>Fix: ${escapeHtml(item.suggestion)}</span></div>`).join("") : `<div class="clear-state"><b>✓</b><span>All room allocations and speaker schedules are strictly conflict-free.</span></div>`}</div></div></div>`}`;
}

function renderCheckin() {
  const included = state.attendees.filter((item) => item.status === "ok"); const search = state.checkinSearch.toLowerCase(); const visible = included.filter((item) => !search || `${item.name} ${item.email} ${item.id} ${item.institution}`.toLowerCase().includes(search)); const checked = included.filter((item) => state.checkins[item.id]).length;
  $("#view-checkin").innerHTML = `<div class="view-heading"><div><p class="eyebrow">05 / Event Day Operations</p><h2>Arrival Desk & Access</h2><p>Zero-latency local check-in desk for event staff. Rapid lookup by attendee name, email, or badge code.</p></div><div class="heading-actions"><button class="button button-ghost" data-action="download-checkin-log"><span>↗</span> Export Arrival Log</button></div></div><div class="checkin-hero"><div><p class="eyebrow">Live Arrival Telemetry</p><h2>${checked} of ${included.length} Checked In</h2><p>Arrival states persist locally in browser memory and export as standard JSONL logs.</p></div><div class="checkin-progress"><div class="progress-ring" style="--progress:${included.length ? checked / included.length * 360 : 0}deg"><span>${included.length ? Math.round(checked / included.length * 100) : 0}%</span></div></div></div><div class="checkin-toolbar"><input class="checkin-search" id="checkin-search" value="${escapeHtml(state.checkinSearch)}" placeholder="Search attendee name or ID code..."><span class="checkin-state">${checked ? `${checked} guest${checked === 1 ? "" : "s"} arrived` : "Ready for arrivals"}</span></div><div class="card table-card"><div class="data-table-wrap"><table class="data-table"><thead><tr><th>Attendee</th><th>Affiliation</th><th>ID Code</th><th>Role</th><th>State</th><th>Action</th></tr></thead><tbody>${visible.length ? visible.map((item) => `<tr><td><div class="attendee-cell"><div class="attendee-avatar">${escapeHtml(getInitials(item.name))}</div><div><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.email)}</small></div></div></td><td>${escapeHtml(item.institution || "—")}</td><td><code>${escapeHtml(item.id)}</code></td><td><span class="role-badge">${escapeHtml(item.role || "Participant")}</span></td><td>${state.checkins[item.id] ? `<span class="status-badge status-good">Arrived</span>` : `<span class="status-badge status-muted">Expected</span>`}</td><td><button class="check-button ${state.checkins[item.id] ? "is-checked" : ""}" data-checkin-id="${escapeHtml(item.id)}">${state.checkins[item.id] ? "Undo" : "Check in"}</button></td></tr>`).join("") : `<tr><td colspan="6" class="empty-table">No attendees match your search query.</td></tr>`}</tbody></table></div></div>`;
  $("#checkin-search")?.addEventListener("input", (event) => { state.checkinSearch = event.target.value; renderCheckin(); }); $$("[data-checkin-id]").forEach((button) => button.addEventListener("click", () => { const id = button.dataset.checkinId; state.checkins[id] = !state.checkins[id]; localStorage.setItem(`eventkit-checkin:${state.event.id}`, JSON.stringify(Object.keys(state.checkins).filter((key) => state.checkins[key]))); setHeader(); renderCheckin(); toast(state.checkins[id] ? "Arrival recorded locally." : "Check-in undone.", "success"); }));
}

function renderCertificates() {
  $("#view-certificates").innerHTML = `<div class="view-heading"><div><p class="eyebrow">06 / Bespoke Credentials</p><h2>Credentials & Attestation</h2><p>Derive deterministic certificate codes in client memory from verified attendance and validate cryptographic manifests.</p></div><div class="heading-actions"><span class="status-badge ${state.credentials.length ? "status-good" : "status-muted"}">${state.credentials.length ? `${state.credentials.length} generated` : "Not generated"}</span></div></div><div class="credential-layout"><div class="card"><div class="card-header"><div><h3>Issue Credentials</h3><small>Signing key stays in this browser tab</small></div></div><div class="credential-form"><div class="form-row"><label class="field"><span class="field-label">Issue Scope</span><select id="credential-scope"><option value="attended">Checked-in attendees only</option><option value="included">All included attendees</option></select></label><label class="field"><span class="field-label">Role Designation</span><input id="credential-role" value="Participant" placeholder="e.g. Participant, Speaker"></label></div><label class="field"><span class="field-label">Signing Secret</span><input id="credential-secret" type="password" placeholder="Enter 16+ characters (never uploaded)"><small class="muted">Used in-memory for HMAC-SHA256 digest derivation. Leave empty for demo preview.</small></label><div class="form-row"><label class="field"><span class="field-label">Certificate Title</span><input id="certificate-title" value="Certificate of Participation"></label><label class="field"><span class="field-label">Issuing Authority</span><input id="certificate-issuer" value="${escapeHtml(state.event.organizer)}"></label></div><button class="button button-gold button-glow" data-action="generate-credentials"><span>Generate Credential Package</span><span class="arrow-icon">→</span></button><div class="verify-box"><h4>Verify Credential Code</h4><div class="verify-row"><input id="verify-code" placeholder="e.g. XXXX-XXXX-XXXX"><button class="button button-dark" data-action="verify-code">Verify</button></div><div id="verify-result" class="verify-result"></div></div></div></div><div class="card"><div class="card-header"><div><h3>Credential Preview</h3><small>First issued specimen</small></div></div><div class="card-body"><div class="cert-preview"><div class="cert-preview-inner"><span class="mini-eyebrow">${escapeHtml($("#certificate-title")?.value || "CERTIFICATE OF PARTICIPATION")}</span><h3>${escapeHtml(state.event.name)}</h3><div class="cert-seal">✦</div><p>This certificate of excellence is presented to</p><div class="cert-name">${escapeHtml(state.credentials[0]?.name || "Honored Attendee")}</div><p>in recognition of distinguished participation as <b>${escapeHtml(state.credentials[0]?.role || "Participant")}</b></p><p class="muted" style="margin-top:16px;font-family:var(--font-mono);letter-spacing:0.08em">${escapeHtml(state.credentials[0]?.code || "CODE-0000-0000")}</p></div></div>${state.credentials.length ? `<button class="button button-ghost" data-action="download-manifest" style="margin-top:16px;width:100%">Download Public Manifest (.json)</button>` : ""}</div></div></div>`;
}

function renderExports() {
  $("#view-exports").innerHTML = `<div class="view-heading"><div><p class="eyebrow">07 / Deliverables Suite</p><h2>Your Ready Event Kit</h2><p>Sovereign data deliverables generated in real-time from current browser tab memory.</p></div><div class="heading-actions"><button class="button button-gold button-glow" data-action="download-all"><span>Download Workspace Snapshot</span><span class="arrow-icon">→</span></button></div></div><div class="export-notice"><span>✦</span><span>Zero-Telemetry Guarantee: All exports are generated in your local JavaScript runtime. No attendee details are transmitted over the network.</span></div><div class="export-grid"><article class="card export-card"><span class="export-icon">♧</span><h3>Clean Roster</h3><p>Every attendee row, status code, stable ID, and issue diagnosis in standard CSV.</p><button class="button button-ghost" data-action="download-roster">Download roster.csv</button></article><article class="card export-card"><span class="export-icon">!</span><h3>Review Findings</h3><p>Human-readable markdown audit summary with precise recommendations.</p><button class="button button-ghost" data-action="download-issues">Download issues.md</button></article><article class="card export-card"><span class="export-icon">◷</span><h3>Calendar Feed</h3><p>Valid sessions formatted as standard RFC-5545 iCalendar format.</p><button class="button button-ghost" data-action="download-ics">Download schedule.ics</button></article><article class="card export-card"><span class="export-icon">✓</span><h3>Arrival Telemetry</h3><p>Immutable appendable JSONL event log for multi-door attendance merging.</p><button class="button button-ghost" data-action="download-checkin-log">Download checkin.log</button></article><article class="card export-card"><span class="export-icon">◇</span><h3>Credential Manifest</h3><p>Public verification hashes and role attestations. Emails excluded for privacy.</p><button class="button button-ghost" data-action="download-manifest">Download manifest.json</button></article><article class="card export-card"><span class="export-icon">✦</span><h3>Printable SVG Specimen</h3><p>High-resolution vector certificate specimen for immediate presentation printing.</p><button class="button button-ghost" data-action="download-certificate">Download first SVG</button></article></div>`;
}

function renderActiveView() {
  setHeader();
  renderOverview();
  renderRoster();
  renderSchedule();
  renderCheckin();
  renderCertificates();
  renderExports();

  const isOverview = state.activeView === "overview";
  const setupBanner = $("#setup-banner");
  const importPanel = $("#import-panel");
  if (setupBanner) {
    setupBanner.classList.toggle("is-hidden", !isOverview || Boolean(state.attendees.length));
  }
  if (importPanel) {
    importPanel.classList.toggle("is-hidden", !isOverview || Boolean(state.attendees.length));
  }
  document.body.setAttribute("data-active-view", state.activeView);

  $$("[data-view-panel]").forEach((panel) => panel.classList.toggle("is-hidden", panel.dataset.viewPanel !== state.activeView));
  $$(".nav-item, #workspace-nav .nav-pill").forEach((item) => item.classList.toggle("is-active", item.dataset.view === state.activeView));
}

function showView(view) {
  state.activeView = view || "overview";
  document.body.setAttribute("data-active-view", state.activeView);
  renderActiveView();
  window.scrollTo({ top: 0, behavior: "instant" });
  $("#sidebar")?.classList.remove("is-open");
}
window.showView = showView;

function loadTables(registrationText, attendanceText = "", scheduleText = "") {
  const registrationTable = parseCSV(registrationText); if (!registrationTable.headers.length) { toast("Add a registrations CSV first.", "error"); return false; }
  const result = validateRegistrations(registrationTable); state.registrations = registrationTable.rows; state.attendees = result.attendees; state.attendance = []; state.attendanceIssues = []; state.sessions = []; state.scheduleIssues = [];
  if (attendanceText.trim()) { const attendanceResult = parseAttendance(parseCSV(attendanceText), state.attendees); state.attendance = attendanceResult.attendance; state.attendanceIssues = attendanceResult.issues; }
  if (scheduleText.trim()) { const scheduleResult = validateSchedule(parseCSV(scheduleText)); state.sessions = scheduleResult.sessions; state.scheduleIssues = scheduleResult.issues; }
  state.attendees.forEach((item) => { if (!state.checkins[item.id]) state.checkins[item.id] = false; }); state.lastRun = new Date(); $("#setup-banner").classList.add("is-hidden"); $("#import-panel").classList.add("is-hidden"); $("#import-status").textContent = `${state.attendees.length} registrations analyzed`; toast(`Analysis complete · ${state.attendees.length} registrations ready.`, "success"); renderActiveView(); return true;
}

async function readFileInput(inputId) { const file = $(inputId).files[0]; if (!file) return ""; return file.text(); }

async function runAnalysis() { setMetaFromInputs(); const registrationText = await readFileInput("#registrations-file"); const attendanceText = await readFileInput("#attendance-file"); const scheduleText = await readFileInput("#schedule-file"); if (!registrationText) { toast("Choose a registrations CSV before analyzing.", "warning"); $("#registrations-file").focus(); return; } loadTables(registrationText, attendanceText, scheduleText); }

function resetWorkspace() { Object.assign(state, { registrations: [], attendees: [], attendance: [], attendanceIssues: [], sessions: [], scheduleIssues: [], checkins: {}, credentials: [], manifest: null, lastRun: null, activeView: "overview", rosterFilter: "all", rosterSearch: "", checkinSearch: "" }); $("#setup-banner").classList.remove("is-hidden"); $("#import-panel").classList.remove("is-hidden"); $("#event-name-input").value = "Untitled event"; $("#organizer-input").value = "Your organization"; $("#event-id-input").value = "my-event-2026"; ["registrations", "attendance", "schedule"].forEach((name) => { $(`#${name}-file`).value = ""; $(`#${name}-file-name`).textContent = "No file selected"; }); renderActiveView(); toast("New workspace ready.", "success"); }

function loadDemo() { $("#event-name-input").value = "Demo Summit 2026"; $("#organizer-input").value = "Campus Tech Club"; $("#event-id-input").value = "demo-summit-2026"; setMetaFromInputs(); loadTables(DEMO_REGISTRATIONS, DEMO_ATTENDANCE, DEMO_SCHEDULE); }

function updateFileName(input, nameTarget) { const file = input.files[0]; const target = $(nameTarget); target.textContent = file ? file.name : "No file selected"; input.closest(".upload-card").classList.toggle("is-loaded", Boolean(file)); }

function base32Code(bytes) { const alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"; let value = 0n; for (let i = 0; i < 8; i += 1) value = (value << 8n) | BigInt(bytes[i]); value >>= 4n; let result = ""; for (let i = 0; i < 12; i += 1) { result = alphabet[Number(value & 31n)] + result; value >>= 5n; } return `${result.slice(0, 4)}-${result.slice(4, 8)}-${result.slice(8)}`; }

function utf8(value) { return new TextEncoder().encode(value); }

async function sha256Hex(value) { const buffer = await crypto.subtle.digest("SHA-256", utf8(value)); return [...new Uint8Array(buffer)].map((byte) => byte.toString(16).padStart(2, "0")).join(""); }

async function hmacCode(secret, attendee) { const key = await crypto.subtle.importKey("raw", utf8(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]); const payload = `cert\x1f${state.event.id}\x1f${attendee.email}\x1f${attendee.role}`; const digest = await crypto.subtle.sign("HMAC", key, utf8(payload)); return base32Code(new Uint8Array(digest)); }

function certificateSvg(record) {
  const esc = (value) => String(value || "").replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&apos;" })[character]);
  return `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="297mm" height="210mm" viewBox="0 0 297 210"><title>Certificate for ${esc(record.name)}</title><style>.system{font-family:'Times New Roman',serif}.background{fill:#fbf8f5}.border{fill:none;stroke:#c85a32;stroke-width:1.4}.border-inner{fill:none;stroke:#e8b099;stroke-width:0.6}.accent{fill:#c85a32}.eyebrow{fill:#c85a32;font-size:6.5px;font-weight:700;letter-spacing:2px}.event{fill:#1f1713;font-size:13px;font-weight:600}.body{fill:#483931;font-size:7.5px}.name{fill:#1f1713;font-size:26px;font-style:italic;font-weight:600}.role{fill:#3a7d65;font-size:11px;font-weight:600}.meta{fill:#756358;font-size:5.5px}</style><rect class="background" width="297" height="210"/><rect class="border" x="9" y="9" width="279" height="192" rx="2"/><rect class="border-inner" x="14" y="14" width="269" height="182" rx="1"/><rect class="accent" x="22" y="22" width="36" height="2" rx="1"/><rect class="accent" x="239" y="22" width="36" height="2" rx="1"/><g class="system" text-anchor="middle"><text class="eyebrow" x="148.5" y="42">${esc(record.title).toUpperCase()}</text><text class="event" x="148.5" y="58">${esc(state.event.name)}</text><line x1="110" y1="67" x2="187" y2="67" stroke="#c85a32" stroke-width="0.8"/><text class="body" x="148.5" y="88">This certificate of distinction is presented to</text><text class="name" x="148.5" y="118">${esc(record.name)}</text><text class="body" x="148.5" y="138">in recognition of distinguished participation as</text><text class="role" x="148.5" y="154">${esc(record.role)}</text></g><g class="system"><text class="meta" x="22" y="185">Presented by ${esc(record.issuer)}</text><text class="meta" x="275" y="185" text-anchor="end">Verification code: ${esc(record.code)}</text></g></svg>`;
}

async function generateCredentials() { if (!state.attendees.length) { toast("Analyze registrations before issuing credentials.", "warning"); return; } const secret = $("#credential-secret").value; if (secret && secret.length < 16) { toast("Use a signing secret of at least 16 characters.", "warning"); return; } const scope = $("#credential-scope").value; const role = $("#credential-role").value.trim() || "Participant"; const title = $("#certificate-title").value.trim() || "Certificate of Participation"; const issuer = $("#certificate-issuer").value.trim() || state.event.organizer; const recipients = state.attendees.filter((item) => item.status === "ok" && (scope === "included" || state.checkins[item.id] || item.attended)); if (!recipients.length) { toast("No eligible attendees yet. Check people in or choose all included.", "warning"); return; } const button = $("[data-action='generate-credentials']"); button.disabled = true; button.textContent = "Generating…"; try { const credentials = []; for (const attendee of recipients) { const code = secret ? await hmacCode(secret, { ...attendee, role }) : `PREV-${hashString(`${state.event.id}|${attendee.email}|${role}`)}-DEMO`; credentials.push({ event_id: state.event.id, code, role, name: attendee.name, email: attendee.email, attendee_id: attendee.id, title, issuer, code_sha256: await sha256Hex(code.replace(/-/g, "")) }); } state.credentials = credentials; state.manifest = { event_id: state.event.id, event_name: state.event.name, issuer: issuer, certificate_count: credentials.length, certificates: credentials.map(({ event_id, code, role: credentialRole, name, code_sha256 }) => ({ code_sha256, code, role: credentialRole, name })) }; renderActiveView(); toast(`${credentials.length} credential${credentials.length === 1 ? "" : "s"} generated locally.`, "success"); } catch (error) { toast(`Credential generation failed: ${error.message}`, "error"); } finally { button.disabled = false; button.innerHTML = "Generate credential package <span>→</span>"; } }

async function verifyCode() { const input = $("#verify-code"); const output = $("#verify-result"); if (!state.manifest) { output.className = "verify-result invalid"; output.textContent = "Generate or load a manifest first."; return; } const canonical = input.value.replace(/[\s-]/g, "").toUpperCase(); const hash = await sha256Hex(canonical); const entry = state.manifest.certificates.find((item) => item.code_sha256 === hash || item.code.replace(/-/g, "").toUpperCase() === canonical); if (entry) { output.className = "verify-result valid"; output.textContent = `VALID · ${entry.name} · ${entry.role}`; } else { output.className = "verify-result invalid"; output.textContent = "INVALID · code is not in this manifest"; } }

function downloadFirstCertificate() { const record = state.credentials[0]; if (!record) { toast("Generate credentials first.", "warning"); return; } downloadFile(`${slug(record.name)}-${record.code}.svg`, certificateSvg(record), "image/svg+xml;charset=utf-8"); }

function downloadManifest() { if (!state.manifest) { toast("Generate credentials first.", "warning"); return; } const safe = { ...state.manifest, certificates: state.manifest.certificates.map(({ code, ...publicRecord }) => publicRecord) }; downloadJSON(`${slug(state.event.name)}-manifest.json`, safe); }

function downloadAll() { const snapshot = { schema_version: 1, event: state.event, generated_at: "browser-session", files: { roster: rosterCSV(), issues: issuesMarkdown(), schedule: scheduleICS(), checkin: checkinLog(), manifest: state.manifest } }; downloadJSON(`${slug(state.event.name)}-eventkit-snapshot.json`, snapshot); toast("Workspace snapshot downloaded.", "success"); }
window.handleAction = handleAction;

function handleAction(action) { if (action === "focus-import") { $("#import-panel").scrollIntoView({ behavior: "smooth", block: "center" }); return; } if (action === "load-demo") { loadDemo(); return; } if (action === "reset-workspace") { resetWorkspace(); return; } if (action === "show-about") { $("#about-modal").showModal(); return; } if (action === "close-about") { $("#about-modal").close(); return; } if (action === "run-analysis") { runAnalysis(); return; } if (action === "download-roster") { downloadFile(`${slug(state.event.name)}-roster.csv`, rosterCSV(), "text/csv;charset=utf-8"); return; } if (action === "download-issues") { downloadFile(`${slug(state.event.name)}-issues.md`, issuesMarkdown()); return; } if (action === "download-ics") { if (!state.sessions.length) { toast("Add a schedule CSV first.", "warning"); return; } downloadFile(`${slug(state.event.name)}-schedule.ics`, scheduleICS(), "text/calendar;charset=utf-8"); return; } if (action === "download-checkin-log") { downloadFile(`${slug(state.event.name)}-checkin.log`, checkinLog(), "application/jsonl;charset=utf-8"); return; } if (action === "download-manifest") { downloadManifest(); return; } if (action === "download-certificate") { downloadFirstCertificate(); return; } if (action === "download-all") { downloadAll(); return; } if (action === "generate-credentials") { generateCredentials(); return; } if (action === "verify-code") { verifyCode(); } }

document.addEventListener("click", (event) => {
  const actionElement = event.target.closest("[data-action]");
  if (actionElement) handleAction(actionElement.dataset.action);
  const viewElement = event.target.closest("[data-view]");
  if (viewElement && !viewElement.classList.contains("nav-item")) showView(viewElement.dataset.view);
});
$$('.nav-item').forEach((button) => button.addEventListener("click", () => showView(button.dataset.view)));
$("#mobile-menu")?.addEventListener("click", () => $("#sidebar")?.classList.toggle("is-open"));
["registrations", "attendance", "schedule"].forEach((name) => { const input = $(`#${name}-file`); input?.addEventListener("change", () => updateFileName(input, `#${name}-file-name`)); });
$("#run-analysis")?.addEventListener("click", runAnalysis);
$("#about-modal")?.addEventListener("click", (event) => { if (event.target === $("#about-modal")) $("#about-modal").close(); });

// Pre-load demo summit data on startup so all features work immediately out of the box
try {
  loadDemo();
} catch (e) {
  renderActiveView();
}
