#!/usr/bin/env python3
"""
lote_signos.py — Busca oraciones de un lote que cierran con ! o ? sin su ¡ o ¿.

Junta, registro por registro, las piezas en español del lote (las de la memoria
y las de LOTE.es.txt, que mandan) y las parte en oraciones. Imprime cada oración
que termina en ! o ? y no tiene el signo de apertura, junto con la pieza donde
empieza.

Da falsos positivos cuando la oración empieza en el registro anterior o detrás
de un nombre que el juego inserta. Hay que revisar cada caso en contexto.

Uso: python3 tools/lote_signos.py retraduccion/lote_talk_kemono_000-000.txt
"""
import re, sys

lote = sys.argv[1]
base = lote[:-4] if lote.endswith(".txt") else lote


def leer(ruta):
    d = {}
    try:
        for linea in open(ruta, encoding="utf-8"):
            if "\t" in linea:
                k, v = linea.rstrip("\n").split("\t", 1)
                d[k] = v
    except FileNotFoundError:
        pass
    return d


es = leer(base + ".auto.es.txt")
es.update(leer(base + ".es.txt"))


def orden(k):
    m = re.search(r"_(\d+):(\d+)$", k)
    return (m.group(1), int(m.group(2))) if m else ("z", 0)


registros = {}
for k in sorted(es, key=orden):
    registros.setdefault(orden(k)[0], []).append(k)

for claves in registros.values():
    resto, inicio = "", None
    for k in claves:
        if not resto.strip():
            inicio = k
        resto += " " + es[k].strip()
        while True:
            m = re.search(r"\.*[!?]+(?=\s|$)|(?<!\.)(?<!Sr)(?<!Sra)(?<!Dr)(?<!St)\.(?!\.)(?=\s|$)", resto)  # "..." no cierra
            if not m:
                break
            oracion, resto = resto[:m.end()].strip(), resto[m.end():]
            if "!" in m.group(0) and "¡" not in oracion:
                print(f"{inicio}\t¡\t{oracion}")
            elif "?" in m.group(0) and "¿" not in oracion:
                print(f"{inicio}\t¿\t{oracion}")
            inicio = k
