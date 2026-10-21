"""The jobs' downloads for the ops panel (runner/ops/server.py; the owner, 21.10.2026: "what downloads, what
speeds"). Python loads this file by itself in every job's container (the runner puts this folder on PYTHONPATH).
Each HTTP response read through urllib is told to the panel over UDP (MG_OPS, host:port): its address when it
opens, its bytes every second or so, and when it ends; an upload's size when it goes. Nothing is waited for and
every error is swallowed: without the panel (or outside the runner, MG_OPS unset) the jobs run as they always did.
"""
import os

_TARGET = os.environ.get("MG_OPS")

if _TARGET:
    try:
        import http.client
        import json
        import socket
        import time
        import urllib.request

        _host, _port = _TARGET.rsplit(":", 1)
        _addr = (_host, int(_port))
        _sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        _job = os.environ.get("MG_JOB", "?")

        def _send(event, url, size, seconds):
            try:
                _sock.sendto(json.dumps({"j": _job, "e": event, "u": url[:300], "b": size, "s": round(seconds, 3),
                                         "t": time.time()}).encode(), _addr)
            except Exception:
                pass

        _open = urllib.request.OpenerDirector.open

        def _opened(self, fullurl, data=None, *args, **kwargs):
            started = time.time()
            reply = _open(self, fullurl, data, *args, **kwargs)
            try:
                url = fullurl.full_url if isinstance(fullurl, urllib.request.Request) else str(fullurl)
                body = data if data is not None else getattr(fullurl, "data", None)
                if body is not None and hasattr(body, "__len__"):
                    _send("up", url, len(body), time.time() - started)
                elif isinstance(reply, http.client.HTTPResponse):
                    reply._mg = [url, started, 0, started, False]
                    _send("start", url, 0, 0)
            except Exception:
                pass
            return reply

        def _count(reply, n):
            mg = getattr(reply, "_mg", None)
            if mg is None or mg[4]:
                return
            mg[2] += n
            now = time.time()
            if n == 0 or (reply.length is not None and reply.length == 0):
                mg[4] = True
                _send("end", mg[0], mg[2], now - mg[1])
            elif now - mg[3] >= 1:
                mg[3] = now
                _send("part", mg[0], mg[2], now - mg[1])

        _read, _readinto, _close = http.client.HTTPResponse.read, http.client.HTTPResponse.readinto, http.client.HTTPResponse.close

        # Python versions differ in whether read() goes through readinto(): whichever is outermost counts.
        def _read_counted(self, amt=None):
            inner = getattr(self, "_mg_in", False)
            self._mg_in = True
            try:
                data = _read(self, amt)
            finally:
                self._mg_in = inner
            if not inner:
                try:
                    _count(self, len(data))
                    if amt is None:
                        _count(self, 0)
                except Exception:
                    pass
            return data

        def _readinto_counted(self, b):
            inner = getattr(self, "_mg_in", False)
            self._mg_in = True
            try:
                n = _readinto(self, b)
            finally:
                self._mg_in = inner
            if not inner:
                try:
                    _count(self, n)
                except Exception:
                    pass
            return n

        def _close_counted(self):
            try:
                mg = getattr(self, "_mg", None)
                if mg is not None and not mg[4]:
                    mg[4] = True
                    _send("end", mg[0], mg[2], time.time() - mg[1])
            except Exception:
                pass
            return _close(self)

        urllib.request.OpenerDirector.open = _opened
        http.client.HTTPResponse.read = _read_counted
        http.client.HTTPResponse.readinto = _readinto_counted
        http.client.HTTPResponse.close = _close_counted
    except Exception:
        pass
