import json
import sys
import tempfile
import traceback
from pathlib import Path


def _run() -> None:
    from skp2dwg.gui import main

    main()


def _self_test() -> int:
    """Comprueba el binario empaquetado sin abrir la interfaz."""
    from skp2dwg import ruby_bridge
    from skp2dwg.sketchup_link import SketchUpClient
    from skp2dwg.sketchup_ui import is_server_running, start_plugin_server

    result: dict = {"running_before": is_server_running()}
    if not result["running_before"]:
        ok, message = start_plugin_server()
        result["start"] = {"ok": ok, "message": message}
    else:
        result["start"] = {"ok": True, "message": "ya estaba en marcha"}

    try:
        with SketchUpClient(timeout=60) as client:
            info = client.eval_json(ruby_bridge.model_info())
        result["model"] = {
            "title": info.get("title"),
            "path": info.get("path"),
            "units": info.get("units"),
            "layers": len(info.get("layers", [])),
            "sections": len(info.get("section_planes", [])),
            "pages": info.get("page_count"),
        }
        result["ok"] = True
    except Exception:
        result["ok"] = False
        result["error"] = traceback.format_exc()

    path = Path(tempfile.gettempdir()) / "skp2dwg_selftest.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        raise SystemExit(_self_test())
    try:
        _run()
    except Exception:
        detail = traceback.format_exc()
        try:
            (Path.home() / "skp2dwg_error.log").write_text(detail, encoding="utf-8")
        except Exception:
            pass
        try:
            import tkinter.messagebox as messagebox

            messagebox.showerror(
                "skp2dwg",
                "No se pudo iniciar la aplicación.\n\n"
                + detail[-1500:]
                + "\n\nSe guardó el detalle en %USERPROFILE%\\skp2dwg_error.log",
            )
        except Exception:
            pass
