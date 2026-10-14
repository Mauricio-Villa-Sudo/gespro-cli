#!/usr/bin/env python3
"""Servidor MCP para GesPro: los comandos mas usados de gespro.py como herramientas para un asistente.

Cada herramienta corre gespro.py con los mismos argumentos que escribirias en la terminal, asi que la
validacion, los mensajes y el token (GESPRO_API_KEY en gespro.env) son los del CLI. Necesita Python 3.10
o mas nuevo y el paquete mcp (pip install mcp). Como registrarlo esta en el README.
"""

import os
import subprocess
import sys

try:
    from mcp.server.mcpserver import MCPServer as Server
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:
    try:  # mcp 1.x
        from mcp.server.fastmcp import FastMCP as Server
        from mcp.server.fastmcp.exceptions import ToolError
    except ImportError:
        # --check corre sin el paquete, como en la CI.
        Server, ToolError = None, RuntimeError

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gespro.py")
# Algunas llamadas recorren todo el proyecto; el CLI ya corta cada pedido a la API a los 30 segundos.
TIMEOUT = 180


def flags(**options):
    """--nombre=valor por cada opcion con valor. True agrega solo la marca; None y False se omiten.
    Con el = pegado, un texto que empieza con guion ("-1", "--status") llega como valor y no como opcion."""
    argv = []
    for name, value in options.items():
        if value is None or value is False:
            continue
        flag = "--" + name.replace("_", "-")
        argv.append(flag if value is True else f"{flag}={value}")
    return argv


def run(*argv):
    """Corre gespro.py y devuelve lo que imprime. Si termina con error, lo lanza como ToolError: el SDK
    esconde el mensaje de cualquier otra excepcion."""
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    try:
        done = subprocess.run([sys.executable, SCRIPT, *argv], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=TIMEOUT, env=env)
    except subprocess.TimeoutExpired:
        raise ToolError(f"gespro.py no respondio en {TIMEOUT} segundos.") from None
    output = (done.stdout + done.stderr).strip()
    if done.returncode == 2:
        # argparse rechazo un valor: el uso completo sobra, la ultima linea dice que estuvo mal.
        output = output.splitlines()[-1]
    if done.returncode != 0:
        raise ToolError(output or f"gespro.py termino con codigo {done.returncode}")
    return output


def self_check():
    assert flags(wp=620, status="Done", percent=0, dry_run=True, comment=None, cascada=False) == [
        "--wp=620", "--status=Done", "--percent=0", "--dry-run"]
    assert flags(comment="--status Done", hours=1.5) == ["--comment=--status Done", "--hours=1.5"]
    print("ok")
    return 0


def build_server():
    server = Server("gespro", instructions=(
        "Herramientas para GesPro, el OpenProject de la carrera. Las tareas se nombran por numero (#620). "
        "Antes de escribir algo que no se pueda deshacer facil, prueba con dry_run=True y muestra el resultado."))

    @server.tool()
    def mis_tareas() -> str:
        """Tus tareas del proyecto, con estado, prioridad, porcentaje y horas."""
        return run("--mis-tareas")

    @server.tool()
    def ver_tarea(id: int) -> str:
        """Todo lo de una tarea: datos, descripcion, tareas dentro, PR enlazados y comentarios."""
        return run(*flags(ver=id))

    @server.tool()
    def buscar_tareas(texto: str) -> str:
        """Tareas con ese texto en el asunto, sin importar mayusculas ni tildes."""
        return run(*flags(buscar=texto))

    @server.tool()
    def sprint_actual() -> str:
        """El sprint en curso y lo que quedo abierto de los anteriores."""
        return run("--sprint-actual")

    @server.tool()
    def mis_horas(desde: str | None = None) -> str:
        """Tus horas registradas. desde: AAAA-MM-DD."""
        return run("--mis-horas", *flags(desde=desde))

    @server.tool()
    def horas_equipo(desde: str | None = None) -> str:
        """Horas de cada integrante, desde el lunes o desde la fecha dada (AAAA-MM-DD)."""
        return run("--horas-equipo", *flags(desde=desde))

    @server.tool()
    def revisar_proyecto() -> str:
        """Tareas sin padre, sin asignar, sin estimado, sin sprint o sin horas, y las atrasadas."""
        return run("--revisar") + "\n\n" + run("--atrasadas")

    @server.tool()
    def daily(fecha: str | None = None) -> str:
        """Texto del standup con tus horas del dia habil anterior, o del dia dado (AAAA-MM-DD)."""
        return run("--daily", *flags(fecha=fecha))

    @server.tool()
    def actualizar_tarea(id: int, status: str | None = None, percent: int | None = None, hours: float | None = None,
                         fecha: str | None = None, comment: str | None = None, sprint: str | None = None,
                         prioridad: str | None = None, asignar: str | None = None, padre: int | None = None,
                         estimado: float | None = None, inicio: str | None = None, fin: str | None = None,
                         dry_run: bool = False) -> str:
        """Cambia una tarea y registra horas. status: "In progress", "Done"...; percent: 0 a 100; hours: horas
        trabajadas (maximo 24) en fecha (AAAA-MM-DD, por defecto hoy); comment va a la tarea y a las horas;
        asignar: parte del nombre del miembro; estimado: horas estimadas; inicio y fin: AAAA-MM-DD."""
        return run(*flags(wp=id, status=status, percent=percent, hours=hours, fecha=fecha, comment=comment,
                                           sprint=sprint, prioridad=prioridad, asignar=asignar, padre=padre,
                                           estimado=estimado, inicio=inicio, fin=fin, dry_run=dry_run))

    @server.tool()
    def crear_tarea(asunto: str, tipo: str | None = None, descripcion: str | None = None, padre: int | None = None,
                    sprint: str | None = None, status: str | None = None, asignar: str | None = None,
                    estimado: float | None = None, inicio: str | None = None, fin: str | None = None,
                    dry_run: bool = False) -> str:
        """Crea una tarea, asignada a ti si no dices a quien. Si ya hay una con el mismo asunto y la misma
        persona, no crea otra. tipo: Task (por defecto), "User story", Epic, Bug. padre: su historia."""
        return run(*flags(crear=asunto, tipo=tipo, descripcion=descripcion, padre=padre, sprint=sprint,
                                             status=status, asignar=asignar, estimado=estimado, inicio=inicio,
                                             fin=fin, dry_run=dry_run))

    return server


if __name__ == "__main__":
    if "--check" in sys.argv:
        sys.exit(self_check())
    if Server is None:
        sys.exit("Falta el paquete mcp: pip install mcp (necesita Python 3.10 o mas nuevo).")
    build_server().run()
