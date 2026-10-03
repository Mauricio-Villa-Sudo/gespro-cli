# Cambios

Los cambios de cada versión, del más nuevo al más antiguo.

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
