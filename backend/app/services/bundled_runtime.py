"""Expose bundled model/program resources through the PAW runtime cache."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

_PROGRAM_ID = re.compile(r"^[a-f0-9]{20}$")


def install() -> dict:
    from programasweights.config import get_base_models_dir, get_programs_dir

    result = {"model": False, "programs": []}
    model_source = Path(
        os.environ.get("PREPARE_OFFLINE_MODEL_PATH", "")
    ).expanduser()
    if model_source.is_file():
        target = get_base_models_dir() / "qwen3-0.6b-q6_k.gguf"
        if not target.exists():
            try:
                target.symlink_to(model_source)
            except OSError:
                shutil.copy2(model_source, target)
        result["model"] = target.exists()

    programs_source = Path(
        os.environ.get("PREPARE_OFFLINE_PAW_PROGRAMS_PATH", "")
    ).expanduser()
    if programs_source.is_dir():
        destination_root = get_programs_dir()
        for source in programs_source.iterdir():
            if not source.is_dir() or not _PROGRAM_ID.fullmatch(source.name):
                continue
            destination = destination_root / source.name
            if not destination.exists():
                shutil.copytree(source, destination)
            if destination.is_dir():
                result["programs"].append(source.name)
    return result
