#!/usr/bin/env python3
"""
original.py — Quita del repo el texto original del juego y lo recupera de la ISO.

El repo público no lleva texto ni archivos del juego. Cada quien los saca de su
propia ISO europea (NPEH-00069) antes de compilar el parche.

  quitar [CARPETA]   Vacía (deja en null) los campos texto_original y raw_hex de
                     translated_text/*.json. Las claves se quedan en su lugar para
                     que restaurar deje el archivo idéntico. En las tablas que usan
                     el inglés como clave (ver CLAVES_JSON, CLAVES_TSV y LE), cambia
                     cada texto en inglés por su huella. CARPETA es la raíz del
                     repo a limpiar (por omisión, este repo).
  restaurar ISO      Hace todo lo que el compilador necesita sacar de la ISO:
                     - llena texto_original y raw_hex leyendo cada registro de su
                       archivo dentro de la ISO,
                     - extrae _tex/cm_menu.bin, _tex/sys.bin y _eboot/EBOOT.BIN,
                     - descifra el EBOOT con externos/pspdecrypt si existe, y
                     - regenera _eboot/eb_inventory.json con eboot_strings2.py, y
                     - cambia cada huella por su inglés, buscándolo entre los
                       textos del inventario, de los calabozos o del EBOOT.
  verificar ISO      Comprueba, sin escribir nada, que restaurar reproduce byte por
                     byte los translated_text/*.json actuales.

Después de restaurar se sigue la receta normal de notas/compilar.md.
"""
import glob, hashlib, json, os, re, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from persona_codec import char_to_code, code_to_char, decode

MD5_EU = "03ee31f32bcd3bb697de347fba4493d9"
PACK = "/PSP_GAME/USRDIR/pack/"
CAMPOS = ("texto_original", "raw_hex")
# En 8 registros, una migración vieja de puntuación dejó en texto_original un "."
# donde el codec actual lee "!" o al revés. Son solo signos y no se pueden deducir
# de la ISO, así que se guardan aparte: {id: [[posición, signo], ...]}.
EXCEPCIONES = os.path.join(ROOT, "tools", "original_signos.json")
EBOOT_OFF, EBOOT_LEN = 1245184, 3838272   # /PSP_GAME/SYSDIR/EBOOT.BIN

# Tablas que usan el texto en inglés como clave. En el repo público cada inglés
# se cambia por su huella y restaurar lo recupera de los textos que saca de la ISO.
# Las JSON van con las claves del inventario del EBOOT. Las TSV dicen de dónde
# sale su primera columna.
CLAVES_JSON = ("_eboot/trans_records.json", "_eboot/trans_8140.json")
CLAVES_TSV = {"_eboot/trans_nombres.tsv": "inventario", "_dng/tabla.tsv": "dng"}
# En trans_le.json el inglés va en el campo "en" y se lee del EBOOT descifrado en
# el offset de la entrada. Su huella guarda también cuántos caracteres tiene.
LE = "_eboot/trans_le.json"
CLAVE = re.compile(r'^(\s*)("(?:[^"\\]|\\.)*")(\s*:)', re.M)
CAMPO_EN = re.compile(r'("en": )("(?:[^"\\]|\\.)*")')


def escribir_json(ruta, data):
    """Mismo formato que tienen los archivos: indent=1, UTF-8 y sin salto final."""
    return json.dumps(data, ensure_ascii=False, indent=1)


def archivos_traduccion(raiz):
    return sorted(glob.glob(os.path.join(raiz, "translated_text", "*.json")))


def quitar(raiz):
    n = 0
    for ruta in archivos_traduccion(raiz):
        data = json.load(open(ruta, encoding="utf-8"))
        for r in data:
            for c in CAMPOS:
                if c in r:
                    r[c] = None
                    n += 1
        open(ruta, "w", encoding="utf-8").write(escribir_json(ruta, data))
    print(f"campos vaciados: {n}")
    quitar_claves(raiz)


def huella(texto):
    return "en#" + hashlib.sha1(texto.encode("utf-8")).hexdigest()[:16]


def es_huella(texto):
    return texto.startswith("en#")


def cambiar_claves_json(texto, f):
    """Aplica f a cada clave de un JSON sin tocar el resto del formato."""
    def uno(m):
        clave = json.loads(m.group(2))
        if clave.startswith("_"):
            return m.group(0)
        return m.group(1) + json.dumps(f(clave), ensure_ascii=False) + m.group(3)
    return CLAVE.sub(uno, texto)


def cambiar_claves_tsv(texto, f):
    """Aplica f a la primera columna de cada línea que no es comentario."""
    lineas = texto.split("\n")
    for i, linea in enumerate(lineas):
        if linea and not linea.startswith("#"):
            en, resto = linea.split("\t", 1)
            lineas[i] = f(en) + "\t" + resto
    return "\n".join(lineas)


def cambiar_le(texto, f):
    """Aplica f(offset, en) al campo "en" de cada entrada de trans_le.json."""
    partes, ultimo = [], None
    for linea in texto.split("\n"):
        m = re.match(r'^"(\d+)": \{$', linea)
        if m:
            ultimo = int(m.group(1))
        partes.append(CAMPO_EN.sub(
            lambda m: m.group(1) + json.dumps(f(ultimo, json.loads(m.group(2))), ensure_ascii=False), linea))
    return "\n".join(partes)


def editar(raiz, rel, cambio):
    ruta = os.path.join(raiz, rel)
    texto = open(ruta, encoding="utf-8").read()
    open(ruta, "w", encoding="utf-8").write(cambio(texto))


def quitar_claves(raiz):
    from eboot_le_apply import codigos
    ocultar = lambda en: en if es_huella(en) else huella(en)
    for rel in CLAVES_JSON:
        editar(raiz, rel, lambda t: cambiar_claves_json(t, ocultar))
    for rel in CLAVES_TSV:
        editar(raiz, rel, lambda t: cambiar_claves_tsv(t, ocultar))
    editar(raiz, LE, lambda t: cambiar_le(
        t, lambda off, en: en if es_huella(en) else f"en#{len(codigos(en))}#{huella(en)[3:]}"))
    print("inglés cambiado por huellas en", ", ".join(CLAVES_JSON + tuple(CLAVES_TSV) + (LE,)))


def texto_le(elf, off, n):
    """Lee n caracteres u16 little-endian del EBOOT descifrado, como en trans_le.json."""
    salida = []
    for k in range(n):
        c = elf[off + 2 * k] | (elf[off + 2 * k + 1] << 8)
        ch = code_to_char(c)
        salida.append(ch if ch is not None and char_to_code(ch) == c else f"<{c:04X}>")
    return "".join(salida)


def restaurar_claves(iso):
    import dng
    inventario = json.load(open(os.path.join(ROOT, "_eboot", "eb_inventory.json"), encoding="utf-8"))
    fuentes = {
        "inventario": {huella(r["texto_original"]): r["texto_original"] for r in inventario},
        "dng": {huella(t[-1]): t[-1] for t in dng.recorrer(iso)},
    }
    faltan = []

    def buscar(fuente):
        def f(en):
            if not es_huella(en):
                return en
            if en not in fuentes[fuente]:
                faltan.append(en)
                return en
            return fuentes[fuente][en]
        return f

    for rel in CLAVES_JSON:
        editar(ROOT, rel, lambda t: cambiar_claves_json(t, buscar("inventario")))
    for rel, fuente in CLAVES_TSV.items():
        editar(ROOT, rel, lambda t: cambiar_claves_tsv(t, buscar(fuente)))

    elf = open(os.path.join(ROOT, "_eboot", "eboot_dec.elf"), "rb").read()

    def le(off, en):
        if not es_huella(en):
            return en
        n, h = en[3:].split("#")
        texto = texto_le(elf, off, int(n))
        if huella(texto)[3:] != h:
            faltan.append(f"{off} {en}")
            return en
        return texto
    editar(ROOT, LE, lambda t: cambiar_le(t, le))

    if faltan:
        sys.exit(f"No se encontró el inglés de {len(faltan)} huellas, por ejemplo {faltan[:3]}")
    print("inglés recuperado en las tablas del EBOOT y de los calabozos")


def md5(ruta):
    h = hashlib.md5()
    with open(ruta, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def leer_iso(iso, off, n):
    with open(iso, "rb") as f:
        f.seek(off)
        return f.read(n)


def offsets():
    """Ruta en minúsculas dentro de pack/ -> (offset, tamaño) en la ISO."""
    o = json.load(open(os.path.join(ROOT, "scripts", "iso_offsets.json")))
    return {k[len(PACK):].lower(): v for k, v in o.items() if k.startswith(PACK)}


def llenar(iso):
    """Devuelve {ruta: contenido nuevo} con los campos recuperados de la ISO."""
    tabla, cache, salida = offsets(), {}, {}
    signos = json.load(open(EXCEPCIONES, encoding="utf-8"))
    for ruta in archivos_traduccion(ROOT):
        data = json.load(open(ruta, encoding="utf-8"))
        for r in data:
            if not any(c in r for c in CAMPOS):
                continue
            nombre = r["archivo_origen"].lower()
            if nombre not in cache:
                cache[nombre] = leer_iso(iso, *tabla[nombre])
            bin_ = cache[nombre]
            ini, fin = r["offset_inicio"], r["offset_fin"]
            if "raw_hex" in r:
                r["raw_hex"] = bin_[ini:fin].hex()
            if "texto_original" in r:
                texto = list(decode(bin_, ini, fin)[0])
                for pos, signo in signos.get(r["id"], []):
                    texto[pos] = signo
                r["texto_original"] = "".join(texto)
        salida[ruta] = escribir_json(ruta, data)
    return salida


def comprobar_iso(iso):
    if md5(iso) != MD5_EU:
        sys.exit(f"La ISO no es la europea PSN esperada (MD5 {MD5_EU}).")


def verificar(iso):
    comprobar_iso(iso)
    malos = [r for r, txt in llenar(iso).items() if txt != open(r, encoding="utf-8").read()]
    for r in malos:
        print("  distinto:", os.path.relpath(r, ROOT))
    print(f"archivos distintos: {len(malos)}")
    return not malos


def restaurar(iso):
    comprobar_iso(iso)
    for ruta, txt in llenar(iso).items():
        open(ruta, "w", encoding="utf-8").write(txt)
    print("texto original recuperado en translated_text/")

    tabla = offsets()
    os.makedirs(os.path.join(ROOT, "_tex"), exist_ok=True)
    os.makedirs(os.path.join(ROOT, "_eboot"), exist_ok=True)
    for nombre in ("cm_menu.bin", "sys.bin"):
        open(os.path.join(ROOT, "_tex", nombre), "wb").write(leer_iso(iso, *tabla[nombre]))
    eboot = os.path.join(ROOT, "_eboot", "EBOOT.BIN")
    open(eboot, "wb").write(leer_iso(iso, EBOOT_OFF, EBOOT_LEN))
    print("extraídos _tex/cm_menu.bin, _tex/sys.bin y _eboot/EBOOT.BIN")

    elf = os.path.join(ROOT, "_eboot", "eboot_dec.elf")
    pspdecrypt = os.path.join(ROOT, "..", "externos", "pspdecrypt", "pspdecrypt")
    if not os.path.exists(elf) and os.path.exists(pspdecrypt):
        subprocess.run([pspdecrypt, "-o", elf, eboot], check=True)
    if not os.path.exists(elf):
        sys.exit("Falta _eboot/eboot_dec.elf: descifra el EBOOT con pspdecrypt "
                 "(ver notas/compilar.md) y vuelve a correr restaurar.")
    inv = os.path.join(ROOT, "_eboot", "eb_inventory.json")
    subprocess.run([sys.executable, os.path.join(ROOT, "tools", "eboot_strings2.py"), elf, inv], check=True)
    print("regenerado _eboot/eb_inventory.json")
    restaurar_claves(iso)


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ("quitar", "restaurar", "verificar"):
        sys.exit(__doc__)
    if sys.argv[1] == "quitar":
        quitar(sys.argv[2] if len(sys.argv) > 2 else ROOT)
    elif sys.argv[1] == "verificar":
        sys.exit(0 if verificar(sys.argv[2]) else 1)
    else:
        restaurar(sys.argv[2])
