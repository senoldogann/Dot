"""Zen's free models as Dot's model picker lists them (item 283), provider ``opencode``: the free models of the OpenCode
server Dot starts (:mod:`dotagent.opencode.server`) that call tools. They answer OpenCode's own agent only, so a run on
one goes to that agent whole (:func:`dotagent.opencode.run.run_in_opencode`) and no single call of Dot's reaches one:
:meth:`OpenCodeProvider.complete` refuses."""

from dotagent.core.errors import ConfigError, ProviderRefusedError
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.opencode.server import FreeModel, OpenCodeServer

OPENCODE_PROVIDER: str = "opencode"


def _info(model: FreeModel) -> ModelInfo:
    return ModelInfo(
        id=model.id,
        vision=model.vision,
        tools=model.tools,
        thinking=model.thinking,
        context_length=model.context_length,
        own_context_length=None,
        window_source=None if model.context_length is None else "provider",
        efforts=model.efforts,
        free=True,
    )


class OpenCodeProvider:
    """A connector to Zen's free models through the OpenCode server Dot starts."""

    def __init__(self, server: OpenCodeServer) -> None:
        self._server: OpenCodeServer = server

    @property
    def name(self) -> str:
        return OPENCODE_PROVIDER

    @property
    def server(self) -> OpenCodeServer:
        """The OpenCode server, which a run on one of the models drives (:mod:`dotagent.opencode.run`)."""
        return self._server

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        raise ProviderRefusedError(
            "Zen's free models answer only as OpenCode's own agent: a run on one goes to OpenCode whole, and no single"
            " call of Dot's (a side question, a summary, a review) can use one; pick another model for it",
            {"provider": OPENCODE_PROVIDER, "purpose": request.session_key or ""},
        )

    async def model_info(self, model: str) -> ModelInfo:
        listed: tuple[ModelInfo, ...] = await self.list_models()
        found: ModelInfo | None = next((info for info in listed if info.id == model), None)
        if found is None:
            raise ConfigError(
                "Zen does not offer this model free any more; pick another of its free models",
                {"provider": OPENCODE_PROVIDER, "model": model, "free": [info.id for info in listed]},
            )
        return found

    async def list_models(self) -> tuple[ModelInfo, ...]:
        """Zen's free models that call tools: the agent works only with tools."""
        return tuple(_info(model) for model in await self._server.list_free_models() if model.tools)

    async def aclose(self) -> None:
        await self._server.aclose()
