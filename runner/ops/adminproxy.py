"""admin.meteogust.com through the home panel (the owner, 21.10.2026: "move whole admin panel to local... combine all
admin and ops features in one"). The database, the file store and the riders' servers stay on Cloudflare; the screens
move home. Two pieces:

- pass(): /admin/api/<path> to the admin Worker's /api/<path> with the admin service token from the env file
  (SHOREWIND_ADMIN_ID/SECRET). Reads are cached (the maps' history reads ~3,000 database rows a call: 10 minutes; the
  overview: 5; the rest: 1). A change (POST) passes only from the panel's own page (its Origin, and the page's
  x-shorewind-admin header) and gets the admin Worker's own guard: its Origin and that header.
- page(): the old admin page itself at /admin/, its calls pointed at /admin/api/, so every editor works from home
  until the new sections replace them.
"""
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

ADMIN = "https://admin.meteogust.com"
ENV_FILE = os.environ.get("MG_ENV", "/root/.meteogust.env")
AGENT = "MeteoGust control panel (data.meteogust.com)"
CACHE_S = {"maps": 600, "overview": 300}
_cache, _lock = {}, threading.Lock()


def env_value(name):
    try:
        with open(ENV_FILE) as f:
            for line in f:
                if "=" in line and line.split("=", 1)[0].strip() == name:
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return None


def token_headers():
    cid, secret = env_value("SHOREWIND_ADMIN_ID"), env_value("SHOREWIND_ADMIN_SECRET")
    if not cid or not secret:
        return None
    return {"CF-Access-Client-Id": cid, "CF-Access-Client-Secret": secret, "User-Agent": AGENT}


def call(method, path, query="", body=None, content_type=None):
    """(status, content type, bytes) from the admin Worker."""
    headers = token_headers()
    if headers is None:
        return 503, "application/json", json.dumps({"error": "The admin service token isn't in the Tower's env file"}).encode()
    if method == "POST":
        headers.update({"Origin": ADMIN, "x-shorewind-admin": "1"})
        if content_type:
            headers["Content-Type"] = content_type
    url = f"{ADMIN}{path}" + (f"?{query}" if query else "")
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as reply:
            return reply.status, reply.headers.get("Content-Type", "application/octet-stream"), reply.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers.get("Content-Type", "text/plain"), error.read()
    except Exception as error:
        return 502, "application/json", json.dumps({"error": f"admin.meteogust.com not reached ({type(error).__name__})"}).encode()


def pass_get(sub, query):
    """A cached read of /api/<sub>."""
    name = sub.split("/")[0]
    ttl = CACHE_S.get(name, 60)
    key = (sub, query)
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
    result = call("GET", f"/api/{sub}", query)
    if result[0] == 200:
        with _lock:
            _cache[key] = (time.time(), result)
            if len(_cache) > 200:
                _cache.clear()
    return result


def pass_post(sub, query, body, content_type):
    """A change: passed on, and the cached reads dropped (the screens show the change)."""
    result = call("POST", f"/api/{sub}", query, body, content_type)
    with _lock:
        _cache.clear()
    return result


# Served from here, the admin pages keep their theme (Auto / Light / Dark) under the panel's own key, so one switch
# sets both on this site (the owner, 22.10.2026: "white theme of whole admin panel. available on switch").
THEME_KEY = ('"admin-theme"', '"ops-theme"')
_LIGHT_BAR = ".homebar{background:#fbefd9;color:#8a5a0b}.homebar a{color:#0f7f88}"
BANNER = ('<style>.homebar{background:#3a2c14;color:#e3a447;font:14px system-ui;padding:6px 12px}.homebar a{color:#46c1c9}'
          '@media (prefers-color-scheme: light){' + _LIGHT_BAR.replace(".homebar", ':root:not([data-theme="dark"]) .homebar') + '}'
          + _LIGHT_BAR.replace(".homebar", ':root[data-theme="light"] .homebar') + '</style>'
          '<div class="homebar">The old admin, served from home. <a href="/">Back to the control panel</a></div>')


def page():
    """The old admin page, its calls pointed at /admin/api/ (and its own links at /admin/)."""
    status, kind, data = call("GET", "/")
    if status != 200:
        return status, kind, data
    text = data.decode("utf-8", "replace")
    for old, new in (('"/api/', '"/admin/api/'), ("'/api/", "'/admin/api/"), ("`/api/", "`/admin/api/"),
                     ('href="/check"', 'href="/admin/check"'), THEME_KEY):
        text = text.replace(old, new)
    text = text.replace("<body>", "<body>" + BANNER, 1)
    return 200, "text/html; charset=utf-8", text.encode()


def check_page():
    status, kind, data = call("GET", "/check")
    if status != 200:
        return status, kind, data
    text = data.decode("utf-8", "replace")
    for old, new in (('"/api/', '"/admin/api/'), ("'/api/", "'/admin/api/"), ("`/api/", "`/admin/api/"), THEME_KEY):
        text = text.replace(old, new)
    return 200, "text/html; charset=utf-8", text.encode()


def allowed_post(handler):
    """A change only from the panel's own page: its Origin is this host, and the page sent x-shorewind-admin."""
    origin = handler.headers.get("Origin") or ""
    host = handler.headers.get("Host") or ""
    same = urllib.parse.urlsplit(origin).netloc == host if origin else False
    return same and handler.headers.get("x-shorewind-admin") == "1"
