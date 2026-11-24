"""Code mode (items 1500 to 1507, docs/code-mode.md): the model writes Python that calls its tools, instead of calling
them one by one through its context.

``code_run`` starts the code in a macOS sandbox of its own: ``(deny default)``, no network, no process, no file but a
scratch folder of the run's. The only way out is the bridge, JSON lines over two pipes; every tool call the code makes
there is run by the loop's own path for a call (:func:`dotagent.agent.executor.execute_nested`), so the same gate, the
same events, the same masking and the same undo journal apply, and an approval asked from code reaches the owner as any
other. Where the sandbox cannot start Python, ``code_run`` runs nothing at all.

- :mod:`.sandbox`: the profile, the launch words and the probe that decides whether code mode can run here.
- :mod:`.runner`: starts the code, serves its bridge and watches its time, memory, output and calls.
- :mod:`.bridge`: the frames of the bridge, read as what they are, text a program wrote.
- ``child_launcher.py`` and ``child_bridge.py``: the part that runs inside the sandbox (standard library only).
"""
