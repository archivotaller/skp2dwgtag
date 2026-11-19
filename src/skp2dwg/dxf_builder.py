"""Construcción del DXF final: una capa de AutoCAD por etiqueta de SketchUp."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import ezdxf
from ezdxf.colors import rgb2int

from .matcher import build_edge_index, match_line
from .projector import CameraBasis, insunits

INVALID_LAYER_CHARS = re.compile(r'[<>/\\":;?*|=`\x00-\x1f]')
MAX_LAYER_LEN = 255

DEFAULT_LINEWEIGHT = 25  # 0.25 mm en centésimas de mm


@dataclass
class LayerSpec:
    name: str
    source: str
    rgb: tuple[int, int, int] | None = None
    hidden: bool = False
    lineweight: int = DEFAULT_LINEWEIGHT
    linetype: str = "CONTINUOUS"


@dataclass
class BuildStats:
    entities_in: int = 0
    entities_out: int = 0
    lines: int = 0
    matched: int = 0
    unmatched: int = 0
    ambiguous: int = 0
    by_tag: dict[str, int] = field(default_factory=dict)
    layer_names: dict[str, str] = field(default_factory=dict)
    cut_segments: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def unmatched_ratio(self) -> float:
        return (self.unmatched / self.lines) if self.lines else 0.0


def sanitize_layer_name(name: str) -> str:
    cleaned = INVALID_LAYER_CHARS.sub("_", (name or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        cleaned = "SIN_NOMBRE"
    return cleaned[:MAX_LAYER_LEN]


def make_layer_plans(
    model_layers: list[dict],
    config: dict | None = None,
) -> tuple[dict[str, LayerSpec], list[str]]:
    """Devuelve {etiqueta_original: LayerSpec} y avisos de renombres."""
    config = config or {}
    overrides = config.get("layers") or {}
    defaults = config.get("defaults") or {}
    used: dict[str, str] = {}
    plans: dict[str, LayerSpec] = {}
    warnings: list[str] = []

    for layer in model_layers:
        source = layer["name"]
        override = overrides.get(source) or {}
        desired = override.get("name", source)
        safe = sanitize_layer_name(desired)
        key = safe.casefold()
        if key in used and used[key] != source:
            suffix = 2
            while f"{safe}_{suffix}".casefold() in used:
                suffix += 1
            warnings.append(
                f"Nombre de capa duplicado: '{desired}' -> '{safe}_{suffix}'"
            )
            safe = f"{safe}_{suffix}"
        used[safe.casefold()] = source

        rgb = override.get("color")
        if not rgb:
            rgb = layer.get("rgb")
        lineweight = int(override.get("lineweight", defaults.get("lineweight", DEFAULT_LINEWEIGHT)))
        plans[source] = LayerSpec(
            name=safe,
            source=source,
            rgb=tuple(rgb) if rgb else None,
            hidden=bool(override.get("hidden", not layer.get("visible", True))),
            lineweight=lineweight,
            linetype=override.get("linetype", "CONTINUOUS"),
        )
    return plans, warnings


def _layer_attribs(spec: LayerSpec) -> dict:
    attrs = {"color": 7}
    return attrs


def _ensure_layer(doc, spec: LayerSpec):
    if spec.name in doc.layers:
        layer = doc.layers.get(spec.name)
    else:
        layer = doc.layers.add(spec.name, color=7)
    if spec.rgb:
        layer.dxf.true_color = rgb2int(spec.rgb)
    layer.dxf.lineweight = int(spec.lineweight)
    try:
        layer.dxf.linetype = spec.linetype
    except Exception:
        pass
    if spec.hidden:
        try:
            layer.freeze()
        except Exception:
            pass
        try:
            layer.off()
        except Exception:
            pass
    return layer


def _explode(entity):
    """Devuelve una lista de segmentos ((x1,y1),(x2,y2)) para LINE/LWPOLYLINE."""
    kind = entity.dxftype()
    if kind == "LINE":
        return [
            (
                (entity.dxf.start.x, entity.dxf.start.y),
                (entity.dxf.end.x, entity.dxf.end.y),
            )
        ]
    if kind == "LWPOLYLINE":
        pts = [(p[0], p[1]) for p in entity.get_points("xy")]
        if len(pts) < 2:
            return []
        segs = list(zip(pts, pts[1:]))
        if entity.closed and pts[0] != pts[-1]:
            segs.append((pts[-1], pts[0]))
        return segs
    if kind == "POLYLINE":
        pts = [(v.dxf.location.x, v.dxf.location.y) for v in entity.vertices]
        if len(pts) < 2:
            return []
        segs = list(zip(pts, pts[1:]))
        if entity.is_closed and pts[0] != pts[-1]:
            segs.append((pts[-1], pts[0]))
        return segs
    return []


def build_layered_dxf(
    dxf_in: str | Path,
    edges_json: str | Path,
    out_dxf: str | Path,
    model_layers: list[dict],
    layer_config: dict | None = None,
    tol: float = 0.05,
    cut_json: str | Path | None = None,
) -> BuildStats:
    import json

    payload = json.loads(Path(edges_json).read_text(encoding="utf-8"))
    units = int(payload.get("units", 2))
    basis = CameraBasis.from_camera(payload["camera"], units)
    index = build_edge_index(payload["edges"], basis, tol=tol)

    plans, warnings = make_layer_plans(model_layers, layer_config)
    stats = BuildStats(warnings=list(warnings))
    stats.layer_names = {src: spec.name for src, spec in plans.items()}

    cfg = (layer_config or {}).get("fallback") or {}
    fallback_names = {
        "unmatched": sanitize_layer_name(cfg.get("unmatched", "SIN_ETIQUETA")),
        "section": sanitize_layer_name(cfg.get("section", "CORTE")),
        "profile": sanitize_layer_name(cfg.get("profile", "PERFIL")),
        "text": sanitize_layer_name(cfg.get("text", "TEXTO")),
    }
    fallback_specs = {
        "unmatched": LayerSpec(fallback_names["unmatched"], "SIN_ETIQUETA", (255, 0, 255)),
        "section": LayerSpec(fallback_names["section"], "CORTE", (255, 0, 0)),
        "profile": LayerSpec(fallback_names["profile"], "PERFIL", (0, 128, 255)),
        "text": LayerSpec(fallback_names["text"], "TEXTO", (255, 255, 0)),
    }

    src = ezdxf.readfile(str(dxf_in))
    doc = ezdxf.new("R2013", setup=False)
    doc.header["$INSUNITS"] = insunits(units)
    doc.header["$MEASUREMENT"] = 1 if units in (2, 3, 4) else 0
    doc.header["$LUNITS"] = 2
    doc.header["$LUPREC"] = 3
    doc.header["$LTSCALE"] = 1.0

    for spec in plans.values():
        _ensure_layer(doc, spec)
    for spec in fallback_specs.values():
        _ensure_layer(doc, spec)
    for fallback_name in fallback_names.values():
        if fallback_name not in doc.layers:
            doc.layers.add(fallback_name, color=7)

    msp_in = src.modelspace()
    msp_out = doc.modelspace()
    section_lines_in_export = False

    for entity in msp_in:
        stats.entities_in += 1
        kind = entity.dxftype()
        src_layer = entity.dxf.layer
        if src_layer == "SectionCutEdges":
            section_lines_in_export = True
        segments = _explode(entity)

        if kind in ("TEXT", "MTEXT"):
            attrs = {"layer": fallback_names["text"], "color": 256}
            if kind == "TEXT":
                msp_out.add_text(
                    entity.dxf.text,
                    dxfattribs={
                        **attrs,
                        "insert": entity.dxf.insert,
                        "height": entity.dxf.get("height", 2.5),
                        "rotation": entity.dxf.get("rotation", 0),
                    },
                )
            else:
                msp_out.add_mtext(
                    entity.text,
                    dxfattribs={
                        **attrs,
                        "insert": entity.dxf.insert,
                        "char_height": entity.dxf.get("char_height", 2.5),
                    },
                )
            stats.entities_out += 1
            continue

        if not segments:
            # arcos, círculos, splines: no se intenta atribuir
            stats.unmatched += 1
            stats.warnings.append(f"Entidad no soportada: {kind} en capa {src_layer}")
            continue

        stats.lines += len(segments)
        seg_tags: list[str] = []
        for (a, b) in segments:
            result = match_line(a[0], a[1], b[0], b[1], index, tol=tol)
            if result.tag is None:
                stats.unmatched += 1
                tag = None
            else:
                stats.matched += 1
                tag = result.tag
                if result.ambiguous:
                    stats.ambiguous += 1
            if src_layer == "SectionCutEdges":
                tag = None
                target = fallback_names["section"]
            elif src_layer == "ProfileEdges":
                target = fallback_names["profile"]
            elif tag is None:
                target = fallback_names["unmatched"]
            else:
                target = plans.get(tag, fallback_specs["unmatched"]).name
                stats.by_tag[tag] = stats.by_tag.get(tag, 0) + 1
            seg_tags.append(target)
            if kind == "LINE":
                msp_out.add_line(
                    (a[0], a[1]),
                    (b[0], b[1]),
                    dxfattribs={"layer": target, "color": 256},
                )
                stats.entities_out += 1

        if kind != "LINE" and seg_tags:
            # polilínea: se conserva la geometría y se usa la etiqueta mayoritaria
            counts: dict[str, int] = {}
            for name in seg_tags:
                counts[name] = counts.get(name, 0) + 1
            target = max(counts, key=counts.get)
            attribs = {"layer": target, "color": 256}
            try:
                if kind == "LWPOLYLINE":
                    pts = [(p[0], p[1], p[4]) for p in entity.get_points("xyseb")]
                    msp_out.add_lwpolyline(
                        pts, format="xyb", close=entity.closed, dxfattribs=attribs
                    )
                else:
                    pts = [(v.dxf.location.x, v.dxf.location.y) for v in entity.vertices]
                    msp_out.add_lwpolyline(
                        pts, close=entity.is_closed, dxfattribs=attribs
                    )
                stats.entities_out += 1
            except Exception as exc:  # pragma: no cover - defensivo
                stats.warnings.append(f"No se pudo copiar {kind}: {exc}")

    if cut_json and not section_lines_in_export and Path(cut_json).exists():
        cut_payload = json.loads(Path(cut_json).read_text(encoding="utf-8"))
        layer_name = fallback_names["section"]
        for seg in cut_payload.get("segments", []):
            x1, y1, z1, x2, y2, z2 = seg[:6]
            u1, v1 = basis.project((x1, y1, z1))
            u2, v2 = basis.project((x2, y2, z2))
            if math.hypot(u2 - u1, v2 - v1) <= 1e-6:
                continue
            msp_out.add_line(
                (u1, v1), (u2, v2), dxfattribs={"layer": layer_name, "color": 256}
            )
            stats.cut_segments += 1
            stats.entities_out += 1

    if stats.unmatched_ratio > 0.35:
        stats.warnings.append(
            "Más del 35% de las líneas quedaron sin etiqueta. "
            "Revisa que la vista sea ortogonal y que la escala sea 1:1."
        )

    doc.saveas(str(out_dxf))
    return stats
