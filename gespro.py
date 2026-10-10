#!/usr/bin/env python3
"""Cliente de linea de comandos para GesPro (https://gespro.devhub.cl), el OpenProject de la carrera.

Las opciones estan en `python gespro.py --help` y los ejemplos en el README. El token (GESPRO_API_KEY)
y el proyecto (GESPRO_PROJECT) salen de variables de entorno o de gespro.env, junto a este script, y
las horas quedan a nombre del dueno del token.
"""

import argparse
import base64
import contextlib
import datetime
import http.client
import io
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "https://gespro.devhub.cl"
# Cloudflare responde 403 (error 1010) al User-Agent por defecto de urllib antes de llegar a la API.
USER_AGENT = "gespro-cli/1.5"
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gespro.env")
# Una tarea se menciona como OP#533, igual que en la integracion de OpenProject con GitHub.
REFERENCE = re.compile(r"\bOP#(\d+)\b", re.IGNORECASE)
# Linea "Horas: 1,5" (o "Hours: 1.5") sola en el mensaje del commit.
HOURS_LINE = re.compile(r"^[ \t]*(?:horas|hours)[ \t]*:(.*)$", re.IGNORECASE | re.MULTILINE)
# Accion del reflog de un commit nuevo. amend, rebase y cherry-pick repiten un mensaje que ya se registro.
NEW_COMMIT = {"commit", "commit (initial)", "commit (merge)"}
# La tabla que escribe --puntos va del encabezado a la frase que la cierra. Al repetir, se reemplaza.
POINTS_HEADER = "**Puntos por tarea**"
# Nota que GesPro deja sola en una historia cuando cambia una de sus tareas, en cursiva y con el numero
# de la tarea: "_Actualizado automaticamente cambiando los valores en el paquete de trabajo hijo #579_".
AUTO_NOTE = re.compile(r"^_[^\n]*#\d+_$")
# Prefijo de sprint que algunos equipos ponen en el asunto ("S1 · "). En la tabla de una historia sobra,
# porque sus tareas son del mismo sprint.
SPRINT_PREFIX = re.compile(r"^S\d+\s*·\s*")
# La tabla de --puntos es el encabezado y las filas que lo siguen; la frase que la explica termina con el
# redondeo. Al repetir solo cambian esas dos cosas, porque el equipo escribe sus notas alrededor.
# Una historia sin tareas puede tener el encabezado sin filas, con una nota debajo.
POINTS_TABLE = re.compile(r"\*\*Puntos por tarea\*\*[ \t\r]*(?:\n|$)(?:[ \t\r]*\n)*(?:[ \t]*\|[^\n]*(?:\n|$))*")
ROUNDING = re.compile(r"redondeada a (?:medio punto|un cuarto de punto)\.")


def read_setting(name):
    """Valor de una variable de entorno o, si no esta, de la linea NOMBRE=valor de gespro.env."""
    value = os.environ.get(name, "").strip()
    if value:
        return value
    try:
        # utf-8-sig: el Bloc de notas y PowerShell suelen guardar el archivo con BOM.
        with open(CONFIG_FILE, encoding="utf-8-sig") as handle:
            for line in handle:
                key, separator, value = line.partition("=")
                if separator and key.strip() == name:
                    return value.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return ""


def percent_arg(value):
    number = int(value)
    if not 0 <= number <= 100:
        raise argparse.ArgumentTypeError("tiene que estar entre 0 y 100")
    return number


def hours_arg(value):
    # estimate_arg ya rechaza lo negativo ("PT-1H", que la API acepta igual) y lo que no llega a un minuto.
    number = estimate_arg(value)
    if number > 24:
        raise argparse.ArgumentTypeError("tiene que ser como maximo 24")
    return number


def points_arg(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("tiene que ser un entero mayor que 0")
    return number


def weight_arg(value):
    """'540=8,5' -> (540, 8.5): las horas con que pesa la tarea 540 en --puntos."""
    task, separator, hours = value.partition("=")
    try:
        pair = (int(task), float(hours.replace(",", ".")))
    except ValueError:
        pair = None
    if not separator or pair is None or not math.isfinite(pair[1]) or pair[1] < 0:
        raise argparse.ArgumentTypeError("usa ID=HORAS, por ejemplo 540=8,5")
    return pair


def day_arg(value):
    try:
        return datetime.date.fromisoformat(value).isoformat()
    except ValueError:
        raise argparse.ArgumentTypeError("usa el formato AAAA-MM-DD, por ejemplo 2026-09-29") from None


def date_arg(value):
    day = day_arg(value)
    if day > datetime.date.today().isoformat():
        raise argparse.ArgumentTypeError("no se registran horas en fechas futuras")
    return day


def estimate_arg(value):
    """Horas estimadas de una tarea. A diferencia de --hours, pueden pasar de 24."""
    number = float(value.replace(",", "."))
    if not math.isfinite(number) or round(number * 60) < 1:
        raise argparse.ArgumentTypeError("tiene que ser de al menos un minuto (0,02)")
    return number


def references(text):
    """Numeros de tarea escritos como OP#533 en un texto, sin repetir y en orden."""
    return sorted({int(number) for number in REFERENCE.findall(text or "")} - {0})


def hours_in(text):
    """Horas de la linea "Horas: 1,5" de un mensaje, o None si no la tiene."""
    values = [value.strip() for value in HOURS_LINE.findall(text or "")]
    if not values:
        return None
    if len(values) > 1:
        raise RuntimeError(f"Hay {len(values)} lineas Horas: y solo puede haber una.")
    try:
        return hours_arg(values[0])
    except (argparse.ArgumentTypeError, ValueError):
        raise RuntimeError(f'La linea "Horas: {values[0]}" no sirve: escribe solo el numero, por ejemplo Horas: 1,5.') from None


def github_commit_url(remote, full_hash):
    """Enlace al commit si el remoto es de GitHub. Un remoto con usuario o token en la URL no calza,
    asi esos datos nunca terminan en un comentario."""
    match = re.match(r"^(?:https://github\.com/|git@github\.com:)([\w.-]+/[\w.-]+?)(?:\.git)?/?$", remote or "")
    return f"https://github.com/{match.group(1)}/commit/{full_hash}" if match else None


def iso_duration(minutes):
    hours, rest = divmod(minutes, 60)
    return "PT" + (f"{hours}H" if hours else "") + (f"{rest}M" if rest or not hours else "")


def hours_from_iso(duration):
    """'PT2H30M' -> 2.5. OpenProject devuelve las horas en formato ISO 8601, y a veces con semanas y dias
    de 24 horas: 'P1DT2H' son 26."""
    match = re.fullmatch(r"P(?:([\d.]+)W)?(?:([\d.]+)D)?(?:T(?:([\d.]+)H)?(?:([\d.]+)M)?(?:([\d.]+)S)?)?", duration or "")
    if not match:
        return 0.0
    weeks, days, hours, minutes, seconds = (float(value or 0) for value in match.groups())
    return round(weeks * 168 + days * 24 + hours + minutes / 60 + seconds / 3600, 2)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """urllib copia el header Authorization al seguir una redireccion, aunque cambie de host.
    Asi una redireccion falla en vez de llevarse el token a otro lado."""

    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def request(method, path, token, payload=None):
    # Solo rutas relativas: el token nunca sale hacia un host que no sea BASE_URL.
    url = BASE_URL + path
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    credentials = base64.b64encode(f"apikey:{token}".encode()).decode("ascii")
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", f"Basic {credentials}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", USER_AGENT)
    try:
        with _OPENER.open(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:400]
        raise RuntimeError(f"{method} {path} -> HTTP {error.code}: {detail}") from None
    except (OSError, http.client.HTTPException) as error:
        # OSError incluye URLError, los timeouts y la conexion que se corta a mitad de la respuesta.
        raise RuntimeError(f"{method} {path} -> sin conexion: {getattr(error, 'reason', None) or error}") from None


def with_filters(path, filters, **extra):
    params = {"filters": json.dumps(filters, ensure_ascii=False), **extra}
    return f"{path}?{urllib.parse.urlencode(params)}"


def elements(payload):
    return payload.get("_embedded", {}).get("elements", [])


def visible_projects(token):
    return elements(request("GET", "/api/v3/projects?pageSize=500", token))


def find_project(token, wanted):
    """El proyecto pedido (identificador o numero) o, si no se pidio, el unico que ve el token."""
    if wanted:
        # Esta instancia no acepta el filtro "identifier" sobre /projects, pero si la ruta directa.
        try:
            return request("GET", f"/api/v3/projects/{urllib.parse.quote(wanted, safe='')}", token)
        except RuntimeError as error:
            # Solo un 403 o un 404 dicen eso. Un 401 o la falta de red se muestran tal cual.
            if not re.search(r"-> HTTP 40[34]:", str(error)):
                raise
            raise RuntimeError(f'Tu token no ve el proyecto "{wanted}". Revisa el nombre con --proyectos.') from None
    projects = visible_projects(token)
    if len(projects) == 1:
        return projects[0]
    raise RuntimeError(
        f"Tu token ve {len(projects)} proyectos. Elige uno con GESPRO_PROJECT=... en gespro.env "
        "o con --proyecto. Los ves con --proyectos."
    )


def list_projects():
    token = require_token()
    print("  identificador (va en GESPRO_PROJECT)         nombre")
    for project in visible_projects(token):
        print(f"  {project['identifier']:<45} {project['name']}")
    return 0


def me(token):
    return request("GET", "/api/v3/users/me", token)


def plain(text):
    """Minusculas y sin tildes: "Tomás" y "tomas" se comparan igual."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).lower().strip()


def project_members(token, project_id):
    """Los miembros del proyecto con su nombre, su enlace y sus roles."""
    path = with_filters("/api/v3/memberships", [{"project": {"operator": "=", "values": [str(project_id)]}}], pageSize=200)
    return [{"name": m["_links"]["principal"]["title"], "href": m["_links"]["principal"]["href"],
             "roles": [role.get("title") for role in m["_links"].get("roles", [])]}
            for m in elements(request("GET", path, token))]


def find_member(members, text):
    """El unico miembro cuyo nombre contiene el texto; si hay cero o varios, el error los muestra."""
    matches = [m for m in members if plain(text) in plain(m["name"])]
    if len(matches) == 1:
        return matches[0]
    options = ", ".join(m["name"] for m in (matches or members))
    problem = "coincide con varios" if matches else "no coincide con nadie"
    raise RuntimeError(f'"{text}" {problem}. Miembros: {options}')


def list_members(wanted):
    token = require_token()
    project = find_project(token, wanted)
    print(f"{project['name']}\n")
    for member in project_members(token, project["id"]):
        print(f"  {member['href'].rsplit('/', 1)[-1]:<5} {member['name']}")
    return 0


def find_by_name(token, path, name, what):
    items = elements(request("GET", path, token))
    for item in items:
        if item["name"].strip().lower() == name.strip().lower():
            return item["id"]
    raise RuntimeError(f'No existe {what} "{name}". Opciones: {", ".join(item["name"] for item in items)}')


def patch_work_package(token, work_package, **fields):
    """PATCH con el lockVersion que OpenProject exige para no pisar cambios de otra persona."""
    payload = {"lockVersion": work_package["lockVersion"], **fields}
    return request("PATCH", f"/api/v3/work_packages/{work_package['id']}", token, payload)


def log_time(token, work_package, minutes, spent_on, comment):
    # "user" es de solo lectura: la entrada queda siempre a nombre del dueno del token.
    payload = {
        "hours": iso_duration(minutes),
        "spentOn": spent_on,
        "comment": {"raw": comment or "Trabajo registrado."},
        "_links": {
            "entity": {"href": f"/api/v3/work_packages/{work_package['id']}"},
            "project": {"href": work_package["_links"]["project"]["href"]},
        },
    }
    return request("POST", "/api/v3/time_entries", token, payload)


def already_noted(token, work_package_id, tag):
    """True si la tarea ya tiene un comentario o una entrada de horas con esa marca (el hash del commit).
    Asi, correr el hook dos veces no repite nada."""
    activities = elements(request("GET", f"/api/v3/work_packages/{work_package_id}/activities", token))
    if any(tag in ((a.get("comment") or {}).get("raw") or "") for a in activities):
        return True
    path = with_filters("/api/v3/time_entries", [{"entity_id": {"operator": "=", "values": [str(work_package_id)]}}], pageSize=500)
    return any(tag in ((e.get("comment") or {}).get("raw") or "") for e in elements(request("GET", path, token)))


def my_tasks(wanted):
    token = require_token()
    project_id = find_project(token, wanted)["id"]
    user = me(token)
    # filters con status "*": sin el, la API esconde las tareas cerradas.
    path = with_filters(
        f"/api/v3/projects/{project_id}/work_packages",
        [{"assignee": {"operator": "=", "values": ["me"]}}, {"status": {"operator": "*", "values": []}}],
        pageSize=500,
        sortBy=json.dumps([["id", "asc"]]),
    )
    tasks = elements(request("GET", path, token))
    print(f"{user.get('name')}: {len(tasks)} tareas en GesPro\n")
    print("  #      estado       prioridad    %    horas  tarea")
    for item in tasks:
        print(
            "  #%-5s %-12s %-10s %3s%%  %5.1f  %s"
            % (
                item["id"],
                item["_links"]["status"]["title"][:12],
                item["_links"]["priority"]["title"][:10],
                item.get("percentageDone") or 0,
                hours_from_iso(item.get("spentTime")),
                item["subject"][:70],
            )
        )
    return 0


def project_time_entries(token, project_id, since=None, only_mine=False):
    filters = [{"project_id": {"operator": "=", "values": [str(project_id)]}}]
    if only_mine:
        filters.append({"user_id": {"operator": "=", "values": ["me"]}})
    if since:
        filters.append({"spent_on": {"operator": "<>d", "values": [since, datetime.date.today().isoformat()]}})
    path = with_filters("/api/v3/time_entries", filters, pageSize=500, sortBy=json.dumps([["spent_on", "asc"]]))
    payload = request("GET", path, token)
    entries = elements(payload)
    if payload.get("total", 0) > len(entries):
        print(f"Aviso: hay {payload['total']} registros y muestro {len(entries)}. Acota con --desde.")
    return entries


def my_hours(wanted, since=None):
    token = require_token()
    project_id = find_project(token, wanted)["id"]
    entries = project_time_entries(token, project_id, since, only_mine=True)
    print(ENTRY_HEADER)
    for entry in entries:
        print(entry_line(entry))
    total = sum(hours_from_iso(entry.get("hours")) for entry in entries)
    print(f"\n  Total: {total:.2f} h en {len(entries)} registros")
    return 0


ENTRY_HEADER = "  id     fecha       horas  #tarea  comentario"


def entry_line(entry):
    """Un registro de horas en una linea. El id es el que piden --editar-horas y --borrar-horas."""
    task = entry["_links"].get("entity") or entry["_links"].get("workPackage") or {}
    task_id = (task.get("href") or "").rsplit("/", 1)[-1]
    comment = ((entry.get("comment") or {}).get("raw") or "").replace("\n", " ")
    hours = hours_from_iso(entry.get("hours"))
    return f"  {entry['id']:<6} {entry['spentOn']}  {hours:5.2f}  #{task_id:<5}  {comment[:60]}"


def own_entry(token, entry_id, project_id):
    """El registro de horas, si es tuyo y de tu proyecto. Los de otra persona no se tocan."""
    entry = request("GET", f"/api/v3/time_entries/{entry_id}", token)
    if entry["_links"]["user"]["href"] != me(token)["_links"]["self"]["href"]:
        owner = entry["_links"]["user"].get("title") or "otra persona"
        raise RuntimeError(f"El registro {entry_id} es de {owner}. Solo cambio los tuyos.")
    if not entry["_links"]["project"]["href"].endswith(f"/projects/{project_id}"):
        raise RuntimeError(f"El registro {entry_id} no es de tu proyecto. No toco nada.")
    print(ENTRY_HEADER)
    print(entry_line(entry))
    return entry


def edit_hours(wanted, entry_id, hours=None, day=None, comment=None, dry_run=False):
    """Corrige las horas, la fecha o el comentario de un registro tuyo."""
    shown = {"horas": hours, "fecha": day, "comentario": comment}
    if not any(shown.values()):
        raise RuntimeError("Dime que cambiar: --hours, --fecha o --comment.")
    token = require_token()
    own_entry(token, entry_id, find_project(token, wanted)["id"])
    if dry_run:
        for label, value in shown.items():
            if value:
                print(f"  (simulacion) pondria {label}: {value}")
        return 0
    # Los registros de horas no tienen lockVersion, a diferencia de las tareas.
    changes = {}
    if hours:
        changes["hours"] = iso_duration(round(hours * 60))
    if day:
        changes["spentOn"] = day
    if comment:
        changes["comment"] = {"raw": comment}
    print("Quedo asi:")
    print(entry_line(request("PATCH", f"/api/v3/time_entries/{entry_id}", token, changes)))
    return 0


def delete_hours(wanted, entry_id, dry_run=False):
    """Borra un registro de horas tuyo. No se puede deshacer, por eso antes lo muestra."""
    token = require_token()
    own_entry(token, entry_id, find_project(token, wanted)["id"])
    if dry_run:
        print("  (simulacion) borraria este registro")
        return 0
    # En la terminal pide confirmacion: un numero mal escrito borraria otro registro tuyo.
    if sys.stdin.isatty() and plain(input("  Escribe si para borrarlo: ")) != "si":
        print("  No borre nada.")
        return 0
    request("DELETE", f"/api/v3/time_entries/{entry_id}", token)
    print("  registro borrado")
    return 0


def report(wanted):
    """Estado del proyecto por persona. No escribe nada."""
    token = require_token()
    project = find_project(token, wanted)
    project_id = project["id"]
    print(f"{project['name']}\n")
    path = with_filters(f"/api/v3/projects/{project_id}/work_packages", [], pageSize=500)
    rows = elements(request("GET", path, token))
    by_person = {}
    for item in rows:
        who = (item["_links"].get("assignee") or {}).get("title") or "SIN ASIGNAR"
        by_person.setdefault(who, []).append(item)
    for who in sorted(by_person, key=lambda name: -len(by_person[name])):
        items = by_person[who]
        spent = sum(hours_from_iso(i.get("spentTime")) for i in items)
        done = sum(1 for i in items if i["_links"]["status"]["title"] in ("Done", "Closed"))
        print(f"== {who}: {len(items)} tareas, {done} terminadas, {spent:.1f} h registradas")
        for item in sorted(items, key=lambda i: i["id"]):
            print(
                "  #%-5s %-12s %3s%%  %s"
                % (item["id"], item["_links"]["status"]["title"][:12], item.get("percentageDone") or 0, item["subject"][:60])
            )
        print()
    return 0


def split_sprints(versions, today):
    """El sprint en curso (o el proximo, si hoy cae entre dos) y los que ya terminaron.
    Las versiones sin fechas, como el Product Backlog, no cuentan."""
    dated = sorted((v for v in versions if v.get("startDate") and v.get("endDate")), key=lambda v: v["startDate"])
    pending = [v for v in dated if v["endDate"] >= today]
    return (pending[0] if pending else None), [v for v in dated if v["endDate"] < today]


def sprint_status(wanted):
    """El sprint en curso por persona, tus tareas abiertas y lo que quedo abierto de sprints anteriores."""
    token = require_token()
    project_id = find_project(token, wanted)["id"]
    today = datetime.date.today().isoformat()
    current, ended = split_sprints(elements(request("GET", f"/api/v3/projects/{project_id}/versions", token)), today)
    closed = {s["_links"]["self"]["href"] for s in elements(request("GET", "/api/v3/statuses", token)) if s.get("isClosed")}
    my_href = me(token)["_links"]["self"]["href"]
    tasks_path = f"/api/v3/projects/{project_id}/work_packages"
    if current:
        start, end = current["startDate"], current["endDate"]
        left = (datetime.date.fromisoformat(end) - datetime.date.today()).days
        when = f"empieza el {start}" if start > today else ("termina hoy" if left == 0 else f"termina en {left} dias")
        print(f"{current['name']}: del {start} al {end}, {when}\n")
        filters = [{"version": {"operator": "=", "values": [str(current["id"])]}}, {"status": {"operator": "*", "values": []}}]
        tasks = elements(request("GET", with_filters(tasks_path, filters, pageSize=500), token))
        counts = {}
        for item in tasks:
            who = (item["_links"].get("assignee") or {}).get("title") or "SIN ASIGNAR"
            is_closed = item["_links"]["status"]["href"] in closed
            open_count, closed_count = counts.get(who, (0, 0))
            counts[who] = (open_count + (not is_closed), closed_count + is_closed)
        print(f"  {'persona':<32} abiertas  cerradas")
        for who in sorted(counts):
            print(f"  {who[:32]:<32} {counts[who][0]:>8}  {counts[who][1]:>8}")
        mine = [i for i in tasks if (i["_links"].get("assignee") or {}).get("href") == my_href
                and i["_links"]["status"]["href"] not in closed]
        print(f"\nTus tareas abiertas: {len(mine)}")
        for item in mine:
            print(f"  #{item['id']:<5} {item['_links']['status']['title'][:12]:<12} {item.get('percentageDone') or 0:>3}%  {item['subject'][:60]}")
    else:
        print("No hay sprints con fechas por delante.")
    if ended:
        filters = [{"version": {"operator": "=", "values": [str(v["id"]) for v in ended]}}, {"status": {"operator": "o", "values": []}}]
        left_over = elements(request("GET", with_filters(tasks_path, filters, pageSize=500), token))
        print(f"\nSiguen abiertas de sprints que ya terminaron: {len(left_over)}")
        for item in left_over:
            who = (item["_links"].get("assignee") or {}).get("title") or "SIN ASIGNAR"
            print(f"  #{item['id']:<5} {item['_links']['version']['title'][:10]:<10} {item['_links']['status']['title'][:12]:<12} "
                  f"{who[:22]:<22} {item['subject'][:40]}")
    return 0


def hours_by_person(entries):
    """Horas y dias con registro de cada persona."""
    people = {}
    for entry in entries:
        who = entry["_links"]["user"].get("title") or "?"
        total, days = people.get(who, (0.0, frozenset()))
        people[who] = (total + hours_from_iso(entry.get("hours")), days | {entry["spentOn"]})
    return people


def team_hours(wanted, since=None):
    """Horas de cada integrante desde una fecha (por defecto, el lunes), y quien no ha registrado."""
    token = require_token()
    project_id = find_project(token, wanted)["id"]
    today = datetime.date.today()
    since = since or (today - datetime.timedelta(days=today.weekday())).isoformat()
    people = hours_by_person(project_time_entries(token, project_id, since))
    print(f"Horas del {since} al {today.isoformat()}\n")
    print(f"  {'persona':<32} horas  dias  ultimo dia")
    for who, (total, days) in sorted(people.items(), key=lambda item: -item[1][0]):
        print(f"  {who[:32]:<32} {total:5.2f}  {len(days):>4}  {max(days)}")
    # Solo el rol Developer, para no contar al docente ni al ayudante; si el proyecto no lo usa, todos.
    members = project_members(token, project_id)
    team = [m["name"] for m in members if "Developer" in m["roles"]] or [m["name"] for m in members]
    missing = sorted(set(team) - set(people))
    print(f"\n  Sin horas en estas fechas: {', '.join(missing) if missing else 'nadie'}")
    return 0



def task_in_project(token, wp_id, project_id):
    """La tarea, si es del proyecto. Un numero mal tipeado no puede terminar escribiendo en otro proyecto."""
    work_package = request("GET", f"/api/v3/work_packages/{wp_id}", token)
    if not work_package["_links"]["project"]["href"].endswith(f"/projects/{project_id}"):
        raise RuntimeError(f"#{wp_id} no es de tu proyecto. No toco nada.")
    return work_package


def resolve_links(token, project_id, sprint=None, status=None, priority=None, assignee_text=None, parent=None):
    """Los enlaces de la API para los nombres escritos, y como mostrarlos. Busca todo antes de escribir,
    para que un nombre mal escrito no deje la tarea a medio cambiar."""
    links, shown = {}, {}
    if sprint:
        version_id = find_by_name(token, f"/api/v3/projects/{project_id}/versions", sprint, "el sprint")
        links["version"] = {"href": f"/api/v3/versions/{version_id}"}
        shown["sprint"] = sprint
    if status:
        status_id = find_by_name(token, "/api/v3/statuses", status, "el estado")
        links["status"] = {"href": f"/api/v3/statuses/{status_id}"}
        shown["estado"] = status
    if priority:
        priority_id = find_by_name(token, "/api/v3/priorities", priority, "la prioridad")
        links["priority"] = {"href": f"/api/v3/priorities/{priority_id}"}
        shown["prioridad"] = priority
    if assignee_text:
        member = find_member(project_members(token, project_id), assignee_text)
        links["assignee"] = {"href": member["href"]}
        shown["asignada a"] = member["name"]
    if parent:
        mother = task_in_project(token, parent, project_id)
        links["parent"] = {"href": f"/api/v3/work_packages/{parent}"}
        shown["dentro de"] = f"#{parent} {mother['subject']}"
    return links, shown


def value_fields(percent=None, estimate=None, start=None, due=None):
    """Los campos de la tarea que no son enlaces, y como mostrarlos."""
    if start and due and start > due:
        raise RuntimeError(f"--inicio {start} queda despues de --fin {due}.")
    fields, shown = {}, {}
    if percent is not None:
        fields["percentageDone"] = percent
        shown["progreso"] = f"{percent}%"
    if estimate:
        fields["estimatedTime"] = iso_duration(round(estimate * 60))
        shown["estimado"] = f"{points_text(estimate)} h"
    if start:
        fields["startDate"] = shown["inicio"] = start
    if due:
        fields["dueDate"] = shown["fin"] = due
    return fields, shown


def update_work_package(wanted, wp_id, sprint=None, status=None, percent=None, hours=None, day=None, comment=None,
                        priority=None, assignee_text=None, parent=None, estimate=None, start=None, due=None,
                        dry_run=False, time_comment=None, tag=None):
    token = require_token()
    project_id = find_project(token, wanted)["id"]
    work_package = task_in_project(token, wp_id, project_id)
    assignee = (work_package["_links"].get("assignee") or {}).get("title") or "sin asignar"
    print(f"#{wp_id}: {work_package['subject']} (asignada a {assignee}, prioridad {work_package['_links']['priority']['title']})")
    spent_on = day or datetime.date.today().isoformat()
    links, shown = resolve_links(token, project_id, sprint, status, priority, assignee_text, parent)
    fields, values_shown = value_fields(percent, estimate, start, due)
    if links:
        fields["_links"] = links
    shown.update(values_shown)

    if tag and already_noted(token, wp_id, tag):
        print(f"  ya tiene registrado {tag}; no repito el comentario ni las horas")
        comment, hours = None, None

    if dry_run:
        for label, value in shown.items():
            print(f"  (simulacion) pondria {label}: {value}")
        if comment:
            print(f"  (simulacion) pondria comentario: {comment}")
        if hours:
            print(f"  (simulacion) registraria {hours} h el {spent_on}")
        return 0

    # Un solo PATCH: si GesPro rechaza un campo (un estado que tu rol no puede usar), no cambia ninguno.
    if fields:
        patch_work_package(token, work_package, **fields)
        for label, value in shown.items():
            print(f"  {label}: {value}")
    if hours:
        log_time(token, work_package, round(hours * 60), spent_on, time_comment or comment)
        print(f"  horas registradas: {hours} el {spent_on}")
    if comment:
        request("POST", f"/api/v3/work_packages/{wp_id}/activities", token, {"comment": {"raw": comment}})
        print("  comentario agregado")
    return 0


def project_tasks(token, project_id, filters=()):
    """Las tareas del proyecto, tambien las cerradas, ordenadas por numero."""
    filters = [*filters, {"status": {"operator": "*", "values": []}}]
    path = with_filters(f"/api/v3/projects/{project_id}/work_packages", filters, pageSize=500,
                        sortBy=json.dumps([["id", "asc"]]))
    payload = request("GET", path, token)
    tasks = elements(payload)
    if payload.get("total", 0) > len(tasks):
        print(f"Aviso: hay {payload['total']} tareas y solo reviso {len(tasks)}.")
    return tasks


def tasks_matching(token, project_id, text):
    """Las tareas cuyo asunto contiene el texto. El filtro de GesPro distingue tildes ("pagina" no
    encuentra "Página"), asi que se compara aca."""
    return [task for task in project_tasks(token, project_id) if plain(text) in plain(task["subject"])]


def same_task(token, project_id, subject, assignee_link):
    """Una tarea con el mismo asunto y la misma persona asignada. El asunto solo no basta: tareas como
    "Planning, dailies, review y retrospectiva" existen una vez por integrante."""
    wanted_href = (assignee_link or {}).get("href")
    for item in tasks_matching(token, project_id, subject):
        if plain(item["subject"]) == plain(subject) and (item["_links"].get("assignee") or {}).get("href") == wanted_href:
            return item
    return None


def search_tasks(wanted, text):
    token = require_token()
    found = tasks_matching(token, find_project(token, wanted)["id"], text)
    print(f'{len(found)} tareas con "{text}" en el asunto\n')
    for item in found:
        links = item["_links"]
        who = (links.get("assignee") or {}).get("title") or "sin asignar"
        print(f"  #{item['id']:<5} {links['type']['title'][:10]:<10} {links['status']['title'][:12]:<12} "
              f"{who[:22]:<22} {item['subject'][:60]}")
    return 0


def show_task(wanted, wp_id):
    """Todo lo de una tarea en un lugar: datos, descripcion, tareas dentro, PR enlazados y comentarios."""
    token = require_token()
    project_id = find_project(token, wanted)["id"]
    task = task_in_project(token, wp_id, project_id)
    links = task["_links"]

    def title(name):
        return (links.get(name) or {}).get("title") or "-"

    print(f"#{wp_id} {title('type')}: {task['subject']}")
    print(f"  estado {title('status')}, {task.get('percentageDone') or 0}%, prioridad {title('priority')}")
    print(f"  asignada a {title('assignee')}, sprint {title('version')}, dentro de {title('parent')}")
    print(f"  del {task.get('startDate') or '-'} al {task.get('dueDate') or '-'}, "
          f"{points_text(hours_from_iso(task.get('estimatedTime')))} h estimadas, "
          f"{points_text(hours_from_iso(task.get('spentTime')))} h registradas")
    if "storyPoints" in task:
        print(f"  puntos de historia: {task.get('storyPoints') or '-'}")
    description = ((task.get("description") or {}).get("raw") or "").strip().splitlines()
    if description:
        print("\n" + "\n".join(description[:15]) + ("\n  (sigue...)" if len(description) > 15 else ""))
    children = project_tasks(token, project_id, [{"parent": {"operator": "=", "values": [str(wp_id)]}}])
    if children:
        print(f"\nTareas dentro: {len(children)}")
        for child in children:
            print(f"  #{child['id']:<5} {child['_links']['status']['title'][:12]:<12} "
                  f"{points_text(hours_from_iso(child.get('estimatedTime'))):>4} h  {child['subject'][:60]}")
    for pull in elements(request("GET", f"/api/v3/work_packages/{wp_id}/github_pull_requests", token)):
        print(f"\nPR: {pull.get('title')} ({pull.get('state')}) {pull.get('htmlUrl') or ''}")
    names = {m["href"]: m["name"] for m in project_members(token, project_id)}
    comments = [a for a in elements(request("GET", f"/api/v3/work_packages/{wp_id}/activities", token))
                if ((a.get("comment") or {}).get("raw") or "").strip()
                and not AUTO_NOTE.match(a["comment"]["raw"].strip())]
    print(f"\nComentarios: {len(comments)}" + (", los ultimos 3:" if len(comments) > 3 else ""))
    for activity in comments[-3:]:
        who = names.get((activity["_links"].get("user") or {}).get("href"), "?")
        text = activity["comment"]["raw"].strip().replace("\n", " ")
        print(f"  {activity['createdAt'][:10]} {who}: {text[:110]}")
    return 0


def create_work_package(wanted, subject, kind=None, description=None, percent=None, estimate=None, start=None,
                        due=None, dry_run=False, **names):
    """Crea una tarea, asignada a ti si no dices a quien. Si ya hay una con el mismo asunto y la misma
    persona asignada, no crea otra."""
    subject = (subject or "").strip()
    if not subject:
        raise RuntimeError("La tarea necesita un asunto.")
    kind = kind or "Task"
    token = require_token()
    project_id = find_project(token, wanted)["id"]
    type_id = find_by_name(token, f"/api/v3/projects/{project_id}/types", kind, "el tipo")
    links, shown = resolve_links(token, project_id, **names)
    if "assignee" not in links:
        user = me(token)
        links["assignee"] = {"href": user["_links"]["self"]["href"]}
        shown["asignada a"] = user["name"]
    twin = same_task(token, project_id, subject, links["assignee"])
    if twin:
        print(f"Ya existe #{twin['id']}: {twin['subject']}. No creo otra; cambiala con --wp {twin['id']}.")
        return 0
    links["type"] = {"href": f"/api/v3/types/{type_id}"}
    values, values_shown = value_fields(percent, estimate, start, due)
    payload = {"subject": subject, "_links": links, **values}
    shown.update(values_shown)
    if description:
        payload["description"] = {"raw": description}
    if dry_run:
        print(f"(simulacion) crearia {kind}: {subject}")
    else:
        created = request("POST", f"/api/v3/projects/{project_id}/work_packages", token, payload)
        print(f"#{created['id']} creada: {created['subject']}")
    for label, value in shown.items():
        print(f"  {label}: {value}")
    return 0


def update_references(wanted, text, hours=None, **changes):
    """Aplica los cambios a cada tarea mencionada como OP#numero en el texto."""
    ids = references(text)
    if not ids:
        print("El texto no menciona tareas como OP#numero. No hago nada.")
        return 0
    if hours and len(ids) > 1:
        listed = ", ".join(f"OP#{i}" for i in ids)
        raise RuntimeError(f"Hay {len(ids)} tareas ({listed}) y no se como repartir las horas. "
                           "Menciona una sola o registra las horas con --wp.")
    # Una referencia mala (OP#999, o una tarea de otro proyecto) no frena a las demas.
    failed = 0
    for wp_id in ids:
        try:
            update_work_package(wanted, wp_id, hours=hours, **changes)
        except RuntimeError as error:
            print(f"#{wp_id}: Error: {error}")
            failed += 1
    return 1 if failed else 0


def apportion(units, weights, at_least_one=False, strict=False):
    """Reparte units enteros segun weights. La suma calza exacto, las tareas con el mismo peso reciben lo
    mismo y una con mas peso nunca recibe menos que otra con menos. Si no hay un reparto asi, con strict
    devuelve None y sin strict desempata por resto mayor."""
    total = sum(weights)
    if total <= 0:
        return [0] * len(weights)
    raw = [units * w / total for w in weights]
    got = [max(1, int(r)) if at_least_one and w > 0 else int(r) for r, w in zip(raw, weights)]
    while sum(got) > units:
        k = max((k for k in range(len(got)) if got[k] > 1), key=lambda k: got[k] - raw[k])
        got[k] -= 1
    left = units - sum(got)
    groups = sorted({w: [k for k, other in enumerate(weights) if other == w] for w in weights if w > 0}.items())
    best, best_error = None, None
    # Prueba todas las combinaciones de grupos de igual peso; alcanza para unos 15 pesos distintos.
    for mask in range(1 << len(groups)):
        chosen = [k for i, (_, ks) in enumerate(groups) if mask >> i & 1 for k in ks]
        if len(chosen) != left:
            continue
        trial = [g + (k in chosen) for k, g in enumerate(got)]
        values = [trial[ks[0]] for _, ks in groups]
        if any(trial[k] != trial[ks[0]] for _, ks in groups for k in ks) or any(a > b for a, b in zip(values, values[1:])):
            continue
        error = sum((t - r) ** 2 for t, r in zip(trial, raw))
        if best_error is None or error < best_error:
            best, best_error = trial, error
    if best is not None or strict:
        return best
    while sum(got) < units:
        k = max((k for k in range(len(got)) if weights[k] > 0), key=lambda k: raw[k] - got[k])
        got[k] += 1
    return got


def split_points(points, weights):
    """Puntos por tarea en pasos de 0,5, o de 0,25 si con medios puntos no sale un reparto parejo.
    Cada tarea con peso recibe al menos un paso. Devuelve los puntos y el paso."""
    positive = sum(1 for w in weights if w > 0)
    if positive <= points * 2:
        halves = apportion(round(points * 2), weights, at_least_one=True, strict=True)
        if halves is not None:
            return [h / 2 for h in halves], 0.5
    # Con cuartos, apportion prueba primero el reparto parejo y solo si no existe desempata.
    if positive > points * 4:
        raise RuntimeError(f"{positive} tareas no caben en {points} puntos: cada una necesita al menos un cuarto. "
                           "Sube --total o deja fuera alguna con --peso ID=0.")
    return [u * 0.25 for u in apportion(round(points / 0.25), weights, at_least_one=True)], 0.25


def points_text(value):
    return f"{value:g}".replace(".", ",")


def points_block(tasks, parts, shares, step):
    rounding = "medio punto" if step == 0.5 else "un cuarto de punto"
    lines = [POINTS_HEADER, "", "| Tarea | Puntos | Abarca |", "|---|---:|---:|"]
    for task, part, share in zip(tasks, parts, shares):
        subject = SPRINT_PREFIX.sub("", task["subject"]).replace("|", "/")
        lines.append(f"| #{task['id']} {subject} | {points_text(part)} | {share}% |")
    lines += [f"| **Total** | **{points_text(sum(parts))}** | **100%** |", "",
              f"Cada tarea recibe la parte que le toca segun sus horas estimadas (columna Abarca), redondeada a {rounding}."]
    return "\n".join(lines)


def with_points_block(description, block):
    """La descripcion con la tabla nueva, al final si no tenia. Si ya tenia una, cambia solo la tabla y el
    redondeo de la frase que la explica: lo escrito antes, despues o en esa misma frase queda igual."""
    old = POINTS_TABLE.search(description)
    if not old:
        return f"{description.rstrip()}\n\n{block}" if description.strip() else block
    table = POINTS_TABLE.match(block).group(0).rstrip("\n")
    rounding = ROUNDING.search(block).group(0)
    after = description[old.end():]
    if ROUNDING.search(after):
        after = ROUNDING.sub(lambda _: rounding, after, count=1)
    else:
        rest = after.lstrip("\n")
        after = f"\n{block[len(table):].strip()}" + (f"\n\n{rest}" if rest else "")
    return f"{description[:old.start()]}{table}\n{after}"



def task_weights(story_id, tasks, weights):
    """Las horas de cada tarea: las de --peso o, si no, las estimadas en GesPro."""
    weights = dict(weights or [])
    unknown = sorted(set(weights) - {task["id"] for task in tasks})
    if unknown:
        listed = ", ".join(f"#{i}" for i in unknown)
        raise RuntimeError(f"--peso habla de tareas que no estan dentro de #{story_id}: {listed}.")
    hours = [weights.get(task["id"], hours_from_iso(task.get("estimatedTime"))) for task in tasks]
    missing = [task["id"] for task, h in zip(tasks, hours) if not h and task["id"] not in weights]
    if missing:
        listed = ", ".join(f"#{i}" for i in missing)
        raise RuntimeError(f"Sin horas estimadas: {listed}. Ponlas en GesPro (campo Trabajo) o usa --peso {missing[0]}=3.")
    if not sum(hours):
        raise RuntimeError("Todas las tareas pesan 0; asi no hay como repartir.")
    return hours


def story_points(wanted, story_id, total=None, weights=None, dry_run=False):
    """Reparte los puntos de una historia entre sus tareas segun sus horas y deja la tabla en la
    descripcion de la historia. Si ya tenia una tabla de --puntos, la reemplaza."""
    token = require_token()
    project_id = find_project(token, wanted)["id"]
    story = task_in_project(token, story_id, project_id)
    if "storyPoints" not in story:
        kind = story["_links"]["type"]["title"]
        raise RuntimeError(f"#{story_id} es de tipo {kind}; solo las User story tienen puntos de historia.")
    points = total or story.get("storyPoints")
    if not points:
        raise RuntimeError(f"#{story_id} no tiene puntos de historia. Daselos con --total, por ejemplo --total 5.")
    tasks = project_tasks(token, project_id, [{"parent": {"operator": "=", "values": [str(story_id)]}}])
    if not tasks:
        raise RuntimeError(f"#{story_id} no tiene tareas. Mete una con --wp ID --padre {story_id}.")
    hours = task_weights(story_id, tasks, weights)
    parts, step = split_points(points, hours)
    block = points_block(tasks, parts, apportion(100, hours), step)
    print(f"#{story_id}: {story['subject']} ({points} puntos)\n\n{block}\n")
    if dry_run:
        print("(simulacion) no escribo nada")
        return 0
    description = (story.get("description") or {}).get("raw") or ""
    patch_work_package(token, story, storyPoints=points, description={"raw": with_points_block(description, block)})
    print(f"#{story_id} queda con {points} puntos y la tabla en su descripcion.")
    return 0


def git(*args, check=True):
    # Git escribe UTF-8; sin encoding, Windows lo leeria como cp1252 y romperia las tildes.
    result = subprocess.run(["git", *args], capture_output=True, encoding="utf-8", errors="replace", check=check)
    return result.stdout.strip()


def hours_from_message(message, action):
    """Horas de la linea "Horas:" del commit, o None con un aviso si no corresponde registrarlas."""
    try:
        hours = hours_in(message)
    except RuntimeError as error:
        print(f"Aviso: {error} No registro horas; hazlo con --wp N --hours X.")
        return None
    if hours and action and action not in NEW_COMMIT:
        print(f"Aviso: el commit viene de un {action}; no registro sus horas para no duplicarlas.")
        return None
    if hours and len(references(message)) > 1:
        print("Aviso: el commit menciona varias tareas; comento en todas, pero las horas registralas con --wp.")
        return None
    return hours


def from_last_commit(wanted, hours=None, comment=None, **changes):
    """Lee el ultimo commit: comenta en sus tareas OP#numero y registra la linea "Horas:" si la trae."""
    try:
        message = git("log", "-1", "--pretty=%B")
        full_hash = git("rev-parse", "HEAD")
    except (OSError, subprocess.CalledProcessError):
        raise RuntimeError("No encuentro el ultimo commit: ejecuta esto dentro de un repositorio de Git con commits.") from None
    remote = git("remote", "get-url", "origin", check=False)
    action = git("reflog", "-1", "--format=%gs", check=False).split(":")[0]
    # Un rebase (tambien el de "pull --rebase") o un cherry-pick copian commits que ya se avisaron.
    if "rebase" in action or action.startswith("cherry-pick"):
        print(f"El commit viene de un {action}; se aviso cuando se hizo. No hago nada.")
        return 0
    short = full_hash[:7]
    subject = message.splitlines()[0] if message else short
    url = github_commit_url(remote, full_hash)
    # El hash va siempre en el comentario: es la marca con que already_noted evita repetirlo.
    note = f"{comment} ({short})" if comment else f"Commit {short}: {subject}" + (f" {url}" if url else "")
    return update_references(
        wanted, message, hours=hours if hours is not None else hours_from_message(message, action),
        comment=note, time_comment=f"{subject} ({short})", tag=short, **changes,
    )


def require_token():
    token = read_setting("GESPRO_API_KEY")
    if not token:
        raise RuntimeError(
            "Falta tu token. Crealo en https://gespro.devhub.cl/my/access_tokens (seccion API) "
            f"y guardalo como GESPRO_API_KEY=... en {CONFIG_FILE}"
        )
    return token


def error_of(call, *args, error=RuntimeError):
    """El mensaje del error que lanza la llamada; falla si no lanza ninguno."""
    try:
        call(*args)
    except error as raised:
        return str(raised)
    raise AssertionError(f"{call.__name__} acepto {args}")


def fake_api(pages, writes, reply=lambda method, payload: {}):
    """request y require_token falsos para --check. Un GET responde con pages[ruta sin la consulta], o
    llama a la funcion que haya ahi con la ruta completa; lo demas se anota en writes."""
    from unittest import mock

    def fake_request(method, path, token, payload=None):
        if method == "GET":
            page = pages[path.split("?")[0]]
            return page(urllib.parse.unquote(path)) if callable(page) else page
        writes.append((method, path, payload))
        return reply(method, payload)

    return mock.patch.multiple(sys.modules[__name__], request=fake_request, require_token=lambda: "x")


def member(user_id, name, role="Developer"):
    """Una membresia como la devuelve /memberships, para --check."""
    return {"_links": {"principal": {"title": name, "href": f"/api/v3/users/{user_id}"}, "roles": [{"title": role}]}}


def check_api_flows():
    """Los flujos que hablan con GesPro y con Git, con respuestas fijas en vez de la red."""
    from unittest import mock

    here = sys.modules[__name__]
    work_package = {"id": 620, "lockVersion": 3, "subject": "Carrito",
                    "_links": {"project": {"href": "/api/v3/projects/7"}, "priority": {"title": "Normal"}}}
    pages = {
        "/api/v3/projects/demo": {"id": 7},
        "/api/v3/work_packages/620": work_package,
        "/api/v3/statuses": {"_embedded": {"elements": [{"id": 2, "name": "In progress"}]}},
        "/api/v3/projects/7/versions": {"_embedded": {"elements": [{"id": 5, "name": "Sprint 2"}]}},
    }
    writes = []
    # Todos los campos van en un PATCH, para que la tarea no quede a medio cambiar.
    with fake_api(pages, writes), contextlib.redirect_stdout(io.StringIO()):
        update_work_package("demo", 620, sprint="Sprint 2", status="In progress", percent=50)
    assert len(writes) == 1 and writes[0][2]["percentageDone"] == 50
    assert set(writes[0][2]["_links"]) == {"version", "status"}

    for code, expected in ((404, "no ve el proyecto"), (403, "no ve el proyecto"), (401, "HTTP 401")):
        failure = RuntimeError(f"GET /api/v3/projects/demo -> HTTP {code}: ...")
        with mock.patch.object(here, "request", side_effect=failure):
            assert expected in error_of(find_project, "x", "demo")

    cut = mock.Mock(**{"open.side_effect": http.client.RemoteDisconnected("conexion cortada")})
    with mock.patch.object(here, "_OPENER", cut):
        assert "sin conexion" in error_of(request, "GET", "/api/v3/users/me", "x")

    outputs = {"log": "feat: x OP#533", "rev-parse": "abc1234def", "reflog": "pull --rebase (pick): feat: x"}
    with mock.patch.multiple(here, git=lambda *args, check=True: outputs.get(args[0], ""),
                             update_references=mock.Mock(side_effect=AssertionError("aviso un rebase"))), \
            contextlib.redirect_stdout(io.StringIO()):
        assert from_last_commit("demo") == 0

    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "gespro.env")
        with open(path, "w", encoding="utf-8-sig") as handle:
            handle.write("GESPRO_CHECK = abc\n")
        with mock.patch.object(here, "CONFIG_FILE", path):
            assert read_setting("GESPRO_CHECK") == "abc"


def check_new_commands():
    """--crear, --editar-horas y --borrar-horas, con respuestas fijas en vez de la red."""
    from unittest import mock

    def entry(user_id, project_id=7):
        return {"id": 372, "spentOn": "2026-09-29", "hours": "PT2H", "comment": {"raw": "Carrito"},
                "_links": {"user": {"href": f"/api/v3/users/{user_id}"}, "project": {"href": f"/api/v3/projects/{project_id}"},
                           "entity": {"href": "/api/v3/work_packages/620"}}}

    def reply(method, payload):
        return {**entry(43), **(payload or {}), "subject": (payload or {}).get("subject")}

    pages = {
        "/api/v3/projects/demo": {"id": 7},
        "/api/v3/users/me": {"name": "YO", "_links": {"self": {"href": "/api/v3/users/43"}}},
        "/api/v3/projects/7/types": {"_embedded": {"elements": [{"id": 1, "name": "Task"}]}},
        "/api/v3/work_packages/533": {"id": 533, "subject": "Carrito", "_links": {"project": {"href": "/api/v3/projects/7"}}},
        "/api/v3/memberships": {"_embedded": {"elements": [member(8, "TOMÁS PÉREZ SOTO"), member(9, "CAMILA DÍAZ")]}},
        "/api/v3/projects/7/work_packages": {"_embedded": {"elements": [
            {"id": 600, "subject": "Planning", "_links": {"assignee": {"href": "/api/v3/users/9"}}},
            {"id": 601, "subject": "Login", "_links": {"assignee": {"href": "/api/v3/users/43"}}}]}},
        "/api/v3/time_entries/372": entry(43),
        "/api/v3/time_entries/373": entry(44),
        "/api/v3/time_entries/374": entry(43, project_id=8),
    }
    writes = []
    terminal = mock.Mock(**{"isatty.return_value": False})
    with fake_api(pages, writes, reply), mock.patch.object(sys, "stdin", terminal), \
            contextlib.redirect_stdout(io.StringIO()):
        # El mismo asunto con otra persona es otra tarea; con la misma persona, ya existe. Sin --asignar es tuya.
        create_work_package("demo", "planning", parent=533, assignee_text="tomas")
        create_work_package("demo", "Planning", assignee_text="camila")
        create_work_package("demo", "login")
        create_work_package("demo", "Logout")
        assert [(m, p) for m, p, _ in writes] == [("POST", "/api/v3/projects/7/work_packages")] * 2
        assert set(writes[0][2]["_links"]) == {"type", "parent", "assignee"}
        assert writes[0][2]["_links"]["assignee"] == {"href": "/api/v3/users/8"}
        assert writes[1][2]["_links"]["assignee"] == {"href": "/api/v3/users/43"}
        writes.clear()
        edit_hours("demo", 372, hours=1.5, day="2026-09-28")
        delete_hours("demo", 372)
        assert writes == [("PATCH", "/api/v3/time_entries/372", {"hours": "PT1H30M", "spentOn": "2026-09-28"}),
                          ("DELETE", "/api/v3/time_entries/372", None)]
        writes.clear()
        # En una terminal solo borra si la respuesta es si.
        terminal.isatty.return_value = True
        with mock.patch("builtins.input", side_effect=["no", "Sí"]):
            delete_hours("demo", 372)
            delete_hours("demo", 372)
        terminal.isatty.return_value = False
        assert writes == [("DELETE", "/api/v3/time_entries/372", None)]
        writes.clear()
        assert "Solo cambio los tuyos" in error_of(edit_hours, "demo", 373, 1.0)
        assert "Solo cambio los tuyos" in error_of(delete_hours, "demo", 373)
        assert "no es de tu proyecto" in error_of(delete_hours, "demo", 374)
        edit_hours("demo", 372, hours=1.0, dry_run=True)
        delete_hours("demo", 372, dry_run=True)
        assert "Dime que cambiar" in error_of(edit_hours, "demo", 372)
        assert "asunto" in error_of(create_work_package, "demo", "  ")
        assert writes == []


def check_team_views():
    """--sprint-actual y --horas-equipo, con respuestas fijas en vez de la red."""
    sprints = [{"id": 1, "name": "Sprint 1", "startDate": "2026-09-09", "endDate": "2026-09-23"},
               {"id": 2, "name": "Sprint 2", "startDate": "2026-09-24", "endDate": "2026-10-06"},
               {"id": 3, "name": "Sprint 3", "startDate": "2026-10-08", "endDate": "2026-10-21"},
               {"id": 9, "name": "Product Backlog", "startDate": None, "endDate": None}]
    assert [s["id"] for s in [split_sprints(sprints, "2026-10-08")[0]] + split_sprints(sprints, "2026-10-08")[1]] == [3, 1, 2]
    assert split_sprints(sprints, "2026-10-07")[0]["id"] == 3  # entre dos sprints, el proximo
    assert split_sprints(sprints, "2026-10-21")[0]["id"] == 3  # el ultimo dia todavia cuenta
    assert split_sprints(sprints, "2026-11-30") == (None, sprints[:3])

    def task(task_id, user_id, status):
        return {"id": task_id, "subject": f"Tarea {task_id}", "percentageDone": 0,
                "_links": {"assignee": {"href": f"/api/v3/users/{user_id}", "title": f"P{user_id}"},
                           "status": {"href": f"/api/v3/statuses/{status}", "title": "S"},
                           "version": {"title": "Sprint 2"}}}

    def hours(user_id, day, duration):
        return {"spentOn": day, "hours": duration, "_links": {"user": {"title": f"P{user_id}"}}}

    entries = [hours(1, "2026-10-06", "PT2H"), hours(1, "2026-10-07", "PT30M"), hours(2, "2026-10-07", "PT1H")]
    assert hours_by_person(entries) == {"P1": (2.5, {"2026-10-06", "2026-10-07"}), "P2": (1.0, {"2026-10-07"})}

    pages = {
        "/api/v3/projects/demo": {"id": 7},
        "/api/v3/users/me": {"_links": {"self": {"href": "/api/v3/users/1"}}},
        "/api/v3/projects/7/versions": {"_embedded": {"elements": sprints}},
        "/api/v3/statuses": {"_embedded": {"elements": [{"isClosed": True, "_links": {"self": {"href": "/api/v3/statuses/7"}}}]}},
        # El sprint en curso pide todos los estados ("*"); lo de sprints anteriores, solo las abiertas.
        "/api/v3/projects/7/work_packages": lambda query: {"_embedded": {"elements": (
            [task(10, 1, 1), task(11, 1, 7), task(12, 2, 1)] if '"*"' in query else [task(5, 2, 1)])}},
        "/api/v3/time_entries": {"total": 3, "_embedded": {"elements": entries}},
        "/api/v3/memberships": {"_embedded": {"elements": [member(1, "P1"), member(2, "P2"), member(3, "P3"),
                                                           member(4, "P4", "Docente")]}},
    }
    output = io.StringIO()
    with fake_api(pages, []), contextlib.redirect_stdout(output):
        sprint_status("demo")
        team_hours("demo", "2026-10-01")
    text = output.getvalue()
    assert "Tus tareas abiertas: 1\n  #10 " in text and "#11" not in text
    assert "Siguen abiertas de sprints que ya terminaron: 1" in text
    assert "Sin horas en estas fechas: P3\n" in text  # P4 es docente


def check_story_points():
    """El reparto de --puntos y su escritura, con respuestas fijas en vez de la red."""
    parts, step = split_points(8, [9, 11, 8, 8, 18, 11, 7, 4])
    assert sum(parts) == 8 and step == 0.5 and parts[4] == 2
    assert split_points(3, [13, 12, 7]) == ([1.5, 1.0, 0.5], 0.5)
    # Tareas con las mismas horas reciben lo mismo, y una con mas horas nunca recibe menos.
    same, _ = split_points(13, [4, 6, 3, 6, 6, 6, 5, 6, 5, 6, 5])
    assert sum(same) == 13 and len({same[k] for k in (1, 3, 4, 5, 7, 9)}) == 1 and len({same[k] for k in (6, 8, 10)}) == 1
    assert same[1] >= same[6] >= same[0] >= same[2]
    # Con medios puntos no sale parejo, asi que baja a cuartos.
    odd, step = split_points(5, [11, 11, 7, 11, 13, 5, 5])
    assert sum(odd) == 5 and step == 0.25 and odd[0] == odd[1] == odd[3] and odd[5] == odd[6]
    assert sum(apportion(100, [6] * 11)) == 100 and split_points(5, [3, 0]) == ([5.0, 0.0], 0.5)
    assert weight_arg("540=8,5") == (540, 8.5)
    # El reparto de 8 puntos entre 8, 1, 1, 4, 8, 8 y 2 horas rompia el empate de las tres de 8.
    tied, _ = split_points(8, [8, 1, 1, 4, 8, 8, 2])
    assert sum(tied) == 8 and tied[0] == tied[4] == tied[5] and tied[1] == tied[2]
    assert "no caben en 1 puntos" in error_of(split_points, 1, [1] * 5)
    for bad in ("540", "x=2", "540=-1", "540=nan", "540=inf"):
        error_of(weight_arg, bad, error=argparse.ArgumentTypeError)

    pair = [{"id": 540, "subject": "S2 · Carrito"}, {"id": 541, "subject": "Pago"}]
    first, second = points_block(pair, [1.5, 0.5], [75, 25], 0.5), points_block(pair, [1.25, 0.75], [62, 38], 0.25)
    assert "| #540 Carrito | 1,5 | 75% |" in first  # sin el prefijo de sprint
    once = with_points_block("Como usuario quiero pagar.", first)
    assert once == "Como usuario quiero pagar.\n\n" + first
    # Al repetir cambian la tabla y el redondeo. Lo que el equipo escribio en la misma frase y despues queda.
    edited = once.replace("Cada tarea", "Vale 2 de los 8 puntos del plan. Cada tarea") + "\n\nNota del equipo."
    twice = with_points_block(edited, second)
    assert twice.count(POINTS_HEADER) == 1 and "| 1,5 |" not in twice and "| 1,25 |" in twice
    assert "Vale 2 de los 8 puntos del plan. Cada tarea" in twice
    assert twice.endswith("un cuarto de punto.\n\nNota del equipo.") and with_points_block(twice, second) == twice
    # Si alguien borro la frase que explica la tabla, vuelve a quedar justo despues de ella.
    table, sentence = POINTS_TABLE.match(second).group(0).rstrip("\n"), second.split("\n\n")[-1]
    bare = "Intro.\n\n" + POINTS_HEADER + "\n\n| a | 1 |\n\nOtra nota."
    assert with_points_block(bare, second) == f"Intro.\n\n{table}\n\n{sentence}\n\nOtra nota."
    # Una historia que todavia no tenia tareas: el encabezado sin tabla y una nota debajo.
    empty = "Intro.\n\n" + POINTS_HEADER + "\n\nTodavia no tiene tareas."
    assert with_points_block(empty, second) == f"Intro.\n\n{table}\n\n{sentence}\n\nTodavia no tiene tareas."
    assert with_points_block("Intro.\n\n" + POINTS_HEADER, second) == f"Intro.\n\n{table}\n\n{sentence}"

    def task(task_id, estimated, subject="Tarea"):
        return {"id": task_id, "subject": subject, "estimatedTime": estimated}

    story = {"id": 533, "lockVersion": 2, "subject": "Pago", "storyPoints": 5, "description": {"raw": "Texto."},
             "_links": {"project": {"href": "/api/v3/projects/7"}, "type": {"title": "User story"}}}
    pages = {"/api/v3/projects/demo": {"id": 7}, "/api/v3/work_packages/533": story,
             "/api/v3/work_packages/534": {**task(534, "PT3H"), "_links": {**story["_links"], "type": {"title": "Task"}}},
             "/api/v3/projects/7/work_packages": {"_embedded": {"elements": [task(535, "PT6H", "A | B"), task(536, None)]}}}
    writes = []
    with fake_api(pages, writes), contextlib.redirect_stdout(io.StringIO()):
        assert "Sin horas estimadas: #536" in error_of(story_points, "demo", 533)
        assert "no estan dentro de #533" in error_of(story_points, "demo", 533, None, [(999, 2)])
        assert "solo las User story" in error_of(story_points, "demo", 534)
        story_points("demo", 533, weights=[(536, 3)], dry_run=True)
        assert writes == []
        story_points("demo", 533, total=3, weights=[(536, 3)])
        story["storyPoints"] = None
        assert "--total" in error_of(story_points, "demo", 533)
    payload = writes[0][2]
    assert payload["storyPoints"] == 3 and payload["lockVersion"] == 2
    raw = payload["description"]["raw"]
    assert raw.startswith("Texto.\n\n**Puntos por tarea**") and "| #535 A / B | 2 | 67% |" in raw and "| #536 Tarea | 1 | 33% |" in raw


def check_more_commands():
    """--estimado, --inicio, --fin, --buscar y --ver, con respuestas fijas en vez de la red."""
    from unittest import mock

    assert day_arg("2099-01-01") == "2099-01-01" and estimate_arg("40") == 40 and estimate_arg("1,5") == 1.5
    for bad in ("0", "-1", "nan", "inf", "0,005"):
        error_of(estimate_arg, bad, error=argparse.ArgumentTypeError)
    assert "queda despues" in error_of(value_fields, None, None, "2026-10-14", "2026-10-09")

    me_link = {"href": "/api/v3/users/43"}
    task = {"id": 620, "lockVersion": 1, "subject": "Página integradora", "percentageDone": 0, "estimatedTime": "PT3H",
            "description": {"raw": "Primera linea."},
            "_links": {"project": {"href": "/api/v3/projects/7"}, "type": {"title": "Task"}, "assignee": me_link,
                       "status": {"title": "New"}, "priority": {"title": "Normal"}}}
    pages = {
        "/api/v3/projects/demo": {"id": 7},
        "/api/v3/users/me": {"name": "YO", "_links": {"self": me_link}},
        "/api/v3/projects/7/types": {"_embedded": {"elements": [{"id": 1, "name": "Task"}]}},
        "/api/v3/work_packages/620": task,
        "/api/v3/projects/7/work_packages": {"total": 1, "_embedded": {"elements": [task]}},
        "/api/v3/work_packages/620/github_pull_requests": {"_embedded": {"elements": [
            {"title": "Carrito", "state": "open", "htmlUrl": "https://github.com/ana/repo/pull/7"}]}},
        "/api/v3/memberships": {"_embedded": {"elements": [member(43, "YO")]}},
        "/api/v3/work_packages/620/activities": {"_embedded": {"elements": [
            {"createdAt": "2026-10-08T12:00:00Z", "comment": {"raw": "Listo el filtro."}, "_links": {"user": me_link}},
            {"createdAt": "2026-10-08T13:00:00Z", "comment": {"raw": ""}, "_links": {"user": me_link}},
            {"createdAt": "2026-10-08T14:00:00Z", "comment": {"raw": "_Actualizado automáticamente cambiando los valores "
                                                                     "en el paquete de trabajo hijo #579_"},
             "_links": {"user": me_link}}]}},
    }

    def reply(method, payload):
        return {"id": 700, "subject": payload.get("subject")}

    writes, output = [], io.StringIO()
    with fake_api(pages, writes, reply), contextlib.redirect_stdout(output):
        update_work_package("demo", 620, percent=50, estimate=2.5, start="2026-10-09", due="2026-10-14")
        # Sin tilde y con otra mayuscula sigue siendo la misma tarea, asi que --crear no la duplica.
        create_work_package("demo", "pagina INTEGRADORA")
        create_work_package("demo", "Carrito", estimate=4)
        search_tasks("demo", "pagina")
        show_task("demo", 620)
    assert writes[0] == ("PATCH", "/api/v3/work_packages/620", {
        "lockVersion": 1, "percentageDone": 50, "estimatedTime": "PT2H30M", "startDate": "2026-10-09", "dueDate": "2026-10-14"})
    assert len(writes) == 2 and writes[1][0] == "POST" and writes[1][2]["estimatedTime"] == "PT4H"
    text = output.getvalue()
    assert "Ya existe #620" in text and '1 tareas con "pagina"' in text
    assert "3 h estimadas" in text and "PR: Carrito (open)" in text and "Comentarios: 1" in text
    assert "2026-10-08 YO: Listo el filtro." in text
    # --inicio y --fin planifican, asi que aceptan fechas futuras (--fecha no).
    argv = ["gespro.py", "--proyecto", "demo", "--wp", "620", "--inicio", "2099-01-01", "--fin", "2099-01-02", "--dry-run"]
    with fake_api(pages, writes), mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()):
        assert main() == 0


def self_check():
    assert iso_duration(360) == "PT6H" and iso_duration(15) == "PT15M" and iso_duration(137) == "PT2H17M"
    assert hours_from_iso("PT2H30M") == 2.5 and hours_from_iso("PT45M") == 0.75 and hours_from_iso(None) == 0
    assert hours_from_iso("PT1.5H") == 1.5 and hours_from_iso("P1DT2H") == 26 and hours_from_iso("P1W") == 168
    assert hours_from_iso("2 horas") == 0
    assert percent_arg("0") == 0 and percent_arg("100") == 100
    assert hours_arg("6,5") == 6.5
    assert date_arg("2026-09-29") == "2026-09-29"
    tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    for parse, bad in ((percent_arg, "-1"), (percent_arg, "101"), (hours_arg, "0"), (hours_arg, "-2"), (hours_arg, "25"),
                       (hours_arg, "0,005"), (date_arg, "29-09-2026"), (date_arg, tomorrow)):
        error_of(parse, bad, error=argparse.ArgumentTypeError)
    path = with_filters("/api/v3/time_entries", [{"user_id": {"operator": "=", "values": ["me"]}}])
    assert "user_id" in urllib.parse.unquote(path)
    members = [{"name": "TOMÁS PÉREZ SOTO"}, {"name": "TAMARA ÁLVAREZ ROJAS"}, {"name": "CAMILA FERNÁNDEZ DÍAZ"}]
    assert find_member(members, "tomas")["name"].startswith("TOM")
    assert find_member(members, "Álvarez")["name"].startswith("TAM")
    for ambiguous_or_missing in ("ez", "ignacio"):
        error_of(find_member, members, ambiguous_or_missing)
    assert references("fix: carrito OP#533 y op#560, de nuevo OP#533") == [533, 560]
    assert references("sin referencia, ni XOP#12 ni OP#") == []
    assert hours_in("feat: algo\n\nOP#533\nHoras: 1,5\n") == 1.5 and hours_in("Hours: 2") == 2.0
    assert hours_in("dice Horas: 3 en medio de una frase") is None and hours_in("") is None
    assert hours_in("Horas: 2\xa0") == 2.0 and references("OP#0 y OP#7") == [7]
    for bad in ("Horas: 30", "Horas: 2h", "Horas: 1:30", "Horas: 1\nHoras: 2"):
        error_of(hours_in, bad)
    with contextlib.redirect_stdout(io.StringIO()):  # los avisos son esperados aca
        assert hours_from_message("feat: x OP#533\nHoras: 2", "commit") == 2.0
        assert hours_from_message("feat: x OP#533\nHoras: 2", "") == 2.0
        assert hours_from_message("feat: x OP#533\nHoras: 2", "commit (amend)") is None
        assert hours_from_message("feat: x OP#533 OP#560\nHoras: 2", "commit") is None
        assert hours_from_message("feat: x OP#533\nHoras: 2h", "commit") is None
    assert github_commit_url("git@github.com:ana/repo.git", "abc") == "https://github.com/ana/repo/commit/abc"
    assert github_commit_url("https://github.com/ana/repo", "abc") == "https://github.com/ana/repo/commit/abc"
    assert github_commit_url("https://ana:token123@github.com/ana/repo.git", "abc") is None
    assert github_commit_url("https://gitlab.com/ana/repo.git", "abc") is None
    check_api_flows()
    check_new_commands()
    check_team_views()
    check_story_points()
    check_more_commands()
    print("autotest: todo bien")
    return 0


def main():
    parser = argparse.ArgumentParser(description="GesPro desde la terminal.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--proyectos", action="store_true", help="lista los proyectos que ve tu token")
    group.add_argument("--mis-tareas", action="store_true", help="lista tus tareas")
    group.add_argument("--mis-horas", action="store_true", help="lista tus horas registradas")
    group.add_argument("--wp", type=int, metavar="ID", help="numero de la tarea a actualizar, ej. --wp 620")
    group.add_argument("--report", action="store_true", help="estado del proyecto por persona")
    group.add_argument("--sprint-actual", action="store_true", help="el sprint en curso y lo que quedo abierto")
    group.add_argument("--horas-equipo", action="store_true", help="horas de cada integrante (desde el lunes)")
    group.add_argument("--miembros", action="store_true", help="lista los miembros del proyecto")
    group.add_argument("--commit", action="store_true", help="avisa en las tareas OP#numero del ultimo commit")
    group.add_argument("--en-texto", metavar="TEXTO", help="aplica los cambios a las tareas OP#numero del texto")
    group.add_argument("--crear", metavar="ASUNTO", help="crea una tarea con ese asunto, si no existe ya")
    group.add_argument("--editar-horas", type=int, metavar="ID", help="corrige un registro de --mis-horas")
    group.add_argument("--borrar-horas", type=int, metavar="ID", help="borra un registro de --mis-horas")
    group.add_argument("--puntos", type=int, metavar="ID", help="reparte los puntos de esa historia entre sus tareas")
    group.add_argument("--ver", type=int, metavar="ID", help="todo lo de una tarea: datos, tareas dentro y comentarios")
    group.add_argument("--buscar", metavar="TEXTO", help="tareas con ese texto en el asunto, sin importar tildes")
    group.add_argument("--check", action="store_true", help="prueba local, sin red")
    parser.add_argument("--proyecto", help="identificador del proyecto (si no, GESPRO_PROJECT)")
    parser.add_argument("--tipo", help='con --crear: Task (por defecto), "User story", Epic, Bug...')
    parser.add_argument("--padre", type=int, metavar="ID", help="tarea dentro de la cual queda, ej. su historia")
    parser.add_argument("--descripcion", help="con --crear: descripcion de la tarea")
    parser.add_argument("--sprint", help='sprint al que mover la tarea, ej. "Sprint 2"')
    parser.add_argument("--status", help='estado nuevo, ej. "In progress" o "Done"')
    parser.add_argument("--percent", type=percent_arg, help="porcentaje completado, de 0 a 100")
    parser.add_argument("--estimado", type=estimate_arg, metavar="HORAS", help="horas estimadas de la tarea (acepta 2,5)")
    parser.add_argument("--inicio", type=day_arg, metavar="FECHA", help="fecha de inicio, AAAA-MM-DD")
    parser.add_argument("--fin", type=day_arg, metavar="FECHA", help="fecha de termino, AAAA-MM-DD")
    parser.add_argument("--hours", type=hours_arg, help="horas trabajadas (acepta 2,5)")
    parser.add_argument("--fecha", type=date_arg, help="dia de las horas, AAAA-MM-DD (por defecto, hoy)")
    parser.add_argument("--desde", type=date_arg, help="con --mis-horas o --horas-equipo: desde esta fecha")
    parser.add_argument("--comment", help="comentario para la tarea (tambien acompana a las horas)")
    parser.add_argument("--prioridad", help="prioridad nueva: Low, Normal, High o Immediate")
    parser.add_argument("--asignar", metavar="NOMBRE", help="parte del nombre del miembro, ej. tomas (ver --miembros)")
    parser.add_argument("--total", type=points_arg, help="con --puntos: puntos de la historia, si no los tiene o para cambiarlos")
    parser.add_argument("--peso", type=weight_arg, action="append", metavar="ID=HORAS",
                        help="con --puntos: horas con que pesa una tarea, ej. 540=6 (se puede repetir)")
    parser.add_argument("--dry-run", action="store_true", help="muestra lo que haria sin escribir")
    args = parser.parse_args()

    try:
        if args.check:
            return self_check()
        if args.proyectos:
            return list_projects()
        wanted = args.proyecto or read_setting("GESPRO_PROJECT")
        if args.mis_tareas:
            return my_tasks(wanted)
        if args.mis_horas:
            return my_hours(wanted, args.desde)
        if args.report:
            return report(wanted)
        if args.sprint_actual:
            return sprint_status(wanted)
        if args.horas_equipo:
            return team_hours(wanted, args.desde)
        if args.miembros:
            return list_members(wanted)
        if args.editar_horas is not None:
            return edit_hours(wanted, args.editar_horas, args.hours, args.fecha, args.comment, args.dry_run)
        if args.borrar_horas is not None:
            return delete_hours(wanted, args.borrar_horas, args.dry_run)
        if args.ver is not None:
            return show_task(wanted, args.ver)
        if args.buscar is not None:
            return search_tasks(wanted, args.buscar)
        if args.puntos is not None:
            others = (args.hours, args.comment, args.fecha, args.sprint, args.status, args.percent is not None,
                      args.prioridad, args.asignar, args.padre, args.estimado, args.inicio, args.fin)
            if any(others):
                raise RuntimeError("--puntos solo acepta --total, --peso y --dry-run. Cambia las tareas con --wp.")
            return story_points(wanted, args.puntos, args.total, args.peso, args.dry_run)
        if args.total or args.peso:
            raise RuntimeError("--total y --peso van con --puntos.")
        names = dict(sprint=args.sprint, status=args.status, priority=args.prioridad, assignee_text=args.asignar,
                     parent=args.padre)
        values = dict(percent=args.percent, estimate=args.estimado, start=args.inicio, due=args.fin)
        if args.crear is not None:
            # Ignorarlas en silencio dejaria horas sin registrar creyendo que quedaron.
            if args.hours or args.comment or args.fecha:
                raise RuntimeError("--crear no registra horas ni comentarios. Crea la tarea y despues usa --wp con su numero.")
            return create_work_package(wanted, args.crear, kind=args.tipo, description=args.descripcion,
                                       dry_run=args.dry_run, **values, **names)
        changes = dict(day=args.fecha, dry_run=args.dry_run, **values, **names)
        if args.commit:
            return from_last_commit(wanted, hours=args.hours, comment=args.comment, **changes)
        if args.en_texto is not None:
            return update_references(wanted, args.en_texto, hours=args.hours, comment=args.comment, **changes)
        return update_work_package(wanted, args.wp, hours=args.hours, comment=args.comment, **changes)
    except (RuntimeError, KeyError, ValueError) as error:
        print(f"Error: {error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
