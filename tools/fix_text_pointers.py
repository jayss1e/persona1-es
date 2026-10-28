#!/usr/bin/env python3
"""
fix_text_pointers.py — Recalcula los punteros del script de eventos (E0..E4.BIN)
para que apunten al texto ES en vez de a la posicion que tenia el texto EN.

SINTOMA EN EL JUEGO
  Nombres de hablante sin color, presentaciones de personaje con "?>" y sin
  color, y frases cortadas al inicio ("studiante boba", "mo si me importara",
  "serio?"). El juego empieza a leer el mensaje a media palabra.

FORMATO (verificado contra el ISO EN)
  - Cada E?.BIN empieza con una tabla de pares u32 LE (inicio, fin), uno por
    bloque de evento. La tabla termina en el primer par vacio o invalido.
  - Dentro de un bloque, el script usa opcodes de 8 bytes:
        FFxx  <arg u16>  <ptr u32 LE>
    ptr es relativo al INICIO DEL BLOQUE segun la tabla de cabecera. El pase
    famB de fix_jump_tables.py asumia la frontera de 2048 anterior, que solo
    coincide con el inicio del bloque por casualidad; por eso dejaba la
    mayoria de los punteros mal y movia otros unos bytes.
  - FF55 apunta a un mensaje de dialogo (empieza en <FF1B> hablante o en
    <FF18> color). Es el unico que apunta al texto.
  - FF22, FF54, FF26, FF30, FF44, FF45 y FF47 apuntan al propio script, que
    no cambia al traducir. Se incluyen solo para deshacer lo que famB les
    escribio mal.

METODO
  Al traducir, los tokens <FFxx> conservan su orden dentro del bloque (solo
  se mueve el texto entre ellos). Asi que si el target EN es el k-esimo
  <FFxx> del bloque, el target ES es el k-esimo <FFxx> del mismo bloque en ES.
  Si un bloque no tiene la misma secuencia de tokens en EN y ES, se salta y
  se avisa.

GUARDAS
  - Un sitio solo cuenta si el opcode esta alineado a 2 bytes, el target cae
    dentro del bloque y en EN el target es un token <FFxx>.
  - El opcode debe seguir en la misma posicion y con los mismos 4 bytes en
    ES. Asi se descartan los <FFxx> que forman parte del dialogo, que si se
    movio al traducir; escribir ahi corromperia el texto.
  - Los opcodes que no son FF55 nunca se mueven: solo se restaura su valor
    EN, y unicamente si su target no cambio de posicion.

NEGOCIACION (talk/*.bin)
  Cada archivo empieza con un u32 LE que dice donde arranca una tabla de u32 LE
  crecientes: el offset absoluto del inicio de cada frase de negociacion (tras su
  separador <FFF5>/<FFFD>.../<F500> y los espacios). En ES las frases se movieron
  y el juego las empezaba a media palabra (7101 de 13284 en el parche publicado).
  Se remapea igual: el ultimo <FFxx> antes del target EN es el k-esimo del archivo;
  en ES se toma su pareja (las secuencias se alinean con difflib, porque algunas
  traducciones cambiaron el orden de los tokens), se avanzan los mismos codigos no-espacio que en EN
  (ej. <00FF><F500>) y se saltan los espacios hasta el primer caracter.

Uso: python3 tools/fix_text_pointers.py ISO_EN ISO_ES
     Modifica ISO_ES en sitio. Correr despues de tools/fix_jump_tables.py y
     antes de tools/build_unified.py. Es idempotente: correrlo dos veces da
     el mismo resultado.
"""
import os, json, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OFFS = json.load(open(os.path.join(ROOT, "scripts", "iso_offsets.json")))
BINS = ["/PSP_GAME/USRDIR/pack/E%d.BIN" % n for n in range(5)]
TALK_BINS = sorted(k for k in OFFS if "/pack/talk/" in k and k.upper().endswith(".BIN"))

TEXT_OP = 0x55                                        # FF55: puntero a mensaje
SCRIPT_OPS = {0x54, 0x26, 0x22, 0x30, 0x44, 0x45, 0x47}  # saltos dentro del script
OPS = SCRIPT_OPS | {TEXT_OP}
HEADER_SIZE = 0x800   # la tabla de bloques ocupa el primer sector


def blocks(data):
    """[(inicio, fin)] de cada bloque de evento segun la tabla de cabecera."""
    out = []
    for k in range(0, HEADER_SIZE, 8):
        a = int.from_bytes(data[k:k + 4], "little")
        b = int.from_bytes(data[k + 4:k + 8], "little")
        if a == 0 or b <= a or b > len(data):
            break
        out.append((a, b))
    return out


def ff_tokens(data, a, b):
    """Posiciones de los tokens <FFxx> (u16 BE) dentro de [a, b)."""
    return [i for i in range(a, b, 2) if data[i] == 0xFF]


def find_sites(EN, a, b, en_t):
    """[(pos_opcode, target)] de los opcodes con puntero en el bloque EN."""
    sites = []
    for i in en_t:
        if EN[i + 1] not in OPS or i + 8 > b:
            continue
        t = a + int.from_bytes(EN[i + 4:i + 8], "little")
        if a <= t < b and EN[t] == 0xFF:
            sites.append((i, t))
    return sites


def talk_table(data):
    """[(slot, target)] de la tabla de frases de un talk/*.bin (ver docstring)."""
    k = int.from_bytes(data[0:4], "little")
    out, prev = [], 0
    while k + 4 <= len(data):
        v = int.from_bytes(data[k:k + 4], "little")
        if not (prev < v < len(data)):
            break
        out.append((k, v)); prev = v; k += 4
    return out


PUNTOS = b"\x00\x04\x00\x04\x00\x04\x00\x00"   # "..." seguido de un espacio


def _tras_puntos(head, ES, q, fin):
    """Posicion ES tras el n-esimo "... " desde q (n = los que hay en head), o None."""
    n = head.count(PUNTOS[:6])
    if n == 0 or not head.endswith(PUNTOS[:6]):
        return None
    for _ in range(n):
        i = ES.find(PUNTOS, q, fin)
        while i >= 0 and (i - q) % 2:          # solo coincidencias alineadas a caracter
            i = ES.find(PUNTOS, i + 1, fin)
        if i < 0:
            return None
        q = i + len(PUNTOS) - 2
    return q


def fix_talk(EN, ES, bin_off):
    """Escrituras para la tabla de frases de un talk/*.bin."""
    import bisect, difflib
    stats = {"frases": 0, "movidas": 0, "sin_cambio": 0, "saltadas": 0}
    table = talk_table(EN)
    if not table:
        return [], stats
    lo = table[-1][0] + 4                     # el texto empieza tras la tabla
    en_t, es_t = ff_tokens(EN, lo, len(EN)), ff_tokens(ES, lo, len(ES))
    # En algunas frases la traduccion cambio el orden de los tokens; se alinean las
    # dos secuencias y solo se usan los tokens emparejados (k EN -> k ES).
    sm = difflib.SequenceMatcher(None, [EN[i + 1] for i in en_t],
                                 [ES[i + 1] for i in es_t], autojunk=False)
    pair = {}
    for a0, b0, n in sm.get_matching_blocks():
        for j in range(n):
            pair[a0 + j] = b0 + j
    writes = []
    for slot, v in table:
        stats["frases"] += 1
        k = bisect.bisect_left(en_t, v) - 1    # ultimo <FFxx> antes del target
        if k < 0 or k not in pair:
            stats["saltadas"] += 1; continue
        head = EN[en_t[k] + 2:v]              # codigos y espacios entre token y frase
        while head.endswith(b"\x00\x00"):
            head = head[:-2]
        q = es_t[pair[k]] + 2
        if ES[q:q + len(head)] == head:
            q += len(head)
        else:
            # Algunas frases EN empiezan a media linea, tras otra frase y sus "...":
            # en ES se busca el mismo numero de "..." seguidos de espacio, sin pasar
            # del siguiente token.
            q = _tras_puntos(head, ES, q, es_t[pair[k] + 1] if pair[k] + 1 < len(es_t) else len(ES))
            if q is None:
                stats["saltadas"] += 1; continue
        while ES[q:q + 2] == b"\x00\x00":
            q += 2
        nv = q.to_bytes(4, "little")
        if ES[slot:slot + 4] == nv:
            stats["sin_cambio"] += 1; continue
        writes.append((bin_off + slot, nv))
        stats["movidas"] += 1
    return writes, stats


def fix_bin(EN, ES, bin_off):
    """Calcula las escrituras para un E?.BIN.

    Devuelve (writes, stats), donde writes es [(offset_en_iso, bytes)].
    """
    writes = []
    stats = {"bloques": 0, "saltados": 0, "texto": 0, "script": 0, "sin_cambio": 0}
    for a, b in blocks(EN):
        en_t = ff_tokens(EN, a, b)
        sites = find_sites(EN, a, b, en_t)
        if not sites:
            continue
        stats["bloques"] += 1
        es_t = ff_tokens(ES, a, b)
        if [EN[i + 1] for i in en_t] != [ES[i + 1] for i in es_t]:
            print(f"  ! bloque @{a:#x}: secuencia de tokens distinta EN/ES -> saltado")
            stats["saltados"] += 1
            continue
        idx = {p: k for k, p in enumerate(en_t)}   # posicion EN -> indice k
        for i, t in sites:
            # El opcode debe estar intacto en ES; si no, es un <FFxx> de dialogo.
            if es_t[idx[i]] != i or ES[i:i + 4] != EN[i:i + 4]:
                continue
            nt = es_t[idx[t]]
            if EN[i + 1] != TEXT_OP and nt != t:
                continue
            nv = (nt - a).to_bytes(4, "little")
            if ES[i + 4:i + 8] == nv:
                stats["sin_cambio"] += 1
                continue
            writes.append((bin_off + i + 4, nv))
            stats["texto" if EN[i + 1] == TEXT_OP else "script"] += 1
    return writes, stats


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    iso_en, iso_es = sys.argv[1], sys.argv[2]
    with open(iso_en, "rb") as fo, open(iso_es, "r+b") as fe:
        for key in BINS:
            off, size = OFFS[key]
            fo.seek(off); EN = fo.read(size)
            fe.seek(off); ES = fe.read(size)
            writes, stats = fix_bin(EN, ES, off)
            for pos, bb in writes:
                fe.seek(pos); fe.write(bb)
            print(f"{os.path.basename(key)}: {stats}")
        for key in TALK_BINS:
            off, size = OFFS[key]
            fo.seek(off); EN = fo.read(size)
            fe.seek(off); ES = fe.read(size)
            writes, stats = fix_talk(EN, ES, off)
            for pos, bb in writes:
                fe.seek(pos); fe.write(bb)
            print(f"talk/{os.path.basename(key)}: {stats}")


if __name__ == "__main__":
    main()
