"""Cliente del puente TCP de SketchUp (plugin MCP Server for SketchUp).

Protocolo: JSON-RPC 2.0 sobre TCP con framing de 4 bytes big-endian.
El primer mensaje de cada conexión debe ser ``hello`` con ``client_version``.
"""

from __future__ import annotations

import ctypes
import json
import socket
import struct
import time
from ctypes import wintypes
from typing import Any

CLIENT_VERSION = "0.3.0"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9876
DEFAULT_TIMEOUT = 300.0

MAX_FRAME = 64 * 1024 * 1024
WM_CLOSE = 0x0010


class SketchUpError(RuntimeError):
    """Error devuelto por el plugin o por la conexión."""


class SketchUpNotRunning(SketchUpError):
    """No hay nada escuchando en el puerto del plugin."""


class SketchUpClient:
    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        timeout: float = DEFAULT_TIMEOUT,
        client_version: str = CLIENT_VERSION,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.client_version = client_version
        self.sock: socket.socket | None = None
        self.server_version: str | None = None
        self._next_id = 0

    # -- ciclo de vida -----------------------------------------------------
    def connect(self) -> "SketchUpClient":
        if self.sock is not None:
            return self
        try:
            self.sock = socket.create_connection((self.host, self.port), timeout=10)
        except OSError as exc:
            raise SketchUpNotRunning(
                f"SketchUp no está escuchando en {self.host}:{self.port}. "
                "Abre SketchUp y ejecuta Extensiones > MCP Server > Start Server."
            ) from exc
        self.sock.settimeout(self.timeout)
        hello = self._request("hello", {"client_version": self.client_version})
        self.server_version = (hello.get("result") or {}).get("server_version")
        return self

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            finally:
                self.sock = None

    def __enter__(self) -> "SketchUpClient":
        return self.connect()

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- transporte --------------------------------------------------------
    def _send(self, payload: dict[str, Any]) -> None:
        assert self.sock is not None
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if len(data) > MAX_FRAME:
            raise SketchUpError(f"Mensaje demasiado grande: {len(data)} bytes")
        self.sock.sendall(struct.pack(">I", len(data)) + data)

    def _recv_exact(self, n: int) -> bytes:
        assert self.sock is not None
        chunks = bytearray()
        while len(chunks) < n:
            chunk = self.sock.recv(n - len(chunks))
            if not chunk:
                raise SketchUpError("El plugin cerró la conexión")
            chunks.extend(chunk)
        return bytes(chunks)

    def _recv(self) -> dict[str, Any]:
        header = self._recv_exact(4)
        size = struct.unpack(">I", header)[0]
        if size > MAX_FRAME:
            raise SketchUpError(f"Frame inválido: {size} bytes")
        return json.loads(self._recv_exact(size).decode("utf-8"))

    def _request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.sock is None:
            self.connect()
        self._next_id += 1
        request_id = self._next_id
        self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params or {},
            }
        )
        while True:
            message = self._recv()
            if message.get("id") == request_id:
                break
            # notificaciones / respuestas tardías: se ignoran
        error = message.get("error")
        if error:
            raise SketchUpError(
                f"[{error.get('code')}] {error.get('message')}"
            )
        return message

    # -- API ---------------------------------------------------------------
    def eval_ruby(self, code: str) -> str:
        """Ejecuta Ruby dentro de SketchUp y devuelve el texto del resultado."""
        response = self._request(
            "tools/call", {"name": "eval_ruby", "arguments": {"code": code}}
        )
        result = response.get("result") or {}
        content = result.get("content") or []
        text = "".join(
            block.get("text", "") for block in content if block.get("type") == "text"
        )
        if result.get("isError"):
            raise SketchUpError(text or "eval_ruby devolvió error")
        return text

    def eval_json(self, code: str) -> Any:
        text = self.eval_ruby(code)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise SketchUpError(
                f"Se esperaba JSON de SketchUp y llegó: {text[:500]!r}"
            ) from exc

    def ping(self) -> dict[str, Any]:
        return {
            "server_version": self.server_version,
            "client_version": self.client_version,
        }


# ---------------------------------------------------------------------------
# Ventanas Win32: el modo de exportación de SketchUp es "el último usado".
# Abrir y cerrar el diálogo 2D deja el exportador headless en modo 2D.
# ---------------------------------------------------------------------------
def _visible_dialogs() -> list[tuple[int, str]]:
    """Devuelve las ventanas visibles de clase #32770 con su título."""
    user32 = ctypes.windll.user32
    result: list[tuple[int, str]] = []
    proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd):
            class_name = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(hwnd, class_name, 64)
            if class_name.value == "#32770":
                title = ctypes.create_unicode_buffer(512)
                user32.GetWindowTextW(hwnd, title, 512)
                result.append((int(hwnd), title.value))
        return True

    user32.EnumWindows(proc_type(callback), 0)
    return result


def close_window(handle: int) -> None:
    if handle:
        ctypes.windll.user32.PostMessageW(
            wintypes.HWND(handle), WM_CLOSE, wintypes.WPARAM(0), wintypes.LPARAM(0)
        )


def _looks_like_2d(title: str) -> bool:
    folded = title.casefold()
    return "2d" in folded


def _looks_like_export(title: str) -> bool:
    folded = title.casefold()
    return "export" in folded


def force_2d_exporter(client: SketchUpClient) -> bool:
    """Deja el exportador headless de SketchUp en modo 2D.

    SketchUp recuerda el último exportador usado. Abrir y cerrar el diálogo
    "Exportar gráfico 2D" es suficiente para que ``model.export`` escriba 2D.
    Devuelve True si se pudo confirmar el diálogo.
    """
    # Si quedó abierto un diálogo de exportación 3D, bloquea el 2D: se cierra.
    for handle, title in _visible_dialogs():
        if _looks_like_export(title) and not _looks_like_2d(title):
            close_window(handle)
            time.sleep(0.3)

    client.eval_ruby(
        "UI.start_timer(0.2, false) { Sketchup.send_action(21237) }; 'ok'"
    )

    deadline = time.time() + 8.0
    while time.time() < deadline:
        for handle, title in _visible_dialogs():
            if _looks_like_2d(title):
                close_window(handle)
                time.sleep(0.4)
                return True
        time.sleep(0.1)
    return False
