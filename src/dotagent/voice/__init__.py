"""Writing down what the owner says, on this Mac (items 626, 969).

The composer's microphone records the voice itself on every device whose Mac can write down, and streams it here while
the owner speaks: a browser's own recognition lost the first second or two while its engine started, and Safari's on an
iPhone goes deaf after the first dictation of a page (WebKit bugs 317741 and 321436). The Mac writes the sound down as
it comes with macOS 26's dictation model, on the device:

- :mod:`dotagent.voice.wav` says what the page streams: its rate, its five minutes, its largest piece, and the silence
  that goes after it;
- :mod:`dotagent.voice.toolchain` says whether this Mac can do it, and finds the compiler that builds the program;
- :mod:`dotagent.voice.helper` builds ``transcribe.swift`` with it once in a daemon's life, starts it for each stream,
  hands it the sound and returns the words.

The composer's microphone in the desktop app is macOS's, not this package's: :mod:`dotagent.voice.desktopapp` says
whether the Dot.app that runs can be given it, and shows System Settings where the owner allows it (item 636).
"""
