"""Check timetable-data.js against the source timetable files.

Usage:  python verify_timetable_data.py <btech_timetable.html> <UG_timetable.pdf> [../static/timetable-data.js]

1. Every subject code printed in the PDF must be in the parsed grid at its own day and column.
2. Every class period found in the sources must be present in timetable-data.js.
(The B.Tech HTML was also checked against Chromium's rendered layout: 750/750 cells matched.)
"""
import json, re, sys, io, os, contextlib
html, pdf_path = sys.argv[1], sys.argv[2]
data_path = sys.argv[3] if len(sys.argv) > 3 else os.path.join(os.path.dirname(__file__), "..", "static", "timetable-data.js")
src = open(os.path.join(os.path.dirname(__file__), "build_timetable_data.py")).read()
sys.argv = ["build", html, pdf_path, os.devnull]
g = {}
with contextlib.redirect_stdout(io.StringIO()):
    exec(src[:src.index("# ---------------- merge consecutive")], g)

bad, n = [], 0
for pn, p in enumerate(g["pdf"].pages):
    grid = g["page_grid"](p)
    t = [t for t in p.find_tables() if "MONDAY" in (p.crop(t.bbox).extract_text() or "")][0]
    hc = sorted([c for c in t.cells if re.match(r"^\d{1,2}\.\d{2}", g["norm"](p.crop(c).extract_text()))], key=lambda c: c[0])
    words = p.within_bbox(t.bbox).extract_words()
    days = sorted([w for w in words if w["text"] in g["DAYS"] or w["text"].startswith("FRSDAY")], key=lambda w: w["top"])
    for w in words:
        if not re.match(r"^[TPC][A-Z]{2}\d{3}$", w["text"]): continue
        di = max(i for i, d in enumerate(days) if d["top"] <= w["top"] + 3)
        col = [i for i, c in enumerate(hc) if c[0] - 3 <= w["x0"] < c[2] - 3]
        n += 1
        if not col or w["text"] not in grid[g["DAYS"][di]][col[0]]:
            bad.append((pn + 1, g["DAYS"][di], w["text"]))
print(f"PDF: {n} printed subject codes checked, {len(bad)} misplaced", bad[:10])

data = json.loads(re.search(r"const TIMETABLE = (.*);", open(data_path).read()).group(1))
mins = lambda t: int(t[:2]) * 60 + int(t[3:])
def secs(s):
    if s.startswith("B.Tech CSE V ("): return {"*"}
    if s.startswith("B.Tech CSE V-"): return {"B.Tech CSE V-" + x.strip() for x in s[13:].split(",")}
    return {x.strip() for x in s.split(";")}
pub = {}
for f in data["faculty"]: pub.setdefault(f["id"].replace("-", " "), []).extend(f["classes"])
raw = {(k, c[0], c[1], c[2], c[3], c[5]) for k, f in g["faculty"].items() for c in f["classes"]}
miss = [r for r in raw if not any(c[0] == r[1] and c[3] == r[4] and mins(c[1]) <= mins(r[2]) and mins(c[2]) >= mins(r[3])
        and (r[5] in secs(c[5]) or "*" in secs(c[5])) for c in pub.get(r[0], []))]
print(f"Data file: {len(raw)} class periods in the sources, {len(miss)} missing from timetable-data.js", miss[:10])
sys.exit(1 if bad or miss else 0)
