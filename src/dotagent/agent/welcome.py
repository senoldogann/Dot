"""An agent's first meeting with the owner (item 993, docs/agents-and-campaigns.md): an agent the owner has just made
greets them in its role, in their language, and asks them one question with a few choices of what they want from it,
as its first chat opens. The run's goal is Dot's words for that meeting, which the conversation does not draw as the
owner's message (``WELCOME_SOURCE``)."""

from dotagent.store.models import AgentRecord

# The source of an agent's first meeting with the owner.
WELCOME_SOURCE: str = "welcome"
# The longest language name the page may give (``navigator.language`` named, "Turkish", "Brazilian Portuguese").
LANGUAGE_CHARS: int = 40


def welcome_goal(agent: AgentRecord, language: str) -> str:
    """The goal of ``agent``'s first meeting with the owner, who reads ``language``."""
    who: str = agent.name if agent.role == "" else f"{agent.name}, {agent.role}"
    return (
        f"The owner has just made you ({who}) and opened your first chat. Greet them in one short, warm sentence in "
        f"your role, in {language}. Then ask them with ask_user, in {language}, the one question that tells you what "
        "they want from you most, with three or four short options and room for their own answer. Do no other work "
        "before they answer."
    )
