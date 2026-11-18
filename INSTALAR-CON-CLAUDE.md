# Instalar Bifrost con Claude

Esta guía es para Claude. La persona te pidió instalar Bifrost, un puente local entre Claude y Figma. Háblale en su idioma, sin tecnicismos y de un paso a la vez. Ella no necesita saber programar.

Bifrost funciona en **macOS y Windows**, con la **app de escritorio de Claude** (Chat o Code) o Claude Code. No funciona en claude.ai desde el navegador ni en el celular, porque el servidor corre en la computadora de la persona.

## Qué cambia en su computadora

Díselo antes de empezar y espera su visto bueno:

- Se copia esta carpeta a un lugar fijo:
  - **Mac:** `~/Library/Application Support/Bifrost`
  - **Windows:** `%LOCALAPPDATA%\Bifrost`
- Se agrega una entrada `bifrost` a la configuración de Claude Code (`~/.claude.json`) y de la app de escritorio (`claude_desktop_config.json`). Antes se guarda un respaldo de cada archivo con terminación `.bak-bifrost`.
- En Windows, si no hay Python, el instalador ofrece instalarlo con `winget` (pregunta antes).
- El plugin de Figma solo habla con `localhost:3055`, en su propia computadora. Bifrost no tiene servidores, cuentas ni analíticas.
- Lo que leas de sus archivos de Figma llega a Claude como parte de la conversación.

## 1. Revisa dónde estás corriendo

- **Tienes terminal en su Mac** (`uname -s` dice `Darwin`): sigue con "Mac".
- **Tienes terminal en su Windows** (PowerShell, o `uname` no existe y `$env:OS` dice `Windows_NT`): sigue con "Windows".
- **Linux:** dile que el instalador no lo cubre todavía y detente.
- **Sandbox o sin terminal** (por ejemplo, el Chat de claude.ai): no puedes instalarlo tú. Pídele que lo haga a mano con los pasos de "Si lo instala a mano" y después guíala desde el paso 3.

## 2a. Mac

Descomprime el zip (si te pasaron uno) y corre el instalador con `bash`, que no choca con el bloqueo de macOS para archivos descargados:

```bash
ditto -x -k /RUTA/A/Bifrost.zip "$TMPDIR/bifrost-zip" && bash "$TMPDIR/bifrost-zip/Bifrost/install.command"
```

Si dice que falta Python, macOS mostrará una ventana para instalar las herramientas de desarrollo. Pídele que la acepte, espera a que termine y corre el comando otra vez.

Comprueba que el servidor responde:

```bash
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1"}}}' | /usr/bin/python3 ~/Library/Application\ Support/Bifrost/server/server.py 2>/dev/null | head -1
```

Debe regresar un JSON con `"result"`. Si no, muéstrale el error y no sigas.

## 2b. Windows

En PowerShell:

```powershell
Expand-Archive -Force C:\RUTA\A\Bifrost.zip "$env:TEMP\bifrost-zip"
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:TEMP\bifrost-zip\Bifrost\install.ps1"
```

Si pregunta por instalar Python, explícale a la persona qué es y deja que ella conteste. Si `winget` no existe, pídele que instale Python desde python.org marcando **"Add python.exe to PATH"** y corre el instalador otra vez.

El instalador imprime la ruta de Python que usó. Comprueba que el servidor responde (cambia `PYTHON` por esa ruta):

```powershell
'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1"}}}' | & PYTHON "$env:LOCALAPPDATA\Bifrost\server\server.py" 2>$null | Select-Object -First 1
```

Debe regresar un JSON con `"result"`.

## 3. Instala el plugin en Figma

Esto lo hace la persona, porque Figma no deja instalarlo desde afuera. El instalador ya dejó copiada la ruta del manifest. Dale estos pasos:

1. Abre la **app de escritorio de Figma**. Desde el navegador no se puede. Si no la tienes, bájala en figma.com/downloads.
2. Abre cualquier archivo de diseño.
3. Ve a **Plugins → Development → Import plugin from manifest…**
4. En la ventana de archivos:
   - **Mac:** presiona **Cmd+Shift+G**, pega con **Cmd+V** y presiona Enter.
   - **Windows:** pega con **Ctrl+V** en el campo del nombre de archivo y presiona Enter.

Espera a que te confirme que lo importó. La conexión se hace después de reiniciar Claude.

## 4. Reinicia Claude (siempre al final)

Reiniciar cierra esta conversación, así que antes de pedirlo déjale claro lo que sigue:

1. Cierra Claude por completo y vuelve a abrirlo:
   - **Mac:** **Cmd+Q**.
   - **Windows:** clic derecho en el ícono de Claude junto al reloj → **Quit**. Cerrar la ventana no basta.
2. Al abrir Claude se crea el código de vinculación en `~/.bifrost/token` (en Windows, `%USERPROFILE%\.bifrost\token`). Ábrelo con un editor de texto y copia su contenido.
3. En Figma, corre **Plugins → Development → Bifrost → Mostrar panel**, pega el código en **Código de vinculación** y pulsa **Conectar**. Figma lo recuerda, así que solo se hace una vez.
4. El panel debe mostrar **Conectado con Claude**.
5. En una conversación nueva, con Figma abierto, pregunta: **"¿Qué tengo seleccionado en Figma?"**

Solo en Mac, opcional, para que Claude encienda el plugin solo: **Ajustes del Sistema → Privacidad y seguridad → Accesibilidad → Claude**. Ese permiso lo tiene que dar la persona. En Windows el plugin se enciende a mano desde el menú Plugins.

## Si lo instala a mano

1. Descomprimir el zip. En Windows: clic derecho → **Extraer todo…** (no abrirlo desde dentro del zip).
2. Correr el instalador:
   - **Mac:** doble clic en `install.command`. Si macOS dice que no se puede abrir, ir a **Ajustes del Sistema → Privacidad y seguridad**, bajar hasta el aviso de `install.command` y elegir **Abrir de todos modos**. En macOS 15 o más nuevo, el clic derecho → Abrir ya no basta.
   - **Windows:** doble clic en `install.bat`. Si sale "Windows protegió tu PC", elegir **Más información → Ejecutar de todas formas**.

## Si algo falla

- **Claude no ve las herramientas de Bifrost después de reiniciar:** una sesión abierta pudo sobrescribir `~/.claude.json`. Cierra Claude, corre el instalador otra vez y vuelve a abrirlo.
- **"El plugin de Figma no está conectado":** corre el plugin en Figma otra vez. Figma lo apaga al recargar o cambiar de archivo.
- **"El puerto 3055 lo tiene otro programa":** puede ser otro conector de Figma como "Talk to Figma", que usa el mismo puerto. Hay que cerrarlo o quitarlo.
- **No aparece la opción Development:** la persona está en el navegador. Tiene que usar la app de escritorio.
- **Figma rechaza el manifest:** copia el mensaje exacto y pásaselo a quien te compartió Bifrost.

Más detalles en `LEEME.md`.
