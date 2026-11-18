#!/bin/bash
# Crea dist/Bifrost.zip para compartir. Solo incluye esta lista, así que ~/.bifrost
# (índices de Design System y archivos conectados) nunca entra al paquete.
set -e
cd "$(dirname "$0")"

STAGE="$(mktemp -d)/Bifrost"
mkdir -p "$STAGE/server" "$STAGE/figma-plugin" "$STAGE/assets" dist
cp INSTALAR-CON-CLAUDE.md LEEME.md README.md LICENSE install.command install.bat install.ps1 "$STAGE/"
cp server/server.py server/register.py "$STAGE/server/"
cp figma-plugin/code.js figma-plugin/ui.html figma-plugin/manifest.json "$STAGE/figma-plugin/"
cp assets/bifrost-icon-128.png "$STAGE/assets/"
# Claude Code carga CLAUDE.md solo si abren la carpeta descomprimida como proyecto.
echo 'Si te piden instalar Bifrost, sigue INSTALAR-CON-CLAUDE.md.' > "$STAGE/CLAUDE.md"

rm -f dist/Bifrost.zip
ditto -c -k --norsrc --keepParent "$STAGE" dist/Bifrost.zip
rm -rf "$(dirname "$STAGE")"
echo "Listo: dist/Bifrost.zip ($(du -h dist/Bifrost.zip | cut -f1))"
