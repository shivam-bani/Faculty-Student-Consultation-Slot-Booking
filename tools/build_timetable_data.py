"""Rebuild timetable-data.js from the university timetable files.

Usage:  pip install beautifulsoup4 pdfplumber
        python build_timetable_data.py <btech_timetable.html or .mhtml> <UG_timetable.pdf> [out.js]

Cells are mapped to periods by their physical position (cell width in the HTML, bounding
box in the PDF), because the Word-exported tables have irregular column spans.
Check the printed per-faculty summary after a rebuild; layouts change between releases.
"""
import sys, re, json, email, pdfplumber
from collections import defaultdict
from bs4 import BeautifulSoup
HTML_PATH, PDF_PATH = sys.argv[1], sys.argv[2]
OUT_PATH = sys.argv[3] if len(sys.argv) > 3 else "timetable-data.js"
raw = open(HTML_PATH, "rb").read()
html = raw.decode("utf-8", "replace")
if raw.lstrip().startswith(b"From:") or b"multipart/related" in raw[:2000]:     # saved as MHTML
    for part in email.message_from_bytes(raw).walk():
        if part.get_content_type() == "text/html":
            html = part.get_payload(decode=True).decode("utf-8", "replace"); break
S = BeautifulSoup(html, "html.parser")
T = S.find_all("table")
def W(td):
    st=td.get('style','')
    m=re.search(r'width:\s*([\d.]+)pt',st)
    return float(m.group(1)) if m else float(td.get('width',0))*0.75
def rows_with_x(t):
    out=[]
    for tr in t.find_all('tr'):
        x=0; r=[]
        for td in tr.find_all(['td','th']):
            w=W(td); txt=re.sub(r'\s+',' ',td.get_text(' ',strip=True))
            r.append((txt,x,x+w)); x+=w
        out.append(r)
    return out
def mapped(t):
    R=rows_with_x(t)
    head=R[0]
    cols=[(h[0],h[1],h[2]) for h in head[1:]]
    res=[]
    for r in R[1:]:
        if not r or not r[0][0]: continue
        day=r[0][0]; slots=['']*len(cols)
        for txt,a,b in r[1:]:
            for i,(lab,ca,cb) in enumerate(cols):
                ov=min(b,cb)-max(a,ca)
                if ov>0.5*(cb-ca): slots[i]=txt if not slots[i] else slots[i]+' || '+txt
        res.append((day,slots))
    return [c[0] for c in cols],res

PERIODS=[("08:00","08:55"),("08:55","09:50"),("10:10","11:05"),("11:05","12:00"),("12:00","12:55"),("13:10","14:05"),("14:05","15:00"),("15:10","16:05"),("16:05","17:00"),("17:00","17:55")]
DAYS=["MONDAY","TUESDAY","WEDNESDAY","THURSDAY","FRIDAY","SATURDAY"]
def norm(t): return re.sub(r'\s+',' ',t or '').strip()
def page_grid(p):
    tables=p.find_tables()
    tt=None
    for t in tables:
        txt=norm(p.crop(t.bbox).extract_text())
        if 'MONDAY' in txt and 'TIME' in txt: tt=t;break
    cells=tt.cells
    def text(b): return norm(p.crop(b).extract_text())
    info=[(b,text(b)) for b in cells]
    heads=[(b,t) for b,t in info if re.match(r'^\d{1,2}\.\d{2}\s?[AP]M',t)]
    heads.sort(key=lambda x:x[0][0])
    assert len(heads)==10,(len(heads),[t for b,t in heads])
    days=[(b,t) for b,t in info if t in DAYS or t.startswith('FRSDAY')]
    days.sort(key=lambda x:x[0][1])
    bands=[]
    for i,(b,t) in enumerate(days):
        y0=b[1]; y1=days[i+1][0][1] if i+1<len(days) else tt.bbox[3]
        bands.append((DAYS[i],y0,y1))
    grid={d:['']*10 for d in DAYS}
    xs=[(h[0][0],h[0][2]) for h in heads]
    for b,t in info:
        if not t or b[0]<xs[0][0]-2: continue
        if re.match(r'^\d{1,2}\.\d{2}',t): continue
        cy=(b[1]+b[3])/2
        day=[d for d,y0,y1 in bands if y0-1<=cy<y1]
        if not day: continue
        for i,(a,c) in enumerate(xs):
            ov=min(b[2],c)-max(b[0],a)
            if ov>0.5*(c-a):
                g=grid[day[0]]; g[i]=(g[i]+' ' if g[i] else '')+t
    # Some cells are not fully enclosed by ruling lines, so their text belongs to no detected
    # cell (e.g. "TBD522 / TBD521" shared slots). Place any such subject code by its position.
    for w in p.within_bbox(tt.bbox).extract_words():
        if not re.match(r'^[TPC][A-Z]{2}\d{3}$', w['text']): continue
        cy=(w['top']+w['bottom'])/2
        day=[d for d,y0,y1 in bands if y0-1<=cy<y1]
        col=[i for i,(a,c) in enumerate(xs) if a-3<=w['x0']<c-3]
        if not day or not col: continue
        g=grid[day[0]]
        if w['text'] not in g[col[0]]: g[col[0]]=(g[col[0]]+' ' if g[col[0]] else '')+w['text']
    return grid


TITLES=r'^(dr|mr|ms|mrs|prof)\.?\s*'
ALIAS={"om prakash pal":"op pal"}
def key(name):
    n=name.lower().replace('.',' ')
    n=re.sub(r'\(.*?\)','',n)
    n=re.sub(r'\s+',' ',n).strip()
    while True:
        m=re.match(r'^(dr|mr|ms|mrs|prof)\b\s*',n)
        if not m: break
        n=n[m.end():]
    n=re.sub(r'\b([a-z]) (?=[a-z]\b)',r'\1',n)   # "o p pal" -> "op pal"
    return ALIAS.get(n.strip(),n.strip())
def pretty(name):
    name=re.sub(r'\(.*?\)','',name).strip()
    name=re.sub(r'\.(?=\S)','. ',name)          # "Dr.Rehan" -> "Dr. Rehan"
    name=re.sub(r'\s+',' ',name)
    words=[]
    for w in name.split(' '):
        if w.isalpha() and (w.isupper() and len(w)>2 or w.islower()): w=w.capitalize()
        if w.upper()=="MR.": w="Mr."
        words.append(w)
    return ' '.join(words)
def split_names(s):
    s=re.sub(r'G\d\s*-\s*','',s)
    out=[]
    for part in re.split(r'/|&|,',s):
        part=part.strip(' -')
        if part and not part.lower().startswith('with ') and part.lower()!='open elective': out.append(part)
    return out

faculty={}   # key -> {"names":Counter,"classes":set,"subjects":set}
def F(name):
    k=key(name)
    if not k: return None
    f=faculty.setdefault(k,{"names":defaultdict(int),"classes":set(),"subjects":set()})
    f["names"][pretty(name)]+=1
    return f
def clean_title(t):
    t=re.sub(r'\s+(G1\s*-.*|CR\s*-?\s*\d+|LT\s*-?\s*\d+|Lab-\d+)$','',t.strip())
    return t.strip()

# ---------------- B.Tech CSE V (HTML) ----------------
BT=[("08:00","08:55"),("09:00","09:55"),("09:55","10:50"),("11:10","12:05"),("12:05","13:00"),("13:00","13:55"),("13:55","14:50"),("15:10","16:05"),("16:05","17:00"),("17:00","17:55")]
DAYN={"Mon":1,"Tue":2,"Wed":3,"Thu":4,"Fri":5,"Sat":6}
heads=[re.sub(r'\s+',' ',h.get_text(' ',strip=True)) for h in S.find_all('h2')]
def code_norm(c): return re.sub(r'\(S\)','',c.replace(' ','')).upper()
for si in range(15):
    sec="B.Tech CSE V-"+re.split(r'[-–]\s*',heads[si])[-1].strip()
    ft=T[si*2+1]; codes={}; electives=[]; career={}; iks=[]; cec=[]
    mode='core'
    for tr in ft.find_all('tr')[1:]:
        c=[re.sub(r'\s+',' ',td.get_text(' ',strip=True)) for td in tr.find_all(['td','th'])]
        if len(c)==1 and c[0]=='Elective': mode='elective'; continue
        if len(c)==2 and c[1]=='Lab': mode='lab'; continue
        if len(c)==2: # CEC row
            cec=[(n,"Career Excellence Classes (CEC)") for n in split_names(c[1])]; continue
        code,title,fac=c[0],c[1],c[2]
        if not fac: continue
        if 'PDCC' in title:
            for part in fac.split('/'):
                t,_,n=part.partition('-'); career[t.strip().lower()]=n.strip()
            continue
        if 'IKS' in title: iks=[(n,"IKS (Online)") for n in split_names(fac)]; continue
        entry=[(n,clean_title(title)) for n in split_names(fac)]
        if mode=='elective': electives+= [(n,t,code) for n,t in entry]
        else:
            for cc in re.findall(r'[A-Z]{3}-\d{3}',code): codes[code_norm(cc)]=[(n,t,cc) for n,t in entry]
    hdr,res=mapped(T[si*2])
    assert len(hdr)==10
    for day,slots in res:
        d=DAYN.get(day[:3])
        if not d: continue
        for i,txt in enumerate(slots):
            if not txt: continue
            st,en=BT[i]; who=[]
            m=re.search(r'PDCC\s*\(\s*(QAR|Verbal|Soft Skills)',txt,re.I)
            if m:
                n=career.get(m.group(1).lower())
                if n: who.append((n,"Career Skills / PDCC ("+m.group(1).strip()+")","XCS-501"))
            elif 'Elective' in txt: who+= [(n,t,c) for n,t,c in electives]
            elif 'IKS' in txt: who+= [(n,t,"IKS") for n,t in iks]
            elif 'CEC' in txt: who+= [(n,t,"CEC") for n,t in cec]
            else:
                for cc in re.findall(r'[A-Z]{3}-\d{3}',txt):
                    who+= codes.get(code_norm(cc),[])
            room=re.sub(r'^.*?(?:[A-Z]{3}-\d{3}(?:\(S\))?\s*(?:Lab)?)','',txt.split(' & ')[0]).strip() if re.search(r'[A-Z]{3}-\d{3}',txt) else ''
            for n,t,c in who:
                f=F(n); f["classes"].add((d,st,en,c,t,sec,room[:30]))
                if c not in ("IKS","CEC") : f["subjects"].add((c,t))

# ---------------- BCA / BSc V (PDF) ----------------
pdf=pdfplumber.open(PDF_PATH)
pdf_rows=[]  # (page, code, title, fac)
for n,p in enumerate(pdf.pages):
    for t in p.extract_tables():
        if t and t[0] and (t[0][0] or '').strip().startswith('Code'):
            for r in t[1:]:
                r=[(x or '').replace('\n',' ').strip() for x in r]
                code=next((x for x in r[:3] if re.match(r'^[A-Z]{3}\d{3}$',x)),'')
                pdf_rows.append((n,code,r[-2],r[-1]))
by_title={}
for n,c,t,f in pdf_rows:
    if c and f and not f.lower().startswith('with') and f!='Open Elective': by_title.setdefault(t,f)
DAYS6=["MONDAY","TUESDAY","WEDNESDAY","THURSDAY","FRIDAY","SATURDAY"]
for n,p in enumerate(pdf.pages):
    title=re.sub(r'\s+',' ',p.extract_text().split('\n')[0]).strip()
    sec=re.sub(r'Para Medical.*','',title).replace('‘','').replace('’','').strip()
    rows={c:(t,f) for pn,c,t,f in pdf_rows if pn==n and c}
    g=page_grid(p)
    for di,dname in enumerate(DAYS6):
        for i,txt in enumerate(g[dname]):
            for cc in re.findall(r'[TPC][A-Z]{2}\d{3}',txt):
                tf=rows.get(cc)
                if not tf:   # e.g. "TBD504" typed in a BCA grid -> same number, same type
                    alt=[k for k in rows if k[0]==cc[0] and k[-3:]==cc[-3:]]
                    tf=rows.get(alt[0]) if alt else None
                if not tf: continue
                t,fac=tf
                if fac.lower().startswith('with'): fac=by_title.get(t,'')
                st,en=PERIODS[i]
                room=txt.split(cc,1)[1].strip()[:30]
                room=re.sub(r'Comprehensive.*','Comprehensive viva',room)
                for nm in split_names(fac):
                    f=F(nm); f["classes"].add((di+1,st,en,cc,t,sec,room)); f["subjects"].add((cc,t))

# ---------------- merge consecutive periods & emit ----------------
def mins(t): h,m=map(int,t.split(':')); return h*60+m
out=[]
for k,f in faculty.items():
    name=max(f["names"].items(),key=lambda x:(x[1],len(x[0])))[0]
    grp={}
    for c in sorted(f["classes"]):
        g=grp.setdefault(c[:5],{"sec":[],"room":[]})
        if c[5] not in g["sec"]: g["sec"].append(c[5])
        if c[6] and c[6] not in g["room"]: g["room"].append(c[6])
    def short(secs):
        secs=sorted(secs)
        if len(secs)==1: return secs[0]
        if len(secs)>4 and all(x.startswith("B.Tech") for x in secs): return "B.Tech CSE V (%d sections)"%len(secs)
        pre=secs[0].rsplit('-',1)[0] if all(x.startswith("B.Tech") for x in secs) else ''
        return (pre+'-'+', '.join(x.rsplit('-',1)[1] for x in secs)) if pre else '; '.join(secs)
    rows=[k+(short(v["sec"]),', '.join(sorted(v["room"])[:2])) for k,v in grp.items()]
    # merge back-to-back periods of the same class (labs, double periods); gaps <= 20 min are breaks
    by={}
    for r in rows: by.setdefault((r[0],r[3],r[4],r[5]),[]).append(r)
    merged=[]
    for _grp,lst in by.items():
        lst.sort(key=lambda r:mins(r[1]))
        cur=lst[0]
        for r in lst[1:]:
            if mins(r[1])-mins(cur[2])<=20: cur=(cur[0],cur[1],r[2])+cur[3:]
            else: merged.append(cur); cur=r
        merged.append(cur)
    merged.sort(key=lambda c:(c[0],mins(c[1]),c[3],c[5]))
    out.append({"id":k.replace(' ','-'),"name":name,
        "subjects":sorted(f["subjects"]),
        "classes":[list(c) for c in merged]})
out.sort(key=lambda x:re.sub(TITLES,'',x["name"].lower()))
print(len(out),'faculty')
for o in out: print(f'{o["name"]:<32}{len(o["classes"]):>3} classes  ', [s[0] for s in o["subjects"]][:6])
import hashlib
version=hashlib.sha1(json.dumps(out,sort_keys=True).encode()).hexdigest()[:12]   # changes whenever the data changes
js="// Generated from the uploaded university timetables (B.Tech CSE V, w.e.f. 13 July 2026;\n// BCA/B.Sc V UG_V_new.pdf). Regenerate when the timetable changes.\n// classes: [day(1=Mon..6=Sat), start, end, code, subject, section, room]\n// Contact numbers printed in the source PDF are deliberately NOT included.\nconst TIMETABLE = "+json.dumps({"source":"Graphic Era timetables, Vth semester 2026-27","version":version,"faculty":out},ensure_ascii=False,separators=(',',':'))+";\n"
open(OUT_PATH,'w').write(js)
print(len(js),'bytes')
