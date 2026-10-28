#!/usr/bin/env python3
"""
Fase 4 - Reinserter.

Lee translated_text/*.json. Para cada registro 'traducido':
  1. Valida que los tokens protegidos <....> esten intactos (mismo conjunto/conteo).
  2. Re-codifica texto_traducido a bytes con el codec.
  3. Valida longitud: bytes_traducidos <= longitud_original.
     - si < : rellena con espacios (0x0000) hasta igualar -> NO cambia punteros.
     - si > : no cabe solo; puede entrar si su cadena (ver _chain) tiene espacio de
       sobra en records vecinos. Si ninguna cadena lo acomoda: 'excede_limite', NO se
       inserta (queda en ingles) y hay que acortarlo.
  4. Acumula parches {archivo, offset_inicio, bytes_nuevos}.

Salida:
  rebuilt/patch_records.json  (lista de parches byte-exactos, relativos a cada .bin)
  docs/reinsert_report.md
Si un record no cabe ni asi, se mueven mensajes suyos al espacio libre al final de
su bloque (tools/reubicar_mensajes.py, que guarda el plan en rebuilt/relocations.json
y lo aplica al final de la compilacion).

Records vecinos separados solo por texto (casi siempre "...") se juntan en un solo
parche para que el relleno no deje huecos visibles (ver _chain). Para leer esos
huecos necesita el ISO EU original en la variable de entorno PERSONA_ISO_EU. Si el
hueco trae una frase en ingles, su traduccion sale de huecos.json (la escribe
tools/escena.py).

NO modifica archivos originales. Solo genera el plan de parche.
"""
import os, sys, json, glob, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from persona_codec import encode, decode
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
from reubicar_mensajes import Planner

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TR = os.path.join(ROOT, "translated_text")
REBUILT = os.path.join(ROOT, "rebuilt")
DOCS = os.path.join(ROOT, "docs")
TOKEN = re.compile(r"<[0-9A-Fa-f]{2,4}>")
HUECOS = os.path.join(ROOT, "huecos.json")


def _tok_bytes(tk):
    h = tk[1:-1]
    c = (0xFF00 | int(h, 16)) if len(h) == 2 else int(h, 16)
    return bytes([(c >> 8) & 0xFF, c & 0xFF])


def _is_anchor(tk):
    """Token de ANCLA = dato estructural a offset FIJO que el engine lee/indexa:
    tokens no-FF (<NNNN> punteros/layout) y <FFFF> (soup de fondo). Los tokens FF de
    control de texto (<FF01/02/03/05/1B/55/67/68...>) NO son ancla: el texto fluye entre
    anclas y el engine los lee secuencialmente."""
    return (not tk.startswith("<FF")) or tk == "<FFFF>"


# Glifos de la fuente sin equivalente ASCII en el codec, vistos dentro de dialogo:
# <0006> ':', <0028> comillas, <0029> '(', <002A> ')', <0044> '>', <0056> '*',
# <0039>/<003A> corchetes, <003E>/<005B>/<0060>/<0062> iconos de boton, etc.
# Los mismos valores tambien aparecen como punteros en tablas, por eso no basta
# el valor: ver _float_flags.
GLYPHS = {"<0005>", "<0006>", "<0007>", "<001E>", "<0028>", "<0029>", "<002A>",
          "<0039>", "<003A>", "<003B>", "<003E>", "<0043>", "<0044>", "<0053>",
          "<0054>", "<0055>", "<0056>", "<005B>", "<0060>", "<0062>"}


# Tokens que cierran una linea o frase: fin de linea <FF03>, de cuadro <FF02>, y en
# las listas de negociacion de talk/*.bin <FFF5>, <FFEF> y <FFFD> (este ultimo abre
# la frase siguiente). Espacios justo antes de ellos quedan al final de una frase.
LINE_ENDS = ("<FF02>", "<FF03>", "<FFF5>", "<FFEF>", "<FFFD>")

# Codigos de texto cuyo argumento <NNNN> va pegado detras: pausa (<FF05><1E00>) y
# color (<FF18><0200>, <FF19><0200>). El argumento no es un puntero ni dato de tabla.
ARG_OPS = {"<FF05>", "<FF18>", "<FF19>"}


def _is_ctrl(tok):
    return tok[:3].upper() == "<FF"


def _neighbors(text):
    """Para cada token de text, el caracter de texto mas cercano antes y despues,
    saltando espacios, backticks y codigos <FFxx> con su argumento <NNNN> pegado.
    Otro token que no sea codigo ni argumento corta la busqueda ('>' antes, '<'
    despues). En "<FF03><0028><FF18><0700>Persona" las comillas tienen "P" despues.

    Dos pasadas lineales (buscar token por token hacia atras era cuadratico en los
    bloques de script con miles de codigos seguidos). Devuelve [(antes, despues)].
    """
    items = []                      # (es_token, valor)
    last = 0
    for m in TOKEN.finditer(text):
        items += [(False, c) for c in text[last:m.start()]]
        items.append((True, m.group(0)))
        last = m.end()
    items += [(False, c) for c in text[last:]]

    def is_arg(k):                  # token no-codigo pegado detras de un codigo
        return k > 0 and items[k - 1][0] and _is_ctrl(items[k - 1][1])

    def step(cur, k, barrier):
        tok, v = items[k]
        if not tok:
            return cur if v in " `" else v
        if _is_ctrl(v) or is_arg(k):
            return cur
        return barrier

    n = len(items)
    before, cur = [""] * n, ""
    for k in range(n):
        before[k] = cur
        cur = step(cur, k, ">")
    after, cur = [""] * n, ""
    for k in range(n - 1, -1, -1):
        after[k] = cur
        cur = step(cur, k, "<")
    return [(before[k], after[k]) for k in range(n) if items[k][0]]


def _float_flags(text):
    """Una bandera por token de text: True si el token fluye con el texto en vez de
    ser ancla. Fluyen:
    - los glifos de GLYPHS usados como texto: el caracter mas cercano a un lado,
      saltando espacios, backticks y codigos de control (_neighbors), es una letra
      o puntuacion. En las tablas de
      punteros cada valor esta rodeado de otros tokens (" <0028> <6028> "), asi
      que ahi sigue siendo ancla.
    - el argumento pegado a un codigo de ARG_OPS. Si quedara fijo, partiria el
      dialogo en tramos cortos sin fin de linea donde esconder el relleno.
    """
    flags = []
    prev_end, prev_tok = -1, None
    for m, (before, after) in zip(TOKEN.finditer(text), _neighbors(text)):
        tok = m.group(0).upper()
        glyph = tok in GLYPHS and any(c and c not in "<>" for c in (before, after))
        arg = prev_end == m.start() and prev_tok in ARG_OPS and not tok.startswith("<FF")
        flags.append(glyph or arg)
        prev_end, prev_tok = m.end(), tok
    return flags


def _runs(orig, trad):
    """Parte orig y trad en tramos entre anclas (ver _align_per_run).
    Devuelve (tramos EN, tramos ES, anclas) o None si las anclas no coinciden."""
    otoks, ttoks = TOKEN.findall(orig), TOKEN.findall(trad)
    if otoks == ttoks:
        floats = _float_flags(orig)
    else:
        floats = [False] * max(len(otoks), len(ttoks))

    def parts(text):
        segs = []; toks = []; last = 0
        for k, m in enumerate(TOKEN.finditer(text)):
            if _is_anchor(m.group(0)) and not floats[k]:
                segs.append(text[last:m.start()]); toks.append(m.group(0)); last = m.end()
        segs.append(text[last:])
        return segs, toks
    os_, ot = parts(orig); ts, tt = parts(trad)
    if ot != tt or len(os_) != len(ts):
        return None
    return os_, ts, tt


def _exceso(orig, trad):
    """Bytes que le sobran a la traduccion, sumando los tramos que no caben en su
    tramo EN; None si las anclas no coinciden. Guia a tools/reubicar_mensajes.py."""
    tramos = _runs(orig, trad)
    if tramos is None:
        return None
    return sum(max(0, len(encode(b)) - len(encode(a))) for a, b in zip(tramos[0], tramos[1]))


def _align_per_run(orig, trad, limite):
    """Padea el texto ENTRE anclas a la longitud-bytes de su tramo EN -> cada token de
    ancla (estructura/puntero/soup) queda en su offset ABSOLUTO original. El texto de
    dialogo fluye libre entre anclas (los <FFxx> de control no fuerzan alineacion).
    None si la secuencia de anclas difiere o un tramo ES excede a su tramo EN."""
    # Los glifos y argumentos de pausa/color (ver _float_flags) fluyen con el texto en
    # vez de anclarse; si no, el relleno de un tramo corto deja huecos visibles
    # ("Apodo   :", "nino     ...<FF05><1E00>"). Las banderas
    # salen del texto EN y se aplican a ES por posicion, asi que solo valen si
    # ambos tienen la misma secuencia de tokens.
    tramos = _runs(orig, trad)
    if tramos is None:
        return None
    os_, ts, tt = tramos
    # Donde va el relleno de cada tramo:
    # - Nunca entre los <FFxx> con que termina el tramo y el ancla siguiente: un ancla
    #   pegada a un <FFxx> es su ARGUMENTO (<FF18><0200> = color cian, <FF01><count>
    #   = tabla de punteros). Separarlos rompe el color o la tabla.
    # - Dentro del resto, justo despues del ultimo <FF01> (fin de mensaje), donde los
    #   espacios no se ven. Si no hay, antes del ultimo token de LINE_ENDS: quedan
    #   como espacios al final de una linea o frase, que tampoco se ven. Si no hay ninguno, al final del cuerpo, donde pueden verse
    #   como hueco antes del texto que sigue al tramo.
    tail_ff = re.compile(r"(?:<FF[0-9A-Fa-f]{2}>)+$")
    nb = bytearray()
    for i, seg in enumerate(ts):
        en = encode(os_[i]); es = encode(seg)
        if len(es) > len(en):
            # whitespace-reclaim (build-time, SIN perder texto): colapsa espacios
            # redundantes del tramo que desborda. Si asi cabe, ancla; si no, RUNOVER.
            seg2 = re.sub(r"  +", " ", seg)
            es2 = encode(seg2)
            if len(es2) <= len(en):
                seg, es = seg2, es2
            else:
                return None
        pad = b"\x00\x00" * ((len(en) - len(es)) // 2)
        m = tail_ff.search(seg)
        body, tail = (seg[:m.start()], m.group(0)) if m else (seg, "")
        cut = body.rfind("<FF01>")
        if cut != -1:
            cut += len("<FF01>")
        else:
            cut = max(body.rfind(t) for t in LINE_ENDS)
            if cut == -1:
                cut = len(body)
        nb += encode(body[:cut]) + pad + encode(body[cut:] + tail)
        if i < len(tt):
            nb += _tok_bytes(tt[i])
    return bytes(nb) if len(nb) == limite else None


def _align_trailing_soup(trad_bytes, limite, raw):
    """Ancla el bloque estructural final (soup verbatim) a su offset original
    insertando el relleno 0x0000 JUSTO ANTES de el. None si ES no termina en esa soup."""
    bnd = -1
    for i in range(0, len(raw) - 1, 2):
        if raw[i] == 0xFF and raw[i + 1] == 0xFF:
            bnd = i; break
    if bnd < 0 or not trad_bytes.endswith(raw[bnd:]):
        return None
    head = trad_bytes[: len(trad_bytes) - len(raw[bnd:])]
    out = head + b"\x00\x00" * ((limite - len(trad_bytes)) // 2) + raw[bnd:]
    return out if len(out) == limite else None


def _pad_fin_de_linea(trad, limite):
    """Relleno de un record de negociacion (talk/*.bin) que no se pudo alinear por
    tramos: va justo antes del ultimo token de LINE_ENDS, donde queda como espacios
    al final de una frase. Al final del record se veria como hueco antes del texto
    que sigue fuera del record ("feas          ... Perdon"). Los punteros de la
    tabla de frases los recalcula tools/fix_text_pointers.py por orden de token.
    None si el record no tiene ningun fin de linea."""
    cut = max(trad.rfind(t) for t in LINE_ENDS)
    if cut <= 0:
        return None
    head, tail = encode(trad[:cut]), encode(trad[cut:])
    out = head + b"\x00\x00" * ((limite - len(head) - len(tail)) // 2) + tail
    return out if len(out) == limite else None


def _match_edge_spaces(orig, trad):
    """Quita a trad los espacios de inicio/final que orig no tiene (nunca agrega).

    ~760 traducciones traen espacios de mas al final ("Jovenes   "), puestos a
    mano para igualar largo. Con records encadenados (ver _chain) esos espacios
    quedan a la vista antes del "..." que sigue; el relleno ya lo pone _align_per_run.
    """
    def edges(t):
        return len(t) - len(t.lstrip(" ")), len(t) - len(t.rstrip(" "))
    (ol, ot), (tl, tt) = edges(orig), edges(trad)
    core = trad.strip(" ")
    if not core:
        return trad
    return " " * min(ol, tl) + core + " " * min(ot, tt)


# bytes: los huecos entre records suelen ser "..." (6) o "...?" (8), pero algunos
# traen frases que el extractor no saco (siguen en ingles y fluyen tal cual)
GAP_MAX = 256
AFTER_BYTES = 16       # bytes tras una cadena que se miran al contar huecos
CHAIN_LOOKAHEAD = 8   # records extra que se prueban tras el ultimo intento fallido


# Hueco visible: 3+ espacios entre dos caracteres de texto (o antes de un glifo).
# Es la misma medida que herramientas/contar_huecos.py.
HUECO = re.compile(r"[A-Za-z0-9!?.,'](?: {3,})(?=[A-Za-z0-9!?.,']|<00[0-9A-F]{2}>)")


def _huecos(data):
    return len(HUECO.findall(decode(data, 0, len(data))[0]))


def _textos_huecos(gaps, gtrad):
    """Texto EN y ES de cada hueco (mas un "" final). gtrad: ES de cada hueco o None."""
    gtxt = [decode(g, 0, len(g))[0] for g in gaps]
    gtrad = gtrad or [None] * len(gaps)
    ges = [en if es is None else es for en, es in zip(gtxt, gtrad)]
    return gtxt + [""], ges + [""]


def _con_huecos(intento, *args, gtrad=None):
    """Prueba intento con los huecos traducidos y, si no alinea, con los huecos en
    ingles: una traduccion mas larga no debe romper una cadena que ya funcionaba."""
    res = intento(*args, gtrad)
    if res is None and gtrad and any(t is not None for t in gtrad):
        res = intento(*args, None)
    return res


def _try_chain(recs, gaps, after, gtrad=None):
    """Alinea records consecutivos (con los huecos de texto entre ellos) como uno solo.

    recs: [(record, bytes_es)], gaps: bytes entre cada par, after: unos bytes del archivo
    justo despues del ultimo record (un hueco puede quedar entre el final de la cadena y
    lo que sigue, p. ej. "diversion,    <0028>"), gtrad: traduccion de cada hueco o
    None (se queda el ingles). Devuelve (bytes_es, bytes_en)
    del tramo completo, o None si no se puede alinear o si deja algun hueco visible.
    """
    gtxt, ges = _textos_huecos(gaps, gtrad)
    orig = "".join(r["texto_original"] + g for (r, _), g in zip(recs, gtxt))
    trad = "".join(r["texto_traducido"] + g for (r, _), g in zip(recs, ges))
    raw = b"".join(bytes.fromhex(r["raw_hex"]) + g for (r, _), g in zip(recs, gaps + [b""]))
    if encode(orig) != raw:
        return None
    fixed = _align_per_run(orig, trad, len(raw))
    if fixed is None:
        return None
    # Solo cadenas sin huecos: aceptar una "con menos huecos" corta la busqueda de
    # subcadenas que si quedan limpias (ver el bucle de _chain).
    if _huecos(fixed + after):
        return None
    return fixed, raw


def _try_relocate(planner, arch, recs, gaps, gtrad=None):
    """Como _try_chain, pero moviendo mensajes al espacio libre del bloque si hace
    falta (tools/reubicar_mensajes.py). Devuelve (bytes_es, bytes_en) o None."""
    gtxt, ges = _textos_huecos(gaps, gtrad)
    orig = "".join(r["texto_original"] + g for (r, _), g in zip(recs, gtxt))
    trad = "".join(r["texto_traducido"] + g for (r, _), g in zip(recs, ges))
    raw = b"".join(bytes.fromhex(r["raw_hex"]) + g for (r, _), g in zip(recs, gaps + [b""]))
    ident = "+".join(r["id"] for r, _ in recs)
    res = planner.plan(arch, recs[0][0]["offset_inicio"], orig, trad, raw, ident,
                       _align_per_run, _exceso)
    return (res[0], raw) if res else None


def _gap_ok(gap, prev_text=""):
    """True si los bytes entre dos records son solo texto que puede fluir:
    caracteres, espacios, <FFxx> de control (no <FFFF>), glifos y el argumento
    pegado a un codigo de ARG_OPS (<FF05><3C00>). prev_text es el texto del record
    anterior: si termina en un codigo de ARG_OPS, su argumento abre el hueco."""
    if not gap or len(gap) > GAP_MAX or len(gap) % 2:
        return False
    text, _ = decode(gap, 0, len(gap))
    if encode(text) != gap:
        return False
    prev_end, prev_tok = 0, (TOKEN.findall(prev_text[-6:]) or [None])[-1]
    for m in TOKEN.finditer(text):
        tk = m.group(0).upper()
        arg = prev_end == m.start() and prev_tok in ARG_OPS and not tk.startswith("<FF")
        if not (arg or (tk.startswith("<FF") and tk != "<FFFF>") or tk in GLYPHS):
            return False
        prev_end, prev_tok = m.end(), tk
    return True


def _cargar_huecos():
    """{archivo: {offset: texto ES}} de huecos.json ({} si no existe)."""
    if not os.path.exists(HUECOS):
        return {}
    data = json.load(open(HUECOS, encoding="utf-8"))
    return {arch: {int(o): t for o, t in v.items()}
            for arch, v in data.items() if not arch.startswith("_")}


def _hueco_es(huecos, arch, off, gap):
    """Traduccion del hueco que empieza en off, o None. Si sus tokens no son los del
    hueco EN (otro ISO o un offset viejo), se avisa y se deja el ingles."""
    es = huecos.get(arch, {}).get(off)
    if es is None:
        return None
    en = decode(gap, 0, len(gap))[0]
    if TOKEN.findall(es) != TOKEN.findall(en):
        print(f"  ! huecos.json {arch}@{off}: tokens distintos al ingles, se ignora")
        return None
    try:
        encode(es)
    except ValueError as e:
        print(f"  ! huecos.json {arch}@{off}: {e}, se ignora")
        return None
    return es


def _chain(done, iso_path, planner):
    """Junta records vecinos separados por huecos de puro texto (casi siempre "...").

    El extractor corto el dialogo en cada "...", que quedo FUERA de los records y
    fijo en su offset. Cada record padeado por separado deja el relleno justo antes
    del "...": "Bien          ... Trato!". Encadenados, el "..." fluye con el texto
    y el relleno puede ir tras un <FF01> (invisible). Si la cadena no se puede
    alinear, cada record se queda con su parche individual.

    done: [(record, bytes_es)] ya validados; bytes_es es None si el record no cabe solo
    y solo puede entrar dentro de una cadena. Si una cadena con records asi no cabe
    entera, se prueba moviendo mensajes al espacio libre del bloque (planner). Devuelve (parches, ids de esos records
    que si entraron).
    """
    offs = json.load(open(os.path.join(ROOT, "scripts", "iso_offsets.json")))
    bmap = {}
    for k, (o, s) in offs.items():
        bmap.setdefault(os.path.basename(k).lower(), (o, s))
    by_file = {}
    for r, b in done:
        by_file.setdefault(r["archivo_origen"], []).append((r, b))
    huecos = _cargar_huecos()
    patches, n_chains, n_recs, colocados = [], 0, 0, set()
    with open(iso_path, "rb") as iso:
        for arch, items in by_file.items():
            items.sort(key=lambda x: x[0]["offset_inicio"])
            base = bmap[os.path.basename(arch).lower()][0]

            def after(r, n=AFTER_BYTES):
                iso.seek(base + r["offset_fin"])
                return iso.read(n)
            # cortar en cadenas de records contiguos por huecos validos
            chains, cur, gaps, gtr = [], [items[0]], [], []
            for prev, nxt in zip(items, items[1:]):
                a, z = prev[0]["offset_fin"], nxt[0]["offset_inicio"]
                iso.seek(base + a)
                gap = iso.read(z - a) if 0 < z - a <= GAP_MAX else b""
                if _gap_ok(gap, prev[0]["texto_original"]):
                    cur.append(nxt); gaps.append(gap)
                    gtr.append(_hueco_es(huecos, arch, a, gap))
                else:
                    chains.append((cur, gaps, gtr)); cur, gaps, gtr = [nxt], [], []
            chains.append((cur, gaps, gtr))
            for recs, gaps, gtr in chains:
                i = 0
                while i < len(recs):
                    # la cadena entera primero; si falla, crecer desde i mientras
                    # alinee (un tramo sin buen lugar para el relleno la tumba entera)
                    best = (_con_huecos(_try_chain, recs[i:], gaps[i:], after(recs[-1][0]), gtrad=gtr[i:])
                            if len(recs) - i > 1 else None)
                    k_best = len(recs) if best else None
                    if best is None and any(bb is None for _, bb in recs[i:]):
                        best = _con_huecos(_try_relocate, planner, arch, recs[i:], gaps[i:], gtrad=gtr[i:])
                        k_best = len(recs) if best else None
                    if best is None:
                        k = i + 2
                        while k <= len(recs) and (k_best is None or k - k_best <= CHAIN_LOOKAHEAD):
                            res = _con_huecos(_try_chain, recs[i:k], gaps[i:k - 1],
                                              after(recs[k - 1][0]), gtrad=gtr[i:k - 1])
                            if res:
                                best, k_best = res, k
                            elif k_best is None and k - i > CHAIN_LOOKAHEAD:
                                break
                            k += 1
                    if best is None:
                        r, bb = recs[i]
                        if bb is not None:   # sin parche propio (no cabe solo): queda en ingles
                            patches.append({"archivo": arch, "offset_inicio": r["offset_inicio"],
                                            "longitud": len(bb), "bytes_hex": bb.hex(), "id": r["id"]})
                        i += 1
                        continue
                    fixed, raw = best
                    n_chains += 1; n_recs += k_best - i
                    colocados.update(r["id"] for r, bb in recs[i:k_best] if bb is None)
                    patches.append({"archivo": arch, "offset_inicio": recs[i][0]["offset_inicio"],
                                    "longitud": len(fixed), "bytes_hex": fixed.hex(),
                                    "id": "+".join(r["id"] for r, _ in recs[i:k_best]),
                                    "orig_hex": raw.hex()})
                    i = k_best
    print(f"Cadenas por huecos de texto: {n_chains} ({n_recs} records)")
    return patches, colocados


def main():
    os.makedirs(REBUILT, exist_ok=True)
    iso_path = os.environ.get("PERSONA_ISO_EU")
    if not iso_path:
        sys.exit("Falta PERSONA_ISO_EU (ruta del ISO EU original): se leen los huecos entre records")
    done = []
    largos = {}   # id -> (bytes ES, limite) de records que no caben solos
    ok = skipped = exceed = badtok = 0
    report = ["# Reporte de reinserción (Fase 4)\n"]
    detail = []
    for jf in sorted(glob.glob(os.path.join(TR, "*.json"))):
        recs = json.load(open(jf, encoding="utf-8"))
        if not isinstance(recs, list):
            continue
        for r in recs:
            if r["estado"] not in ("traducido", "shortened", "retraducido") or not r["texto_traducido"].strip():
                continue
            orig = r["texto_original"]
            trad = r["texto_traducido"] = _match_edge_spaces(orig, r["texto_traducido"])
            limite_b = r["longitud_original_bytes"]
            # 1. tokens intactos
            if sorted(TOKEN.findall(orig)) != sorted(TOKEN.findall(trad)):
                badtok += 1
                detail.append(f"- ❌ {r['id']}: tokens de control alterados -> RECHAZADO")
                continue
            # 2. re-codificar
            try:
                b = encode(trad)
            except ValueError as e:
                skipped += 1
                detail.append(f"- ❌ {r['id']}: char no codificable ({e}) -> RECHAZADO")
                continue
            # 3. longitud
            if len(b) > limite_b:
                # sin parche propio: solo entra si una cadena lo acomoda (ver _chain)
                largos[r["id"]] = (len(b), limite_b)
                done.append((r, None))
                continue
            if len(b) <= limite_b:
                # tambien con longitud EXACTA: el total puede cuadrar con las
                # anclas <NNNN>/<FFFF> corridas (98 records lo tenian). per-run
                # las devuelve a su offset; si no se puede, queda como estaba.
                # FIX offset-shift: el relleno al-FINAL desplaza cualquier token de
                # control/estructura que el engine lea a offset FIJO dentro del record
                # -> rompe (a) soup de layout <FFFF> (descuadre de fondo) y (b) bloques
                # de punteros <NNNN>/backtick que indexan sub-dialogos de NPCs multiples
                # (el dialogo abre y cierra de golpe). _align_per_run padea cada run de
                # texto a la longitud de su run EN -> TODO token queda en su offset
                # original. Se aplica a TODOS los records (no solo soup); si un run ES
                # excede su run EN (RUNOVER) cae a trailing-soup y luego a pad-al-final.
                raw = bytes.fromhex(r["raw_hex"])
                fixed = _align_per_run(orig, trad, limite_b)
                if fixed is None:
                    fixed = _align_trailing_soup(b, limite_b, raw)
                if fixed is None and r["archivo_origen"].startswith("talk/"):
                    fixed = _pad_fin_de_linea(trad, limite_b)
                b = fixed if fixed is not None else (
                    b + b"\x00\x00" * ((limite_b - len(b)) // 2))
            assert len(b) == limite_b
            done.append((r, b))
            ok += 1
            detail.append(f"- ✅ {r['id']}: '{trad.strip()}' ({len(b)} bytes) OK")
    # Lo que no cabe ni encadenado se acomoda moviendo mensajes al espacio libre del
    # bloque (tools/reubicar_mensajes.py; el plan lo aplica ese script al final).
    planner = Planner(iso_path)
    patches, colocados = _chain(done, iso_path, planner)
    ok += len(colocados)
    for r, b in done:
        rid = r["id"]
        if b is not None or rid in colocados:
            continue
        nb, lim = largos[rid]
        antes = len(planner.relocs)
        res = planner.plan(r["archivo_origen"], r["offset_inicio"], r["texto_original"],
                           r["texto_traducido"], bytes.fromhex(r["raw_hex"]), rid,
                           _align_per_run, _exceso)
        n = len(planner.relocs) - antes
        if res is None:
            exceed += 1
            detail.append(f"- ⚠ {rid}: excede_limite ({nb} > {lim} bytes), ni en cadena "
                          f"ni reubicando -> acortar")
            continue
        fixed, _ = res
        patches.append({"archivo": r["archivo_origen"], "offset_inicio": r["offset_inicio"],
                        "longitud": len(fixed), "bytes_hex": fixed.hex(), "id": rid})
        ok += 1
        detail.append(f"- ✅ {rid}: no cabia ({nb} > {lim} bytes), {n} mensaje(s) reubicado(s)")
    for rid in colocados:
        nb, lim = largos[rid]
        detail.append(f"- ✅ {rid}: no cabia solo ({nb} > {lim} bytes), entro en su cadena")
    planner.save()
    print(f"Mensajes reubicados al espacio libre: {len(planner.relocs)}")
    json.dump(patches, open(os.path.join(REBUILT, "patch_records.json"), "w"), indent=1)
    report.append(f"- Insertados OK: **{ok}**")
    report.append(f"- Excede límite (no insertados): **{exceed}**")
    report.append(f"- Tokens alterados (rechazados): **{badtok}**")
    report.append(f"- No codificables (rechazados): **{skipped}**")
    report.append(f"\nParches byte-exactos -> `rebuilt/patch_records.json`\n")
    report.append("## Detalle por registro\n")
    report += detail
    open(os.path.join(DOCS, "reinsert_report.md"), "w", encoding="utf-8").write("\n".join(report) + "\n")
    print("\n".join(report[:6]))

if __name__ == "__main__":
    main()
