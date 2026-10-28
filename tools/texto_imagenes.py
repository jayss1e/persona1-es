#!/usr/bin/env python3
"""
texto_imagenes.py — Traduce las etiquetas de texto que vienen dibujadas en
imagenes GIM de la interfaz (tiras de palabras de los menus: "Confirm Back
Select...", la barra de la pantalla de nombre, encabezados de tablas...).

LAS TIRAS
  Son GIM de 4 bits (16 colores): la paleta es un solo color (p. ej. gris 204) con
  16 niveles de transparencia, y el texto es una fuente sin remates de ~11 px con
  bordes suavizados. El juego recorta cada etiqueta de la tira con un rectangulo
  fijo, asi que la etiqueta en espanol se escribe DENTRO de la caja de la inglesa:
  mismo inicio a la izquierda y sin pasarse del ancho. Si no cabe, se angosta
  hasta MIN_ANCHO (o el "min" de la etiqueta); si ni asi cabe, es error y hay
  que abreviar.

  OJO: los pixeles empiezan donde dice el campo +0x1C de la cabecera de imagen
  (ver tools/fuente.py); tools/gim2png.py lee desde otro lado y las tiras salen
  revueltas.

LA ESPECIFICACION (_tex/imagenes_es.json)
  [{"archivo": "/PSP_GAME/USRDIR/pack/name/na.bin", "gim": 1,
    "etiquetas": [{"en": "Delete", "caja": [x0, y0, x1, y1], "es": "Borrar"}, ...]}]
  gim es el numero de imagen dentro del archivo (por orden de aparicion de la
  firma MIG.00.1PSP). caja es el rectangulo de la etiqueta inglesa (x1, y1
  exclusivos); "en" es solo para leerlo. Las cajas se sacan con "cajas".

FUENTE
  Noto Sans variable (ejes de peso y ancho), de Google Fonts, licencia OFL:
  https://github.com/google/fonts/raw/main/ofl/notosans/NotoSans%5Bwdth,wght%5D.ttf
  Va en externos/fuentes/ (fuera del repo) o donde diga la variable FUENTE_ES. Se
  usa con peso 500 y a la altura de mayuscula del ingles (medir_cap). Si la
  etiqueta no cabe, primero se angosta la fuente (eje de ancho, hasta 62.5) y
  despues la imagen.

Uso: python3 tools/texto_imagenes.py cajas ISO ARCHIVO GIM      (renglones y palabras)
     python3 tools/texto_imagenes.py medir                      (etiquetas que no caben)
     python3 tools/texto_imagenes.py vista ISO SALIDA_DIR       (PNG antes/despues)
     python3 tools/texto_imagenes.py aplicar ISO_ES             (escribe en sitio)
ISO es cualquier ISO EU (original o traducido): los archivos se leen de ahi.
"""
import json, os, re, struct, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gim2png import unswizzle, pal_entry, palsize
from png2gim import swizzle
from PIL import Image, ImageDraw, ImageFont

SPEC = os.path.join(ROOT, "_tex", "imagenes_es.json")
OFFS = json.load(open(os.path.join(ROOT, "scripts", "iso_offsets.json")))
FUENTE = os.environ.get("FUENTE_ES", os.path.join(os.path.dirname(ROOT), "externos", "fuentes",
                                                  "NotoSans[wdth,wght].ttf"))
PESO = 500
ANCHOS_FUENTE = (100, 93.75, 87.5, 81.25, 75, 68.75, 62.5)
MIN_ANCHO = 0.75
FIRMA = b"MIG.00.1PSP"


def u16(b, o):
    return struct.unpack_from("<H", b, o)[0]


def u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def leer(iso, archivo):
    off, size = OFFS[archivo]
    with open(iso, "rb") as f:
        f.seek(off)
        return off, bytearray(f.read(size))


def gims(d):
    """[(inicio, fin)] de cada GIM dentro del archivo."""
    offs = [m.start() for m in re.finditer(re.escape(FIRMA), d)]
    return [(o, offs[k + 1] if k + 1 < len(offs) else len(d)) for k, o in enumerate(offs)]


class Tira:
    """Un GIM de 4 bits como matriz de indices de paleta."""

    def __init__(self, g):
        self.g = bytearray(g)
        p, self.img, self.pal = 0x10, None, None
        while p + 16 <= len(g):
            t, nx = u16(g, p), u32(g, p + 8)
            if t in (4, 5):
                dp = p + 16
                blk = dict(fmt=u16(g, dp + 4), order=u16(g, dp + 6), w=u16(g, dp + 8),
                           h=u16(g, dp + 10), pix=dp + u32(g, dp + 0x1C))
                if t == 4:
                    self.img = blk
                else:
                    self.pal = blk
            if nx == 0 or p + nx > len(g):
                break
            p += nx
        if not self.img or self.img["fmt"] != 4:
            raise ValueError("solo se editan GIM de 4 bits")
        self.w, self.h = self.img["w"], self.img["h"]
        self.pitch = ((self.w // 2) + 15) & ~15
        self.alto = (self.h + 7) & ~7
        raw = bytes(g[self.img["pix"]:self.img["pix"] + self.pitch * self.alto])
        if self.img["order"] == 1:
            raw = unswizzle(raw, self.pitch, self.alto)
        self.idx = [[(raw[y * self.pitch + x // 2] >> (4 * (x & 1))) & 15 for x in range(self.w)]
                    for y in range(self.alto)]
        pf = self.pal["fmt"]
        self.paleta = [pal_entry(pf, g, self.pal["pix"] + i * palsize(pf)) for i in range(16)]
        colores = {tuple(c[:3]) for c in self.paleta if c[3]}
        if len(colores) > 1:
            raise ValueError(f"la paleta no es de un solo color: {sorted(colores)}")

    def alfa(self, x, y):
        return self.paleta[self.idx[y][x]][3]

    def indice_para(self, a):
        """Indice de paleta con la transparencia mas cercana a a (0-255)."""
        return min(range(16), key=lambda i: (abs(self.paleta[i][3] - a), i))

    def imagen(self):
        im = Image.new("RGBA", (self.w, self.h))
        for y in range(self.h):
            for x in range(self.w):
                im.putpixel((x, y), tuple(self.paleta[self.idx[y][x]]))
        return im

    def bytes(self):
        raw = bytearray(self.pitch * self.alto)
        for y in range(self.alto):
            for x in range(0, self.w, 2):
                raw[y * self.pitch + x // 2] = self.idx[y][x] | (self.idx[y][x + 1] << 4)
        if self.img["order"] == 1:
            raw = swizzle(bytes(raw), self.pitch, self.alto)
        g = bytearray(self.g)
        g[self.img["pix"]:self.img["pix"] + len(raw)] = raw
        return bytes(g)


def renglones(t):
    filas = [any(t.alfa(x, y) for x in range(t.w)) for y in range(t.h)]
    out, y = [], 0
    while y < t.h:
        if filas[y]:
            a = y
            while y < t.h and filas[y]:
                y += 1
            out.append((a, y))
        else:
            y += 1
    return out


def palabras(t, a, b):
    cols = [any(t.alfa(x, y) for y in range(a, b)) for x in range(t.w)]
    out, x = [], 0
    while x < t.w:
        if cols[x]:
            s = x
            while x < t.w and cols[x]:
                x += 1
            out.append((s, x))
        else:
            x += 1
    return out


ESCALA = 4      # se dibuja a 4x y se reduce promediando: espaciado parejo


def fuente(cap, ancho_fuente=100):
    """(fuente a ESCALA veces, fila de arriba de la H) para que la H mayuscula,
    reducida, mida cap px."""
    ref = ImageFont.truetype(FUENTE, 1000)
    ref.set_variation_by_axes([PESO, ancho_fuente])
    x0, y0, x1, y1 = ref.getbbox("H")
    f = ImageFont.truetype(FUENTE, max(4, round(1000 * cap * ESCALA / (y1 - y0))))
    f.set_variation_by_axes([PESO, ancho_fuente])
    return f, f.getbbox("H")[1]


def dibujar(texto, cap, ancho, minimo=MIN_ANCHO):
    """(filas de alfa 0-255, ancho, desfase) de texto con mayusculas de cap px. La
    fila 0 de las filas queda desfase filas abajo de la parte de arriba de la H
    (negativo si un acento o ¿ ¡ sube mas que la H)."""
    for ancho_fuente in ANCHOS_FUENTE:
        f, arriba = fuente(cap, ancho_fuente)
        caja = f.getbbox(texto)
        if (caja[2] - caja[0]) / ESCALA <= ancho:
            break
    margen = cap * ESCALA
    im = Image.new("L", (caja[2] + ESCALA * 2, cap * ESCALA * 3))
    ImageDraw.Draw(im).text((0, margen - arriba), texto, font=f, fill=255)
    tinta = im.getbbox()
    if not tinta:
        return [], 1, 0
    # recorte alineado a la reticula de ESCALA para que la H caiga en filas enteras
    y_ini = margen + ((tinta[1] - margen) // ESCALA) * ESCALA
    y_fin = y_ini + -(-(tinta[3] - y_ini) // ESCALA) * ESCALA
    im = im.crop((tinta[0], y_ini, tinta[0] + -(-(tinta[2] - tinta[0]) // ESCALA) * ESCALA, y_fin))
    im = im.resize((im.width // ESCALA, im.height // ESCALA), Image.BOX)
    desfase = (y_ini - margen) // ESCALA
    if im.width > ancho:
        if im.width * minimo > ancho:
            raise ValueError(f"{texto!r} mide {im.width} px y la caja {ancho}")
        im = im.resize((ancho, im.height), Image.LANCZOS)
    filas = [[im.getpixel((x, y)) for x in range(im.width)] for y in range(im.height)]
    return filas, im.width, desfase


def medir_cap(t, caja):
    """(fila de arriba, alto de mayuscula) del texto ingles en la caja: de la primera
    fila con tinta a la linea base, que es la ultima fila con al menos 45% de
    la tinta de la fila mas llena (las colitas de g, p, y quedan fuera)."""
    x0, y0, x1, y1 = caja
    cuenta = [sum(1 for x in range(x0, x1) if t.alfa(x, y) > 40) for y in range(y0, y1)]
    if not any(cuenta):
        return y0, 9
    arriba = next(i for i, c in enumerate(cuenta) if c)
    base = max(i for i, c in enumerate(cuenta) if c >= max(cuenta) * 0.45)
    return y0 + arriba, base - arriba + 1


def cap_renglon(t, y0, y1):
    """Alto de mayuscula del renglon y0-y1 medido en todo su ancho, para que todas
    las etiquetas de un renglon queden del mismo tamano."""
    return medir_cap(t, (0, y0, t.w, y1))[1]


def editar(t, etiquetas, cap=None, errores=None):
    """Escribe las etiquetas en la tira. Si errores es una lista, las que no caben se
    anotan ahi y se saltan; si no, se lanza la excepcion."""
    for e in etiquetas:
        x0, y0, x1, y1 = e["caja"]
        arriba, _ = medir_cap(t, e["caja"])
        alto_cap = e.get("cap", cap or cap_renglon(t, y0, y1))
        for y in range(y0, y1):
            for x in range(x0, x1):
                t.idx[y][x] = t.indice_para(0)
        try:
            filas, w, desfase = dibujar(e["es"], alto_cap, x1 - x0, e.get("min", MIN_ANCHO))
        except ValueError as err:
            if errores is None:
                raise
            errores.append(f"{e['en']}: {err} (mayuscula de {alto_cap} px)")
            continue
        for dy, fila in enumerate(filas):
            y = arriba + desfase + dy
            if not 0 <= y < t.h:
                continue
            for dx, a in enumerate(fila):
                if a:
                    t.idx[y][x0 + dx] = t.indice_para(a)


def medir():
    """Lista las etiquetas que no caben ni angostadas al MIN_ANCHO."""
    malas = 0
    for item in json.load(open(SPEC, encoding="utf-8")):
        for e in item["etiquetas"]:
            x0, y0, x1, y1 = e["caja"]
            f, _ = fuente(e.get("cap", 10), ANCHOS_FUENTE[-1])   # aproximado: sin la tira
            ancho = (f.getbbox(e["es"])[2] - f.getbbox(e["es"])[0]) / ESCALA
            if ancho * e.get("min", MIN_ANCHO) > x1 - x0:
                malas += 1
                print(f"{os.path.basename(item['archivo'])}#{item['gim']} {e['en']}: "
                      f"{e['es']!r} {ancho} px, caja {x1 - x0} (cabe {int((x1 - x0) / MIN_ANCHO)})")
    print(f"no caben: {malas}")


def cajas(iso, archivo, n):
    _, d = leer(iso, archivo)
    o, z = gims(d)[n]
    t = Tira(d[o:z])
    print(f"{t.w}x{t.h}")
    for a, b in renglones(t):
        print(f"renglon y={a}-{b}: " + " ".join(f"[{s},{e}]" for s, e in palabras(t, a, b)))


def vista(iso, salida):
    os.makedirs(salida, exist_ok=True)
    for item in json.load(open(SPEC, encoding="utf-8")):
        _, d = leer(iso, item["archivo"])
        o, z = gims(d)[item["gim"]]
        t = Tira(d[o:z])
        antes = t.imagen()
        errores = []
        editar(t, item["etiquetas"], errores=errores)
        for err in errores:
            print(f"  NO CABE {os.path.basename(item['archivo'])}#{item['gim']} {err}")
        despues = t.imagen()
        im = Image.new("RGBA", (t.w, t.h * 2 + 4), (0, 0, 0, 255))
        im.alpha_composite(antes, (0, 0))
        im.alpha_composite(despues, (0, t.h + 4))
        nombre = os.path.basename(item["archivo"]) + f"_{item['gim']}.png"
        im.resize((im.width * 4, im.height * 4), Image.NEAREST).save(os.path.join(salida, nombre))
        print(os.path.join(salida, nombre))


def aplicar(iso):
    spec = json.load(open(SPEC, encoding="utf-8"))
    por_archivo = {}
    for item in spec:
        por_archivo.setdefault(item["archivo"], []).append(item)
    for archivo, items in por_archivo.items():
        off, d = leer(iso, archivo)
        lista = gims(d)
        for item in items:
            o, z = lista[item["gim"]]
            t = Tira(d[o:z])
            editar(t, item["etiquetas"])
            nuevo = t.bytes()
            assert len(nuevo) == z - o
            d[o:z] = nuevo
        with open(iso, "r+b") as f:
            f.seek(off)
            f.write(d)
        print(f"{archivo}: {sum(len(i['etiquetas']) for i in items)} etiquetas")


if __name__ == "__main__":
    modo = sys.argv[1:2]
    if modo == ["cajas"]:
        cajas(sys.argv[2], sys.argv[3], int(sys.argv[4]))
    elif modo == ["medir"]:
        medir()
    elif modo == ["vista"]:
        vista(sys.argv[2], sys.argv[3])
    elif modo == ["aplicar"]:
        aplicar(sys.argv[2])
    else:
        sys.exit(__doc__)
