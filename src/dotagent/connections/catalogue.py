"""The applications Customize › Plugins sets Dot up with (items 278 and 316). Dot drives the Mac's applications with
AppleScript once macOS lets the app that runs Dot control them (Privacy & Security › Automation); it works in the
web's applications in its own browser once the owner has signed in there; and it reads and writes the files OneDrive
keeps in a folder of the Mac once OneDrive syncs one. Each says what Dot does with it."""

from typing import Literal

from dotagent.core.types import FrozenModel

type ConnectionKind = Literal["mac", "web", "folder"]


class MacApp(FrozenModel):
    """An application of the Mac that Dot drives with AppleScript; ``bundle_id`` names it to macOS."""

    id: str
    name: str
    bundle_id: str
    does: str


class WebApp(FrozenModel):
    """An application of the web that Dot uses in its own browser. The owner is signed in when the browser holds one of
    ``cookies`` (one the site sets only on a signed-in browser) for one of ``domains`` or a host under it; ``sign_in``
    is the page where they sign in."""

    id: str
    name: str
    domains: tuple[str, ...]
    cookies: tuple[str, ...]
    sign_in: str
    does: str


class FolderApp(FrozenModel):
    """An application of the Mac that keeps files in sync in a folder, which Dot reads and writes like any other.
    ``bundle_ids`` name it to macOS: the ids its builds go by, the first Launch Services finds being the one installed
    (OneDrive's direct download and its App Store build do not share one)."""

    id: str
    name: str
    bundle_ids: tuple[str, ...]
    does: str


MAC_APPS: tuple[MacApp, ...] = (
    MacApp(id="mail", name="Mail", bundle_id="com.apple.mail", does="Reads, sorts and writes your email."),
    MacApp(id="calendar", name="Calendar", bundle_id="com.apple.iCal", does="Reads your events and adds new ones."),
    MacApp(id="notes", name="Notes", bundle_id="com.apple.Notes", does="Finds, writes and files your notes."),
    MacApp(id="reminders", name="Reminders", bundle_id="com.apple.reminders", does="Keeps your to-dos and lists."),
    MacApp(id="messages", name="Messages", bundle_id="com.apple.MobileSMS", does="Reads and sends your messages."),
    MacApp(id="contacts", name="Contacts", bundle_id="com.apple.AddressBook", does="Looks up the people you know."),
    MacApp(
        id="excel", name="Microsoft Excel", bundle_id="com.microsoft.Excel", does="Opens, edits and builds workbooks."
    ),
    MacApp(id="word", name="Microsoft Word", bundle_id="com.microsoft.Word", does="Opens, edits and writes documents."),
    MacApp(
        id="powerpoint",
        name="Microsoft PowerPoint",
        bundle_id="com.microsoft.Powerpoint",
        does="Builds and edits presentations.",
    ),
    MacApp(
        id="outlook",
        name="Microsoft Outlook",
        bundle_id="com.microsoft.Outlook",
        does="Reads and writes your work email and events.",
    ),
)

FOLDER_APPS: tuple[FolderApp, ...] = (
    FolderApp(
        id="onedrive",
        name="OneDrive",
        bundle_ids=("com.microsoft.OneDrive-mac", "com.microsoft.OneDrive"),
        does="Reads and writes the files OneDrive keeps in sync on this Mac.",
    ),
)

WEB_APPS: tuple[WebApp, ...] = (
    WebApp(
        id="google",
        name="Google",
        domains=("google.com",),
        cookies=("SID", "__Secure-1PSID"),
        sign_in="https://accounts.google.com/",
        does="Gmail, Google Calendar and Drive, in Dot's browser.",
    ),
    WebApp(
        id="microsoft",
        name="Microsoft 365",
        domains=("live.com", "login.microsoftonline.com"),
        cookies=("MSPAuth", "ESTSAUTHPERSISTENT"),
        sign_in="https://outlook.office.com/mail/",
        does="Outlook, OneDrive and Office on the web, in Dot's browser.",
    ),
    WebApp(
        id="github",
        name="GitHub",
        domains=("github.com",),
        cookies=("user_session",),
        sign_in="https://github.com/login",
        does="Repositories, issues and pull requests, in Dot's browser.",
    ),
    WebApp(
        id="slack",
        name="Slack",
        domains=("slack.com",),
        cookies=("d",),
        sign_in="https://slack.com/signin",
        does="Your workspaces' channels and messages, in Dot's browser.",
    ),
    WebApp(
        id="notion",
        name="Notion",
        domains=("notion.so", "notion.com"),
        cookies=("token_v2",),
        sign_in="https://www.notion.so/login",
        does="Your pages and databases, in Dot's browser.",
    ),
    WebApp(
        id="linkedin",
        name="LinkedIn",
        domains=("linkedin.com",),
        cookies=("li_at",),
        sign_in="https://www.linkedin.com/login",
        does="Your profile, messages and posts, in Dot's browser.",
    ),
    WebApp(
        id="x",
        name="X",
        domains=("x.com", "twitter.com"),
        cookies=("auth_token",),
        sign_in="https://x.com/i/flow/login",
        does="Your timeline, messages and posts, in Dot's browser.",
    ),
    WebApp(
        id="instagram",
        name="Instagram",
        domains=("instagram.com",),
        cookies=("sessionid",),
        sign_in="https://www.instagram.com/accounts/login/",
        does="Your feed, messages and posts, in Dot's browser.",
    ),
)
