"""Arranque, parada y reinicio del servidor del plugin de SketchUp.

Usa UI Automation (pywinauto) para pulsar el menú del plugin por nombre:
``Extensiones > MCP Server > Start/Stop Server``. No usa coordenadas fijas.
"""

from __future__ import annotations

import socket
import time

from pywinauto import Desktop
from pywinauto.application import Application


def is_server_running(host: str = "127.0.0.1", port: int = 9876) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.7):
            return True
    except OSError:
        return False


def _connect():
    app = Application(backend="uia").connect(title_re=".*SketchUp.*", timeout=10)
    win = app.window(title_re=".*SketchUp.*")
    win.set_focus()
    return app, win


def _app_menubar(win):
    for bar in win.descendants(control_type="MenuBar"):
        try:
            names = [item.window_text() for item in bar.items()]
        except Exception:
            continue
        if any("archivo" in name.casefold() for name in names) or any(
            "extension" in name.casefold() for name in names
        ):
            return bar
    return None


def _find_menu_item(win, text: str):
    wanted = text.casefold()
    for item in win.descendants(control_type="MenuItem"):
        try:
            if item.window_text().strip().casefold() == wanted:
                return item
        except Exception:
            continue
    return None


def _click_menu_item(win, text: str) -> bool:
    item = _find_menu_item(win, text)
    if item is None:
        return False
    item.click_input()
    return True


def _open_plugin_submenu(win) -> bool:
    bar = _app_menubar(win)
    if bar is None:
        return False

    target = None
    for item in bar.items():
        name = item.window_text().casefold()
        if "extension" in name or "plugin" in name or "complement" in name:
            target = item
            break
    if target is None:
        items = bar.items()
        if len(items) >= 2:
            target = items[-2]
    if target is None:
        return False

    target.click_input()
    time.sleep(0.6)
    if not _click_menu_item(win, "MCP Server"):
        win.type_keys("{ESC}")
        return False
    time.sleep(0.9)
    return True


def _plugin_action(action: str) -> tuple[bool, str]:
    try:
        _app, win = _connect()
    except Exception as exc:  # noqa: BLE001
        return False, f"No se pudo conectar a la ventana de SketchUp: {exc}"

    if not _open_plugin_submenu(win):
        win.type_keys("{ESC}")
        time.sleep(0.3)
        if not _open_plugin_submenu(win):
            return False, "No se encontró el menú Extensiones > MCP Server."

    item = _find_menu_item(win, action)
    if item is None:
        win.type_keys("{ESC}")
        return False, f"No se encontró '{action}' en el submenú del plugin."
    try:
        if not item.is_enabled():
            win.type_keys("{ESC}")
            return True, f"'{action}' no está disponible (¿ya está en ese estado?)."
    except Exception:
        pass

    item.click_input()
    return True, f"Acción '{action}' enviada."


def _wait_server(state: bool, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_server_running() == state:
            return True
        time.sleep(0.3)
    return False


def start_plugin_server(timeout: float = 20.0) -> tuple[bool, str]:
    if is_server_running():
        return True, "El servidor ya estaba en marcha."
    ok, message = _plugin_action("Start Server")
    if not ok:
        return False, message
    if _wait_server(True, timeout):
        return True, "Servidor del plugin iniciado."
    return False, (
        "Se pulsó 'Start Server' pero el puerto 9876 no responde. "
        "Revisa Extensiones > MCP Server > Show Log."
    )


def stop_plugin_server(timeout: float = 15.0) -> tuple[bool, str]:
    if not is_server_running():
        return True, "El servidor ya estaba parado."
    ok, message = _plugin_action("Stop Server")
    if not ok:
        return False, message
    if _wait_server(False, timeout):
        return True, "Servidor del plugin detenido."
    return False, "Se pulsó 'Stop Server' pero el puerto 9876 sigue abierto."


def restart_plugin_server() -> tuple[bool, str]:
    stop_plugin_server()
    return start_plugin_server()


def focus_sketchup() -> bool:
    try:
        _app, win = _connect()
        win.set_focus()
        return True
    except Exception:
        return False


def sketchup_windows() -> list[str]:
    titles = []
    for window in Desktop(backend="uia").windows():
        try:
            title = window.window_text()
        except Exception:
            continue
        if "SketchUp" in title:
            titles.append(title)
    return titles
