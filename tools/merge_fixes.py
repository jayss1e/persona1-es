#!/usr/bin/env python3
"""
merge_fixes.py — Fusiona los resultados de los agentes:
  1. scratchpad/verbfix/slice_XX_fixed.json -> translated_text/*.json (por id)
     Solo si el record VALIDA (tokens intactos + per-run fit). Reporta rechazos.
  2. scratchpad/ebgap/eb_XX_fixed.json -> _eboot/trans_records.json (por texto_original)
     Claves ya presentes en trans_records.json NO se sobreescriben (manuales ganan).
Uso: python3 tools/merge_fixes.py <scratchpad_dir>
"""
import os, sys, json, glob, re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from persona_codec import encode

SCR = sys.argv[1]
TOKEN = re.compile(r"<[0-9A-Fa-f]{2,4}>")
def is_anchor(tk): return (not tk.startswith("<FF")) or tk == "<FFFF>"
def parts(text):
    segs = []; toks = []; last = 0
    for m in TOKEN.finditer(text):
        if is_anchor(m.group(0)):
            segs.append(text[last:m.start()]); toks.append(m.group(0)); last = m.end()
    segs.append(text[last:])
    return segs, toks

def valida_story(orig, trad):
    if sorted(TOKEN.findall(orig)) != sorted(TOKEN.findall(trad)):
        return "tokens"
    try:
        encode(trad)
    except ValueError as e:
        return f"encode:{e}"
    os_, ot = parts(orig); ts, tt = parts(trad)
    if ot != tt or len(os_) != len(ts):
        return "anclas"
    for i, seg in enumerate(ts):
        if len(encode(re.sub(r"  +", " ", seg))) > len(encode(os_[i])):
            return f"run{i}"
    return None

# --- 1. historia ---
fixes = {}
for ff in sorted(glob.glob(os.path.join(SCR, "verbfix", "slice_*_fixed.json"))):
    for e in json.load(open(ff)):
        fixes[e["id"]] = e["texto_traducido"]
print("fixes de historia:", len(fixes))
aplicados = rech = 0
rechazos = []
for jf in sorted(glob.glob(os.path.join(ROOT, "translated_text", "*.json"))):
    recs = json.load(open(jf))
    if not isinstance(recs, list):
        continue
    dirty = False
    for r in recs:
        if r["id"] in fixes:
            nuevo = fixes[r["id"]]
            err = valida_story(r["texto_original"], nuevo)
            if err:
                rech += 1; rechazos.append((r["id"], err))
                continue
            if r["texto_traducido"] != nuevo:
                r["texto_traducido"] = nuevo; dirty = True
            aplicados += 1
    if dirty:
        json.dump(recs, open(jf, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"historia: aplicados={aplicados} rechazados={rech}")
for x in rechazos[:20]: print("   RECHAZO", x)

# --- 2. eboot ---
trp = os.path.join(ROOT, "_eboot", "trans_records.json")
tr = json.load(open(trp))
nuevos = colis = 0
for ff in sorted(glob.glob(os.path.join(SCR, "ebgap", "eb_*_fixed.json"))):
    for e in json.load(open(ff)):
        en, es = e["texto_original"], e["texto_traducido"]
        if not es:
            continue
        if en in tr:
            colis += 1; continue   # manual/anterior gana
        tr[en] = es; nuevos += 1
json.dump(tr, open(trp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"eboot: nuevos={nuevos} colisiones(respetadas)={colis} total={len(tr)}")
