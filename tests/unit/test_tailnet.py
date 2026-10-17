"""What Tailscale's command line says of the Mac (item 271), as Settings › Phone reads it: whether it runs signed in, the
Mac's name on the tailnet, and whether it serves this daemon's port over HTTPS."""

import pytest

from dotagent.core.errors import PhoneSetupError
from dotagent.tailnet import running_name, serves

_STATUS: str = '{"BackendState": "Running", "Self": {"DNSName": "mac.tail1234.ts.net.", "HostName": "mac"}}'
_SERVED: str = (
    '{"TCP": {"443": {"HTTPS": true}}, "Web": {"mac.tail1234.ts.net:443": {"Handlers": {"/": '
    '{"Proxy": "http://127.0.0.1:8765"}}}}}'
)


def test_the_status_says_whether_it_runs_and_the_macs_name() -> None:
    assert running_name(_STATUS) == (True, "mac.tail1234.ts.net")
    assert running_name('{"BackendState": "NeedsLogin", "Self": {"DNSName": ""}}') == (False, None)
    assert running_name('{"BackendState": "Stopped"}') == (False, None)
    with pytest.raises(PhoneSetupError):
        running_name("not json")


def test_serving_is_this_daemons_port_at_the_macs_name_over_https() -> None:
    assert serves(_SERVED, "mac.tail1234.ts.net", 8765)
    assert not serves(_SERVED, "mac.tail1234.ts.net", 8766)
    assert not serves(_SERVED, "other.tail1234.ts.net", 8765)
    assert not serves("", "mac.tail1234.ts.net", 8765)
    assert not serves("{}", "mac.tail1234.ts.net", 8765)
