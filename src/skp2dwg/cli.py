"""CLI de skp2dwg."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__, ruby_bridge
from .dxf_builder import build_layered_dxf, make_layer_plans, sanitize_layer_name
from .dwg import AccoreConsoleError, dxf_to_dwg, dwg_to_dxf, find_accoreconsole
from .pipeline import PipelineOptions, run_pipeline
from .sketchup_link import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    SketchUpClient,
    SketchUpError,
    SketchUpNotRunning,
)
from .verify import verify_dwg, verify_dxf
from .views import auto_views, load_views, scene_views


def _load_yaml(path: str | None) -> dict:
    if not path:
        return {}
    import yaml

    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def _print(message: str) -> None:
    print(message, file=sys.stderr)


def cmd_doctor(args: argparse.Namespace) -> int:
    result: dict = {"version": __version__}
    try:
        with SketchUpClient(args.host, args.port) as client:
            result["sketchup"] = client.ping()
            info = client.eval_json(ruby_bridge.model_info())
            result["model"] = info
    except SketchUpNotRunning as exc:
        result["sketchup_error"] = str(exc)
    except SketchUpError as exc:
        result["sketchup_error"] = str(exc)

    try:
        result["accoreconsole"] = str(find_accoreconsole())
    except AccoreConsoleError as exc:
        result["accoreconsole_error"] = str(exc)

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    print(f"skp2dwg {__version__}")
    if "sketchup" in result:
        skp = result["sketchup"]
        model = result["model"]
        print(f"  SketchUp: plugin {skp['server_version']} - cliente {skp['client_version']}")
        print(f"  Modelo: {model.get('title')} ({model.get('path') or 'sin guardar'})")
        print(
            f"  Unidades: {model.get('units')} | etiquetas: {len(model.get('layers', []))}"
            f" | escenas: {model.get('page_count')} | secciones: {len(model.get('section_planes', []))}"
        )
        for section in model.get("section_planes", []):
            print(f"    - sección: {section.get('name')} (activa={section.get('active')})")
    else:
        print(f"  SketchUp: ERROR — {result.get('sketchup_error')}")
    print(f"  accoreconsole: {result.get('accoreconsole') or result.get('accoreconsole_error')}")
    return 0 if "sketchup" in result and "accoreconsole" in result else 1


def cmd_export(args: argparse.Namespace) -> int:
    layer_config = _load_yaml(args.layers)
    with SketchUpClient(args.host, args.port) as client:
        info = client.eval_json(ruby_bridge.model_info())
        if args.views:
            views = load_views(args.views)
        elif args.scenes:
            views = scene_views(info)
            if not views:
                raise SystemExit("El modelo no tiene escenas.")
        else:
            views = auto_views(info)
        if args.only:
            wanted = {name.strip().casefold() for name in args.only.split(",")}
            views = [v for v in views if v.name.casefold() in wanted]
            if not views:
                raise SystemExit("Ninguna vista coincide con --only.")

        options = PipelineOptions(
            views=views,
            out_dir=Path(args.out) if args.out else None,
            layer_config=layer_config,
            include_hidden=not args.visible_only,
            tol=args.tol,
        )
        _print(f"Exportando {len(views)} vista(s)...")
        report = run_pipeline(client, options)

    for view in report.views:
        if view.error:
            _print(f"  [ERROR] {view.view.name}: {view.error}")
        else:
            stats = view.stats
            _print(
                f"  [OK] {view.view.name}: {stats.lines} líneas, "
                f"{stats.unmatched} sin etiqueta, {view.seconds}s -> {view.dwg}"
            )
    for warning in report.warnings:
        _print(f"  aviso: {warning}")
    _print(f"Salida: {report.out_dir}")

    if args.open:
        last = next((v.dwg for v in reversed(report.views) if v.dwg), None)
        if last and last.exists():
            os.startfile(str(last))

    return 0 if not any(v.error for v in report.views) else 2


def cmd_build(args: argparse.Namespace) -> int:
    edges = json.loads(Path(args.edges).read_text(encoding="utf-8"))
    if args.model_info:
        info = json.loads(Path(args.model_info).read_text(encoding="utf-8"))
        model_layers = info.get("layers", [])
    else:
        tags = sorted({item[6] for item in edges["edges"] if item[6]})
        model_layers = [
            {"name": tag, "visible": True, "rgb": [255, 255, 255]} for tag in tags
        ]
    layer_config = _load_yaml(args.layers)
    stats = build_layered_dxf(
        dxf_in=args.dxf,
        edges_json=args.edges,
        out_dxf=args.out,
        model_layers=model_layers,
        layer_config=layer_config,
        tol=args.tol,
    )
    print(
        f"{stats.lines} líneas · {stats.matched} con etiqueta · "
        f"{stats.unmatched} sin etiqueta · {stats.ambiguous} ambiguas"
    )
    for tag, count in sorted(stats.by_tag.items(), key=lambda kv: -kv[1]):
        print(f"  {tag} -> {stats.layer_names.get(tag, '?')}: {count}")
    for warning in stats.warnings:
        _print(f"aviso: {warning}")
    return 0


def cmd_to_dwg(args: argparse.Namespace) -> int:
    result = dxf_to_dwg(args.dxf, args.dwg)
    print(f"DWG generado en {result.seconds:.1f}s: {result.output_path}")
    return 0


def cmd_from_dwg(args: argparse.Namespace) -> int:
    result = dwg_to_dxf(args.dwg, args.dxf)
    print(f"DXF generado en {result.seconds:.1f}s: {result.output_path}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    expected = [x.strip() for x in args.expected.split(",") if x.strip()]
    if args.dwg:
        result = verify_dwg(args.dwg, expected, require_layer0_empty=not args.allow_layer0)
    else:
        result = verify_dxf(args.dxf, expected)
    print(json.dumps(
        {
            "ok": result.ok,
            "missing_layers": result.missing_layers,
            "extra_layers": result.extra_layers,
            "layer0_entities": result.layer0_entities,
            "insunits": result.insunits,
            "warnings": result.warnings,
            "counts": result.counts,
        },
        indent=2,
        ensure_ascii=False,
    ))
    return 0 if result.ok else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="skp2dwg",
        description="Exporta vistas 2D de SketchUp a DWG con las etiquetas como capas.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    def network(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--host", default=DEFAULT_HOST)
        subparser.add_argument("--port", type=int, default=DEFAULT_PORT)

    doctor = sub.add_parser("doctor", help="Comprueba SketchUp, plugin y accoreconsole")
    network(doctor)
    doctor.add_argument("--json", action="store_true")
    doctor.set_defaults(func=cmd_doctor)

    export = sub.add_parser("export", help="Exporta las vistas a DWG")
    network(export)
    export.add_argument("--out", help="Carpeta de salida")
    export.add_argument("--views", help="YAML con la definicion de vistas")
    export.add_argument("--scenes", action="store_true", help="Usar las escenas del modelo")
    export.add_argument("--only", help="Lista de nombres de vista separada por comas")
    export.add_argument("--layers", help="YAML con overrides de capas")
    export.add_argument("--visible-only", action="store_true",
                        help="No activar las etiquetas ocultas durante el export")
    export.add_argument("--tol", type=float, default=0.05,
                        help="Tolerancia de coincidencia en unidades del dibujo")
    export.add_argument("--open", action="store_true", help="Abrir el ultimo DWG al terminar")
    export.set_defaults(func=cmd_export)

    build = sub.add_parser("build", help="Reconstruye capas a partir de un DXF 2D y edges.json")
    build.add_argument("--dxf", required=True, help="DXF 2D exportado por SketchUp")
    build.add_argument("--edges", required=True, help="JSON de aristas (extract_edges)")
    build.add_argument("--out", required=True, help="DXF de salida")
    build.add_argument("--model-info", help="JSON con la información del modelo")
    build.add_argument("--layers", help="YAML con overrides de capas")
    build.add_argument("--tol", type=float, default=0.05)
    build.set_defaults(func=cmd_build)

    to_dwg = sub.add_parser("to-dwg", help="Convierte un DXF a DWG con accoreconsole")
    to_dwg.add_argument("--dxf", required=True)
    to_dwg.add_argument("--dwg", required=True)
    to_dwg.set_defaults(func=cmd_to_dwg)

    from_dwg = sub.add_parser("from-dwg", help="Convierte un DWG a DXF para auditar")
    from_dwg.add_argument("--dwg", required=True)
    from_dwg.add_argument("--dxf", required=True)
    from_dwg.set_defaults(func=cmd_from_dwg)

    verify = sub.add_parser("verify", help="Audita capas de un DWG o DXF")
    group = verify.add_mutually_exclusive_group(required=True)
    group.add_argument("--dwg")
    group.add_argument("--dxf")
    verify.add_argument("--expected", required=True, help="Capas esperadas separadas por comas")
    verify.add_argument("--allow-layer0", action="store_true",
                        help="No exigir que la capa 0 esté vacía")
    verify.set_defaults(func=cmd_verify)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except (SketchUpError, AccoreConsoleError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
