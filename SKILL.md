---
name: gespro-cli
description: Registrar trabajo en GesPro (el OpenProject de la carrera, gespro.devhub.cl) desde la terminal con gespro.py. Ver y actualizar tareas, estado, sprint, porcentaje, horas, comentarios, crear tareas, repartir puntos de historia, revisar el sprint y armar el daily. Usar cuando el usuario quiera anotar o consultar algo en GesPro, en vez de abrir el navegador.
---

`gespro.py` está en la misma carpeta que este archivo. Usa solo la biblioteca estándar de Python 3.9
o superior. Llámalo con la ruta completa:

    python <carpeta de esta skill>/gespro.py --mis-tareas

El token y el proyecto salen de `gespro.env`, al lado del script, o de las variables de entorno
`GESPRO_API_KEY` y `GESPRO_PROJECT`. Nunca pidas el token por el chat ni lo escribas en un archivo
del repositorio o en un commit. Si falta, dile al usuario que lo cree en
<https://gespro.devhub.cl/my/access_tokens> (sección API) y que lo pegue él en `gespro.env`, copiando
`gespro.env.example`. Si no sabe el identificador del proyecto, `--proyectos` se lo muestra.

La primera vez, `--check` prueba el script sin conectarse a GesPro.

## Comandos

Para leer, que no escriben nada:

- `--mis-tareas`, `--mis-horas [--desde AAAA-MM-DD]`, `--buscar TEXTO`, `--ver N`, `--miembros`
- `--report`, `--sprint-actual`, `--horas-equipo [--desde ...]`
- `--revisar`, `--atrasadas`, `--burndown [--sprint ...]`, `--daily [--fecha ...]`
- `--formato csv|json` con `--report`, `--mis-horas` y `--horas-equipo`

Para escribir:

- `--wp N` con `--status`, `--percent`, `--hours`, `--fecha`, `--comment`, `--sprint`, `--prioridad`,
  `--asignar`, `--padre`, `--estimado`, `--inicio` y `--fin`
  (`--status` y `--sprint` también mueven la tarjeta en el tablero del sprint)
- `--crear "asunto"` con `--tipo`, `--padre`, `--descripcion` y las opciones de `--wp` que no son
  horas ni comentarios. `--crear-desde archivo.csv` crea una por fila.
- `--puntos N [--total P] [--peso ID=HORAS]` reparte los puntos de una User story entre sus tareas
- `--editar-horas ID`, `--borrar-horas ID` (el `id` sale de `--mis-horas`)
- `--commit`, `--en-texto "texto con OP#N"`
- `--cerrar-sprint S --sprint S2`, `--relacionar N --con M [--como bloquea]`, `--adjuntar N archivo`
- `--iniciar N`, `--parar [--comment ...]`

`--proyecto identificador` cambia de proyecto en una sola llamada. Los detalles de cada comando están
en `README.md` y la automatización con Git y GitHub en `GUIA-AUTOMATIZACION.md`, los dos en esta
carpeta.

## Reglas

1. Antes de escribir en GesPro, corre el mismo comando con `--dry-run`, muéstrale al usuario qué
   cambiaría y espera su visto bueno.
2. Las horas las pone el usuario. Si te pide estimarlas, di de dónde salen (commits, archivos, su
   actividad del día) y espera que las apruebe. Revisa `--mis-horas` antes, para no registrarlas dos
   veces. Corregir o borrar horas también lo decide él.
3. Al tomar una tarea: `In progress`, el porcentaje que de verdad lleva y un comentario con lo que va
   a hacer. Al terminarla, otro comentario con lo que quedó y el enlace al PR o al commit.
4. Comentarios cortos y en español: qué se hizo, qué falta y por qué.
5. `Done` solo para lo que ya está integrado en la rama principal del equipo.
6. No cambies tareas de otra persona sin preguntar. El script lo permite y muestra a quién está
   asignada.
7. Rama, commit y PR siguen el fragmento de Git de GesPro, con `OP#numero` agregado:
   - Rama: `task/<numero>-<asunto>`, la que da GesPro en la tarea.
   - Commit: título `[#numero] asunto`; en el cuerpo, `OP#numero` y el enlace a la tarea.
   - PR: al final de la descripción, `GesPro: OP#numero.` y el enlace.

   El script y la integración leen `OP#numero`; `[#numero]` solo no les basta. Si el commit es de una
   sola tarea y el usuario dio las horas, agrega la línea `Horas: N`, solo con el número.
8. Si el usuario tiene el hook de `ejemplos/post-commit`, cada commit con `OP#numero` escribe en
   GesPro. Muéstrale el mensaje del commit antes de hacerlo.

## Si algo falla

El script explica el error y, si escribiste mal un estado, sprint, prioridad o persona, lista los
nombres válidos. Un `HTTP 403` con `1010` es Cloudflare: no cambies el `USER_AGENT` del script. Un
`HTTP 409` significa que alguien cambió la tarea mientras tanto; vuelve a correr el comando. La tabla
completa está al final del README.
