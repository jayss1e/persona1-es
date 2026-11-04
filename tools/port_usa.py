#!/usr/bin/env python3
"""
port_usa.py — Pasa la traducción de la ISO europea a la ISO USA (ULUS-10432).

La versión USA y la europea de PSN (NPEH-00069) traen casi los mismos datos.
Comparadas archivo por archivo, solo cambian el EBOOT, pack/title/title.bin y
pack/end/ENDALL.BIN. Por eso no se vuelve a traducir nada: se toma lo que la
traducción cambió en la ISO europea y se aplica sobre la USA.

Cómo lo hace:

  Archivos de datos. Para cada archivo que la traducción cambió en la ISO
  europea se toman los bytes que cambiaron y se escriben en el mismo lugar del
  archivo USA. Antes se comprueba que en la USA esos bytes sean iguales al
  original europeo. Si no lo son, se detiene, porque el archivo es distinto y
  habría que portarlo a mano. Así entra title.bin, que en la USA solo cambia en
  una zona que la traducción no toca.

  EBOOT. El código de la versión USA es 0x420 bytes más corto, así que el texto
  y los datos quedan en otras posiciones y los punteros valen otra cosa.
    - Cada tramo que cambió en el EBOOT europeo se busca en el EBOOT USA por
      su contenido original con algo de contexto alrededor. Los tramos del
      segmento de datos se corren lo mismo que el segmento entero.
    - Las palabras que son punteros se reconocen con la tabla de reubicaciones
      del propio EBOOT (secciones .rel.*, tipo R_MIPS_32). A esas no se les
      copia el valor europeo: se convierte la dirección a la que apuntan a su
      dirección en la versión USA.
    - Se comprueba que en la USA cada tramo tenga el mismo texto original y que
      cada puntero tenga el valor que le corresponde. Lo que no cuadre se
      reporta y el script falla.

Uso (después de bash tools/compilar_todo.sh, que deja rebuilt/persona_es_full.iso):
  PERSONA_ISO_EU=~/ruta/Persona.iso PERSONA_ISO_USA=~/ruta/USA.iso \\
      python3 tools/port_usa.py
Salida: rebuilt/persona_es_usa.iso y patches/persona_es_usa.xdelta
"""
import bisect
import hashlib
import os
import shutil
import struct
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ISO_FIN_EU = os.path.join(ROOT, "rebuilt", "persona_es_full.iso")
ISO_FIN_US = os.path.join(ROOT, "rebuilt", "persona_es_usa.iso")
XDELTA_US = os.path.join(ROOT, "patches", "persona_es_usa.xdelta")
ELF_EU = os.path.join(ROOT, "_eboot", "eboot_dec.elf")
ELF_US = os.path.join(ROOT, "_eboot", "usa_dec.elf")
PSPDECRYPT = os.path.join(ROOT, "..", "externos", "pspdecrypt", "pspdecrypt")
EBOOT = "/PSP_GAME/SYSDIR/EBOOT.BIN"
MD5_EU = "03ee31f32bcd3bb697de347fba4493d9"
MD5_US = "c75f75b0f1e92d7cca77d75585bebd82"
SECTOR = 2048


# ---------------------------------------------------------------- ISO 9660

def listar_iso(ruta):
    """Devuelve {ruta_en_iso: (byte_inicio, tamaño)} de todos los archivos."""
    archivos = {}
    with open(ruta, "rb") as f:
        def leer(lba, n):
            f.seek(lba * SECTOR)
            return f.read(n)

        pvd = leer(16, SECTOR)
        if pvd[1:6] != b"CD001":
            raise SystemExit(f"{ruta} no parece una ISO 9660")

        def recorrer(lba, tam, base):
            datos = leer(lba, tam)
            i = 0
            while i < len(datos):
                largo = datos[i]
                if largo == 0:  # el resto del sector está vacío
                    i = (i // SECTOR + 1) * SECTOR
                    continue
                reg = datos[i:i + largo]
                ext, n = struct.unpack_from("<I", reg, 2)[0], struct.unpack_from("<I", reg, 10)[0]
                banderas, nlen = reg[25], reg[32]
                nombre = reg[33:33 + nlen]
                i += largo
                if nombre in (b"\x00", b"\x01"):  # . y ..
                    continue
                nombre = nombre.decode("ascii").split(";")[0]
                ruta_iso = f"{base}/{nombre}"
                if banderas & 2:
                    recorrer(ext, n, ruta_iso)
                else:
                    archivos[ruta_iso] = (ext * SECTOR, n)

        raiz = pvd[156:156 + 34]
        recorrer(struct.unpack_from("<I", raiz, 2)[0], struct.unpack_from("<I", raiz, 10)[0], "")
    return archivos


def leer(ruta, inicio, n):
    with open(ruta, "rb") as f:
        f.seek(inicio)
        return f.read(n)


def md5(ruta):
    h = hashlib.md5()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def tramos(a, b, unir=1):
    """Tramos [ini, fin) donde a y b difieren. Une los separados por menos de `unir` bytes."""
    res = []
    i, n = 0, min(len(a), len(b))
    while i < n:
        if a[i] != b[i]:
            if res and i - res[-1][1] < unir:
                res[-1][1] = i + 1
            else:
                res.append([i, i + 1])
        i += 1
    return res


# ---------------------------------------------------------------- ELF de PSP

class Elf:
    """Lo mínimo de un ELF de PSP: segmentos y palabras que son punteros."""

    def __init__(self, datos):
        self.d = datos
        phoff, shoff = struct.unpack_from("<II", datos, 0x1C)
        phnum = struct.unpack_from("<H", datos, 0x2C)[0]
        shnum = struct.unpack_from("<H", datos, 0x30)[0]
        # segmentos: (desplazamiento en archivo, dirección, tamaño en archivo)
        self.seg = []
        for k in range(phnum):
            _, off, vaddr, _, filesz = struct.unpack_from("<IIIII", datos, phoff + 32 * k)
            self.seg.append((off, vaddr, filesz))
        # .text, para asegurar que la traducción no toca código
        self.text = None
        shstr = struct.unpack_from("<IIIIII", datos, shoff + 40 * struct.unpack_from("<H", datos, 0x32)[0])
        for k in range(shnum):
            nom, tipo, _, _, off, tam = struct.unpack_from("<IIIIII", datos, shoff + 40 * k)
            fin = datos.index(b"\0", shstr[4] + nom)
            if datos[shstr[4] + nom:fin] == b".text":
                self.text = (off, off + tam)
        # punteros: palabras con reubicación R_MIPS_32 (tipo 2). Las secciones de
        # reubicación de PSP son del tipo 0x700000A0. r_info trae el tipo en el
        # byte bajo y en el siguiente el segmento al que se suma r_offset.
        self.punteros = set()
        for k in range(shnum):
            _, tipo, _, _, off, tam = struct.unpack_from("<IIIIII", datos, shoff + 40 * k)
            if tipo != 0x700000A0:
                continue
            for j in range(off, off + tam, 8):
                r_offset, r_info = struct.unpack_from("<II", datos, j)
                if r_info & 0xFF == 2:
                    base = self.seg[(r_info >> 8) & 0xFF]
                    self.punteros.add(base[0] + r_offset)

    def segmento(self, off):
        """Índice del segmento que contiene el byte `off` del archivo, o None."""
        for k, (s_off, _, tam) in enumerate(self.seg):
            if s_off <= off < s_off + tam:
                return k
        return None

    def a_archivo(self, vaddr):
        for s_off, s_va, tam in self.seg:
            if s_va <= vaddr <= s_va + tam:
                return s_off + vaddr - s_va
        return None

    def a_direccion(self, off):
        for s_off, s_va, tam in self.seg:
            if s_off <= off <= s_off + tam:
                return s_va + off - s_off
        return None

    def palabra(self, off):
        return struct.unpack_from("<I", self.d, off)[0]


class Mapa:
    """Convierte posiciones del EBOOT europeo en posiciones del EBOOT USA."""

    VENTANA = 0x4000  # cuánto se busca alrededor de la posición esperada

    def __init__(self, eu, us):
        self.eu, self.us = eu, us
        self.fijos = []  # tramos ya ubicados: (ini_eu, fin_eu, ini_us), ordenados

    def agregar(self, ini, fin, destino):
        bisect.insort(self.fijos, (ini, fin, destino))

    def corrimiento_segmento(self, k):
        return self.us.seg[k][0] - self.eu.seg[k][0]

    def buscar(self, ini, fin, contexto=32):
        """Busca eu[ini-contexto:fin+contexto] en el EBOOT USA. Devuelve ini en la USA o None."""
        k = self.eu.segmento(ini)
        if k is not None and k > 0:
            # el segmento de datos es igual en las dos versiones, solo corrido
            return ini + self.corrimiento_segmento(k)
        a, b = max(0, ini - contexto), fin + contexto
        muestra = self.eu.d[a:b]
        esperado = a + self.corrimiento_segmento(0)
        lo = max(0, esperado - self.VENTANA)
        hi = esperado + self.VENTANA + len(muestra)
        zona = self.us.d[lo:hi]
        p = zona.find(muestra)
        if p < 0 or zona.find(muestra, p + 1) >= 0:
            return None
        return lo + p + (ini - a)

    def posicion(self, off):
        """Posición USA de un byte cualquiera del EBOOT europeo."""
        i = bisect.bisect_right(self.fijos, (off, float("inf"), 0)) - 1
        if i >= 0:
            ini, fin, destino = self.fijos[i]
            if ini <= off < fin:
                return destino + off - ini
        r = self.buscar(off, off + 16, contexto=16)
        if r is None:
            r = self.buscar(off, off + 48, contexto=0)
        return r

    def direccion(self, vaddr):
        """Dirección USA de una dirección de memoria europea."""
        off = self.eu.a_archivo(vaddr)
        if off is None:
            return None
        nuevo = self.posicion(off)
        return None if nuevo is None else self.us.a_direccion(nuevo)


def portar_eboot(eboot_eu_trad, errores):
    """Aplica al EBOOT USA lo que la traducción cambió en el europeo. Devuelve los bytes."""
    eu = Elf(open(ELF_EU, "rb").read())
    us = Elf(open(ELF_US, "rb").read())
    trad = eboot_eu_trad[:len(eu.d)]
    if len(eboot_eu_trad.rstrip(b"\0")) > len(eu.d):
        errores.append("el EBOOT traducido europeo es más largo que el original")
    mapa = Mapa(eu, us)
    cambios = tramos(eu.d, trad, unir=8)
    for ini, fin in cambios:
        if eu.text and ini < eu.text[1] and fin > eu.text[0]:
            errores.append(f"EBOOT: la traducción cambia código en 0x{ini:X}, eso no se porta solo")

    # 1. ubicar cada tramo. Primero con contexto y, si no aparece, solo con sus bytes.
    pendientes = []
    for ini, fin in cambios:
        destino = mapa.buscar(ini, fin)
        if destino is None:
            destino = mapa.buscar(ini, fin, contexto=0) if fin - ini >= 12 else None
        if destino is None:
            pendientes.append((ini, fin))
            continue
        mapa.agregar(ini, fin, destino)

    # Algunos tramos están entre tablas de punteros, que valen distinto en cada
    # versión, y su contexto no aparece. A esos se les prueba el corrimiento del
    # tramo ya ubicado anterior y el del siguiente, y se acepta el que deja el
    # mismo texto original en la USA.
    for ini, fin in pendientes:
        i = bisect.bisect_left(mapa.fijos, (ini, 0, 0))
        vecinos = mapa.fijos[max(0, i - 1):i + 1]
        destino = None
        for v_ini, _, v_dest in vecinos:
            s = v_dest - v_ini
            if us.d[ini + s:fin + s] == eu.d[ini:fin]:
                destino = ini + s
                break
        if destino is None:
            errores.append(f"EBOOT: no se encontró en la USA el tramo 0x{ini:X}-0x{fin:X}")
            continue
        mapa.agregar(ini, fin, destino)

    # 2. copiar los bytes nuevos y corregir punteros
    salida = bytearray(us.d)
    n_texto = n_punteros = 0
    for ini, fin, destino in list(mapa.fijos):
        palabras = {p for p in range(ini & ~3, fin, 4) if p in eu.punteros}
        for p in range(ini, fin):
            if (p & ~3) in palabras:
                continue
            if us.d[destino + p - ini] != eu.d[p]:
                errores.append(f"EBOOT: el original USA no coincide en 0x{p:X} (USA 0x{destino + p - ini:X})")
                break
            salida[destino + p - ini] = trad[p]
            n_texto += 1
        for p in sorted(palabras):
            q = destino + p - ini
            if q not in us.punteros:
                errores.append(f"EBOOT: 0x{p:X} es puntero en la europea y 0x{q:X} no lo es en la USA")
                continue
            viejo, nuevo = eu.palabra(p), struct.unpack_from("<I", trad, p)[0]
            if mapa.direccion(viejo) != us.palabra(q):
                errores.append(f"EBOOT: el puntero 0x{p:X} no corresponde al de la USA 0x{q:X}")
                continue
            convertido = mapa.direccion(nuevo)
            if convertido is None:
                errores.append(f"EBOOT: no se pudo convertir el puntero 0x{p:X} -> 0x{nuevo:X}")
                continue
            struct.pack_into("<I", salida, q, convertido)
            n_punteros += 1
    print(f"EBOOT: {len(cambios)} tramos, {n_texto} bytes de texto, {n_punteros} punteros convertidos")
    return bytes(salida)


# ---------------------------------------------------------------- principal

def main():
    iso_eu = os.environ.get("PERSONA_ISO_EU")
    iso_us = os.environ.get("PERSONA_ISO_USA")
    if not iso_eu or not iso_us:
        raise SystemExit("faltan PERSONA_ISO_EU y PERSONA_ISO_USA con las rutas de las dos ISO")
    for ruta, esperado in ((iso_eu, MD5_EU), (iso_us, MD5_US)):
        if md5(ruta) != esperado:
            raise SystemExit(f"{ruta}: el MD5 no es {esperado}")
    if not os.path.exists(ISO_FIN_EU):
        raise SystemExit("falta rebuilt/persona_es_full.iso, primero corre tools/compilar_todo.sh")

    lista_eu, lista_us = listar_iso(iso_eu), listar_iso(iso_us)

    # EBOOT USA descifrado
    if not os.path.exists(ELF_US):
        cifrado = os.path.join(ROOT, "rebuilt", "usa_EBOOT.BIN")
        open(cifrado, "wb").write(leer(iso_us, *lista_us[EBOOT]))
        subprocess.run([PSPDECRYPT, "-o", ELF_US, cifrado], check=True, stdout=subprocess.DEVNULL)
        os.remove(cifrado)

    print("copiando la ISO USA...")
    shutil.copyfile(iso_us, ISO_FIN_US)
    errores = []
    iguales = mezclados = 0
    with open(ISO_FIN_US, "r+b") as out:
        for ruta, (ini, tam) in sorted(lista_eu.items()):
            orig = leer(iso_eu, ini, tam)
            trad = leer(ISO_FIN_EU, ini, tam)
            if orig == trad:
                continue
            if ruta == EBOOT:
                datos = portar_eboot(trad, errores)
                u_ini, u_tam = lista_us[EBOOT]
                if len(datos) > u_tam:
                    errores.append("el EBOOT USA descifrado no cabe en el lugar del EBOOT.BIN")
                    continue
                out.seek(u_ini)
                out.write(datos + b"\0" * (u_tam - len(datos)))
                continue
            if ruta not in lista_us or lista_us[ruta][1] != tam:
                errores.append(f"{ruta}: no existe en la USA o tiene otro tamaño")
                continue
            u_ini = lista_us[ruta][0]
            u_orig = leer(iso_us, u_ini, tam)
            if u_orig == orig:
                out.seek(u_ini)
                out.write(trad)
                iguales += 1
                continue
            # el archivo cambia entre versiones: aplicar solo los bytes traducidos
            nuevo = bytearray(u_orig)
            for a, b in tramos(orig, trad):
                if u_orig[a:b] != orig[a:b]:
                    errores.append(f"{ruta}: la traducción toca 0x{a:X}, que es distinto en la USA")
                    break
                nuevo[a:b] = trad[a:b]
            out.seek(u_ini)
            out.write(nuevo)
            mezclados += 1
            print(f"{ruta}: distinto en la USA, se aplicaron solo los bytes traducidos")
    print(f"archivos: {iguales} copiados tal cual, {mezclados} mezclados")

    if errores:
        for e in errores[:40]:
            print("ERROR", e)
        print(f"{len(errores)} errores, no se generó el parche")
        sys.exit(1)
    subprocess.run(["xdelta3", "-e", "-9", "-f", "-s", iso_us, ISO_FIN_US, XDELTA_US], check=True)
    print("ISO USA:", md5(ISO_FIN_US))
    print("parche:", XDELTA_US, os.path.getsize(XDELTA_US), "bytes")


if __name__ == "__main__":
    main()
