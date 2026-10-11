// CampusSync demo (no backend). Everything is saved in THIS browser only (localStorage).
// Not secure - for demonstration only. When the Flask backend is ready, replace the
// functions in the DATA LAYER section below with fetch() calls to the backend.

const USERS_KEY = "campussync_users";
const CURRENT_KEY = "campussync_current";

function getUsers() {
  return JSON.parse(localStorage.getItem(USERS_KEY) || "[]");
}
function getCurrent() {
  return JSON.parse(localStorage.getItem(CURRENT_KEY) || "null");
}
function showMessage(text, ok) {
  const box = document.getElementById("formMsg");
  box.textContent = text;
  box.className = "form-msg" + (ok ? " ok" : "");
}

// ---------- Sign up ----------
const signupForm = document.getElementById("signupForm");
if (signupForm) {
  signupForm.addEventListener("submit", function (e) {
    e.preventDefault();
    const name = signupForm.name.value.trim();
    const email = signupForm.email.value.trim().toLowerCase();
    const password = signupForm.password.value;
    const role = signupForm.role.value;

    if (password !== signupForm.confirm.value) {
      return showMessage("Passwords do not match.", false);
    }
    const users = getUsers();
    if (users.some(u => u.email === email)) {
      return showMessage("This email is already registered. Please log in.", false);
    }
    users.push({ name, email, password, role });
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
    const role = e.submitter.value;                    // "student" or "faculty"
    const email = loginForm.email.value.trim().toLowerCase();
    const password = loginForm.password.value;

    const user = getUsers().find(u => u.email === email && u.password === password);
    if (!user) return showMessage("Wrong email or password, or no account yet. Please sign up first.", false);
    if (user.role !== role) return showMessage("This account is registered as " + user.role + ". Use the " + user.role + " login button.", false);

    localStorage.setItem(CURRENT_KEY, JSON.stringify({ name: user.name, email: user.email, role: user.role }));
    window.location.href = role === "student" ? "student-dashboard.html" : "faculty-dashboard.html";
  });
}

// ================= DATA LAYER (replace with backend calls later) =================
const SESSIONS = "campussync_sessions", BOOKINGS = "campussync_bookings", NOTES = "campussync_notes";
const load = key => JSON.parse(localStorage.getItem(key) || "[]");
const save = (key, value) => localStorage.setItem(key, JSON.stringify(value));

const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmtDate = d => new Date(d + "T00:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
const pill = s => `<span class="pill ${s.toLowerCase().replace(" ", "")}">${s}</span>`;
const today = () => new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);
const el = id => document.getElementById(id);
const setText = (id, v) => { if (el(id)) el(id).textContent = v; };

const isActive = b => b.status === "Waiting" || b.status === "In Session";
const sessionOf = b => load(SESSIONS).find(s => s.id === b.sessionId);
const bookedCount = s => load(BOOKINGS).filter(b => b.sessionId === s.id && b.status !== "Cancelled").length;
const seatsLeft = s => s.capacity - bookedCount(s);
const sessionStatus = s => (seatsLeft(s) > 0 ? "Open" : "Full");

function notify(email, title, message) {
  const all = load(NOTES);
  all.push({ email, title, message, time: new Date().toLocaleString("en-GB"), read: false });
  save(NOTES, all);
}
function queuePosition(b) {
  if (b.status === "In Session") return "Now";
  if (b.status !== "Waiting") return "-";
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

// ================= PAGES =================
function studentDashboard(me) {
  const mine = load(BOOKINGS).filter(b => b.studentEmail === me.email).reverse();
  setText("statOpen", load(SESSIONS).filter(s => seatsLeft(s) > 0).length);
  setText("statActive", mine.filter(isActive).length);
  setText("statNotif", load(NOTES).filter(n => n.email === me.email && !n.read).length);
  const L = ["Token", "Faculty", "Topic", "Date", "Time", "Status"];
  fill("myAppts", mine.map(b => { const s = sessionOf(b);
    return row(L, ["#" + b.token, esc(s.facultyName), esc(b.topic), fmtDate(s.date), `${s.start} - ${s.end}`, pill(b.status)]); }).join(""),
    6, "No appointments yet. Book a consultation to get started.");
}

function appointmentsPage(me) {
  const mine = load(BOOKINGS).filter(b => b.studentEmail === me.email).reverse();
  const L = ["Token", "Faculty", "Topic", "Date & Time", "Queue Position", "Status"];
  const build = list => list.map(b => { const s = sessionOf(b);
    return row(L, ["#" + b.token, esc(s.facultyName), esc(b.topic), `${fmtDate(s.date)}, ${s.start}`, queuePosition(b), pill(b.status)]); }).join("");
  fill("upcomingAppts", build(mine.filter(isActive)), 6, "No upcoming appointments.");
  fill("previousAppts", build(mine.filter(b => !isActive(b))), 6, "No previous appointments.");
}

function bookPage(me) {
  const form = el("bookForm");
  const faculty = getUsers().filter(u => u.role === "faculty");
  form.faculty.innerHTML = faculty.length
    ? '<option value="">Select a faculty member</option>' + faculty.map(f => `<option value="${esc(f.email)}">${esc(f.name)}</option>`).join("")
    : '<option value="">No faculty registered yet</option>';

  form.faculty.addEventListener("change", () => {
    const list = load(SESSIONS).filter(s => s.facultyEmail === form.faculty.value && seatsLeft(s) > 0);
    form.slot.innerHTML = list.length
      ? '<option value="">Select a time slot</option>' + list.map(s => `<option value="${s.id}">${fmtDate(s.date)} · ${s.start} - ${s.end} (${seatsLeft(s)} seats left)</option>`).join("")
      : '<option value="">No open sessions for this faculty</option>';
    form.date.value = ""; form.capacity.value = "";
  });
  form.slot.addEventListener("change", () => {
    const s = load(SESSIONS).find(x => x.id === form.slot.value);
    form.date.value = s ? fmtDate(s.date) : "";
    form.capacity.value = s ? `${seatsLeft(s)} of ${s.capacity} seats available` : "";
  });

  form.addEventListener("submit", e => {
    e.preventDefault();
    const s = load(SESSIONS).find(x => x.id === form.slot.value);
    if (!s) return showMessage("Please choose a time slot.", false);
    if (seatsLeft(s) <= 0) return showMessage("This session is full.", false);
    const all = load(BOOKINGS);
    if (all.some(b => b.sessionId === s.id && b.studentEmail === me.email && isActive(b)))
      return showMessage("You have already booked this session.", false);
    const token = Math.max(0, ...all.filter(b => b.sessionId === s.id).map(b => b.token)) + 1;   // next token for this session
    all.push({ id: "b" + Date.now(), sessionId: s.id, studentEmail: me.email, studentName: me.name,
               topic: form.topic.value.trim(), desc: form.desc.value.trim(), token, status: "Waiting", createdAt: Date.now() });
    save(BOOKINGS, all);
    notify(me.email, "Booking confirmed", `Your consultation with ${s.facultyName} is booked. Your token is #${token}.`);
    notify(s.facultyEmail, "New booking", `${me.name} booked "${s.topic}" (token #${token}).`);
    if (seatsLeft(s) <= 0) notify(s.facultyEmail, "Capacity reached", `"${s.topic}" is now fully booked.`);
    showMessage(`Booked! Your token is #${token}. Redirecting...`, true);
    setTimeout(() => { window.location.href = "appointments.html"; }, 1200);
  });
}

function facultyDashboard(me) {
  const mine = load(SESSIONS).filter(s => s.facultyEmail === me.email);
  const bookings = load(BOOKINGS).filter(b => mine.some(s => s.id === b.sessionId));
  setText("fStatToday", mine.filter(s => s.date === today()).length);
  setText("fStatBookings", bookings.filter(b => b.status !== "Cancelled").length);
  setText("fStatWaiting", bookings.filter(b => b.status === "Waiting").length);
  const L = ["Topic", "Date", "Time", "Booked Capacity", "Status"];
  fill("mySessions", mine.map(s => row(L, [esc(s.topic), fmtDate(s.date), `${s.start} - ${s.end}`, `${bookedCount(s)} / ${s.capacity}`, pill(sessionStatus(s))])).join(""),
    5, "No sessions yet. Click Create Session to add one.");
}

function createSessionPage(me) {
  const form = el("sessionForm");
  form.addEventListener("submit", e => {
    e.preventDefault();
    if (form.end.value <= form.start.value) return showMessage("End time must be after start time.", false);
    const all = load(SESSIONS);
    all.push({ id: "s" + Date.now(), facultyEmail: me.email, facultyName: me.name, topic: form.topic.value.trim(),
               desc: form.desc.value.trim(), date: form.date.value, start: form.start.value, end: form.end.value,
               capacity: Number(form.capacity.value) });
    save(SESSIONS, all);
    form.reset();
    showMessage("Session created. Students can now book it.", true);
  });
}

function queuePage(me) {
  const mine = load(SESSIONS).filter(s => s.facultyEmail === me.email);
  const pick = el("sessionPick");
  pick.innerHTML = mine.length ? mine.map(s => `<option value="${s.id}">${esc(s.topic)} · ${fmtDate(s.date)}</option>`).join("")
                               : '<option value="">No sessions yet</option>';
  const current = () => mine.find(s => s.id === pick.value);

  function draw() {
    const s = current();
    setText("qTitle", s ? s.topic : "No session selected");
    setText("qDate", s ? fmtDate(s.date) : "-");
    setText("qTime", s ? `${s.start} - ${s.end}` : "-");
    setText("qCap", s ? `${bookedCount(s)} / ${s.capacity} booked` : "-");
    el("qStatus").innerHTML = s ? pill(sessionStatus(s)) : "-";
    const list = s ? load(BOOKINGS).filter(b => b.sessionId === s.id && b.status !== "Cancelled").sort((a, b) => a.token - b.token) : [];
    const L = ["Student", "Token", "Request Time", "Waiting", "Priority", "Status", "Status Controls"];
    fill("queueBody", list.map(b => {
      const mins = b.status === "Waiting" ? Math.floor((Date.now() - b.createdAt) / 60000) + " min" : "-";
      const ctrl = '<div class="status-controls">' + ["Waiting", "In Session", "Completed"].map(st =>
        `<button type="button" data-id="${b.id}" data-status="${st}"${st === b.status ? ' class="on"' : ""}>${st}</button>`).join("") + "</div>";
      return row(L, [esc(b.studentName), "#" + b.token, new Date(b.createdAt).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }), mins, pill("Normal"), pill(b.status), ctrl]);
    }).join(""), 7, "No students have booked this session yet.");
  }

  function setStatus(id, status) {
    const all = load(BOOKINGS), b = all.find(x => x.id === id);
    b.status = status; save(BOOKINGS, all);
    if (status === "In Session") notify(b.studentEmail, "Your turn", `Token #${b.token}: the faculty is ready. Please join now.`);
    if (status === "Completed") notify(b.studentEmail, "Consultation completed", `Your consultation for token #${b.token} is completed.`);
  }

  pick.addEventListener("change", draw);
  el("queueBody").addEventListener("click", e => {
    const btn = e.target.closest("button[data-id]");
    if (btn) { setStatus(btn.dataset.id, btn.dataset.status); draw(); }
  });
  el("callNext").addEventListener("click", () => {
    const s = current();
    if (!s) return showMessage("Create a session first.", false);
    const next = load(BOOKINGS).filter(b => b.sessionId === s.id && b.status === "Waiting").sort((a, b) => a.token - b.token)[0];   // first come, first served
    if (!next) return showMessage("No students are waiting.", false);
    setStatus(next.id, "In Session");
    showMessage(`Called ${next.studentName} (token #${next.token}).`, true);
    draw();
  });
  draw();
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
    if (el("mySessions")) facultyDashboard(me);
    if (el("sessionForm")) createSessionPage(me);
    if (el("queueBody")) queuePage(me);
    if (el("noteList")) renderNotes(me);
  }
}