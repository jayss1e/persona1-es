#!/usr/bin/env python3
"""
reubicar_mensajes.py — Mueve al espacio libre de su bloque los mensajes cuya
traduccion completa no cabe en su lugar, y redirige sus punteros.

POR QUE FUNCIONA (comprobado en el juego el 2026-10-26, ver notas/estilo.md)
  Los bloques de evento de E0..E4.BIN miden multiplos de 2048 y terminan en un
  relleno de ceros de menos de un sector (~13% de cada bloque). El juego carga el
  bloque completo, y cada mensaje se alcanza por un puntero del script (FF55) o
  de una tabla famA, que se pueden redirigir a ese relleno.

DOS PARTES
  1. Plan (lo llama scripts/reinsert.py, clase Planner): si un record no cabe ni
     solo ni encadenado, su cadena de records (o el record solo) se parte en
     mensajes: de su <FF1B>/<FF18> inicial hasta su <FF01>, a veces una
     conversacion entera. Se mueven mensajes, primero los que liberan mas espacio
     por byte libre gastado, hasta que el resto quepa. En el lugar original de cada
     mensaje movido quedan SOLO sus tokens, sin letras: nadie lo lee, pero asi no
     se mueve ningun ancla ni cambia la secuencia de tokens de la que dependen los
     punteros, y casi todo su espacio queda para el texto que se queda. Solo se
     mueven mensajes cuyo inicio es destino de un puntero, que no terminan en una
     tabla famA (<FF01><count>) y que caben en el espacio libre que le queda al
     bloque. El plan se guarda en rebuilt/relocations.json.
  2. Aplicacion (main): escribe cada mensaje en su destino y redirige todos los
     punteros FF55 y famA que caian dentro de el, por indice de token <FFxx>.
     Va al FINAL de la compilacion: despues de tools/fix_text_pointers.py, que
     si se volviera a correr apuntaria otra vez a la copia en ingles.

Uso: python3 tools/reubicar_mensajes.py ISO_EN ISO_ES
"""
import json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from persona_codec import encode
from fix_text_pointers import blocks, ff_tokens, find_sites, OFFS, TEXT_OP
from fix_jump_tables import find_tables

PLAN = os.path.join(ROOT, "rebuilt", "relocations.json")
E_BINS = {"e%d.bin" % n: "/PSP_GAME/USRDIR/pack/E%d.BIN" % n for n in range(5)}
MARGEN = 4          # ceros que se dejan antes de cada mensaje reubicado
TOKEN = re.compile(r"<[0-9A-Fa-f]{4}>")
UNIT_END = re.compile(r"<FF01>", re.I)
MSG_START = ("<FF1B>", "<FF18>")


def _text_end(data, a, b):
    """Fin del contenido del bloque [a, b): donde empieza el relleno de ceros."""
    e = b
    while e > a and data[e - 2:e] == b"\x00\x00":
        e -= 2
    return e


def _targets(data):
    """Offsets destino de punteros FF55 y de tablas famA en un E?.BIN (EN)."""
    out = set()
    for a, b in blocks(data):
        out.update(t for i, t in find_sites(data, a, b, ff_tokens(data, a, b))
                   if data[i + 1] == TEXT_OP)
    for _, base, ptrs in find_tables(data):
        out.update(base + v for v in ptrs)
    return out


def _units(text):
    """Parte text en [(inicio, fin)] de cada tramo que termina en <FF01> (incluido).
    Lo que sigue al ultimo <FF01> no es un mensaje completo y no se devuelve."""
    out, last = [], 0
    for m in UNIT_END.finditer(text):
        out.append((last, m.end()))
        last = m.end()
    return out


class Planner:
    """Decide que mensajes de un record mover y a que offset (ver docstring)."""

    def __init__(self, iso_path):
        self.iso_path = iso_path
        self.files = {}         # archivo -> (data, bloques, destinos, libre por bloque)
        self.relocs = []

    def _file(self, arch):
        if arch not in self.files:
            off, size = OFFS[E_BINS[arch]]
            with open(self.iso_path, "rb") as f:
                f.seek(off)
                data = f.read(size)
            bl = blocks(data)
            free = {a: _text_end(data, a, b) + MARGEN for a, b in bl}
            self.files[arch] = (data, bl, _targets(data), free)
        return self.files[arch]

    def plan(self, arch, rec0, orig, trad, raw, ident, align, exceso):
        """Para un tramo que no cabe: (bytes ES del tramo, n mensajes movidos) o None.

        El tramo es un record o una cadena de records con sus huecos (ver
        reinsert._chain): un mensaje suele abarcar varios records, porque el
        extractor corto el texto en cada "...". rec0 es su offset en el archivo,
        orig/trad su texto EN/ES y raw sus bytes EN. align(orig, trad, limite) y
        exceso(orig, trad) son reinsert._align_per_run y reinsert._exceso (se pasan
        para no importar reinsert desde aqui). Un mensaje solo se mueve si baja el
        exceso: los que estan en tramos que ya caben gastarian espacio libre sin ayudar."""
        if arch not in E_BINS:
            return None
        if TOKEN.findall(orig) != TOKEN.findall(trad) or encode(orig) != raw:
            return None
        data, bl, targets, free = self._file(arch)
        uo, ut = _units(orig), _units(trad)

        cands = []
        for (o0, o1), (t0, t1) in zip(uo, ut):
            # inicio del mensaje: primer token del tramo, solo con espacios antes
            ko = orig.find("<", o0, o1)
            kt = trad.find("<", t0, t1)
            if orig[o0:ko].strip(" ") or orig[ko:ko + 6].upper() not in MSG_START:
                continue
            en_ini = rec0 + len(encode(orig[:ko]))
            en_fin = rec0 + len(encode(orig[:o1]))
            if en_ini not in targets:
                continue
            nxt = data[en_fin:en_fin + 2]
            if nxt != b"\x00\x00" and nxt[:1] != b"\xff":    # <FF01><count>: tabla famA
                continue
            es = encode(trad[kt:t1])
            stub = "".join(TOKEN.findall(orig[ko:o1]))      # lo que queda en su lugar
            libera = len(encode(trad[t0:t1])) - len(encode(stub))
            if libera > 0:
                block = next(a for a, b in bl if a <= en_ini < b)
                cands.append((libera / (len(es) + MARGEN), (t0, t1), stub,
                              en_ini, en_fin, es, block))
        cands.sort(key=lambda c: (-c[0], len(c[5])))

        def mezclar(elegidos):
            """trad con los mensajes elegidos reemplazados por solo sus tokens."""
            mixed, last = [], 0
            for (cc, _) in sorted(elegidos, key=lambda x: x[0][1][0]):
                (t0, t1), stub = cc[1], cc[2]
                mixed += [trad[last:t0], stub]
                last = t1
            mixed.append(trad[last:])
            return "".join(mixed)

        chosen, alloc = [], {}
        actual = exceso(orig, trad)
        for c in cands:
            _, _, _, en_ini, en_fin, es, block = c
            dest = alloc.get(block, free[block])
            b_end = next(b for a, b in bl if a == block)
            if dest + len(es) > b_end:
                continue
            nuevo = exceso(orig, mezclar(chosen + [(c, dest)]))
            if nuevo is None or nuevo >= actual:       # no ayuda: no gastar espacio
                continue
            actual = nuevo
            alloc[block] = dest + len(es) + MARGEN
            chosen.append((c, dest))
            fixed = align(orig, mezclar(chosen), len(raw))
            if fixed is not None:
                free.update(alloc)
                for cc, dest in chosen:
                    self.relocs.append({"archivo": arch, "id": ident, "bloque": cc[6],
                                        "en_inicio": cc[3], "en_fin": cc[4],
                                        "destino": dest, "es_hex": cc[5].hex()})
                return fixed, len(chosen)
        return None

    def save(self):
        json.dump(self.relocs, open(PLAN, "w"), indent=1)


def main():
    iso_en, iso_es = sys.argv[1:3]
    plan = json.load(open(PLAN))
    by_file = {}
    for x in plan:
        by_file.setdefault(x["archivo"], []).append(x)
    total = {"mensajes": 0, "ff55": 0, "famA": 0, "sin_puntero": 0}
    with open(iso_en, "rb") as fo, open(iso_es, "r+b") as fe:
        for arch, items in sorted(by_file.items()):
            off, size = OFFS[E_BINS[arch]]
            fo.seek(off); EN = fo.read(size)
            fe.seek(off); ES = bytearray(fe.read(size))
            bl = dict(blocks(EN))
            sites = {a: [(i, t) for i, t in find_sites(EN, a, b, ff_tokens(EN, a, b))
                         if EN[i + 1] == TEXT_OP] for a, b in bl.items()}
            tables = find_tables(EN)
            for x in items:
                a, dest, es = x["bloque"], x["destino"], bytes.fromhex(x["es_hex"])
                if dest + len(es) > bl[a] or any(ES[dest - MARGEN:dest + len(es)]):
                    sys.exit(f"ABORTA: {x['id']}: el destino {dest:#x} no esta libre")
                en_t = ff_tokens(EN, x["en_inicio"], x["en_fin"])
                es_t = [k for k in range(0, len(es), 2) if es[k] == 0xFF]
                if [EN[i + 1] for i in en_t] != [es[k + 1] for k in es_t]:
                    sys.exit(f"ABORTA: {x['id']}: tokens distintos entre EN y ES")
                ES[dest:dest + len(es)] = es
                where = {i: dest + k for i, k in zip(en_t, es_t)}
                n = 0
                for slot, t in sites[a]:
                    if t in where:
                        ES[slot + 4:slot + 8] = (where[t] - a).to_bytes(4, "little")
                        n += 1; total["ff55"] += 1
                for tpos, base, ptrs in tables:
                    for j, v in enumerate(ptrs):
                        if base + v in where:
                            nv = where[base + v] - base
                            if not 0 < nv <= 0xFFFF:
                                sys.exit(f"ABORTA: {x['id']}: puntero famA fuera de rango")
                            slot = tpos + 4 + 4 * j + 2
                            ES[slot:slot + 2] = nv.to_bytes(2, "little")
                            n += 1; total["famA"] += 1
                total["mensajes"] += 1
                if n == 0:
                    total["sin_puntero"] += 1
                    print(f"  ! {x['id']}: ningun puntero redirigido")
            fe.seek(off); fe.write(ES)
    print(f"reubicados: {total}")


if __name__ == "__main__":
    main()
