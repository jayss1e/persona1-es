#!/usr/bin/env python3
"""
registros_previos.py — Agrega como registro el principio de un mensaje que el
extractor dejó fuera, entre datos del script y el registro que lo continúa.

Antes de algunos registros hay una etiqueta de hablante y una frase corta que
nunca se extrajeron ("<FF1B>Yukino<FF03>What the..." y luego el registro
" Who's that on the floor?"). Como el registro anterior no tiene traducción (es
del script), scripts/reinsert.py no los ve como hueco y quedan en inglés, con
todo y la etiqueta. Este script los convierte en registros propios, que van de la
etiqueta hasta el inicio del registro siguiente. reinsert.py los encadena con ese
registro, así que el espacio que sobra o falta se reparte entre los dos.

  buscar      Lista los casos: id del registro siguiente, etiqueta y frase.
  agregar TSV Lee id<TAB>texto en español (con la etiqueta, por ejemplo
              "<FF1B>Yukino<FF03>Pero qué...") y escribe en translated_text/ un
              registro "<id>p" por línea, con estado "retraducido". Los tokens
              deben ser los mismos que en inglés. Si el registro ya existe, lo
              actualiza.

Uso: PERSONA_ISO_EU=... python3 tools/registros_previos.py buscar
     PERSONA_ISO_EU=... python3 tools/registros_previos.py agregar retraduccion/previos.tsv
"""
import glob, json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from persona_codec import decode, encode
from escena import OFFS, TOKEN, ESTADOS, cargar
from migrar_puntuacion import json_style

VENTANA = 160           # bytes antes del registro donde se busca la etiqueta
GAP_MAX = 256           # mismo límite que scripts/reinsert.py para los huecos
ETIQUETA = re.compile(r"<FF1B>([^<]{1,30})<FF03>((?:[^<]|<FF03>)+)$")


def leer_bin(arch):
    """Bytes de arch ("e1.bin", "talk/basket.bin") en el ISO de PERSONA_ISO_EU."""
    carpeta, nombre = os.path.split(arch)
    off, size = OFFS["/PSP_GAME/USRDIR/pack/" + (carpeta + "/" if carpeta else "") + nombre.upper()]
    with open(os.environ["PERSONA_ISO_EU"], "rb") as f:
        f.seek(off)
        return f.read(size)


def casos():
    """[(archivo JSON, registro siguiente, offset de la etiqueta, texto EN)]."""
    out = []
    for ruta in sorted(glob.glob(os.path.join(ROOT, "translated_text", "*.json"))):
        data = json.load(open(ruta, encoding="utf-8"))
        if not isinstance(data, list):
            continue
        regs = sorted((r for r in data if isinstance(r.get("offset_inicio"), int)
                       and not r["id"].endswith("p")), key=lambda r: r["offset_inicio"])
        try:
            datos = leer_bin(regs[0]["archivo_origen"])
        except (IndexError, KeyError):  # sin registros o fuera de pack/ (bt_menu...)
            continue
        for anterior, r in zip([None] + regs, regs):
            a = int(r["offset_inicio"])
            if r["estado"] not in ESTADOS or a < VENTANA:
                continue
            if (anterior and anterior["estado"] in ESTADOS
                    and a - int(anterior["offset_fin"]) <= GAP_MAX):
                continue                # hueco normal: lo cubre reinsert.py
            texto, _ = decode(datos, a - VENTANA, a)
            m = ETIQUETA.search(texto)
            if not m or sum(c.isalpha() for c in m.group(2)) < 2:
                continue
            ini = a - VENTANA + 2 * len(TOKEN.sub("#", texto[:m.start()]))
            out.append((ruta, r, ini, m.group(0)))
    return out


def buscar():
    for ruta, r, ini, en in casos():
        print(r["id"], en, sep="\t")


def agregar(tsv):
    es = {}
    for linea in open(tsv, encoding="utf-8"):
        if "\t" in linea and not linea.startswith("#"):
            k, v = linea.rstrip("\n").split("\t", 1)
            es[k] = v
    por_ruta = {}
    for ruta, r, ini, en in casos():
        if r["id"] in es:
            por_ruta.setdefault(ruta, []).append((r, ini, en, es.pop(r["id"])))
    if es:
        sys.exit(f"ABORTA: no se encontraron {sorted(es)}")
    errores, n, escribir = [], 0, []
    for ruta, lista in por_ruta.items():
        raw = open(ruta, encoding="utf-8").read()
        data = json.loads(raw)
        kw, nl = json_style(raw, data)
        por_id = {x["id"]: x for x in data}
        for r, ini, en, texto in lista:
            if TOKEN.findall(en) != TOKEN.findall(texto):
                errores.append(f"{r['id']}: tokens distintos al inglés")
                continue
            try:
                encode(texto)
            except ValueError as e:
                errores.append(f"{r['id']}: {e}")
                continue
            datos = leer_bin(r["archivo_origen"])
            fin = int(r["offset_inicio"])
            nuevo = {"id": r["id"] + "p", "archivo_origen": r["archivo_origen"],
                     "offset_inicio": ini, "offset_fin": fin,
                     "longitud_original_bytes": fin - ini, "limite_chars": (fin - ini) // 2,
                     "tokens_protegidos": TOKEN.findall(en), "texto_original": en,
                     "texto_traducido": texto, "notas": "inicio de mensaje fuera del extractor "
                     "(tools/registros_previos.py)", "estado": "retraducido",
                     "raw_hex": datos[ini:fin].hex()}
            if nuevo["id"] in por_id:
                por_id[nuevo["id"]].update(nuevo)
            else:
                data.insert(data.index(por_id[r["id"]]), nuevo)
            n += 1
        escribir.append((ruta, json.dumps(data, **kw) + nl))
    if errores:
        sys.exit("\n".join("ERROR: " + e for e in errores) + "\nABORTA: no se escribió nada")
    for ruta, texto in escribir:
        open(ruta, "w", encoding="utf-8").write(texto)
    print(f"registros agregados o actualizados: {n}")


if __name__ == "__main__":
    if sys.argv[1:] == ["buscar"]:
        buscar()
    elif sys.argv[1:2] == ["agregar"] and len(sys.argv) == 3:
        agregar(sys.argv[2])
    else:
        sys.exit(__doc__)
