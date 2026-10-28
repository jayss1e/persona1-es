#!/usr/bin/env python3
"""
lote_exceden.py — Muestra qué registros de un lote no caben y con qué piezas.

Lee docs/reinsert_report.md (lo escribe scripts/reinsert.py) y, para cada
registro marcado "excede_limite" que aparezca en el lote, imprime cuántos
caracteres sobran y sus piezas en inglés y en español, de la más larga a la más
corta, para decidir qué acortar.

Uso: python3 tools/lote_exceden.py retraduccion/lote_e2_000-020.txt
     (después de aplicar el lote y correr scripts/reinsert.py)
"""
import os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
lote = sys.argv[1]

# "- ⚠ e1_00034: excede_limite (120 > 108 bytes), ..."
sobran = {}
for linea in open(os.path.join(ROOT, "docs", "reinsert_report.md"), encoding="utf-8"):
    m = re.search(r"(\w+): excede_limite \((\d+) > (\d+) bytes\)", linea)
    if m:
        sobran[m.group(1)] = (int(m.group(2)) - int(m.group(3))) // 2  # u16 por carácter

def pares(ruta):
    if not os.path.exists(ruta):
        return {}
    d = {}
    for linea in open(ruta, encoding="utf-8"):
        if "\t" in linea and not linea.startswith("#"):
            k, t = linea.rstrip("\n").split("\t", 1)
            d[k] = t
    return d

en = pares(lote)
en.update(pares(lote.replace(".txt", ".auto.en.txt")))
es = pares(lote.replace(".txt", ".auto.es.txt"))
es.update(pares(lote.replace(".txt", ".es.txt")))

for rid, n in sobran.items():
    claves = [k for k in en if k.split(":")[1] == rid]
    if not claves:
        continue
    print(f"== {rid}: sobran {n} car")
    for k in sorted(claves, key=lambda k: -len(es.get(k, ""))):
        print(f"  {k}\t{len(es.get(k, ''))}\t{en[k]!r} -> {es.get(k, '')!r}")
