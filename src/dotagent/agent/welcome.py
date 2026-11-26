"""An agent's first meeting with the owner (item 993, docs/agents-and-campaigns.md): an agent the owner has just made
opens its first chat with a message of its own, in their language: who it is, what it does for them (from its role and
instructions) and the one question that gets it started (item 1962). The run is one answer on the fast model whose
goal asks it to call no tool, so the message is there within seconds. The goal is Dot's words for that meeting, which
the conversation does not draw as the owner's message (``WELCOME_SOURCE``)."""

from dotagent.store.models import AgentRecord

# The source of an agent's first meeting with the owner.
WELCOME_SOURCE: str = "welcome"
# The longest language name the page may give (``navigator.language`` named, "Turkish", "Brazilian Portuguese").
LANGUAGE_CHARS: int = 40


def welcome_goal(agent: AgentRecord, language: str) -> str:
    """The goal of ``agent``'s first meeting with the owner, who reads ``language``."""
    who: str = agent.name if agent.role == "" else f"{agent.name}, {agent.role}"
    return (
        f"The owner has just made you ({who}) and opened your first chat. Write your first message to them in "
        f"{language}, in your own voice and in this order: greet them in one short sentence and say who you are; "
        "say in two or three sentences what you will do for them, from your role and your instructions and nothing "
        "you were not given; end with the one question whose answer you need most to start, and say what is enough "
        "of an answer (a link, a name, a sentence). Call no tool and do no other work before they answer."
    )
