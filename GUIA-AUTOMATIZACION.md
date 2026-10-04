# Automatizar GesPro con Git, GitHub y un asistente de IA

Esta guía parte de que `gespro.py` ya funciona con tu token y tu proyecto (ver el README). Hay cuatro
niveles. Cada uno sirve solo, y se pueden combinar.

| Nivel | Qué hace | Qué necesitas |
|---|---|---|
| 1. Mencionar la tarea | El commit y el PR dicen a qué tarea pertenecen | Nada |
| 2. Hook de Git | Cada commit deja un comentario en su tarea y registra las horas que declares | Tu computador |
| 3. GitHub Actions | Al abrir o integrar un PR, GesPro lo comenta y cambia el estado | Ser admin del repo |
| 4. Integración de GesPro con GitHub | El PR aparece en la pestaña GitHub de la tarea, con su estado y sus checks | Ser admin del repo y del proyecto en GesPro |

## 1. Mencionar la tarea con OP#número

Escribe el número de la tarea así, en el mensaje del commit o en el título o la descripción del PR:

```
feat(carrito): recalcular el total al quitar un ítem

OP#621
Horas: 1,5
```

- `OP#621` es el formato que usa la integración oficial de OpenProject con GitHub, así que sirve también
  para el nivel 4.
- La línea `Horas: 1,5` es opcional. Va sola en su línea, con el número solo: `Horas: 2h` o
  `Horas: 1:30` no sirven, y el script avisa sin registrar nada. Solo cuenta cuando el commit menciona
  una sola tarea.
- Un commit puede mencionar varias tareas. En ese caso se comentan todas, pero las horas no se registran,
  porque el script no sabe cómo repartirlas. Regístralas con `--wp`.

Sin hook ni workflow, puedes hacer lo mismo a mano después de cada commit:

```
python gespro.py --commit --dry-run
python gespro.py --commit
```

`--commit` lee el último commit del repositorio donde estás. Deja en cada tarea mencionada un comentario
con el hash, el título y el enlace al commit en GitHub, y registra las horas de la línea `Horas:` con la
fecha de hoy. Si lo ejecutas dos veces, no repite nada.

Un `git commit --amend`, un rebase o un cherry-pick crean un commit nuevo con el mismo mensaje. En esos
casos el script comenta el commit nuevo, pero no vuelve a registrar sus horas.

## 2. Hook de Git: que pase solo al hacer commit

1. Copia `ejemplos/post-commit` a `.git/hooks/post-commit` dentro de tu repositorio.
2. Si `gespro.py` no está en `~/gespro-cli/`, cambia la variable `GESPRO` por la ruta donde lo dejaste.
3. En Linux o macOS: `chmod +x .git/hooks/post-commit`. En Windows no hace falta, porque Git for
   Windows lo ejecuta con su propio `sh`.

El hook corre `gespro.py --commit` después de cada commit. Usa el primer Python que de verdad funcione
(`python3`, `python` o `py`), porque en Windows `python3` suele ser un acceso directo a la Microsoft Store.
Si no encuentra el script o Python, si no hay internet o si el token venció, avisa y deja pasar el
commit igual.

Los hooks viven en tu carpeta `.git` y no se suben al repositorio: cada persona del equipo instala el
suyo, con su propio token.

## 3. GitHub Actions: avisar cuando se abre o se integra un PR

`ejemplos/gespro-pr.yml` hace dos cosas:

- **Al abrir un PR:** pasa a `In Review` las tareas mencionadas y les deja el enlace al PR. Cualquier
  `OP#número` del título o la descripción cuenta, así que menciona solo las tareas que el PR resuelve.
- **Al integrarlo:** les deja el enlace con «PR integrado». No las marca `Done`, porque eso le toca a
  quien revisa.

Para instalarlo:

1. Copia `gespro.py` a `scripts/gespro.py` en tu repositorio. Así el workflow no descarga código de
   internet en cada ejecución.
2. Copia `ejemplos/gespro-pr.yml` a `.github/workflows/gespro-pr.yml`.
3. En GitHub, en *Settings > Secrets and variables > Actions*, crea el secreto `GESPRO_API_KEY` y la
   variable `GESPRO_PROJECT`.
4. Agrega `gespro.env` al `.gitignore` del repositorio. El workflow usa el secreto y no necesita ese
   archivo. Para usar el script en tu computador, usa tu copia de `~/gespro-cli` y no la de
   `scripts/`. Si creas `scripts/gespro.env`, tu token puede terminar en un commit.

Ten en cuenta:

- **Permisos:** crear secretos exige ser administrador del repositorio. En los repositorios del curso,
  pídeselo a quien administre la organización.
- **El token:** todo lo que haga el workflow queda a nombre del dueño del token. Lo ideal es un usuario
  de servicio. Si usas el tuyo, todos los comentarios del equipo saldrán con tu nombre.
- **Estados:** `In Review` existe en la instancia de la carrera, pero si tu proyecto usa otros nombres,
  cámbialo. El script muestra los estados válidos cuando uno no existe.
- **Forks:** GitHub no entrega secretos a los PR que vienen de un fork, así que el workflow se salta
  esos PR.

## 4. Integración nativa de GesPro con GitHub

GesPro tiene activada la integración de OpenProject con GitHub: cada tarea puede mostrar una pestaña
GitHub con los PR que la mencionan, su estado y el resultado de la CI. Para eso:

1. En GesPro, quien administre el proyecto activa el módulo GitHub en la configuración del proyecto.
2. Se crea un usuario de GesPro solo para la integración, con un rol que tenga dos permisos: ver tareas
   y agregar notas. Ese usuario es miembro del proyecto y crea su propio token. No uses el tuyo,
   porque el token queda escrito en la URL del webhook.
3. En GitHub, un administrador del repositorio crea un webhook en *Settings > Webhooks*:
   - **Payload URL:** `https://gespro.devhub.cl/webhooks/github?key=TOKEN_DEL_USUARIO_DE_SERVICIO`
   - **Content type:** `application/json`
   - **Eventos:** *Pull requests*, *Issue comments* y *Check runs*. GesPro ignora los demás.

Desde ese momento, cada PR que mencione `OP#número` aparece en la tarea.

Este nivel y el 3 se complementan: el 4 muestra el PR dentro de la tarea y el 3 cambia el estado y deja
comentarios.

## Con un asistente de IA en la terminal

Un asistente de IA que puede ejecutar comandos en tu terminal (por ejemplo Claude Code u otro parecido)
puede usar `gespro.py` por ti. Así lo uso yo:

1. **Instrucciones:** le doy reglas fijas. Están en `ejemplos/instrucciones-ia.md`; pégalas en el
   archivo de instrucciones de tu asistente (por ejemplo `CLAUDE.md` o `AGENTS.md`).
2. **Pedidos en lenguaje normal**, por ejemplo:
   - «¿Qué tareas mías siguen abiertas en el Sprint 2?»
   - «Pasa OP#605 a In progress con 10 % y comenta qué falta.»
   - «Registra 2 horas de ayer en OP#533 por la revisión del PR #7.»
   - «Revisa los commits de esta semana y propónme las horas por tarea antes de cargarlas.»
3. **Revisión antes de escribir:** el asistente corre el comando con `--dry-run`, me muestra el
   resultado y escribe solo cuando lo apruebo.
4. **Commits y PR:** el asistente pone `OP#número` en los mensajes que escribe, y el hook del nivel 2
   hace el resto. Con el hook instalado, un commit con `OP#número` escribe en GesPro sin pasar por el
   `--dry-run`. Por eso el asistente me muestra el mensaje antes de hacer el commit, sobre todo si
   trae una línea `Horas:`.

Cuidados:

- **El token:** no se lo pegues al asistente. Déjalo en `gespro.env`, que el script lee solo.
- **Las horas:** las decides tú. Si le pides una estimación, que muestre de dónde sale y revísala:
  GesPro es la evidencia de tu trabajo individual.
- **Uso de IA:** si tu ramo pide declarar su uso, decláralo.
