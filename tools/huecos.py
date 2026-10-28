#!/usr/bin/env python3
"""
huecos.py — Exporta y aplica de un jalon la traduccion de todas las frases entre
records que el extractor dejo fuera ("... Look...", "... Help..."), en todos los
archivos de texto (E?.BIN y talk/*.BIN).

tools/escena.py hace lo mismo por escena, solo para E?.BIN. Este script es para la
pasada completa. Los dos escriben en huecos.json, que usa scripts/reinsert.py al
encadenar los records vecinos (si la cadena no se arma, el hueco queda en ingles).

exportar escribe retraduccion/huecos_todos.json: por hueco, el archivo, su offset,
el final del record anterior y el inicio del siguiente (para tener contexto) y sus
piezas de texto {n, en, es}. Se traduce el campo "es" de cada pieza.

aplicar valida cada hueco igual que escena.py (piezas no vacias y sin tokens, texto
codificable; las etiquetas de hablante salen de glosario_hablantes.json) y lo
guarda en huecos.json. Si hay errores no escribe nada.

Uso: PERSONA_ISO_EU=... python3 tools/huecos.py exportar
     PERSONA_ISO_EU=... python3 tools/huecos.py aplicar
"""
import glob, json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from persona_codec import encode
from escena import (TOKEN, ESTADOS, OFFS, piezas, tramos, editable, huecos_texto,
                    _leer_huecos, _escribir_huecos)

SALIDA = os.path.join(ROOT, "retraduccion", "huecos_todos.json")
CONTEXTO = 80


def leer_bin(arch):
    """Bytes de arch ("e0.bin", "talk/basket.bin") en el ISO de PERSONA_ISO_EU."""
    carpeta, nombre = os.path.split(arch)
    clave = "/PSP_GAME/USRDIR/pack/" + (carpeta + "/" if carpeta else "") + nombre.upper()
    off, size = OFFS[clave]
    iso = os.environ.get("PERSONA_ISO_EU")
    if not iso:
        sys.exit("Falta PERSONA_ISO_EU")
    with open(iso, "rb") as f:
        f.seek(off)
        return f.read(size)


def legible(texto):
    """Texto para dar contexto: saltos de linea como espacio, sin otros tokens."""
    texto = re.sub(r"<FF03>", " ", texto, flags=re.I)
    return re.sub(r"\s+", " ", TOKEN.sub(" ", texto)).strip()


def por_archivo():
    """{archivo: records con traduccion ordenados por offset}."""
    out = {}
    for path in sorted(glob.glob(os.path.join(ROOT, "translated_text", "*.json"))):
        data = json.load(open(path, encoding="utf-8"))
        if not isinstance(data, list):
            continue
        for r in data:
            if r["estado"] in ESTADOS and r["texto_traducido"].strip():
                out.setdefault(r["archivo_origen"], []).append(r)
    for regs in out.values():
        regs.sort(key=lambda r: r["offset_inicio"])
    return out


def exportar():
    ya = _leer_huecos()
    lista = []
    for arch, regs in por_archivo().items():
        gaps = dict(huecos_texto(leer_bin(arch), regs, 0, 1 << 30))
        fin = {r["offset_fin"]: k for k, r in enumerate(regs)}
        for x, en in sorted(gaps.items()):
            k = fin[x]
            te = tramos(piezas(en))
            ts = tramos(piezas(ya[arch][x])) if x in ya.get(arch, {}) else te
            lista.append({
                "archivo": arch, "hueco": x,
                "antes": legible(regs[k]["texto_traducido"])[-CONTEXTO:],
                "despues": legible(regs[k + 1]["texto_traducido"])[:CONTEXTO],
                "piezas": [{"n": n, "en": te[n].strip(" "), "es": ts[n].strip(" ")}
                           for n in range(len(te)) if editable(te, n)]})
    os.makedirs(os.path.dirname(SALIDA), exist_ok=True)
    json.dump({"_meta": "Frases entre records. Traducir el campo 'es' de cada pieza "
                        "(ortografia completa). Aplicar con: python3 tools/huecos.py aplicar",
               "huecos": lista}, open(SALIDA, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"{SALIDA}: {len(lista)} huecos")


def aplicar():
    datos = json.load(open(SALIDA, encoding="utf-8"))["huecos"]
    glos = {k: v for k, v in json.load(open(os.path.join(ROOT, "glosario_hablantes.json"),
                                            encoding="utf-8")).items() if not k.startswith("_")}
    regs = por_archivo()
    gaps = {}
    errores, nuevos = [], {}
    for h in datos:
        arch, x = h["archivo"], h["hueco"]
        if arch not in gaps:
            gaps[arch] = dict(huecos_texto(leer_bin(arch), regs[arch], 0, 1 << 30))
        if x not in gaps[arch]:
            errores.append(f"{arch}@{x}: ya no esta entre dos records traducidos")
            continue
        te = TOKEN.split(gaps[arch][x])
        for p in h["piezas"]:
            en_p, es_p = te[p["n"]], p["es"].strip(" ")
            if TOKEN.search(es_p) or not es_p:
                errores.append(f"{arch}@{x} pieza {p['n']}: vacia o con tokens")
                continue
            lead = len(en_p) - len(en_p.lstrip(" "))
            tail = len(en_p) - len(en_p.rstrip(" "))
            te[p["n"]] = " " * lead + es_p + " " * tail
        for k in range(1, len(te) - 1):
            if te[k - 1].upper() == "<FF1B>" and te[k + 1].upper() == "<FF03>":
                te[k] = glos.get(te[k].strip(), te[k].strip())
        texto = "".join(te)
        try:
            encode(texto)
        except ValueError as e:
            errores.append(f"{arch}@{x}: {e}")
            continue
        nuevos.setdefault(arch, {})[x] = texto
    if errores:
        print("\n".join("  ERROR: " + e for e in errores))
        sys.exit("ABORTA: no se escribio nada")
    h = _leer_huecos()
    for arch, v in nuevos.items():
        h.setdefault(arch, {}).update(v)
    _escribir_huecos(h)
    print(f"huecos.json: {sum(len(v) for v in nuevos.values())} huecos")


if __name__ == "__main__":
    {"exportar": exportar, "aplicar": aplicar}.get(sys.argv[1:2] and sys.argv[1], lambda: sys.exit(__doc__))()
