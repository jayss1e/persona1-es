#!/usr/bin/env python3
"""
ortografia.py — Pasada de ortografia sobre la traduccion: acentos, enie y signos de
apertura (¿ ¡). La traduccion original se escribio sin ellos porque la fuente no los
tenia (ahora si, ver tools/fuente.py).

No cambia el sentido de nada: solo agrega tildes, cambia n por enie y pone ¿ ¡.

ACENTOS
  Con una lista de frecuencias del espanol (FrequencyWords, de subtitulos; no se
  incluye en el repo, ver --dicc) se decide cada palabra sin tilde:
  - Si la forma con tilde es al menos RAZON veces mas comun que la forma sin tilde
    (o esta no existe), se pone: "aqui" -> "aquí", "nino" -> "niño", "ano" -> "año".
  - Las terminadas en -cion / -sion siempre llevan tilde.
  - Las palabras que cambian de sentido con la tilde (que/qué, tu/tú, el/él, si/sí,
    mi/mí, se/sé, esta/está, como/cómo, aun/aún...) NO pasan por la regla anterior,
    sino por reglas de contexto (ver contexto() y preguntas()).
  - Mayusculas y minusculas se conservan ("Aqui" -> "Aquí").

SIGNOS DE APERTURA
  Cada oracion que termina en ? o ! recibe su ¿ o ¡ al inicio. Una oracion empieza
  tras . ! ? ..., tras un cambio de pagina o de hablante (<FF01>, <FF02>, <FF04>,
  la etiqueta <FF1B>Nombre<FF03>) o al inicio del registro. Los saltos de linea
  (<FF03> fuera de etiqueta) no cortan la oracion. "?!" al final abre con "¡¿".
  Cada signo ocupa un caracter mas, asi que solo se agregan si el registro sigue
  cabiendo solo en su espacio original; si no, queda solo con los acentos.

CADENAS
  scripts/reinsert.py junta records vecinos en cadenas para que el relleno no deje
  huecos visibles. Un ¿ o ¡ de mas puede hacer que una cadena ya no se alinee, y
  entonces sus records se parchean por separado y aparecen huecos ("feas     ...
  Perdon"). Esos records van en tools/ortografia_sin_signos.txt (uno por linea):
  llevan acentos pero no signos de apertura. La lista se arma comparando las
  cadenas de rebuilt/patch_records.json antes y despues de la pasada.

NO SE TOCAN
  - Registros con estado "retraducido" (ya se escribieron con ortografia completa).
  - Registros cuyo texto traducido es igual al original (siguen en ingles).

Uso: python3 tools/ortografia.py --dicc es_50k.txt revisar [N]   (muestra N ejemplos)
     python3 tools/ortografia.py --dicc es_50k.txt aplicar
Se aplica sobre la traduccion sin ortografia: para repetirla, primero restaurar
translated_text/ desde git.
La lista: https://github.com/hermitdave/FrequencyWords (content/2018/es/es_50k.txt).
"""
import collections, glob, json, os, random, re, sys, unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from persona_codec import encode
from migrar_puntuacion import json_style

TR = os.path.join(ROOT, "translated_text")
SIN_SIGNOS = os.path.join(ROOT, "tools", "ortografia_sin_signos.txt")
TOKEN = re.compile(r"(<[0-9A-Fa-f]{4}>)")
PALABRA = re.compile(r"[A-Za-zÁÉÍÓÚÑÜáéíóúñü]+")
RAZON = 5
RAZON_PRETERITO = 20
ESTADOS = ("traducido", "shortened")

# Palabras cuya tilde depende del sentido: solo las tocan las reglas de contexto.
AMBIGUAS = {
    "que", "quien", "quienes", "como", "donde", "adonde", "cuando", "cuanto", "cuanta",
    "cuantos", "cuantas", "cual", "cuales", "cuan", "porque", "tu", "el", "si", "mi",
    "se", "te", "de", "mas", "esta", "estas", "este", "esa", "ese", "eso", "esto",
    "aun", "solo", "o", "rio", "practica", "cesar", "ira", "sabana",
}
INTERROGATIVAS = {
    "que": "qué", "quien": "quién", "quienes": "quiénes", "como": "cómo",
    "donde": "dónde", "adonde": "adónde", "cuando": "cuándo", "cuanto": "cuánto",
    "cuanta": "cuánta", "cuantos": "cuántos", "cuantas": "cuántas", "cual": "cuál",
    "cuales": "cuáles", "cuan": "cuán",
}
PREPOSICIONES = {"a", "de", "en", "con", "por", "para", "hacia", "hasta", "desde",
                 "sobre", "sin", "contra", "ante", "entre", "segun", "según"}

# Palabra siguiente que indica "tú" y no el posesivo "tu".
TU_VERBO = {
    "eres", "estas", "estás", "tienes", "sabes", "puedes", "quieres", "vas", "haces",
    "crees", "dices", "piensas", "has", "debes", "eras", "estabas", "tenias", "tenías",
    "podrias", "podrías", "serias", "serías", "seras", "serás", "sabias", "sabías",
    "sientes", "hablas", "ves", "conoces", "necesitas", "deberias", "deberías", "vienes",
    "vives", "mismo", "misma", "solo", "también", "tambien", "y", "no", "lo", "la", "los",
    "las", "te", "me", "nos", "le", "les", "ya", "nunca", "siempre", "sola", "solito",
}
# Palabra siguiente que indica "él" y no el articulo "el".
EL_VERBO = {
    "es", "era", "fue", "será", "sera", "está", "esta", "estaba", "tiene", "tenía",
    "tenia", "no", "y", "o", "también", "tambien", "solo", "lo", "la", "le", "les", "se",
    "me", "te", "nos", "ya", "dijo", "hizo", "sabe", "puede", "quiere", "va", "ha",
    "había", "habia", "podría", "podria", "debe", "nunca", "siempre", "sabía", "sabia",
}
# Palabra siguiente que indica "está" y no el demostrativo "esta".
ESTA_VERBO = {
    "bien", "mal", "aquí", "aqui", "ahí", "ahi", "allí", "alli", "allá", "alla", "en",
    "muy", "tan", "más", "mas", "claro", "loco", "loca", "lleno", "llena", "listo",
    "lista", "listos", "listas", "seguro", "segura", "vivo", "viva", "vivos", "muerto",
    "muerta", "muertos", "solo", "sola", "solos", "cerca", "lejos", "por", "a", "con",
    "bajo", "dentro", "fuera", "todo", "mejor", "peor", "contigo", "conmigo", "raro",
    "rara", "extraño", "extraña", "roto", "rota", "tranquilo", "tranquila", "pasando",
    "haciendo", "lloviendo", "pensando", "esperando", "aquí", "pasa", "detrás", "detras",
    "atrás", "atras", "arriba", "abajo", "adentro", "afuera", "frente", "hecho", "hecha",
    "bastante", "demasiado", "siendo", "como", "igual", "libre", "ocupado", "ocupada",
    "preocupado", "preocupada", "enojado", "enojada", "enfermo", "enferma", "herido",
    "herida", "perdido", "perdida", "seguros", "seguras", "despierto", "despierta",
    "dormido", "dormida", "sucediendo", "ocurriendo", "pasando", "mintiendo",
}
SE_ANTES = {"no", "yo", "ya", "lo", "bien", "tú", "tu"}
SE_DESPUES_NO = {"lo", "la", "los", "las", "le", "les", "me", "te", "nos", "puede",
                 "pueden", "va", "van", "ve", "ven", "ha", "han", "hace", "hacen",
                 "trata", "debe", "deben", "siente", "sienten", "acerca", "acercan",
                 "fue", "fueron", "queda", "quedan", "llama", "llaman", "dice", "dicen",
                 "vuelve", "volvió", "volvio", "ve", "atreve", "atreven"}
SE_DESPUES_SI = {"que", "qué", "si", "como", "cómo", "donde", "dónde", "cuando",
                 "cuándo", "quien", "quién", "nada", "mucho", "bien", "exactamente",
                 "todo", "cual", "cuál", "eso", "esto", "por"}
AUN_NO = {"asi", "así", "cuando", "si", "sin", "con", "siendo", "a", "y", "al", "tras"}
# Tras "sé" la interrogativa indirecta lleva tilde ("no sé cómo"); "que" no ("sé que vienes").
TRAS_SE = {k: v for k, v in INTERROGATIVAS.items() if k != "que"}
MI_DESPUES = {"mismo", "misma", "y", "o", "no", "que", "también", "tambien", "nunca",
              "jamás", "jamas", "tampoco", "solo", "sola"}


def plano(w):
    """Palabra sin tildes (la enie tambien pasa a n)."""
    return "".join(c for c in unicodedata.normalize("NFD", w)
                   if unicodedata.category(c) != "Mn")


def con_mayusculas(nueva, vieja):
    """Copia a nueva el patron de mayusculas de vieja."""
    if vieja.isupper() and len(vieja) > 1:
        return nueva.upper()
    if vieja[:1].isupper():
        return nueva[:1].upper() + nueva[1:]
    return nueva


class Diccionario:
    def __init__(self, ruta):
        self.freq = {}
        for linea in open(ruta, encoding="utf-8"):
            w, f = linea.split()
            self.freq[w] = int(f)
        self.tilde = {}                         # forma plana -> (frecuencia, forma con tilde)
        for w, f in self.freq.items():
            p = plano(w)
            if p != w and f > self.tilde.get(p, (0, ""))[0]:
                self.tilde[p] = (f, w)

    def acentuar(self, w, anterior=None):
        """Forma con tilde de w si es claramente la correcta, si no None. anterior es
        la palabra previa: "yo amenazo" (presente) no pasa a "amenazó" (preterito)."""
        lw = w.lower()
        if lw in AMBIGUAS or lw != plano(lw):
            return None
        if re.search(r"[cs]ion$", lw) and len(lw) > 4:
            return con_mayusculas(lw[:-3] + "ión", w)
        if lw not in self.tilde:
            return None
        f_tilde, forma = self.tilde[lw]
        f_plana = self.freq.get(lw, 0)
        if forma.endswith("ó") and f_plana:            # presente "yo tomo" / preterito "tomó"
            if anterior == "yo" or f_tilde < RAZON_PRETERITO * f_plana:
                return None
        if f_tilde >= RAZON * max(f_plana, 1):
            return con_mayusculas(forma, w)
        return None


# --- texto como lista de piezas: tokens <XXXX> intactos, texto editable ---

def piezas(texto):
    return [p for p in TOKEN.split(texto) if p != ""]


def es_token(p):
    return bool(TOKEN.fullmatch(p))


def palabras(lista):
    """[(i_pieza, inicio, fin, palabra)] de todas las palabras del texto."""
    out = []
    for i, p in enumerate(lista):
        if not es_token(p):
            for m in PALABRA.finditer(p):
                out.append((i, m.start(), m.end(), m.group(0)))
    return out


def cambiar(lista, pos, nueva):
    i, a, z, _ = pos
    lista[i] = lista[i][:a] + nueva + lista[i][z:]


def entre(lista, a, b):
    """Lo que hay entre el fin de la palabra a y el inicio de la palabra b (o hasta el
    final del texto si b es None), como lista de piezas."""
    i, _, z, _ = a
    if b is None:
        return [lista[i][z:]] + lista[i + 1:]
    j, s, _, _ = b
    if i == j:
        return [lista[i][z:s]]
    return [lista[i][z:]] + lista[i + 1:j] + [lista[j][:s]]


def junto(medio):
    """True si entre dos palabras solo hay espacios y saltos de linea (<FF03>): siguen
    en la misma frase aunque cambien de renglon."""
    return all(p.upper() == "<FF03>" if es_token(p) else p.strip() == "" for p in medio)


def cierra(medio, hay_siguiente):
    """True si tras la palabra viene puntuacion o un token que no es salto de linea
    (fin de pagina, de mensaje...), o si no hay mas palabras."""
    for p in medio:
        if es_token(p):
            if p.upper() != "<FF03>":
                return True
            continue
        t = p.lstrip()
        if t:
            return t[0] in ".,;:!?…"
    return not hay_siguiente


def acentos_generales(lista, dicc):
    ps = palabras(lista)
    for k in reversed(range(len(ps))):
        pos = ps[k]
        ant = ps[k - 1][3].lower() if k > 0 else None
        nueva = dicc.acentuar(pos[3], ant)
        if nueva:
            cambiar(lista, pos, nueva)


def contexto(lista):
    """Reglas para tu, el, si, mi, se, esta, estas, aun, mas segun la palabra vecina."""
    ps = palabras(lista)
    for k in reversed(range(len(ps))):
        pos = ps[k]
        w = pos[3]
        lw = w.lower()
        nxt = ps[k + 1] if k + 1 < len(ps) else None
        sig = nxt[3].lower() if nxt and junto(entre(lista, pos, nxt)) else None
        ant = ps[k - 1][3].lower() if k > 0 and junto(entre(lista, ps[k - 1], pos)) else None
        fin = cierra(entre(lista, pos, nxt), nxt is not None)
        nueva = None
        if lw == "mas":
            nueva = "más"
        elif lw == "tu" and (fin or sig in TU_VERBO or (sig and re.search(r"(aste|iste)$", sig))):
            nueva = "tú"
        elif lw == "el" and (fin or sig in EL_VERBO) and ant != "por":
            nueva = "él"
        elif lw == "si" and (fin or sig in ("mismo", "misma", "mismos")):
            nueva = "sí"
        elif lw == "mi" and ant in PREPOSICIONES and (fin or sig in MI_DESPUES):
            nueva = "mí"
        elif lw == "se" and ant in SE_ANTES and (fin or (sig in SE_DESPUES_SI and sig not in SE_DESPUES_NO)):
            nueva = "sé"
        elif lw in ("esta", "estas") and sig and (sig in ESTA_VERBO or re.search(r"(ando|iendo|ado|ada|ido|ida|ados|adas|idos|idas)$", sig)):
            nueva = "estás" if lw == "estas" else "está"
        elif lw == "aun" and sig not in AUN_NO:
            nueva = "aún"
        elif lw in TRAS_SE and ant in ("sé", "se") and k > 1 and ps[k - 2][3].lower() in SE_ANTES:
            nueva = TRAS_SE[lw]
        if nueva:
            cambiar(lista, pos, con_mayusculas(nueva, w))


# --- oraciones y signos de apertura ---

FIN_ORACION = re.compile(r"(\.\.\.|…|[.!?])")
ABREVIATURA = re.compile(r"(?:Sr|Sra|Srta|Dr|Dra|St|Prof)$")
# Cortan la oracion: cambio de pagina y los *efectos de sonido* (<0056> es el asterisco).
CORTE = {"<FF01>", "<FF02>", "<FF04>", "<0056>"}


def oraciones(lista):
    """[(inicio, fin, final)] por oracion terminada en ? o !: inicio = (pieza, char)
    donde empieza, final = signos de cierre ("?", "!", "!?", "?!")."""
    out, ini = [], None
    k = 0
    etiqueta = False
    while k < len(lista):
        p = lista[k]
        if es_token(p):
            u = p.upper()
            if u == "<FF1B>":
                etiqueta = True
            elif u == "<FF03>" and etiqueta:
                etiqueta, ini = False, None
            elif u in CORTE:
                ini = None
            k += 1
            continue
        if etiqueta:
            k += 1
            continue
        j = 0
        while j < len(p):
            if ini is None:
                if p[j].isspace():
                    j += 1
                    continue
                ini = (k, j)
            m = FIN_ORACION.match(p, j)
            if m and m.group(0) == "." and ABREVIATURA.search(p[:j]):
                m = None
            if m:
                z = m.end()
                while z < len(p) and p[z] in "!?":
                    z += 1
                cierre = p[m.start():z]
                if "?" in cierre or "!" in cierre:
                    out.append((ini, cierre.replace(".", "")))
                ini = None
                j = z
            else:
                j += 1
        k += 1
    return out


def apertura(cierre):
    """"?" -> "¿", "!?" -> "¿¡" (orden espejo del cierre, sin repetir)."""
    out = []
    for c in reversed(cierre):
        s = "¿" if c == "?" else "¡"
        if s not in out:
            out.append(s)
    return "".join(out)


def preguntas(lista, con_signos):
    """Tilde en la interrogativa que abre cada pregunta o exclamacion y, si
    con_signos, el ¿ ¡ de apertura. Devuelve los signos agregados."""
    agregados = 0
    for (k, j), cierre in reversed(oraciones(lista)):
        p = lista[k]
        resto = p[j:]
        # primeras dos palabras, tras un conector opcional ("Y que...") y con
        # tartamudeo opcional ("Q-Que")
        m = re.match(r"(?:(?:y|pero|entonces|bueno|oye|eh|ah)[,]?\s+)?(?:\w-)?(\w+)(?:\s+(?:\w-)?(\w+))?",
                     resto, re.I)
        if m:
            w1, w2 = m.group(1), m.group(2)
            g = None
            if w1.lower() in INTERROGATIVAS:
                g, nueva = 1, INTERROGATIVAS[w1.lower()]
            elif w2 and w1.lower() in PREPOSICIONES and w2.lower() in INTERROGATIVAS:
                g, nueva = 2, INTERROGATIVAS[w2.lower()]
            elif w1.lower() == "porque" and "?" in cierre:
                g, nueva = 1, "por qué"
            if g:
                a, w = j + m.start(g), m.group(g)
                p = p[:a] + con_mayusculas(nueva, w) + p[a + len(w):]
        if re.match(r"(qu[eé])(\s+(?:te|le|les|nos|me))?\s+paso\b", p[j:], re.I):
            p = p[:j] + re.sub(r"\bpaso\b", "pasó", p[j:], count=1)
        if con_signos and not p[j:j + 1] in ("¿", "¡"):
            p = p[:j] + apertura(cierre) + p[j:]
            agregados += 1
        lista[k] = p
    return agregados


def corregir(texto, limite, dicc, signos=True):
    """Texto corregido. Los signos de apertura solo si signos y el registro sigue
    cabiendo."""
    def pasada(con_signos):
        lista = piezas(texto)
        acentos_generales(lista, dicc)
        contexto(lista)
        preguntas(lista, con_signos)
        return "".join(lista)
    nuevo = pasada(signos)
    if signos and len(encode(nuevo)) > limite and len(encode(nuevo)) > len(encode(texto)):
        nuevo = pasada(False)
    return nuevo


def registros():
    for path in sorted(glob.glob(os.path.join(TR, "*.json"))):
        raw = open(path, encoding="utf-8").read()
        data = json.loads(raw)
        if isinstance(data, list):
            yield path, raw, data


def main():
    args = sys.argv[1:]
    if len(args) < 3 or args[0] != "--dicc":
        sys.exit(__doc__)
    dicc = Diccionario(args[1])
    modo = args[2]
    sin_signos_ids = set()
    if os.path.exists(SIN_SIGNOS):
        sin_signos_ids = {l.strip() for l in open(SIN_SIGNOS) if l.strip()}
    cambios, sin_signos, ejemplos = 0, 0, []
    for path, raw, data in registros():
        kw, nl = json_style(raw, data)
        tocado = False
        for r in data:
            t = r["texto_traducido"]
            if r["estado"] not in ESTADOS or not t.strip() or t == r["texto_original"]:
                continue
            nuevo = corregir(t, r["longitud_original_bytes"], dicc, r["id"] not in sin_signos_ids)
            if nuevo != t:
                cambios += 1
                if re.search(r"[?!]", t) and not re.search(r"[¿¡]", nuevo):
                    sin_signos += 1
                ejemplos.append((r["id"], t, nuevo))
                if modo == "aplicar":
                    r["texto_traducido"] = nuevo
                    tocado = True
        if tocado:
            open(path, "w", encoding="utf-8").write(json.dumps(data, **kw) + nl)
    print(f"registros cambiados: {cambios} (sin espacio para ¿ ¡: {sin_signos})")
    if modo == "revisar":
        n = int(args[3]) if len(args) > 3 else 20
        random.seed(0)
        for rid, a, b in random.sample(ejemplos, min(n, len(ejemplos))):
            print(f"\n{rid}\n  - {a}\n  + {b}")


if __name__ == "__main__":
    main()
