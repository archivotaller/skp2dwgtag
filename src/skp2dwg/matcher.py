"""Atribución de cada línea del dibujo 2D a la etiqueta de SketchUp de origen.

El exportador 2D de SketchUp no escribe capas por etiqueta: entrega líneas en
la capa 0. La atribución se hace proyectando las aristas reales del modelo con
la misma cámara del export y buscando la arista coincidente.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

from .projector import CameraBasis

EPS = 1e-7


@dataclass
class ProjectedEdge:
    u1: float
    v1: float
    u2: float
    v2: float
    tag: str
    depth: float
    length: float


@dataclass
class MatchResult:
    tag: str | None
    distance: float
    candidates: list[str] = field(default_factory=list)
    ambiguous: bool = False
    depth: float = 0.0


class EdgeIndex:
    """Índice espacial de aristas proyectadas.

    Las celdas se calculan rasterizando el segmento (no su bbox): una viga de
    20 m no reserva millones de celdas. El tamaño de celda se escala con el
    modelo para mantener el número de candidatos bajo.
    """

    def __init__(self, edges: list[ProjectedEdge], tol: float = 0.05) -> None:
        self.edges = edges
        self.tol = tol
        if edges:
            us = [e.u1 for e in edges] + [e.u2 for e in edges]
            vs = [e.v1 for e in edges] + [e.v2 for e in edges]
            span = max(max(us) - min(us), max(vs) - min(vs), 1.0)
        else:
            span = 1.0
        self.cell = max(span / 80.0, tol * 40.0, 1.0)
        self.grid: dict[tuple[int, int], list[int]] = defaultdict(list)
        for idx, edge in enumerate(edges):
            for key in self._segment_cells(edge.u1, edge.v1, edge.u2, edge.v2):
                self.grid[key].append(idx)

    def _cell(self, x: float, y: float) -> tuple[int, int]:
        return (int(math.floor(x / self.cell)), int(math.floor(y / self.cell)))

    def _segment_cells(
        self, x1: float, y1: float, x2: float, y2: float, margin: int = 0
    ):
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        steps = max(1, int(length / (self.cell * 0.5)))
        cells = set()
        for i in range(steps + 1):
            t = i / steps
            cx, cy = self._cell(x1 + dx * t, y1 + dy * t)
            for ox in range(-margin, margin + 1):
                for oy in range(-margin, margin + 1):
                    cells.add((cx + ox, cy + oy))
        return cells

    def candidates(self, x1: float, y1: float, x2: float, y2: float):
        seen = set()
        for key in self._segment_cells(x1, y1, x2, y2, margin=1):
            for idx in self.grid.get(key, ()):
                if idx not in seen:
                    seen.add(idx)
                    yield self.edges[idx]


def build_edge_index(
    edges: list[list],
    basis: CameraBasis,
    tol: float = 0.05,
    include_tags: set[str] | None = None,
) -> EdgeIndex:
    projected: list[ProjectedEdge] = []
    for item in edges:
        ax, ay, az, bx, by, bz, tag, visible = item[:8]
        if not visible:
            continue
        tag = tag or "Layer0"
        if include_tags is not None and tag not in include_tags:
            continue
        u1, v1 = basis.project((ax, ay, az))
        u2, v2 = basis.project((bx, by, bz))
        length = math.hypot(u2 - u1, v2 - v1)
        if length <= tol * 0.5:
            continue
        depth = basis.depth(
            ((ax + bx) / 2.0, (ay + by) / 2.0, (az + bz) / 2.0)
        )
        projected.append(ProjectedEdge(u1, v1, u2, v2, tag, depth, length))
    return EdgeIndex(projected, tol=tol)


def _point_segment(px, py, x1, y1, x2, y2):
    """Distancia al segmento y parámetro t sin recortar."""
    dx, dy = x2 - x1, y2 - y1
    l2 = dx * dx + dy * dy
    if l2 <= EPS:
        return math.hypot(px - x1, py - y1), 0.0, 0.0
    t = ((px - x1) * dx + (py - y1) * dy) / l2
    tc = min(1.0, max(0.0, t))
    cx, cy = x1 + tc * dx, y1 + tc * dy
    return math.hypot(px - cx, py - cy), t, math.sqrt(l2)


def match_line(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    index: EdgeIndex,
    tol: float = 0.05,
    depth_eps: float = 0.01,
) -> MatchResult:
    hits: list[tuple[float, ProjectedEdge, float, float]] = []
    for edge in index.candidates(x1, y1, x2, y2):
        d1, t1, _ = _point_segment(x1, y1, edge.u1, edge.v1, edge.u2, edge.v2)
        d2, t2, _ = _point_segment(x2, y2, edge.u1, edge.v1, edge.u2, edge.v2)
        if d1 <= tol and d2 <= tol and -0.01 <= t1 <= 1.01 and -0.01 <= t2 <= 1.01:
            hits.append((d1 + d2, edge, min(t1, t2), max(t1, t2)))

    if not hits:
        # líneas que el exportador fusionó a partir de varias aristas colineales
        for edge in index.candidates(x1, y1, x2, y2):
            d1, t1, _ = _point_segment(x1, y1, edge.u1, edge.v1, edge.u2, edge.v2)
            d2, t2, _ = _point_segment(x2, y2, edge.u1, edge.v1, edge.u2, edge.v2)
            if d1 <= tol * 4 and d2 <= tol * 4:
                lo, hi = min(t1, t2), max(t1, t2)
                overlap = max(0.0, min(hi, 1.0) - max(lo, 0.0))
                span = min(1.0, hi - lo)
                if span > 0 and overlap / span >= 0.9:
                    hits.append((d1 + d2, edge, lo, hi))

    if not hits:
        return MatchResult(tag=None, distance=float("inf"))

    best_distance = min(h[0] for h in hits)
    near = [h for h in hits if h[0] <= best_distance + tol * 0.5]
    min_depth = min(h[1].depth for h in near)
    top = [h for h in near if h[1].depth <= min_depth + depth_eps]

    tags = sorted({h[1].tag for h in top})
    if len(tags) > 1 and "Layer0" in tags:
        tags_non_default = [t for t in tags if t != "Layer0"]
        if len(tags_non_default) == 1:
            tags = tags_non_default
            top = [h for h in top if h[1].tag == tags[0]]

    chosen = min(top, key=lambda h: (h[1].depth, h[0]))
    return MatchResult(
        tag=chosen[1].tag,
        distance=chosen[0],
        candidates=tags,
        ambiguous=len(tags) > 1,
        depth=chosen[1].depth,
    )
