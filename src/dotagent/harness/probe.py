"""A probe of the harness (item 391, docs/harness.md): one text played on the scoring set, each task a run in a
throw-away home with the exclusions of an eval, within a budget of minutes and tokens, and what each run came to as a
scorecard. A probe runs in a process of its own (`dotx harness probe`): an attempt switches the process's Dot home to
its throw-away one, which the daemon, whose runs go on meanwhile, must never see; the step reads the report back."""

import asyncio
import json
import os
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path

from pydantic import ValidationError

from dotagent.config import AppConfig, load_user_config
from dotagent.core.errors import HarnessError
from dotagent.core.logs import get_logger
from dotagent.core.text import truncate_middle
from dotagent.core.types import FrozenModel
from dotagent.evals.catalog import select_tasks
from dotagent.evals.results import Cut, TaskOutcome
from dotagent.evals.runner import eval_config, lookup_secrets, play, reached_models, secret_names
from dotagent.evals.types import EvalTask
from dotagent.harness.components import GivenText
from dotagent.store.models import RunStatus
from dotagent.tools.shell import read_login_env

log = get_logger(__name__)

# The label of the probe of the adopted texts, which the candidates are compared with.
BASE: str = "base"
# The label of the probe of the adopted texts played again, and of the survivor played again (``confirm-<its id>``), on
# new attempts, before a step adopts the survivor on its own (item 589, review round 1).
CONFIRM_BASE: str = "confirm-base"


def confirm_label(version_id: str) -> str:
    """The label of the confirmation probe of the version ``version_id``."""
    return f"confirm-{version_id}"


# How long the step waits for a probe past its budget of minutes before it ends the process.
_GRACE_S: float = 120.0
# How long a probe told to end (SIGTERM) may take to end its play and the runs it started before it is killed.
_END_S: float = 20.0
_OUTPUT_CHARS: int = 2000
# The most characters of why an attempt did not start that a scorecard keeps.
FAILURE_CHARS: int = 300


class Scorecard(FrozenModel):
    """What one attempt of a probe came to: whether it passed, how the run ended, whether it was cut, and its cost: the
    steps, the tokens (the run's and those of the runs it delegated to), the tool errors, the seconds, the approvals its
    run asked (each declined: nobody is there) and how many of the task's checks held of how many (item 589)."""

    task_id: str
    attempt: int
    passed: bool
    status: RunStatus
    cut: Cut
    steps: int
    input_tokens: int
    output_tokens: int
    tool_errors: int
    seconds: float
    approvals_asked: int = 0
    checks_passed: int = 0
    checks_total: int = 0
    # Whether the attempt's run started (item 717): one that ended failed with no step and no token never did, and says
    # nothing of the text; ``failure`` is why, in the run's own words, for the owner.
    started: bool = True
    failure: str = ""


class ProbeAsk(FrozenModel):
    """What a probe is asked to play: the text under ``label``, the tasks of the scoring set ``repeats`` times, against
    ``model`` (the reference through the scripted model when ``scripted``), within ``max_minutes`` and ``max_tokens``
    of the model under test."""

    label: str
    text: GivenText
    tasks: tuple[str, ...]
    repeats: int
    model: str
    scripted: bool
    max_minutes: float
    max_tokens: int


class ProbeReport(FrozenModel):
    """What a probe came to: a scorecard per attempt, the tokens and seconds it took in all, and why it stopped short
    of its tasks when it did (``cut_short``: ``minutes`` or ``tokens``)."""

    label: str
    tasks: tuple[str, ...]
    repeats: int
    cards: tuple[Scorecard, ...]
    tokens: int
    seconds: float
    cut_short: str | None


type Prober = Callable[[ProbeAsk], Awaitable[ProbeReport]]


def started(outcome: TaskOutcome) -> bool:
    """Whether the run of ``outcome`` started: one that ended failed with no step and no token never reached the model
    (a provider that refused its key, a prompt that could not be built), whatever text it was given."""
    return not (
        outcome.status == "failed"
        and outcome.metrics.steps == 0
        and outcome.metrics.input_tokens + outcome.metrics.output_tokens == 0
    )


def card_of(outcome: TaskOutcome) -> Scorecard:
    began: bool = started(outcome)
    return Scorecard(
        task_id=outcome.task_id,
        attempt=outcome.attempt,
        passed=outcome.passed,
        status=outcome.status,
        cut=outcome.cut,
        steps=outcome.metrics.steps,
        input_tokens=outcome.metrics.input_tokens,
        output_tokens=outcome.metrics.output_tokens,
        tool_errors=outcome.metrics.tool_errors,
        seconds=outcome.seconds,
        approvals_asked=outcome.metrics.approvals_asked or 0,
        checks_passed=sum(1 for check in outcome.checks if check.passed),
        checks_total=len(outcome.checks),
        started=began,
        failure="" if began else truncate_middle(outcome.answer.strip() or "no answer", FAILURE_CHARS),
    )


async def probe(
    config: AppConfig, login_env: Mapping[str, str], secrets: Mapping[str, str], ask: ProbeAsk
) -> ProbeReport:
    """Play ``ask`` in this process, one attempt after another, and stop before the attempt that would pass its budget,
    taking the next attempt to cost what the earlier ones did (:func:`over_budget`). ``config`` is an eval
    configuration (:func:`dotagent.evals.runner.eval_config`)."""
    tasks: tuple[EvalTask, ...] = select_tasks(list(ask.tasks))
    started: float = time.monotonic()
    cards: list[Scorecard] = []
    tokens: int = 0
    cut_short: str | None = None
    attempts = play(config, login_env, secrets, ask.model, tasks, ask.repeats, ask.text, ask.scripted)
    try:
        while (cut_short := over_budget(ask, tokens, time.monotonic() - started, len(cards))) is None:
            try:
                outcome: TaskOutcome = await anext(attempts)
            except StopAsyncIteration:
                break
            card: Scorecard = card_of(outcome)
            cards.append(card)
            tokens += card.input_tokens + card.output_tokens
    finally:
        await attempts.aclose()
    report: ProbeReport = ProbeReport(
        label=ask.label,
        tasks=ask.tasks,
        repeats=ask.repeats,
        cards=tuple(cards),
        tokens=tokens,
        seconds=time.monotonic() - started,
        cut_short=cut_short,
    )
    log.info("harness_probe", label=ask.label, attempts=len(cards), tokens=tokens, cut_short=cut_short)
    return report


def over_budget(ask: ProbeAsk, tokens: int, seconds: float, attempts: int) -> str | None:
    """Why the next attempt of ``ask`` is not played, after ``attempts`` that spent ``tokens`` and ``seconds``:
    ``"tokens"`` or ``"minutes"`` when the budget is spent, or when the next attempt, at the mean cost of those played,
    would pass it; ``None`` when it fits."""
    expected_tokens: float = tokens / attempts if attempts else 0.0
    expected_seconds: float = seconds / attempts if attempts else 0.0
    if tokens + expected_tokens >= ask.max_tokens:
        return "tokens"
    if seconds + expected_seconds >= ask.max_minutes * 60.0:
        return "minutes"
    return None


def nothing_played(ask: ProbeAsk) -> ProbeReport:
    """The report of ``ask`` when none of it is played, the budget having nothing left: no attempt, cut short."""
    return ProbeReport(
        label=ask.label,
        tasks=ask.tasks,
        repeats=ask.repeats,
        cards=(),
        tokens=0,
        seconds=0.0,
        cut_short="tokens" if ask.max_tokens <= 0 else "minutes",
    )


async def probe_here(ask: ProbeAsk) -> ProbeReport:
    """Play ``ask`` in this process on the owner's configuration, as `dotx harness probe` does: for a process that runs
    nothing else, since an attempt switches the process's Dot home."""
    config: AppConfig = eval_config(load_user_config())
    secrets: dict[str, str] = (
        {}
        if ask.scripted
        else await asyncio.to_thread(lookup_secrets, secret_names(config, reached_models(config, ask.model)))
    )
    login_env: dict[str, str] = await read_login_env(config.shell.login_env_timeout_s, ())
    return await probe(config, login_env, secrets, ask)


async def probe_apart(ask: ProbeAsk) -> ProbeReport:
    """Play ``ask`` in a process of its own (`dotx harness probe --ask FILE`, which prints its report as JSON), so that
    the daemon's own home and runs are never touched. A process that outlives the budget by :data:`_GRACE_S` is ended
    and its report is cut short (what it spent is unknown and counts as nothing; its minutes are spent, so what follows
    plays nothing); one is ended too when the step that asked is cancelled (the daemon stopping), so that it never runs
    on. Ending is SIGTERM, which `dotx harness probe` answers by cancelling its play, so that the attempt under way and
    the commands it started end with it, and SIGKILL after :data:`_END_S`. One that fails raises a
    :class:`HarnessError` with what it printed."""
    with tempfile.TemporaryDirectory(prefix="dot-harness-") as folder:
        path: Path = Path(folder) / "ask.json"
        await asyncio.to_thread(path.write_text, ask.model_dump_json(), encoding="utf-8")
        process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-P",
            "-m",
            "dotagent",
            "harness",
            "probe",
            "--ask",
            str(path),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=dict(os.environ),
        )
        try:
            async with asyncio.timeout(ask.max_minutes * 60.0 + _GRACE_S):
                out, err = await process.communicate()
        except TimeoutError:
            log.warning("harness_probe_overran", label=ask.label, minutes=ask.max_minutes)
            return ProbeReport(
                label=ask.label,
                tasks=ask.tasks,
                repeats=ask.repeats,
                cards=(),
                tokens=0,
                seconds=ask.max_minutes * 60.0 + _GRACE_S,
                cut_short="minutes",
            )
        finally:
            if process.returncode is None:
                process.terminate()
                try:
                    async with asyncio.timeout(_END_S):
                        await process.wait()
                except TimeoutError:
                    process.kill()
                    await process.wait()
    if process.returncode != 0:
        raise HarnessError(
            "the probe failed",
            {
                "label": ask.label,
                "exit": process.returncode,
                "stderr": truncate_middle(err.decode("utf-8", "replace"), _OUTPUT_CHARS),
            },
        )
    try:
        return ProbeReport.model_validate_json(out.decode("utf-8", "replace"))
    except ValidationError as exc:
        raise HarnessError(
            "the probe printed no report",
            {"label": ask.label, "stdout": truncate_middle(out.decode("utf-8", "replace"), _OUTPUT_CHARS)},
        ) from exc


def ask_from_file(path: Path) -> ProbeAsk:
    """The ask `dotx harness probe` reads from ``path``."""
    try:
        return ProbeAsk.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise HarnessError("the ask of the probe cannot be read", {"path": str(path), "error": str(exc)}) from exc


def report_json(report: ProbeReport) -> str:
    return json.dumps(json.loads(report.model_dump_json()), ensure_ascii=False)
