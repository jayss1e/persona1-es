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
from persona_codec import encode

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

def main():
    inp,outp,ebj,trj=sys.argv[1:5]
    d=bytearray(open(inp,'rb').read())
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
        seps=_seps_for(r['id'], en)
        span=None
        if seps:
            span=_align_seg(en, es, len(be), seps)   # alineacion por tramos-ancla
            if span is None:
                errs.append(('SEG_OVERFLOW',sorted(seps),en[:60]))
        if span is None:
            pad = b'\xff\xff' if r['term']=='FF' else b'\x00\x00'
            span=bytearray(bs)
            while len(span)<len(be): span+=pad   # rellena solo el footprint del string original
        assert len(span)==len(be)
        d[off:off+len(span)]=span
        ok+=1
    open(outp,'wb').write(d)
    print('aplicadas=%d  skip=%d  mismatch=%d  toolong=%d  tokbad=%d'%(ok,skip,mism,toolong,tokbad))
    for e in errs[:15]: print('  ',e)

if __name__=='__main__': main()
