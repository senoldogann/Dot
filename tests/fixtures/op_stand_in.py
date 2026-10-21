"""A stand-in for 1Password's ``op`` (item 493), for the tests: the commands Dot runs, answered from a JSON file of
state, in the shapes of ``op`` 2's ``--format json``. Run as ``python op_stand_in.py STATE ARGS...``; a wrapper named
``op`` passes STATE. Without ``OP_SERVICE_ACCOUNT_TOKEN`` it answers as the owner's ``op`` signed in through the app
does; with one, as the service account the token is, which reads its vaults alone.

The state: ``accounts`` (as ``op account list`` gives them), ``vaults`` (every vault: ``id``, ``name``), ``token`` (the
service account's, or null), ``reads`` (the ids of the vaults it reads), ``made_token`` (what ``op service-account
create`` gives), ``items`` (by vault id: ``id``, ``title``, ``urls``, ``fields``, ``otp``).
"""

import json
import os
import sys
from pathlib import Path


def _fail(text: str) -> None:
    print(f"[ERROR] {text}", file=sys.stderr)
    sys.exit(1)


def _value(args: list[str], flag: str) -> str:
    if flag not in args:
        _fail(f"{flag} is needed")
    return args[args.index(flag) + 1]


def main() -> None:
    path: Path = Path(sys.argv[1])
    args: list[str] = sys.argv[2:]
    state: dict[str, object] = json.loads(path.read_text(encoding="utf-8"))
    token: str | None = os.environ.get("OP_SERVICE_ACCOUNT_TOKEN")
    if token is not None and token != state["token"]:
        _fail("the service account token is not valid")
    vaults: list[dict[str, str]] = state["vaults"]  # type: ignore[assignment]
    items: dict[str, list[dict[str, object]]] = state["items"]  # type: ignore[assignment]
    readable: list[dict[str, str]] = (
        [vault for vault in vaults if vault["id"] in state["reads"]] if token is not None else vaults  # type: ignore[operator]
    )
    match args[:2]:
        case ["account", "list"]:
            print(json.dumps(state["accounts"]))
        case ["vault", "list"]:
            print(json.dumps(readable))
        case ["vault", "create"]:
            vault: dict[str, str] = {"id": f"v{len(vaults) + 1}", "name": args[2]}
            vaults.append(vault)
            items[vault["id"]] = []
            path.write_text(json.dumps(state), encoding="utf-8")
            print(json.dumps(vault))
        case ["service-account", "create"]:
            vault_id: str = _value(args, "--vault").split(":", 1)[0]
            state["token"] = state["made_token"]
            state["reads"] = [vault_id]
            path.write_text(json.dumps(state), encoding="utf-8")
            print(f"Service account created successfully!\nService account token: {state['made_token']}")
        case ["item", "list"]:
            vault_id = _value(args, "--vault")
            if vault_id not in [vault["id"] for vault in readable]:
                _fail("no vault of that id")
            listed = [{key: item[key] for key in ("id", "title", "urls") if key in item} for item in items[vault_id]]
            print(json.dumps(listed))
        case ["item", "get"]:
            vault_id = _value(args, "--vault")
            found = next((item for item in items.get(vault_id, []) if item["id"] == args[2]), None)
            if found is None or vault_id not in [vault["id"] for vault in readable]:
                _fail("no item of that id")
            if "--otp" in args:
                if not found.get("otp"):
                    _fail("the item has no one-time password")
                print(found["otp"])
            else:
                print(json.dumps({key: found[key] for key in ("id", "title", "fields") if key in found}))
        case _:
            _fail(f"unknown command {' '.join(args)}")


main()
