"""Aplicación de escritorio de skp2dwg (tkinter).

No depende de OpenCode: habla con el plugin de SketchUp por TCP y con
``accoreconsole`` para generar los DWG.
"""

from __future__ import annotations

import os
import queue
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import tkinter as tk

import yaml

from . import __version__, ruby_bridge
from .pipeline import PipelineOptions, run_pipeline
from .sketchup_link import SketchUpClient, SketchUpError
from .sketchup_ui import is_server_running, start_plugin_server
from .views import ViewSpec, file_stem


def _default_out_dir(info: dict) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    model_path = info.get("path") or ""
    base = Path(model_path).parent if model_path else Path.home() / "Documents"
    return base / "_DWG_skp2dwg" / stamp


class SketchUpDwgApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.queue: queue.Queue = queue.Queue()
        self.info: dict | None = None
        self.view_vars: dict[str, tk.BooleanVar] = {}
        self.view_specs: dict[str, ViewSpec] = {}
        self.busy = False
        self.last_out: Path | None = None
        self.last_dwg: Path | None = None
        self._auto_out: str | None = None

        root.title(f"skp2dwg {__version__} - SketchUp a DWG con capas")
        root.geometry("940x760")
        root.minsize(780, 620)

        self._build_ui()
        root.after(100, self._poll_queue)
        root.after(300, self.check_status)

    # -- construcción de UI ------------------------------------------------
    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)

        # Estado de SketchUp
        status_frame = ttk.LabelFrame(outer, text="SketchUp", padding=8)
        status_frame.pack(fill="x")
        row = ttk.Frame(status_frame)
        row.pack(fill="x")
        self.status_label = tk.Label(
            row, text="Comprobando...", anchor="w", fg="#666666"
        )
        self.status_label.pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Comprobar", command=self.check_status).pack(
            side="right", padx=(6, 0)
        )
        ttk.Button(row, text="Iniciar servidor", command=self.start_server).pack(
            side="right"
        )
        self.model_label = tk.Label(
            status_frame, text="Sin modelo cargado", anchor="w", justify="left"
        )
        self.model_label.pack(fill="x", pady=(6, 0))

        # Vistas
        views_frame = ttk.LabelFrame(outer, text="Vistas a exportar", padding=8)
        views_frame.pack(fill="both", expand=True, pady=(10, 0))

        toolbar = ttk.Frame(views_frame)
        toolbar.pack(fill="x", pady=(0, 6))
        ttk.Button(toolbar, text="Todas", command=lambda: self._set_all(True)).pack(
            side="left"
        )
        ttk.Button(toolbar, text="Ninguna", command=lambda: self._set_all(False)).pack(
            side="left", padx=(6, 0)
        )
        self.views_count = ttk.Label(toolbar, text="")
        self.views_count.pack(side="right")

        canvas = tk.Canvas(views_frame, borderwidth=0, highlightthickness=0, height=190)
        scrollbar = ttk.Scrollbar(views_frame, orient="vertical", command=canvas.yview)
        self.views_inner = ttk.Frame(canvas)
        self.views_inner.bind(
            "<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=self.views_inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        canvas.bind(
            "<MouseWheel>",
            lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"),
        )
        self.views_canvas = canvas

        # Salida
        out_frame = ttk.LabelFrame(outer, text="Salida y opciones", padding=8)
        out_frame.pack(fill="x", pady=(10, 0))

        ttk.Label(out_frame, text="Carpeta:").grid(row=0, column=0, sticky="w")
        self.out_var = tk.StringVar()
        ttk.Entry(out_frame, textvariable=self.out_var).grid(
            row=0, column=1, sticky="ew", padx=(6, 6)
        )
        ttk.Button(out_frame, text="Examinar...", command=self.browse_out).grid(
            row=0, column=2, sticky="e"
        )

        ttk.Label(out_frame, text="Capas (YAML):").grid(
            row=1, column=0, sticky="w", pady=(6, 0)
        )
        self.layers_var = tk.StringVar()
        ttk.Entry(out_frame, textvariable=self.layers_var).grid(
            row=1, column=1, sticky="ew", padx=(6, 6), pady=(6, 0)
        )
        ttk.Button(out_frame, text="Examinar...", command=self.browse_layers).grid(
            row=1, column=2, sticky="e", pady=(6, 0)
        )

        opts = ttk.Frame(out_frame)
        opts.grid(row=2, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self.include_hidden = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            opts,
            text="Incluir etiquetas ocultas (capa congelada)",
            variable=self.include_hidden,
        ).pack(side="left")
        self.open_when_done = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            opts, text="Abrir el DWG al terminar", variable=self.open_when_done
        ).pack(side="left", padx=(16, 0))

        out_frame.columnconfigure(1, weight=1)

        # Acciones
        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(10, 0))
        self.export_button = ttk.Button(
            actions, text="Exportar DWG", command=self.export
        )
        self.export_button.pack(side="left")
        self.open_folder_button = ttk.Button(
            actions, text="Abrir carpeta", command=self.open_folder, state="disabled"
        )
        self.open_folder_button.pack(side="left", padx=(8, 0))
        self.open_dwg_button = ttk.Button(
            actions, text="Abrir DWG", command=self.open_dwg, state="disabled"
        )
        self.open_dwg_button.pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="Salir", command=self.root.destroy).pack(side="right")

        self.progress = ttk.Progressbar(outer, mode="determinate")
        self.progress.pack(fill="x", pady=(10, 0))
        self.progress_label = ttk.Label(outer, text="")
        self.progress_label.pack(fill="x")

        # Log
        log_frame = ttk.LabelFrame(outer, text="Registro", padding=6)
        log_frame.pack(fill="both", expand=True, pady=(10, 0))
        self.log_text = tk.Text(
            log_frame, height=9, wrap="word", state="disabled", borderwidth=0
        )
        log_scroll = ttk.Scrollbar(
            log_frame, orient="vertical", command=self.log_text.yview
        )
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

    # -- utilidades --------------------------------------------------------
    def log(self, message: str) -> None:
        self.queue.put(("log", message))

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "log":
                    self._append_log(payload)
                elif kind == "status":
                    self._apply_status(*payload)
                elif kind == "progress":
                    index, total, name = payload
                    self.progress.configure(maximum=total, value=index)
                    self.progress_label.configure(
                        text=f"Exportando {name} ({index}/{total})"
                    )
                elif kind == "done":
                    self._on_done(payload)
                elif kind == "error":
                    self._on_error(payload)
                elif kind == "busy":
                    self._set_busy(payload)
                elif kind == "refresh":
                    self.check_status()
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _append_log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message.rstrip() + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        state = "disabled" if busy or self.info is None else "normal"
        self.export_button.configure(state=state)

    def _apply_status(self, state: str, payload) -> None:
        if state == "on":
            self.info = payload
            title = payload.get("title") or "modelo"
            path = payload.get("path") or "(sin guardar)"
            self.status_label.configure(
                text="SketchUp conectado - plugin 0.3.0", fg="#1a7f37"
            )
            self.model_label.configure(
                text=(
                    f"{title} — {path}\n"
                    f"Unidades: {payload.get('units')} | etiquetas: "
                    f"{len(payload.get('layers', []))} | escenas: "
                    f"{payload.get('page_count')} | secciones: "
                    f"{len(payload.get('section_planes', []))}"
                )
            )
            self._populate_views(payload)
            new_default = str(_default_out_dir(payload))
            if not self.out_var.get().strip() or self.out_var.get() == self._auto_out:
                self.out_var.set(new_default)
            self._auto_out = new_default
            self.export_button.configure(
                state="disabled" if self.busy else "normal"
            )
        elif state == "off":
            self.info = None
            self.status_label.configure(
                text="SketchUp no conectado - inicia el servidor del plugin",
                fg="#b42318",
            )
            self.model_label.configure(text="")
            self._populate_views({})
            self.export_button.configure(state="disabled")
        else:
            self.status_label.configure(text=f"Error: {payload}", fg="#b42318")
            self._append_log(f"Error de conexión: {payload}")

    def _populate_views(self, info: dict) -> None:
        for child in self.views_inner.winfo_children():
            child.destroy()
        self.view_vars.clear()
        self.view_specs.clear()

        entries: list[tuple[str, ViewSpec]] = [
            ("PLANTA", ViewSpec("PLANTA", "top")),
            ("ELEV_FRONTAL", ViewSpec("ELEV_FRONTAL", "front")),
            ("ELEV_POSTERIOR", ViewSpec("ELEV_POSTERIOR", "back")),
            ("ELEV_DERECHA", ViewSpec("ELEV_DERECHA", "right")),
            ("ELEV_IZQUIERDA", ViewSpec("ELEV_IZQUIERDA", "left")),
        ]
        for section in info.get("section_planes", []):
            name = (section.get("name") or "").strip()
            if name:
                entries.append(
                    (
                        f"CORTE — {name}",
                        ViewSpec(f"CORTE_{file_stem(name)}", "section", section=name),
                    )
                )
        for page in info.get("pages", []):
            entries.append((f"ESCENA — {page}", ViewSpec(page, "scene")))

        for label, spec in entries:
            var = tk.BooleanVar(value=True)
            ttk.Checkbutton(self.views_inner, text=label, variable=var).pack(
                anchor="w", padx=4, pady=1
            )
            self.view_vars[label] = var
            self.view_specs[label] = spec

        if not entries:
            ttk.Label(
                self.views_inner,
                text="Abre un modelo en SketchUp y pulsa Comprobar.",
                foreground="#666666",
            ).pack(anchor="w", padx=6, pady=6)

        self.views_count.configure(text=f"{len(entries)} vistas disponibles")
        self.views_canvas.yview_moveto(0)

    def _set_all(self, value: bool) -> None:
        for var in self.view_vars.values():
            var.set(value)

    def check_status(self) -> None:
        if self.busy:
            return
        self.status_label.configure(text="Comprobando SketchUp...", fg="#666666")

        def work() -> None:
            if not is_server_running():
                self.queue.put(("status", ("off", None)))
                return
            try:
                with SketchUpClient(timeout=60) as client:
                    info = client.eval_json(ruby_bridge.model_info())
                self.queue.put(("status", ("on", info)))
            except Exception as exc:  # noqa: BLE001
                self.queue.put(("status", ("error", str(exc))))

        threading.Thread(target=work, daemon=True).start()

    def start_server(self) -> None:
        self.log("Arrancando el servidor del plugin de SketchUp...")
        self._set_busy(True)

        def work() -> None:
            ok, message = start_plugin_server()
            self.queue.put(("log", message))
            self.queue.put(("busy", False))
            if ok:
                time.sleep(0.5)
                self.queue.put(("refresh", None))
            else:
                self.queue.put(("error", message))

        threading.Thread(target=work, daemon=True).start()

    # -- acciones ----------------------------------------------------------
    def browse_out(self) -> None:
        initial = self.out_var.get().strip() or str(Path.home())
        folder = filedialog.askdirectory(initialdir=initial, title="Carpeta de salida")
        if folder:
            self.out_var.set(folder)

    def browse_layers(self) -> None:
        filename = filedialog.askopenfilename(
            title="Configuración de capas",
            filetypes=[("YAML", "*.yaml *.yml"), ("Todos", "*.*")],
        )
        if filename:
            self.layers_var.set(filename)

    def _collect_views(self) -> list[ViewSpec]:
        return [
            self.view_specs[label]
            for label, var in self.view_vars.items()
            if var.get()
        ]

    def _load_layer_config(self) -> dict:
        path = self.layers_var.get().strip()
        if not path:
            return {}
        return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}

    def export(self) -> None:
        if self.busy:
            return
        views = self._collect_views()
        if not views:
            messagebox.showwarning("Sin vistas", "Selecciona al menos una vista.")
            return
        out_dir = self.out_var.get().strip()
        out_path = Path(out_dir) if out_dir else None
        if out_path and out_path.exists() and any(out_path.glob("*.dwg")):
            if not messagebox.askyesno(
                "Sobrescribir",
                f"En {out_path} ya hay DWG. ¿Sobrescribirlos?",
            ):
                return
        try:
            layer_config = self._load_layer_config()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Capas", f"No se pudo leer el YAML de capas:\n{exc}")
            return

        self._set_busy(True)
        self.progress.configure(value=0, maximum=len(views))
        self.progress_label.configure(text="Preparando export...")
        self.log(
            f"Exportando {len(views)} vista(s) a {out_path or 'la carpeta del modelo'}..."
        )

        def on_event(name: str, payload: dict) -> None:
            if name == "view_start":
                self.queue.put(
                    (
                        "progress",
                        (payload["index"] - 1, payload["total"], payload["view"].name),
                    )
                )
            elif name == "view_done":
                result = payload["result"]
                view = payload["view"]
                if result.error:
                    self.queue.put(("log", f"[ERROR] {view.name}: {result.error}"))
                else:
                    stats = result.stats
                    self.queue.put(
                        (
                            "log",
                            f"[OK] {view.name}: {stats.lines} líneas, "
                            f"{stats.unmatched} sin etiqueta, {result.seconds}s",
                        )
                    )

        def work() -> None:
            try:
                options = PipelineOptions(
                    views=views,
                    out_dir=out_path,
                    layer_config=layer_config,
                    include_hidden=self.include_hidden.get(),
                    on_event=on_event,
                )
                with SketchUpClient(timeout=600) as client:
                    report = run_pipeline(client, options)
                self.queue.put(("done", report))
            except Exception:  # noqa: BLE001
                self.queue.put(("error", traceback.format_exc()))

        threading.Thread(target=work, daemon=True).start()

    def _on_done(self, report) -> None:
        self._set_busy(False)
        self.progress.configure(value=self.progress["maximum"])
        self.progress_label.configure(text="Terminado")
        self.last_out = report.out_dir
        dugs = [v.dwg for v in report.views if v.dwg]
        self.last_dwg = dugs[-1] if dugs else None
        self.open_folder_button.configure(state="normal")
        self.open_dwg_button.configure(
            state="normal" if self.last_dwg else "disabled"
        )
        errors = [v for v in report.views if v.error]
        self.log(f"Salida: {report.out_dir}")
        for warning in report.warnings:
            self.log(f"aviso: {warning}")
        if errors:
            messagebox.showwarning(
                "Export terminado con errores",
                f"{len(errors)} vista(s) fallaron. Revisa el registro.",
            )
        else:
            messagebox.showinfo(
                "Export completado",
                f"{len(report.views)} vista(s) generadas en:\n{report.out_dir}",
            )
        if self.open_when_done.get() and self.last_dwg:
            self.open_dwg()

    def _on_error(self, message: str) -> None:
        self._set_busy(False)
        self.progress_label.configure(text="Error")
        self.log(message)
        messagebox.showerror(
            "Error",
            "No se pudo completar la operación. Revisa el registro.\n\n"
            + message.splitlines()[-1][:300],
        )

    def open_folder(self) -> None:
        target = self.last_out or Path(self.out_var.get().strip() or ".")
        if target and Path(target).exists():
            os.startfile(str(target))

    def open_dwg(self) -> None:
        if self.last_dwg and self.last_dwg.exists():
            os.startfile(str(self.last_dwg))


def main() -> None:
    try:
        from ctypes import windll

        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:  # noqa: BLE001
        pass
    root = tk.Tk()
    SketchUpDwgApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
