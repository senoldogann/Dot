"""Hooks (``docs/hooks.md``): an address a service calls to start Dot's work at once, signed with the hook's own secret.

A hook is bound to a schedule or to one of the owner's agents. A call whose signature matches starts that schedule's run
or a run of that agent, within the schedule's or the agent's own bounds and nothing more, started tainted: the named
fields of what the call carried reach the run quoted and fenced, as data. :mod:`dotagent.hooks.model` holds the pure
parts (the signature, the window, the fields and the words of the run), :mod:`dotagent.hooks.book` the file they are
kept in, :mod:`dotagent.hooks.calls` the handling of a call.
"""
