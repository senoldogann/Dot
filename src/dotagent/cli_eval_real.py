"""``dotx eval suite``, ``real``, ``ablate`` and ``costs``: the evals drawn from the owner's
real runs (docs/development.md, "Evals from real runs").

``suite`` and ``costs`` read Dot's record opened read-only and call no model; ``suite`` writes the suite to
``<home>/evals/real/suite.json``. ``real`` plays that suite against a model and ``ablate`` plays its goals with and
without a skill or a note and has a model judge the answers: both spend that model's tokens.
"""

import asyncio
import sys
import time
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from dotagent.cli_commands import (
    Command,
    EvalAblateCommand,
    EvalCostsCommand,
    EvalRealCommand,
    EvalSuiteCommand,
)
from dotagent.cli_skills_lint import find_skill, installed_skills
from dotagent.config import AppConfig, load_user_config
from dotagent.core.errors import ConfigError, InvalidValueError
from dotagent.core.paths import db_path, evals_dir
from dotagent.evals.ablation import (
    WITH,
    WITHOUT,
    WITHOUT_AGAIN,
    Pairing,
    Variant,
    findings,
    note_variants,
    part_variants,
    render_findings,
    skill_variants,
)
from dotagent.evals.ablation_play import judge_pairs
from dotagent.evals.call_costs import CallCost, DailyCost, daily_costs, read_call_costs
from dotagent.evals.catalog import select_tasks
from dotagent.evals.real_runs import (
    GOLDEN_TASKS,
    RealCase,
    RealGrade,
    Suite,
    golden_cases,
    grade,
    mix_suite,
    owner_cases,
    read_owner_cases,
    read_real_cases,
    render_grades,
    render_suite,
    task_of,
)
from dotagent.evals.results import TaskOutcome
from dotagent.evals.runner import (
    eval_config,
    lookup_secrets,
    play_attempt,
    reached_models,
    required_secret_names,
    secret_names,
)
from dotagent.evals.types import EvalTask, SkillSeed
from dotagent.llm.router import parse_model_ref
from dotagent.skills.types import Skill, SkillCatalog
from dotagent.tools.shell import read_login_env

DAY_S: float = 86_400.0
# The names a day of the costs table shows, the dearest first.
_NAMES_A_DAY: int = 8


def _write(lines: Sequence[str]) -> None:
    sys.stdout.write("\n".join(lines) + "\n")


def real_dir() -> Path:
    """Where the suite of real runs and the owner's own cases are kept."""
    return evals_dir() / "real"


def suite_path() -> Path:
    return real_dir() / "suite.json"


def owner_path() -> Path:
    return real_dir() / "owner.json"


def _save_suite(suite: Suite) -> Path:
    path: Path = suite_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(suite.model_dump_json(indent=2), encoding="utf-8")
    return path


def _load_suite() -> Suite:
    path: Path = suite_path()
    if not path.exists():
        raise ConfigError("there is no suite of real runs yet", {"path": str(path), "fix": "run: dotx eval suite"})
    return Suite.model_validate_json(path.read_bytes())


async def cmd_eval_suite(command: EvalSuiteCommand) -> int:
    """Build the suite from the owner's runs of the last ``days``, the catalog's golden tasks and ``owner.json``."""
    now: float = time.time()
    real: tuple[RealCase, ...] = await asyncio.to_thread(read_real_cases, db_path(), now - command.days * DAY_S)
    owner: tuple[RealCase, ...] = owner_cases(await asyncio.to_thread(read_owner_cases, owner_path()))
    suite: Suite = mix_suite(golden_cases(select_tasks(GOLDEN_TASKS)), real, owner, command.size, now)
    path: Path = await asyncio.to_thread(_save_suite, suite)
    _write([*render_suite(suite), f"Saved: {path}", f"Your own cases are read from {owner_path()}."])
    return 0


def seed_of(skill: Skill) -> SkillSeed:
    return SkillSeed(name=skill.name, description=skill.description, body=skill.body)


async def _secrets(config: AppConfig, models: Sequence[str]) -> dict[str, str]:
    """The API keys the attempts and the judge need, every one present."""
    reached: list[str] = [name for model in models for name in reached_models(config, model)]
    found: dict[str, str] = await asyncio.to_thread(lookup_secrets, secret_names(config, reached))
    missing: list[str] = [name for name in required_secret_names(config, models) if name not in found]
    if missing:
        raise ConfigError(
            "an API key the eval needs is missing",
            {
                "names": ", ".join(missing),
                "hint": "export it, add it to the .env file of the Dot home, or run: dotx auth set NAME",
            },
        )
    return found


def _task(case: RealCase, skills: Sequence[SkillSeed]) -> EvalTask:
    """The task of a case: a golden case's catalog task, else the case's own."""
    if case.origin == "golden":
        return select_tasks((case.id,))[0]
    return task_of(case, skills)


async def cmd_eval_real(command: EvalRealCommand) -> int:
    """Play the saved suite against the model, ``repeat`` times, and grade each attempt on its way and its ending."""
    config: AppConfig = load_user_config()
    model: str = command.model if command.model is not None else config.models.main
    parse_model_ref(model)
    suite: Suite = await asyncio.to_thread(_load_suite)
    found: dict[str, str] = await _secrets(config, (model,))
    login_env: dict[str, str] = await read_login_env(config.shell.login_env_timeout_s, ())
    run_config: AppConfig = eval_config(config)
    # Every skill a case read is found before any case is played: one gone fails the command before it spends tokens.
    catalog: SkillCatalog = await asyncio.to_thread(installed_skills)
    seeds_by_name: dict[str, SkillSeed] = {
        name: seed_of(find_skill(catalog, name)) for case in suite.cases for name in case.skills
    }
    _write(
        [
            f"Playing {len(suite.cases)} cases, {command.repeat} time(s) each, against {model}, each in a throw-away "
            "home and held to its workspace. This spends that model's tokens."
        ]
    )
    grades: list[RealGrade] = []
    for attempt in range(1, command.repeat + 1):
        for case in suite.cases:
            seeds: list[SkillSeed] = [seeds_by_name[name] for name in case.skills]
            outcome: TaskOutcome = await play_attempt(
                _task(case, seeds), run_config, login_env, found, model, attempt, ()
            )
            graded: RealGrade = grade(case, outcome)
            grades.append(graded)
            _write([f"  {'PASS' if graded.passed else 'FAIL'}  {case.id} #{attempt}  order {graded.order:.0%}"])
    _write(render_grades(grades))
    return 0


def _ablation_cases(suite: Suite, command: EvalAblateCommand) -> tuple[RealCase, ...]:
    """The cases an ablation plays: those named, else those whose run read the skill."""
    playable: tuple[RealCase, ...] = tuple(case for case in suite.cases if case.origin != "golden")
    if command.cases:
        known: dict[str, RealCase] = {case.id: case for case in playable}
        unknown: list[str] = [case_id for case_id in command.cases if case_id not in known]
        if unknown:
            raise InvalidValueError("no such real or owner case in the suite", {"cases": ", ".join(unknown)})
        return tuple(known[case_id] for case_id in command.cases)
    reading: tuple[RealCase, ...] = tuple(
        case for case in playable if command.skill is not None and command.skill in case.skills
    )
    if not reading:
        raise InvalidValueError(
            "no case of the suite read that skill: name the cases to play it on",
            {"skill": command.skill, "note": command.note is not None, "fix": "--case ID, as dotx eval suite lists"},
        )
    return reading


def _variants(command: EvalAblateCommand) -> tuple[str, tuple[Variant, ...]]:
    """What is ablated and its arms."""
    if command.skill is not None:
        seed: SkillSeed = seed_of(find_skill(installed_skills(), command.skill))
        arms: tuple[Variant, ...] = skill_variants(seed)
        return f"the skill {command.skill}", (*arms, *part_variants(seed)) if command.parts else arms
    if command.note is not None:
        return "the note", note_variants(command.note)
    raise InvalidValueError("name a skill or a note to ablate", {"fix": "--skill NAME or --note TEXT"})


def _pairs(variants: Sequence[Variant]) -> tuple[tuple[str, str], ...]:
    """The pairs judged, each variant on the left: *with* against *without*, the two *without* (the noise band), each
    part against the whole."""
    parts: list[tuple[str, str]] = [
        (variant.name, WITH) for variant in variants if variant.name not in (WITH, WITHOUT, WITHOUT_AGAIN)
    ]
    return ((WITH, WITHOUT), (WITHOUT, WITHOUT_AGAIN), *parts)


async def cmd_eval_ablate(command: EvalAblateCommand) -> int:
    """Play the cases with each arm, have the judge pick the better answer of each pair, and say what helps."""
    config: AppConfig = load_user_config()
    model: str = command.model if command.model is not None else config.models.main
    judge: str = command.judge if command.judge is not None else config.models.review
    parse_model_ref(model)
    parse_model_ref(judge)
    suite: Suite = await asyncio.to_thread(_load_suite)
    cases: tuple[RealCase, ...] = _ablation_cases(suite, command)
    subject, variants = await asyncio.to_thread(_variants, command)
    found: dict[str, str] = await _secrets(config, (model, judge))
    login_env: dict[str, str] = await read_login_env(config.shell.login_env_timeout_s, ())
    run_config: AppConfig = eval_config(config)
    _write(
        [
            f"Playing {len(cases)} case(s) with {len(variants)} arms each against {model}, judged by {judge}. "
            "This spends both models' tokens."
        ]
    )
    outcomes: dict[tuple[str, str], TaskOutcome] = {}
    for case in cases:
        bare: EvalTask = task_of(case.model_copy(update={"skills": ()}), ())
        for variant in variants:
            task: EvalTask = bare.model_copy(update={"skills": variant.skills})
            outcomes[(case.id, variant.name)] = await play_attempt(
                task, run_config, login_env, found, model, 1, variant.notes
            )
            _write([f"  played {case.id} {variant.name}"])
    pairings: tuple[Pairing, ...] = await judge_pairs(
        run_config, login_env, found, judge, cases, _pairs(variants), outcomes
    )
    _write(render_findings(subject, findings(pairings)))
    return 0


def render_costs(rows: Sequence[DailyCost]) -> list[str]:
    """Per day, its dearest names: their calls, their tokens and the tokens of a call."""
    if not rows:
        return ["No tool call in that span."]
    lines: list[str] = []
    days: dict[str, list[DailyCost]] = {}
    for row in rows:
        days.setdefault(row.day, []).append(row)
    for day, mine in days.items():
        total: float = sum(row.tokens for row in mine)
        lines.append(f"{day}  {sum(row.calls for row in mine)} calls, {total:,.0f} tokens")
        for row in mine[:_NAMES_A_DAY]:
            lines.append(
                f"  {row.name:<28} {row.calls:>5} calls {row.tokens:>14,.0f} tokens "
                f"{row.tokens / row.calls:>10,.0f} a call"
            )
    return lines


async def cmd_eval_costs(command: EvalCostsCommand) -> int:
    """What each tool and skill call cost, per day of the last ``days``, read from the record opened read-only."""
    now: float = time.time()
    costs: tuple[CallCost, ...] = await asyncio.to_thread(read_call_costs, db_path(), now - command.days * DAY_S, now)
    zone = datetime.now().astimezone().tzinfo
    if zone is None:
        raise ConfigError("the Mac's time zone could not be read", {})
    _write(render_costs(daily_costs(costs, zone)))
    return 0


async def dispatch(command: Command) -> int:
    """Carry out one of this module's commands."""
    match command:
        case EvalSuiteCommand():
            return await cmd_eval_suite(command)
        case EvalRealCommand():
            return await cmd_eval_real(command)
        case EvalAblateCommand():
            return await cmd_eval_ablate(command)
        case EvalCostsCommand():
            return await cmd_eval_costs(command)
        case _:
            raise InvalidValueError("not a command of this module", {"name": command.name})
