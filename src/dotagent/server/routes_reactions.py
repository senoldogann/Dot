"""Routes of the reactions on Dot's messages (item 586, :mod:`dotagent.agent.reactions`): the reactions that stand on a
message or in a chat, one put on a message or taken back, and the settings of what a reaction does once its run has
ended. A message is told apart by its run and a key, ``answer`` or ``step-<n>``. The owner's reactions come from the
page and nowhere else: behind the API's token like the rest, and a reaction vouches for nothing a run read."""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Path, Response

from dotagent.agent.reaction_words import MESSAGE_PATTERN
from dotagent.agent.reactions import ReactionOutcome, Reactions
from dotagent.config import AfterRun, ReactionsConfig
from dotagent.config_edit import set_setting
from dotagent.core.errors import ConfigError
from dotagent.core.paths import config_path
from dotagent.server.context import ServerContext
from dotagent.server.views import ReactionRequest, ReactionSettingRequest
from dotagent.store.models import ReactionName, ReactionRecord

# A message of a run as a path names it.
MessageKey = Annotated[str, Path(pattern=MESSAGE_PATTERN)]


def reactions_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")
    service: Reactions = ctx.reactions if ctx.reactions is not None else Reactions(ctx.runtime, ctx.manager)
    # One change of the settings at a time: a second one waits for the first and finds what it did.
    changing: asyncio.Lock = asyncio.Lock()

    @router.get("/threads/{thread_id}/reactions")
    async def chat_reactions(thread_id: str) -> list[ReactionRecord]:
        """The reactions that stand on the messages of a chat's runs, the oldest first; 404 for no chat."""
        return await service.of_chat(thread_id)

    @router.get("/runs/{run_id}/messages/{message}/reactions")
    async def message_reactions(run_id: str, message: MessageKey) -> list[ReactionRecord]:
        """The reactions that stand on one message of a run; 404 for no run."""
        return await service.of_message(run_id, message)

    @router.post("/runs/{run_id}/messages/{message}/reactions", status_code=201)
    async def add_reaction(
        run_id: str, message: MessageKey, request: ReactionRequest, response: Response
    ) -> ReactionOutcome:
        """Put a reaction on a message and do what it says: told to the run at its next step while it works (a ✋ holds
        it), a follow-up run in the chat once it ended (queued behind the run at work), a note for a 📌. The outcome
        says what it did and holds the message's reactions. A reaction already there changes nothing (200); a message
        with no words, or one the run has not said, is refused (404, 422)."""
        outcome: ReactionOutcome = await service.add(run_id, message, request.reaction)
        if outcome.effect == "unchanged":
            response.status_code = 200
        return outcome

    @router.delete("/runs/{run_id}/messages/{message}/reactions/{reaction}")
    async def remove_reaction(run_id: str, message: MessageKey, reaction: ReactionName) -> ReactionOutcome:
        """Take a reaction back: a run at work is told, a ✋ taken back lets a held run go on, and the note a 📌 made
        goes when it is as it was made. What the reaction started stays started. One not there changes nothing."""
        return await service.remove(run_id, message, reaction)

    @router.get("/reactions/settings")
    async def reaction_settings() -> ReactionsConfig:
        """The switch of the follow-ups and what each reaction does once its run has ended, as they stand now."""
        return service.config

    @router.post("/reactions/settings")
    async def change_reaction_setting(request: ReactionSettingRequest) -> ReactionsConfig:
        """Change one setting: it holds from the next press and is kept in the config file; a config file that cannot
        be changed leaves the setting as it was."""
        async with changing:
            before: bool | AfterRun = getattr(service.config, request.key)
            service.apply(request.key, request.value)
            try:
                await asyncio.to_thread(set_setting, config_path(), "reactions", request.key, request.value)
            except (ConfigError, OSError):
                service.apply(request.key, before)
                raise
        return service.config

    return router
