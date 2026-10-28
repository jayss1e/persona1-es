#!/usr/bin/env python3
"""
dng.py — Traduce los mensajes de los calabozos y mapas (pack/dng/*/*.bin).

Esos archivos no estaban en la traducción original: ahí están mensajes como
"The door is locked." o las voces que se oyen tras una puerta. Cada mensaje vive
dentro de un bloque de evento que guarda su propio tamaño, así que el español se
escribe en el mismo lugar y ocupando exactamente los mismos bytes que el inglés,
rellenando con espacios al final. Nada se mueve.

Un "tramo" es una corrida de texto entre códigos de control. Dentro de un tramo
se permiten <FF03> (salto de línea) y <FF07> (nombre del protagonista). Tras
<004F> vienen parámetros que no se traducen: "C05" (color) o "N" (antes del
nombre de quien habla, que sí se traduce).

La traducción va en _dng/tabla.tsv (inglés <TAB> español, "#" para comentarios),
como las tablas de retraduccion/. El mismo inglés se traduce igual en todos lados.

  exportar ISO [SALIDA]   Lista los tramos únicos con su largo máximo, marcando
                          los que ya están en la tabla. Por omisión escribe
                          _dng/pendientes.tsv (no se sube, trae el inglés).
  revisar ISO             Dice cuántos tramos hay, cuántos están traducidos y
                          cuáles no caben.
  aplicar ISO_EN ISO_ES   Escribe la tabla sobre ISO_ES (la ISO ya armada).

Uso típico: python3 tools/dng.py aplicar "$PERSONA_ISO_EU" rebuilt/persona_es_full.iso
"""
import json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from persona_codec import code_to_char, encode

TABLA = os.path.join(ROOT, "_dng", "tabla.tsv")
EN_LINEA = {0xFF03: "<FF03>", 0xFF07: "<FF07>"}   # códigos que no cortan un tramo
PARAM = 0x004F
# Códigos que van justo antes de un mensaje real. Los datos del mapa también se
# leen como letras ("aaaa", "bba"), pero nunca vienen después de estos.
ANTES = {0x0043, 0x0028, 0x0055, 0xFF01, 0xFF02, 0xFF04, 0xFF0F, 0xFF21}


def archivos_dng():
    o = json.load(open(os.path.join(ROOT, "scripts", "iso_offsets.json")))
    return sorted((k, v) for k, v in o.items() if "/pack/dng/" in k.lower())


def es_texto(c):
    return c in EN_LINEA or code_to_char(c) is not None


def tramos(data):
    """Devuelve [(inicio, fin, texto)] de los tramos de mensaje de un archivo."""
    n = len(data) & ~1
    cod = [(data[i] << 8) | data[i + 1] for i in range(0, n, 2)]
    salida, i = [], 0
    while i < len(cod):
        if not es_texto(cod[i]):
            i += 1
            continue
        j = i
        while j < len(cod) and es_texto(cod[j]):
            j += 1
        a, b = i, j
        tras_param = a > 0 and cod[a - 1] == PARAM
        if tras_param:                                # saltar el parámetro de <004F>
            texto = "".join(code_to_char(c) or "?" for c in cod[a:a + 3])
            a += 3 if re.match(r"C\d\d", texto) else 1 if texto[:1] == "N" else 0
        k = i - 1                                     # código anterior, sin espacios
        while k >= 0 and cod[k] == 0:
            k -= 1
        while a < b and cod[a] == 0:                  # quitar espacios de las orillas
            a += 1
        while b > a and cod[b - 1] == 0:
            b -= 1
        texto = "".join(EN_LINEA.get(c) or code_to_char(c) for c in cod[a:b])
        if (tras_param or (k >= 0 and cod[k] in ANTES)) and len(texto) >= 3 \
                and re.search(r"[a-z]{2}|[A-Z]{3}", texto) \
                and len(set(texto.lower()) & set("aeiou")) >= 2:
            salida.append((2 * a, 2 * b, texto))
        i = j
    return salida


def leer_tabla():
    t = {}
    if os.path.exists(TABLA):
        for linea in open(TABLA, encoding="utf-8"):
            linea = linea.rstrip("\n")
            if linea and not linea.startswith("#"):
                en, es = linea.split("\t")
                t[en] = es
    return t


def recorrer(iso):
    with open(iso, "rb") as f:
        for ruta, (off, tam) in archivos_dng():
            f.seek(off)
            data = f.read(tam)
            for a, b, texto in tramos(data):
                yield ruta, off, a, b, texto


def codificar(es, largo):
    """Español codificado y rellenado con espacios hasta 'largo' bytes, o None si no cabe."""
    b = encode(es)
    return b + b"\x00" * (largo - len(b)) if len(b) <= largo else None


def exportar(iso, salida):
    tabla, vistos = leer_tabla(), {}
    for ruta, _, a, b, texto in recorrer(iso):
        r = vistos.setdefault(texto, [b - a, ruta.split("/pack/")[1], 0])
        r[0] = min(r[0], b - a)
        r[2] += 1
    os.makedirs(os.path.dirname(salida), exist_ok=True)
    with open(salida, "w", encoding="utf-8") as f:
        for texto, (largo, ruta, n) in vistos.items():
            if texto not in tabla:
                f.write(f"{largo // 2}\t{n}\t{ruta}\t{texto}\n")
    print(f"tramos únicos: {len(vistos)}, sin traducir: {sum(t not in tabla for t in vistos)} -> {salida}")


def revisar(iso):
    tabla, total, hechos, largos = leer_tabla(), 0, 0, []
    for ruta, _, a, b, texto in recorrer(iso):
        total += 1
        if texto in tabla:
            hechos += 1
            if codificar(tabla[texto], b - a) is None:
                largos.append((len(encode(tabla[texto])) // 2, (b - a) // 2, texto, tabla[texto]))
    print(f"tramos={total} traducidos={hechos} largos={len(largos)}")
    for n, m, en, es in largos:
        print(f"  LARGO {n}>{m}\t{en}\t{es}")
    return not largos


def aplicar(iso_en, iso_es):
    tabla, n = leer_tabla(), 0
    if not revisar(iso_en):
        sys.exit("ABORTA: hay traducciones que no caben, no se escribió nada")
    with open(iso_es, "r+b") as f:
        for ruta, off, a, b, texto in recorrer(iso_en):
            if texto in tabla:
                f.seek(off + a)
                f.write(codificar(tabla[texto], b - a))
                n += 1
    print(f"escritos: {n}")


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] not in ("exportar", "revisar", "aplicar"):
        sys.exit(__doc__)
    if sys.argv[1] == "exportar":
        exportar(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else os.path.join(ROOT, "_dng", "pendientes.tsv"))
    elif sys.argv[1] == "revisar":
        sys.exit(0 if revisar(sys.argv[2]) else 1)
    else:
        aplicar(sys.argv[2], sys.argv[3])
