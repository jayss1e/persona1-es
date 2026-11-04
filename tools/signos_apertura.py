#!/usr/bin/env python3
"""
signos_apertura.py — Agrega el ¿ o ¡ que les falta a las oraciones de los diálogos
retraducidos.

Recorre los bloques de retraduccion/ (ARCH_bNNN.json) y, con las reglas de
tools/ortografia.py para partir oraciones, busca en el texto final de cada registro
las oraciones que cierran con ? o ! y no tienen su signo de apertura en ninguna
parte ("Crees en ella, ¿eh?" sí lo tiene).

  revisar   Lista los casos, separados en dos grupos:
            - AUTO: la oración empieza dentro de una pieza editable del bloque, así
              que el signo va al inicio de la oración, en esa pieza.
            - MANO: la oración empieza al inicio del registro con minúscula o con
              un signo suelto ("! ¿¡Estás bien!?"). Casi siempre viene del registro
              anterior o de un nombre que inserta el juego, y hay que verla en
              contexto.
  aplicar   Hace los cambios AUTO:
            - pone el signo en el campo "es" de la pieza en el bloque,
            - agrega la pieza corregida al final del .es.txt de su lote, para que
              un "lote.py aplicar" posterior no la pierda, y
            - corre "escena.py aplicar" sobre cada bloque cambiado, que valida el
              largo de las líneas y escribe translated_text/.

  poner CLAVE TEXTO
            Corrige a mano una pieza (CLAVE es bNNN:id:n, como en los lotes): la
            pone en su bloque, en el .es.txt de su lote y aplica el bloque. Si la
            pieza no estaba en el bloque (una vacía entre dos códigos, donde va
            el signo antes de un nombre), la agrega.

Después hay que compilar y revisar que nada exceda (tools/compilar_lote.sh). Las
negociaciones no tienen reubicación de mensajes: cada registro tiene que caber en
su espacio en inglés.

Uso: python3 tools/signos_apertura.py revisar
     python3 tools/signos_apertura.py aplicar
     python3 tools/signos_apertura.py poner b120:e0_02042:10 "Si destruimos..."
"""
import glob, json, os, re, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import escena
from ortografia import oraciones, apertura

DIR = escena.DIR
TOKEN = escena.TOKEN
BLOQUE = re.compile(r"(.+)_b(\d{3})\.json$")


# Tokens que el juego cambia por un nombre o por comillas: una oración que empieza
# con ellos lleva el signo en la pieza vacía de antes ("¡<FF07>!", "¿<0028>...<0028>?").
INSERTOS = {"<FF07>", "<FF08>", "<FFF0>", "<FFF3>", "<FFF4>", "<0028>"}


# Piezas que las reglas tocarían mal: la oración del registro siguiente es de otro
# personaje ("pero allá va" + "es verdad?").
NO_TOCAR = {("e0_00619", 40)}

# Interjección sola al final de un registro: la pregunta empieza después de ella,
# en el registro siguiente.
INTERJECCION = re.compile(r"(?i)(hm+|mm+|eh|ah|uh|oh|ay|este)")


def faltantes(te):
    """[(n, j, frase, signos, todos)] de las oraciones de te (TOKEN.split del texto)
    sin su signo de apertura. n es el índice de la pieza (el mismo "n" de los
    bloques) y j la posición donde empieza la oración, frase su texto, signos lo
    que falta poner y todos los signos de apertura que le tocan."""
    out = []
    for (n, j), cierre in oraciones(te):
        resto = te[n][j:] + "".join(te[n + 1:])
        m = re.search(r"[!?]", resto)
        frase = resto[:m.end() + len(cierre)] if m else resto
        todos = apertura(cierre)
        signos = "".join(s for s in todos if s not in frase)
        if signos and "`" not in frase:
            out.append((n, j, frase, signos, todos))
    return out


def donde(te, n, j, signos, todos):
    """(pieza, posición) donde va el signo, o None si hay que verlo en contexto.
    - Si la oración ya tiene uno de sus dos signos, el otro va justo después
      ("¿Qué dices!?" -> "¿¡Qué dices!?", "Lo sabes, ¿verdad!?" -> "¿¡verdad").
    - Si empieza con un nombre o comillas que inserta el juego, en la pieza vacía de
      antes, cuando la hay.
    - Si empieza con mayúscula tras un espacio, al inicio de la oración.
    Las que empiezan con minúscula (vienen del registro anterior) o pegadas a lo
    anterior ("Mk.II!") se ven a mano."""
    if signos != todos:
        hecho = next(s for s in todos if s not in signos)
        k, pos = n, te[n].find(hecho, j)
        while pos < 0:
            k += 1
            pos = -1 if TOKEN.fullmatch(te[k]) else te[k].find(hecho)
        return k, pos + 1
    if te[n][j] in "!?" or (not te[n][:j].strip() and n > 0 and te[n - 1].upper() in INSERTOS):
        k = n - 1
        while k > 0 and te[k].upper() in INSERTOS and te[k - 1] == "":
            k -= 2
        if k + 1 < n and te[k + 1] == "" and (k < 0 or TOKEN.fullmatch(te[k])):
            return k + 1, 0
        return None
    if te[n][j].isalpha() and not te[n][j].islower() and (j == 0 or te[n][j - 1].isspace()):
        return n, j
    return None


def abierta_antes(anterior, signos):
    """True si la última oración del registro anterior sigue abierta y ya trae los
    signos ("¿Amo" + "Kei?"): la oración empieza ahí y no lleva otro signo."""
    tramo = re.split(r"\.\.\.|…|[.!?]", TOKEN.sub(" ", anterior))[-1]
    return all(s in tramo for s in signos)


def claves_lotes():
    """{clave bNNN:id:n: ruta del .es.txt de su lote}."""
    out = {}
    for ruta in sorted(glob.glob(os.path.join(DIR, "lote_*.txt"))):
        if ruta.endswith((".es.txt", ".en.txt")):
            continue
        es = ruta[:-4] + ".es.txt"
        for base in (ruta, ruta[:-4] + ".auto.en.txt"):
            if os.path.exists(base):
                for linea in open(base, encoding="utf-8"):
                    if "\t" in linea and not linea.startswith("#"):
                        out[linea.split("\t", 1)[0]] = es
    return out


def inicio_ultima(te):
    """(pieza, posición) donde empieza la última oración de te si quedó sin cerrar
    (sigue en el registro siguiente), o None si te termina con puntuación o con fin
    de mensaje."""
    ini = None
    for k in range(len(te) - 1, -1, -1):
        p = te[k]
        if TOKEN.fullmatch(p):
            if p.upper() == "<FF03>" or p.upper() in INSERTOS:
                continue
            return ini
        if not p.strip():
            continue
        if k > 0 and te[k - 1].upper() == "<FF1B>":       # etiqueta de hablante
            return None
        m = list(re.finditer(r"\.\.\.|…|[.!?]", p))
        resto = p[m[-1].end():] if m else p
        if resto.strip():
            ini = (k, len(p) - len(resto) + len(resto) - len(resto.lstrip()))
        if m:
            return ini if resto.strip() else None
    return ini


def casos():
    """Recorre los bloques y devuelve (bloques, mano). bloques: {ruta: bloque} de los
    que cambian, con el campo "nuevo" en cada pieza que lleva signos. mano:
    [(id, texto de la oración)].
    Si una oración empieza con minúscula al inicio del registro y el registro
    anterior quedó sin cerrar ("Acaso eres" + "mujer?"), el signo va en el
    anterior, al inicio de su última oración. Si lo que quedó abierto es solo una
    interjección ("Hmm"), va al inicio del registro: "Hmm... ¿un regalo?"."""
    datos, regs_bloque = {}, {}
    for ruta in sorted(glob.glob(os.path.join(DIR, "*_b[0-9][0-9][0-9].json"))):
        arch = BLOQUE.search(os.path.basename(ruta)).group(1)
        if arch not in datos:
            regs = sorted(escena.cargar(arch)[2], key=lambda r: int(r["offset_inicio"]))
            datos.update({r["id"]: (r, a) for r, a in zip(regs, [None] + regs)})
        bloque = json.load(open(ruta, encoding="utf-8"))
        for reg in bloque["registros"]:
            if "id" in reg:
                regs_bloque[reg["id"]] = (ruta, bloque, reg)
    signos_en, mano = {}, []                    # {(id, pieza): [(posición, signos)]}
    for rid in regs_bloque:
        r, anterior = datos[rid]
        te = TOKEN.split(r["texto_traducido"])
        primera = next((k for k, p in enumerate(te) if p.strip() and not TOKEN.fullmatch(p)), -1)
        for n, j, frase, signos, todos in faltantes(te):
            if n == primera and anterior and abierta_antes(anterior["texto_traducido"], signos):
                continue
            lugar, destino = donde(te, n, j, signos, todos), rid
            if (lugar is None and n == primera and te[n][j].islower() and anterior
                    and anterior["id"] in regs_bloque and signos == todos):
                ta = TOKEN.split(anterior["texto_traducido"])
                ini = inicio_ultima(ta)
                if ini:
                    tramo = ta[ini[0]][ini[1]:] + "".join(ta[ini[0] + 1:])
                    if INTERJECCION.fullmatch(TOKEN.sub("", tramo).strip()):
                        lugar = (n, j)          # "Hmm" + "un regalo?" -> "Hmm... ¿un regalo?"
                    elif not any(c in tramo for c in "¿¡"):
                        lugar, destino = ini, anterior["id"]
            if lugar is None or (destino, lugar[0]) in NO_TOCAR:
                mano.append((rid, frase[:70]))
                continue
            signos_en.setdefault((destino, lugar[0]), []).append((lugar[1], signos))
    bloques = {}
    for (rid, k), lista in signos_en.items():
        ruta, bloque, reg = regs_bloque[rid]
        te = TOKEN.split(datos[rid][0]["texto_traducido"])
        p = next((x for x in reg["piezas"] if x["n"] == k), None)
        if p is None:                           # pieza vacía o puntuación del inglés
            p = {"n": k, "en": te[k], "es": te[k].strip(" ")}
            reg["piezas"].append(p)
            reg["piezas"].sort(key=lambda x: x["n"])
        lead = len(te[k]) - len(te[k].lstrip(" "))
        es = p["es"].strip(" ")
        for pos, signos in sorted(set(lista), reverse=True):
            es = es[:pos - lead] + signos + es[pos - lead:]
        p["nuevo"] = es
        bloques[ruta] = bloque
    return bloques, mano


def piezas_nuevas(bloques):
    """[(ruta, clave del lote, pieza)] de cada pieza que cambia."""
    for ruta, bloque in bloques.items():
        for reg in bloque["registros"]:
            for p in reg.get("piezas", []):
                if "nuevo" in p:
                    yield ruta, "b%03d:%s:%d" % (bloque["bloque"], reg["id"], p["n"]), p


def revisar():
    bloques, mano = casos()
    nuevas = list(piezas_nuevas(bloques))
    for ruta, clave, p in nuevas:
        print("AUTO", clave, p["nuevo"], sep="\t")
    for rid, frase in mano:
        print("MANO", rid, frase, sep="\t")
    print(f"auto={len(nuevas)} mano={len(mano)}", file=sys.stderr)


def guardar(bloques, cambios):
    """Escribe los bloques, agrega cambios [(clave, texto)] al .es.txt de su lote y
    aplica cada bloque con escena.py."""
    claves = claves_lotes()
    por_lote = {}
    for clave, texto in cambios:
        if clave in claves:
            por_lote.setdefault(claves[clave], []).append(f"{clave}\t{texto}")
    for ruta, bloque in bloques.items():
        json.dump(bloque, open(ruta, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for es, lineas in por_lote.items():
        with open(es, "a", encoding="utf-8") as f:
            f.write("# signos de apertura (tools/signos_apertura.py)\n" + "\n".join(lineas) + "\n")
    for ruta in bloques:
        r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "escena.py"), "aplicar", ruta],
                           capture_output=True, text=True)
        if r.returncode:
            sys.exit(f"ABORTA en {ruta}:\n{r.stdout}{r.stderr}")
    return por_lote


def aplicar():
    bloques, _ = casos()
    cambios = []
    for ruta, clave, p in piezas_nuevas(bloques):
        p["es"] = p.pop("nuevo")
        cambios.append((clave, p["es"]))
    por_lote = guardar(bloques, cambios)
    print(f"piezas={len(cambios)} bloques={len(bloques)} lotes={len(por_lote)}")


def poner(clave, texto):
    b, rid, n = clave.split(":")
    arch = rid.rsplit("_", 1)[0]
    ruta = os.path.join(DIR, "%s_%s.json" % (arch, b))
    bloque = json.load(open(ruta, encoding="utf-8"))
    reg = next(r for r in bloque["registros"] if r.get("id") == rid)
    p = next((x for x in reg["piezas"] if x["n"] == int(n)), None)
    if p is None:
        te = TOKEN.split(next(r for r in escena.cargar(arch)[2] if r["id"] == rid)["texto_original"])
        p = {"n": int(n), "en": te[int(n)], "es": ""}
        reg["piezas"].append(p)
        reg["piezas"].sort(key=lambda x: x["n"])
    print(f"{clave}: {p['es']!r} -> {texto!r}")
    p["es"] = texto
    guardar({ruta: bloque}, [(clave, texto)])


if __name__ == "__main__":
    if sys.argv[1:] == ["revisar"]:
        revisar()
    elif sys.argv[1:] == ["aplicar"]:
        aplicar()
    elif sys.argv[1:2] == ["poner"] and len(sys.argv) == 4:
        poner(sys.argv[2], sys.argv[3])
    else:
        sys.exit(__doc__)
