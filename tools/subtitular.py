#!/usr/bin/env python3
"""
subtitular.py — Pega los subtítulos de subtitulos/es/*.srt en las cinemáticas
de una ISO de Persona y los escribe en su lugar, sin mover nada más.

Por cada MVxx.srt:
  1. Lee PSP_GAME/USRDIR/MOVIE/MVxx.pmf de la ISO.
  2. Recodifica el video con los subtítulos quemados (ffmpeg + libx264, dos
     pasadas), con las mismas características que el original: H.264 Main 2.1,
     sin cuadros B, una referencia, delimitadores de unidad y un cuadro clave
     en cada inicio de GOP del original. Ver notas/subtitulos.md.
  3. Lo arma con tools/pmf.py, con el audio y el encabezado originales, al
     mismo tamaño que el original, y lo escribe encima en la ISO.

Si un video ya trae subtítulos (no coincide con el original) se avisa y se
deja como está. Los videos recodificados se guardan en rebuilt/videos/ con
una clave del original, del .srt y de la receta, así que solo se vuelve a
codificar lo que cambió.

Uso:
  python3 tools/subtitular.py ISO            modifica la ISO en su lugar
  python3 tools/subtitular.py ISO MV12 MV13  solo esos videos

Necesita ffmpeg con libx264 y libass, y Noto Sans en externos/fuentes/ (o en
la carpeta que diga FUENTES_ES).
"""
import hashlib
import os
import subprocess
import sys
import tempfile

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
sys.path.insert(0, AQUI)
import pmf                      # noqa: E402
from port_usa import listar_iso  # noqa: E402

SRT = os.path.join(RAIZ, "subtitulos", "es")
CACHE = os.path.join(RAIZ, "rebuilt", "videos")
FUENTES = os.environ.get("FUENTES_ES",
                         os.path.join(RAIZ, "..", "externos", "fuentes"))

ESTILO = "FontName=Noto Sans,FontSize=15,Bold=1,Outline=1.5,Shadow=0,MarginV=8"
X264 = ("aud=1:nal-hrd=vbr:bframes=0:ref=1:scenecut=0:keyint=600:min-keyint=1:"
        "weightp=0:forced-idr=1")
# Fracciones de la tasa original que se prueban, de mayor a menor, hasta que
# el video armado quepa en el tamaño del original.
TASAS = (0.93, 0.88, 0.82, 0.75)
RECETA = f"{ESTILO}|{X264}|{TASAS}|1"


def codificar(orig_pmf, srt, r, fraccion, tmp):
    """Recodifica el video de orig_pmf con los subtítulos de srt. Devuelve el H.264."""
    n = len(r["cuadros"])
    segundos = n * pmf.CUADRO_V / 90000
    kbps = int(len(r["video"]) * 8 / segundos / 1000 * fraccion)
    inicios, k = [], 0
    for g in r["gops"]:
        inicios.append(k)
        k += g
    claves = "expr:" + "+".join(f"eq(n,{i})" for i in inicios)
    ruta_srt = srt.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    filtro = f"subtitles={ruta_srt}:fontsdir={FUENTES}:force_style='{ESTILO}'"
    salida = os.path.join(tmp, "nuevo.h264")
    stats = os.path.join(tmp, "x264.stats")
    for paso in (1, 2):
        cmd = ["ffmpeg", "-v", "error", "-y", "-i", orig_pmf, "-an",
               "-fps_mode", "passthrough", "-vf", filtro,
               "-c:v", "libx264", "-preset", "slow", "-profile:v", "main",
               "-level", "2.1", "-b:v", f"{kbps}k", "-maxrate", "4000k",
               "-bufsize", "500k", "-force_key_frames", claves,
               "-x264-params", f"{X264}:pass={paso}:stats={stats}",
               "-f", "h264", salida if paso == 2 else os.devnull]
        subprocess.run(cmd, check=True)
    es = open(salida, "rb").read()
    cuadros = pmf.partir_cuadros(es)
    if len(cuadros) != n:
        raise SystemExit(f"{srt}: salieron {len(cuadros)} cuadros y el original tiene {n}")
    return cuadros


def subtitular(orig, srt, nombre):
    """Devuelve el .pmf nuevo, del mismo tamaño que orig."""
    clave = hashlib.md5(orig + open(srt, "rb").read() + RECETA.encode()).hexdigest()[:12]
    os.makedirs(CACHE, exist_ok=True)
    guardado = os.path.join(CACHE, f"{nombre}_{clave}.pmf")
    if os.path.exists(guardado):
        return open(guardado, "rb").read()
    r = pmf.desarmar(orig)
    with tempfile.TemporaryDirectory() as tmp:
        ruta = os.path.join(tmp, nombre + ".pmf")
        open(ruta, "wb").write(orig)
        for fraccion in TASAS:
            cuadros = codificar(ruta, srt, r, fraccion, tmp)
            if max(len(c) for c in cuadros) > 0xFFFF:
                print(f"  {nombre}: un cuadro pasa de 65535 bytes, bajo la tasa")
                continue
            nuevo = pmf.armar(r, cuadros, len(orig))
            if len(nuevo) == len(orig):
                break
            print(f"  {nombre}: no cupo al {fraccion:.0%}, bajo la tasa")
        else:
            raise SystemExit(f"{nombre}: no cupo en el tamaño del original")
    for f in os.listdir(CACHE):          # borra versiones viejas de este video
        if f.startswith(nombre + "_"):
            os.remove(os.path.join(CACHE, f))
    open(guardado, "wb").write(nuevo)
    return nuevo


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    iso = sys.argv[1]
    pedidos = set(sys.argv[2:])
    archivos = {os.path.basename(k).upper(): v for k, v in listar_iso(iso).items()
                if "/MOVIE/" in k.upper()}
    srts = sorted(f for f in os.listdir(SRT) if f.endswith(".srt"))
    hechos = 0
    with open(iso, "r+b") as f:
        for s in srts:
            nombre = s[:-4]
            if pedidos and nombre not in pedidos:
                continue
            inicio, tam = archivos[nombre.upper() + ".PMF"]
            f.seek(inicio)
            orig = f.read(tam)
            # Para saber si ya tiene subtítulos, compara con lo guardado en caché.
            if any(open(os.path.join(CACHE, c), "rb").read() == orig
                   for c in (os.listdir(CACHE) if os.path.isdir(CACHE) else [])
                   if c.startswith(nombre + "_")):
                print(f"  {nombre}: ya tenía subtítulos")
                hechos += 1
                continue
            nuevo = subtitular(orig, os.path.join(SRT, s), nombre)
            f.seek(inicio)
            f.write(nuevo)
            hechos += 1
            print(f"  {nombre}: listo")
    print(f"Videos con subtítulos: {hechos}")


if __name__ == "__main__":
    main()
