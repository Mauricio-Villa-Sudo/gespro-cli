# Cambios

Los cambios de cada versión, del más nuevo al más antiguo.

## 1.4.0, 8 de octubre de 2026

### Agregado

- `--crear`: crea una tarea con `--tipo`, `--padre`, `--descripcion` y las opciones de `--wp` que no son
  horas ni comentarios. Sin `--asignar`, queda asignada a ti. Si ya hay una con el mismo asunto y la
  misma persona asignada, no crea otra.
- `--padre` en `--wp`: deja una tarea dentro de otra, por ejemplo dentro de su historia.
- `--editar-horas` y `--borrar-horas`: corrigen o borran un registro tuyo de `--mis-horas`. Los de
  otra persona no se tocan. En la terminal, `--borrar-horas` pide escribir `si` antes de borrar.
- `--mis-horas` muestra el `id` de cada registro.

### Corregido

- `--hours` y la línea `Horas:` rechazan menos de un minuto. Antes quedaba un registro de 0 minutos.

## 1.3.1, 8 de octubre de 2026

### Corregido

- `--wp` manda todos los cambios de una tarea en un solo `PATCH`. Antes mandaba uno por campo, y si
  GesPro rechazaba el estado, el sprint ya había cambiado.
- Si la conexión se corta a mitad de la respuesta o se queda esperando, el script muestra un error en
  vez de caerse. Con `--en-texto` y `--commit` sigue con las demás tareas.
- `--commit` no hace nada con los commits de un rebase (también de `git pull --rebase`) ni de un
  cherry-pick. Antes el hook dejaba un comentario nuevo por cada commit reaplicado.
- `Tu token no ve el proyecto` sale solo cuando GesPro responde 403 o 404. Si el token venció (401) o
  no hay internet, se muestra ese error.
- `gespro.env` se lee aunque tenga BOM, que agregan PowerShell 5.1 y el Bloc de notas antiguo, o
  espacios alrededor del `=`.
- `--check` prueba estos casos sin conectarse a GesPro.

## 1.3.0, 4 de octubre de 2026

### Agregado

- `--commit`: lee el último commit, comenta en cada tarea mencionada como `OP#número` con el enlace al
  commit y registra la línea `Horas: N` del mensaje cuando menciona una sola tarea. Si se ejecuta dos
  veces, no repite el comentario ni las horas. Tampoco vuelve a registrar las horas de un commit que
  sale de un `--amend`, un rebase o un cherry-pick.
- `--en-texto`: aplica las opciones de `--wp` a cada tarea mencionada como `OP#número` en un texto, por
  ejemplo la descripción de un PR. Si una tarea da error, sigue con las demás.
- `GUIA-AUTOMATIZACION.md`: cómo automatizarlo con un hook de Git, GitHub Actions, la integración de
  GesPro con GitHub y un asistente de IA.
- `ejemplos/`: hook `post-commit`, workflow `gespro-pr.yml` e instrucciones para un asistente de IA.
- El README explica dónde clonar el script.

### Cambiado

- `--status` y `--sprint` también se revisan antes de escribir nada, incluso con `--dry-run`.

## 1.2.0, 3 de octubre de 2026

### Agregado

- `--prioridad`: cambia la prioridad de una tarea (`Low`, `Normal`, `High` o `Immediate`).
- `--asignar`: reasigna una tarea a otro miembro del proyecto con parte de su nombre, sin importar
  mayúsculas ni tildes.
- `--miembros`: lista los miembros del proyecto.
- `--mis-tareas` muestra la prioridad de cada tarea, y `--wp` la muestra junto al asignado.

### Cambiado

- `--prioridad` y `--asignar` se revisan antes de escribir nada, también con `--dry-run`. Un nombre
  mal escrito ya no deja la tarea a medio cambiar.

## 1.1.0, 30 de septiembre de 2026

### Agregado

- Sirve para cualquier proyecto de GesPro: el proyecto sale de `GESPRO_PROJECT` o de `--proyecto`.
- `--proyectos`: lista los proyectos que ve tu token, con el identificador que hay que copiar.

### Cambiado

- El README está escrito para cualquier estudiante de la carrera.

## 1.0.0, 30 de septiembre de 2026

### Agregado

- `--mis-tareas`, `--mis-horas` y `--report` para ver tareas, horas y el estado del equipo.
- `--wp` con `--status`, `--percent`, `--hours`, `--fecha`, `--comment` y `--sprint` para actualizar una
  tarea.
- `--dry-run` para ver lo que haría sin escribir, y `--check` como prueba local.
