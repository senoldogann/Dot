"""The Chrome profile of Dot's meetings (item 1138), where Dot's own Google account is signed in, is among what no run
may read or change, as Dot's other Chrome profiles are."""

from dotagent.config import load_default_config
from dotagent.core.paths import meetings_profile_dir
from dotagent.runtime import _guarded_files


def test_the_meetings_profile_is_guarded_from_runs() -> None:
    assert meetings_profile_dir() in _guarded_files(load_default_config())
