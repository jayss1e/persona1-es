#!/usr/bin/env python3
"""
eboot_apply.py — Aplica traducciones al EBOOT descifrado por OFFSET (seguro).

Lee el inventario eb.json (registros con off/rawlen/room/term/texto_original) y un
mapa JSON {texto_original: texto_traducido}. Para cada registro cuyo texto este en
el mapa: verifica que los bytes en `off` == encode(texto_original), exige que
encode(es) <= rawlen+room, sobreescribe y rellena:
  - term '00': relleno con espacios 0x0000
  - term 'FF': relleno con 0xFFFF (terminador de nombre); puede usar `room`
Valida que el conteo de tokens <FFxx> coincida.

Uso: python3 tools/eboot_apply.py <in.elf> <out.elf> <eb.json> <trans.json>
"""
import sys, os, json, re
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from persona_codec import encode
from eboot_punteros import destinos

TOK=re.compile(r'<FF[0-9A-Fa-f]{2}>')

# Blobs de MENU/LISTA indexados por OFFSET-FIJO: el engine lee cada entrada a un
# offset de byte fijo (no camina el separador). Si ES cambia longitudes, el offset
# cae a media palabra -> se ve la COLA de la entrada (bug de verbos de negociacion,
# capturas 1-5 del playtest). Regla de deteccion:
#   - <FFF5> presente          -> lista de respuestas de negociacion: alinear por <FFF5>
#   - <FF01> presente SIN <FF02> -> lista/concatenado de strings: alinear por <FF01>
#     (con <FF02> el FF01 es salto de pagina de dialogo secuencial: NO alinear)
#   - <FF03> sin marcadores de dialogo -> menus (tiendas, casino...): alinear por <FF03>
# Si la alineacion falla (un segmento ES excede su EN) se cae al relleno clasico
# de string completo y se reporta SEG_OVERFLOW (arreglar acortando ese segmento).
# (Antes habia una lista MENU_FF03 de ids de pedazos de menus de casino. Desde que
# eboot_strings2.py trata <8140> como espacio, esos menus salen completos en un solo
# registro y la regla general de <FF03> los cubre; los ids viejos ya no existen.)

def _seps_for(rec_id, en):
    """Tokens ANCLA del record. <FF0E> SIEMPRE (opcode abrir-menu: su argumento
    <NNNN> va inmediatamente despues, fuera del footprint; si FF0E se desplaza,
    el engine lee arg=0 -> menu generico Yes/No en vez del menu correcto)."""
    seps=set()
    if '<FF0E>' in en: seps.add('<FF0E>')
    if '<FFF5>' in en: seps.add('<FFF5>')
    if '<FF01>' in en and '<FF02>' not in en: seps.add('<FF01>')
    # cadenas de menu FF03 (opciones indexadas por offset: tiendas Buy/Sell/Equip,
    # maquinas de casino, Velvet, pantalla de analisis Element/Weak...): son records
    # SIN marcadores de dialogo. Con FF02/FF1B/FF0E es dialogo secuencial: no anclar.
    if '<FF03>' in en and not any(x in en for x in ('<FF02>','<FF1B>','<FF0E>','<FFF5>','<FF01>')):
        seps.add('<FF03>')
    return seps

def _align_seg(en, es, be_len, seps):
    """Padea cada tramo entre tokens-ancla (`seps`) a la long-bytes de su tramo EN
    -> cada ancla queda en su offset ABSOLUTO original. None si la secuencia de
    anclas difiere, un tramo ES excede su EN, o el total no cuadra."""
    import re as _re
    pat='|'.join(_re.escape(s) for s in sorted(seps))
    ens=_re.split(f'({pat})',en); ess=_re.split(f'({pat})',es)
    if len(ens)!=len(ess): return None
    out=bytearray()
    for a,b in zip(ens,ess):
        if a in seps:
            if b!=a: return None
            out+=encode(a)
            continue
        ba=encode(a); bb=encode(b)
        if len(bb)>len(ba): return None
        out+=bb + b'\x00\x00'*((len(ba)-len(bb))//2)   # relleno espacios al final del tramo
    return bytes(out) if len(out)==be_len else None

# ANCLAS (2026-10-28, bugs 20 y 21 de notas/bugs.md). Dos cosas que el relleno al
# final del texto rompia:
#   - Muchos records juntan varios mensajes y el juego entra a cada uno por un
#     puntero que apunta tras su <FF02><FF01>. Si el español es mas corto, ese
#     puntero cae en otro lado ("omo diablos..." sin la etiqueta de Mark).
#   - <FF05> y <FF18> llevan un parametro en la palabra siguiente, que puede quedar
#     fuera del record. Igual que con <FF0E>, el codigo tiene que quedar en su lugar
#     o el juego lee el relleno como parametro y dibuja basura ("vengo...   ?").
# _align_anclas deja en su posicion original cada destino de puntero y cada uno de
# esos codigos cuando cierra el record, y rellena cada tramo antes de su cola de codigos y espacios, asi
# el relleno sale como espacios al final de una linea, que no se ven.
TOKEN = re.compile(r'(<[0-9A-Fa-f]{4}>)')
CON_PARAMETRO = ('<FF05>', '<FF18>', '<FF0E>')
# Codigos que cierran una linea, pagina o mensaje: el relleno va antes de ellos.
# Los demas (<FF07> nombre del protagonista, <FF1B> hablante...) se ven, asi que
# el relleno nunca va antes de uno de esos.
COLA = ('<FF01>', '<FF02>', '<FF03>', '<FF04>', '<FF05>', '<FF18>', '<FF0E>', '<FFF5>')


def _unidades(texto):
    """Texto -> lista de unidades u16: cada letra o token <XXXX> es una."""
    out = []
    for p in TOKEN.split(texto):
        out.extend([p] if TOKEN.fullmatch(p) else list(p))
    return out


def _align_anclas(en, es, be_len, seps, refs):
    """Como _align_seg, pero tambien ancla los destinos de puntero `refs` (en
    unidades desde el inicio del record) y los codigos de CON_PARAMETRO. None si
    algun tramo del español no cabe en el suyo."""
    ue, us = _unidades(en), _unidades(es)
    te = [i for i, u in enumerate(ue) if TOK.fullmatch(u)]
    ts = [i for i, u in enumerate(us) if TOK.fullmatch(u)]
    if [ue[i] for i in te] != [us[i] for i in ts]:
        return None
    anclas = {0: 0, len(ue): len(us)}
    for j, i in enumerate(te):
        # El parametro de un codigo a media cadena viaja con el; solo el del
        # ultimo codigo queda fuera del record.
        if (ue[i] in CON_PARAMETRO and i == len(ue) - 1) or ue[i] in seps:
            anclas[i] = ts[j]
        if ue[i] in seps:
            anclas[i + 1] = ts[j] + 1
    for k in refs:
        previos = [j for j, i in enumerate(te) if i < k]
        if not previos:
            return None
        j = previos[-1]
        d = k - te[j] - 1                     # unidades entre el codigo y el destino
        if ue[te[j] + 1:k] != us[ts[j] + 1:ts[j] + 1 + d]:
            return None
        anclas[k] = ts[j] + 1 + d
    pares = sorted(anclas.items())
    if any(b1 < b0 for (_, b0), (_, b1) in zip(pares, pares[1:])):
        return None
    out = []
    for (a0, b0), (a1, b1) in zip(pares, pares[1:]):
        tramo, largo = us[b0:b1], a1 - a0
        if len(tramo) > largo:
            return None
        corte = len(tramo)
        while corte and (tramo[corte - 1] in COLA or tramo[corte - 1] == ' '):
            corte -= 1
        out += tramo[:corte] + [' '] * (largo - len(tramo)) + tramo[corte:]
    bs = encode(''.join(out))
    return bs if len(bs) == be_len else None


def anclas_de(d, dst, off, en):
    """Destinos de puntero dentro del record, en unidades desde su inicio."""
    n = len(encode(en)) // 2
    return [k for k in range(1, n) if off + 2 * k in dst]


def necesita_anclas(d, dst, off, en):
    refs = anclas_de(d, dst, off, en)
    return refs, bool(refs) or en.endswith(CON_PARAMETRO)


def colocar(r, en, es, d, dst):
    """Bytes que se escriben en el footprint del record r, con su lista de avisos.
    Primero intenta las anclas, luego los tramos de _seps_for y si nada cuadra
    rellena al final como antes. es ya debe caber completo y tener los mismos
    codigos <FFxx> que en."""
    be, bs = encode(en), encode(es)
    seps = _seps_for(r['id'], en)
    span, avisos = None, []
    refs, anclar = necesita_anclas(d, dst, r['off'], en)
    if anclar:
        span = _align_anclas(en, es, len(be), seps, refs)
        if span is None:
            avisos.append(('ANCLA', r['id'], en[:60]))
    if span is None and seps:
        span = _align_seg(en, es, len(be), seps)   # alineacion por tramos-ancla
        if span is None:
            avisos.append(('SEG_OVERFLOW', sorted(seps), en[:60]))
    if span is None:
        pad = b'\xff\xff' if r['term'] == 'FF' else b'\x00\x00'
        span = bytearray(bs)
        while len(span) < len(be): span += pad   # rellena solo el footprint del string original
    return bytes(span), avisos


def main():
    inp,outp,ebj,trj=sys.argv[1:5]
    d=bytearray(open(inp,'rb').read())
    dst=destinos(bytes(d))
    recs=json.load(open(ebj))
    tr=json.load(open(trj))
    # cap minimo por texto (todo-o-nada: ES debe caber en TODAS sus apariciones)
    mincap={}
    for r in recs:
        c=r['rawlen']+(r['room'] if r['term']=='FF' else 0)
        mincap[r['texto_original']]=min(mincap.get(r['texto_original'],10**9),c)
    ok=skip=mism=toolong=tokbad=0; errs=[]; toolong_set=set()
    for r in recs:
        en=r['texto_original']
        if en not in tr: continue
        es=tr[en]
        if es is None or es=='' or es==en:
            skip+=1; continue
        try:
            be=encode(en); bs=encode(es)
        except Exception as ex:
            errs.append(('ENC',en,str(ex))); continue
        off=r['off']
        if bytes(d[off:off+len(be)])!=be:
            mism+=1; errs.append(('MISMATCH',en,hex(off))); continue
        # SEGURIDAD: solo se puede sobrescribir el FOOTPRINT EXACTO del string
        # original (len(be)). rawlen suele ser MAYOR que el string real -> los bytes
        # [len(be)..rawlen] son OTRAS strings/estructura que el juego lee (render, etc).
        # Rellenar hasta rawlen los borraba (bug del descuadre). Ahora: es debe caber
        # en len(be); se rellena SOLO hasta len(be) y NO se toca nada despues.
        if len(bs)>len(be):
            if en not in toolong_set:
                toolong+=1; toolong_set.add(en); errs.append(('TOOLONG',en,es,len(bs),len(be)))
            continue
        if TOK.findall(en)!=TOK.findall(es):
            tokbad+=1; errs.append(('TOK',en,es)); continue
        span,avisos=colocar(r, en, es, d, dst)
        errs+=avisos
        assert len(span)==len(be)
        d[off:off+len(span)]=span
        ok+=1
    open(outp,'wb').write(d)
    print('aplicadas=%d  skip=%d  mismatch=%d  toolong=%d  tokbad=%d'%(ok,skip,mism,toolong,tokbad))
    print('sin anclar: %d'%sum(1 for e in errs if e[0]=='ANCLA'))
    for e in errs[:15]: print('  ',e)

if __name__=='__main__': main()
