#!/usr/bin/env python3
"""
aplicar_hablantes.py — Unifica las etiquetas de hablante de translated_text/
segun glosario_hablantes.json.

Cada mensaje empieza con <FF1B>Nombre<FF03>. La traduccion vieja tenia la misma
etiqueta traducida de varias formas ("Clerk" como Mozo, Cajero, Vendedor y
Clerk), 954 sin traducir y hasta personajes cambiados (Brown como Mark).

Por posicion: la k-esima etiqueta ES toma glosario[k-esima etiqueta EN]; si la
etiqueta EN no esta en el glosario es un nombre propio y se copia igual. Los
records con distinto numero de etiquetas en EN y ES se saltan y se listan, y los
de "_omitir" en el glosario no se tocan (no caben con las etiquetas nuevas).

Conserva el formato de cada JSON (ver tools/migrar_puntuacion.json_style).

Uso: python3 tools/aplicar_hablantes.py      (desde la raiz del repo)
"""
import glob, json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from migrar_puntuacion import json_style

LABEL = re.compile(r"<FF1B>([^<]*)<FF03>")


def main():
    raw_glos = json.load(open(os.path.join(ROOT, "glosario_hablantes.json"), encoding="utf-8"))
    glos = {k: v for k, v in raw_glos.items() if not k.startswith("_")}
    omitir = set(raw_glos.get("_omitir", []))   # records que no caben (ver _omitir_motivo)
    cambiadas = saltados = 0
    for path in sorted(glob.glob(os.path.join(ROOT, "translated_text", "*.json"))):
        raw = open(path, encoding="utf-8").read()
        data = json.loads(raw)
        if not isinstance(data, list):
            continue
        kw, nl = json_style(raw, data)
        tocado = False
        for r in data:
            if r["estado"] not in ("traducido", "shortened", "retraducido") or r["id"] in omitir:
                continue
            en = [m.group(1).strip() for m in LABEL.finditer(r["texto_original"])]
            es = list(LABEL.finditer(r["texto_traducido"]))
            if not en:
                continue
            if len(en) != len(es):
                saltados += 1
                print(f"  ! {r['id']}: {len(en)} etiquetas EN, {len(es)} ES -> saltado")
                continue
            t, out, last = r["texto_traducido"], [], 0
            for name, m in zip(en, es):
                nuevo = glos.get(name, name)
                if m.group(1) != nuevo:
                    cambiadas += 1
                out += [t[last:m.start(1)], nuevo]
                last = m.end(1)
            out.append(t[last:])
            nuevo_t = "".join(out)
            if nuevo_t != t:
                r["texto_traducido"] = nuevo_t
                tocado = True
        if tocado:
            open(path, "w", encoding="utf-8").write(json.dumps(data, **kw) + nl)
    print(f"etiquetas cambiadas: {cambiadas}  records saltados: {saltados}")


if __name__ == "__main__":
    main()
