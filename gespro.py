#!/usr/bin/env python3
"""Cliente de linea de comandos para GesPro (https://gespro.devhub.cl), el OpenProject de la carrera.

Uso:
    python gespro.py --proyectos                          proyectos que ve tu token
    python gespro.py --mis-tareas                         tus tareas, con estado, % y horas
    python gespro.py --mis-horas [--desde 2026-09-28]     tus horas ya registradas
    python gespro.py --wp 620 --status "In progress"      cambia el estado de una tarea
    python gespro.py --wp 620 --percent 50                progreso
    python gespro.py --wp 620 --hours 2.5 [--fecha 2026-09-29]   registra horas (hoy si no hay fecha)
    python gespro.py --wp 620 --comment "texto"           deja un comentario
    python gespro.py --wp 620 --sprint "Sprint 2"         mueve la tarea a un sprint
    python gespro.py --wp 620 --prioridad High            prioridad: Low, Normal, High o Immediate
    python gespro.py --wp 620 --asignar tomas             reasigna la tarea a otro miembro
    python gespro.py --wp 620 --padre 533                 la deja dentro de otra tarea (su historia)
    python gespro.py --crear "Carrito: quitar items" --padre 533 --asignar tomas   crea una tarea
    python gespro.py --editar-horas 372 --hours 1,5       corrige un registro de --mis-horas
    python gespro.py --borrar-horas 372                   borra un registro tuyo
    python gespro.py --miembros                           miembros del proyecto (para --asignar)
    python gespro.py --commit                             avisa en las tareas OP#numero del ultimo commit
    python gespro.py --en-texto "texto con OP#620" --status "In Review"   aplica a las tareas del texto
    python gespro.py --report                             estado del proyecto por persona
    python gespro.py --check                              prueba local, sin red

Las opciones de --wp se pueden combinar en una sola llamada. --dry-run muestra lo que haria sin
escribir nada.

El token (GESPRO_API_KEY) y el proyecto (GESPRO_PROJECT) se leen de variables de entorno o del
archivo gespro.env que esta junto a este script. --proyecto cambia el proyecto en una llamada.
Cada persona usa su token: las horas quedan a nombre del dueno del token.
"""

import argparse
import base64
import contextlib
import datetime
import http.client
import io
import json
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
USER_AGENT = "gespro-cli/1.4"
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gespro.env")
# Una tarea se menciona como OP#533, igual que en la integracion de OpenProject con GitHub.
REFERENCE = re.compile(r"\bOP#(\d+)\b", re.IGNORECASE)
# Linea "Horas: 1,5" (o "Hours: 1.5") sola en el mensaje del commit.
HOURS_LINE = re.compile(r"^[ \t]*(?:horas|hours)[ \t]*:(.*)$", re.IGNORECASE | re.MULTILINE)
# Accion del reflog de un commit nuevo. amend, rebase y cherry-pick repiten un mensaje que ya se registro.
NEW_COMMIT = {"commit", "commit (initial)", "commit (merge)"}


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
    # Una hora negativa generaria duraciones como "PT-1H" que la API acepta igual.
    number = float(value.replace(",", "."))
    if not 0 < number <= 24:
        raise argparse.ArgumentTypeError("tiene que ser mayor que 0 y como maximo 24")
    return number


def date_arg(value):
    try:
        day = datetime.date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError("usa el formato AAAA-MM-DD, por ejemplo 2026-09-29") from None
    if day > datetime.date.today():
        raise argparse.ArgumentTypeError("no se registran horas en fechas futuras")
    return day.isoformat()


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
    if hours and rest:
        return f"PT{hours}H{rest}M"
    if hours:
        return f"PT{hours}H"
    return f"PT{rest}M"


def hours_from_iso(duration):
    """'PT2H30M' -> 2.5. OpenProject devuelve las horas en formato ISO 8601."""
    if not duration or not duration.startswith("PT"):
        return 0.0
    total, number = 0.0, ""
    for char in duration[2:]:
        if char.isdigit() or char == ".":
            number += char
            continue
        value = float(number or 0)
        total += {"H": value, "M": value / 60, "S": value / 3600}.get(char, 0)
        number = ""
    return round(total, 2)


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
    path = with_filters("/api/v3/principals", [{"member": {"operator": "=", "values": [str(project_id)]}}], pageSize=200)
    return elements(request("GET", path, token))


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
        print(f"  {member['id']:<5} {member['name']}")
    return 0


def find_by_name(token, path, name, what):
    for item in elements(request("GET", path, token)):
        if item["name"].strip().lower() == name.strip().lower():
            return item["id"]
    names = ", ".join(item["name"] for item in elements(request("GET", path, token)))
    raise RuntimeError(f'No existe {what} "{name}". Opciones: {names}')


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


def add_comment(token, work_package_id, text):
    return request("POST", f"/api/v3/work_packages/{work_package_id}/activities", token, {"comment": {"raw": text}})


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


def my_hours(wanted, since=None):
    token = require_token()
    project_id = find_project(token, wanted)["id"]
    filters = [
        {"user_id": {"operator": "=", "values": ["me"]}},
        {"project_id": {"operator": "=", "values": [str(project_id)]}},
    ]
    if since:
        filters.append({"spent_on": {"operator": "<>d", "values": [since, datetime.date.today().isoformat()]}})
    path = with_filters("/api/v3/time_entries", filters, pageSize=500, sortBy=json.dumps([["spent_on", "asc"]]))
    entries = elements(request("GET", path, token))
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
        links["assignee"] = {"href": member["_links"]["self"]["href"]}
        shown["asignada a"] = member["name"]
    if parent:
        mother = task_in_project(token, parent, project_id)
        links["parent"] = {"href": f"/api/v3/work_packages/{parent}"}
        shown["dentro de"] = f"#{parent} {mother['subject']}"
    return links, shown


def update_work_package(wanted, wp_id, sprint=None, status=None, percent=None, hours=None, day=None, comment=None,
                        priority=None, assignee_text=None, parent=None, dry_run=False, time_comment=None, tag=None):
    token = require_token()
    project_id = find_project(token, wanted)["id"]
    work_package = task_in_project(token, wp_id, project_id)
    assignee = (work_package["_links"].get("assignee") or {}).get("title") or "sin asignar"
    print(f"#{wp_id}: {work_package['subject']} (asignada a {assignee}, prioridad {work_package['_links']['priority']['title']})")
    spent_on = day or datetime.date.today().isoformat()
    links, shown = resolve_links(token, project_id, sprint, status, priority, assignee_text, parent)
    fields = {"_links": links} if links else {}
    if percent is not None:
        fields["percentageDone"] = percent
        shown["progreso"] = f"{percent}%"

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
        add_comment(token, wp_id, comment)
        print("  comentario agregado")
    return 0


def same_task(token, project_id, subject, assignee_link):
    """Una tarea con el mismo asunto y la misma persona asignada. El asunto solo no basta: tareas como
    "Planning, dailies, review y retrospectiva" existen una vez por integrante."""
    filters = [{"subject": {"operator": "~", "values": [subject]}}, {"status": {"operator": "*", "values": []}}]
    path = with_filters(f"/api/v3/projects/{project_id}/work_packages", filters, pageSize=500)
    wanted_href = (assignee_link or {}).get("href")
    for item in elements(request("GET", path, token)):
        if plain(item["subject"]) == plain(subject) and (item["_links"].get("assignee") or {}).get("href") == wanted_href:
            return item
    return None


def create_work_package(wanted, subject, kind=None, description=None, percent=None, dry_run=False, **names):
    """Crea una tarea. Si ya hay una con el mismo asunto y la misma persona asignada, no crea otra."""
    subject = (subject or "").strip()
    if not subject:
        raise RuntimeError("La tarea necesita un asunto.")
    kind = kind or "Task"
    token = require_token()
    project_id = find_project(token, wanted)["id"]
    type_id = find_by_name(token, f"/api/v3/projects/{project_id}/types", kind, "el tipo")
    links, shown = resolve_links(token, project_id, **names)
    twin = same_task(token, project_id, subject, links.get("assignee"))
    if twin:
        print(f"Ya existe #{twin['id']}: {twin['subject']}. No creo otra; cambiala con --wp {twin['id']}.")
        return 0
    links["type"] = {"href": f"/api/v3/types/{type_id}"}
    payload = {"subject": subject, "_links": links}
    if description:
        payload["description"] = {"raw": description}
    if percent is not None:
        payload["percentageDone"] = percent
        shown["progreso"] = f"{percent}%"
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


def error_of(call, *args):
    """El mensaje del RuntimeError que lanza la llamada; falla si no lanza ninguno."""
    try:
        call(*args)
    except RuntimeError as error:
        return str(error)
    raise AssertionError(f"{call.__name__} no fallo")


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
    patches = []

    def fake_request(method, path, token, payload=None):
        if method == "PATCH":
            patches.append(payload)
            return {**work_package, "percentageDone": payload.get("percentageDone")}
        return pages[path]

    # Todos los campos van en un PATCH, para que la tarea no quede a medio cambiar.
    with mock.patch.multiple(here, request=fake_request, require_token=lambda: "x"), \
            contextlib.redirect_stdout(io.StringIO()):
        update_work_package("demo", 620, sprint="Sprint 2", status="In progress", percent=50)
    assert len(patches) == 1 and patches[0]["percentageDone"] == 50
    assert set(patches[0]["_links"]) == {"version", "status"}

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

    def person(user_id, name):
        return {"id": user_id, "name": name, "_links": {"self": {"href": f"/api/v3/users/{user_id}"}}}

    def entry(user_id, project_id=7):
        return {"id": 372, "spentOn": "2026-09-29", "hours": "PT2H", "comment": {"raw": "Carrito"},
                "_links": {"user": {"href": f"/api/v3/users/{user_id}"}, "project": {"href": f"/api/v3/projects/{project_id}"},
                           "entity": {"href": "/api/v3/work_packages/620"}}}

    pages = {
        "/api/v3/projects/demo": {"id": 7},
        "/api/v3/users/me": person(43, "YO"),
        "/api/v3/projects/7/types": {"_embedded": {"elements": [{"id": 1, "name": "Task"}]}},
        "/api/v3/work_packages/533": {"id": 533, "subject": "Carrito", "_links": {"project": {"href": "/api/v3/projects/7"}}},
        "/api/v3/principals": {"_embedded": {"elements": [person(8, "TOMÁS PÉREZ SOTO"), person(9, "CAMILA DÍAZ")]}},
        "/api/v3/projects/7/work_packages": {"_embedded": {"elements": [
            {"id": 600, "subject": "Planning", "_links": {"assignee": {"href": "/api/v3/users/9"}}}]}},
        "/api/v3/time_entries/372": entry(43),
        "/api/v3/time_entries/373": entry(44),
        "/api/v3/time_entries/374": entry(43, project_id=8),
    }
    writes = []

    def fake_request(method, path, token, payload=None):
        if method == "GET":
            return pages[path.split("?")[0]]
        writes.append((method, path, payload))
        return {**entry(43), **(payload or {}), "subject": (payload or {}).get("subject")}

    with mock.patch.multiple(sys.modules[__name__], request=fake_request, require_token=lambda: "x"), \
            contextlib.redirect_stdout(io.StringIO()):
        # El mismo asunto con otra persona es otra tarea; con la misma persona, ya existe.
        create_work_package("demo", "planning", parent=533, assignee_text="tomas")
        create_work_package("demo", "Planning", assignee_text="camila")
        assert [(m, p) for m, p, _ in writes] == [("POST", "/api/v3/projects/7/work_packages")]
        assert set(writes[0][2]["_links"]) == {"type", "parent", "assignee"}
        writes.clear()
        edit_hours("demo", 372, hours=1.5, day="2026-09-28")
        delete_hours("demo", 372)
        assert writes == [("PATCH", "/api/v3/time_entries/372", {"hours": "PT1H30M", "spentOn": "2026-09-28"}),
                          ("DELETE", "/api/v3/time_entries/372", None)]
        writes.clear()
        assert "Solo cambio los tuyos" in error_of(edit_hours, "demo", 373, 1.0)
        assert "Solo cambio los tuyos" in error_of(delete_hours, "demo", 373)
        assert "no es de tu proyecto" in error_of(delete_hours, "demo", 374)
        edit_hours("demo", 372, hours=1.0, dry_run=True)
        delete_hours("demo", 372, dry_run=True)
        assert "Dime que cambiar" in error_of(edit_hours, "demo", 372)
        assert "asunto" in error_of(create_work_package, "demo", "  ")
        assert writes == []


def self_check():
    assert iso_duration(360) == "PT6H" and iso_duration(15) == "PT15M" and iso_duration(137) == "PT2H17M"
    assert hours_from_iso("PT2H30M") == 2.5 and hours_from_iso("PT45M") == 0.75 and hours_from_iso(None) == 0
    assert hours_from_iso("PT1.5H") == 1.5
    assert percent_arg("0") == 0 and percent_arg("100") == 100
    assert hours_arg("6,5") == 6.5
    assert date_arg("2026-09-29") == "2026-09-29"
    for parse, bad in (
        (percent_arg, "-1"),
        (percent_arg, "101"),
        (hours_arg, "0"),
        (hours_arg, "-2"),
        (hours_arg, "25"),
        (date_arg, "29-09-2026"),
        (date_arg, (datetime.date.today() + datetime.timedelta(days=1)).isoformat()),
    ):
        try:
            parse(bad)
            raise AssertionError(f"{parse.__name__} acepto {bad}")
        except argparse.ArgumentTypeError:
            pass
    path = with_filters("/api/v3/time_entries", [{"user_id": {"operator": "=", "values": ["me"]}}])
    assert "user_id" in urllib.parse.unquote(path)
    members = [{"name": "TOMÁS PÉREZ SOTO"}, {"name": "TAMARA ÁLVAREZ ROJAS"}, {"name": "CAMILA FERNÁNDEZ DÍAZ"}]
    assert find_member(members, "tomas")["name"].startswith("TOM")
    assert find_member(members, "Álvarez")["name"].startswith("TAM")
    for ambiguous_or_missing in ("ez", "ignacio"):
        try:
            find_member(members, ambiguous_or_missing)
            raise AssertionError(f"find_member acepto {ambiguous_or_missing}")
        except RuntimeError:
            pass
    assert references("fix: carrito OP#533 y op#560, de nuevo OP#533") == [533, 560]
    assert references("sin referencia, ni XOP#12 ni OP#") == []
    assert hours_in("feat: algo\n\nOP#533\nHoras: 1,5\n") == 1.5 and hours_in("Hours: 2") == 2.0
    assert hours_in("dice Horas: 3 en medio de una frase") is None and hours_in("") is None
    assert hours_in("Horas: 2\xa0") == 2.0 and references("OP#0 y OP#7") == [7]
    for bad in ("Horas: 30", "Horas: 2h", "Horas: 1:30", "Horas: 1\nHoras: 2"):
        try:
            hours_in(bad)
            raise AssertionError(f"hours_in acepto {bad!r}")
        except RuntimeError:
            pass
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
    group.add_argument("--miembros", action="store_true", help="lista los miembros del proyecto")
    group.add_argument("--commit", action="store_true", help="avisa en las tareas OP#numero del ultimo commit")
    group.add_argument("--en-texto", metavar="TEXTO", help="aplica los cambios a las tareas OP#numero del texto")
    group.add_argument("--crear", metavar="ASUNTO", help="crea una tarea con ese asunto, si no existe ya")
    group.add_argument("--editar-horas", type=int, metavar="ID", help="corrige un registro de --mis-horas")
    group.add_argument("--borrar-horas", type=int, metavar="ID", help="borra un registro de --mis-horas")
    group.add_argument("--check", action="store_true", help="prueba local, sin red")
    parser.add_argument("--proyecto", help="identificador del proyecto (si no, GESPRO_PROJECT)")
    parser.add_argument("--tipo", help='con --crear: Task (por defecto), "User story", Epic, Bug...')
    parser.add_argument("--padre", type=int, metavar="ID", help="tarea dentro de la cual queda, ej. su historia")
    parser.add_argument("--descripcion", help="con --crear: descripcion de la tarea")
    parser.add_argument("--sprint", help='sprint al que mover la tarea, ej. "Sprint 2"')
    parser.add_argument("--status", help='estado nuevo, ej. "In progress" o "Done"')
    parser.add_argument("--percent", type=percent_arg, help="porcentaje completado, de 0 a 100")
    parser.add_argument("--hours", type=hours_arg, help="horas trabajadas (acepta 2,5)")
    parser.add_argument("--fecha", type=date_arg, help="dia de las horas, AAAA-MM-DD (por defecto, hoy)")
    parser.add_argument("--desde", type=date_arg, help="con --mis-horas: solo desde esta fecha")
    parser.add_argument("--comment", help="comentario para la tarea (tambien acompana a las horas)")
    parser.add_argument("--prioridad", help="prioridad nueva: Low, Normal, High o Immediate")
    parser.add_argument("--asignar", metavar="NOMBRE", help="parte del nombre del miembro, ej. tomas (ver --miembros)")
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
        if args.miembros:
            return list_members(wanted)
        if args.editar_horas is not None:
            return edit_hours(wanted, args.editar_horas, args.hours, args.fecha, args.comment, args.dry_run)
        if args.borrar_horas is not None:
            return delete_hours(wanted, args.borrar_horas, args.dry_run)
        names = dict(sprint=args.sprint, status=args.status, priority=args.prioridad, assignee_text=args.asignar,
                     parent=args.padre)
        if args.crear is not None:
            # Ignorarlas en silencio dejaria horas sin registrar creyendo que quedaron.
            if args.hours or args.comment or args.fecha:
                raise RuntimeError("--crear no registra horas ni comentarios. Crea la tarea y despues usa --wp con su numero.")
            return create_work_package(wanted, args.crear, kind=args.tipo, description=args.descripcion,
                                       percent=args.percent, dry_run=args.dry_run, **names)
        changes = dict(percent=args.percent, day=args.fecha, dry_run=args.dry_run, **names)
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
