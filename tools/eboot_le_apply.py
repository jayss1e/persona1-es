#!/usr/bin/env python3
"""
eboot_le_apply.py — Aplica _eboot/trans_le.json sobre el EBOOT traducido.

El EBOOT contiene un corpus de UI en u16 LITTLE-ENDIAN (invisible para el
extractor BE): Yes/No de tiendas, prompts de compra/venta, menus de batalla,
nombres de mapa, tablas de estados/elementos/razas, titulos de capitulo, etc.
Cada entrada de trans_le.json: offset -> {en, es, budget}. Se verifica que el
EN siga en su sitio y se escribe el ES codificado LE, relleno con 0x0000.
budget se cuenta en caracteres (u16); un token <XXXX> cuenta como uno.

OJO con los espacios: el codec escribe " " como 0x0000, y en las etiquetas de
interfaz 0x0000 TERMINA el texto. Ahi los espacios del ingles son <8140> (el
espacio ancho de Shift-JIS) y en el ES hay que escribirlos igual
("Pon<8140>tu<8140>nombre."). Un " " en el EN de una etiqueta separa dos textos
distintos ("Enter Nickname" = "Enter" del boton X y "Nickname"): cada palabra ES
debe empezar en la misma posicion que la EN, porque el juego las lee por separado.
En los mensajes largos (p. ej. "Retry saving game data?") 0x0000 si es espacio.

Uso: python3 tools/eboot_le_apply.py  (tras eboot_apply.py; edita
     _eboot/eboot_translated.elf in place)
"""
import os, re, sys, json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from persona_codec import char_to_code

ELF = os.path.join(ROOT, "_eboot", "eboot_translated.elf")
TAB = os.path.join(ROOT, "_eboot", "trans_le.json")
TOKEN = re.compile(r"(<[0-9A-Fa-f]{4}>)")


def codigos(text):
    """Texto con tokens <XXXX> -> lista de u16."""
    out = []
    for p in TOKEN.split(text):
        if TOKEN.fullmatch(p):
            out.append(int(p[1:-1], 16))
        else:
            out.extend(char_to_code(ch) for ch in p)
    return out


def main():
    d = bytearray(open(ELF, "rb").read())
    tab = json.load(open(TAB))
    ok = mism = toolong = 0
    for soff, e in tab.items():
        off = int(soff)
        en, es, budget = e["en"], e["es"], e["budget"]
        # verificar EN (o que ya este aplicado el ES: re-ejecucion idempotente)
        cur = [d[off + 2 * k] | (d[off + 2 * k + 1] << 8) for k in range(budget)]
        c_en, c_es = codigos(en), codigos(es)
        relleno = lambda c: c + [0] * (budget - len(c))
        if not (cur[:len(c_en)] == c_en or cur == relleno(c_es)):
            mism += 1
            print(f"  MISMATCH @{off:#x}: {cur[:10]} != {en!r}")
            continue
        if len(c_es) > budget:
            toolong += 1
            continue
        for k, w in enumerate(relleno(c_es)):
            d[off + 2 * k] = w & 0xFF
            d[off + 2 * k + 1] = (w >> 8) & 0xFF
        ok += 1
    open(ELF, "wb").write(d)
    print(f"LE aplicadas={ok} mismatch={mism} toolong={toolong}")


if __name__ == "__main__":
    main()
