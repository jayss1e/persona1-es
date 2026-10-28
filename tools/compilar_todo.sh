#!/usr/bin/env bash
# compilar_todo.sh — Arma la ISO en español completa y su parche .xdelta.
#
# Sigue el orden de notas/compilar.md: texto de la historia, menú cm_menu, EBOOT,
# ISO unificada, mensajes de calabozos (tools/dng.py), imágenes con texto y fuente
# con acentos. Al final genera el .xdelta
# sobre la ISO ya terminada (build_unified.py hace uno antes de las imágenes y la
# fuente, que no sirve para publicar).
#
# Antes, en un repo recién clonado, hay que sacar de la ISO lo que el repo no trae:
#   python3 tools/original.py restaurar "$PERSONA_ISO_EU"
#
# Uso:
#   PERSONA_ISO_EU=~/ruta/Persona.iso bash tools/compilar_todo.sh
# Salida: rebuilt/persona_es_full.iso y patches/persona_es_full.xdelta
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
ISO_EN="${PERSONA_ISO_EU:?falta PERSONA_ISO_EU con la ruta de la ISO europea}"
ISO_ES="$REPO/rebuilt/persona_es_patched.iso"
ISO_FIN="$REPO/rebuilt/persona_es_full.iso"
XD="$REPO/patches/persona_es_full.xdelta"
cd "$REPO"
mkdir -p rebuilt patches

echo "== texto de la historia"
python3 scripts/reinsert.py | grep -E "Insertados|Excede"
python3 scripts/build_patch.py > /dev/null
python3 tools/fix_jump_tables.py > /dev/null
python3 tools/fix_text_pointers.py "$ISO_EN" "$ISO_ES" > /dev/null
python3 tools/reubicar_mensajes.py "$ISO_EN" "$ISO_ES" | tail -1

echo "== menú y EBOOT"
python3 tools/edit_cm_menu20.py > /dev/null
python3 tools/eboot_menus_8140.py _eboot/eb_inventory.json _eboot/trans_8140.json _eboot/trans_records.json | tail -1
python3 tools/eboot_nombres.py _eboot/trans_nombres.tsv _eboot/trans_records.json | head -1
python3 tools/eboot_apply.py _eboot/eboot_dec.elf _eboot/eboot_translated.elf _eboot/eb_inventory.json _eboot/trans_records.json > /dev/null
python3 tools/eboot_le_lineas.py aplicar | tail -1
python3 tools/eboot_le_apply.py | tail -1

echo "== ISO unificada, imágenes y fuente"
python3 tools/build_unified.py > /dev/null
python3 tools/dng.py aplicar "$ISO_EN" "$ISO_FIN" | tail -1
python3 tools/texto_imagenes.py aplicar "$ISO_FIN" > /dev/null
python3 tools/fuente.py "$ISO_FIN" | tail -1

echo "== parche"
xdelta3 -e -9 -f -s "$ISO_EN" "$ISO_FIN" "$XD"
md5sum "$ISO_FIN"
ls -l "$XD"
