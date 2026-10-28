#!/usr/bin/env python3
"""
lote_tabla.py — Genera el .es.txt de un lote a partir de tablas inglés→español.

En los lotes de tools/lote.py el mismo texto en inglés se repite en muchas
escenas. En vez de traducir clave por clave, se escribe una tabla TSV con una
línea por texto único:
    texto en inglés<TAB>texto en español
y este script arma LOTE.es.txt poniendo a cada clave del lote la traducción de
su texto. Al final reporta cuántas piezas quedaron sin traducción.

Las tablas de cada lote se guardan en retraduccion/tablas/.

Uso: python3 tools/lote_tabla.py retraduccion/lote_e2_000-020.txt TABLA1.tsv [TABLA2.tsv ...]
     -> escribe retraduccion/lote_e2_000-020.es.txt

Para corregir una pieza en particular sin tocar la tabla (por ejemplo una
palabra corta que en otro contexto se traduce distinto), se agrega al final del
.es.txt una línea clave<TAB>español: tools/lote.py aplicar usa la última.
"""
import sys

tabla = {}
for nombre in sys.argv[2:]:
    for linea in open(nombre, encoding="utf-8"):
        if "\t" in linea:
            en, es = linea.rstrip("\n").split("\t", 1)
            tabla[en] = es

salida, faltan = [], []
for linea in open(sys.argv[1], encoding="utf-8"):
    if "\t" in linea and not linea.startswith("#"):
        clave, en = linea.rstrip("\n").split("\t", 1)
        if tabla.get(en):
            salida.append(f"{clave}\t{tabla[en]}")
        else:
            faltan.append((clave, en))

destino = sys.argv[1].replace(".txt", ".es.txt")
open(destino, "w", encoding="utf-8").write("\n".join(salida) + "\n")
print(len(salida), "faltan", len(faltan), faltan[:10])
