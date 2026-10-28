# Handoff de bugs de playtest — Persona PSP ES

> **Para: Fable 5.** Este documento reúne los errores observados en playtest (con las capturas
> reales), el diagnóstico actual y **soluciones candidatas**. **No está cerrado**: cada diagnóstico
> es la mejor hipótesis actual, no un veredicto. Si encuentras una causa o una solución mejor,
> tómala — el objetivo es arreglar el juego, no defender estas hipótesis.
>
> Fecha: 2026-07-03. Repo: `/home/josechavez/persona_es`. Emulador de prueba: PPSSPP.
> ISO de prueba ya construido: `rebuilt/persona_es_full.iso` (carga directa, sin aplicar xdelta).

---

## Contexto técnico imprescindible (leer antes de tocar nada)

- **Codec** (`scripts/persona_codec.py`): cada carácter y cada token de control ocupa **2 bytes**
  (u16 big-endian). Espacio = `0x0000`. Control = `0xFFxx` → token `<FFxx>`. Punteros/otros = `<NNNN>`.
- **Sin acentos ni ñ**: la fuente base del juego no los tiene; el codec lanza error. Se escribe
  `nino`, `mas`, `corazon`. (Regla 3 del README.)
- **Presupuesto de bytes**: `encode(traducción) ≤ longitud_original`. El ES no puede crecer.
- **Regla de oro (regla 4 del README)**: los tokens estructurales (`<NNNN>` punteros, `<FFFF>` soup
  de fondo) deben quedar en su **byte/offset absoluto original**. Cuando el ES es más corto, NO se
  rellena al final: se rellena **cada tramo** hasta la longitud de su tramo EN, para que cada ancla
  quede en su offset exacto. Ver `scripts/reinsert.py` (`_align_per_run`, `_align_trailing_soup`).
  → **Ese diseño es CORRECTO.** (Un análisis previo dijo que ese padding era la causa de los bugs;
  es falso — el padding es trailing dentro de cada run y preserva offsets. No lo revierta.)
- **Dos corpus separados, dos pipelines:**
  - **Historia / negociación de demonios / tienda**: bins del ISO → `extracted_text/*.json` →
    `translated_text/*.json` → `scripts/reinsert.py` → `rebuilt/patch_records.json` →
    `scripts/build_patch.py` → `rebuilt/persona_es_patched.iso`.
  - **EBOOT.BIN (texto de sistema, menús, verbos de comando)**: `_eboot/eboot_dec.elf` +
    `_eboot/eb_inventory.json` + `_eboot/trans_records.json` → `tools/eboot_apply.py` →
    `_eboot/eboot_translated.elf`.
  - **Unificación**: `tools/build_unified.py` → `patches/persona_es_full.xdelta` +
    `rebuilt/persona_es_full.iso` (verifica MD5 original + round-trip).
- **Cómo rebuild y probar**: tras editar traducciones/EBOOT, correr `python3 tools/eboot_apply.py
  _eboot/eboot_dec.elf _eboot/eboot_translated.elf _eboot/eb_inventory.json _eboot/trans_records.json`
  (si tocaste EBOOT) y/o `python3 scripts/build_patch.py` (si tocaste historia), luego
  `python3 tools/build_unified.py`. Cargar `rebuilt/persona_es_full.iso` en PPSSPP.

---

## BUG 1 — Verbos de negociación de combate cortados al inicio  ✅ ARREGLADO (verificar in-game)

**Imágenes:** `docs/bugs_img/1.png`–`5.png` (menú de "contacto/negociación" con un monstruo;
opciones como `gar`, `edir`, `ailar`, `rlar`, `star`, `on` — cada verbo pierde sus primeras letras;
la cantidad varía por verbo y por personaje).

**Diagnóstico (CONFIRMADO):** los 37 verbos viven en UN blob empacado en EBOOT
(`_eboot/trans_records.json`, id de inventario `eb_02563`, off de archivo `0x2ACDF8`, 546 bytes),
separados por `<FF01>`: `Truco<FF01>Grita<FF01>Amagar<FF01>...`. El engine lee **cada entrada a un
offset de byte fijo** (no camina el separador; no hay tabla de punteros absolutos localizable). Al
traducir, cada verbo ES cambió de longitud → drift acumulado → el offset EN cae a media palabra ES →
se ve la cola.

**Solución aplicada:**
1. `tools/eboot_apply.py`: añadido `_align_ff01()` + `MENU_IDS={'eb_02563'}`. Para ese record padea
   **cada segmento `<FF01>`** a la longitud-bytes de su segmento EN → cada entrada vuelve a su offset
   absoluto. Solo se aplica a `MENU_IDS` (aplicarlo a diálogo normal rompería líneas que caben por
   total pero varían por línea).
2. Como cada verbo ES debe ser ≤ su verbo EN, se acortaron 11 verbos en `_eboot/trans_records.json`
   (backup `.bak_verbfix`): Grita, Gime, Seduce, Invita, Canta, Ego(Brag), Burla, Baila, Habl(Chat),
   Tima(Lie), Cant(Sing 2ª).

**Estado:** verificado a nivel de bytes (drift 0, 37 verbos completos). `rebuilt/persona_es_full.iso`
regenerado. **Falta confirmar in-game** (combate → negociar → ciclar los 4 personajes con L/R).

**Abierto a mejorar:** (a) algunas abreviaturas son feas (`Habl`, `Ego`, `Cant`); si se localiza y
recalcula la tabla de índices/punteros de este blob, los verbos podrían ir completos sin acortar —
se buscó tabla contigua u32/u16 y punteros absolutos y NO se hallaron, pero podría estar codificada de
otra forma (índices, offsets relativos raros). (b) Confirmar que no hay OTROS blobs de menú con el
mismo patrón (otras listas indexadas por offset) que necesiten entrar en `MENU_IDS`.

---

## BUG 2 — Diálogo de tienda/NPC cortado al inicio (faltan 1–3 palabras)  ❓ PENDIENTE

**Imágenes:**
- `docs/bugs_img/7.png` — Satomi Tadashi (farmacia): la línea inferior muestra `o a los demonios.`
  (probable original `...algo a los demonios.` → faltan ~3 chars al inicio).
- `docs/bugs_img/8.png` — misma escena: `ajo sus futones.` (probable `Bajo sus futones.` → falta `B`).
- `docs/bugs_img/11.png` — Sennen Mannen-Do: `sa tienda rara del centro al parecer / abrira pronto.`
  (probable `Esa tienda...` → falta `E`; la 2ª línea sale completa).

**Diagnóstico (HIPÓTESIS, NO confirmado):** dos posibilidades a distinguir:
1. **Scroll de typewriter**: si la captura se tomó mientras el texto avanzaba, la caja de una sola
   línea puede mostrar el final de la línea con el inicio ya "scrolleado". En ese caso NO es bug.
   → **Verificar**: dejar que el diálogo termine de escribirse y ver si la primera palabra aparece.
2. **Mismo tipo de bug que BUG 1** (lectura a offset fijo dentro de un string con estructura), pero
   en el pipeline de historia (`reinsert.py`) o en strings de tienda del EBOOT.
   → **Verificar**: identificar el record exacto (buscar el texto ES en `translated_text/*.json` y en
   `_eboot/trans_records.json`), comparar `raw_hex`/bytes aplicados contra el original, y ver si el
   inicio se perdió en los DATOS (bug real) o si los datos están completos (entonces es scroll/render).

**Soluciones candidatas (según cuál sea la causa):**
- Si es **scroll**: no hay nada que arreglar; descartar.
- Si el string tiene **soup/anclas al inicio** y el ES desalineó un tramo previo: revisar que
  `_align_per_run`/`_align_trailing_soup` cubran ese record (puede estar cayendo al fallback de
  padding-al-final por un `RUNOVER`, lo que sí correría el inicio visible). Buscar en
  `docs/reinsert_report.md` si ese record salió como "excede_limite" o usó fallback.
- Si es un **string de menú/tienda indexado por offset** (como BUG 1): extender el enfoque de
  `_align_ff01`/`MENU_IDS` al record correspondiente.

**Primer paso recomendado:** pedir/observar si el corte es fijo (texto quieto ya cortado) o durante
el scroll. Eso decide todo.

---

## BUG 3 — Opciones múltiples genéricas o no acordes al diálogo  ❓ PENDIENTE

**Imágenes:**
- `docs/bugs_img/6.png` y `docs/bugs_img/10.png` — tiendas/cajero: el subtítulo ya está en ES
  (`Cajero`, `Que buscas?`) pero las opciones del menú salen en inglés `Yes` / `No`.
- También se reporta que en casinos/tiendas "solo sale Sí y No" y opciones que no corresponden.

**Diagnóstico (HIPÓTESIS):** las cadenas de opción (`Yes`/`No` y otras) son records **sin traducir**
todavía (quedan en inglés), y/o están en un menú común (`extracted_text/cm_menu.json` / EBOOT) que no
se ha mapeado. Relaciona con la brecha de texto conocida (ver `persona-es-text-gap` en memoria: ~838
records de historia/talk nunca traducidos).

**Soluciones candidatas:**
- Localizar `Yes`/`No` y demás opciones en `cm_menu.json` / `bt_menu.json` / EBOOT
  (`_eboot/trans_records.json`) y traducir (`Si`/`No`), respetando presupuesto de bytes.
- Si las opciones "no corresponden al diálogo" (p.ej. salen opciones de otro contexto), puede ser un
  bug de **índice/puntero** distinto: investigar si el menú de opciones se arma con IDs que se
  desalinearon. Esto NO es solo traducción; requiere revisar la estructura del menú.
- Verificar cuáles opciones son texto y cuáles son **sprites pre-renderizados** (ver
  `persona-es-ui-graphics`: varios labels de UI son gráficos, no texto).

---

## BUG 4 — Diálogos en blanco o que se cierran de golpe (p.ej. hablar con doctores)  ❓ PENDIENTE

**Reporte del usuario:** al hablar con ciertos NPC (ejemplo: cualquier doctor), el diálogo sale en
blanco o se cierra de golpe. (Sin captura específica aislada; relacionado con las escenas de tienda
7/8 y con NPCs de diálogo.)

**Diagnóstico (HIPÓTESIS):** un string mal formado puede romper el parser del engine:
- Falta de terminador o soup `<FFFF>` desalineada → la caja se cierra/queda vacía.
- Un record que cayó al fallback de padding-al-final y corrió una ancla estructural.
- Un record con tokens de control alterados que el validador dejó pasar (revisar).

**Soluciones candidatas:**
- Identificar el NPC/record exacto (nombre del doctor, ubicación) para localizar el string.
- Revisar en `docs/reinsert_report.md` y en el reporte de EBOOT si ese record tuvo warnings
  (excede_limite, fallback, tokens).
- Comparar bytes aplicados vs original; confirmar que la soup/terminador quedó en su offset.
- **Riesgo alto**: un diálogo que se cierra puede indicar corrupción de estructura → priorizar,
  porque puede bloquear progreso.

---

## BUG 5 — Casino: no se puede interactuar con máquinas/tragaperras  ❓ PENDIENTE (funcional)

**Imágenes:** `docs/bugs_img/10.png`, `11.png`, `12.png` (sala de arcade/casino "Judgment 1999";
las máquinas no responden al intentar usarlas).

**Diagnóstico (HIPÓTESIS):** bug **funcional**, no solo de texto. Posibles causas:
- Un string de menú del minijuego con estructura corrida (soup/puntero) que impide abrir el menú de
  la máquina (softlock de menú).
- Un record del minijuego que quedó con bytes inválidos.
- Los minijuegos de casino tienen bins propios (blackjack, slot, poker, dice) con offsets en
  `scripts/iso_offsets.json`; revisar si alguno se parcheó mal.

**Soluciones candidatas:**
- Reproducir en `rebuilt/persona_es_full.iso` y también en el ISO **solo-historia**
  (`rebuilt/persona_es_patched.iso`) y en uno **solo-EBOOT** para aislar qué capa introduce el fallo.
- Si al desactivar los parches de casino la interacción vuelve, el culpable es un record de casino
  concreto → localizar y corregir su alineación.
- Revisar `_wfmaps/e0_casino.py` y `translated_text/_wfmaps/e2_casino_build.py`.

---

## BUG 6 — Cadenas todavía en inglés  ❓ PENDIENTE (brecha de traducción)

**Imágenes:** `docs/bugs_img/9.png` (título `Judgment 1999`), `docs/bugs_img/12.png`
(`Go to the hallway`), y las opciones `Yes`/`No` de BUG 3.

**Diagnóstico (CONFIRMADO como categoría):** records nunca traducidos (quedan en inglés in-game).
Ver memoria `persona-es-text-gap`: la afirmación "texto 100%" era falsa; ~838 records reales de
historia/talk siguen en inglés. Títulos de escena y algunos menús también.

**Soluciones candidatas:**
- Ampliar la cobertura de traducción sobre los records pendientes (usar el método de chunk+reuse+
  validación del README). Priorizar por visibilidad (títulos de escena, opciones, NPCs frecuentes).
- Para títulos como `Judgment 1999` / `Go to the hallway`: localizar el record (historia o EBOOT) y
  traducir respetando bytes.

---

## Orden sugerido de ataque (abierto a reordenar)

1. **BUG 1**: confirmar in-game (ya arreglado a nivel bytes).
2. **BUG 4** (diálogos que se cierran) — riesgo de bloquear progreso.
3. **BUG 5** (casino no interactúa) — funcional.
4. **BUG 2** (corte al inicio en tienda) — primero decidir scroll vs bug real.
5. **BUG 3** y **BUG 6** (opciones/inglés) — cobertura de traducción + revisión de menús.

## Cómo aislar capas (útil para todos los bugs)

- `rebuilt/persona_es_patched.iso` = solo historia (sin EBOOT nuevo).
- `rebuilt/persona_es_full.iso` = historia + EBOOT + texturas.
- ISO original limpio en la ruta de `ISO_SRC` dentro de `tools/build_unified.py`.
- Construir variantes intermedias para saber qué capa introduce cada fallo.

## Memorias relacionadas (contexto del proyecto)

`persona-es-menu-offset-bug`, `persona-es-eboot-phase`, `persona-es-text-gap`,
`persona-es-codec-rules`, `persona-es-pipeline`, `persona-es-ui-graphics`, `persona-es-release`.
