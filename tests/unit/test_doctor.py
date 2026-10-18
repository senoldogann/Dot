"""The doctor's review row: a review model that is the main model is a warning, never a failure, and with lessons on
auto the warning says that the same model approves them too, so an independent check wants another review model."""

from dotagent.config import AppConfig, LessonsMode, load_default_config
from dotagent.doctor import CheckResult, _review_row


def _same_review_as_main() -> AppConfig:
    config: AppConfig = load_default_config()
    return config.model_copy(update={"models": config.models.model_copy(update={"review": config.models.main})})


def test_lessons_on_auto_and_the_main_model_as_review_warn_about_independence() -> None:
    config: AppConfig = _same_review_as_main()
    row: CheckResult = _review_row(config.models, "auto")
    assert (row.name, row.ok, row.required) == ("review model", False, False)
    assert "Lessons are approved by the run's own model" in row.detail
    assert "give [models] review another model for an independent check" in row.detail


def test_lessons_that_are_not_approved_automatically_leave_the_review_warning_as_it_was() -> None:
    config: AppConfig = _same_review_as_main()
    for lessons in ("off", "propose"):
        row: CheckResult = _review_row(config.models, lessons)
        assert (row.ok, row.required) == (False, False)
        assert "Lessons are approved by the run's own model" not in row.detail


def test_another_review_model_is_no_warning_whatever_the_lessons_mode() -> None:
    config: AppConfig = load_default_config()
    models = config.models.model_copy(update={"review": "anthropic:claude-opus-5-5"})
    lessons: LessonsMode
    for lessons in ("off", "propose", "auto"):
        row: CheckResult = _review_row(models, lessons)
        assert (row.ok, row.required) == (True, False)
