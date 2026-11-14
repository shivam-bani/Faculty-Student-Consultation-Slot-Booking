// CampusSync. Logins go to the Flask server (see api() below); the other data is still saved in
// THIS browser (localStorage) while the backend is being connected. When the Flask backend is
// ready for a feature, the functions in the DATA LAYER section are replaced with api() calls.
//
// Page wiring: every page loads timetable-data.js (the university timetable, as data)
// and then this file. Each page is detected by an element id (see the bottom of the file).

const USERS_KEY = "campussync_users";

function getUsers() {
  return JSON.parse(localStorage.getItem(USERS_KEY) || "[]");
}

// ---------- Server API ----------
// Calls the Flask backend (same site, so the login cookie is sent automatically). GET when there
// is no body, POST with JSON otherwise. Returns the response data, or throws an Error whose message
// is the server's explanation, ready to show to the user.
async function api(path, body) {
  const options = body === undefined ? {}
    : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  let res;
  try {
    res = await fetch("/api" + path, options);
  } catch {
    throw Object.assign(new Error("Cannot reach the server. Is it running?"), { status: 0 });
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.ok) throw Object.assign(new Error(data.error || `Server error (${res.status}).`), { status: res.status });
  return data;
}

function showMessage(text, ok) {
  const box = document.getElementById("formMsg");
  if (!box) return;
  box.textContent = text;
  box.className = "form-msg" + (ok ? " ok" : "");
}

// ---------- Sign up (students only; faculty accounts are created by the admin) ----------
const homeFor = role => ({ student: "student-dashboard.html", faculty: "requests.html", admin: "admin-faculty.html" }[role]);
const signupForm = document.getElementById("signupForm");
if (signupForm) {
  signupForm.addEventListener("submit", async function (e) {
    e.preventDefault();
    const password = signupForm.password.value;
    if (password !== signupForm.confirm.value) {
      return showMessage("Passwords do not match.", false);
    }
    try {
      // The server checks the @geu.ac.in email, the password length and duplicates, and stores a hash.
      await api("/auth/signup", { name: signupForm.name.value, email: signupForm.email.value, password });
    } catch (err) {
      return showMessage(err.message, false);
    }
    showMessage("Account created. Redirecting to login...", true);
    setTimeout(() => { window.location.href = "index.html"; }, 1200);
  });
}

// ---------- Login ----------
const loginForm = document.getElementById("loginForm");
if (loginForm) {
  // Opening the login page = logged out. Kept as a promise so a quick login waits for it,
  // otherwise the logout could arrive after the login and end the new session.
  const loggedOut = api("/auth/logout", {}).catch(() => {});
  loginForm.addEventListener("submit", async function (e) {
    e.preventDefault();
    const role = e.submitter.value;                    // "student", "faculty" or "admin"
    await loggedOut;
    try {
      const { user } = await api("/auth/login", { email: loginForm.email.value, password: loginForm.password.value, role });
      window.location.href = homeFor(user.role);
    } catch (err) {
      showMessage(err.message, false);                 // e.g. "Email or password is incorrect."
    }
  });

  const resetBtn = document.getElementById("resetData");
  if (resetBtn) resetBtn.addEventListener("click", () => {
    if (!confirm("Delete all accounts, bookings, notifications and timetable edits saved in this browser?")) return;
    Object.keys(localStorage).filter(k => k.startsWith("campussync_")).forEach(k => localStorage.removeItem(k));
    location.reload();
  });

}

// ================= DATA LAYER (replace with backend calls later) =================
const SESSIONS = "campussync_sessions", BOOKINGS = "campussync_bookings", NOTES = "campussync_notes";
const AVAIL = "campussync_availability";             // per-faculty timetable link + slot settings
const load = key => JSON.parse(localStorage.getItem(key) || "[]");
const save = (key, value) => localStorage.setItem(key, JSON.stringify(value));
const loadObj = key => JSON.parse(localStorage.getItem(key) || "{}");

const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmtDate = d => new Date(d + "T00:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
const pill = s => `<span class="pill ${s.toLowerCase().replace(/\s+/g, "")}">${s}</span>`;
const today = () => new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);
const el = id => document.getElementById(id);
const setText = (id, v) => { if (el(id)) el(id).textContent = v; };

// ---- time helpers ----
const pad = n => String(n).padStart(2, "0");
const toMin = t => { const [h, m] = t.split(":").map(Number); return h * 60 + m; };
const toHM = m => pad(Math.floor(m / 60)) + ":" + pad(m % 60);
const ymd = x => `${x.getFullYear()}-${pad(x.getMonth() + 1)}-${pad(x.getDate())}`;
const addDays = (d, n) => { const x = new Date(d + "T00:00:00"); x.setDate(x.getDate() + n); return ymd(x); };
const dowOf = d => new Date(d + "T00:00:00").getDay();          // 0 = Sunday, 1 = Monday ... 6 = Saturday
const nowHM = () => { const n = new Date(); return toHM(n.getHours() * 60 + n.getMinutes()); };
const isPast = (date, start) => date < today() || (date === today() && start <= nowHM());
const overlaps = (a1, a2, b1, b2) => toMin(a1) < toMin(b2) && toMin(b1) < toMin(a2);
const DAY_SHORT = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const DAY_LONG = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

// ---- booking status ----
// Pending (sent by student) -> Waiting (accepted; joins the queue with "I'm here") -> In Session
// (called in by the faculty member) -> Completed, or Rejected / Cancelled.
// "Waiting" is shown to students and in requests as "Accepted".
// Once a meeting's end time has passed it closes by itself: never answered -> Expired,
// accepted but never called in -> Missed, still in session -> Completed.
function effStatus(b) {
  if (b.status === "Pending" || b.status === "Waiting" || b.status === "In Session") {
    const s = load(SESSIONS).find(x => x.id === b.sessionId);
    if (s && isPast(s.date, s.end)) return { Pending: "Expired", Waiting: "Missed", "In Session": "Completed" }[b.status];
  }
  return b.status;
}
// Queue settings from the server (.env): check-in opens this many minutes before a slot; one
// priority level is added per agingMinutes of waiting. Filled in by syncFromServer().
const QUEUE = { agingMinutes: 10, checkinEarlyMinutes: 10 };
const checkInOpensAt = s => toHM(Math.max(0, toMin(s.start) - QUEUE.checkinEarlyMinutes));
const canCheckIn = b => { const s = sessionOf(b);
  return effStatus(b) === "Waiting" && !b.checkedInAt && s && s.date === today() && nowHM() >= checkInOpensAt(s); };
const isActive = b => ["Pending", "Waiting", "In Session"].includes(effStatus(b));
const holdsSeat = b => b.status !== "Cancelled" && b.status !== "Rejected";
const shownStatus = b => (effStatus(b) === "Waiting" ? "Accepted" : effStatus(b));
const statusLabel = b => (b.reschedule && isActive(b) ? "Reschedule requested" : shownStatus(b));
const statusPills = b => `<div class="pills">${pill(shownStatus(b))}${effStatus(b) === "Waiting" && b.checkedInAt ? pill("Checked in") : ""}${b.reschedule && isActive(b) ? pill("Reschedule requested") : ""}</div>`;
// Timetable slots become sessions only when booked; hide those whose bookings all moved away.
const hasBookings = s => s.source !== "timetable" || load(BOOKINGS).some(b => b.sessionId === s.id);
const sessionOf = b => load(SESSIONS).find(s => s.id === b.sessionId);
const safeSession = b => sessionOf(b) || { facultyName: "-", topic: "-", date: today(), start: "-", end: "-", capacity: 0 };
const bookedCount = s => load(BOOKINGS).filter(b => b.sessionId === s.id && holdsSeat(b)).length;
const seatsLeft = s => s.capacity - bookedCount(s);
const sessionStatus = s => (s.cancelled ? "Cancelled" : seatsLeft(s) > 0 ? "Open" : "Full");

function notify(email, title, message) {
  const all = load(NOTES);
  all.push({ email, title, message, time: new Date().toLocaleString("en-GB"), read: false });
  save(NOTES, all);
}
// The place comes from the server's queue (priority, aging, check-in time), not from the token.
function queuePosition(b) {
  const st = effStatus(b);
  if (st === "In Session") return "Now";
  if (st === "Pending") return "Awaiting approval";
  if (st !== "Waiting") return "-";
  if (b.queuePosition) return String(b.queuePosition);
  const s = sessionOf(b);
  return s && s.date === today() && nowHM() >= checkInOpensAt(s) ? "Press \"I'm here\"" : `Check in from ${s ? checkInOpensAt(s) : "-"}`;
}
function row(labels, cells) {
  return "<tr>" + cells.map((c, i) => `<td data-label="${labels[i]}">${c}</td>`).join("") + "</tr>";
}
function fill(id, html, cols, emptyText) {
  el(id).innerHTML = html || `<tr><td colspan="${cols}">${emptyText}</td></tr>`;
}
function renderNotes(me) {
  const mine = load(NOTES).filter(n => n.email === me.email).reverse();
  el("noteList").innerHTML = mine.length
    ? mine.map(n => `<article class="note ${n.read ? "read" : "unread"}"><div><strong>${esc(n.title)}</strong><p>${esc(n.message)}</p><small>${n.time}</small></div></article>`).join("")
    : '<p class="muted">No notifications yet.</p>';
  save(NOTES, load(NOTES).map(n => (n.email === me.email ? { ...n, read: true } : n)));   // opening the page marks them read
}

// ================= TIMETABLE + AVAILABILITY =================
// TIMETABLE (timetable-data.js) is the imported university timetable. The admin edits a working
// copy kept under TTS: { profileId: { name, classes: [{ id, day, start, end, code, subject, section, room }] } }.
const TT = typeof TIMETABLE !== "undefined" ? TIMETABLE.faculty : [];
const TTS = "campussync_timetable";
const DAY_START = "08:00", DAY_END = "17:55";      // college working hours used to find free time

function nameKey(n) {                                // "Dr. Neeraj Kumar Pandey" -> "neeraj-kumar-pandey"
  let k = String(n).toLowerCase().replace(/\(.*?\)/g, "").replace(/\./g, " ").replace(/\s+/g, " ").trim();
  let m;
  while ((m = k.match(/^(dr|mr|ms|mrs|prof)\b\s*/))) k = k.slice(m[0].length);
  k = k.replace(/\b([a-z]) (?=[a-z]\b)/g, "$1");     // "h s negi" -> "hs negi"
  k = k.trim().replace(/ /g, "-");
  return k === "om-prakash-pal" ? "op-pal" : k;
}
const importedProfile = f => ({ name: f.name, classes: f.classes.map((c, i) => ({ id: f.id + "-" + i,
  day: c[0], start: c[1], end: c[2], code: c[3], subject: c[4], section: c[5], room: c[6] })) });
function ttStore() {
  let st = loadObj(TTS);
  const version = typeof TIMETABLE !== "undefined" ? TIMETABLE.version || "1" : "";
  if (version && localStorage.getItem(TTS + "_version") !== version) {
    // New or corrected timetable file: reload every imported profile the admin has not edited.
    TT.forEach(f => { if (!st[f.id] || !st[f.id].edited) st[f.id] = importedProfile(f); });
    save(TTS, st); localStorage.setItem(TTS + "_version", version);
  }
  return st;
}
if (TT.length) ttStore();                             // apply timetable corrections as soon as any page loads
const ttProfiles = () => Object.entries(ttStore()).map(([id, p]) => ({ id, ...p })).sort((a, b) => nameKey(a.name).localeCompare(nameKey(b.name)));
const guessTtId = name => (ttStore()[nameKey(name)] ? nameKey(name) : "");
const linkedEmail = id => (getUsers().find(u => u.role === "faculty" && getAvail(u.email).ttId === id) || {}).email || "";
async function createFacultyAccount(name, email, password, ttId) {   // returns an error message or ""
  if (!ttId) ttId = guessTtId(name);                  // typed a name that is in the timetable: link it
  if (!ttId) {                                       // really new: start an empty timetable
    const st = ttStore(), base = nameKey(name).replace(/[^a-z0-9-]/g, "") || "faculty";
    let id = base, n = 2;
    while (st[id]) id = base + "-" + n++;
    ttId = id;
  }
  try {     // the server checks the email, the password and that the timetable is not linked yet
    await api("/admin/faculty", { name, email, password, profile_id: ttId, profile_name: ttFaculty(ttId)?.name || name });
  } catch (err) {
    return err.message;
  }
  await syncFromServer();
  notify(email, "Welcome to CampusSync", "Your faculty account was created by the admin. Student requests for your free periods appear under Booking Requests.");
  return "";
}

// Faculty accounts, timetables and consultation settings live on the server. This copies them into
// the browser's cache, which the pages still read for drawing and for the bookings that are kept in
// the browser for now. It runs on every page load and after every admin change, so the server stays
// the source of truth. It also strips passwords that older browser-only versions stored in the cache.
async function syncFromServer() {
  const [{ timetables }, { faculty }, { sessions, bookings }, queue] =
    await Promise.all([api("/timetables"), api("/faculty"), api("/bookings"), api("/queue")]);
  QUEUE.agingMinutes = queue.agingMinutes; QUEUE.checkinEarlyMinutes = queue.checkinEarlyMinutes;
  const places = Object.fromEntries(queue.entries.map(e => [e.id, e]));
  bookings.forEach(b => {                            // place in the queue, worked out by the server
    b.queuePosition = places[b.id] ? places[b.id].position : null;
    b.effectivePriority = places[b.id] ? places[b.id].effectivePriority : null;
  });
  save(SESSIONS, sessions);                          // only what this user may see (the server decides)
  save(BOOKINGS, bookings);
  const st = {};
  timetables.forEach(t => { st[t.id] = { name: t.name, edited: t.edited, classes: t.classes }; });
  save(TTS, st);
  if (typeof TIMETABLE !== "undefined") localStorage.setItem(TTS + "_version", TIMETABLE.version || "1");  // ttStore() must not reload the file over it
  const users = getUsers().filter(u => u.role === "student").map(({ password, ...u }) => u);
  faculty.forEach(f => {
    users.push({ id: f.id, name: f.name, email: f.email, role: "faculty" });
    const s = f.settings || { slot_len: 30, capacity: 3, days: [1, 2, 3, 4, 5], off: [], custom: [] };
    setAvail(f.email, { ttId: f.profile_id || "", slotLen: s.slot_len, capacity: s.capacity, days: s.days,
      off: Object.fromEntries(s.off.map(key => [key, true])), custom: s.custom });
  });
  localStorage.setItem(USERS_KEY, JSON.stringify(users));
}
const ttFaculty = id => (id && ttStore()[id]) || null;

function getAvail(email) {
  return Object.assign({ ttId: "", slotLen: 30, capacity: 3, days: [1, 2, 3, 4, 5], off: {}, custom: [] }, loadObj(AVAIL)[email] || {});
}
function setAvail(email, a) {
  const all = loadObj(AVAIL); all[email] = a; save(AVAIL, all);
}
function classesOf(email) {
  const f = ttFaculty(getAvail(email).ttId);
  return f ? f.classes.slice() : [];
}
function subjectsOf(email) {
  return [...new Set(classesOf(email).filter(c => c.code !== "Busy").map(c => `${c.code} - ${c.subject}`))].sort();
}
function classClash(email, day, start, end) {        // first class that overlaps, or undefined
  return classesOf(email).find(c => c.day === day && overlaps(c.start, c.end, start, end));
}

// Weekly pattern: free gaps between classes, cut into slots of slotLen minutes, plus extra slots.
function weeklySlots(email) {
  const a = getAvail(email), cls = classesOf(email), out = [];
  const custom = a.custom.map(c => ({ key: "c|" + c.id, id: c.id, day: c.day, start: c.start, end: c.end, capacity: c.capacity, kind: "custom", off: false }));
  a.days.forEach(d => {                              // no timetable yet = no classes, so working hours are free
    const busy = cls.filter(c => c.day === d).map(c => [toMin(c.start), toMin(c.end)]).sort((x, y) => x[0] - y[0]);
    let t = toMin(DAY_START);
    const gaps = [];
    busy.forEach(([s, e]) => { if (s > t) gaps.push([t, s]); t = Math.max(t, e); });
    if (toMin(DAY_END) > t) gaps.push([t, toMin(DAY_END)]);
    gaps.forEach(([gs, ge]) => {
      for (let s = gs; s + a.slotLen <= ge; s += a.slotLen) {
        const start = toHM(s), end = toHM(s + a.slotLen), key = d + "|" + start;
        if (custom.some(c => c.day === d && overlaps(c.start, c.end, start, end))) continue;
        out.push({ key, day: d, start, end, capacity: a.capacity, kind: "auto", off: !!a.off[key] });
      }
    });
  });
  return out.concat(custom).sort((x, y) => x.day - y.day || toMin(x.start) - toMin(y.start));
}

// What students can book with a faculty member (the next 14 days), worked out by the server from
// MySQL: timetable slots not yet turned into sessions, plus future sessions with seats left.
async function serverSlots(email) {
  const f = getUsers().find(u => u.role === "faculty" && u.email === email);
  if (!f) return [];
  const { slots } = await api(`/faculty/${f.id}/slots`);
  return slots.map(s => ({ ref: s.ref, facultyEmail: email, date: s.date, start: s.start, end: s.end, capacity: s.capacity,
    seats: s.seats, topic: s.topic, ...(s.session_id ? { sessionId: String(s.session_id) } : {}) }));
}
const slotText = s => `${DAY_SHORT[dowOf(s.date)]} ${fmtDate(s.date)} · ${s.start} - ${s.end} (${s.seats} seat${s.seats === 1 ? "" : "s"} left)`;
function slotOptions(list, skipSessionId) {
  const byDate = {};
  list.filter(s => s.sessionId !== skipSessionId || !skipSessionId).forEach(s => (byDate[s.date] = byDate[s.date] || []).push(s));
  return Object.keys(byDate).map(d => `<optgroup label="${DAY_LONG[dowOf(d)]}, ${fmtDate(d)}">` +
    byDate[d].map(s => `<option value="${esc(s.ref)}">${s.start} - ${s.end}${s.topic !== "Consultation hours" ? " · " + esc(s.topic) : ""} (${s.seats} left)</option>`).join("") + "</optgroup>").join("");
}

// Bookings are created and changed only by the server (bookings.py); pages call the API and then
// reload their copy with syncFromServer().
const whenText = s => `${fmtDate(s.date)}, ${s.start} - ${s.end}`;

// ---- shared dialog (used for reschedule / reject / cancel / details) ----
function openDialog({ title, body, confirm, danger, onConfirm, onOpen, extra }) {
  let d = el("csDialog");
  if (!d) { d = document.createElement("dialog"); d.id = "csDialog"; d.className = "dialog"; document.body.appendChild(d); }
  d.innerHTML = `<form class="dialog-form" novalidate><h2>${esc(title)}</h2><div class="dialog-body">${body}</div>
    <p class="form-msg" id="dlgMsg" role="alert"></p>
    <div class="form-actions">${confirm ? `<button type="submit" class="btn ${danger ? "danger" : "primary"}">${esc(confirm)}</button>` : ""}
    ${extra ? `<button type="button" class="btn danger" data-extra>${esc(extra.text)}</button>` : ""}
    <button type="button" class="btn" data-close>Close</button></div></form>`;
  const form = d.querySelector("form");
  d.querySelector("[data-close]").addEventListener("click", () => d.close());
  // onConfirm / extra.onClick return an error message or "" (or a promise of one, for server calls).
  if (extra) d.querySelector("[data-extra]").addEventListener("click", async () => {
    const err = await extra.onClick(form);
    if (err) { d.querySelector("#dlgMsg").textContent = err; return; }
    d.close();
  });
  form.addEventListener("submit", async e => {
    e.preventDefault();
    const err = onConfirm ? await onConfirm(form) : "";
    if (err) { d.querySelector("#dlgMsg").textContent = err; return; }
    d.close();
  });
  if (onOpen) onOpen(form);
  d.showModal();
}

function studentDetailsHTML(b) {
  const st = b.student || {}, s = safeSession(b);
  const items = [["Name", st.name || b.studentName], ["Student ID", st.studentId], ["University roll no.", st.rollNo],
    ["Course", st.course], ["Semester / section", [st.semester, st.section].filter(Boolean).join(" / ")], ["PBL group ID", st.groupId],
    ["Topic", b.topic], ["Description", b.desc], ["Session", whenText(s)], ["Token", "#" + b.token], ["Email", b.studentEmail]];
  const hist = (b.history || []).map(h => `<li>${new Date(h.at).toLocaleString("en-GB")}: ${esc(h.text)}</li>`).join("");
  return `<dl class="kv">${items.map(([k, v]) => `<dt>${k}</dt><dd>${esc(v || "-")}</dd>`).join("")}</dl>` + (hist ? `<h3 class="kv-h">History</h3><ul class="hist">${hist}</ul>` : "");
}

// ---- weekly timetable grid (faculty edits it, students only view it) ----
function clusterClasses(list) {                       // merge combined / overlapping classes into one block
  const out = [];
  list.slice().sort((a, b) => toMin(a.start) - toMin(b.start)).forEach(c => {
    const last = out[out.length - 1];
    if (last && toMin(c.start) < toMin(last.end)) { last.items.push(c); if (toMin(c.end) > toMin(last.end)) last.end = c.end; }
    else out.push({ start: c.start, end: c.end, items: [c] });
  });
  return out.map(k => ({ ...k,
    codes: [...new Set(k.items.map(i => i.code))].join(", "),
    where: [...new Set(k.items.map(i => i.section))].join("; "),
    title: k.items.map(i => `${i.code} ${i.subject} | ${i.section}${i.room ? " | " + i.room : ""} | ${i.start}-${i.end}`).join("\n") }));
}
function renderWeek(box, email, editable) {
  const a = getAvail(email), cls = classesOf(email);
  const slots = weeklySlots(email).filter(s => editable || !s.off);
  const days = [1, 2, 3, 4, 5, 6].filter(d => d <= 5 || a.days.includes(d) || cls.some(c => c.day === d) || slots.some(s => s.day === d));
  const PPM = 1.15, T0 = toMin("08:00"), T1 = toMin("18:00"), H = Math.round((T1 - T0) * PPM);
  const pos = (s, e) => `top:${Math.round((toMin(s) - T0) * PPM)}px;height:${Math.max(14, Math.round((toMin(e) - toMin(s)) * PPM) - 2)}px`;
  let hours = "";
  for (let h = 8; h <= 18; h++) hours += `<span style="top:${Math.round((h * 60 - T0) * PPM)}px">${pad(h)}:00</span>`;
  const cols = days.map(d => {
    const dayCls = cls.filter(c => c.day === d);
    const cb = editable   // admin: every class entry is its own button; overlapping ones sit side by side
      ? clusterClasses(dayCls).map(k => k.items.map((c, i) =>
          `<button type="button" class="blk class${c.code === "Busy" ? " busy" : ""}" data-class="${esc(c.id)}" style="${pos(c.start, c.end)};left:${(100 * i / k.items.length).toFixed(2)}%;right:auto;width:${(100 / k.items.length).toFixed(2)}%" title="${esc(`${c.code} ${c.subject} | ${c.section} | ${c.room} | ${c.start}-${c.end}. Select to edit.`)}"><strong>${esc(c.code)}</strong><span>${esc(c.section || c.subject)}</span></button>`).join("")).join("")
      : clusterClasses(dayCls).map(c =>
      `<div class="blk class${c.codes === "Busy" ? " busy" : ""}" style="${pos(c.start, c.end)}" title="${esc(c.title)}"><strong>${esc(c.codes)}</strong><span>${esc(c.where)}</span></div>`).join("");
    const sb = slots.filter(s => s.day === d).map(s => {
      const label = `${s.start}-${s.end}`;
      if (!editable) return `<div class="blk free${s.kind === "custom" ? " custom" : ""}" style="${pos(s.start, s.end)}" title="${label}, up to ${s.capacity} students">${label}</div>`;
      if (s.kind === "custom") return `<button type="button" class="blk free custom" data-cid="${s.id}" style="${pos(s.start, s.end)}" title="Extra slot for ${s.capacity} students. Select to remove it.">${label}</button>`;
      return `<button type="button" class="blk free${s.off ? " off" : ""}" data-key="${s.key}" aria-pressed="${!s.off}" style="${pos(s.start, s.end)}" title="${s.off ? "Not offered. Select to offer it." : "Offered to students. Select to stop offering it."}">${label}</button>`;
    }).join("");
    return `<div class="week-day"><div class="week-name"><span class="long">${DAY_LONG[d]}</span><span class="short">${DAY_SHORT[d]}</span></div>
      <div class="week-col" style="height:${H}px;background-size:100% ${Math.round(60 * PPM)}px">${cb}${sb}</div></div>`;
  }).join("");
  box.innerHTML = `<div class="week-scroll"><div class="week-inner" style="--cols:${days.length}"><div class="week-times" style="height:${H}px">${hours}</div>${cols}</div></div>`;
}

// ================= PAGES =================
function studentDashboard(me) {
  const mine = load(BOOKINGS).filter(b => b.studentEmail === me.email).reverse();
  const L = ["Token", "Faculty", "Topic", "Date", "Time", "Status"];
  fill("myAppts", mine.map(b => { const s = safeSession(b);
    return row(L, ["#" + b.token, esc(s.facultyName), esc(b.topic), fmtDate(s.date), `${s.start} - ${s.end}`, pill(statusLabel(b))]); }).join(""),
    6, "No appointments yet. Book a consultation to get started.");
}

function appointmentsPage(me) {
  function draw() {
    const mine = load(BOOKINGS).filter(b => b.studentEmail === me.email)
      .sort((x, y) => { const a = safeSession(x), b = safeSession(y); return (a.date + a.start).localeCompare(b.date + b.start); });
    const L = ["Token", "Faculty", "Topic", "Date & Time", "Queue Position", "Status", "Actions"];
    const build = (list, withActions) => list.map(b => { const s = safeSession(b);
      let acts = "";
      if (withActions) {
        const btns = [];
        if (canCheckIn(b)) btns.push(`<button type="button" class="btn small primary" data-act="checkin" data-id="${b.id}">I'm here</button>`);
        if (b.status === "Pending" || b.status === "Waiting") {
          btns.push(b.reschedule ? `<button type="button" class="btn small" data-act="withdraw" data-id="${b.id}">Withdraw change</button>`
                                 : `<button type="button" class="btn small" data-act="resched" data-id="${b.id}">Request reschedule</button>`);
          btns.push(`<button type="button" class="btn small danger" data-act="cancel" data-id="${b.id}">Cancel booking</button>`);
        }
        acts = `<div class="actions">${btns.join("") || '<span class="muted">-</span>'}</div>`;
      }
      const when = `${fmtDate(s.date)}, ${s.start}` + (b.reschedule ? `<span class="sub">Asked for: ${esc(b.reschedule.label)}</span>` : "");
      const cells = ["#" + b.token, esc(s.facultyName), esc(b.topic), `<div class="cell">${when}</div>`, queuePosition(b), statusPills(b)];
      return row(L, withActions ? cells.concat(acts) : cells);
    }).join("");
    fill("upcomingAppts", build(mine.filter(isActive), true), 7, "No upcoming appointments.");
    fill("previousAppts", build(mine.filter(b => !isActive(b)).reverse(), false), 6, "No previous appointments.");
  }

  // Each action is sent to the server; then the page reloads the server's data and redraws.
  el("upcomingAppts").addEventListener("click", async e => {
    const btn = e.target.closest("button[data-act]");
    if (!btn) return;
    const b = load(BOOKINGS).find(x => x.id === btn.dataset.id), s = safeSession(b);
    if (btn.dataset.act === "checkin") {             // joins the queue; waiting time counts from now
      btn.disabled = true;
      try {
        const { position } = await api(`/bookings/${b.id}/check-in`, {});
        showMessage(`You are checked in. Your place in the queue: ${position}.`, true);
      } catch (err) { showMessage(err.message, false); }
      await syncFromServer();
      draw();
    }
    if (btn.dataset.act === "resched") {
      const opts = slotOptions(await serverSlots(s.facultyEmail).catch(() => []), b.sessionId);
      openDialog({ title: "Request a new time", confirm: "Send request",
        body: `<p class="muted">Current slot: ${whenText(s)} with ${esc(s.facultyName)}. The faculty member approves or declines the change.</p>
          <div class="field"><label for="dlgSlot">New time</label><select id="dlgSlot" name="slot">${opts ? '<option value="">Choose a slot</option>' + opts : '<option value="">No other open slots</option>'}</select></div>
          <div class="field"><label for="dlgReason">Reason</label><textarea id="dlgReason" name="reason" placeholder="e.g. Clashes with my lab exam"></textarea></div>`,
        onConfirm: async f => {
          if (!f.slot.value) return "Choose the new time you want.";
          let label;
          try {
            ({ reschedule: { label } } = await api(`/bookings/${b.id}/reschedule`, { ref: f.slot.value, reason: f.reason.value.trim() }));
          } catch (err) { return err.message; }
          await syncFromServer();
          notify(s.facultyEmail, "Reschedule request", `${b.studentName} (token #${b.token}) asked to move from ${whenText(s)} to ${label}.` + (f.reason.value.trim() ? ` Reason: ${f.reason.value.trim()}` : ""));
          draw(); return "";
        } });
    }
    if (btn.dataset.act === "withdraw") {
      try { await api(`/bookings/${b.id}/reschedule/withdraw`, {}); } catch (err) { alert(err.message); }
      await syncFromServer();
      draw();
    }
    if (btn.dataset.act === "cancel") {
      openDialog({ title: "Cancel this booking?", confirm: "Cancel booking", danger: true,
        body: `<p>${esc(b.topic)} with ${esc(s.facultyName)} on ${whenText(s)}. Your seat is released for other students.</p>`,
        onConfirm: async () => {
          try { await api(`/bookings/${b.id}/cancel`, {}); } catch (err) { return err.message; }
          await syncFromServer();
          notify(s.facultyEmail, "Booking cancelled", `${b.studentName} cancelled token #${b.token} (${whenText(s)}).`);
          draw(); return "";
        } });
    }
  });
  draw();
  autoRefresh(draw);                                   // the queue place changes as others are called in
}

// Reload from the server every 15 seconds while the page is visible and no dialog is open.
function autoRefresh(draw, seconds = 15) {
  setInterval(async () => {
    if (document.hidden || document.querySelector("dialog[open]")) return;
    try { await syncFromServer(); draw(); } catch { /* try again next time */ }
  }, seconds * 1000);
}

function bookPage(me) {
  const form = el("bookForm");
  const faculty = getUsers().filter(u => u.role === "faculty");
  const params = new URLSearchParams(location.search);
  form.faculty.innerHTML = faculty.length
    ? '<option value="">Select a faculty member</option>' + faculty.map(f => `<option value="${esc(f.email)}">${esc(f.name)}</option>`).join("")
    : '<option value="">No faculty registered yet</option>';

  // Prefill the student's details from their saved profile (one profile per student, on the server).
  form.fullName.value = me.name;
  api("/profile").then(({ profile }) => {
    if (profile) ["studentId", "rollNo", "course", "semester", "section", "groupId"].forEach(k => { if (profile[k]) form[k].value = profile[k]; });
  }).catch(() => { /* the details can still be typed in */ });

  let slots = [];                                      // the server's bookable slots for the chosen faculty member
  async function showSlots() {
    slots = form.faculty.value ? await serverSlots(form.faculty.value).catch(() => []) : [];
    form.slot.innerHTML = !form.faculty.value ? '<option value="">Choose a faculty first</option>'
      : slots.length ? '<option value="">Select a time slot</option>' + slotOptions(slots) : '<option value="">No open slots for this faculty</option>';
    form.date.value = ""; form.capacity.value = "";
  }
  form.faculty.addEventListener("change", showSlots);
  form.slot.addEventListener("change", () => {
    const s = slots.find(x => x.ref === form.slot.value);
    form.date.value = s ? `${DAY_LONG[dowOf(s.date)]}, ${fmtDate(s.date)}` : "";
    form.capacity.value = s ? `${s.seats} of ${s.capacity} seats available` : "";
  });
  if (params.get("faculty")) {                         // arriving from Available Slots
    form.faculty.value = params.get("faculty");
    showSlots().then(() => {
      if (params.get("slot")) { form.slot.value = params.get("slot"); form.slot.dispatchEvent(new Event("change")); }
    });
  }

  form.addEventListener("submit", async e => {
    e.preventDefault();
    const ref = form.slot.value, slot = slots.find(x => x.ref === ref);
    if (!ref || !slot) return showMessage("Please choose a time slot.", false);
    const student = { studentId: form.studentId.value.trim().toUpperCase(), rollNo: form.rollNo.value.trim().toUpperCase(),
                      course: form.course.value, semester: form.semester.value, section: form.section.value.trim().toUpperCase(),
                      groupId: form.groupId.value.trim().toUpperCase() };
    let booking;
    try {
      // One locked transaction on the server: checks seats, an existing booking and time clashes,
      // gives the next token and works out the priority from the semester and consultation type.
      ({ booking } = await api("/bookings", { ref, topic: form.topic.value, desc: form.desc.value,
                                              consultationType: form.ctype.value, profile: student }));
    } catch (err) {
      return showMessage(err.message, false);           // e.g. "This session is full."
    }
    const fac = faculty.find(f => f.email === form.faculty.value), token = booking.token;
    notify(me.email, "Booking request sent", `Your request to ${fac.name} for ${whenText(slot)} is waiting for approval. Your token is #${token}.`);
    notify(fac.email, "New booking request", `${me.name} (${student.rollNo}, ${student.course}) requested "${form.topic.value.trim()}" on ${whenText(slot)} (token #${token}).`);
    if (slot.seats <= 1) notify(fac.email, "Capacity reached", `${whenText(slot)} is now fully booked.`);
    showMessage(`Request sent. Your token is #${token}. Redirecting...`, true);
    setTimeout(() => { window.location.href = "appointments.html"; }, 1200);
  });
}

function slotsPage(me) {
  const pick = el("slotFaculty"), dayPick = el("slotDay");
  const faculty = getUsers().filter(u => u.role === "faculty");
  pick.innerHTML = faculty.length ? faculty.map(f => `<option value="${esc(f.email)}">${esc(f.name)}</option>`).join("")
                                  : '<option value="">No faculty registered yet</option>';
  async function draw() {
    const email = pick.value;
    if (!email) {
      el("weekGrid").innerHTML = '<p class="muted">Slots appear here once faculty members create their accounts.</p>';
      setText("slotInfo", ""); dayPick.innerHTML = ""; return fill("slotRows", "", 5, "No slots to show.");
    }
    setText("slotInfo", classesOf(email).length ? "Grey blocks are this faculty member's classes. Highlighted times are open for consultation."
      : "No timetable has been entered for this faculty member yet, so all working hours are shown. The faculty member accepts or reschedules each request.");
    renderWeek(el("weekGrid"), email, false);
    const list = await serverSlots(email).catch(() => []);
    if (pick.value !== email) return;                  // another faculty member was chosen meanwhile
    const dates = [...new Set(list.map(s => s.date))];
    const keep = dayPick.value;
    dayPick.innerHTML = '<option value="">All dates</option>' + dates.map(d => `<option value="${d}">${DAY_SHORT[dowOf(d)]} ${fmtDate(d)} (${list.filter(s => s.date === d).length})</option>`).join("");
    dayPick.value = dates.includes(keep) ? keep : (dates[0] || "");
    const shown = list.filter(s => !dayPick.value || s.date === dayPick.value);
    const L = ["Date", "Day", "Time", "Seats left", "Action"];
    fill("slotRows", shown.map(s => row(L, [fmtDate(s.date), DAY_LONG[dowOf(s.date)], `${s.start} - ${s.end}` + (s.topic !== "Consultation hours" ? `<span class="sub">${esc(s.topic)}</span>` : ""),
      `${s.seats} / ${s.capacity}`, `<a class="btn small primary" href="book.html?faculty=${encodeURIComponent(email)}&slot=${encodeURIComponent(s.ref)}">Book</a>`])).join(""),
      5, "No open slots in the next 14 days.");
  }
  pick.addEventListener("change", () => { dayPick.value = ""; draw(); });
  dayPick.addEventListener("change", draw);
  draw();
}

// ---- faculty: own timetable, read-only (the admin maintains it) ----
function facultyTimetablePage(me) {
  const f = ttFaculty(getAvail(me.email).ttId), a = getAvail(me.email);
  const offered = weeklySlots(me.email).filter(s => !s.off).length;
  setText("ttSummary", (classesOf(me.email).length ? `${classesOf(me.email).length} classes and busy periods` : "No classes entered yet, so all working hours are open to students. Ask the admin to enter your timetable")
    + ` · ${offered} consultation slots offered each week (${a.slotLen} minutes, up to ${a.capacity} students)`);
  setText("ttSubjects", subjectsOf(me.email).length ? "Subjects: " + subjectsOf(me.email).join(", ") : "");
  renderWeek(el("weekGrid"), me.email, false);
}

// Bookings that now overlap a class (after the admin changed the timetable).
function clashingBookings(email) {
  const mine = load(SESSIONS).filter(s => s.facultyEmail === email && !s.cancelled).map(s => s.id);
  return load(BOOKINGS).filter(b => mine.includes(b.sessionId) && isActive(b)).filter(b => {
    const s = sessionOf(b); return classClash(email, dowOf(s.date), s.start, s.end);
  });
}
function timetableChanged(email, what) {
  const clash = clashingBookings(email);
  notify(email, "Timetable updated", `The admin ${what}.` + (clash.length
    ? ` ${clash.length} booked meeting(s) now clash with a class. Reschedule them under Booking Requests.` : ""));
  return clash.length;
}

// ================= ADMIN =================
const facultyUsers = () => getUsers().filter(u => u.role === "faculty").sort((a, b) => nameKey(a.name).localeCompare(nameKey(b.name)));
const openFor = email => { const ids = load(SESSIONS).filter(s => s.facultyEmail === email).map(s => s.id); return load(BOOKINGS).filter(b => ids.includes(b.sessionId) && isActive(b)); };

function adminAccountPage(me) {
  setText("accEmail", me.email);
  const pf = el("pwForm");
  pf.addEventListener("submit", async e => {
    e.preventDefault();
    if (pf.next.value !== pf.confirm.value) return showMessage("New passwords do not match.", false);
    try {
      // The server checks the current password against its hash and the new password's length.
      await api("/auth/password", { current: pf.current.value, new: pf.next.value });
    } catch (err) {
      return showMessage(err.message, false);
    }
    pf.reset(); showMessage("Password changed.", true);
  });
}

function adminFacultyPage(me) {
  const form = el("addFacultyForm"), search = el("ttSearch");
  const sectionsOf = p => [...new Set(p.classes.flatMap(c => String(c.section || "").split(";").flatMap(x => {
    x = x.trim(); const m = x.match(/^(B\.Tech CSE V-)(.+)$/);   // "B.Tech CSE V-A, B" -> two sections
    return m ? m[2].split(",").map(y => m[1] + y.trim()) : [x];
  })).filter(Boolean))].sort();

  function drawAccounts() {
    const L = ["Name", "Email", "Timetable", "Open requests", "Actions"];
    fill("facultyRows", facultyUsers().map(u => {
      const p = ttFaculty(getAvail(u.email).ttId);
      const tt = !p ? '<span class="sub warn">Not linked</span>'
        : !p.classes.length ? '<span class="sub warn">No classes yet</span>'
        : `${p.classes.length} entries` + (nameKey(p.name) !== nameKey(u.name) ? `<span class="sub warn">Linked to ${esc(p.name)}</span>` : "");
      return row(L, [`<strong>${esc(u.name)}</strong>`, esc(u.email), `<div class="cell">${tt}</div>`, String(openFor(u.email).length),
        `<div class="actions"><a class="btn small" href="admin-timetable.html?f=${encodeURIComponent(u.email)}">Edit timetable</a><button type="button" class="btn small" data-link="${esc(u.email)}">Link timetable</button><button type="button" class="btn small" data-reset="${esc(u.email)}">Reset password</button><button type="button" class="btn small danger" data-remove="${esc(u.email)}">Remove</button></div>`]);
    }).join(""), 5, "No faculty accounts yet. Create them from the timetable list below.");
  }
  function drawDirectory() {
    const q = nameKey(search.value || "");
    const list = ttProfiles().filter(p => TT.some(f => f.id === p.id) && (!q || p.id.includes(q) || p.classes.some(c => c.code.toLowerCase().includes(search.value.trim().toLowerCase()))));
    setText("ttCount", `${list.length} of ${TT.length} faculty from the university timetable`);
    const L = ["Name", "Subjects", "Sections", "Classes", "Account"];
    fill("ttRows", list.map(p => {
      const subs = [...new Set(p.classes.map(c => c.code))].filter(c => c !== "Busy"), secs = sectionsOf(p), acc = linkedEmail(p.id);
      return row(L, [`<strong>${esc(p.name)}</strong>`, `<div class="cell">${esc(subs.join(", ")) || "-"}</div>`,
        `<div class="cell"><span class="sub">${esc(secs.slice(0, 3).join("; "))}${secs.length > 3 ? ` and ${secs.length - 3} more` : ""}</span></div>`, String(p.classes.length),
        acc ? `<span class="muted">${esc(acc)}</span>` : `<button type="button" class="btn small primary" data-create="${esc(p.id)}">Create account</button>`]);
    }).join(""), 5, "No faculty match your search.");
  }
  const draw = () => { drawAccounts(); drawDirectory(); };
  search.addEventListener("input", drawDirectory);

  const accountFields = (name, extra = "") => `${extra}<div class="field"><label for="dName">Full name</label><input id="dName" name="name" value="${esc(name)}" required></div>
    <div class="field"><label for="dEmail">College email</label><input id="dEmail" name="email" type="email" placeholder="name@geu.ac.in" required></div>
    <div class="field"><label for="dPass">Temporary password</label><input id="dPass" name="password" type="text" minlength="8" placeholder="At least 8 characters" required></div>`;

  document.querySelector("main").addEventListener("click", e => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.create) {
      const p = ttFaculty(b.dataset.create);
      openDialog({ title: `Create account for ${p.name}`, confirm: "Create account",
        body: accountFields(p.name, `<p class="muted">${p.classes.length} classes from the university timetable are linked to this account.</p>`),
        onConfirm: async f => {
          if (!f.name.value.trim() || !f.email.checkValidity() || !f.email.value) return "Enter the name and a valid email.";
          const err = await createFacultyAccount(f.name.value.trim(), f.email.value.trim().toLowerCase(), f.password.value, p.id);
          if (err) return err;
          showMessage(`Account created for ${p.name}. They log in with ${f.email.value.trim().toLowerCase()} and the temporary password, using "Login as Faculty".`, true);
          draw(); return "";
        } });
    }
    if (b.dataset.link) {
      const u = getUsers().find(x => x.email === b.dataset.link), cur = getAvail(u.email).ttId;
      const opts = ttProfiles().filter(p => p.id === cur || !linkedEmail(p.id)).map(p => `<option value="${esc(p.id)}"${p.id === cur ? " selected" : ""}>${esc(p.name)}${TT.some(f => f.id === p.id) ? "" : " (entered by admin)"}</option>`).join("");
      openDialog({ title: `Link ${u.name} to a timetable`, confirm: "Save link",
        body: `<div class="field"><label for="dLink">Name in the university timetable</label><select id="dLink" name="link">${opts}</select></div>`,
        onConfirm: async f => {
          // The server also clears the "not offered" marks, which belonged to the old timetable.
          try { await api(`/admin/faculty/${u.id}/link`, { profile_id: f.link.value }); } catch (err) { return err.message; }
          await syncFromServer();
          timetableChanged(u.email, "linked your account to the timetable of " + ttFaculty(f.link.value).name);
          showMessage(`${u.name} now uses the timetable of ${ttFaculty(f.link.value).name}.`, true); draw(); return "";
        } });
    }
    if (b.dataset.reset) {
      const u = getUsers().find(x => x.email === b.dataset.reset);
      openDialog({ title: `Reset password for ${u.name}`, confirm: "Set password",
        body: `<div class="field"><label for="dPass">New temporary password</label><input id="dPass" name="password" type="text" minlength="8" placeholder="At least 8 characters"></div>`,
        onConfirm: async f => {
          try { await api(`/admin/faculty/${u.id}/password`, { password: f.password.value }); } catch (err) { return err.message; }
          showMessage(`Password reset for ${u.email}.`, true); return "";
        } });
    }
    if (b.dataset.remove) {
      const u = getUsers().find(x => x.email === b.dataset.remove), open = openFor(u.email);
      openDialog({ title: `Remove ${u.name}?`, confirm: "Remove faculty", danger: true,
        body: `<p>The account is deactivated and students can no longer book this faculty member. Past meetings keep their name.</p><p>${open.length} open request(s) or meeting(s) will be cancelled and the students notified. The timetable stays, so the account can be created again later.</p>`,
        onConfirm: async () => {
          let res;                                          // the server cancels the open bookings itself
          try { res = await api(`/admin/faculty/${u.id}/deactivate`, {}); } catch (err) { return err.message; }
          open.filter(bk => res.cancelled.includes(bk.id)).forEach(bk => { const s = safeSession(bk);
            notify(bk.studentEmail, "Booking cancelled", `${s.facultyName} is no longer available on CampusSync, so your meeting on ${whenText(s)} was cancelled. Please book another faculty member.`); });
          const av = loadObj(AVAIL); delete av[u.email]; save(AVAIL, av);
          await syncFromServer();
          showMessage(`${u.name} removed. ${res.cancelled.length} booking(s) cancelled.`, true); draw(); return "";
        } });
    }
  });

  form.addEventListener("submit", async e => {         // faculty who are not in the timetable files
    e.preventDefault();
    const name = form.name.value.trim(), email = form.email.value.trim().toLowerCase();
    const match = guessTtId(name);
    const err = await createFacultyAccount(name, email, form.password.value, "");
    if (err) return showMessage(err, false);
    form.reset();
    showMessage(match ? `${name} is in the university timetable, so the account was linked to those classes.`
                      : `${name} added with an empty timetable. Enter their classes under Timetables.`, true);
    draw();
  });
  draw();
}

function adminTimetablePage(me) {
  const pick = el("admFaculty"), sf = el("slotSettings"), ef = el("entryForm");
  const list = facultyUsers();
  pick.innerHTML = list.length ? list.map(u => `<option value="${esc(u.email)}">${esc(u.name)}</option>`).join("") : '<option value="">No faculty accounts yet</option>';
  const want = new URLSearchParams(location.search).get("f");
  if (want && list.some(u => u.email === want)) pick.value = want;
  const email = () => pick.value;
  const facultyId = () => (facultyUsers().find(u => u.email === email()) || {}).id;
  const profileId = () => getAvail(email()).ttId;    // every faculty account is linked to a timetable on the server
  // Sends one change to the server, then reloads the server's data. Returns an error message or "".
  const send = async (path, body) => {
    try { await api(path, body); } catch (err) { return err.message; }
    await syncFromServer();
    return "";
  };

  function draw() {
    if (!email()) { el("weekGrid").innerHTML = '<p class="muted">Add a faculty member first.</p>'; return; }
    const a = getAvail(email()), id = profileId(), p = ttFaculty(id);
    if (!p) { el("weekGrid").innerHTML = '<p class="muted">Link this account to a timetable under Faculty first.</p>'; return; }
    sf.slotLen.value = String(a.slotLen); sf.defCap.value = a.capacity;
    sf.querySelectorAll('input[name="days"]').forEach(c => { c.checked = a.days.includes(Number(c.value)); });
    const offered = weeklySlots(email()).filter(s => !s.off).length;
    setText("ttSummary", `${p.classes.length} timetable entries · ${offered} consultation slots offered each week`);
    el("restoreTT").hidden = !TT.some(f => f.id === id);
    const clash = clashingBookings(email()).length;
    setText("clashNote", clash ? `${clash} booked meeting(s) clash with this timetable. The faculty member has been asked to reschedule them.` : "");
    renderWeek(el("weekGrid"), email(), true);
  }

  pick.addEventListener("change", () => { history.replaceState(null, "", "?f=" + encodeURIComponent(email())); draw(); });

  sf.addEventListener("submit", async e => {
    e.preventDefault();
    const days = [...sf.querySelectorAll('input[name="days"]:checked')].map(c => Number(c.value));
    if (!days.length) return showMessage("Choose at least one consultation day.", false);
    const slotLen = Number(sf.slotLen.value);   // a new slot length also clears the "not offered" marks (server)
    const err = await send(`/admin/faculty/${facultyId()}/settings`, { slot_len: slotLen, capacity: Number(sf.defCap.value), days });
    if (err) return showMessage(err, false);
    notify(email(), "Consultation hours updated", `The admin set your consultation slots to ${slotLen} minutes for up to ${sf.defCap.value} students.`);
    showMessage("Slot settings saved.", true); draw();
  });

  ef.type.addEventListener("change", () => {
    const consult = ef.type.value === "consult";
    ef.querySelectorAll(".for-class").forEach(x => { x.hidden = consult; });
    ef.querySelectorAll(".for-consult").forEach(x => { x.hidden = !consult; });
  });
  ef.addEventListener("submit", async e => {
    e.preventDefault();
    if (!email()) return showMessage("Add a faculty member first.", false);
    const day = Number(ef.day.value), start = ef.start.value, end = ef.end.value;
    if (!start || !end || end <= start) return showMessage("End time must be after start time.", false);
    if (ef.type.value === "consult") {
      const a = getAvail(email());
      if (classClash(email(), day, start, end) && !confirm("This overlaps a class. Add the consultation slot anyway?")) return;
      const err = await send(`/admin/faculty/${facultyId()}/custom-slots`, { day, start, end, capacity: Number(ef.capacity.value) || a.capacity });
      if (err) return showMessage(err, false);
      notify(email(), "Consultation hours updated", `The admin added consultation hours on ${DAY_LONG[day]}, ${start} - ${end}.`);
      showMessage(`Consultation slot added on ${DAY_LONG[day]}.`, true);
    } else {
      const busy = ef.type.value === "busy";
      if (!busy && !ef.code.value.trim()) return showMessage("Enter the subject code.", false);
      const err = await send(`/admin/timetables/${profileId()}/classes`, { day, start, end, code: busy ? "Busy" : ef.code.value.trim().toUpperCase(),
        subject: ef.subject.value.trim() || (busy ? "Not available" : ""), section: ef.section.value.trim(), room: ef.room.value.trim() });
      if (err) return showMessage(err, false);
      const n = timetableChanged(email(), `added ${busy ? "a busy period" : "a class"} on ${DAY_LONG[day]}, ${start} - ${end}`);
      showMessage(`Added to ${DAY_LONG[day]}.` + (n ? ` ${n} booked meeting(s) now clash; the faculty member was notified.` : ""), !n);
    }
    ef.start.value = ""; ef.end.value = ""; ef.code.value = ""; ef.subject.value = ""; ef.section.value = ""; ef.room.value = "";
    draw();
  });

  el("weekGrid").addEventListener("click", async e => {
    const btn = e.target.closest("button.blk");
    if (!btn) return;
    const a = getAvail(email());
    if (btn.dataset.key) {                             // offer / stop offering a free slot
      const [day, start] = btn.dataset.key.split("|");
      const err = await send(`/admin/faculty/${facultyId()}/slots-off`, { day: Number(day), start, off: !a.off[btn.dataset.key] });
      if (err) return showMessage(err, false);
      draw();
      const again = el("weekGrid").querySelector(`[data-key="${btn.dataset.key}"]`); if (again) again.focus();
      return;
    }
    if (btn.dataset.cid) {
      if (!confirm("Remove this extra consultation slot? Existing bookings are kept.")) return;
      const err = await send(`/admin/custom-slots/${btn.dataset.cid}/delete`, {});
      if (err) return showMessage(err, false);
      draw();
      return;
    }
    const c = classesOf(email()).find(x => x.id === btn.dataset.class);
    if (!c) return;
    const dayOpts = [1, 2, 3, 4, 5, 6].map(d => `<option value="${d}"${d === c.day ? " selected" : ""}>${DAY_LONG[d]}</option>`).join("");
    openDialog({ title: `Edit ${c.code}`, confirm: "Save changes",
      body: `<div class="form-grid"><div class="field"><label for="dDay">Day</label><select id="dDay" name="day">${dayOpts}</select></div>
        <div class="field"><label for="dCode">Code</label><input id="dCode" name="code" value="${esc(c.code)}"></div>
        <div class="field"><label for="dStart">Start time</label><input id="dStart" name="start" type="time" value="${c.start}"></div>
        <div class="field"><label for="dEnd">End time</label><input id="dEnd" name="end" type="time" value="${c.end}"></div>
        <div class="field wide"><label for="dSub">Subject</label><input id="dSub" name="subject" value="${esc(c.subject)}"></div>
        <div class="field"><label for="dSec">Section</label><input id="dSec" name="section" value="${esc(c.section)}"></div>
        <div class="field"><label for="dRoom">Room</label><input id="dRoom" name="room" value="${esc(c.room)}"></div></div>`,
      onConfirm: async f => {
        if (!f.code.value.trim()) return "Enter a code.";
        if (!f.start.value || !f.end.value || f.end.value <= f.start.value) return "End time must be after start time.";
        const err = await send(`/admin/classes/${c.id}`, { day: Number(f.day.value), start: f.start.value, end: f.end.value,
          code: f.code.value.trim(), subject: f.subject.value.trim(), section: f.section.value.trim(), room: f.room.value.trim() });
        if (err) return err;
        const n = timetableChanged(email(), `changed ${c.code} (${DAY_SHORT[c.day]} ${c.start}) to ${DAY_LONG[Number(f.day.value)]} ${f.start.value} - ${f.end.value}`);
        showMessage("Timetable entry updated." + (n ? ` ${n} booked meeting(s) now clash; the faculty member was notified.` : ""), !n);
        draw(); return "";
      },
      extra: { text: "Remove entry", onClick: async () => {
        const err = await send(`/admin/classes/${c.id}/delete`, {});
        if (err) return err;
        timetableChanged(email(), `removed ${c.code} on ${DAY_LONG[c.day]}, ${c.start} - ${c.end}. That time is now open for consultations`);
        showMessage(`${c.code} removed. The freed time is now offered to students.`, true); draw(); return "";
      } } });
  });

  el("restoreTT").addEventListener("click", async () => {
    const id = profileId();
    if (!TT.some(f => f.id === id) || !confirm("Replace this faculty member's timetable with the imported university timetable? Your edits to it will be lost.")) return;
    const err = await send(`/admin/timetables/${id}/restore`, {});   // edited flag cleared: future corrections apply
    if (err) return showMessage(err, false);
    timetableChanged(email(), "restored your timetable to the official university timetable");
    showMessage("Imported timetable restored.", true); draw();
  });
  el("offerAll").addEventListener("click", async () => {
    const err = await send(`/admin/faculty/${facultyId()}/slots-off/clear`, {});
    if (err) return showMessage(err, false);
    draw();
  });
  draw();
}

function requestsPage(me) {
  function draw() {
    const mineIds = load(SESSIONS).filter(s => s.facultyEmail === me.email).map(s => s.id);
    const list = load(BOOKINGS).filter(b => mineIds.includes(b.sessionId))
      .sort((x, y) => { const a = safeSession(x), b = safeSession(y); return (a.date + a.start).localeCompare(b.date + b.start) || x.token - y.token; });
    const open = list.filter(isActive);
    const L = ["Student", "Course / Subject", "Topic", "Session", "Status", "Actions"];
    const cells = b => {
      const st = b.student || {}, s = safeSession(b);
      return [
        `<div class="cell"><strong>${esc(st.name || b.studentName)}</strong><span class="sub">${st.studentId ? "ID " + esc(st.studentId) : esc(b.studentEmail)}${st.rollNo ? " · Roll " + esc(st.rollNo) : ""}</span></div>`,
        `<div class="cell">${esc(st.course || "-")}${st.semester ? " " + esc(st.semester) : ""}${st.section ? "-" + esc(st.section) : ""}${st.groupId ? `<span class="sub">Group ${esc(st.groupId)}</span>` : ""}</div>`,
        `<div class="cell">${esc(b.topic)}<button type="button" class="linkish" data-act="details" data-id="${b.id}">Details</button></div>`,
        `<div class="cell">${fmtDate(s.date)}, ${s.start} - ${s.end}<span class="sub">Token #${b.token}</span>${b.reschedule && isActive(b) ? `<span class="sub ask">Asks for: ${esc(b.reschedule.label)}</span>` : ""}${(() => { const cl = isActive(b) && classClash(me.email, dowOf(s.date), s.start, s.end); return cl ? `<span class="sub warn">Clashes with ${esc(cl.code)} (${cl.start} - ${cl.end})</span>` : ""; })()}</div>`,
        statusPills(b)];
    };
    const actions = b => {
      const btn = (act, text, cls = "") => `<button type="button" class="btn small ${cls}" data-act="${act}" data-id="${b.id}">${text}</button>`;
      const out = [];
      if (b.reschedule) out.push(btn("approve", "Approve new time", "primary"), btn("decline", "Keep old time"));
      if (b.status === "Pending") out.push(btn("accept", "Accept", b.reschedule ? "" : "primary"), btn("reject", "Reject", "danger"), btn("move", "Reschedule"));
      if (b.status === "Waiting") out.push(btn("move", "Reschedule"), btn("cancel", "Cancel", "danger"));
      return `<div class="actions">${out.join("")}</div>`;
    };
    fill("openReqs", open.map(b => row(L, cells(b).concat(actions(b)))).join(""), 6, "No open requests. New student bookings appear here.");
    fill("closedReqs", list.filter(b => !isActive(b)).reverse().map(b => row(L.slice(0, 5), cells(b))).join(""), 5, "No closed requests yet.");
  }

  // Every action is done by the server; the page then reloads its copy and tells the student.
  async function send(b, action, body) {
    const res = await api(`/faculty/bookings/${b.id}/${action}`, body || {});
    await syncFromServer();
    return res;
  }
  function notifyMoved(b, res, note) {
    const t = load(SESSIONS).find(x => x.id === res.booking.sessionId);
    notify(b.studentEmail, "Consultation rescheduled", `Your consultation with ${t.facultyName} is now on ${whenText(t)}. New token #${res.booking.token}.` + (note ? ` Note: ${note}` : ""));
  }

  async function moveDialog(b) {
    const s = safeSession(b);
    let opts;
    try { opts = slotOptions(await serverSlots(me.email), b.sessionId); } catch (err) { showMessage(err.message, false); return; }
    openDialog({ title: `Reschedule ${b.studentName}`, confirm: "Reschedule",
      body: `<p class="muted">Now: ${whenText(s)}, token #${b.token}. Rescheduling also accepts a pending request.</p>
        <div class="field"><label for="dlgSlot">Move to</label><select id="dlgSlot" name="slot"><option value="">Choose a slot</option>${opts}<option value="custom">Another date and time...</option></select></div>
        <div class="form-grid" id="dlgCustom" hidden><div class="field wide"><label for="dlgDate">Date</label><input id="dlgDate" name="date" type="date" min="${today()}"></div>
          <div class="field"><label for="dlgStart">Start time</label><input id="dlgStart" name="start" type="time"></div>
          <div class="field"><label for="dlgEnd">End time</label><input id="dlgEnd" name="end" type="time"></div></div>
        <div class="field"><label for="dlgNote">Message to the student (optional)</label><textarea id="dlgNote" name="note"></textarea></div>`,
      onOpen: f => f.slot.addEventListener("change", () => { f.querySelector("#dlgCustom").hidden = f.slot.value !== "custom"; }),
      onConfirm: async f => {
        const note = f.note.value.trim(), body = { ref: f.slot.value, note };
        if (!body.ref) return "Choose where to move the booking.";
        if (body.ref === "custom") {                   // the server checks these again
          const date = f.date.value, start = f.start.value, end = f.end.value;
          if (!date || !start || !end) return "Fill in the date, start and end time.";
          if (end <= start) return "End time must be after start time.";
          if (isPast(date, start)) return "Choose a time in the future.";
          const cl = classClash(me.email, dowOf(date), start, end);
          if (cl && !confirm(`This overlaps your class ${cl.code} (${cl.start} - ${cl.end}). Use this time anyway?`)) return "Pick a time that does not clash with your class.";
          Object.assign(body, { ref: "", date, start, end });  // the server makes a one-student session at that time
        }
        let res;
        try { res = await send(b, "move", body); } catch (err) { return err.message; }
        notifyMoved(b, res, note);
        showMessage(`${b.studentName} moved and notified.`, true); draw(); return "";
      } });
  }
  function reasonDialog(b, kind) {
    const s = safeSession(b), reject = kind === "reject";
    openDialog({ title: reject ? "Reject this request?" : "Cancel this booking?", confirm: reject ? "Reject request" : "Cancel booking", danger: true,
      body: `<p>${esc(b.studentName)}, ${esc(b.topic)}, ${whenText(s)}.</p><div class="field"><label for="dlgNote">Reason (sent to the student)</label><textarea id="dlgNote" name="note" placeholder="${reject ? "e.g. Please discuss this with your project mentor first" : "e.g. I am on official duty that day"}"></textarea></div>`,
      onConfirm: async f => {
        const note = f.note.value.trim();
        try { await send(b, kind, { note }); } catch (err) { return err.message; }
        notify(b.studentEmail, reject ? "Request rejected" : "Booking cancelled", `${s.facultyName} ${reject ? "rejected your request" : "cancelled your booking"} for ${whenText(s)} (token #${b.token}).` + (note ? ` Reason: ${note}` : "") + " You can book another slot.");
        draw(); return "";
      } });
  }

  document.querySelector("main").addEventListener("click", async e => {
    const btn = e.target.closest("button[data-act]");
    if (!btn || !btn.dataset.id) return;
    const b = load(BOOKINGS).find(x => x.id === btn.dataset.id), s = safeSession(b);
    try {
      switch (btn.dataset.act) {
        case "details": return openDialog({ title: "Booking details", body: studentDetailsHTML(b) });
        case "accept":
          await send(b, "accept");
          notify(b.studentEmail, "Request accepted", `${s.facultyName} accepted your consultation on ${whenText(s)}. Token #${b.token}.`);
          showMessage(`Accepted ${b.studentName}.`, true); return draw();
        case "reject": return reasonDialog(b, "reject");
        case "cancel": return reasonDialog(b, "cancel");
        case "move": return await moveDialog(b);
        case "approve": {
          let res;
          try { res = await send(b, "approve-reschedule"); }
          catch (err) { showMessage(err.message + " Use Reschedule to pick a different slot.", false); return; }
          notifyMoved(b, res, "");
          showMessage(`Moved ${b.studentName} to the requested time.`, true); return draw();
        }
        case "decline":
          await send(b, "decline-reschedule");
          notify(b.studentEmail, "Reschedule declined", `${s.facultyName} kept your consultation on ${whenText(s)} (token #${b.token}).`);
          return draw();
      }
    } catch (err) { showMessage(err.message, false); await syncFromServer(); draw(); }
  });
  draw();
}

// ---- live queue (faculty): the order comes from the server's scheduler (queue_manager.py) ----
function queuePage(me) {
  const select = el("queueSession"), button = el("callNext");
  const inSlot = s => load(BOOKINGS).filter(b => b.sessionId === s.id && holdsSeat(b) && b.status !== "Pending");
  const queueOpen = s => s.date === today() && nowHM() >= checkInOpensAt(s) && !isPast(s.date, s.end);

  function draw() {
    // Sessions from today on that have accepted students; keep the choice across refreshes.
    const list = load(SESSIONS).filter(s => s.facultyEmail === me.email && !s.cancelled && s.date >= today() && inSlot(s).length)
      .sort((x, y) => (x.date + x.start).localeCompare(y.date + y.start));
    const chosen = list.find(s => s.id === select.value) || list.find(s => !isPast(s.date, s.end)) || list[0];
    select.innerHTML = list.length
      ? list.map(s => `<option value="${s.id}">${DAY_SHORT[dowOf(s.date)]} ${fmtDate(s.date)}, ${s.start} - ${s.end}${s.topic !== "Consultation hours" ? " · " + esc(s.topic) : ""}</option>`).join("")
      : '<option value="">No accepted bookings yet</option>';
    if (chosen) select.value = chosen.id;
    const bookings = chosen ? inSlot(chosen) : [];
    const now = bookings.find(b => b.status === "In Session" && effStatus(b) === "In Session");
    const waiting = bookings.filter(b => b.queuePosition && effStatus(b) === "Waiting").sort((x, y) => x.queuePosition - y.queuePosition);
    const open = chosen && queueOpen(chosen);

    el("queueInfo").textContent = !chosen ? "" : open
      ? `Queue open until ${chosen.end}. Priority rises by 1 for every ${QUEUE.agingMinutes} minute(s) of waiting.`
      : isPast(chosen.date, chosen.end) ? "This session has ended."
      : `The queue opens at ${checkInOpensAt(chosen)} on ${fmtDate(chosen.date)}, when students can start pressing "I'm here".`;
    el("nowServing").innerHTML = now ? `<strong>In session now:</strong> ${esc(now.studentName)} (token #${now.token})` : "";
    button.dataset.current = now ? now.id : "";
    button.textContent = waiting.length ? "Call next" : now ? "Finish current student" : "Call next";
    button.disabled = !open || (!waiting.length && !now);

    const who = b => `<div class="cell"><strong>${esc(b.studentName)}</strong><span class="sub">${b.student && b.student.studentId ? "ID " + esc(b.student.studentId) : esc(b.studentEmail)}</span></div>`;
    const kind = b => `<div class="cell">${esc(b.consultationType || "General")}<span class="sub">Semester ${esc((b.student && b.student.semester) || "-")}</span></div>`;
    const clock = ms => new Date(ms).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
    const L = ["Position", "Student", "Token", "Consultation", "Checked in", "Waiting", "Priority"];
    fill("queueRows", waiting.map(b => row(L, [String(b.queuePosition), who(b), "#" + b.token, kind(b), clock(b.checkedInAt),
      `${Math.floor((Date.now() - b.checkedInAt) / 60000)} min`,
      `<div class="cell">${pill(b.basePriority === 2 ? "High" : "Normal")}<span class="sub">Now ${b.effectivePriority} (base ${b.basePriority} + ${b.effectivePriority - b.basePriority} for waiting)</span></div>`])).join(""),
      7, "Nobody is waiting. Students appear here after pressing \"I'm here\".");
    const others = bookings.filter(b => !waiting.includes(b)).sort((x, y) => x.token - y.token);
    const L2 = ["Student", "Token", "Consultation", "Status"];
    fill("otherRows", others.map(b => row(L2, [who(b), "#" + b.token, kind(b),
      effStatus(b) === "Waiting" ? pill("Accepted") + '<span class="sub">Not checked in yet</span>' : pill(shownStatus(b))])).join(""),
      4, "No other students in this session.");
  }

  select.addEventListener("change", draw);
  button.addEventListener("click", async () => {
    button.disabled = true;                            // one click = one call (the server also checks)
    try {
      const res = await api(`/faculty/sessions/${select.value}/call-next`, { current: button.dataset.current || null });
      const s = load(SESSIONS).find(x => x.id === select.value);
      if (res.called) {
        notify(res.called.email, "It's your turn", `${me.name} is ready for you now (${whenText(s)}, token #${res.called.token}).`);
        showMessage(`Called in ${res.called.name} (token #${res.called.token}).`, true);
      } else showMessage(res.completed ? `Finished with ${res.completed.name}. Nobody else is waiting.` : "Nobody is waiting.", true);
    } catch (err) { showMessage(err.message, false); }
    await syncFromServer();
    draw();
  });
  draw();
  autoRefresh(draw);                                   // new check-ins and aging show up without reloading
}

// ---- calendar (students: their meetings; faculty: sessions with bookings) ----
function calendarEvents(me) {
  const shortName = n => String(n).replace(/^(Dr|Mr|Ms|Mrs|Prof)\.?\s*/i, "");
  if (me.role === "student") {
    return load(BOOKINGS).filter(b => b.studentEmail === me.email && holdsSeat(b) && effStatus(b) !== "Expired" && sessionOf(b)).map(b => { const s = sessionOf(b);
      return { date: s.date, start: s.start, end: s.end, title: s.facultyName, short: shortName(s.facultyName), sub: `${b.topic} · Token #${b.token}`, label: statusLabel(b) }; });
  }
  const bookings = load(BOOKINGS);
  return load(SESSIONS).filter(s => s.facultyEmail === me.email && !s.cancelled).map(s => {
    const bk = bookings.filter(b => b.sessionId === s.id && holdsSeat(b) && effStatus(b) !== "Expired");
    if (!bk.length && s.source === "timetable") return null;
    const pend = bk.filter(b => effStatus(b) === "Pending").length;
    const label = pend ? "Pending" : bk.length && bk.every(b => ["Completed", "Missed"].includes(effStatus(b))) ? "Completed" : bk.length ? "Accepted" : "Open";
    return { date: s.date, start: s.start, end: s.end, title: s.topic, short: bk.length ? `${bk.length} student${bk.length > 1 ? "s" : ""}` : s.topic,
             sub: bk.length ? bk.map(b => b.studentName).join(", ") + (pend ? ` (${pend} awaiting approval)` : "") : "No bookings yet", label };
  }).filter(Boolean);
}
function calendarPage(me) {
  const t = new Date();
  let y = t.getFullYear(), m = t.getMonth(), picked = null;
  function draw() {
    const all = calendarEvents(me).sort((a, b) => (a.date + a.start).localeCompare(b.date + b.start));
    const first = new Date(y, m, 1), offset = (first.getDay() + 6) % 7, daysIn = new Date(y, m + 1, 0).getDate(), ym = `${y}-${pad(m + 1)}`;
    setText("calTitle", first.toLocaleDateString("en-GB", { month: "long", year: "numeric" }));
    let html = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map(d => `<div class="cal-head" aria-hidden="true">${d}</div>`).join("");
    for (let i = 0; i < offset; i++) html += '<div class="cal-cell blank" aria-hidden="true"></div>';
    for (let d = 1; d <= daysIn; d++) {
      const date = `${ym}-${pad(d)}`, list = all.filter(e => e.date === date);
      const cls = ["cal-cell", date === today() ? "today" : "", date === picked ? "picked" : "", list.length ? "has" : ""].join(" ");
      html += `<button type="button" class="${cls}" data-date="${date}" aria-pressed="${date === picked}" aria-label="${fmtDate(date)}, ${list.length} meeting${list.length === 1 ? "" : "s"}">
        <span class="cal-num">${d}</span>${list.slice(0, 2).map(e => `<span class="cal-ev ${e.label.toLowerCase().replace(/\s+/g, "")}">${e.start} ${esc(e.short)}</span>`).join("")}
        ${list.length > 2 ? `<span class="cal-more">+${list.length - 2} more</span>` : ""}${list.length ? `<span class="cal-dot">${list.length}</span>` : ""}</button>`;
    }
    el("calGrid").innerHTML = html;
    const shown = all.filter(e => (picked ? e.date === picked : e.date.startsWith(ym)));
    setText("calListTitle", picked ? `Meetings on ${fmtDate(picked)}` : "Meetings this month");
    el("calList").innerHTML = shown.length ? shown.map(e => `<li><div class="cal-when"><strong>${DAY_SHORT[dowOf(e.date)]} ${fmtDate(e.date)}</strong><span>${e.start} - ${e.end}</span></div>
      <div class="cal-what"><strong>${esc(e.title)}</strong><span class="muted">${esc(e.sub)}</span></div>${pill(e.label)}</li>`).join("")
      : `<li class="muted">${picked ? "No meetings on this day." : "No meetings this month."}</li>`;
  }
  el("calPrev").addEventListener("click", () => { m--; if (m < 0) { m = 11; y--; } picked = null; draw(); });
  el("calNext").addEventListener("click", () => { m++; if (m > 11) { m = 0; y++; } picked = null; draw(); });
  el("calGrid").addEventListener("click", e => {
    const c = e.target.closest("button[data-date]");
    if (c) { picked = picked === c.dataset.date ? null : c.dataset.date; draw(); }
  });
  draw();
}

function navBadge(me) {                               // unread count next to "Notifications"
  const link = document.querySelector('.sidebar a[href$="notifications.html"]');
  const n = load(NOTES).filter(x => x.email === me.email && !x.read).length;
  if (link && n) link.insertAdjacentHTML("beforeend", ` <span class="badge" aria-label="${n} unread">${n}</span>`);
}

// ================= Protected pages: show the logged-in name, block wrong role =================
// The server says who is logged in (from the signed cookie), so editing localStorage cannot
// change the user or the role.
const requiredRole = document.body.dataset.role;
if (requiredRole) (async () => {
  let me = null;
  try { me = (await api("/auth/me")).user; } catch { /* not logged in, or server down */ }
  if (!me || me.role !== requiredRole) {
    window.location.href = "index.html";
  } else {
    try { await syncFromServer(); } catch { /* keep the cached list if the server is briefly unavailable */ }
    document.querySelectorAll("[data-user-name]").forEach(n => { n.textContent = me.name; });
    if (el("myAppts")) studentDashboard(me);
    if (el("upcomingAppts")) appointmentsPage(me);
    if (el("bookForm")) bookPage(me);
    if (el("slotRows")) slotsPage(me);
    if (el("ttView")) facultyTimetablePage(me);
    if (el("openReqs")) requestsPage(me);
    if (el("queueRows")) queuePage(me);
    if (el("pwForm")) adminAccountPage(me);
    if (el("addFacultyForm")) adminFacultyPage(me);
    if (el("entryForm")) adminTimetablePage(me);
    if (el("calGrid")) calendarPage(me);
    if (el("noteList")) renderNotes(me);
    navBadge(me);
  }
})();
