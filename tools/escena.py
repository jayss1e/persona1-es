#!/usr/bin/env python3
"""
escena.py — Retraduccion por escenas: exporta un bloque de evento de E?.BIN a un
archivo legible y aplica de vuelta lo traducido, con validacion.

Una escena es un bloque de evento (ver tools/fix_text_pointers.blocks). El archivo
retraduccion/<archivo>_b<NNN>.json tiene, por record, solo sus PIEZAS DE TEXTO:
los tramos entre tokens <....> con al menos dos caracteres, alguna letra y sin
backticks (lo demas son datos del script o puntuacion suelta). Los tokens no se
pueden editar, asi que no se pueden romper.

Al aplicar:
  - cada pieza ES reemplaza a su pieza EN; los espacios al inicio y al final se
    copian de la pieza EN (importan donde el extractor corto a media frase);
  - las etiquetas de hablante (<FF1B>Nombre<FF03>) salen de glosario_hablantes.json;
    una clave "Nombre@id_registro" cambia la etiqueta solo en ese registro (para
    registros sin espacio para el nombre largo);
  - se revisa el largo de cada linea: mas de 44 caracteres es aviso, mas de 48 es
    error (en ingles el 99.9% mide 44 o menos y la mas larga 48); tambien avisa si
    la ultima palabra de una linea se repite al inicio de la siguiente (pasa al
    reacomodar una frase entre dos lineas y olvidar la segunda);
  - el record queda con estado "retraducido".

HUECOS: el extractor dejo fuera de los records algunas frases cortas entre dos
records ("... Look...", "... Help..."). Se exportan en su lugar, entre los
registros, como {"hueco": offset, "piezas": [...]}. Al aplicar, su traduccion
completa (con los tokens del ingles) se guarda en huecos.json, que usa
scripts/reinsert.py al encadenar los records vecinos. Si la cadena no se puede
armar, el hueco se queda en ingles.

El espacio extra que necesite la traduccion lo resuelve scripts/reinsert.py
(cadenas de records y tools/reubicar_mensajes.py).

Uso: python3 tools/escena.py exportar e0 0       (archivo, numero de bloque)
     python3 tools/escena.py aplicar retraduccion/e0_b000.json
"""
import json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from persona_codec import encode, decode
from fix_text_pointers import blocks, OFFS
from migrar_puntuacion import json_style

DIR = os.path.join(ROOT, "retraduccion")
HUECOS = os.path.join(ROOT, "huecos.json")
ESTADOS = ("traducido", "shortened", "retraducido")
TOKEN = re.compile(r"(<[0-9A-Fa-f]{4}>)")
LINE_BREAK = re.compile(r"<FF0[1234]>|<FFF5>|<FFEF>|<FFFD>|<FF1B>", re.I)
AVISO, MAXIMO = 44, 48


def piezas(text):
    """Parte text en tokens y tramos; devuelve la lista (los tokens en su lugar)."""
    return [p for p in TOKEN.split(text) if p != ""]


def editable(lista, k):
    """True si la pieza k es texto traducible (ver docstring)."""
    p = lista[k]
    if TOKEN.fullmatch(p) or "`" in p or len(p.strip(" ")) < 2:
        return False
    if not any(c.isalpha() for c in p):
        return False
    return not (k > 0 and lista[k - 1].upper() == "<FF1B>")      # etiqueta


def lineas(text):
    """Lineas de dialogo de text (cada token cuenta como un caracter). Descarta lo
    que no parece dialogo (mas de 120 caracteres, menos de 60% letras o mas de 4
    tokens): son datos del script. Mismo filtro que herramientas/lineas_largas.py."""
    for l in LINE_BREAK.split(text):
        l = TOKEN.sub("#", l).strip(" ")
        if (3 <= len(l) <= 120 and sum(c.isalpha() for c in l) / len(l) > 0.6
                and l.count("#") <= 4):
            yield l


def tramos(lista):
    """Piezas -> lista alternada [tramo, token, tramo, ..., tramo]."""
    out, cur = [], ""
    for p in lista:
        if TOKEN.fullmatch(p):
            out.append(cur); out.append(p); cur = ""
        else:
            cur += p
    out.append(cur)
    return out


def huecos_texto(data, regs, a, b):
    """[(offset, texto EN)] de los huecos con frase entre records del bloque [a, b).
    Son los mismos que encadena scripts/reinsert.py (_chain): entre records
    consecutivos con traduccion, de puro texto (_gap_ok) y con alguna pieza editable.
    data: bytes del archivo .BIN."""
    from reinsert import _gap_ok, GAP_MAX
    out = []
    for p, n in zip(regs, regs[1:]):
        x, z = p["offset_fin"], n["offset_inicio"]
        if not (a <= x < b and 0 < z - x <= GAP_MAX):
            continue
        gap = data[x:z]
        if not _gap_ok(gap, p["texto_original"]):
            continue
        en = decode(gap, 0, len(gap))[0]
        t = tramos(piezas(en))
        if any(editable(t, k) for k in range(len(t))):
            out.append((x, en))
    return out


def leer_bin(arch):
    """Bytes del archivo arch (p. ej. "e0") en el ISO de PERSONA_ISO_EU.
    Las negociaciones se llaman "talk_basket" y viven en pack/talk/BASKET.BIN."""
    if arch.startswith("talk_"):
        ruta = "/PSP_GAME/USRDIR/pack/talk/%s.BIN" % arch[5:].upper()
    else:
        ruta = "/PSP_GAME/USRDIR/pack/%s.BIN" % arch.upper()
    off, size = OFFS[ruta]
    iso = os.environ.get("PERSONA_ISO_EU")
    if not iso:
        sys.exit("Falta PERSONA_ISO_EU")
    with open(iso, "rb") as f:
        f.seek(off)
        return f.read(size)


def cargar(arch):
    path = os.path.join(ROOT, "translated_text", arch + ".json")
    raw = open(path, encoding="utf-8").read()
    return path, raw, json.loads(raw)


def exportar(arch, n):
    path, _, data = cargar(arch)
    binario = leer_bin(arch)
    # En las negociaciones el archivo entero es un solo bloque: las respuestas del
    # jugador ("Qué pena", "Ven igual") quedan fuera de los bloques de la cabecera.
    a, b = (0, len(binario)) if arch.startswith("talk_") else blocks(binario)[n]
    todos = [r for r in sorted(data, key=lambda r: r["offset_inicio"])
             if r["estado"] in ESTADOS and r["texto_traducido"].strip()]
    ya = _leer_huecos().get(arch + ".bin", {})
    huecos = {x: en for x, en in huecos_texto(binario, todos, a, b)}
    regs = []
    for r in todos:
        if not a <= r["offset_inicio"] < b:
            continue
        en = piezas(r["texto_original"])
        es = piezas(r["texto_traducido"])
        if [p for p in en if TOKEN.fullmatch(p)] != [p for p in es if TOKEN.fullmatch(p)]:
            print(f"  ! {r['id']}: tokens distintos EN/ES, se exporta sin traduccion previa")
            es = en
        # alinear piezas EN/ES por posicion entre tokens
        te, ts = tramos(en), tramos(es)
        items = [{"n": k, "en": te[k].strip(" "), "es": ts[k].strip(" ")}
                 for k in range(len(te)) if editable(te, k)]
        if items:
            regs.append({"id": r["id"], "piezas": items})
        x = r["offset_fin"]
        if x in huecos:
            te = tramos(piezas(huecos[x]))
            ts = tramos(piezas(ya[x])) if x in ya else te
            regs.append({"hueco": x, "piezas": [
                {"n": k, "en": te[k].strip(" "), "es": ts[k].strip(" ")}
                for k in range(len(te)) if editable(te, k)]})
    out = {"_meta": "Escena %s bloque %d. Traducir el campo 'es' de cada pieza segun "
                    "notas/estilo.md. Con ortografia completa: acentos, enie, ¿ y ¡ "
                    "(tools/fuente.py). Los 'hueco' son frases sueltas entre registros. "
                    "Aplicar con: python3 tools/escena.py aplicar <este archivo>" % (arch, n),
           "archivo": arch, "bloque": n, "registros": regs}
    os.makedirs(DIR, exist_ok=True)
    dest = os.path.join(DIR, "%s_b%03d.json" % (arch, n))
    json.dump(out, open(dest, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    nh = sum("hueco" in r for r in regs)
    print(f"{dest}: {len(regs) - nh} registros, {nh} huecos, "
          f"{sum(len(r['piezas']) for r in regs)} piezas")


def _leer_huecos():
    """{archivo: {offset: texto ES}} de huecos.json."""
    if not os.path.exists(HUECOS):
        return {}
    d = json.load(open(HUECOS, encoding="utf-8"))
    return {k: {int(o): t for o, t in v.items()} for k, v in d.items() if not k.startswith("_")}


def _escribir_huecos(h):
    out = {"_meta": "Traduccion de las frases entre records que el extractor no saco. "
                    "archivo -> offset del hueco en el .BIN -> texto ES con los mismos "
                    "tokens que el ingles. Lo escribe tools/escena.py y lo usa "
                    "scripts/reinsert.py."}
    for arch in sorted(h):
        out[arch] = {str(o): h[arch][o] for o in sorted(h[arch])}
    json.dump(out, open(HUECOS, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open(HUECOS, "a").write("\n")


def aplicar(escena_path):
    esc = json.load(open(escena_path, encoding="utf-8"))
    path, raw, data = cargar(esc["archivo"])
    kw, nl = json_style(raw, data)
    glos = {k: v for k, v in json.load(open(os.path.join(ROOT, "glosario_hablantes.json"),
                                            encoding="utf-8")).items() if not k.startswith("_")}
    by_id = {r["id"]: r for r in data}
    errores, avisos, nuevos, nuevos_h = [], [], {}, {}
    gaps = None
    for reg in esc["registros"]:
        if "hueco" in reg:
            if gaps is None:
                regs = [r for r in sorted(data, key=lambda r: r["offset_inicio"])
                        if r["estado"] in ESTADOS and r["texto_traducido"].strip()]
                gaps = dict(huecos_texto(leer_bin(esc["archivo"]), regs, 0, 1 << 30))
            if reg["hueco"] not in gaps:
                errores.append(f"hueco {reg['hueco']}: ya no esta entre dos records traducidos")
                continue
            en_texto = gaps[reg["hueco"]]
        else:
            en_texto = by_id[reg["id"]]["texto_original"]
        nombre = reg.get("id") or "hueco %d" % reg["hueco"]
        te = TOKEN.split(en_texto)                      # tramos y tokens alternados
        for p in reg["piezas"]:
            k, en_p, es_p = p["n"], te[p["n"]], p["es"].strip(" ")
            if TOKEN.search(es_p) or not es_p:
                errores.append(f"{nombre} pieza {k}: vacia o con tokens")
                continue
            lead = len(en_p) - len(en_p.lstrip(" "))
            tail = len(en_p) - len(en_p.rstrip(" "))
            te[k] = " " * lead + es_p + " " * tail
        for k in range(1, len(te) - 1):                # etiquetas de hablante
            if te[k - 1].upper() == "<FF1B>" and te[k + 1].upper() == "<FF03>":
                nombre_en = te[k].strip()          # "Nombre@registro" gana sobre "Nombre"
                te[k] = glos.get(f"{nombre_en}@{reg.get('id')}", glos.get(nombre_en, nombre_en))
        texto = "".join(te)
        try:
            encode(texto)
        except ValueError as e:
            errores.append(f"{nombre}: {e}")
            continue
        ls = list(lineas(texto))
        sin_etiquetas = list(lineas(re.sub(r"<FF1B>[^<]*<FF03>", "<FF1B>", texto, flags=re.I)))
        for l1, l2 in zip(sin_etiquetas, sin_etiquetas[1:]):   # "...el cerebro," / "cerebro, ..."
            w1 = re.findall(r"[A-Za-z]+", l1)[-1:]
            w2 = re.findall(r"[A-Za-z]+", l2)[:1]
            if w1 and w1 == w2:
                avisos.append(f"{nombre}: palabra repetida entre lineas: {l1} / {l2}")
        for l in ls:
            if len(l) > MAXIMO:
                errores.append(f"{nombre}: linea de {len(l)} > {MAXIMO}: {l}")
            elif len(l) > AVISO:
                avisos.append(f"{nombre}: linea de {len(l)}: {l}")
        if "hueco" in reg:
            nuevos_h[reg["hueco"]] = texto
        else:
            nuevos[reg["id"]] = texto
    for a in avisos:
        print("  aviso:", a)
    if errores:
        print("\n".join("  ERROR: " + e for e in errores))
        sys.exit("ABORTA: no se escribio nada")
    for rid, texto in nuevos.items():
        by_id[rid]["texto_traducido"] = texto
        by_id[rid]["estado"] = "retraducido"
    open(path, "w", encoding="utf-8").write(json.dumps(data, **kw) + nl)
    if nuevos_h:
        h = _leer_huecos()
        h.setdefault(esc["archivo"] + ".bin", {}).update(nuevos_h)
        _escribir_huecos(h)
    print(f"{esc['archivo']}: {len(nuevos)} registros retraducidos, {len(nuevos_h)} huecos")


if __name__ == "__main__":
    if sys.argv[1] == "exportar":
        exportar(sys.argv[2], int(sys.argv[3]))
    elif sys.argv[1] == "aplicar":
        aplicar(sys.argv[2])
    else:
        sys.exit(__doc__)
