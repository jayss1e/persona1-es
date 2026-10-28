#!/usr/bin/env bash
# compilar_lote.sh — Compila el texto de la historia y lo verifica después de cada
# lote de retraducción (pasos 1-3 de notas/compilar.md más los verificadores).
#
# Al final deben salir "Excede límite: 0", punteros sin malos y "huecos: 0".
# Si aparecen huecos, un tramo entre anclas no cuadra con su tramo en inglés
# (casi siempre es más largo, a veces mucho más corto): hay que acortarlo o alargarlo.
#
# Uso (desde cualquier carpeta):
#   PERSONA_ISO_EU=~/Escritorio/Persona.iso bash tools/compilar_lote.sh
set -e
REPO="$(cd "$(dirname "$0")/.." && pwd)"
HERR="$REPO/../herramientas"
ISO_EN="${PERSONA_ISO_EU:?falta PERSONA_ISO_EU}"
ISO_ES="$REPO/rebuilt/persona_es_patched.iso"
cd "$REPO"

python3 scripts/reinsert.py | grep -E "Insertados|Excede"
python3 scripts/build_patch.py > /dev/null
python3 tools/fix_jump_tables.py > /dev/null
python3 tools/fix_text_pointers.py "$ISO_EN" "$ISO_ES" > /dev/null
python3 tools/reubicar_mensajes.py "$ISO_EN" "$ISO_ES" | tail -1
python3 "$HERR/verificar_punteros.py" "$REPO" "$ISO_EN" "$ISO_ES" | tail -1
python3 "$HERR/contar_huecos.py" "$REPO" "$ISO_ES" | tail -1
