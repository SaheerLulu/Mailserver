"""Shared test helpers."""
import asyncio
import threading


def raw_message(frm, to, subject, body, extra=""):
    """Build a simple RFC 5322 message as bytes.

    Pass a ``Message-ID`` (and other headers) via ``extra`` when a test needs
    one; we don't add a default so callers stay in control of headers.
    """
    return (
        f"From: {frm}\r\nTo: {to}\r\nSubject: {subject}\r\n"
        f"Date: Mon, 1 Jun 2026 10:00:00 +0000\r\n"
        f"{extra}\r\n{body}\r\n"
    ).encode()


class ThreadedServer:
    """Run an asyncio ``start_server`` handler on an ephemeral port in a thread.

    Used to drive the IMAP/POP3 servers with real clients inside tests.
    Use as a context manager; ``.port`` is the bound port.
    """

    def __init__(self, handler):
        self.handler = handler
        self.port = None
        self._loop = None
        self._thread = None
        self._ready = threading.Event()

    def __enter__(self):
        def run():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)

            async def main():
                server = await asyncio.start_server(self.handler, "127.0.0.1", 0)
                self.port = server.sockets[0].getsockname()[1]
                self._ready.set()
                async with server:
                    await server.serve_forever()

            try:
                self._loop.run_until_complete(main())
            except (asyncio.CancelledError, RuntimeError):
                pass

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()
        if not self._ready.wait(5):
            raise RuntimeError("server did not start")
        return self

    def __exit__(self, *exc):
        if self._loop:
            # Close DB connections opened in the server's threads (both the
            # event-loop thread and the thread-sensitive executor thread) so
            # the test database can be dropped without a lingering session.
            done = threading.Event()

            async def _close():
                from asgiref.sync import sync_to_async
                from django.db import connections
                connections.close_all()                      # loop thread
                await sync_to_async(connections.close_all)()  # executor thread
                done.set()

            try:
                asyncio.run_coroutine_threadsafe(_close(), self._loop)
                done.wait(5)
            except Exception:  # noqa: BLE001
                pass
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=5)
        return False
