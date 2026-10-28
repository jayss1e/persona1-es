#!/usr/bin/env python3
"""
lote.py — Retraduccion por lotes de escenas: varias escenas en un solo archivo de
texto plano, una linea por pieza, para traducir rapido sin tocar JSON.

exportar ARCH DESDE HASTA
  Exporta con tools/escena.py los bloques DESDE..HASTA (incluidos) de ARCH y escribe
  retraduccion/lote_ARCH_DESDE-HASTA.txt. Cada pieza es una linea
      clave<TAB>texto en ingles
  donde clave es bloque:id:n ("b001:e0_00052:4", o "b001:h12686:0" para un hueco
  entre records). Antes de las piezas de cada registro va una linea "# Hablante"
  con quien habla, como contexto. Se saltan los bloques que ya tienen todos sus
  registros retraducidos.

  MEMORIA: las piezas cuyo ingles ya se tradujo en un lote anterior (pares
  lote_*.txt / lote_*.es.txt de retraduccion/) no van al archivo a traducir: se
  escriben ya traducidas en lote_ARCH_DESDE-HASTA.auto.es.txt, que aplicar lee
  junto con el archivo del usuario (el del usuario manda si repiten clave).

aplicar ARCHIVO_ES
  ARCHIVO_ES tiene lineas clave<TAB>texto en espanol (las demas se ignoran). Pone cada
  texto en el campo "es" de su pieza en retraduccion/ARCH_bNNN.json y aplica cada
  bloque con tools/escena.py aplicar, que valida y escribe en translated_text/ y en
  huecos.json. Si un bloque tiene errores, ese bloque no se escribe y se sigue con
  los demas; al final se listan.

Uso: PERSONA_ISO_EU=... python3 tools/lote.py exportar e0 1 20
     PERSONA_ISO_EU=... python3 tools/lote.py aplicar retraduccion/lote_e0_001-020.es.txt
"""
import io, json, os, re, sys
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import escena

DIR = escena.DIR
HABLANTE = re.compile(r"<FF1B>([^<]*)<FF03>", re.I)


def ruta_bloque(arch, n):
    return os.path.join(DIR, "%s_b%03d.json" % (arch, n))


def leer_pares(ruta):
    """{clave: texto} de un archivo clave<TAB>texto."""
    out = {}
    if os.path.exists(ruta):
        for linea in open(ruta, encoding="utf-8"):
            if "\t" in linea and not linea.startswith("#"):
                k, t = linea.rstrip("\n").split("\t", 1)
                out[k] = t
    return out


def repetidas_en(ruta):
    """Claves que aparecen mas de una vez en un archivo clave<TAB>texto."""
    vistas, rep = set(), set()
    if os.path.exists(ruta):
        for linea in open(ruta, encoding="utf-8"):
            if "\t" in linea and not linea.startswith("#"):
                k = linea.split("\t", 1)[0]
                (rep if k in vistas else vistas).add(k)
    return rep


def memoria(arch):
    """{ingles: espanol} de todos los lotes ya traducidos.
    Las negociaciones (talk_*) solo usan la memoria de otras negociaciones: las
    piezas cortas de la historia ("to", "for you?") ahi significan otra cosa."""
    mem = {}
    lotes = [m.group(1) for m in (re.fullmatch(r"(lote_.+)\.txt", n) for n in sorted(os.listdir(DIR)))
             if m and not m.group(0).endswith(".es.txt")]
    if arch.startswith("talk_"):
        lotes = [l for l in lotes if l.startswith("lote_talk_")]
    # primero lo autorrellenado y despues lo escrito a mano, que siempre gana
    for sufijo in (".auto.es.txt", ".es.txt"):
        for lote in lotes:
            en = leer_pares(os.path.join(DIR, lote + ".txt"))
            en.update(leer_pares(os.path.join(DIR, lote + ".auto.en.txt")))
            es = leer_pares(os.path.join(DIR, lote + sufijo))
            # Las claves repetidas son correcciones puestas al final del .es.txt
            # para una pieza en su contexto ("That's" -> "¡Por eso"): no valen
            # como traduccion general de ese ingles.
            repetidas = repetidas_en(os.path.join(DIR, lote + sufijo))
            for k, t in en.items():
                if k in es and k not in repetidas:
                    mem[t] = es[k]
    return mem


def exportar(arch, desde, hasta):
    mem = memoria(arch)
    auto, auto_en = [], []
    _, _, data = escena.cargar(arch)
    por_id = {r["id"]: r for r in data}
    lineas, n_piezas = [], 0
    for n in range(desde, hasta + 1):
        with redirect_stdout(io.StringIO()):
            escena.exportar(arch, n)
        esc = json.load(open(ruta_bloque(arch, n), encoding="utf-8"))
        regs = esc["registros"]
        if not regs or all("id" in r and por_id[r["id"]]["estado"] == "retraducido"
                           for r in regs):
            continue
        lineas.append(f"## bloque {n}")
        for reg in regs:
            if "id" in reg:
                quien = HABLANTE.findall(por_id[reg["id"]]["texto_original"])
                if quien:
                    lineas.append("# " + " / ".join(dict.fromkeys(q.strip() for q in quien)))
                rid = reg["id"]
            else:
                rid = "h%d" % reg["hueco"]
            for p in reg["piezas"]:
                clave = f"b{n:03d}:{rid}:{p['n']}"
                if p["en"] in mem:
                    auto.append(f"{clave}\t{mem[p['en']]}")
                    auto_en.append(f"{clave}\t{p['en']}")
                else:
                    lineas.append(f"{clave}\t{p['en']}")
                    n_piezas += 1
    base = os.path.join(DIR, "lote_%s_%03d-%03d" % (arch, desde, hasta))
    open(base + ".txt", "w", encoding="utf-8").write("\n".join(lineas) + "\n")
    open(base + ".auto.es.txt", "w", encoding="utf-8").write("\n".join(auto) + "\n")
    open(base + ".auto.en.txt", "w", encoding="utf-8").write("\n".join(auto_en) + "\n")
    print(f"{base}.txt: {n_piezas} piezas por traducir, {len(auto)} de la memoria")


def aplicar(ruta):
    arch = re.search(r"lote_(\w+?)_\d", os.path.basename(ruta)).group(1)
    pares = leer_pares(re.sub(r"\.es\.txt$", ".auto.es.txt", ruta))
    pares.update(leer_pares(ruta))
    nuevos = {}
    for clave, texto in pares.items():
        b, rid, k = clave.split(":")
        nuevos.setdefault(int(b[1:]), {})[(rid, int(k))] = texto
    fallidos = []
    for n, piezas in sorted(nuevos.items()):
        path = ruta_bloque(arch, n)
        esc = json.load(open(path, encoding="utf-8"))
        puestas = 0
        for reg in esc["registros"]:
            rid = reg.get("id") or "h%d" % reg["hueco"]
            for p in reg["piezas"]:
                if (rid, p["n"]) in piezas:
                    p["es"] = piezas[(rid, p["n"])]
                    puestas += 1
        json.dump(esc, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        salida = io.StringIO()
        try:
            with redirect_stdout(salida):
                escena.aplicar(path)
        except SystemExit:
            fallidos.append(n)
        texto = salida.getvalue().strip()
        if texto:
            print(f"bloque {n} ({puestas} piezas):\n  " + texto.replace("\n", "\n  "))
    if fallidos:
        print("bloques con errores (no se escribieron):", fallidos)


if __name__ == "__main__":
    if sys.argv[1:2] == ["exportar"]:
        exportar(sys.argv[2], int(sys.argv[3]), int(sys.argv[4]))
    elif sys.argv[1:2] == ["aplicar"]:
        aplicar(sys.argv[2])
    else:
        sys.exit(__doc__)
