#!/usr/bin/env python3
"""
eboot_nombres.py — Mete en trans_records.json las correcciones de nombres del EBOOT.

Lee _eboot/trans_nombres.tsv (inglés exacto <TAB> español, "#" para comentarios) y
reemplaza esas entradas en _eboot/trans_records.json. Antes valida con la misma regla
que tools/eboot_apply.py: el español codificado no puede ocupar más bytes que el
inglés (cada letra, también las acentuadas, ocupa 2 bytes) y debe tener los mismos
tokens <FFxx>. Las que no cumplen no se escriben y se listan con su largo, para
acortarlas en el .tsv.

Uso: python3 tools/eboot_nombres.py _eboot/trans_nombres.tsv _eboot/trans_records.json
     Correrlo antes del paso de eboot_apply.py de notas/compilar.md.
"""
import json, os, re, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from persona_codec import encode

TOK = re.compile(r"<FF[0-9A-Fa-f]{2}>")
tsv, ruta = sys.argv[1], sys.argv[2]
tr = json.load(open(ruta, encoding="utf-8"))

ok, largos, otros = 0, [], []
for linea in open(tsv, encoding="utf-8"):
    linea = linea.rstrip("\n")
    if not linea or linea.startswith("#"):
        continue
    en, es = linea.split("\t")
    if en not in tr:
        otros.append(("no está en trans_records", en)); continue
    if TOK.findall(en) != TOK.findall(es):
        otros.append(("tokens distintos", en)); continue
    if len(encode(es)) > len(encode(en)):
        largos.append((en, es, len(encode(es)) // 2, len(encode(en)) // 2)); continue
    tr[en] = es
    ok += 1

json.dump(tr, open(ruta, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"aplicadas={ok} largas={len(largos)} otros={len(otros)}")
for en, es, n, m in largos:
    print(f"  LARGA {n}>{m}\t{en}\t{es}")
for motivo, en in otros:
    print(f"  {motivo}\t{en}")
