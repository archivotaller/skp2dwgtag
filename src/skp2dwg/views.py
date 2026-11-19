"""Definición de vistas: escenas de SketchUp o especificación YAML."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

KINDS = {"top", "front", "back", "left", "right", "section", "scene"}


@dataclass
class ViewSpec:
    name: str
    kind: str
    section: str | None = None
    flip: bool = False


def load_views(path: str | Path) -> list[ViewSpec]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    raw_views = data.get("views") or []
    views: list[ViewSpec] = []
    for item in raw_views:
        kind = str(item.get("kind") or item.get("type") or "").lower()
        if kind not in KINDS:
            raise ValueError(f"Vista '{item.get('name')}': kind inválido '{kind}'")
        name = str(item.get("name") or kind).strip()
        views.append(
            ViewSpec(
                name=name,
                kind=kind,
                section=item.get("section") or item.get("plane"),
                flip=bool(item.get("flip", False)),
            )
        )
    if not views:
        raise ValueError(f"{path} no define ninguna vista")
    return views


def scene_views(info: dict[str, Any]) -> list[ViewSpec]:
    return [ViewSpec(name=name, kind="scene") for name in info.get("pages", [])]


def auto_views(info: dict[str, Any]) -> list[ViewSpec]:
    views = [
        ViewSpec("PLANTA", "top"),
        ViewSpec("ELEV_FRONTAL", "front"),
        ViewSpec("ELEV_POSTERIOR", "back"),
        ViewSpec("ELEV_DERECHA", "right"),
        ViewSpec("ELEV_IZQUIERDA", "left"),
    ]
    for index, section in enumerate(info.get("section_planes", []), start=1):
        name = section.get("name") or f"SECCION_{index}"
        views.append(ViewSpec(f"CORTE_{_slug(name)}", "section", section=section.get("name")))
    return views


def resolve_section(view: ViewSpec, info: dict[str, Any]) -> str | None:
    if view.kind != "section":
        return None
    if view.section:
        return view.section
    sections = info.get("section_planes") or []
    if not sections:
        raise ValueError("La vista de sección no tiene plano y el modelo no tiene secciones")
    return sections[0].get("name")


def camera_for(view: ViewSpec, info: dict[str, Any]) -> tuple[list[float], list[float], list[float]]:
    """Cámara en pulgadas (unidades internas de SketchUp)."""
    bounds = info["bounds"]
    bmin = [float(v) for v in bounds["min"]]
    bmax = [float(v) for v in bounds["max"]]
    center = [(bmin[i] + bmax[i]) / 2.0 for i in range(3)]
    diagonal = math.dist(bmin, bmax)
    distance = max(diagonal * 3.0, 100.0)

    if view.kind == "top":
        return (
            [center[0], center[1], center[2] + distance],
            list(center),
            [0.0, 1.0, 0.0],
        )
    if view.kind == "front":
        return (
            [center[0], center[1] - distance, center[2]],
            list(center),
            [0.0, 0.0, 1.0],
        )
    if view.kind == "back":
        return (
            [center[0], center[1] + distance, center[2]],
            list(center),
            [0.0, 0.0, 1.0],
        )
    if view.kind == "right":
        return (
            [center[0] + distance, center[1], center[2]],
            list(center),
            [0.0, 0.0, 1.0],
        )
    if view.kind == "left":
        return (
            [center[0] - distance, center[1], center[2]],
            list(center),
            [0.0, 0.0, 1.0],
        )
    if view.kind == "section":
        sections = info.get("section_planes", [])
        match = None
        for section in sections:
            if view.section is None or section.get("name") == view.section:
                match = section
                break
        if match is None or not match.get("plane"):
            raise ValueError(
                f"No se encontró el plano de sección '{view.section}' en el modelo"
            )
        a, b, c, d = [float(v) for v in match["plane"]]
        norm = math.sqrt(a * a + b * b + c * c) or 1.0
        normal = [a / norm, b / norm, c / norm]
        point = [-d * normal[0], -d * normal[1], -d * normal[2]]
        sign = -1.0 if view.flip else 1.0
        eye = [point[i] + sign * normal[i] * distance for i in range(3)]
        up = [0.0, 0.0, 1.0] if abs(normal[2]) < 0.9 else [0.0, 1.0, 0.0]
        return (eye, point, up)
    raise ValueError(f"La vista '{view.name}' no tiene cámara propia (kind={view.kind})")


def _slug(text: str) -> str:
    import re

    cleaned = re.sub(r"[^\w\-]+", "_", text, flags=re.UNICODE).strip("_")
    return cleaned.upper() or "VISTA"


def file_stem(name: str) -> str:
    return _slug(name)
