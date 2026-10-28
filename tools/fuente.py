#!/usr/bin/env python3
"""
fuente.py — Agrega a la fuente del juego las letras del espanol que no tiene
(a e i o u con acento, n con tilde, u con dieresis, sus mayusculas, signos de
apertura) y las escribe en el ISO.

LA FUENTE
  /PSP_GAME/USRDIR/pack/sys.bin es un contenedor; sus partes 1 y 2 son imagenes
  GIM de 256x256, 4 bits por pixel (paleta de 16 niveles de blanco con alfa),
  con el swizzle de PSP (bloques de 16 bytes x 8 filas). Cada una es una
  cuadricula de 16x16 casillas de 16x16 pixeles, y el codigo de una letra es su
  numero de casilla: parte 1 = codigos 0x00-0xFF (mayusculas, digitos, signos y
  muchos simbolos y kana sin uso), parte 2 = codigos 0x100-0x1FF (minusculas en
  0x101-0x11A).

  LIMITES (comprobados en el juego el 2026-10-26):
  - El dibujante de texto del EBOOT (p. ej. la funcion en 0x1db68) solo dibuja
    codigos menores que 0x120; para los demas dibuja el codigo 0x0008 ("?"). Hay
    9 comparaciones "slti ..., 0x120" iguales en las rutinas de texto.
  - El ancho de cada letra no sale de la imagen sino de una tabla del EBOOT
    (ver ANCHOS mas abajo). Las letras nuevas van sobre simbolos ASCII que
    ningun texto usa y las casillas libres tras la "z" (ver EXTRA en
    scripts/persona_codec.py), y a cada una se le pone el ancho de su dibujo.

  OJO: los pixeles empiezan donde dice el campo +0x1C de la cabecera de imagen
  (+0x40), no tras el tamano de cabecera (+0x30) como asume tools/gim2png.py.
  Con +0x30 cada letra sale corrida una fila dentro de su bloque de 8. No se
  corrige gim2png.py porque las ediciones de menus de Jose-Chavez96 se ajustaron
  sobre esa lectura.

LAS TABLAS DE ANCHOS (encontradas el 2026-10-26)
  En el EBOOT descifrado, que el ISO guarda tal cual en EBOOT_OFF, hay tablas
  de 0x120 entradas de 8 bytes, una por codigo: (desplazamiento x, ancho), dos
  int32 LE. Los simbolos que reemplazamos tenian (0, 16), el ancho de una
  casilla completa, y por eso las letras nuevas salian separadas ("s i  pasan").
  - ANCHOS: la que usa el dialogo. Las rutinas de texto (0x1db68 y 0x1eadc) la
    leen con "lui/addiu $fp, ..., 0x198c", relocalizado contra el segmento de
    datos (vaddr 0x263900), y avanzan ancho + 1 tras cada letra. Esta en
    pixeles: coincide con los bordes del dibujo en 59 de 65 letras y signos
    (las demas difieren en 1 pixel). Por eso a cada letra nueva se le ponen los
    bordes de su propio dibujo.
  - ANCHOS_MEDIA: otra tabla igual a media escala (por ejemplo "a" = (3, 5)
    contra (4, 9)), 0x900 bytes antes. No se sabe aun donde se usa. A cada
    letra nueva se le copia la entrada de su letra base.

LAS LETRAS NUEVAS
  Se dibujan en esas casillas, a partir de letras del propio juego para conservar el estilo: vocal + acento, n + tilde, u + dieresis,
  mayuscula + acento o tilde en las filas 0-1 (las mayusculas ocupan 2-12), y
  "?" y "!" girados 180 grados para los signos de apertura.

  scripts/persona_codec.py solo CODIFICA estos caracteres (EXTRA): al leer los
  datos originales esos codigos siguen saliendo como tokens <01xx>, porque
  tambien aparecen en datos del script y no deben confundirse con letras.

Uso: python3 tools/fuente.py ISO_ES          (escribe en sitio; idempotente)
     python3 tools/fuente.py ISO_ES --png F  (ademas guarda una vista de las letras)
"""
import os, sys, json, struct

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from gim2png import split_container, u16, u32, unswizzle
from png2gim import swizzle
from persona_codec import EXTRA, char_to_code

SYS = "/PSP_GAME/USRDIR/pack/sys.bin"
EBOOT_OFF = 1245184          # /PSP_GAME/SYSDIR/EBOOT.BIN (descifrado) en el ISO
ELF_DATOS = 0x263900 + 0xC0  # inicio del segmento de datos dentro del ELF
ANCHOS = ELF_DATOS + 0x198c        # tabla de anchos del dialogo, en pixeles
ANCHOS_MEDIA = ELF_DATOS + 0x108c  # la misma a media escala
W = H = 256
PITCH = W // 2
CELL = 16


def _pixels_offset(g):
    """Offset de los pixeles de la imagen (bloque 0x04) dentro del GIM g."""
    p = 0x10
    while p + 16 <= len(g):
        typ, nxt = u16(g, p), u32(g, p + 8)
        if typ == 0x04:
            dp = p + 16
            assert u16(g, dp + 4) == 4 and u16(g, dp + 8) == W and u16(g, dp + 10) == H
            return dp + u32(g, dp + 0x1C)
        if nxt == 0:
            break
        p += nxt
    raise ValueError("GIM sin imagen")


class Hoja:
    """Una parte de sys.bin como matriz de indices de paleta (0-15)."""

    def __init__(self, g):
        self.g = bytearray(g)
        self.pix = _pixels_offset(g)
        raw = unswizzle(bytes(g[self.pix:self.pix + PITCH * H]), PITCH, H)
        self.px = [[(raw[y * PITCH + x // 2] >> (4 * (x & 1))) & 0xF for x in range(W)]
                   for y in range(H)]

    def bytes(self):
        raw = bytearray(PITCH * H)
        for y in range(H):
            for x in range(0, W, 2):
                raw[y * PITCH + x // 2] = self.px[y][x] | (self.px[y][x + 1] << 4)
        self.g[self.pix:self.pix + PITCH * H] = swizzle(bytes(raw), PITCH, H)
        return bytes(self.g)

    def celda(self, n):
        y0, x0 = (n // 16) * CELL, (n % 16) * CELL
        return [self.px[y0 + y][x0:x0 + CELL] for y in range(CELL)]

    def poner(self, n, m):
        y0, x0 = (n // 16) * CELL, (n % 16) * CELL
        for y in range(CELL):
            self.px[y0 + y][x0:x0 + CELL] = m[y]


# Marcas (filas de indices, "." = 0). Se centran sobre la letra.
AGUDO_MIN = ["..4fc", ".4fc.", "4c4.."]          # filas 2-4 (minusculas)
AGUDO_MAY = ["..5fc", ".cf5."]                   # filas 0-1 (mayusculas)
TILDE_MIN = [".5fc5..8", "8..5cf5."]             # filas 2-3
TILDE_MAY = TILDE_MIN                             # filas 0-1
DIERESIS = ["cf..cf"]                             # fila 3


def _centro(m, filas):
    cols = [x for y in filas for x in range(CELL) if m[y][x]]
    return (min(cols) + max(cols) + 1) // 2


def _marca(m, patron, fila0, centro):
    m = [r[:] for r in m]
    x0 = centro - (len(patron[0]) + 1) // 2
    for dy, fila in enumerate(patron):
        for dx, c in enumerate(fila):
            if c != ".":
                m[fila0 + dy][x0 + dx] = int(c, 16)
    return m


def _girar(m):
    return [list(reversed(r)) for r in reversed(m)]


def construir(h1, h2):
    """{codigo: matriz} de las letras nuevas, a partir de letras del juego."""
    low = lambda ch: h2.celda(ord(ch) - ord("a") + 1)   # "a" = codigo 0x101
    up = lambda ch: h1.celda(ord(ch) + 0x9F)            # "A" = codigo 0xE0
    out = {}
    for ch, base in (("á", "a"), ("é", "e"), ("ó", "o"), ("ú", "u")):
        b = low(base)
        out[EXTRA[ch]] = _marca(b, AGUDO_MIN, 2, _centro(b, range(5, 13)))
    i = [r[:] for r in low("i")]
    for y in range(2, 6):                          # quitar el punto de la i
        i[y] = [0] * CELL
    out[EXTRA["í"]] = _marca(i, AGUDO_MIN, 2, _centro(i, range(6, 13)) + 1)
    n = low("n")
    out[EXTRA["ñ"]] = _marca(n, TILDE_MIN, 2, _centro(n, range(5, 13)))
    u = low("u")
    out[EXTRA["ü"]] = _marca(u, DIERESIS, 3, _centro(u, range(5, 13)))
    for ch, base in (("Á", "A"), ("É", "E"), ("Í", "I"), ("Ó", "O"), ("Ú", "U")):
        b = up(base)
        out[EXTRA[ch]] = _marca(b, AGUDO_MAY, 0, _centro(b, range(2, 13)))
    N = up("N")
    out[EXTRA["Ñ"]] = _marca(N, TILDE_MAY, 0, _centro(N, range(2, 13)))
    U = up("U")
    out[EXTRA["Ü"]] = _marca(U, ["cf..cf"], 0, _centro(U, range(2, 13)))
    out[EXTRA["¿"]] = _girar(h1.celda(0x08))     # "?" (codigo 0x0008)
    out[EXTRA["¡"]] = _girar(h1.celda(0x09))     # "!" (codigo 0x0009)
    return out


def base(ch):
    """Codigo de la letra del juego cuyo ancho hereda la letra nueva ch."""
    b = {"¿": "?", "¡": "!"}.get(ch) or {"á": "a", "é": "e", "í": "i", "ó": "o",
         "ú": "u", "ñ": "n", "ü": "u"}.get(ch.lower(), "")
    b = b.upper() if ch.isupper() else b
    return char_to_code(b)


def bordes(m):
    """(primera columna con tinta, ancho) de una casilla."""
    xs = [x for fila in m for x in range(CELL) if fila[x]]
    return min(xs), max(xs) - min(xs) + 1


def poner_anchos(f, nuevas):
    """Escribe en las tablas del EBOOT el ancho de cada letra nueva."""
    for ch, code in EXTRA.items():
        f.seek(EBOOT_OFF + ANCHOS + 8 * code)
        f.write(struct.pack("<ii", *bordes(nuevas[code])))
        f.seek(EBOOT_OFF + ANCHOS_MEDIA + 8 * base(ch))
        ent = f.read(8)
        f.seek(EBOOT_OFF + ANCHOS_MEDIA + 8 * code)
        f.write(ent)


def main():
    iso = sys.argv[1]
    offs = json.load(open(os.path.join(ROOT, "scripts", "iso_offsets.json")))
    off, size = offs[SYS]
    with open(iso, "r+b") as f:
        f.seek(off)
        data = bytearray(f.read(size))
        partes = split_container(bytes(data))
        h1, h2 = Hoja(partes[1]), Hoja(partes[2])
        assert h2.bytes() == bytes(partes[2]), "la ida y vuelta no reproduce el GIM"
        nuevas = construir(h1, h2)
        assert h1.bytes() == bytes(partes[1]), "la ida y vuelta no reproduce el GIM"
        for code, m in nuevas.items():
            assert code < 0x120, "el juego no dibuja codigos >= 0x120"
            (h1 if code < 0x100 else h2).poner(code & 0xFF, m)
        for parte, hoja in ((partes[1], h1), (partes[2], h2)):
            g = hoja.bytes()
            pos = bytes(data).find(bytes(parte))
            assert pos >= 0 and len(g) == len(parte)
            data[pos:pos + len(g)] = g
        f.seek(off)
        f.write(data)
        poner_anchos(f, nuevas)
    print(f"letras nuevas: {len(nuevas)} ({''.join(sorted(EXTRA))})")
    if "--png" in sys.argv:
        from PIL import Image
        dest = sys.argv[sys.argv.index("--png") + 1]
        im = Image.new("L", (CELL * len(nuevas), CELL))
        for k, (code, m) in enumerate(sorted(nuevas.items())):
            for y in range(CELL):
                for x in range(CELL):
                    im.putpixel((k * CELL + x, y), m[y][x] * 17)
        im.resize((im.width * 4, im.height * 4), Image.NEAREST).save(dest)
        print("vista:", dest)


if __name__ == "__main__":
    main()
