"""Writing a recording down on this Mac (item 626).

The composer's microphone on an iPhone records the voice itself and sends it here, because Safari's own speech
recognition goes deaf after the first dictation of a page (WebKit bugs 317741 and 321436). The Mac writes the recording
down with macOS 26's dictation model, on the device:

- :mod:`dotagent.voice.wav` reads the recording the page sent (a WAV) into a model and writes it out again in the one
  form the program is given;
- :mod:`dotagent.voice.toolchain` says whether this Mac can do it, and finds the compiler that builds the program;
- :mod:`dotagent.voice.helper` builds ``transcribe.swift`` with it once in a daemon's life, runs it on a recording and
  returns the words.
"""
