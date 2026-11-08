# Filtros de Gmail para promociones recurrentes

JARVIS sirve para acciones puntuales sobre una lista que estás viendo («borra el segundo», «borra todos los de AliExpress»). Para el correo que llega todas las semanas del mismo remitente, un filtro de Gmail es más rápido: actúa solo, sin voz ni confirmaciones. Esta guía no automatiza nada: los filtros los creas tú en Gmail.

## Crear un filtro (Gmail en el navegador)

1. En la barra de búsqueda, pulsa el icono de opciones de búsqueda (a la derecha).
2. En **De**, escribe el remitente o su dominio, por ejemplo `aliexpress.com`.
3. Pulsa **Buscar** y revisa que salen solo los correos que quieres filtrar.
4. Vuelve a abrir las opciones de búsqueda y pulsa **Crear filtro**.
5. Elige qué hacer (abajo, lo recomendado) y marca **Aplicar también el filtro a las conversaciones que coinciden** para limpiar lo que ya tienes.
6. Pulsa **Crear filtro**.

Los nombres de los botones pueden variar ligeramente según la versión de Gmail.

## Qué acción elegir

| Acción | Cuándo | Reversible |
|---|---|---|
| **Saltar Recibidos (Archivarlo)** + **Aplicar la etiqueta** «Promos» | Recomendada: no ensucia la bandeja y sigue ahí si un día buscas un pedido o una factura | Sí, siempre |
| **Marcar como leído** | Junto con la anterior, para que no cuente como no leído | Sí |
| **Eliminarlo** | Solo si nunca te interesa nada de ese remitente. Va a la papelera y Gmail la vacía a los 30 días | 30 días |

Evita **Eliminarlo** para remitentes que también mandan cosas importantes (por ejemplo, una tienda que envía publicidad y también confirmaciones de pedido): mejor archiva con etiqueta.

## Búsquedas útiles para afinar el filtro

Se escriben en el campo **Contiene las palabras** o directamente en la barra de búsqueda:

- `from:aliexpress.com`: todo lo de ese dominio.
- `from:aliexpress.com -pedido -factura`: excluye lo que lleve esas palabras.
- `category:promotions from:amazon.es`: solo lo que Gmail ya clasificó como promoción.
- `from:alertas@ejemplo.com older_than:7d`: alertas de más de una semana (útil para buscar y borrar a mano de vez en cuando; los filtros solo actúan sobre el correo nuevo, así que este operador no sirve dentro de un filtro).

## Darse de baja

Si un remitente legítimo te manda publicidad que no quieres, Gmail suele mostrar **Cancelar suscripción** junto al remitente. Es más limpio que filtrar. No lo uses con correos sospechosos: en ese caso, márcalo como spam.

## Revisar o quitar filtros

Ajustes (rueda dentada) → **Ver todos los ajustes** → pestaña **Filtros y direcciones bloqueadas**. Desde ahí se editan o eliminan.
