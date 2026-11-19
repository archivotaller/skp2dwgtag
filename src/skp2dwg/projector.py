"""Proyección ortográfica de la geometría de SketchUp al plano del dibujo 2D.

SketchUp trabaja internamente en pulgadas. El exportador 2D escribe en las
unidades de visualización del modelo y toma como origen el ``target`` de la
cámara, con ejes ``right``/``up`` de la vista.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# unidades de visualización de SketchUp -> unidades por pulgada
UNITS_PER_INCH = {
    0: 1.0,          # pulgadas
    1: 1.0 / 12.0,   # pies
    2: 25.4,         # milímetros
    3: 2.54,         # centímetros
    4: 0.0254,       # metros
    5: 1.0 / 36.0,   # yardas
}

INSUNITS = {
    0: 1,   # inch
    1: 2,   # foot
    2: 4,   # mm
    3: 5,   # cm
    4: 6,   # m
    5: 10,  # yard
}

UNIT_LABEL = {0: "in", 1: "ft", 2: "mm", 3: "cm", 4: "m", 5: "yd"}


def units_per_inch(units: int) -> float:
    return UNITS_PER_INCH.get(int(units), 25.4)


def insunits(units: int) -> int:
    return INSUNITS.get(int(units), 4)


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _normalize(a):
    length = math.sqrt(_dot(a, a))
    if length < 1e-12:
        raise ValueError("Vector nulo")
    return (a[0] / length, a[1] / length, a[2] / length)


@dataclass(frozen=True)
class CameraBasis:
    eye: tuple[float, float, float]
    target: tuple[float, float, float]
    forward: tuple[float, float, float]
    right: tuple[float, float, float]
    up: tuple[float, float, float]
    scale: float
    perspective: bool = False

    @classmethod
    def from_camera(cls, camera: dict, units: int) -> "CameraBasis":
        eye = tuple(float(v) for v in camera["eye"])
        target = tuple(float(v) for v in camera["target"])
        up = tuple(float(v) for v in camera["up"])
        forward = _normalize(_sub(target, eye))
        right = _normalize(_cross(forward, up))
        # re-ortogonalizamos el up de la vista
        up2 = _cross(right, forward)
        return cls(
            eye=eye,
            target=target,
            forward=forward,
            right=right,
            up=up2,
            scale=units_per_inch(units),
            perspective=bool(camera.get("perspective")),
        )

    def project(self, point) -> tuple[float, float]:
        """Devuelve coordenadas en el plano del DXF (unidades del modelo)."""
        delta = _sub(point, self.target)
        u = _dot(delta, self.right) * self.scale
        v = _dot(delta, self.up) * self.scale
        return (u, v)

    def depth(self, point) -> float:
        """Distancia en el eje de vista (menor = más cerca de la cámara)."""
        return _dot(_sub(point, self.eye), self.forward)


def project_polyline(points, basis: CameraBasis):
    return [basis.project(p) for p in points]
