"""Auditoría del DWG final: capas, geometría en capa 0 y unidades."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from collections import Counter

import ezdxf

from .dwg import dwg_to_dxf


@dataclass
class VerifyResult:
    ok: bool
    layers_found: list[str] = field(default_factory=list)
    missing_layers: list[str] = field(default_factory=list)
    extra_layers: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    layer0_entities: int = 0
    insunits: int | None = None
    warnings: list[str] = field(default_factory=list)


def summarize_dxf(path: str | Path) -> tuple[list[str], Counter, int | None]:
    doc = ezdxf.readfile(str(path))
    msp = doc.modelspace()
    layers = [layer.dxf.name for layer in doc.layers]
    counts = Counter(entity.dxf.layer for entity in msp)
    insunits = doc.header.get("$INSUNITS")
    return layers, counts, insunits


def verify_dwg(
    dwg: str | Path,
    expected_layers: list[str],
    require_layer0_empty: bool = True,
) -> VerifyResult:
    with tempfile.TemporaryDirectory(prefix="skp2dwg_verify_") as tmp:
        dxf = Path(tmp) / "verify.dxf"
        dwg_to_dxf(dwg, dxf)
        layers, counts, insunits = summarize_dxf(dxf)

    found = set(layers)
    expected = set(expected_layers)
    missing = sorted(expected - found)
    extra = sorted(found - expected - {"0", "Defpoints"})
    layer0 = int(counts.get("0", 0))
    warnings: list[str] = []
    if layer0 and require_layer0_empty:
        warnings.append(f"La capa 0 tiene {layer0} entidades")
    if insunits not in (4, 5, 6, 1, 2):
        warnings.append(f"$INSUNITS inesperado: {insunits}")
    ok = not missing and not (layer0 and require_layer0_empty)
    return VerifyResult(
        ok=ok,
        layers_found=sorted(found),
        missing_layers=missing,
        extra_layers=extra,
        counts=dict(counts),
        layer0_entities=layer0,
        insunits=insunits,
        warnings=warnings,
    )


def verify_dxf(dxf: str | Path, expected_layers: list[str]) -> VerifyResult:
    layers, counts, insunits = summarize_dxf(dxf)
    found = set(layers)
    expected = set(expected_layers)
    missing = sorted(expected - found)
    extra = sorted(found - expected - {"0", "Defpoints"})
    layer0 = int(counts.get("0", 0))
    warnings = []
    if layer0:
        warnings.append(f"La capa 0 tiene {layer0} entidades")
    return VerifyResult(
        ok=not missing and not layer0,
        layers_found=sorted(found),
        missing_layers=missing,
        extra_layers=extra,
        counts=dict(counts),
        layer0_entities=layer0,
        insunits=insunits,
        warnings=warnings,
    )
