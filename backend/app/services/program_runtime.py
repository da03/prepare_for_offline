"""Run one compiled PAW program at a time with a hard timeout."""

from __future__ import annotations

import multiprocessing
import os
import time
from dataclasses import dataclass

from .neural_worker import run_child


@dataclass(frozen=True)
class ProgramResult:
    output: str
    elapsed_ms: float
    peak_rss_mb: float
    isolated: bool


def run(
    program_id: str,
    text: str,
    *,
    max_tokens: int = 320,
    timeout_seconds: float = 30.0,
) -> ProgramResult:
    if os.environ.get("PFO_NEURAL_IN_PROCESS") == "1":
        import programasweights as paw

        started = time.perf_counter()
        function = paw.function(program_id, offline=True)
        output = function(text, max_tokens=max_tokens, temperature=0.0)
        return ProgramResult(
            output=str(output).strip(),
            elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
            peak_rss_mb=0.0,
            isolated=False,
        )

    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    process = context.Process(
        target=run_child,
        args=(send, program_id, text, max_tokens),
        daemon=True,
    )
    process.start()
    send.close()
    try:
        if not receive.poll(timeout_seconds):
            process.terminate()
            process.join(timeout=2)
            raise TimeoutError(f"PAW program {program_id} timed out")
        try:
            payload = receive.recv()
        except EOFError as exc:
            raise RuntimeError(
                f"PAW program worker {program_id} exited unexpectedly"
            ) from exc
    finally:
        receive.close()
    process.join(timeout=2)
    if process.is_alive():
        process.terminate()
        process.join(timeout=2)
    if not payload.get("ok"):
        raise RuntimeError(payload.get("error") or "PAW program failed")
    return ProgramResult(
        output=payload["output"],
        elapsed_ms=float(payload["elapsed_ms"]),
        peak_rss_mb=float(payload["peak_rss_mb"]),
        isolated=True,
    )
