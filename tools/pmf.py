#!/usr/bin/env python3
"""
pmf.py — Desarma y vuelve a armar los videos .pmf de la PSP (PSP_GAME/USRDIR/MOVIE/).

Formato, tal como lo traen los videos de Persona (ver notas/subtitulos.md):

  - Encabezado PSMF de 0x800 bytes. En 0x0C va el tamaño del stream que sigue.
  - Un MPEG-PS en packs de 2048 bytes, con el reloj del pack (SCR) a 25000 x 50
    bytes por segundo, o sea 147.456 tics de 90 kHz por pack.
  - Video H.264 en PES 0xE0, con un delimitador de unidad (00 00 00 01 09) antes
    de cada cuadro. Va por grupos de cuadros (GOP) que empiezan en un pack nuevo
    con system header (0xBB), un índice del GOP en private stream 2 (0xBF) y un
    PES de video con PTS, DTS y P-STD. Dentro del GOP, cada 16 cuadros un PES
    lleva PTS y DTS. El último pack de video del GOP se rellena con un PES 0xBE.
  - El índice 0xBF: 01 E0, cuatro u16 con el pack (relativo al inicio del GOP)
    donde termina cada uno de los cuadros 1 a 4 (FFFF si no hay), cuatro bytes
    en cero, un u16 con el largo de la tabla, el número de cuadros y por cada
    cuadro 00 80 y su tamaño en bytes.
  - Audio ATRAC3plus en PES 0xBD, en packs propios. Cada PES lleva PTS del primer
    cuadro de audio que empieza en él y 4 bytes propios: sub-stream 0, un cero y
    un u16 con la posición de ese cuadro dentro de los datos. El primer PES de
    audio de cada GOP lleva además P-STD. Los cuadros de audio miden 752 bytes:
    8 de encabezado (0F D0 y parámetros) y 744 de datos, 2048 muestras a 44.1 kHz.

Al volver a armar, el video se manda a lo más 1 segundo antes de su DTS y el
audio unas 0.185 s antes de su PTS, que es lo que hace el original. El reloj
avanza un pack a la vez y salta cuando hay que esperar.

Uso:
  python3 tools/pmf.py audio VIDEO.pmf SALIDA.oma       audio que lee ffmpeg
  python3 tools/pmf.py video VIDEO.pmf SALIDA.h264      video H.264 con sus delimitadores
  python3 tools/pmf.py gops VIDEO.pmf                   cuadros donde empieza cada GOP
  python3 tools/pmf.py armar VIDEO.pmf NUEVO.h264 SALIDA.pmf
        arma un .pmf con el audio y el encabezado del original y el video nuevo.
        El video nuevo debe tener los mismos cuadros, sin cuadros B y con un
        cuadro clave al inicio de cada GOP del original. Si queda más chico que
        el original, se rellena hasta el mismo tamaño.
"""
import struct
import sys

PACK = 2048
HEADER = 0x800
TICS_PACK = 2048 * 90000 / (25000 * 50)   # 147.456
CUADRO_V = 3003                            # 29.97 cuadros por segundo
MUESTRAS_A = 2048                          # muestras por cuadro de audio
CUADRO_A = 752
ANTICIPO_V = 90000                          # el video llega a lo más 1 s antes
ANTICIPO_A = 16700                          # el audio llega unas 0.185 s antes
AUD = b"\x00\x00\x00\x01\x09"


# ---------------------------------------------------------------- marcas de tiempo

def leer_ts(b):
    return ((b[0] >> 1) & 7) << 30 | b[1] << 22 | (b[2] >> 1) << 15 | b[3] << 7 | b[4] >> 1


def escribir_ts(prefijo, t):
    return bytes([(prefijo << 4) | ((t >> 29) & 0x0E) | 1, (t >> 22) & 0xFF,
                  ((t >> 14) & 0xFE) | 1, (t >> 7) & 0xFF, ((t << 1) & 0xFE) | 1])


def leer_scr(b):
    return (((b[0] >> 3) & 7) << 30 | (b[0] & 3) << 28 | b[1] << 20 |
            (b[2] >> 3) << 15 | (b[2] & 3) << 13 | b[3] << 5 | b[4] >> 3)


def encabezado_pack(scr, mux, relleno=0):
    s = scr
    return (b"\x00\x00\x01\xba" + bytes([
        0x44 | ((s >> 27) & 0x38) | ((s >> 28) & 3), (s >> 20) & 0xFF,
        ((s >> 12) & 0xF8) | 4 | ((s >> 13) & 3), (s >> 5) & 0xFF,
        ((s << 3) & 0xF8) | 4, 1,
        (mux >> 14) & 0xFF, (mux >> 6) & 0xFF, ((mux << 2) & 0xFC) | 3,
        0xF8 | relleno]) + b"\xff" * relleno)


def pes(sid, cabeza, datos):
    """PES con los bytes de encabezado opcionales `cabeza` (flags, largo y campos)."""
    cuerpo = cabeza + datos
    return b"\x00\x00\x01" + bytes([sid]) + struct.pack(">H", len(cuerpo)) + cuerpo


def relleno_pes(n):
    """PES de relleno (0xBE) de n bytes en total. n >= 6."""
    return b"\x00\x00\x01\xbe" + struct.pack(">H", n - 6) + b"\xff" * (n - 6)


# ---------------------------------------------------------------- desarmar

def packs(d):
    """Genera, por pack: (scr, mux, [(stream_id, bytes del PES completo)])."""
    pos = HEADER
    while pos + 14 <= len(d):
        if d[pos:pos + 4] != b"\x00\x00\x01\xba":
            raise ValueError(f"no hay pack en 0x{pos:X}")
        scr = leer_scr(d[pos + 4:pos + 10])
        mux = d[pos + 10] << 14 | d[pos + 11] << 6 | d[pos + 12] >> 2
        i, fin, items = pos + 14 + (d[pos + 13] & 7), pos + PACK, []
        while i + 6 <= fin and d[i:i + 3] == b"\x00\x00\x01":
            largo = struct.unpack(">H", d[i + 4:i + 6])[0]
            items.append((d[i + 3], d[i:i + 6 + largo]))
            i += 6 + largo
        yield scr, mux, items
        pos = fin


def desarmar(d):
    """Saca del .pmf todo lo necesario para volver a armarlo."""
    if d[:4] != b"PSMF":
        raise ValueError("no es un .pmf")
    r = {"encabezado": d[:HEADER], "video": bytearray(), "audio": bytearray(),
         "gops": [], "pts_audio": [], "plantillas": {}}
    scr0 = mux0 = None
    for scr, mux, items in packs(d):
        if scr0 is None:
            scr0, mux0 = scr, mux
        for sid, p in items:
            if sid == 0xBB:
                r["plantillas"].setdefault("system", p)
            elif sid == 0xBF:
                r["gops"].append(struct.unpack(">H", p[22:24])[0])
            elif sid == 0xE0:
                hl = p[8]
                if p[7] & 0x80 and "pts_video" not in r:
                    r["pts_video"] = leer_ts(p[9:14])
                    r["dts_video"] = leer_ts(p[14:19]) if p[7] & 0x40 else r["pts_video"]
                if hl == 13:
                    r["plantillas"].setdefault("pstd_video", p[20:22])
                r["video"] += p[9 + hl:]
            elif sid == 0xBD:
                hl = p[8]
                if p[9 + hl] != 0:
                    continue
                desde = struct.unpack(">H", p[9 + hl + 2:9 + hl + 4])[0]
                r["pts_audio"].append((len(r["audio"]) + desde, leer_ts(p[9:14])))
                if hl == 8:
                    r["plantillas"].setdefault("pstd_audio", p[15:17])
                r["audio"] += p[9 + hl + 4:]
    r["scr0"], r["mux"] = scr0, mux0
    r["cuadros"] = partir_cuadros(bytes(r["video"]))
    if len(r["audio"]) % CUADRO_A:
        raise ValueError("el audio no es un número entero de cuadros")
    # Inicio del audio en tics, con decimales: el intervalo que deja todos los PTS bien redondeados.
    lo, hi = -1e18, 1e18
    for byte, pts in r["pts_audio"]:
        k = byte // CUADRO_A
        t = k * MUESTRAS_A * 90000 / 44100
        lo, hi = max(lo, pts - 0.5 - t), min(hi, pts + 0.5 - t)
    r["inicio_audio"] = (lo + hi) / 2
    return r


def partir_cuadros(es):
    """Parte un H.264 Annex B en cuadros, usando el delimitador de unidad."""
    cortes = []
    k = es.find(AUD)
    if k != 0:
        raise ValueError("el video no empieza con un delimitador de unidad (aud)")
    while k >= 0:
        cortes.append(k)
        k = es.find(AUD, k + 5)
    cortes.append(len(es))
    return [es[cortes[j]:cortes[j + 1]] for j in range(len(cortes) - 1)]


# ---------------------------------------------------------------- armar

def armar(orig, cuadros, tam_objetivo=None):
    """Arma un .pmf con el audio, el encabezado y los GOP de `orig` y los cuadros dados."""
    if len(cuadros) != sum(orig["gops"]):
        raise ValueError(f"el video nuevo tiene {len(cuadros)} cuadros y el original {sum(orig['gops'])}")
    pl = orig["plantillas"]
    pts0, dts0 = orig["pts_video"], orig["dts_video"]
    audio = bytes(orig["audio"])
    n_audio = len(audio) // CUADRO_A

    def pts_a(k):
        return int(orig["inicio_audio"] + k * MUESTRAS_A * 90000 / 44100 + 0.5)

    # Trozos de audio, uno por PES: (inicio en bytes, si lleva P-STD). El tamaño
    # depende de si lleva P-STD, así que se decide al armar.
    salida = []
    reloj = float(orig["scr0"])
    primero = True
    pos_audio = 0
    audio_pstd = False

    def soltar_audio():
        """Primer cuadro que empieza en el siguiente trozo de audio y su liberación."""
        k = -(-pos_audio // CUADRO_A)
        return k, pts_a(k) - ANTICIPO_A

    def pack_audio(scr):
        nonlocal pos_audio, audio_pstd
        k = -(-pos_audio // CUADRO_A)
        cabeza = b"\x81\x81\x08" + escribir_ts(2, pts_a(k)) + b"\x1e" + pl["pstd_audio"] if audio_pstd \
            else b"\x81\x80\x05" + escribir_ts(2, pts_a(k))
        audio_pstd = False
        lugar = PACK - 14 - 6 - len(cabeza) - 4
        trozo = audio[pos_audio:pos_audio + lugar]
        priv = b"\x00\x00" + struct.pack(">H", k * CUADRO_A - pos_audio)
        p = pes(0xBD, cabeza, priv + trozo)
        pos_audio += len(trozo)
        cuerpo = p
        if len(cuerpo) < PACK - 14:
            cuerpo += relleno_pes(PACK - 14 - len(cuerpo))
        return encabezado_pack(scr, orig["mux"]) + cuerpo

    indice = 0                      # primer cuadro del GOP actual
    for n_gop in orig["gops"]:
        gop = cuadros[indice:indice + n_gop]
        datos = b"".join(gop)
        # dónde empieza cada cuadro dentro de `datos`
        inicios, acum = [], 0
        for c in gop:
            inicios.append(acum)
            acum += len(c)
        largo_tabla = 2 + 4 * n_gop
        bf_largo = 6 + 16 + largo_tabla
        system = pl["system"]
        # Repartir el video del GOP en PES: el primero con PTS, DTS y P-STD, y
        # luego uno con PTS y DTS donde empieza cada cuadro múltiplo de 16.
        trozos = []                 # (inicio, fin, cabeza)
        pos = 0
        sello = 16
        while pos < len(datos):
            if pos == 0:
                lugar = PACK - 14 - len(system) - bf_largo - 9 - 13
                t = dts0 + CUADRO_V * indice
                cabeza = b"\x81\xc1\x0d" + escribir_ts(3, t + CUADRO_V) + escribir_ts(1, t) + b"\x1e" + pl["pstd_video"]
                fin = min(pos + lugar, len(datos))
            else:
                lugar = PACK - 14 - 9
                fin = min(pos + lugar, len(datos))
                # ¿empieza en este trozo el cuadro que toca sellar?
                cabeza = b"\x81\x00\x00"
                if sello < n_gop and inicios[sello] < pos + lugar - 10:
                    lugar -= 10
                    fin = min(pos + lugar, len(datos))
                    if inicios[sello] < fin:
                        # PTS del primer cuadro que empieza en este trozo
                        j = next(j for j in range(n_gop) if inicios[j] >= pos)
                        t = dts0 + CUADRO_V * (indice + j)
                        cabeza = b"\x81\xc0\x0a" + escribir_ts(3, t + CUADRO_V) + escribir_ts(1, t)
                        while sello < n_gop and inicios[sello] < fin:
                            sello += 16
                    else:
                        lugar += 10
                        fin = min(pos + lugar, len(datos))
            trozos.append((pos, fin, cabeza))
            pos = fin
        # Índice 0xBF. Los packs donde terminan los cuadros 1 a 4 se escriben al
        # final del GOP, porque cuentan también los packs de audio intercalados.
        tabla = struct.pack(">H", n_gop) + b"".join(b"\x00\x80" + struct.pack(">H", len(c)) for c in gop)
        if any(len(c) > 0xFFFF for c in gop):
            raise ValueError("un cuadro mide más de 65535 bytes, baja la tasa de bits")
        bf = (b"\x01\xe0" + b"\xff" * 8 +
              b"\x00" * 4 + struct.pack(">H", largo_tabla) + tabla)
        bf = b"\x00\x00\x01\xbf" + struct.pack(">H", len(bf)) + bf
        # Los packs de video de este GOP, intercalando audio cuando le toca.
        audio_pstd = True
        pack_de_trozo = []
        for t, (a, b, cabeza) in enumerate(trozos):
            ultimo = a + (b - a) - 1
            j = max(x for x in range(n_gop) if inicios[x] <= ultimo)
            libera_v = dts0 + CUADRO_V * (indice + j) - ANTICIPO_V
            # audio pendiente que deba ir antes
            while pos_audio < len(audio):
                k, libera_a = soltar_audio()
                # el primer paquete de audio va justo después del primer pack de video
                if libera_a >= libera_v and not (pos_audio == 0 and salida):
                    break
                reloj = reloj + TICS_PACK if not primero else reloj
                reloj = max(reloj, libera_a)
                primero = False
                salida.append(pack_audio(int(reloj + 0.5)))
            reloj = reloj + TICS_PACK if not primero else reloj
            reloj = max(reloj, libera_v)
            primero = False
            if t == 0:
                inicio_gop = len(salida)
            pack_de_trozo.append(len(salida) - inicio_gop)
            p = pes(0xE0, cabeza, datos[a:b])
            if t == 0:
                cuerpo = system + bf + p
            else:
                cuerpo = p
            sobra = PACK - 14 - len(cuerpo)
            if sobra >= 6:
                pk = encabezado_pack(int(reloj + 0.5), orig["mux"]) + cuerpo + relleno_pes(sobra)
            else:
                pk = encabezado_pack(int(reloj + 0.5), orig["mux"], sobra) + cuerpo
            salida.append(pk)
        fin_cuadro = []
        for j in range(4):
            if j < n_gop:
                ultimo = inicios[j] + len(gop[j]) - 1
                t = next(t for t, (a, b, _) in enumerate(trozos) if a <= ultimo < b)
                fin_cuadro.append(pack_de_trozo[t])
            else:
                fin_cuadro.append(0xFFFF)
        primero_gop = bytearray(salida[inicio_gop])
        q = 14 + (primero_gop[13] & 7) + len(system) + 6 + 2
        primero_gop[q:q + 8] = b"".join(struct.pack(">H", x) for x in fin_cuadro)
        salida[inicio_gop] = bytes(primero_gop)
        indice += n_gop
    while pos_audio < len(audio):
        k, libera_a = soltar_audio()
        reloj = max(reloj + TICS_PACK, libera_a)
        salida.append(pack_audio(int(reloj + 0.5)))
    stream = b"".join(salida)
    if tam_objetivo is not None:
        falta = tam_objetivo - HEADER - len(stream)
        if falta < 0:
            raise ValueError(f"el .pmf nuevo mide {-falta} bytes más que el original, baja la tasa de bits")
        while falta >= PACK:
            reloj += TICS_PACK
            stream += encabezado_pack(int(reloj + 0.5), orig["mux"]) + relleno_pes(PACK - 14)
            falta -= PACK
        if falta:
            raise ValueError("el tamaño del original no es múltiplo de 2048")
    enc = bytearray(orig["encabezado"])
    struct.pack_into(">I", enc, 0x0C, len(stream))
    return bytes(enc) + stream


def audio_oma(r):
    """Audio como archivo OMA: encabezado EA3 de 96 bytes y cuadros sin su encabezado."""
    audio = bytes(r["audio"])
    sync = audio[:4]
    h = bytearray(96)
    h[0:3] = b"EA3"
    h[5] = 96
    h[6:8] = b"\xff\xff"          # sin cifrar
    h[32] = 1                     # ATRAC3plus
    h[34:36] = sync[2:4]          # canales, frecuencia y tamaño de cuadro
    cuerpo = []
    for k in range(0, len(audio), CUADRO_A):
        if audio[k:k + 4] != sync:
            raise ValueError(f"cuadro de audio desalineado en {k}")
        cuerpo.append(audio[k + 8:k + CUADRO_A])
    return bytes(h) + b"".join(cuerpo), len(cuerpo)


def main():
    accion, ruta = sys.argv[1], sys.argv[2]
    r = desarmar(open(ruta, "rb").read())
    if accion == "audio":
        oma, n = audio_oma(r)
        open(sys.argv[3], "wb").write(oma)
        print(f"{n} cuadros de audio, {n * MUESTRAS_A / 44100:.1f} s")
    elif accion == "video":
        open(sys.argv[3], "wb").write(bytes(r["video"]))
        print(f"{len(r['cuadros'])} cuadros de video")
    elif accion == "gops":
        inicio, lista = 0, []
        for n in r["gops"]:
            lista.append(inicio)
            inicio += n
        print(" ".join(map(str, lista)))
    elif accion == "armar":
        nuevo = partir_cuadros(open(sys.argv[3], "rb").read())
        tam = len(open(ruta, "rb").read())
        open(sys.argv[4], "wb").write(armar(r, nuevo, tam))
        print(f"armado {sys.argv[4]}")
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
