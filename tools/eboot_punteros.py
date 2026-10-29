#!/usr/bin/env python3
"""
eboot_punteros.py — Direcciones del EBOOT descifrado a las que apunta algún puntero.

Las relocaciones del ELF dicen qué palabras del código o de los datos guardan una
dirección. Si una apunta a media cadena de texto, el juego empieza a leer ahí: la
traducción tiene que empezar en esa misma posición. Lo usan eboot_apply.py y
eboot_le_lineas.py.
"""
import struct

ELF_DESP = 0xC0          # desplazamiento de archivo = vaddr + 0xC0 en ambos segmentos
SEGMENTOS = (0x0, 0x263900)


def destinos(elf):
    """Conjunto de desplazamientos de archivo a los que apunta alguna relocación."""
    shoff, = struct.unpack_from("<I", elf, 0x20)
    shnum, = struct.unpack_from("<H", elf, 0x30)
    out = set()
    for k in range(shnum):
        _, typ, _, _, off, size = struct.unpack_from("<IIIIII", elf, shoff + 40 * k)
        if typ != 0x700000A0:
            continue
        hi = None
        for p in range(off, off + size, 8):
            r_off, info = struct.unpack_from("<II", elf, p)
            tipo, base_ofs, base_dst = info & 0xFF, (info >> 8) & 0xFF, (info >> 16) & 0xFF
            if base_ofs > 1 or base_dst > 1:
                continue
            donde = r_off + SEGMENTOS[base_ofs] + ELF_DESP
            w, = struct.unpack_from("<I", elf, donde)
            if tipo == 2:                                  # puntero de 32 bits
                out.add(w + SEGMENTOS[base_dst] + ELF_DESP)
            elif tipo == 5:                                # HI16 (lui)
                hi = w & 0xFFFF
            elif tipo == 6 and hi is not None:             # LO16 tras su HI16
                lo = w & 0xFFFF
                lo -= 0x10000 if lo & 0x8000 else 0
                out.add((hi << 16) + lo + SEGMENTOS[base_dst] + ELF_DESP)
    return out
