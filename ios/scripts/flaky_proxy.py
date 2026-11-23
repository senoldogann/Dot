"""A bad network between the simulator and a scratch daemon, in the way of Apple's Network Link Conditioner (which needs
the Mac's settings and a password): a TCP proxy whose mode is a word in a file, read for every request, so that a test
can change the link while the app runs.

    python3 flaky_proxy.py <listen-port> <upstream-port> <mode-file>

Modes (the file's first word; a missing file is `pass`):

    pass          the link is as it is
    bad[:scale]   every request from the phone waits 2 to 8 seconds (times `scale`, default 1) and three in ten are lost:
                  the connection is closed on the phone before the daemon hears it
    slow[:scale]  every request waits 0.5 to 1.5 seconds (a poor cellular link)
    down          nothing gets through; connections that are open are closed

The decision for the n-th request is a function of n alone (a fixed seed), so a run is the same twice. It reads and writes
no file but the mode file, and listens on 127.0.0.1 only.
"""

import asyncio
import contextlib
import random
import sys
from pathlib import Path


class Proxy:
    def __init__(self, listen: int, upstream: int, mode_file: Path) -> None:
        self.listen = listen
        self.upstream = upstream
        self.mode_file = mode_file
        self.open: set[asyncio.StreamWriter] = set()
        self.requests = 0
        self.rng = random.Random(7)

    def mode(self) -> tuple[str, float]:
        try:
            word = self.mode_file.read_text().split()[0]
        except (OSError, IndexError):
            return "pass", 1.0
        name, _, scale = word.partition(":")
        try:
            return name, float(scale) if scale else 1.0
        except ValueError:
            return name, 1.0

    async def pipe(self, source: asyncio.StreamReader, target: asyncio.StreamWriter, from_phone: bool) -> None:
        try:
            while data := await source.read(65536):
                if from_phone:
                    name, scale = self.mode()
                    self.requests += 1
                    if name == "down":
                        return
                    if name == "bad":
                        if self.rng.random() < 0.3:
                            return
                        await asyncio.sleep(self.rng.uniform(2, 8) * scale)
                    elif name == "slow":
                        await asyncio.sleep(self.rng.uniform(0.5, 1.5) * scale)
                if from_phone:
                    # The daemon answers the address it listens on only.
                    data = data.replace(f"127.0.0.1:{self.listen}".encode(), f"127.0.0.1:{self.upstream}".encode())
                target.write(data)
                await target.drain()
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            with contextlib.suppress(Exception):
                target.close()

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        name, _ = self.mode()
        if name == "down":
            writer.close()
            return
        try:
            up_reader, up_writer = await asyncio.open_connection("127.0.0.1", self.upstream)
        except OSError:
            writer.close()
            return
        self.open.update({writer, up_writer})
        try:
            await asyncio.gather(self.pipe(reader, up_writer, True), self.pipe(up_reader, writer, False))
        finally:
            self.open.difference_update({writer, up_writer})
            for end in (writer, up_writer):
                with contextlib.suppress(Exception):
                    end.close()

    async def watch(self) -> None:
        # A link that goes down takes the connections that are open with it.
        while True:
            await asyncio.sleep(0.25)
            if self.mode()[0] == "down":
                for end in list(self.open):
                    with contextlib.suppress(Exception):
                        end.close()

    async def run(self) -> None:
        server = await asyncio.start_server(self.handle, "127.0.0.1", self.listen)
        print("proxy", self.listen, "->", self.upstream, flush=True)
        async with server:
            await asyncio.gather(server.serve_forever(), self.watch())


if __name__ == "__main__":
    listen_port, upstream_port, path = int(sys.argv[1]), int(sys.argv[2]), Path(sys.argv[3])
    asyncio.run(Proxy(listen_port, upstream_port, path).run())
