# GesPro desde la terminal · Grupo 3

Script para registrar horas y actualizar tareas de GesPro sin abrir el navegador. Funciona con el
proyecto `cinf100-202620-con-8528-grupo_3` de <https://gespro.devhub.cl>.

Es un solo archivo, `gespro.py`, y usa solo la biblioteca estándar de Python 3.9 o superior. No hay
que instalar nada.

## 1. Tu token

Cada persona usa su propio token. GesPro registra las horas a nombre del dueño del token: con el
token de otra persona, tus horas quedan a nombre suyo.

1. Entra a <https://gespro.devhub.cl/my/access_tokens>.
2. En la sección **API**, crea un token y cópialo.
3. Copia `gespro.env.example` como `gespro.env` y pega el token después del `=`:

   ```
   GESPRO_API_KEY=tu_token
   ```

`gespro.env` está en `.gitignore`, así que no se sube al repositorio. No pegues el token en el chat
del grupo ni en un commit. Si se filtra, bórralo en la misma página y crea otro.

Otra opción es dejarlo en la variable de entorno `GESPRO_API_KEY`.

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
`--mis-horas` muestra las horas que ya registraste. Revísalo antes de cargar horas de días
anteriores, para no registrarlas dos veces.

### Actualizar una tarea

El número de la tarea es el que aparece en GesPro, por ejemplo `#620`.

```
python gespro.py --wp 620 --status "In progress"
python gespro.py --wp 620 --percent 50
python gespro.py --wp 620 --hours 2.5 --comment "Carrito: suma y quita líneas"
python gespro.py --wp 620 --hours 1,5 --fecha 2026-09-29
python gespro.py --wp 620 --sprint "Sprint 2"
```

- Las opciones se pueden combinar en una sola llamada.
- Sin `--fecha`, las horas quedan con la fecha de hoy.
- `--comment` queda como comentario en la tarea y también acompaña a las horas.
- Agrega `--dry-run` para ver lo que haría sin escribir nada. Úsalo la primera vez.

Estados válidos: `New`, `In progress`, `In Review`, `Blocked`, `Done` y los demás que muestra GesPro.
Sprints: `Sprint 1`, `Sprint 2`, `Sprint 3`, `Sprint 4` y `Product Backlog`.

### Ver al equipo

```
python gespro.py --report
```

Lista las tareas de cada persona con su estado y el total de horas. No escribe nada.

## 3. Reglas del grupo

- Registra las horas que trabajaste de verdad, el día que las trabajaste. La evaluación es
  individual y GesPro es la evidencia.
- Al tomar una tarea, pásala a `In progress`, súbele el porcentaje y deja un comentario con lo que
  vas a hacer. Al terminarla, otro comentario con lo que quedó hecho y el enlace al PR.
- Marca `Done` solo lo que ya está integrado en `desa`.
- Si una tarea no es tuya, no le registres horas. El script igual te deja, porque a veces hay que
  corregir algo, pero te muestra a quién está asignada.

## 4. Si algo falla

| Mensaje | Qué pasa | Qué hacer |
|---|---|---|
| `Falta tu token` | No encuentra `gespro.env` ni la variable | Revisa que `gespro.env` esté en la misma carpeta que `gespro.py` |
| `HTTP 401` | El token no sirve | Crea uno nuevo en la página de tokens |
| `HTTP 403` con `1010` | Cloudflare bloqueó la petición | No cambies el `USER_AGENT` del script |
| `HTTP 403` | Tu usuario no tiene permiso para eso | Pídeselo a quien administra el proyecto en GesPro |
| `HTTP 409` | Alguien cambió la tarea mientras tanto | Vuelve a ejecutar el comando |
| `No existe el estado` o `el sprint` | El nombre no coincide | El error lista los nombres válidos |
| `no es del proyecto del grupo` | El número no es de nuestro proyecto | Revisa el número en GesPro |

## Cómo funciona

- Usa la API v3 de OpenProject con autenticación básica `apikey:<token>`.
- Todo `PATCH` manda el `lockVersion` de la tarea, que OpenProject exige para no pisar cambios de
  otra persona.
- Las horas se crean con `POST /api/v3/time_entries`; el campo de horas de la tarea no se puede
  editar directo.
- Antes de escribir, comprueba que la tarea sea del proyecto del grupo. Así un número mal tipeado no
  cambia tareas de otro proyecto.
- No sigue redirecciones, para que el token no termine en otro sitio.
