#!/usr/bin/env bash
# publicar.sh — Prepara la copia pública del repo, sin texto ni archivos del juego.
#
# La copia local de trabajo (rama arreglos-texto) guarda todo, incluido el texto
# en inglés, y nunca se sube. Este script arma aparte, en ../publicar, un worktree
# de la rama "persona1-es" con el contenido actual de la rama de trabajo, y luego:
#   - pone el README y el .gitignore de publico/,
#   - saca del índice todo lo que ignora ese .gitignore, y
#   - vacía texto_original y raw_hex con tools/original.py quitar.
# No hace commit ni push: deja los cambios preparados para revisarlos.
#
# La rama persona1-es se sube a main de github.com/jayss1e/persona1-es (remoto
# "persona1-es"). No lleva el historial de Jose-Chavez96 porque sus commits traen
# texto extraído del juego. Su crédito va como Co-authored-by y en el README.
#
# Uso (desde el repo de trabajo, con todo ya en commit):
#   bash tools/publicar.sh
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PUB="$(dirname "$REPO")/publicar"
cd "$REPO"
if ! git diff --quiet HEAD; then
    echo "Hay cambios sin commit en el repo de trabajo." >&2; exit 1
fi
RAMA="$(git rev-parse --abbrev-ref HEAD)"

if [ ! -d "$PUB" ]; then
    git fetch -q https://github.com/jayss1e/persona1-es.git main:persona1-es
    git worktree add "$PUB" persona1-es
fi

cd "$PUB"
git read-tree -u --reset "$RAMA"           # mismo árbol que la rama de trabajo
\cp -f publico/README.md README.md
\cp -f publico/gitignore .gitignore
git rm -r -q -f --cached .                 # rearmar el índice respetando .gitignore
git clean -fdqX                            # borra de esta copia lo ignorado
python3 tools/original.py quitar "$PUB"
git add -A
git status --short | cut -c1-2 | sort | uniq -c
echo "Listo en $PUB. Revisar y hacer commit ahí."
