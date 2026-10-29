"""Where the server is and how to open its UI."""

from dotagent.server.guard import API_ROOT


def base_url(host: str, port: int) -> str:
    bracketed: str = f"[{host}]" if ":" in host else host
    return f"http://{bracketed}:{port}"


def health_url(host: str, port: int) -> str:
    return f"{base_url(host, port)}{API_ROOT}/health"


def status_url(host: str, port: int) -> str:
    return f"{base_url(host, port)}{API_ROOT}/status"


def ui_url(host: str, port: int, token: str) -> str:
    """The address that opens the UI already signed in: the token travels in the fragment, which is never sent."""
    return f"{base_url(host, port)}/#token={token}"
