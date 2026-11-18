# Bifrost · Resumen de mejoras

18 de noviembre de 2026 · Versión 7

Bifrost conecta Claude con Figma Desktop mediante un puente local. Las mejoras permiten autenticar las conexiones, dirigir comandos a un documento concreto y comprobar desde el panel qué operaciones se ejecutaron.

## Qué cambió

| Área | Mejora | Beneficio |
| --- | --- | --- |
| Seguridad | El puente y sus clientes se autentican mutuamente mediante HMAC. El plugin de Figma y las sesiones de Claude usan credenciales distintas. La herramienta `run_code` queda desactivada por defecto. | El puente exige una credencial válida antes de aceptar comandos. |
| Conexión | El panel muestra **Conectado con Claude** cuando el servidor confirma que registró el documento. La autenticación funciona también en el entorno de Figma Desktop que carece de `crypto.subtle`. | El indicador verde confirma que el puente reconoce el archivo y puede recibir comandos para él. |
| Reconexión | Figma guarda la vinculación localmente y la recupera al volver a ejecutar el plugin. | Después de reiniciar Figma y Claude, puedes reabrir Bifrost sin volver a pegar el código. |
| Selección del archivo | El puente resuelve los enlaces por clave y rechaza nombres ambiguos. Cuando Figma omite la clave, puedes asociar el enlace desde el panel del propio documento. | Puedes dirigir una operación al archivo correcto aunque dos pestañas tengan el mismo nombre. |
| Copia de capas | Bifrost valida la referencia de destino antes de insertar capas y revierte los nodos creados si falla su colocación. | Una referencia inexistente devuelve un error y deja el destino listo para reintentar. |
| Interfaz | El panel muestra la conexión, el documento y la actividad. Tras asociar el enlace, el formulario se pliega. Cada evento muestra su hora y los errores tienen una fila destacada. | Puedes identificar el archivo abierto y revisar el resultado de una operación desde el mismo panel. |

## Qué se verificó

- **Lectura en vivo después de reiniciar Figma y Claude.** Una sesión nueva de Claude Code ejecutó `get_selection` y recuperó la instancia **Bottomsheet - OTP**, con ID `34542:205349`, usando la vinculación guardada.
- **Dos archivos con el mismo nombre.** En Figma Desktop, ambos documentos se llamaban **Bifrost QA**. El enlace de origen devolvió la capa **Origen QA**, el de destino devolvió **Destino QA** y la consulta por el nombre compartido produjo un error de ambigüedad.
- **Copia fallida entre archivos.** Se intentó copiar una capa junto a un nodo inexistente. Al comparar el destino antes y después, conservaba exactamente su única capa, **Destino QA** (ID `1:2`). Esta prueba cubrió el fallo de referencia.
- **Panel rediseñado.** En Chromium, con el puente real, se probaron la conexión, el rechazo de un enlace inválido, la asociación de un enlace válido, **Ver enlace** y la desconexión. La verificación visual de este rediseño se hizo en Chromium.

## Uso diario

El enlace es opcional. Si omites el archivo, Bifrost usa el documento conectado con actividad más reciente. También puedes indicar un nombre único. Para distinguir archivos homónimos, usa sus enlaces.

El código de vinculación se conserva entre reinicios. La asociación manual de un enlace dura mientras el plugin siga abierto en ese documento. Si cierras el plugin, vuelve a asociar el enlace cuando necesites dirigir comandos por URL.

[Guía de instalación y uso](LEEME.md)
