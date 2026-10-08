"""Eval reports on disk: one JSON file per run, named after the time it started and the model it played."""

import re
import time
from pathlib import Path

from pydantic import ValidationError

from dotagent.core.errors import EvalError
from dotagent.evals.results import EvalReport


def report_file_name(model: str, started_at: float) -> str:
    stamp: str = time.strftime("%Y%m%d-%H%M%S", time.gmtime(started_at))
    slug: str = re.sub(r"[^a-z0-9]+", "-", model.lower()).strip("-")
    return f"{stamp}-{slug}.json"


def save_report(directory: Path, report: EvalReport) -> Path:
    """Write ``report`` into ``directory`` (created when missing) and return the file. Blocking."""
    directory.mkdir(parents=True, exist_ok=True)
    path: Path = directory / report_file_name(report.model, report.started_at)
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_report(path: Path) -> EvalReport:
    """The report in ``path``. Blocking."""
    try:
        text: str = path.read_text(encoding="utf-8")
    except OSError as error:
        raise EvalError("a report could not be read", {"path": str(path), "error": str(error)}) from error
    try:
        return EvalReport.model_validate_json(text)
    except ValidationError as error:
        raise EvalError(
            "the file is not an eval report",
            {"path": str(path), "errors": error.json(include_url=False, include_input=False)},
        ) from error
