# GesPro desde la terminal

Script para registrar horas y actualizar tareas de GesPro (<https://gespro.devhub.cl>) sin abrir el
navegador. Sirve para cualquier proyecto de la carrera que use GesPro.

Es un solo archivo, `gespro.py`, y usa solo la biblioteca estándar de Python 3.9 o superior. No hay
que instalar nada.

## 1. Configuración

### Descarga

Clona el repositorio en tu carpeta personal. La guía de automatización y los ejemplos usan esa ruta:

```
git clone https://github.com/Mauricio-Villa-Sudo/gespro-cli ~/gespro-cli
```

### Tu token

Cada persona usa su propio token. GesPro registra las horas a nombre del dueño del token: con el
token de otra persona, tus horas quedan a nombre suyo.

1. Entra a <https://gespro.devhub.cl/my/access_tokens>.
2. En la sección **API**, crea un token y cópialo.
3. Copia `gespro.env.example` como `gespro.env` y pega el token después de `GESPRO_API_KEY=`.

`gespro.env` está en `.gitignore`, así que no se sube si trabajas sobre una copia de este
repositorio. No pegues el token en un chat ni en un commit. Si se filtra, bórralo en la misma página
y crea otro.

### Tu proyecto

```
python gespro.py --proyectos
```

Muestra los proyectos que ve tu token. Copia el identificador del tuyo en `gespro.env`:

```
GESPRO_API_KEY=tu_token
GESPRO_PROJECT=cinf100-202620-con-XXXX-grupo_N
```

Si tu token ve un solo proyecto, puedes dejar `GESPRO_PROJECT` vacío. Si participas en más de uno,
usa `--proyecto identificador` para cambiar de proyecto en una llamada.

Las dos líneas también se pueden dar como variables de entorno con el mismo nombre.

## 2. Comandos

Revisa primero que el script funcione. Este comando no se conecta a GesPro:

```
python gespro.py --check
```

### Ver lo tuyo

```
python gespro.py --mis-tareas
python gespro.py --mis-horas
python gespro.py --mis-horas --desde 2026-09-28
```

`--mis-tareas` muestra el número, el estado, el porcentaje y las horas de cada tarea asignada a ti.
`--mis-horas` muestra las horas que ya registraste, cada registro con su `id`. Revísalo antes de
cargar horas de días anteriores, para no registrarlas dos veces.

### Actualizar una tarea

El número de la tarea es el que aparece en GesPro, por ejemplo `#620`.

```
python gespro.py --wp 620 --status "In progress"
python gespro.py --wp 620 --percent 50
python gespro.py --wp 620 --hours 2.5 --comment "Carrito: suma y quita líneas"
python gespro.py --wp 620 --hours 1,5 --fecha 2026-09-29
python gespro.py --wp 620 --sprint "Sprint 2"
python gespro.py --wp 620 --prioridad High
python gespro.py --wp 620 --asignar tomas
python gespro.py --wp 620 --padre 533
```

- Las opciones se pueden combinar en una sola llamada.
- `--padre` deja la tarea dentro de otra, por ejemplo dentro de su historia.
- Sin `--fecha`, las horas quedan con la fecha de hoy. No acepta fechas futuras.
- `--comment` queda como comentario en la tarea y también acompaña a las horas.
- Agrega `--dry-run` para ver lo que haría sin escribir nada. Úsalo la primera vez.

Si escribes mal un estado, un sprint o una prioridad, el error muestra los nombres válidos de tu
proyecto. Las prioridades son `Low`, `Normal`, `High` e `Immediate`.

`--asignar` recibe parte del nombre de un miembro, sin importar mayúsculas ni tildes: `tomas`,
`perez` o `Tomás` sirven. Si el texto calza con más de una persona, el script no cambia nada y
muestra los nombres. Para ver los miembros:

```
python gespro.py --miembros
```

### Crear una tarea

```
python gespro.py --crear "Carrito: quitar ítems" --padre 533 --asignar tomas --dry-run
python gespro.py --crear "Pago con tarjeta" --tipo "User story" --padre 510 --sprint "Sprint 2"
```

Sin `--tipo`, crea una `Task`. También acepta `--descripcion` y las opciones de `--wp` que no son
horas ni comentarios: `--sprint`, `--status`, `--percent`, `--prioridad` y `--asignar`.

Antes de crearla, busca una tarea con el mismo asunto y la misma persona asignada. Si la encuentra,
no crea otra y te da su número. Con otra persona asignada sí la crea, porque tareas como «Planning,
dailies, review y retrospectiva» van una vez por integrante.

### Corregir horas

```
python gespro.py --mis-horas
python gespro.py --editar-horas 372 --hours 1,5 --dry-run
python gespro.py --editar-horas 372 --fecha 2026-09-29 --comment "Revisión del PR 7"
python gespro.py --borrar-horas 372 --dry-run
```

El número es el `id` que muestra `--mis-horas`. `--editar-horas` cambia las horas, la fecha o el
comentario, y `--borrar-horas` borra el registro. Los dos tocan solo registros tuyos y de tu
proyecto. Un registro borrado no se recupera, así que primero usa `--dry-run` para ver cuál es.

### Desde un commit o un PR

Si el mensaje del commit menciona la tarea como `OP#620`, el script la encuentra solo:

```
python gespro.py --commit --dry-run
python gespro.py --commit
python gespro.py --en-texto "Cierra OP#620 y OP#621" --status "In Review"
```

`--commit` lee el último commit y deja en cada tarea mencionada un comentario con el enlace al commit.
Si el mensaje trae una línea `Horas: 1,5` y menciona una sola tarea, también registra esas horas. Si
lo ejecutas dos veces, no repite nada. `--en-texto` aplica las mismas opciones de `--wp` a cada tarea
mencionada en el texto, por ejemplo la descripción de un PR. Si una de las tareas da error, sigue con
las demás.

Para que esto pase solo con un hook de Git, con GitHub Actions o con un asistente de IA, mira
[GUIA-AUTOMATIZACION.md](GUIA-AUTOMATIZACION.md).

### Ver al equipo

```
python gespro.py --report
```

Lista las tareas de cada persona del proyecto con su estado y el total de horas. No escribe nada.

## 3. Buenas prácticas

- Registra las horas que trabajaste de verdad, el día que las trabajaste. GesPro es la evidencia de
  tu trabajo individual.
- Al tomar una tarea, pásala a `In progress` y deja un comentario con lo que vas a hacer. Al
  terminarla, deja otro con lo que quedó hecho y el enlace al PR o al commit.
- No registres horas en tareas de otra persona. El script te deja, porque a veces hay que corregir
  algo, pero te muestra a quién está asignada.

## 4. Si algo falla

| Mensaje | Qué pasa | Qué hacer |
|---|---|---|
| `Falta tu token` | No encuentra `gespro.env` ni la variable | Revisa que `gespro.env` esté en la misma carpeta que `gespro.py` |
| `Tu token ve N proyectos` | No sabe cuál es el tuyo | Completa `GESPRO_PROJECT` o usa `--proyecto` |
| `Tu token no ve el proyecto` | El identificador está mal o no eres miembro | Cópialo tal cual desde `--proyectos` |
| `HTTP 401` | El token no sirve | Crea uno nuevo en la página de tokens |
| `HTTP 403` con `1010` | Cloudflare bloqueó la petición | No cambies el `USER_AGENT` del script |
| `HTTP 403` | Tu usuario no tiene permiso para eso | Pídeselo a quien administra tu proyecto |
| `HTTP 409` | Alguien cambió la tarea mientras tanto | Vuelve a ejecutar el comando |
| `no es de tu proyecto` | El número de tarea es de otro proyecto | Revisa el número en GesPro |
| `coincide con varios` | El texto de `--asignar` calza con más de un miembro | Escribe más del nombre o el apellido |
| `Ya existe #N` | Hay una tarea con ese asunto y esa persona asignada | Cámbiala con `--wp N` |
| `Solo cambio los tuyos` | El registro de horas es de otra persona | Revisa el `id` en `--mis-horas` |

## Cómo funciona

- Usa la API v3 de OpenProject con autenticación básica `apikey:<token>`.
- Todo `PATCH` manda el `lockVersion` de la tarea, que OpenProject exige para no pisar cambios de
  otra persona.
- Las horas se crean con `POST /api/v3/time_entries`; el campo de horas de la tarea no se puede
  editar directo.
- Antes de escribir, comprueba que la tarea sea de tu proyecto. Así un número mal tipeado no cambia
  tareas de otro proyecto.
- No sigue redirecciones, para que el token no termine en otro sitio.

Los cambios de cada versión están en [CHANGELOG.md](CHANGELOG.md). Las mejoras se reciben como issue o pull request.
