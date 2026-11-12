"""A local page that stands for Google Meet's, with the aria labels and texts ``dotagent.meetings.meet`` reads.

The pre-join screen has the mic and camera toggles, the name field and "Ask to join"; a click shows "Asking to be let
in" for about half a second, then the call (Leave call, captions toggle, a captions region, the chat). The test adds
and rewrites caption blocks with ``window.standIn``; Leave call shows "You left the meeting".
"""

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAGE: str = """<!doctype html><meta charset="utf-8"><title>Meet stand-in</title>
<body><div id="root"></div>
<script>
const root = document.getElementById('root');
const toggle = (on, onLabel, offLabel) => {
  const b = document.createElement('div');
  b.setAttribute('role', 'button');
  const set = () => { b.setAttribute('aria-label', on ? onLabel : offLabel); b.textContent = on ? 'on' : 'off'; };
  b.onclick = () => { on = !on; set(); };
  set();
  return b;
};
const prejoin = () => {
  root.innerHTML = '';
  root.append(toggle(true, 'Turn off microphone', 'Turn on microphone'));
  root.append(toggle(true, 'Turn off camera', 'Turn on camera'));
  const name = document.createElement('input');
  name.type = 'text'; name.setAttribute('aria-label', 'Your name');
  root.append(name);
  const ask = document.createElement('button');
  ask.textContent = 'Ask to join';
  ask.onclick = () => {
    window.guestName = name.value;
    root.innerHTML = '<p>Asking to be let in</p>';
    setTimeout(call, 500);
  };
  root.append(ask);
};
const call = () => {
  root.innerHTML = '';
  const leave = document.createElement('button');
  leave.setAttribute('aria-label', 'Leave call');
  leave.textContent = 'Leave';
  leave.onclick = () => { root.innerHTML = '<h2>You left the meeting</h2>'; };
  root.append(leave);
  let caps = false;
  const cap = document.createElement('button');
  const region = document.createElement('div');
  region.setAttribute('role', 'region'); region.tabIndex = 0;
  const setCap = () => {
    cap.setAttribute('aria-label', caps ? 'Turn off captions' : 'Turn on captions');
    region.style.display = caps ? 'block' : 'none';
  };
  cap.onclick = () => { caps = !caps; setCap(); };
  setCap();
  root.append(cap, region);
  const chatButton = document.createElement('button');
  chatButton.setAttribute('aria-label', 'Chat with everyone');
  chatButton.textContent = 'Chat';
  const panel = document.createElement('div');
  panel.style.display = 'none';
  const live = document.createElement('div');
  live.setAttribute('aria-live', 'polite');
  const input = document.createElement('textarea');
  input.setAttribute('aria-label', 'Send a message');
  input.onkeydown = (e) => {
    if (e.key === 'Enter' && input.value) {
      e.preventDefault();
      window.standIn.chat('You', input.value);
      input.value = '';
    }
  };
  panel.append(live, input);
  chatButton.onclick = () => { panel.style.display = 'block'; };
  root.append(chatButton, panel);
  const row = (who, words) => {
    const el = document.createElement('div');
    const w = document.createElement('div'); w.textContent = who;
    const t = document.createElement('div'); t.textContent = words;
    el.append(w, t);
    return el;
  };
  window.standIn = {
    caption: (id, who, words) => {
      let el = document.getElementById('cap-' + id);
      if (!el) { el = row(who, words); el.id = 'cap-' + id; region.append(el); }
      el.children[1].textContent = words;
    },
    drop: (id) => { document.getElementById('cap-' + id).remove(); },
    chat: (who, words) => { live.append(row(who, words)); },
  };
};
prejoin();
</script></body>"""


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body: bytes = PAGE.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


@contextmanager
def serve_stand_in() -> Iterator[str]:
    """Serve the stand-in on a free local port; yields its address."""
    server: ThreadingHTTPServer = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread: threading.Thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/abc-defg-hij"
    finally:
        server.shutdown()
        server.server_close()
