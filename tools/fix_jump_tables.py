#!/usr/bin/env python3
"""
fix_jump_tables.py — Reescribe los punteros de las tablas de salto de dialogo
multi-NPC (crowd chatter) para que apunten al texto ES reflowado.

Contexto (BUG 2 del playtest): cada bloque de evento tiene al final una tabla
  <FF01> <count u16 LE> ( <0000> <ptr u16 LE> ) * count
El engine salta a base+ptr (base = inicio de bloque, alineado a 2048). En EN
cada target cae sobre un <FF1B> (marcador de hablante). Tras traducir, el texto
entre anclas se reflowa (regla 4: solo <NNNN>/<FFFF> quedan fijos) y los <FF1B>
se mueven -> el salto cae a media palabra ("sa tienda rara...", "ajo sus
futones.").

Fix: los bytes de la tabla SI quedan fijos (los ptr son tokens <NNNN> = ancla).
Se reescribe el VALOR de cada ptr: el target EN es el k-esimo <FF1B> del bloque;
el nuevo ptr = posicion del k-esimo <FF1B> del bloque en el bin ES.

Uso: python3 tools/fix_jump_tables.py   (tras scripts/build_patch.py,
     antes de tools/build_unified.py; opera sobre rebuilt/persona_es_patched.iso)
"""
import os, json, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# ISO EU original; se puede indicar con la variable de entorno PERSONA_ISO_EU
ISO_SRC = os.environ.get("PERSONA_ISO_EU", "/mnt/c/Users/59395/Downloads/Shin Megami Tensei - Persona (Europe) (PSP) (PSN)/Shin Megami Tensei - Persona (Europe) (PSP) (PSN).iso")
ISO_ES = os.path.join(ROOT, "rebuilt", "persona_es_patched.iso")
OFFS = json.load(open(os.path.join(ROOT, "scripts", "iso_offsets.json")))
BINS = {
    "e0.bin": "/PSP_GAME/USRDIR/pack/E0.BIN",
    "e1.bin": "/PSP_GAME/USRDIR/pack/E1.BIN",
    "e2.bin": "/PSP_GAME/USRDIR/pack/E2.BIN",
    "e3.bin": "/PSP_GAME/USRDIR/pack/E3.BIN",
    "e4.bin": "/PSP_GAME/USRDIR/pack/E4.BIN",
}


def find_tables(data):
    """[(pos_tabla, base, [ptr,...])] — mismo algoritmo que scan_jump_tables."""
    ff1b = set(i for i in range(0, len(data) - 1, 2)
               if data[i] == 0xFF and data[i + 1] == 0x1B)
    out = []
    i = 0
    while i < len(data) - 8:
        if not (data[i] == 0xFF and data[i + 1] == 0x01):
            i += 2; continue
        n = data[i + 2]
        if data[i + 3] != 0 or not (2 <= n <= 96):
            i += 2; continue
        ptrs = []
        p = i + 4
        ok = True
        for _ in range(n):
            if p + 4 > len(data) or data[p] != 0 or data[p + 1] != 0:
                ok = False; break
            v = data[p + 2] | (data[p + 3] << 8)
            if v == 0 or v % 2 or (ptrs and v <= ptrs[-1]):
                ok = False; break
            ptrs.append(v); p += 4
        if not ok or len(ptrs) != n:
            i += 2; continue
        bases = []
        for t in ff1b:
            B = t - ptrs[0]
            if B < i - 65536 or B > i:
                continue
            if B + ptrs[-1] <= i and all((B + q) in ff1b for q in ptrs):
                bases.append(B)
        aligned = sorted(b for b in bases if b % 2048 == 0)
        pick = aligned[-1] if aligned else (bases[0] if len(bases) == 1 else None)
        if pick is not None:
            out.append((i, pick, ptrs))
            i = p
        else:
            i += 2
    return out


def ff1b_positions(data, lo, hi):
    return [i for i in range(lo, min(hi, len(data) - 1), 2)
            if data[i] == 0xFF and data[i + 1] == 0x1B]


# ---------- pase famB: gotos de script (FF55/FF54/FF26/FF22/FF30/FF44/FF45/FF47) ----------
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from persona_codec import code_to_char  # noqa: E402

FAMB = {0xFF55, 0xFF54, 0xFF26, 0xFF22, 0xFF30, 0xFF44, 0xFF45, 0xFF47}


def load_records(name):
    import glob
    out = []
    for jf in glob.glob(os.path.join(ROOT, "translated_text", "*.json")):
        recs = json.load(open(jf, encoding="utf-8"))
        if not isinstance(recs, list):
            continue
        for r in recs:
            if r.get("archivo_origen") == name and r.get("estado") in ("traducido", "shortened", "retraducido") \
               and r.get("texto_traducido", "").strip():
                out.append((r["offset_inicio"], r["offset_fin"], r["id"]))
    out.sort()
    return out


def lex_tokens(data, lo, hi):
    """posiciones de tokens (palabras no-texto) en [lo,hi)."""
    pos = []
    for i in range(lo, min(hi, len(data) - 1), 2):
        w = (data[i] << 8) | data[i + 1]
        if w != 0 and code_to_char(w) is None:
            pos.append(i)
    return pos


def famb_sites(data):
    """[(slot_offset, ptr_val)] con parseo estricto: FFxx, arg (bytes b,0 con b<=0x30
    o 0000), ptr u16 LE par y no-cero."""
    sites = []
    for i in range(0, len(data) - 6, 2):
        w0 = (data[i] << 8) | data[i + 1]
        if w0 not in FAMB:
            continue
        if data[i + 3] != 0 or data[i + 2] > 0x30:
            continue
        v = data[i + 4] | (data[i + 5] << 8)
        if v == 0 or v % 2:
            continue
        sites.append((i + 4, v))
    return sites


def famb_pass(name, EN, ES, bin_off):
    import bisect
    recs = load_records(name)
    starts = [r[0] for r in recs]
    writes = []
    stats = {"tok": 0, "unchanged": 0, "midtext": 0, "space": 0, "outside": 0,
             "lexmis": 0, "slotmoved": 0, "textop": 0}
    midtext_log = []
    lexcache = {}
    for slot, v in famb_sites(EN):
        # GUARDA 1: operando EN debe ser token no-texto (<NNNN>); si decodifica
        # como texto, el "sitio" es un falso positivo dentro de dialogo.
        opw = (EN[slot] << 8) | EN[slot + 1]
        if opw == 0 or code_to_char(opw) is not None:
            stats["textop"] += 1
            continue
        # GUARDA 2: el slot debe estar intacto en ES (ancla en su offset);
        # si difiere, el record destino/portador uso fallback -> no escribir.
        if ES[slot:slot + 2] != EN[slot:slot + 2]:
            stats["slotmoved"] += 1
            continue
        B = (slot // 2048) * 2048
        t = B + v
        j = bisect.bisect_right(starts, t) - 1
        if j < 0 or not (recs[j][0] <= t < recs[j][1]):
            stats["outside"] += 1
            continue
        lo, hi, rid = recs[j]
        if rid not in lexcache:
            en_t = lex_tokens(EN, lo, hi)
            es_t = lex_tokens(ES, lo, hi)
            lexcache[rid] = (en_t, es_t) if len(en_t) == len(es_t) else None
        lx = lexcache[rid]
        if lx is None:
            stats["lexmis"] += 1
            continue
        en_t, es_t = lx
        if t in en_t:
            k = en_t.index(t)
            nv = es_t[k] - B
            if nv == v:
                stats["unchanged"] += 1
            elif 0 < nv <= 0xFFFF and nv % 2 == 0:
                writes.append((bin_off + slot, bytes([nv & 0xFF, (nv >> 8) & 0xFF])))
                stats["tok"] += 1
            continue
        w = (EN[t] << 8) | EN[t + 1]
        if w == 0:
            # target sobre espacios: mapear al siguiente token conservando distancia
            nxt = [p for p in en_t if p > t]
            if nxt:
                k = en_t.index(nxt[0])
                gap = nxt[0] - t
                nv = es_t[k] - B - gap
                base_floor = es_t[k - 1] + 2 - B if k > 0 else 0
                nv = max(nv, base_floor)
                if 0 < nv <= 0xFFFF and nv % 2 == 0 and nv != v:
                    writes.append((bin_off + slot, bytes([nv & 0xFF, (nv >> 8) & 0xFF])))
                    stats["space"] += 1
                    continue
            stats["unchanged"] += 1
        else:
            stats["midtext"] += 1
            midtext_log.append({"bin": name, "record": rid, "slot": slot,
                                "ptr": v, "target": t})
    return writes, stats, midtext_log


def main():
    no_famb = "--no-famb" in sys.argv   # biseccion: solo tablas famA (crowd chatter)
    stats = {"tables": 0, "rewritten": 0, "unchanged": 0, "skipped": 0}
    all_midtext = []
    with open(ISO_SRC, "rb") as fo, open(ISO_ES, "r+b") as fe:
        for name, key in BINS.items():
            off, size = OFFS[key]
            fo.seek(off); EN = fo.read(size)
            fe.seek(off); ES = fe.read(size)
            writes = []
            for tpos, B, ptrs in find_tables(EN):
                en_ff1b = ff1b_positions(EN, B, tpos)
                es_ff1b = ff1b_positions(ES, B, tpos)
                if len(en_ff1b) != len(es_ff1b):
                    print(f"  ! {name} tabla @{tpos:#x}: FF1B count EN {len(en_ff1b)} != ES {len(es_ff1b)} -> saltada")
                    stats["skipped"] += 1
                    continue
                idx = {pos: k for k, pos in enumerate(en_ff1b)}
                stats["tables"] += 1
                for j, v in enumerate(ptrs):
                    t = B + v
                    k = idx.get(t)
                    if k is None:  # no deberia pasar (validado en find_tables)
                        stats["skipped"] += 1
                        continue
                    nv = es_ff1b[k] - B
                    slot = tpos + 4 + 4 * j + 2  # byte del ptr (tras el 0000)
                    # GUARDA: el slot debe seguir intacto en ES (ancla preservada);
                    # si difiere, el record se inserto con fallback y escribir
                    # aqui corromperia texto reflowado.
                    if ES[slot:slot + 2] != EN[slot:slot + 2]:
                        print(f"  ! {name} tabla @{tpos:#x} ptr{j}: slot movido en ES -> saltado")
                        stats["skipped"] += 1
                        continue
                    if nv == v:
                        stats["unchanged"] += 1
                        continue
                    if not (0 < nv <= 0xFFFF) or nv % 2:
                        print(f"  ! {name} tabla @{tpos:#x} ptr{j}: nuevo valor invalido {nv:#x}")
                        stats["skipped"] += 1
                        continue
                    writes.append((off + slot, bytes([nv & 0xFF, (nv >> 8) & 0xFF])))
                    stats["rewritten"] += 1
            if no_famb:
                bwrites, bstats, mlog = [], {"off": True}, []
            else:
                bwrites, bstats, mlog = famb_pass(name, EN, ES, off)
            all_midtext += mlog
            for pos, bb in writes + bwrites:
                fe.seek(pos); fe.write(bb)
            print(f"{name}: famA {len(writes)} ptrs, famB {bstats}")
    mt = os.path.join(ROOT, "docs", "midtext_targets.json")
    json.dump(all_midtext, open(mt, "w"), indent=1)
    print("== resumen famA ==", stats)
    print(f"midtext pendientes: {len(all_midtext)} -> {mt}")


if __name__ == "__main__":
    main()
