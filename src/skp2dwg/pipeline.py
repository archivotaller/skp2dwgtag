"""Orquestación: modelo -> vistas 2D -> DXF con capas -> DWG verificado."""

from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import ezdxf

from . import ruby_bridge
from .dxf_builder import BuildStats, build_layered_dxf, make_layer_plans
from .dwg import ConversionResult, dxf_to_dwg
from .sketchup_link import SketchUpClient, SketchUpError, force_2d_exporter
from .verify import VerifyResult, verify_dwg
from .views import ViewSpec, camera_for, file_stem, resolve_section


@dataclass
class PipelineOptions:
    views: list[ViewSpec]
    out_dir: Path | None = None
    layer_config: dict | None = None
    include_hidden: bool = True
    tol: float = 0.05
    keep_work: bool = True
    on_event: Callable[[str, dict[str, Any]], None] | None = None


@dataclass
class ViewResult:
    view: ViewSpec
    stem: str
    raw_dxf: Path | None = None
    dxf: Path | None = None
    dwg: Path | None = None
    stats: BuildStats | None = None
    verify: VerifyResult | None = None
    conversion: ConversionResult | None = None
    error: str | None = None
    seconds: float = 0.0


@dataclass
class PipelineReport:
    model: dict[str, Any]
    out_dir: Path
    views: list[ViewResult] = field(default_factory=list)
    started: str = ""
    finished: str = ""
    warnings: list[str] = field(default_factory=list)


def _ruby_path(path: Path) -> str:
    return str(path).replace("\\", "/")


def _default_out_dir(info: dict[str, Any]) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    model_path = info.get("path") or ""
    if model_path:
        base = Path(model_path).parent
    else:
        base = Path.cwd() / (info.get("title") or "modelo")
    return base / "_DWG_skp2dwg" / stamp


def _is_2d_dxf(path: Path) -> tuple[bool, str]:
    try:
        doc = ezdxf.readfile(str(path))
    except Exception as exc:
        return False, f"No se pudo leer el DXF exportado: {exc}"
    msp = doc.modelspace()
    inserts = sum(1 for e in msp if e.dxftype() == "INSERT")
    zmax = 0.0
    for entity in msp:
        if entity.dxftype() == "LINE":
            zmax = max(zmax, abs(entity.dxf.start.z), abs(entity.dxf.end.z))
    if inserts:
        return False, f"El export salió en 3D ({inserts} bloques INSERT)"
    if zmax > 0.01:
        return False, f"El export salió en 3D (Z máximo {zmax:.3f})"
    return True, ""


def _export_raw(client: SketchUpClient, raw_dxf: Path, retries: int = 1) -> dict:
    last_error = ""
    for attempt in range(retries + 1):
        if attempt:
            force_2d_exporter(client)
        payload = client.eval_json(ruby_bridge.export_2d(_ruby_path(raw_dxf)))
        if not payload.get("exists"):
            last_error = f"SketchUp no escribió {raw_dxf}"
            continue
        ok, reason = _is_2d_dxf(raw_dxf)
        if ok:
            return payload
        last_error = reason
        # si salió 3D, forzamos el exportador 2D y reintentamos
        force_2d_exporter(client)
        payload = client.eval_json(ruby_bridge.export_2d(_ruby_path(raw_dxf)))
        ok, reason = _is_2d_dxf(raw_dxf)
        if ok:
            return payload
        last_error = reason
    raise SketchUpError(last_error or "Export 2D fallido")


def _emit(options: PipelineOptions, name: str, payload: dict[str, Any]) -> None:
    if options.on_event:
        try:
            options.on_event(name, payload)
        except Exception:  # noqa: BLE001 - un callback no debe romper el export
            pass


def run_pipeline(client: SketchUpClient, options: PipelineOptions) -> PipelineReport:
    info = client.eval_json(ruby_bridge.model_info())
    out_dir = options.out_dir or _default_out_dir(info)
    out_dir = Path(out_dir)
    work_dir = out_dir / "_work"
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    report = PipelineReport(
        model=info,
        out_dir=out_dir,
        started=datetime.now().isoformat(timespec="seconds"),
    )

    snapshot_path = work_dir / "snapshot.json"
    client.eval_json(ruby_bridge.snapshot_state(_ruby_path(snapshot_path)))
    _emit(options, "start", {"model": info, "out_dir": out_dir})

    plans, plan_warnings = make_layer_plans(info.get("layers", []), options.layer_config)
    report.warnings.extend(plan_warnings)

    fallback = (options.layer_config or {}).get("fallback") or {}
    from .dxf_builder import sanitize_layer_name

    expected_layers = [spec.name for spec in plans.values()]
    expected_layers += [
        sanitize_layer_name(fallback.get("unmatched", "SIN_ETIQUETA")),
        sanitize_layer_name(fallback.get("section", "CORTE")),
        sanitize_layer_name(fallback.get("profile", "PERFIL")),
        sanitize_layer_name(fallback.get("text", "TEXTO")),
    ]

    try:
        client.eval_json(ruby_bridge.set_page_transitions(False))
        if not force_2d_exporter(client):
            report.warnings.append(
                "No se pudo abrir/cerrar el diálogo 2D; se intentará exportar igualmente."
            )
        for index, view in enumerate(options.views, start=1):
            started = time.time()
            stem = file_stem(view.name)
            result = ViewResult(view=view, stem=stem)
            report.views.append(result)
            _emit(
                options,
                "view_start",
                {"view": view, "index": index, "total": len(options.views)},
            )
            try:
                section_name = resolve_section(view, info)
                if view.kind == "scene":
                    scene_info = client.eval_json(
                        ruby_bridge.activate_scene(
                            view.name, all_layers_visible=options.include_hidden
                        )
                    )
                    section_name = scene_info.get("section") or None
                else:
                    eye, target, up = camera_for(
                        ViewSpec(view.name, view.kind, section_name, view.flip), info
                    )
                    client.eval_json(
                        ruby_bridge.set_view(
                            eye,
                            target,
                            up,
                            ortho=True,
                            section=section_name,
                            all_layers_visible=options.include_hidden,
                            visible_layers=None
                            if options.include_hidden
                            else [
                                layer["name"]
                                for layer in info.get("layers", [])
                                if layer.get("visible")
                            ],
                        )
                    )

                edges_path = work_dir / f"edges_{index:02d}_{stem}.json"
                client.eval_json(ruby_bridge.extract_edges(_ruby_path(edges_path)))

                cut_path = None
                if section_name:
                    cut_path = work_dir / f"cut_{index:02d}_{stem}.json"
                    client.eval_json(
                        ruby_bridge.section_cut(section_name, _ruby_path(cut_path))
                    )

                raw_dxf = work_dir / f"raw_{index:02d}_{stem}.dxf"
                _export_raw(client, raw_dxf)
                result.raw_dxf = raw_dxf

                dxf_out = out_dir / f"{stem}.dxf"
                result.stats = build_layered_dxf(
                    dxf_in=raw_dxf,
                    edges_json=edges_path,
                    out_dxf=dxf_out,
                    model_layers=info.get("layers", []),
                    layer_config=options.layer_config,
                    tol=options.tol,
                    cut_json=cut_path,
                )
                result.dxf = dxf_out

                dwg_out = out_dir / f"{stem}.dwg"
                result.conversion = dxf_to_dwg(dxf_out, dwg_out)
                result.dwg = dwg_out
                result.verify = verify_dwg(dwg_out, expected_layers)
                if not result.verify.ok:
                    report.warnings.append(
                        f"{view.name}: verificación con problemas "
                        f"(faltan {len(result.verify.missing_layers)} capas, "
                        f"capa 0 con {result.verify.layer0_entities} entidades)"
                    )
            except Exception as exc:  # noqa: BLE001 - se reporta por vista
                result.error = str(exc)
                report.warnings.append(f"{view.name}: {exc}")
            finally:
                result.seconds = round(time.time() - started, 2)
            _emit(options, "view_done", {"view": view, "result": result})
    finally:
        try:
            client.eval_json(ruby_bridge.restore_state(_ruby_path(snapshot_path)))
        except Exception as exc:  # noqa: BLE001
            report.warnings.append(f"No se pudo restaurar el estado del modelo: {exc}")

    report.finished = datetime.now().isoformat(timespec="seconds")
    _emit(options, "finish", {"report": report})
    _write_outputs(report, options)
    return report


def _serializable(value: Any):
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, BuildStats):
        return {k: v for k, v in asdict(value).items()}
    if isinstance(value, VerifyResult):
        data = asdict(value)
        data.pop("layers_found", None)
        return data
    if isinstance(value, ConversionResult):
        return {
            "input_path": str(value.input_path),
            "output_path": str(value.output_path),
            "seconds": value.seconds,
        }
    return value


def _write_outputs(report: PipelineReport, options: PipelineOptions) -> None:
    manifest = {
        "started": report.started,
        "finished": report.finished,
        "model": report.model,
        "out_dir": str(report.out_dir),
        "warnings": report.warnings,
        "views": [
            {
                "name": view.view.name,
                "kind": view.view.kind,
                "section": view.view.section,
                "stem": view.stem,
                "raw_dxf": _serializable(view.raw_dxf),
                "dxf": _serializable(view.dxf),
                "dwg": _serializable(view.dwg),
                "stats": _serializable(view.stats),
                "verify": _serializable(view.verify),
                "conversion": _serializable(view.conversion),
                "error": view.error,
                "seconds": view.seconds,
            }
            for view in report.views
        ],
    }
    (report.out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    lines: list[str] = []
    model = report.model
    lines.append(f"# Export DWG — {model.get('title', 'modelo')}")
    lines.append("")
    lines.append(f"- Modelo: `{model.get('path') or '(sin guardar)'}`")
    from .projector import UNIT_LABEL

    lines.append(f"- Unidades: {UNIT_LABEL.get(model.get('units'), model.get('units'))}")
    lines.append(f"- Etiquetas: {len(model.get('layers', []))}")
    lines.append(f"- Salida: `{report.out_dir}`")
    lines.append(f"- Inicio: {report.started}  ·  Fin: {report.finished}")
    lines.append("")
    lines.append("| Vista | DWG | Líneas | Con etiqueta | Sin etiqueta | Ambiguas | Cortes | Verificación |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---|")
    for view in report.views:
        if view.error:
            lines.append(f"| {view.view.name} | — | — | — | — | — | — | ERROR: {view.error} |")
            continue
        stats = view.stats
        verify = view.verify
        ok = "OK" if verify and verify.ok else "revisar"
        lines.append(
            f"| {view.view.name} | {view.dwg.name if view.dwg else '—'} | "
            f"{stats.lines if stats else 0} | {stats.matched if stats else 0} | "
            f"{stats.unmatched if stats else 0} | {stats.ambiguous if stats else 0} | "
            f"{stats.cut_segments if stats else 0} | {ok} |"
        )
    lines.append("")
    if report.warnings:
        lines.append("## Avisos")
        lines.append("")
        for warning in report.warnings:
            lines.append(f"- {warning}")
        lines.append("")
    lines.append("## Capas")
    lines.append("")
    for view in report.views:
        if not view.stats:
            continue
        lines.append(f"### {view.view.name}")
        lines.append("")
        lines.append("| Etiqueta | Capa AutoCAD | Entidades |")
        lines.append("|---|---|---:|")
        for tag, count in sorted(view.stats.by_tag.items(), key=lambda kv: -kv[1]):
            layer = view.stats.layer_names.get(tag, "SIN_ETIQUETA")
            lines.append(f"| {tag} | {layer} | {count} |")
        lines.append("")
    (report.out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
