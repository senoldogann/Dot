"""The owner's own messaging apps as ways to reach Dot (item 572): today iMessage, through the conversation the owner
has with themselves.

A *channel* receives the owner's messages and sends Dot's. The :mod:`router <dotagent.channels.router>` posts what a
channel receives into the channel's own chat ("iMessage") as the owner's message typed in the web UI, and sends the
answer of each run of that chat back, with one line when a run waits for the owner. The router knows channels only by
what :class:`~dotagent.channels.types.Channel` says, so that another could be added beside iMessage. Approvals and
questions are never answered on a channel: a channel's account, if taken, must not be able to approve. (Not to be
confused with :mod:`dotagent.tools.channels`, which tells what way a tool reaches the world: command, web, browser.)
"""
