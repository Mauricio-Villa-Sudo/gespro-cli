# GesPro (pega esto en el archivo de instrucciones de tu asistente)

Registro mi trabajo en GesPro con `gespro.py`, que está en `~/gespro-cli/`. Mi token y mi proyecto
están en `gespro.env`, al lado del script. Nunca me pidas el token ni lo escribas en el chat, en un
archivo del repositorio o en un commit.

Comandos que puedes usar:

- Leer: `--mis-tareas`, `--mis-horas --desde AAAA-MM-DD`, `--report`, `--buscar`, `--ver`, `--sprint-actual`,
  `--horas-equipo`, `--miembros`.
- Escribir: `--wp N` con `--status`, `--percent`, `--hours`, `--fecha`, `--comment`, `--prioridad`,
  `--asignar`, `--sprint`, `--padre`, `--estimado`, `--inicio` y `--fin`; `--crear "asunto"` con
  `--tipo`, `--padre` y `--descripcion`;
  `--editar-horas ID`, `--borrar-horas ID`, `--commit` y `--en-texto`.

Reglas:

1. Antes de escribir en GesPro, ejecuta el mismo comando con `--dry-run` y muéstrame qué cambiaría.
2. Las horas las pongo yo. Si te pido estimarlas, dime de dónde salen (commits, archivos, mi
   actividad del día) y espera mi visto bueno antes de registrarlas. Revisa `--mis-horas` para no
   duplicar. Corregir o borrar horas con `--editar-horas` o `--borrar-horas` también lo decido yo.
3. Al tomar una tarea: pásala a `In progress`, súbele el porcentaje según lo que realmente está hecho
   y deja un comentario con lo que vas a hacer. Al terminarla, otro comentario con lo que quedó y el
   enlace al PR o al commit.
4. Comentarios cortos y concretos, en español: qué se hizo, qué falta y por qué.
5. Marca `Done` solo lo que ya está integrado en la rama principal del equipo.
6. No cambies tareas de otra persona sin preguntarme.
7. Rama, commit y PR siguen el fragmento de Git de GesPro, con `OP#numero` agregado:
   - Rama: `task/<numero>-<asunto>`, la que da GesPro en la tarea.
   - Commit: título `[#numero] asunto`; en el cuerpo, `OP#numero` y el enlace a la tarea.
   - PR: al final de la descripción, `GesPro: OP#numero.` y el enlace.

   Si el commit es de una sola tarea y me preguntaste las horas, agrega la línea `Horas: N`, solo con
   el número.
8. Tengo un hook de Git que avisa en GesPro en cada commit con `OP#numero`. Por eso un commit cuenta
   como escritura en GesPro. Muéstrame el mensaje y espera mi visto bueno antes de hacerlo.
