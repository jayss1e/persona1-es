#!/usr/bin/env python3
"""
eboot_le_lineas.py — Traduce lineas completas del corpus LE del EBOOT en lugar de
palabra por palabra.

Por que existe: en las etiquetas y mensajes de interfaz, 0x0000 termina el texto y
las palabras van separadas por <8140> (el espacio ancho de Shift-JIS). trans_le.json
se tradujo por piezas: cada palabra por separado, rellena con 0x0000. Eso corta la
frase en la primera pieza ("Pon" en vez de "Pon tu nombre.") y, en los mensajes de
varias lineas, pasa palabras de una linea a otra ("abierto. A"), con lo que una
linea puede quedar vacia (ver bug 13 de notas/bugs.md).

Una LINEA empieza tras un 0x0000, o donde apunte una relocacion del ELF si antes hay
datos pegados ("...<FF1A>What will you buy?"), y termina en el siguiente 0x0000.
Cada mensaje de varias lineas es una linea por renglon, y el juego apunta a cada
una por separado. Las relocaciones las lee herramientas/revisar_le.py.

_eboot/trans_le_lineas.json: {offset de la linea: texto ES}. Un "!" tras el offset
("2983944!") salta la comprobacion de inicio de linea, para lineas de tablas de
registros de largo fijo a las que el juego llega sumando, sin puntero propio. Se escribe como se ve
en el juego, con espacios normales: aqui se convierten a <8140>. Los tokens <XXXX>
del ingles (colores, ':' <0006>, pausas) deben ir en el ES en el mismo orden.

Valida antes de escribir nada:
  - que el offset sea inicio de linea en el EBOOT original,
  - si una relocacion apunta a media linea, avisa: podrian ser dos textos. Suelen
    ser falsos positivos del emparejado lui/addiu de revisar_le.py, porque el
    <8140> indica que el juego lee la linea entera,
  - que los tokens sean los mismos,
  - que el ES quepa: largo <= caracteres de la linea EN + ceros que la siguen - 1
    (siempre queda un 0x0000 como terminador).
Luego quita de trans_le.json las piezas que caen dentro de cada linea y pone una
entrada con la linea completa, que aplica tools/eboot_le_apply.py.

Uso: python3 tools/eboot_le_lineas.py aplicar
     python3 tools/eboot_le_lineas.py mostrar DESDE HASTA   (offsets de archivo)
"""
import json, os, re, struct, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from persona_codec import code_to_char, char_to_code

ELF = os.path.join(ROOT, "_eboot", "eboot_dec.elf")
TAB = os.path.join(ROOT, "_eboot", "trans_le.json")
LINEAS = os.path.join(ROOT, "_eboot", "trans_le_lineas.json")
TOKEN = re.compile(r"(<[0-9A-Fa-f]{4}>)")
ESPACIO = 0x8140
sys.path.insert(0, os.path.join(ROOT, "tools"))
from eboot_punteros import destinos


class Elf:
    def __init__(self):
        self.d = open(ELF, "rb").read()
        self.punteros = destinos(self.d)

    def u16(self, off):
        return struct.unpack_from("<H", self.d, off)[0]

    def linea(self, off):
        """(fin, libre): fin = offset del terminador, libre = offset del siguiente
        caracter no nulo (el espacio disponible llega hasta ahi)."""
        fin = off
        while self.u16(fin) != 0:
            fin += 2
        libre = fin
        while self.u16(libre) == 0 and libre - fin < 0x400:
            libre += 2
        return fin, libre

    def texto(self, a, z):
        """Texto legible de [a, z): <8140> como espacio, otros codigos como token."""
        out = []
        for o in range(a, z, 2):
            c = self.u16(o)
            ch = " " if c == ESPACIO else code_to_char(c)
            out.append(ch if ch is not None and c != 0 else "<%04X>" % c)
        return "".join(out)


def a_codec(texto):
    """Texto legible -> texto para eboot_le_apply.py (espacios como <8140>)."""
    return "".join(p if TOKEN.fullmatch(p) else p.replace(" ", "<8140>")
                   for p in TOKEN.split(texto))


def largo(texto):
    return sum(1 if TOKEN.fullmatch(p) else len(p) for p in TOKEN.split(texto) if p)


def mostrar(a, z):
    elf, tab = Elf(), {int(k): v for k, v in json.load(open(TAB, encoding="utf-8")).items()}
    o = a
    while o < z:
        if elf.u16(o) == 0:
            o += 2
            continue
        fin, libre = elf.linea(o)
        es = " | ".join(tab[k]["es"] for k in sorted(tab) if o <= k < fin)
        print(f"{o} max {(libre - o) // 2 - 1}: {elf.texto(o, fin)!r}\n    ES actual: {es}")
        o = fin


def aplicar():
    elf = Elf()
    raw = open(TAB, encoding="utf-8").read()
    tab = json.loads(raw)
    lineas = {k: v for k, v in json.load(open(LINEAS, encoding="utf-8")).items()
              if not k.startswith("_")}
    errores, nuevas = [], {}
    for clave, es in sorted(lineas.items(), key=lambda kv: int(kv[0].rstrip("!"))):
        off, forzar = int(clave.rstrip("!")), clave.endswith("!")
        inicio = elf.u16(off - 2) == 0 or off in elf.punteros or forzar
        if not inicio or elf.u16(off) == 0:
            errores.append(f"{off}: no es inicio de linea")
            continue
        fin, libre = elf.linea(off)
        dentro = [o for o in range(off + 2, fin, 2) if o in elf.punteros]
        if dentro:
            print(f"  aviso: {off}: relocaciones a media linea en {dentro}")
        en = elf.texto(off, fin)
        maximo = (libre - off) // 2 - 1
        if TOKEN.findall(en) != TOKEN.findall(es):
            errores.append(f"{off}: tokens distintos al ingles {TOKEN.findall(en)}")
            continue
        try:
            for p in TOKEN.split(es):
                if not TOKEN.fullmatch(p):
                    [char_to_code(ch) for ch in p.replace(" ", "")]
        except ValueError as e:
            errores.append(f"{off}: {e}")
            continue
        if largo(es) > maximo:
            errores.append(f"{off}: {largo(es)} caracteres > {maximo}: {es!r}")
            continue
        nuevas[off] = (fin, a_codec(en), a_codec(es), maximo)
    if errores:
        print("\n".join("  ERROR: " + e for e in errores))
        sys.exit("ABORTA: no se escribio nada")
    quitadas = 0
    for off, (fin, en, es, maximo) in nuevas.items():
        # tambien las piezas que empiezan antes y se meten en la linea ("> Reset")
        for k in [k for k in tab if int(k) < fin and int(k) + 2 * tab[k]["budget"] > off]:
            if int(k) < off:
                print(f"  quito {k} {tab[k]['en']!r} -> {tab[k]['es']!r}: se mete en {off}")
            del tab[k]
            quitadas += 1
        tab[str(off)] = {"en": en, "es": es, "budget": maximo}
    tab = dict(sorted(tab.items(), key=lambda kv: int(kv[0])))
    open(TAB, "w", encoding="utf-8").write(json.dumps(tab, indent=0, ensure_ascii=False))
    print(f"lineas escritas: {len(nuevas)}, piezas quitadas: {quitadas}")


if __name__ == "__main__":
    if sys.argv[1:2] == ["aplicar"]:
        aplicar()
    elif sys.argv[1:2] == ["mostrar"]:
        mostrar(int(sys.argv[2]), int(sys.argv[3]))
    else:
        sys.exit(__doc__)
