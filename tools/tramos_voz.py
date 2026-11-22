#!/usr/bin/env python3
"""
tramos_voz.py — Encuentra los tramos con voz de un audio y transcribe cada uno
con whisper, para poner los subtítulos en su tiempo real.

whisper solo da tiempos aproximados (la primera frase siempre "empieza" en 0 y
las frases cortas salen redondeadas). Aquí se mide la energía en la banda de la
voz (300-3400 Hz) cada 50 ms, se marcan las ventanas que pasan un umbral
relativo entre el piso de ruido y el máximo, se juntan los huecos de menos de
0.35 s y cada tramo se transcribe por separado.

Uso:
  python3 tools/tramos_voz.py AUDIO.wav [UMBRAL]

UMBRAL va de 0 a 1 (0.45 por defecto). Más alto marca más tramos; si la música
de fondo los junta en uno largo, bajarlo (0.25 o 0.15). Imprime
"inicio fin |texto" por tramo. El .wav sale de tools/pmf.py audio y
ffmpeg -ac 1 -ar 16000.

Necesita whisper.cpp en externos/whisper.cpp (o en WHISPER_CPP) con el modelo
ggml-small.en.bin. Ver notas/subtitulos.md.
"""
import os
import subprocess
import sys
import tempfile

import numpy as np

WHISPER = os.environ.get("WHISPER_CPP", os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "externos", "whisper.cpp"))
VENTANA = 800          # 50 ms a 16 kHz


def tramos(wav, umbral):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", wav, "-af", "highpass=300,lowpass=3400",
                          "-f", "s16le", "-ac", "1", "-ar", "16000", "-"],
                         capture_output=True, check=True).stdout
    x = np.frombuffer(raw, np.int16).astype(float)
    x = x[:len(x) // VENTANA * VENTANA].reshape(-1, VENTANA)
    e = 20 * np.log10(np.sqrt(np.mean(x ** 2, axis=1)) + 1)
    alto, piso = np.percentile(e, 95), np.percentile(e, 10)
    voz = e > piso + (alto - piso) * (1 - umbral)
    segs, i = [], 0
    while i < len(voz):
        if voz[i]:
            j = i
            while j < len(voz) and voz[j]:
                j += 1
            if segs and i - segs[-1][1] < 7:      # hueco de menos de 0.35 s
                segs[-1][1] = j
            else:
                segs.append([i, j])
            i = j
        else:
            i += 1
    return [(a * 0.05, b * 0.05) for a, b in segs if b - a >= 3]


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    wav = sys.argv[1]
    umbral = float(sys.argv[2]) if len(sys.argv) > 2 else 0.45
    cli = os.path.join(WHISPER, "build", "bin", "whisper-cli")
    modelo = os.path.join(WHISPER, "models", "ggml-small.en.bin")
    with tempfile.TemporaryDirectory() as tmp:
        corte = os.path.join(tmp, "t.wav")
        for a, b in tramos(wav, umbral):
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(max(0, a - 0.2)),
                            "-to", str(b + 0.3), "-i", wav, "-ar", "16000", "-ac", "1", corte],
                           check=True)
            texto = subprocess.run([cli, "-m", modelo, "-f", corte, "-nt", "-np"],
                                   capture_output=True, text=True).stdout
            print(f"{a:7.2f} {b:7.2f} |{' '.join(texto.split())}")


if __name__ == "__main__":
    main()
