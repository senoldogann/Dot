"""``dotx scope``: the programs and engagements the owner may test, kept in one file (item 1888, docs/recon.md).

A scope is the published list of a bug bounty program or an engagement: the hosts it allows. `add` writes one, `list`
shows them, `remove` drops one, and `check` answers the question a run asks before it touches a host — which program
covers it, and what rules that program wrote."""

from urllib.parse import urlsplit

from pydantic import ValidationError

from dotagent import scopes
from dotagent.cli_commands import Command, ScopeAddCommand, ScopeCheckCommand, ScopeListCommand, ScopeRemoveCommand
from dotagent.config import ReconScope, load_user_config


def _host_of(target: str) -> str:
    """The bare host of what was typed: a URL's host, or the name itself."""
    text: str = target.strip()
    host: str | None = urlsplit(text if "://" in text else f"//{text}").hostname
    return (host or text).strip().lower().rstrip(".")


def _show(scope: ReconScope) -> str:
    hosts: str = ", ".join(scope.hosts) if scope.hosts else "(no hosts yet)"
    rules: str = f" — {scope.note}" if scope.note else ""
    page: str = f" ({scope.url})" if scope.url else ""
    return f"{scope.program}{page}\n    {hosts}{rules}"


async def _list(_command: ScopeListCommand) -> int:
    held: tuple[ReconScope, ...] = scopes.load_scopes()
    config = load_user_config().recon
    owned: str = ", ".join(config.owned_hosts) if config.owned_hosts else "(none)"
    print(f"Owned hosts: {owned}")
    if not held:
        print("\nNo scope yet. Add one with: dotx scope add --program 'Bugcrowd · Acme' --hosts acme.com")
        return 0
    print(f"\n{len(held)} scope(s) in {scopes.scopes_path()}:")
    for scope in held:
        print(f"\n  {_show(scope)}")
    return 0


async def _add(command: ScopeAddCommand) -> int:
    hosts: tuple[str, ...] = tuple(
        host.strip() for entry in command.hosts for host in entry.replace(",", " ").split() if host.strip()
    )
    try:
        scope, replaced = scopes.add_scope(command.program, hosts, url=command.url, note=command.note)
    except ValidationError as exc:
        problems: str = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}" for error in exc.errors()
        )
        print(f"The scope of {command.program!r} was not written: {problems}")
        return 1
    print(f"{'Replaced' if replaced else 'Added'} the scope of {scope.program}:")
    print(f"  {_show(scope)}")
    print(f"\nWritten to {scopes.scopes_path().name}. A host here is treated as an owned host, so recon and")
    print("security_scan run on it and every report names the program.")
    return 0


async def _remove(command: ScopeRemoveCommand) -> int:
    if not scopes.remove_scope(command.program):
        print(f"No scope of {command.program!r} in {scopes.scopes_path().name}.")
        return 1
    print(f"Removed the scope of {command.program}.")
    return 0


async def _check(command: ScopeCheckCommand) -> int:
    host: str = _host_of(command.target)
    authority = scopes.authorising(host, load_user_config().recon)
    if authority is None:
        print(f"{host}: no scope covers it. Recon's port scan and security_scan will not touch it.")
        print("If a program you are in lists it, add the program: dotx scope add --program ... --hosts ...")
        return 1
    if authority.program is None:
        print(f"{host}: yours (in [recon] owned_hosts).")
    else:
        print(f"{host}: may be tested under {authority.program}.")
        if authority.note:
            print(f"  Rules: {authority.note}")
    return 0


async def dispatch(command: Command) -> int:
    try:
        return await _dispatch(command)
    except scopes.ScopesFileError as exc:
        print(exc.message)
        return 1


async def _dispatch(command: Command) -> int:
    if isinstance(command, ScopeListCommand):
        return await _list(command)
    if isinstance(command, ScopeAddCommand):
        return await _add(command)
    if isinstance(command, ScopeRemoveCommand):
        return await _remove(command)
    if isinstance(command, ScopeCheckCommand):
        return await _check(command)
    raise SystemExit(f"unknown scope command: {command.name}")
