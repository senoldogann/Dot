"""Model Context Protocol client: starts the tool servers the user configured and talks to them over stdio."""

import logging

# The mcp SDK logs what goes wrong with Python's logging (a token refresh refused, the sign-in flow it then fails),
# which Dot does not set up: with no handler, Python printed each as a traceback to the daemon's stderr at every start
# (the review of every module). Dot logs the outcome itself (``mcp_server_left_out``, with the server's words).
logging.getLogger("mcp").addHandler(logging.NullHandler())
