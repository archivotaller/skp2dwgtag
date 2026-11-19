"""Conversión DXF <-> DWG con el núcleo de AutoCAD (accoreconsole)."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000


class AccoreConsoleError(RuntimeError):
    pass


@dataclass
class ConversionResult:
    input_path: Path
    output_path: Path
    seconds: float
    log: str


def find_accoreconsole() -> Path:
    env = os.environ.get("ACCORECONSOLE")
    if env and Path(env).exists():
        return Path(env)
    roots = [Path(r"C:\Program Files\Autodesk")]
    candidates: list[Path] = []
    for root in roots:
        if root.exists():
            candidates.extend(root.glob("AutoCAD */accoreconsole.exe"))
    if not candidates:
        raise AccoreConsoleError(
            "No se encontró accoreconsole.exe. Instala AutoCAD o define ACCORECONSOLE."
        )
    candidates.sort(key=lambda p: p.parent.name, reverse=True)
    return candidates[0]


def _script(lines: list[str]) -> str:
    return "\r\n".join(lines) + "\r\n"


def _run(accore: Path, source: Path, scr_text: str, timeout: float) -> str:
    with tempfile.TemporaryDirectory(prefix="skp2dwg_accore_") as tmp:
        script = Path(tmp) / "run.scr"
        script.write_bytes(scr_text.encode("ascii", errors="replace"))
        start = time.time()
        proc = subprocess.run(
            [str(accore), "/i", str(source), "/s", str(script)],
            capture_output=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
        )
        elapsed = time.time() - start
        raw = proc.stdout or b""
        for encoding in ("utf-16", "utf-8", "cp1252"):
            try:
                log = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            log = raw.decode("utf-8", errors="replace")
        if proc.returncode != 0:
            raise AccoreConsoleError(
                f"accoreconsole terminó con {proc.returncode} en {elapsed:.1f}s\n{log[-3000:]}"
            )
        return log


def dxf_to_dwg(dxf: str | Path, dwg: str | Path, timeout: float = 240.0) -> ConversionResult:
    dxf = Path(dxf)
    dwg = Path(dwg)
    dwg.parent.mkdir(parents=True, exist_ok=True)
    if dwg.exists():
        dwg.unlink()
    accore = find_accoreconsole()
    script = _script(
        [
            "EXPERT 1",
            "FILEDIA 0",
            "CMDDIA 0",
            "_.QSAVE",
            "2018",
            f'"{dwg}"',
        ]
    )
    start = time.time()
    log = _run(accore, dxf, script, timeout)
    elapsed = time.time() - start
    if not dwg.exists() or dwg.stat().st_size == 0:
        raise AccoreConsoleError(
            f"accoreconsole no generó {dwg}.\n{log[-3000:]}"
        )
    return ConversionResult(dxf, dwg, elapsed, log)


def dwg_to_dxf(dwg: str | Path, dxf: str | Path, timeout: float = 240.0) -> ConversionResult:
    dwg = Path(dwg)
    dxf = Path(dxf)
    dxf.parent.mkdir(parents=True, exist_ok=True)
    if dxf.exists():
        dxf.unlink()
    accore = find_accoreconsole()
    script = _script(
        [
            "EXPERT 1",
            "FILEDIA 0",
            "CMDDIA 0",
            "_.DXFOUT",
            f'"{dxf}"',
            "16",
        ]
    )
    start = time.time()
    log = _run(accore, dwg, script, timeout)
    elapsed = time.time() - start
    if not dxf.exists() or dxf.stat().st_size == 0:
        raise AccoreConsoleError(f"accoreconsole no generó {dxf}.\n{log[-3000:]}")
    return ConversionResult(dwg, dxf, elapsed, log)
