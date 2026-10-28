#!/usr/bin/env python3
"""
eboot_menus_8140.py — Mete en trans_records.json las traducciones de
_eboot/trans_8140.json (textos del EBOOT con <8140> entre palabras).

Por que existe: <8140> es el espacio ancho de Shift-JIS. El extractor viejo lo
tomaba como fin de cadena y partia menus como "Bet<8140>on<8140>Mark" en
pedazos de largo fijo, que se tradujeron sueltos ("Apu on Mark"). Con
tools/eboot_strings2.py corregido, cada menu sale como un registro completo y
aqui se traduce entero.

trans_8140.json se escribe como se ve en el juego. Este script lo convierte a
la convencion del codec:
  - Un espacio al inicio de un tramo (inicio de texto o tras <FF03>) queda como
    0x0000, igual que en el original. Los demas espacios pasan a <8140>.
  - Si el valor es una lista, es el menu grande: una opcion por elemento,
    unidas con <FF03>.

Valida, antes de escribir nada:
  - que cada clave exista en el inventario,
  - que los tokens <FFxx> sean los mismos y en el mismo orden,
  - que cada tramo entre anclas quepa en su tramo EN, con la misma regla que
    usa tools/eboot_apply.py (_seps_for), o el texto completo si no hay anclas.

Tambien quita de trans_records.json las claves huerfanas: pedazos viejos que
ya no coinciden con ningun registro del inventario.

Uso: python3 tools/eboot_menus_8140.py _eboot/eb_inventory.json \
         _eboot/trans_8140.json _eboot/trans_records.json
"""
import sys, os, json, re
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.dirname(__file__))
from persona_codec import encode
from eboot_apply import _seps_for, TOK

TOKEN = re.compile(r"(<[0-9A-Fa-f]{4}>)")


def to_codec(text):
    """Texto como se ve en el juego -> texto en la convencion del codec."""
    out = []
    for part in TOKEN.split(text):
        if TOKEN.fullmatch(part):
            out.append(part)
            continue
        # espacio inicial de tramo: si el trozo previo termina en <FF03> o no hay previo
        lead = ""
        if part.startswith(" ") and (not out or out[-1].upper() == "<FF03>"):
            lead, part = " ", part[1:]
        out.append(lead + part.replace(" ", "<8140>"))
    return "".join(out)


def check_fit(rec_id, en, es):
    """Lista de errores de espacio: tramo ES mas largo que su tramo EN."""
    seps = _seps_for(rec_id, en)
    if not seps:
        pairs = [(en, es)]
    else:
        pat = "(" + "|".join(re.escape(s) for s in sorted(seps)) + ")"
        ens, ess = re.split(pat, en), re.split(pat, es)
        if len(ens) != len(ess):
            return [f"{rec_id}: distinto numero de tramos ({len(ens)} EN, {len(ess)} ES)"]
        pairs = list(zip(ens, ess))
    errs = []
    for a, b in pairs:
        if len(encode(b)) > len(encode(a)):
            errs.append(f"{rec_id}: {b!r} ocupa {len(encode(b)) // 2}, cabe {len(encode(a)) // 2}")
    return errs


def main():
    inv_p, src_p, rec_p = sys.argv[1:4]
    inv = json.load(open(inv_p))
    by_text = {r["texto_original"]: r for r in inv}
    src = json.load(open(src_p))
    recs = json.load(open(rec_p))

    new, errs = {}, []
    for en, val in src.items():
        if en.startswith("_"):
            continue
        es = to_codec("<FF03>".join(val) if isinstance(val, list) else val)
        r = by_text.get(en)
        if r is None:
            errs.append(f"no esta en el inventario: {en[:60]!r}")
            continue
        if TOK.findall(en) != TOK.findall(es):
            errs.append(f"{r['id']}: tokens <FFxx> distintos")
            continue
        errs += check_fit(r["id"], en, es)
        new[en] = es
    if errs:
        print("\n".join(errs))
        sys.exit("ABORTA: corrige trans_8140.json")

    huerfanas = [k for k in recs if k not in by_text]
    for k in huerfanas:
        del recs[k]
    recs.update(new)
    json.dump(recs, open(rec_p, "w"), ensure_ascii=False, indent=1)
    print(f"traducciones <8140>: {len(new)}  huerfanas quitadas: {len(huerfanas)}")


if __name__ == "__main__":
    main()
