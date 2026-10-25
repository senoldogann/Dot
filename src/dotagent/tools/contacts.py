"""The owner's saved contact details (item 621): ``vault_contacts`` lists the contacts of Dot's vault and the billing
address of each card, with their fields, for a run to type where a form asks for a name, an address, a phone number or
an email. They are the owner's own words, kept in Dot's database and not in the Keychain, and a run reads them as it
reads any words it types; where it then sends them is judged by the step that sends, as ever. No card number, expiry or
security code is among them."""

from pydantic import Field

from dotagent.policy.types import ActionInfo
from dotagent.store.models import VaultRecord
from dotagent.tools.base import ACTING_MODES, RegisteredTool, ToolArgs, ToolResult, make_tool, ok
from dotagent.tools.context import ToolContext
from dotagent.vault.items import BILLING_FIELDS, CONTACT_FIELDS

CONTACTS_TOOL: str = "vault_contacts"
_QUICK_TIMEOUT_S: float = 45.0
# What each detail is called in the list a run reads.
_FIELD_NAMES: dict[str, str] = {
    "name": "name",
    "address": "address",
    "address2": "address line 2",
    "city": "city",
    "region": "state or region",
    "postal_code": "postal code",
    "country": "country",
    "phone": "phone",
    "email": "email",
}


class ContactsArgs(ToolArgs):
    label: str | None = Field(
        default=None, description="Part of a contact's label, to list the matching ones alone (for example Home)."
    )


def _line(record: VaultRecord) -> str:
    """A contact, or the billing address of a card, as one line: its id, what it is and its details."""
    order: tuple[str, ...] = CONTACT_FIELDS if record.kind == "contact" else BILLING_FIELDS
    details: str = "; ".join(f"{_FIELD_NAMES[name]} {record.fields[name]}" for name in order if name in record.fields)
    what: str = record.label if record.kind == "contact" else f"billing address of the card {record.label}"
    return f"- {record.id} · {what}: {details}"


def _contacts_tool() -> RegisteredTool:
    def classify(args: ContactsArgs, ctx: ToolContext) -> ActionInfo:
        target: str = ctx.browser.current_url(ctx.thread_id) or ""
        return ActionInfo(
            tool=CONTACTS_TOOL,
            kind="read",
            risk="safe",
            summary="List the owner's saved contact details",
            target=target,
            flags=(),
        )

    async def run(args: ContactsArgs, ctx: ToolContext) -> ToolResult:
        wanted: str = (args.label or "").lower()
        found: list[VaultRecord] = [
            record
            for record in await ctx.runtime.vault_items.items()
            if record.fields and record.kind in ("contact", "card") and wanted in record.label.lower()
        ]
        if not found:
            return ok("No contact details" + (f" with {args.label!r} in their label." if args.label else "."))
        return ok(
            "The owner's saved contact details, in their own words, by id. Type them where a form asks; they are "
            "no secret, but send them nowhere the owner did not ask for:\n"
            + "\n".join(_line(record) for record in found)
        )

    return make_tool(
        name=CONTACTS_TOOL,
        description=(
            "List the contact details the owner saved in their vault (Settings › Vault): each contact's label, name, "
            "address, phone and email, and the billing address of their cards. Read them to fill a form that asks for "
            "them. It never shows a password, a card number, an expiry or a security code."
        ),
        args_model=ContactsArgs,
        modes=ACTING_MODES,
        parallel_safe=True,
        timeout_s=_QUICK_TIMEOUT_S,
        classify=classify,
        run=run,
    )


def build_contacts_tools() -> tuple[RegisteredTool, ...]:
    return (_contacts_tool(),)
