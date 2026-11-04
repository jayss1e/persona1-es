#!/usr/bin/env python3
"""
buscar_ingles.py — Busca frases que siguen en inglés en la ISO ya traducida.

Decodifica cada archivo de pack/ con el codec del diálogo (palabras de 16 bits,
en las dos paridades) y parte el resultado en tramos de texto entre códigos (salvo los saltos de línea).
Marca los tramos con al menos dos palabras distintas que solo existen en inglés
("the", "you", "what"...). No ve el EBOOT ni las imágenes, que tienen su propia
codificación (ver tools/eboot_apply.py y tools/texto_imagenes.py).

Para cada tramo imprime el archivo, el offset dentro de él, si cae dentro de un
registro de translated_text/ (con su id) o entre registros, y el texto.

Uso: python3 tools/buscar_ingles.py ISO_ES [ARCHIVO...]
     (ARCHIVO filtra por nombre, por ejemplo E0.BIN o talk/BASKET.BIN)
"""
import glob, json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from persona_codec import decode

PACK = "/PSP_GAME/USRDIR/pack/"
OFFS = json.load(open(os.path.join(ROOT, "scripts", "iso_offsets.json")))
TOKEN = re.compile(r"<[0-9A-F]{4}>")
# Palabras que no existen (o casi no se usan) en español.
INGLES = {"the", "you", "your", "what", "this", "that", "is", "are", "it's", "i'm",
          "don't", "can't", "my", "with", "have", "for", "of", "to", "and", "be",
          "was", "it", "we", "they", "she", "not", "just", "if", "but", "so", "all",
          "there", "here", "know", "get", "got", "gonna", "what's", "who", "where"}
# Con este codec las letras nuevas (á, é, ¿...) salen como signos ("o%do"): cuentan
# como parte de la palabra para que "oído" no se lea como "o" y "do".
PALABRA = re.compile(r"[A-Za-z'%$#&*+=<]+")


def registros():
    """{nombre de archivo en minúsculas: [(inicio, fin, id)]} de translated_text/."""
    out = {}
    for ruta in glob.glob(os.path.join(ROOT, "translated_text", "*.json")):
        data = json.load(open(ruta, encoding="utf-8"))
        if not isinstance(data, list):
            continue
        for r in data:
            if r.get("archivo_origen") and str(r.get("offset_inicio", "")).isdigit():
                out.setdefault(r["archivo_origen"].lower(), []).append(
                    (int(r["offset_inicio"]), int(r["offset_fin"]), r["id"]))
    return out


def tramos(data):
    """[(offset, texto)] de los tramos de texto de data en las dos paridades. Cada
    código, letra o token, mide 2 bytes. Los saltos de línea no cortan el tramo."""
    for par in (0, 1):
        texto, _ = decode(data, par, len(data) - (len(data) - par) % 2)
        codigos, ini, actual = 0, 0, []
        for m in re.finditer(r"<[0-9A-F]{4}>|.", texto, re.S):
            if m.group(0) == "<FF03>":          # salto de línea: sigue la frase
                actual.append(" ")
            elif len(m.group(0)) > 1:
                if actual:
                    yield par + 2 * ini, "".join(actual)
                actual = []
                ini = codigos + 1
            else:
                actual.append(m.group(0))
            codigos += 1
        if actual:
            yield par + 2 * ini, "".join(actual)


def es_ingles(t):
    palabras = {w.lower() for w in PALABRA.findall(t)}
    return len(palabras & INGLES) >= 2


def main():
    iso, filtro = sys.argv[1], [f.upper() for f in sys.argv[2:]]
    regs = registros()
    f = open(iso, "rb")
    for ruta, (off, size) in sorted(OFFS.items()):
        if not ruta.startswith(PACK) or "MOVIE" in ruta:
            continue
        nombre = ruta[len(PACK):]
        if filtro and nombre.upper() not in filtro:
            continue
        f.seek(off)
        data = f.read(size)
        dentro = regs.get(os.path.basename(nombre).lower(), [])
        vistos = set()
        for o, t in tramos(data):
            t = t.strip()
            if len(t) < 6 or not es_ingles(t) or (o, t) in vistos:
                continue
            vistos.add((o, t))
            rid = next((i for a, z, i in dentro if a <= o < z), "-")
            print(f"{nombre}\t{o}\t{rid}\t{t}")


if __name__ == "__main__":
    main()
