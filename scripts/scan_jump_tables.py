#!/usr/bin/env python3
"""
Escanea los bins ORIGINALES (desde el ISO limpio) buscando tablas de salto de
dialogo multi-NPC:  <FF01> <count u16 LE> ( <0000> <ptr u16 LE> ) * count
El engine salta a base+ptr, que en EN cae siempre sobre un <FF1B> (marcador de
hablante). La base se resuelve buscando B tal que TODOS los ptrs caigan en un
<FF1B> del bin (ventana de 64KB antes de la tabla).

Salida: rebuilt/jump_targets.json  { "e2.bin": [offset_abs, ...], ... }
reinsert.py la usa para anclar esos <FF1B> a su offset absoluto original.
"""
import os, sys, json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ISO_SRC = "/mnt/c/Users/59395/Downloads/Shin Megami Tensei - Persona (Europe) (PSP) (PSN)/Shin Megami Tensei - Persona (Europe) (PSP) (PSN).iso"
OFFS = json.load(open(os.path.join(ROOT, "scripts", "iso_offsets.json")))

# bins con texto de historia/evento (records en translated_text usan estos nombres)
BINS = {
    "e0.bin": "/PSP_GAME/USRDIR/pack/E0.BIN",
    "e1.bin": "/PSP_GAME/USRDIR/pack/E1.BIN",
    "e2.bin": "/PSP_GAME/USRDIR/pack/E2.BIN",
    "e3.bin": "/PSP_GAME/USRDIR/pack/E3.BIN",
    "e4.bin": "/PSP_GAME/USRDIR/pack/E4.BIN",
}


def scan_bin(data):
    ff1b = set(i for i in range(0, len(data) - 1, 2)
               if data[i] == 0xFF and data[i + 1] == 0x1B)
    targets = set()
    tables = 0
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
        # resolver base: B tal que todos los ptrs caen en FF1B.
        # Los bloques empiezan a limite de sector (2048); la tabla vive al final
        # de su bloque -> base = el MAYOR candidato alineado con base+max(ptr) <= i.
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
            tables += 1
            for q in ptrs:
                targets.add(pick + q)
            i = p
        else:
            if len(bases) > 1:
                print(f"  ! tabla @{i:#x} ambigua sin base alineada -> ignorada", bases)
            i += 2
    return tables, sorted(targets)


def main():
    out = {}
    with open(ISO_SRC, "rb") as f:
        for name, key in BINS.items():
            off, size = OFFS[key]
            f.seek(off)
            data = f.read(size)
            tables, targets = scan_bin(data)
            out[name] = targets
            print(f"{name}: {tables} tablas, {len(targets)} targets")
    dst = os.path.join(ROOT, "rebuilt", "jump_targets.json")
    json.dump(out, open(dst, "w"), indent=0)
    print("->", dst)


if __name__ == "__main__":
    main()
