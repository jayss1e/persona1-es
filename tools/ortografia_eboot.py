#!/usr/bin/env python3
"""
ortografia_eboot.py — La pasada de tools/ortografia.py, ahora sobre los textos del
EBOOT: acentos, eñe y signos de apertura (¿ ¡).

La traducción del EBOOT se escribió sin acentos, porque la fuente no los tenía, y
ortografia.py solo corrió sobre translated_text/. Aquí se corrigen las tres fuentes
de los textos del EBOOT que se codifican en big-endian:
  - _eboot/trans_records.json  {inglés: español}
  - _eboot/trans_8140.json     {inglés: español o lista de opciones}
  - _eboot/trans_nombres.tsv   inglés <TAB> español
No se toca _eboot/trans_le.json: esos textos salen en etiquetas y mapas, y algunas
de sus fuentes no tienen las letras nuevas (bug 17 de notas/bugs.md).

Cada texto se corrige con ortografia.corregir(). Los acentos no cambian el largo,
pero cada ¿ o ¡ ocupa un carácter más, así que solo se dejan si el texto sigue
cabiendo con las mismas reglas que usan eboot_apply.py (su función colocar(), con
las anclas de los punteros) y eboot_menus_8140.py. Si no, el texto se queda solo
con los acentos.

Tampoco se tocan los letreros de lugar ("Salon vacio 1F<FF01>", ver LETRERO): salen
arriba a la izquierda con otra letra, y no se ha probado si esa letra tiene acentos.

Las palabras de PROTEGIDAS no se tocan nunca: son nombres propios que la lista de
frecuencias confunde con palabras comunes ("Mana" -> "Maná").

Uso: python3 tools/ortografia_eboot.py --dicc es_50k.txt revisar   (lista los cambios)
     python3 tools/ortografia_eboot.py --dicc es_50k.txt aplicar
La lista de frecuencias se descarga como dice ortografia.py.
"""
import collections, json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from persona_codec import encode
from ortografia import Diccionario, PALABRA, corregir
from eboot_apply import TOK, colocar, destinos
from eboot_menus_8140 import check_fit, to_codec

RECORDS = os.path.join(ROOT, "_eboot", "trans_records.json")
INVENTARIO = os.path.join(ROOT, "_eboot", "eb_inventory.json")
ELF = os.path.join(ROOT, "_eboot", "eboot_dec.elf")
MENUS = os.path.join(ROOT, "_eboot", "trans_8140.json")
NOMBRES = os.path.join(ROOT, "_eboot", "trans_nombres.tsv")
PROTEGIDAS = {"Mana"}
LETRERO = re.compile(r"[^<.?!]+<FF01>")
# Errores de las reglas de contexto en textos del EBOOT, revisados a mano: el botón
# va en un código aparte ("presiona el <botón>") y "tu" va antes de un nombre.
ARREGLOS = [(re.compile(r"\b(presiona|Presiona|pulsa|Pulsa) él\b"), r"\1 el"),
            (re.compile(r"\bde tú (?=Memory)"), "de tu ")]


def proteger(viejo, nuevo):
    """Devuelve nuevo con las palabras de PROTEGIDAS como estaban en viejo."""
    a, b = PALABRA.findall(viejo), PALABRA.findall(nuevo)
    if len(a) != len(b):
        return nuevo
    for w_viejo, w_nuevo in zip(a, b):
        if w_viejo in PROTEGIDAS and w_viejo != w_nuevo:
            nuevo = nuevo.replace(w_nuevo, w_viejo, 1)
    return nuevo


class Colocacion:
    """Comprueba textos con colocar() de eboot_apply.py en todas las apariciones de
    su inglés en el EBOOT."""
    def __init__(self):
        self.d = open(ELF, "rb").read()
        self.dst = destinos(self.d)
        self.por_texto = {}
        for r in json.load(open(INVENTARIO, encoding="utf-8")):
            self.por_texto.setdefault(r["texto_original"], []).append(r)

    def avisos(self, en, es):
        """None si no cabe completo o cambian los códigos, si no la lista de avisos."""
        be, bs = encode(en), encode(es)
        if len(bs) > len(be) or TOK.findall(en) != TOK.findall(es):
            return None
        return [a for r in self.por_texto.get(en, []) for a in colocar(r, en, es, self.d, self.dst)[1]]

    def cabe(self, en, viejo, nuevo):
        """El texto nuevo no puede dar avisos que el viejo no daba."""
        a_nuevo = self.avisos(en, nuevo)
        return a_nuevo is not None and len(a_nuevo) <= len(self.avisos(en, viejo) or [])


def corregir_con(texto, dicc, cabe):
    """Corrige con signos si cabe, si no solo con acentos."""
    for signos in (True, False):
        nuevo = proteger(texto, corregir(texto, 10 ** 6, dicc, signos))
        for patron, cambio in ARREGLOS:
            nuevo = patron.sub(cambio, nuevo)
        if cabe(nuevo):
            return nuevo
    return texto


def pasada(dicc):
    """Devuelve (records, menus, lineas de nombres, cambios)."""
    cambios, col = [], Colocacion()
    records = json.load(open(RECORDS, encoding="utf-8"))
    for en, es in records.items():
        if not es or es == en or LETRERO.fullmatch(en):
            continue
        cabe = lambda t, en=en, es=es: col.cabe(en, es, t)
        nuevo = corregir_con(es, dicc, cabe)
        if nuevo != es:
            cambios.append(("records", es, nuevo))
            records[en] = nuevo

    menus = json.load(open(MENUS, encoding="utf-8"))
    for en, val in menus.items():
        if en.startswith("_"):
            continue
        partes = val if isinstance(val, list) else [val]
        for i, parte in enumerate(partes):
            def cabe(t, i=i):
                prueba = to_codec("<FF03>".join(partes[:i] + [t] + partes[i + 1:]))
                actual = to_codec("<FF03>".join(partes))
                return not check_fit("", en, prueba) and col.cabe(en, actual, prueba)
            nuevo = corregir_con(parte, dicc, cabe)
            if nuevo != parte:
                cambios.append(("menus", parte, nuevo))
                partes[i] = nuevo
        menus[en] = partes if isinstance(val, list) else partes[0]

    lineas = open(NOMBRES, encoding="utf-8").read().split("\n")
    for k, linea in enumerate(lineas):
        if not linea or linea.startswith("#"):
            continue
        en, es = linea.split("\t")
        nuevo = corregir_con(es, dicc, lambda t, en=en: len(encode(t)) <= len(encode(en)))
        if nuevo != es:
            cambios.append(("nombres", es, nuevo))
            lineas[k] = en + "\t" + nuevo
    return records, menus, lineas, cambios


def main():
    args = sys.argv[1:]
    if len(args) != 3 or args[0] != "--dicc" or args[2] not in ("revisar", "aplicar"):
        sys.exit(__doc__)
    records, menus, lineas, cambios = pasada(Diccionario(args[1]))
    palabras = collections.Counter()
    for _, viejo, nuevo in cambios:
        a, b = PALABRA.findall(viejo), PALABRA.findall(nuevo)
        palabras.update(f"{x} -> {y}" for x, y in zip(a, b) if x != y)
    if args[2] == "revisar":
        for par, n in sorted(palabras.items(), key=lambda kv: -kv[1]):
            print(f"{n:4d}  {par}")
    else:
        open(RECORDS, "w", encoding="utf-8").write(json.dumps(records, ensure_ascii=False, indent=1))
        texto = open(MENUS, encoding="utf-8").read()
        for en, val in menus.items():
            if en.startswith("_"):
                continue
            viejo = json.load(open(MENUS, encoding="utf-8"))[en]
            if viejo != val:
                # trans_8140.json está formateado a mano: se cambia cada valor en su lugar.
                for a, b in zip(viejo if isinstance(viejo, list) else [viejo],
                                val if isinstance(val, list) else [val]):
                    if a != b:
                        texto = texto.replace(json.dumps(a, ensure_ascii=False),
                                              json.dumps(b, ensure_ascii=False), 1)
        open(MENUS, "w", encoding="utf-8").write(texto)
        open(NOMBRES, "w", encoding="utf-8").write("\n".join(lineas))
    signos = sum(1 for _, a, b in cambios if re.search("[¿¡]", b) and not re.search("[¿¡]", a))
    print(f"textos cambiados: {len(cambios)} (con ¿ ¡ nuevos: {signos})")


if __name__ == "__main__":
    main()
