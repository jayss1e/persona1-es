#!/usr/bin/env python3
"""
migrar_puntuacion.py — Migracion de una sola vez: intercambia '!' y '.' en los
textos guardados, para acompanar la correccion de PUNCT en scripts/persona_codec.py.

Antes el codec decodificaba 0x0004 como '!' y 0x0009 como '.', pero el juego
dibuja 0x0004 como '.' y 0x0009 como '!'. Todos los textos se guardaron con esa
convencion invertida (para ver "!" en el juego habia que escribir "."). Al
corregir el codec, cada texto se intercambia '!' <-> '.' fuera de los tokens
<....>, y los bytes que producen al codificarse son exactamente los mismos.

Solo toca los datos que usa la compilacion y sus exportaciones:
  translated_text/*.json y *.csv   texto_original, texto_traducido
  extracted_text/*.json y *.csv    texto_original, texto_traducido
  _eboot/eb_inventory.json         texto_original, texto_traducido
  _eboot/trans_records.json        claves y valores
  _eboot/trans_master.json         claves y valores
  _eboot/trans_le.json             en, es
  _eboot/trans_intro.json          pares [en, es]
  _eboot/trans_8140.json           solo las claves (los valores ya estaban como se
                                   ven en el juego); se hizo a mano en la migracion
No toca los archivos de trabajo historicos (_wfmaps, _textgap, _eboot/work,
docs/checkpoints, trans_records.bak_*): siguen en la convencion vieja.

Conserva el formato exacto de cada archivo: antes de convertir comprueba que
puede reproducirlo byte por byte; si no, aborta sin escribir nada.

Uso: python3 tools/migrar_puntuacion.py          (desde la raiz del repo)
"""
import csv, glob, io, json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN = re.compile(r"(<[0-9A-Fa-f]{2,4}>)")
SWAP = str.maketrans({"!": ".", ".": "!"})
FIELDS = ("texto_original", "texto_traducido")


def swap(text):
    """Intercambia '!' y '.' fuera de los tokens <....>."""
    return "".join(p if TOKEN.fullmatch(p) else p.translate(SWAP) for p in TOKEN.split(text))


def json_style(raw, data):
    """Parametros de json.dump que reproducen raw exactamente, o None."""
    for indent in (None, 0, 1, 2):
        for ascii_ in (False, True):
            for nl in ("", "\n"):
                if json.dumps(data, indent=indent, ensure_ascii=ascii_) + nl == raw:
                    return dict(indent=indent, ensure_ascii=ascii_), nl
    return None


def convert_json(data, kind):
    if kind == "records":
        for r in data:
            for f in FIELDS:
                if isinstance(r.get(f), str):
                    r[f] = swap(r[f])
        return data
    if kind == "dict":
        return {swap(k): swap(v) for k, v in data.items()}
    if kind == "le":
        for v in data.values():
            v["en"], v["es"] = swap(v["en"]), swap(v["es"])
        return data
    if kind == "pairs":
        return [[swap(a), swap(b)] for a, b in data]
    raise ValueError(kind)


def plan_json(path, kind):
    raw = open(path, encoding="utf-8").read()
    data = json.loads(raw)
    style = json_style(raw, data)
    if style is None:
        sys.exit(f"ABORTA: no se reproduce el formato de {path}")
    if kind == "records" and not isinstance(data, list):
        return None
    kw, nl = style
    return json.dumps(convert_json(data, kind), **kw) + nl


def plan_csv(path):
    raw = open(path, encoding="utf-8", newline="").read()
    rows = list(csv.reader(io.StringIO(raw)))

    def dump(rows):
        out = io.StringIO()
        csv.writer(out, lineterminator=dialect_nl).writerows(rows)
        return out.getvalue()
    dialect_nl = "\r\n" if "\r\n" in raw else "\n"
    if dump(rows) != raw:
        sys.exit(f"ABORTA: no se reproduce el formato de {path}")
    head = rows[0]
    cols = [head.index(f) for f in FIELDS if f in head]
    for row in rows[1:]:
        for c in cols:
            if c < len(row):
                row[c] = swap(row[c])
    return dump(rows)


def main():
    jobs = []
    for d in ("translated_text", "extracted_text"):
        jobs += [(p, "records") for p in sorted(glob.glob(os.path.join(ROOT, d, "*.json")))]
        jobs += [(p, "csv") for p in sorted(glob.glob(os.path.join(ROOT, d, "*.csv")))]
    eb = os.path.join(ROOT, "_eboot")
    jobs += [(os.path.join(eb, "eb_inventory.json"), "records"),
             (os.path.join(eb, "trans_records.json"), "dict"),
             (os.path.join(eb, "trans_master.json"), "dict"),
             (os.path.join(eb, "trans_le.json"), "le"),
             (os.path.join(eb, "trans_intro.json"), "pairs")]
    # primero se calcula todo; si algo aborta, no se escribio nada
    out = []
    for path, kind in jobs:
        new = plan_csv(path) if kind == "csv" else plan_json(path, kind)
        if new is not None:
            out.append((path, new))
    for path, new in out:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(new)
    print(f"archivos convertidos: {len(out)}")


if __name__ == "__main__":
    main()
