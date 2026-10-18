// CampusSync demo (no backend). Everything is saved in THIS browser only (localStorage).
// Not secure - for demonstration only. When the Flask backend is ready, replace the
// functions in the DATA LAYER section below with fetch() calls to the backend.
//
// Page wiring: every page loads timetable-data.js (the university timetable, as data)
// and then this file. Each page is detected by an element id (see the bottom of the file).

const USERS_KEY = "campussync_users";
const CURRENT_KEY = "campussync_current";

function getUsers() {
  return JSON.parse(localStorage.getItem(USERS_KEY) || "[]");
}
function getCurrent() {
  return JSON.parse(localStorage.getItem(CURRENT_KEY) || "null");
}
// A built-in admin account exists from the first visit, so there is always someone who can add
// faculty. Change its password on the Admin Dashboard. (Demo only: real logins need a backend.)
const DEFAULT_ADMIN = { name: "CampusSync Admin", email: "admin@campussync.edu", password: "Admin@1234", role: "admin" };
(function ensureAdmin() {
  const users = getUsers();
  if (!users.some(u => u.role === "admin")) { users.push(DEFAULT_ADMIN); localStorage.setItem(USERS_KEY, JSON.stringify(users)); }
})();

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
  signupForm.addEventListener("submit", function (e) {
    e.preventDefault();
    const name = signupForm.name.value.trim();
    const email = signupForm.email.value.trim().toLowerCase();
    const password = signupForm.password.value;

    if (password !== signupForm.confirm.value) {
      return showMessage("Passwords do not match.", false);
    }
    const users = getUsers();
    if (users.some(u => u.email === email)) {
      return showMessage("This email is already registered. Please log in.", false);
    }
    users.push({ name, email, password, role: "student" });
    localStorage.setItem(USERS_KEY, JSON.stringify(users));
    showMessage("Account created. Redirecting to login...", true);
    setTimeout(() => { window.location.href = "index.html"; }, 1200);
  });
}

// ---------- Login ----------
const loginForm = document.getElementById("loginForm");
if (loginForm) {
  localStorage.removeItem(CURRENT_KEY);               // opening the login page = logged out
  loginForm.addEventListener("submit", function (e) {
    e.preventDefault();
    const role = e.submitter.value;                    // "student", "faculty" or "admin"
    const email = loginForm.email.value.trim().toLowerCase();
    const password = loginForm.password.value;

    const account = getUsers().find(u => u.email === email);
    if (!account) return showMessage(role === "student" ? "No account with this email. Sign up first."
      : role === "faculty" ? "No faculty account with this email. Ask the admin to create one under Admin > Faculty."
      : "No admin account with this email. Use the admin email given in README.md.", false);
    if (account.password !== password) return showMessage("Wrong password for " + email + ".", false);
    const user = account;
    if (user.role !== role) return showMessage("This email belongs to a " + user.role + " account. Use the \"Login as " + user.role.charAt(0).toUpperCase() + user.role.slice(1) + "\" button.", false);

    localStorage.setItem(CURRENT_KEY, JSON.stringify({ name: user.name, email: user.email, role: user.role }));
    window.location.href = homeFor(role);
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
// Pending (sent by student) -> Waiting (accepted, in the queue) -> In Session -> Completed
// or Rejected / Cancelled. "Waiting" is shown to students and in requests as "Accepted".
// Once a meeting's end time has passed it closes by itself: accepted -> Completed, never answered -> Expired.
function effStatus(b) {
  if (b.status === "Pending" || b.status === "Waiting" || b.status === "In Session") {
    const s = load(SESSIONS).find(x => x.id === b.sessionId);
    if (s && isPast(s.date, s.end)) return b.status === "Pending" ? "Expired" : "Completed";
  }
  return b.status;
}
const isActive = b => ["Pending", "Waiting", "In Session"].includes(effStatus(b));
const holdsSeat = b => b.status !== "Cancelled" && b.status !== "Rejected";
const shownStatus = b => (effStatus(b) === "Waiting" ? "Accepted" : effStatus(b));
const statusLabel = b => (b.reschedule && isActive(b) ? "Reschedule requested" : shownStatus(b));
const statusPills = b => `<div class="pills">${pill(shownStatus(b))}${b.reschedule && isActive(b) ? pill("Reschedule requested") : ""}</div>`;
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
function queuePosition(b) {
  if (b.status === "In Session") return "Now";
  if (b.status === "Pending" && isActive(b)) return "Awaiting approval";
  if (b.status !== "Waiting" || !isActive(b)) return "-";
  const ahead = load(BOOKINGS).filter(x => x.sessionId === b.sessionId && x.status === "Waiting" && x.token <= b.token);
  return String(ahead.length);
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
function createFacultyAccount(name, email, password, ttId) {   // returns an error message or ""
  const users = getUsers();
  if (users.some(u => u.email === email)) return "An account with this email already exists.";
  if (password.length < 8) return "The temporary password needs at least 8 characters.";
  if (!ttId) ttId = guessTtId(name);                  // typed a name that is in the timetable: link it
  if (ttId && linkedEmail(ttId)) return `That timetable is already linked to ${linkedEmail(ttId)}.`;
  if (!ttId) {                                       // really new: start an empty timetable
    const st = ttStore(); let id = nameKey(name) || "faculty", n = 2;
    while (st[id]) id = nameKey(name) + "-" + n++;
    st[id] = { name, classes: [], edited: true }; save(TTS, st); ttId = id;
  }
  users.push({ name, email, password, role: "faculty" });
  localStorage.setItem(USERS_KEY, JSON.stringify(users));
  setAvail(email, { ...getAvail(email), ttId });
  notify(email, "Welcome to CampusSync", "Your faculty account was created by the admin. Student requests for your free periods appear under Booking Requests.");
  return "";
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

// Bookable slots for one faculty member: timetable slots for the next nDays that are not yet
// turned into sessions, plus every future session (created or from the timetable) with seats left.
function upcomingSlots(email, nDays = 14) {
  const sessions = load(SESSIONS).filter(s => s.facultyEmail === email);
  const live = sessions.filter(s => !s.cancelled);
  const used = new Set(sessions.map(s => s.origKey).filter(Boolean));
  const weekly = weeklySlots(email).filter(w => !w.off);
  const out = [];
  for (let i = 0; i < nDays; i++) {
    const date = addDays(today(), i), dw = dowOf(date);
    weekly.filter(w => w.day === dw).forEach(w => {
      if (isPast(date, w.start) || used.has(date + "|" + w.start)) return;
      if (live.some(s => s.date === date && overlaps(s.start, s.end, w.start, w.end))) return;
      out.push({ ref: `v:${email}|${date}|${w.start}|${w.end}|${w.capacity}`, facultyEmail: email, date, start: w.start, end: w.end, capacity: w.capacity, seats: w.capacity, topic: "Consultation hours" });
    });
  }
  live.filter(s => !isPast(s.date, s.start)).forEach(s => {
    const seats = seatsLeft(s);
    if (seats > 0) out.push({ ref: "s:" + s.id, facultyEmail: email, date: s.date, start: s.start, end: s.end, capacity: s.capacity, seats, topic: s.topic, sessionId: s.id });
  });
  return out.sort((x, y) => (x.date + x.start).localeCompare(y.date + y.start));
}
const slotText = s => `${DAY_SHORT[dowOf(s.date)]} ${fmtDate(s.date)} · ${s.start} - ${s.end} (${s.seats} seat${s.seats === 1 ? "" : "s"} left)`;
function slotOptions(list, skipSessionId) {
  const byDate = {};
  list.filter(s => s.sessionId !== skipSessionId || !skipSessionId).forEach(s => (byDate[s.date] = byDate[s.date] || []).push(s));
  return Object.keys(byDate).map(d => `<optgroup label="${DAY_LONG[dowOf(d)]}, ${fmtDate(d)}">` +
    byDate[d].map(s => `<option value="${esc(s.ref)}">${s.start} - ${s.end}${s.topic !== "Consultation hours" ? " · " + esc(s.topic) : ""} (${s.seats} left)</option>`).join("") + "</optgroup>").join("");
}

// Turn a slot reference into a real session record (timetable slots become sessions on first booking).
function ensureSession(ref) {
  if (ref.startsWith("s:")) {
    const s = load(SESSIONS).find(x => x.id === ref.slice(2));
    return s && !s.cancelled ? s : null;
  }
  const [email, date, start, end, cap] = ref.slice(2).split("|");
  if (!upcomingSlots(email).some(x => x.ref === ref)) return null;     // turned off, taken or in the past
  const fac = getUsers().find(u => u.email === email);
  const s = { id: "s" + Date.now() + Math.floor(Math.random() * 1000), facultyEmail: email, facultyName: fac ? fac.name : email,
              topic: "Consultation hours", desc: "From the faculty member's weekly timetable", date, start, end,
              capacity: Number(cap), source: "timetable", origKey: date + "|" + start };
  const all = load(SESSIONS); all.push(s); save(SESSIONS, all);
  return s;
}
const nextToken = sessionId => Math.max(0, ...load(BOOKINGS).filter(b => b.sessionId === sessionId).map(b => b.token)) + 1;

// ---- booking actions (used by students and faculty) ----
function updateBooking(id, fn) {
  const all = load(BOOKINGS), b = all.find(x => x.id === id);
  if (!b) return null;
  fn(b); save(BOOKINGS, all);
  return b;
}
const logB = (b, text) => { (b.history = b.history || []).push({ at: Date.now(), text }); };
const whenText = s => `${fmtDate(s.date)}, ${s.start} - ${s.end}`;

function moveBooking(id, ref, byFaculty, note) {      // returns an error message, or "" when done
  const b0 = load(BOOKINGS).find(x => x.id === id);
  if (!b0) return "Booking not found.";
  const target = ensureSession(ref);
  if (!target) return "That slot is no longer available. Choose another one.";
  if (target.id === b0.sessionId) return "The booking is already in that slot.";
  if (seatsLeft(target) <= 0) return "That slot is full. Choose another one.";
  const token = nextToken(target.id);
  const b = updateBooking(id, x => {
    x.sessionId = target.id; x.token = token; x.reschedule = null;
    if (x.status === "Pending" && byFaculty) x.status = "Waiting";    // a faculty reschedule also accepts the request
    logB(x, `Moved to ${whenText(target)}${byFaculty ? " by faculty" : ""}`);
  });
  const msg = `Your consultation with ${target.facultyName} is now on ${whenText(target)}. New token #${token}.` + (note ? ` Note: ${note}` : "");
  notify(b.studentEmail, "Consultation rescheduled", msg);
  if (!byFaculty) notify(target.facultyEmail, "Booking moved", `${b.studentName}'s booking moved to ${whenText(target)}.`);
  return "";
}

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
  if (extra) d.querySelector("[data-extra]").addEventListener("click", () => {
    const err = extra.onClick(form);
    if (err) { d.querySelector("#dlgMsg").textContent = err; return; }
    d.close();
  });
  form.addEventListener("submit", e => {
    e.preventDefault();
    const err = onConfirm ? onConfirm(form) : "";
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

  el("upcomingAppts").addEventListener("click", e => {
    const btn = e.target.closest("button[data-act]");
    if (!btn) return;
    const b = load(BOOKINGS).find(x => x.id === btn.dataset.id), s = safeSession(b);
    if (btn.dataset.act === "resched") {
      const opts = slotOptions(upcomingSlots(s.facultyEmail), b.sessionId);
      openDialog({ title: "Request a new time", confirm: "Send request",
        body: `<p class="muted">Current slot: ${whenText(s)} with ${esc(s.facultyName)}. The faculty member approves or declines the change.</p>
          <div class="field"><label for="dlgSlot">New time</label><select id="dlgSlot" name="slot">${opts ? '<option value="">Choose a slot</option>' + opts : '<option value="">No other open slots</option>'}</select></div>
          <div class="field"><label for="dlgReason">Reason</label><textarea id="dlgReason" name="reason" placeholder="e.g. Clashes with my lab exam"></textarea></div>`,
        onConfirm: f => {
          if (!f.slot.value) return "Choose the new time you want.";
          const label = f.slot.selectedOptions[0].parentElement.label + ", " + f.slot.selectedOptions[0].textContent.replace(/\s*\(.*\)$/, "");
          updateBooking(b.id, x => { x.reschedule = { ref: f.slot.value, label, reason: f.reason.value.trim(), at: Date.now() }; logB(x, "Student asked to move to " + label); });
          notify(s.facultyEmail, "Reschedule request", `${b.studentName} (token #${b.token}) asked to move from ${whenText(s)} to ${label}.` + (f.reason.value.trim() ? ` Reason: ${f.reason.value.trim()}` : ""));
          draw(); return "";
        } });
    }
    if (btn.dataset.act === "withdraw") {
      updateBooking(b.id, x => { x.reschedule = null; logB(x, "Student withdrew the reschedule request"); });
      draw();
    }
    if (btn.dataset.act === "cancel") {
      openDialog({ title: "Cancel this booking?", confirm: "Cancel booking", danger: true,
        body: `<p>${esc(b.topic)} with ${esc(s.facultyName)} on ${whenText(s)}. Your seat is released for other students.</p>`,
        onConfirm: () => {
          updateBooking(b.id, x => { x.status = "Cancelled"; x.reschedule = null; logB(x, "Cancelled by student"); });
          notify(s.facultyEmail, "Booking cancelled", `${b.studentName} cancelled token #${b.token} (${whenText(s)}).`);
          draw(); return "";
        } });
    }
  });
  draw();
}

function bookPage(me) {
  const form = el("bookForm");
  const faculty = getUsers().filter(u => u.role === "faculty");
  const params = new URLSearchParams(location.search);
  form.faculty.innerHTML = faculty.length
    ? '<option value="">Select a faculty member</option>' + faculty.map(f => `<option value="${esc(f.email)}">${esc(f.name)}</option>`).join("")
    : '<option value="">No faculty registered yet</option>';

  // Prefill student details from the last booking (saved on the user record).
  const prof = (getUsers().find(u => u.email === me.email) || {}).profile || {};
  form.fullName.value = prof.name || me.name;
  ["studentId", "rollNo", "course", "semester", "section", "groupId"].forEach(k => { if (prof[k]) form[k].value = prof[k]; });

  form.faculty.addEventListener("change", () => {
    const list = upcomingSlots(form.faculty.value);
    form.slot.innerHTML = !form.faculty.value ? '<option value="">Choose a faculty first</option>'
      : list.length ? '<option value="">Select a time slot</option>' + slotOptions(list) : '<option value="">No open slots for this faculty</option>';
    form.date.value = ""; form.capacity.value = "";
  });
  form.slot.addEventListener("change", () => {
    const s = upcomingSlots(form.faculty.value).find(x => x.ref === form.slot.value);
    form.date.value = s ? `${DAY_LONG[dowOf(s.date)]}, ${fmtDate(s.date)}` : "";
    form.capacity.value = s ? `${s.seats} of ${s.capacity} seats available` : "";
  });
  if (params.get("faculty")) {                         // arriving from Available Slots
    form.faculty.value = params.get("faculty");
    form.faculty.dispatchEvent(new Event("change"));
    if (params.get("slot")) { form.slot.value = params.get("slot"); form.slot.dispatchEvent(new Event("change")); }
  }

  form.addEventListener("submit", e => {
    e.preventDefault();
    const ref = form.slot.value;
    if (!ref) return showMessage("Please choose a time slot.", false);
    const slot = upcomingSlots(form.faculty.value).find(x => x.ref === ref);
    if (!slot) return showMessage("This slot is no longer available. Please choose another one.", false);
    const all = load(BOOKINGS);
    const clash = all.find(b => b.studentEmail === me.email && isActive(b) && (() => { const s = safeSession(b); return s.date === slot.date && overlaps(s.start, s.end, slot.start, slot.end); })());
    if (clash) return showMessage(`You already have a consultation at that time (token #${clash.token}). Choose a different slot.`, false);
    const s = ensureSession(ref);
    if (!s) return showMessage("This slot is no longer available. Please choose another one.", false);
    if (seatsLeft(s) <= 0) return showMessage("This session is full.", false);
    if (load(BOOKINGS).some(b => b.sessionId === s.id && b.studentEmail === me.email && isActive(b)))
      return showMessage("You have already booked this session.", false);

    const student = { name: form.fullName.value.trim(), studentId: form.studentId.value.trim().toUpperCase(), rollNo: form.rollNo.value.trim().toUpperCase(),
                      course: form.course.value, semester: form.semester.value, section: form.section.value.trim().toUpperCase(),
                      groupId: form.groupId.value.trim().toUpperCase() };
    const users = getUsers(), u = users.find(x => x.email === me.email);
    if (u) { u.profile = student; localStorage.setItem(USERS_KEY, JSON.stringify(users)); }

    const token = nextToken(s.id);                     // next token for this session
    const list = load(BOOKINGS);
    list.push({ id: "b" + Date.now(), sessionId: s.id, studentEmail: me.email, studentName: student.name, student,
                topic: form.topic.value.trim(), desc: form.desc.value.trim(), token, status: "Pending", createdAt: Date.now(),
                reschedule: null, history: [{ at: Date.now(), text: "Requested by student" }] });
    save(BOOKINGS, list);
    notify(me.email, "Booking request sent", `Your request to ${s.facultyName} for ${whenText(s)} is waiting for approval. Your token is #${token}.`);
    notify(s.facultyEmail, "New booking request", `${student.name} (${student.rollNo}, ${student.course}) requested "${form.topic.value.trim()}" on ${whenText(s)} (token #${token}).`);
    if (seatsLeft(s) <= 0) notify(s.facultyEmail, "Capacity reached", `${whenText(s)} is now fully booked.`);
    showMessage(`Request sent. Your token is #${token}. Redirecting...`, true);
    setTimeout(() => { window.location.href = "appointments.html"; }, 1200);
  });
}

function slotsPage(me) {
  const pick = el("slotFaculty"), dayPick = el("slotDay");
  const faculty = getUsers().filter(u => u.role === "faculty");
  pick.innerHTML = faculty.length ? faculty.map(f => `<option value="${esc(f.email)}">${esc(f.name)}</option>`).join("")
                                  : '<option value="">No faculty registered yet</option>';
  function draw() {
    const email = pick.value;
    if (!email) {
      el("weekGrid").innerHTML = '<p class="muted">Slots appear here once faculty members create their accounts.</p>';
      setText("slotInfo", ""); dayPick.innerHTML = ""; return fill("slotRows", "", 5, "No slots to show.");
    }
    setText("slotInfo", classesOf(email).length ? "Grey blocks are this faculty member's classes. Highlighted times are open for consultation."
      : "No timetable has been entered for this faculty member yet, so all working hours are shown. The faculty member accepts or reschedules each request.");
    renderWeek(el("weekGrid"), email, false);
    const list = upcomingSlots(email);
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
  pf.addEventListener("submit", e => {
    e.preventDefault();
    const users = getUsers(), u = users.find(x => x.email === me.email);
    if (u.password !== pf.current.value) return showMessage("Current password is wrong.", false);
    if (pf.next.value.length < 8) return showMessage("Use at least 8 characters for the new password.", false);
    if (pf.next.value !== pf.confirm.value) return showMessage("New passwords do not match.", false);
    u.password = pf.next.value; localStorage.setItem(USERS_KEY, JSON.stringify(users));
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
        onConfirm: f => {
          if (!f.name.value.trim() || !f.email.checkValidity() || !f.email.value) return "Enter the name and a valid email.";
          const err = createFacultyAccount(f.name.value.trim(), f.email.value.trim().toLowerCase(), f.password.value, p.id);
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
        onConfirm: f => {
          setAvail(u.email, { ...getAvail(u.email), ttId: f.link.value, off: {} });
          timetableChanged(u.email, "linked your account to the timetable of " + ttFaculty(f.link.value).name);
          showMessage(`${u.name} now uses the timetable of ${ttFaculty(f.link.value).name}.`, true); draw(); return "";
        } });
    }
    if (b.dataset.reset) {
      const u = getUsers().find(x => x.email === b.dataset.reset);
      openDialog({ title: `Reset password for ${u.name}`, confirm: "Set password",
        body: `<div class="field"><label for="dPass">New temporary password</label><input id="dPass" name="password" type="text" minlength="8" placeholder="At least 8 characters"></div>`,
        onConfirm: f => {
          if (f.password.value.length < 8) return "Use at least 8 characters.";
          const users = getUsers(); users.find(x => x.email === u.email).password = f.password.value;
          localStorage.setItem(USERS_KEY, JSON.stringify(users));
          showMessage(`Password reset for ${u.email}.`, true); return "";
        } });
    }
    if (b.dataset.remove) {
      const u = getUsers().find(x => x.email === b.dataset.remove), open = openFor(u.email);
      openDialog({ title: `Remove ${u.name}?`, confirm: "Remove faculty", danger: true,
        body: `<p>The account is deleted and students can no longer book this faculty member.</p><p>${open.length} open request(s) or meeting(s) will be cancelled and the students notified. The timetable stays, so the account can be created again later.</p>`,
        onConfirm: () => {
          open.forEach(bk => { const s = safeSession(bk);
            updateBooking(bk.id, x => { x.status = "Cancelled"; x.reschedule = null; logB(x, "Cancelled: faculty account removed by admin"); });
            notify(bk.studentEmail, "Booking cancelled", `${s.facultyName} is no longer available on CampusSync, so your meeting on ${whenText(s)} was cancelled. Please book another faculty member.`); });
          localStorage.setItem(USERS_KEY, JSON.stringify(getUsers().filter(x => x.email !== u.email)));
          const av = loadObj(AVAIL); delete av[u.email]; save(AVAIL, av);
          showMessage(`${u.name} removed. ${open.length} booking(s) cancelled.`, true); draw(); return "";
        } });
    }
  });

  form.addEventListener("submit", e => {               // faculty who are not in the timetable files
    e.preventDefault();
    const name = form.name.value.trim(), email = form.email.value.trim().toLowerCase();
    const match = guessTtId(name);
    const err = createFacultyAccount(name, email, form.password.value, "");
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
  const profileId = () => {                            // make sure the account has a timetable to edit
    const a = getAvail(email());
    if (a.ttId && ttFaculty(a.ttId)) return a.ttId;
    const u = getUsers().find(x => x.email === email()), st = ttStore();
    const id = guessTtId(u.name) || (nameKey(u.name) + "-" + Date.now().toString(36));
    if (!st[id]) { st[id] = { name: u.name, classes: [] }; save(TTS, st); }
    setAvail(email(), { ...a, ttId: id }); return id;
  };
  const editClasses = fn => { const st = ttStore(), id = profileId(); fn(st[id].classes); st[id].edited = true; save(TTS, st); };

  function draw() {
    if (!email()) { el("weekGrid").innerHTML = '<p class="muted">Add a faculty member first.</p>'; return; }
    const a = getAvail(email()), id = profileId(), p = ttFaculty(id);
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

  sf.addEventListener("submit", e => {
    e.preventDefault();
    const days = [...sf.querySelectorAll('input[name="days"]:checked')].map(c => Number(c.value));
    if (!days.length) return showMessage("Choose at least one consultation day.", false);
    const a = getAvail(email()), slotLen = Number(sf.slotLen.value);
    setAvail(email(), { ...a, slotLen, capacity: Number(sf.defCap.value), days, off: slotLen !== a.slotLen ? {} : a.off });
    notify(email(), "Consultation hours updated", `The admin set your consultation slots to ${slotLen} minutes for up to ${sf.defCap.value} students.`);
    showMessage("Slot settings saved.", true); draw();
  });

  ef.type.addEventListener("change", () => {
    const consult = ef.type.value === "consult";
    ef.querySelectorAll(".for-class").forEach(x => { x.hidden = consult; });
    ef.querySelectorAll(".for-consult").forEach(x => { x.hidden = !consult; });
  });
  ef.addEventListener("submit", e => {
    e.preventDefault();
    if (!email()) return showMessage("Add a faculty member first.", false);
    const day = Number(ef.day.value), start = ef.start.value, end = ef.end.value;
    if (!start || !end || end <= start) return showMessage("End time must be after start time.", false);
    if (ef.type.value === "consult") {
      const a = getAvail(email());
      if (classClash(email(), day, start, end) && !confirm("This overlaps a class. Add the consultation slot anyway?")) return;
      a.custom.push({ id: "c" + Date.now(), day, start, end, capacity: Number(ef.capacity.value) || a.capacity });
      setAvail(email(), a);
      notify(email(), "Consultation hours updated", `The admin added consultation hours on ${DAY_LONG[day]}, ${start} - ${end}.`);
      showMessage(`Consultation slot added on ${DAY_LONG[day]}.`, true);
    } else {
      const busy = ef.type.value === "busy";
      if (!busy && !ef.code.value.trim()) return showMessage("Enter the subject code.", false);
      editClasses(cl => cl.push({ id: "x" + Date.now(), day, start, end, code: busy ? "Busy" : ef.code.value.trim().toUpperCase(),
        subject: ef.subject.value.trim() || (busy ? "Not available" : ""), section: ef.section.value.trim(), room: ef.room.value.trim() }));
      const n = timetableChanged(email(), `added ${busy ? "a busy period" : "a class"} on ${DAY_LONG[day]}, ${start} - ${end}`);
      showMessage(`Added to ${DAY_LONG[day]}.` + (n ? ` ${n} booked meeting(s) now clash; the faculty member was notified.` : ""), !n);
    }
    ef.start.value = ""; ef.end.value = ""; ef.code.value = ""; ef.subject.value = ""; ef.section.value = ""; ef.room.value = "";
    draw();
  });

  el("weekGrid").addEventListener("click", e => {
    const btn = e.target.closest("button.blk");
    if (!btn) return;
    const a = getAvail(email());
    if (btn.dataset.key) {                             // offer / stop offering a free slot
      if (a.off[btn.dataset.key]) delete a.off[btn.dataset.key]; else a.off[btn.dataset.key] = true;
      setAvail(email(), a); draw();
      const again = el("weekGrid").querySelector(`[data-key="${btn.dataset.key}"]`); if (again) again.focus();
      return;
    }
    if (btn.dataset.cid) {
      if (confirm("Remove this extra consultation slot? Existing bookings are kept.")) { a.custom = a.custom.filter(c => c.id !== btn.dataset.cid); setAvail(email(), a); draw(); }
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
      onConfirm: f => {
        if (!f.code.value.trim()) return "Enter a code.";
        if (!f.start.value || !f.end.value || f.end.value <= f.start.value) return "End time must be after start time.";
        editClasses(cl => Object.assign(cl.find(x => x.id === c.id), { day: Number(f.day.value), start: f.start.value, end: f.end.value,
          code: f.code.value.trim(), subject: f.subject.value.trim(), section: f.section.value.trim(), room: f.room.value.trim() }));
        const n = timetableChanged(email(), `changed ${c.code} (${DAY_SHORT[c.day]} ${c.start}) to ${DAY_LONG[Number(f.day.value)]} ${f.start.value} - ${f.end.value}`);
        showMessage("Timetable entry updated." + (n ? ` ${n} booked meeting(s) now clash; the faculty member was notified.` : ""), !n);
        draw(); return "";
      },
      extra: { text: "Remove entry", onClick: () => {
        editClasses(cl => cl.splice(cl.findIndex(x => x.id === c.id), 1));
        timetableChanged(email(), `removed ${c.code} on ${DAY_LONG[c.day]}, ${c.start} - ${c.end}. That time is now open for consultations`);
        showMessage(`${c.code} removed. The freed time is now offered to students.`, true); draw(); return "";
      } } });
  });

  el("restoreTT").addEventListener("click", () => {
    const id = profileId(), orig = TT.find(f => f.id === id);
    if (!orig || !confirm("Replace this faculty member's timetable with the imported university timetable? Your edits to it will be lost.")) return;
    const st = ttStore(); st[id] = importedProfile(orig); save(TTS, st);   // edited flag cleared: future corrections apply
    timetableChanged(email(), "restored your timetable to the official university timetable");
    showMessage("Imported timetable restored.", true); draw();
  });
  el("offerAll").addEventListener("click", () => { const a = getAvail(email()); a.off = {}; setAvail(email(), a); draw(); });
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

  function moveDialog(b) {
    const s = safeSession(b);
    const opts = slotOptions(upcomingSlots(me.email), b.sessionId);
    openDialog({ title: `Reschedule ${b.studentName}`, confirm: "Reschedule",
      body: `<p class="muted">Now: ${whenText(s)}, token #${b.token}. Rescheduling also accepts a pending request.</p>
        <div class="field"><label for="dlgSlot">Move to</label><select id="dlgSlot" name="slot"><option value="">Choose a slot</option>${opts}<option value="custom">Another date and time...</option></select></div>
        <div class="form-grid" id="dlgCustom" hidden><div class="field wide"><label for="dlgDate">Date</label><input id="dlgDate" name="date" type="date" min="${today()}"></div>
          <div class="field"><label for="dlgStart">Start time</label><input id="dlgStart" name="start" type="time"></div>
          <div class="field"><label for="dlgEnd">End time</label><input id="dlgEnd" name="end" type="time"></div></div>
        <div class="field"><label for="dlgNote">Message to the student (optional)</label><textarea id="dlgNote" name="note"></textarea></div>`,
      onOpen: f => f.slot.addEventListener("change", () => { f.querySelector("#dlgCustom").hidden = f.slot.value !== "custom"; }),
      onConfirm: f => {
        let ref = f.slot.value;
        if (!ref) return "Choose where to move the booking.";
        if (ref === "custom") {
          const date = f.date.value, start = f.start.value, end = f.end.value;
          if (!date || !start || !end) return "Fill in the date, start and end time.";
          if (end <= start) return "End time must be after start time.";
          if (isPast(date, start)) return "Choose a time in the future.";
          const cl = classClash(me.email, dowOf(date), start, end);
          if (cl && !confirm(`This overlaps your class ${cl.code} (${cl.start} - ${cl.end}). Use this time anyway?`)) return "Pick a time that does not clash with your class.";
          const all = load(SESSIONS);
          const ns = { id: "s" + Date.now(), facultyEmail: me.email, facultyName: me.name, topic: "Rescheduled consultation", desc: "Created while rescheduling a booking",
                       date, start, end, capacity: 1, source: "created" };
          all.push(ns); save(SESSIONS, all); ref = "s:" + ns.id;
        }
        const err = moveBooking(b.id, ref, true, f.note.value.trim());
        if (err) return err;
        showMessage(`${b.studentName} moved and notified.`, true); draw(); return "";
      } });
  }
  function reasonDialog(b, kind) {
    const s = safeSession(b), reject = kind === "reject";
    openDialog({ title: reject ? "Reject this request?" : "Cancel this booking?", confirm: reject ? "Reject request" : "Cancel booking", danger: true,
      body: `<p>${esc(b.studentName)}, ${esc(b.topic)}, ${whenText(s)}.</p><div class="field"><label for="dlgNote">Reason (sent to the student)</label><textarea id="dlgNote" name="note" placeholder="${reject ? "e.g. Please discuss this with your project mentor first" : "e.g. I am on official duty that day"}"></textarea></div>`,
      onConfirm: f => {
        const note = f.note.value.trim();
        updateBooking(b.id, x => { x.status = reject ? "Rejected" : "Cancelled"; x.reschedule = null; logB(x, (reject ? "Rejected" : "Cancelled") + " by faculty" + (note ? ": " + note : "")); });
        notify(b.studentEmail, reject ? "Request rejected" : "Booking cancelled", `${s.facultyName} ${reject ? "rejected your request" : "cancelled your booking"} for ${whenText(s)} (token #${b.token}).` + (note ? ` Reason: ${note}` : "") + " You can book another slot.");
        draw(); return "";
      } });
  }

  document.querySelector("main").addEventListener("click", e => {
    const btn = e.target.closest("button[data-act]");
    if (!btn || !btn.dataset.id) return;
    const b = load(BOOKINGS).find(x => x.id === btn.dataset.id), s = safeSession(b);
    switch (btn.dataset.act) {
      case "details": return openDialog({ title: "Booking details", body: studentDetailsHTML(b) });
      case "accept":
        updateBooking(b.id, x => { x.status = "Waiting"; logB(x, "Accepted by faculty"); });
        notify(b.studentEmail, "Request accepted", `${s.facultyName} accepted your consultation on ${whenText(s)}. Token #${b.token}.`);
        showMessage(`Accepted ${b.studentName}.`, true); return draw();
      case "reject": return reasonDialog(b, "reject");
      case "cancel": return reasonDialog(b, "cancel");
      case "move": return moveDialog(b);
      case "approve": {
        const err = moveBooking(b.id, b.reschedule.ref, true, "");
        if (err) { showMessage(err + " Use Reschedule to pick a different slot.", false); return; }
        showMessage(`Moved ${b.studentName} to the requested time.`, true); return draw();
      }
      case "decline":
        updateBooking(b.id, x => { x.reschedule = null; logB(x, "Reschedule request declined by faculty"); });
        notify(b.studentEmail, "Reschedule declined", `${s.facultyName} kept your consultation on ${whenText(s)} (token #${b.token}).`);
        return draw();
    }
  });
  draw();
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
    const label = pend ? "Pending" : bk.length && bk.every(b => effStatus(b) === "Completed") ? "Completed" : bk.length ? "Accepted" : "Open";
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
const requiredRole = document.body.dataset.role;
if (requiredRole) {
  const me = getCurrent();
  if (!me || me.role !== requiredRole) {
    window.location.href = "index.html";
  } else {
    document.querySelectorAll("[data-user-name]").forEach(n => { n.textContent = me.name; });
    if (el("myAppts")) studentDashboard(me);
    if (el("upcomingAppts")) appointmentsPage(me);
    if (el("bookForm")) bookPage(me);
    if (el("slotRows")) slotsPage(me);
    if (el("ttView")) facultyTimetablePage(me);
    if (el("openReqs")) requestsPage(me);
    if (el("pwForm")) adminAccountPage(me);
    if (el("addFacultyForm")) adminFacultyPage(me);
    if (el("entryForm")) adminTimetablePage(me);
    if (el("calGrid")) calendarPage(me);
    if (el("noteList")) renderNotes(me);
    navBadge(me);
  }
}
