"""Web pages in the user's own Chromium browser (Brave, Chrome or Edge) through the DevTools Protocol.

browser_read lists the buttons, links and fields a person can see as numbered lines; browser_click and browser_type
act on a number from that list and return the page as it is afterwards. Reading the page this way takes tens of
milliseconds and clicks exactly the element that was read, where UI Automation sees little of a web page and a
screenshot plus a vision model takes seconds and guesses coordinates.

The browser must allow it once: open brave://inspect/#remote-debugging (or chrome://, edge://), tick "Allow remote
debugging for this browser instance", and click Allow when the browser asks. Iris keeps one connection open, so it
asks once per run. Nothing here reaches the network: only the browser on this PC at 127.0.0.1.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from iris import config

MAX_ITEMS = 120  # numbered controls per read; a page full of links is cut off here
MAX_TEXT = 1500  # characters of visible page text after the list


class BrowserError(Exception):
    """Something the user can fix, said in words."""


def _profiles() -> list[Path]:
    env = config.setting("IRIS_BROWSER_PROFILE", "browser_profile")
    if env:
        return [Path(env)]
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return [local / "BraveSoftware" / "Brave-Browser" / "User Data", local / "Google" / "Chrome" / "User Data",
            local / "Microsoft" / "Edge" / "User Data"]


def _ws_url() -> str:
    """The browser's DevTools websocket, from the DevToolsActivePort file it writes when remote debugging is on."""
    for base in _profiles():
        try:
            lines = (base / "DevToolsActivePort").read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        port, path = (lines + ["", ""])[0].strip(), (lines + ["", ""])[1].strip()
        if not port.isdigit():
            continue
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1) as r:
                return json.load(r)["webSocketDebuggerUrl"]
        except urllib.error.HTTPError as exc:
            if exc.code == 403:
                raise BrowserError("the browser is waiting for you to click Allow on its remote debugging "
                                   "question") from None
            if exc.code == 404 and path:  # newer browsers turn off /json/* on the default profile
                return f"ws://127.0.0.1:{port}{path}"
        except (OSError, ValueError, KeyError):
            continue  # a stale file from a browser that is closed now
    raise BrowserError("the browser does not allow remote control yet: open brave://inspect/#remote-debugging, tick "
                       "'Allow remote debugging for this browser instance', and click Allow when it asks")


class Connection:
    """One DevTools websocket to the whole browser; commands for a tab carry its session id."""

    def __init__(self, url: str, handshake: float = 60):
        try:
            import websocket  # websocket-client, the browser extra
        except ImportError:
            raise BrowserError("install the browser extra first: pip install -e .[browser]") from None

        # the handshake waits while the browser asks the user to Allow it
        self.ws: Any = websocket.create_connection(url, timeout=handshake, suppress_origin=True)
        self.ws.settimeout(15)
        self._id = 0
        self._slow = (TimeoutError, websocket.WebSocketTimeoutException)
        self._lock = threading.Lock()  # the watcher's check and a request can send at the same moment

    def send(self, method: str, params: dict | None = None, session: str | None = None, wait: float = 15) -> dict:
        """Send one DevTools command and wait for the reply with its id, skipping events in between."""
        with self._lock:
            self._id += 1
            msg: dict[str, Any] = {"id": self._id, "method": method, "params": params or {}}
            if session:
                msg["sessionId"] = session
            self.ws.send(json.dumps(msg))
            self.ws.settimeout(wait)
            while True:
                try:
                    reply = json.loads(self.ws.recv())
                except self._slow:
                    # a busy or frozen tab, not a dead socket: reconnecting would make the browser ask Allow again,
                    # and the late reply is skipped by its id
                    raise BrowserError(f"the browser did not answer {method} within {wait:g} s") from None
                if reply.get("id") == self._id:  # events and other replies are skipped
                    if "error" in reply:
                        raise BrowserError(f"{method} failed: {reply['error'].get('message', reply['error'])}")
                    return reply.get("result", {})


_conn: Connection | None = None
_sessions: dict[str, str] = {}  # targetId -> sessionId, so a tab is attached once
_last: dict[int, dict] = {}  # number -> item of the last list Iris read, for tiers and confirm questions
_last_tab: list[str] = []  # the DevTools session of the tab that list came from; empty when it came from text
_watching = threading.Event()  # voice mode: watch() connects, so a request never waits on the browser's question
WATCH_WAIT = 5.0  # seconds a request waits for watch() to have the connection
ASKS = "Brave asks whether Iris may control it. Click Allow in Brave."


def _connect() -> Connection:
    global _conn
    if _conn is None:
        if _watching.is_set():
            deadline = time.perf_counter() + WATCH_WAIT
            while _conn is None and time.perf_counter() < deadline:
                time.sleep(0.1)
            if _conn is None:
                raise BrowserError("the browser is not connected yet: open Brave, and click Allow when it asks "
                                   "whether Iris may control it")
            return _conn
        _conn = Connection(_ws_url())
        _sessions.clear()
    return _conn


def watch(announce: Callable[[str], object] | None = None, every: float = 5, ask_after: float = 3,
          stop: threading.Event | None = None) -> threading.Thread:
    """Voice mode: connect in the background as soon as the browser is open and allows remote control, so its Allow
    question comes when Iris or the browser starts instead of halfway through a request, and connect again after the
    browser restarts. One click per Iris run while the browser stays open. `announce` says the question is waiting."""
    _watching.set()

    def loop() -> None:
        global _conn
        pause = every
        while not (stop and stop.is_set()):
            try:
                if _conn is None:
                    url = _ws_url()  # a BrowserError while the browser is closed or does not allow it yet
                    asking = threading.Timer(ask_after, lambda: announce and announce(ASKS))
                    asking.daemon = True
                    asking.start()  # a handshake that takes this long is waiting for the user's click
                    try:
                        conn = Connection(url, handshake=600)
                    finally:
                        asking.cancel()
                    _sessions.clear()
                    _conn = conn
                else:
                    _conn.send("Browser.getVersion", wait=5)  # still there? a closed browser breaks the socket
                pause = every
            except BrowserError:
                pass  # closed, not allowed, or busy: look again soon
            except Exception:  # noqa: BLE001 - a broken socket, or the question was refused or left open
                if _conn is None:
                    pause = min(max(pause * 2, 30), 600)  # do not ask again every few seconds after a Cancel
                _conn = None
            time.sleep(pause)

    thread = threading.Thread(target=loop, daemon=True, name="browser-watch")
    thread.start()
    return thread


def _call(method: str, params: dict | None = None, session: str | None = None, wait: float = 15) -> dict:
    """A command, with one reconnect when the browser was restarted since the last one."""
    global _conn
    try:
        return _connect().send(method, params, session, wait)
    except BrowserError:
        raise
    except Exception:  # noqa: BLE001 - a dropped socket: connect again once
        _conn = None
        return _connect().send(method, params, session, wait)


def _eval(session: str, expression: str, wait: float = 15) -> Any:
    result = _call("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True},
                   session, wait)
    if "exceptionDetails" in result:
        raise BrowserError("the page refused the script: " + str(result["exceptionDetails"].get("text", ""))[:200])
    return result.get("result", {}).get("value")


def _tab(tab: str = "") -> str:
    """The session of the tab whose title or address contains `tab`, else the tab the user is looking at."""
    pages = [t for t in _call("Target.getTargets").get("targetInfos", [])
             if t.get("type") == "page" and not t.get("url", "").startswith(("devtools://", "chrome-extension://"))]
    if tab:
        want = tab.lower()
        pages = [t for t in pages if want in t.get("title", "").lower() or want in t.get("url", "").lower()]
        if not pages:
            raise BrowserError(f"no browser tab whose title or address contains {tab!r}")
    if not pages:
        raise BrowserError("the browser has no open tab")
    best: tuple[int, str] | None = None
    for t in pages:
        tid = t["targetId"]
        try:
            if tid not in _sessions:
                _sessions[tid] = _call("Target.attachToTarget", {"targetId": tid, "flatten": True})["sessionId"]
            score = _eval(_sessions[tid], "(document.visibilityState === 'visible') + document.hasFocus()", 2) or 0
        except BrowserError:  # a frozen background tab, or one whose session went stale: not the one in front
            _sessions.pop(tid, None)
            if tab:
                raise
            continue
        if best is None or score > best[0]:
            best = (score, tid)
        if score == 2 or tab:
            break
    if best is None:
        raise BrowserError("no browser tab answered; click in the tab you mean and ask again")
    return _sessions[best[1]]


# Numbers every control a person can see, keeps the elements in window.__iris for clicking, and returns the list.
_SNAPSHOT = """(() => {
const sel = 'a[href],button,input:not([type=hidden]),select,textarea,summary,[role=button],[role=link],[role=tab],' +
  '[role=menuitem],[role=option],[role=checkbox],[role=radio],[role=switch],[role=combobox],[role=searchbox],' +
  '[role=textbox],[contenteditable=""],[contenteditable=true],[onclick],[tabindex]:not([tabindex="-1"])';
const seen = new Set(), items = [], els = [];
const vw = innerWidth, vh = innerHeight;
const clean = s => (s || '').replace(/\\s+/g, ' ').trim().slice(0, 80);
for (const el of document.querySelectorAll(sel)) {
  if (els.length >= MAX_ITEMS) break;
  const r = el.getBoundingClientRect();
  if (r.width < 2 || r.height < 2 || r.bottom < 0 || r.right < 0 || r.top > vh || r.left > vw) continue;
  const cs = getComputedStyle(el);
  if (cs.visibility === 'hidden' || cs.display === 'none' || +cs.opacity === 0) continue;
  let p = el.parentElement, inside = false;
  while (p) {
    if (seen.has(p) && p.matches('a[href],button,[role=button],[role=link]')) { inside = true; break; }
    p = p.parentElement;
  }
  if (inside) continue;
  seen.add(el);
  const tag = el.tagName.toLowerCase(), type = (el.getAttribute('type') || '').toLowerCase();
  const by = el.getAttribute('aria-labelledby');
  const labelled = by ? by.split(/\\s+/).map(id => (document.getElementById(id) || {}).innerText || '').join(' ') : '';
  const img = el.querySelector && el.querySelector('img[alt]');
  const own = el.labels && el.labels[0] && el.labels[0].innerText;
  const name = clean(el.getAttribute('aria-label') || labelled || own ||
    el.innerText || el.getAttribute('placeholder') || el.getAttribute('title') || (img && img.alt) ||
    (tag === 'input' && ['submit', 'button'].includes(type) ? el.value : ''));
  const role = el.getAttribute('role') || (tag === 'a' ? 'link' : tag === 'select' ? 'select' :
    tag === 'textarea' ? 'textbox' : tag === 'input' ? (['checkbox', 'radio', 'submit', 'button'].includes(type) ?
    (['submit', 'button'].includes(type) ? 'button' : type) : 'textbox') : tag === 'button' ? 'button' : 'clickable');
  const field = ['input', 'textarea', 'select'].includes(tag) &&
    !['submit', 'button', 'checkbox', 'radio'].includes(type);
  const tick = type === 'checkbox' || type === 'radio';
  const value = field ? (type === 'password' ? '' : clean(el.value)) : (tick ? (el.checked ? 'checked' : '') : null);
  if (!name && value === null) continue;
  els.push(el);
  items.push({role, name, value, type});
}
window.__iris = els;
const text = document.body ? (document.body.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, MAX_TEXT) : '';
return JSON.stringify({title: document.title, url: location.href, items, text});
})()""".replace("MAX_ITEMS", str(MAX_ITEMS)).replace("MAX_TEXT", str(MAX_TEXT))


def _format(snap: dict) -> str:
    lines = [f"Tab: {snap.get('title', '')} ({snap.get('url', '')})"]
    for i, it in enumerate(snap.get("items", []), 1):
        line = f'[{i}] {it["role"]} "{it["name"]}"'
        if it.get("value") is not None:
            line += f' = "{it["value"]}"' if it["value"] else " (empty)"
        lines.append(line)
    if not snap.get("items"):
        lines.append("(no buttons, links or fields visible; scroll, or wait for the page to load)")
    if snap.get("text"):
        lines.append("Page text: " + snap["text"])
    return "\n".join(lines)


def remember(text: str) -> None:
    """Keep the numbered items of a list Iris read, so tiers and questions know what number 3 is."""
    _last.clear()
    _last_tab.clear()
    url = ""
    for line in text.splitlines():
        if line.startswith("Tab: ") and line.endswith(")") and " (" in line:
            url = line[line.rindex(" (") + 2:-1]
        m = re.match(r'\[(\d+)\] (\S+) "(.*?)"(?: = ".*"| \(empty\))?$', line)
        if m:
            _last[int(m[1])] = {"role": m[2], "name": m[3], "url": url,
                                "type": "password" if "password" in m[3].lower() else ""}


def item(index: int) -> dict:
    """What number `index` was in the last list, or {} when Iris has not read it."""
    try:
        return _last.get(int(index), {})
    except (TypeError, ValueError):
        return {}


def _read(session: str) -> str:
    snap = json.loads(_eval(session, _SNAPSHOT))
    text = _format(snap)
    remember(text)
    for i, it in enumerate(snap.get("items", []), 1):  # the page's own name and type, which the text can not hold
        _last.setdefault(i, {"role": it.get("role", ""), "url": snap.get("url", "")})
        _last[i].update(name=it.get("name", ""), type=it.get("type", ""))
    _last_tab.append(session)
    return text


def _same_tab(session: str) -> None:
    """Numbers belong to the tab they were read in: the tier and the question describe that tab's item, so a click
    in another tab is refused instead of hitting whatever that tab has under the same number."""
    if _last_tab and _last_tab[0] != session:
        raise BrowserError("the tab in front is not the one Iris read last; call browser_read first, then use its "
                           "numbers")


def _settle(session: str) -> None:
    """Give the page a moment to react: a short pause, then up to 2 s while it is still loading."""
    time.sleep(0.4)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and _eval(session, "document.readyState") != "complete":
        time.sleep(0.1)


def _point(session: str, index: int) -> tuple[float, float]:
    """The centre of element `index` after scrolling it into view; an error when it is gone or covered."""
    js = """(() => { const el = (window.__iris || [])[INDEX];
      if (!el || !el.isConnected) return 'stale';
      el.scrollIntoView({block: 'center', inline: 'center'});
      const r = el.getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + r.height / 2;
      const top = document.elementFromPoint(x, y);
      if (top && top !== el && !el.contains(top) && !top.contains(el)) return 'covered';
      return JSON.stringify([x, y]); })()""".replace("INDEX", str(int(index) - 1))
    got = _eval(session, js)
    if got == "stale":
        raise BrowserError(f"item {index} is no longer on the page; call browser_read again")
    if got == "covered":
        raise BrowserError(f"item {index} is covered by something else (a pop-up?); call browser_read again")
    x, y = json.loads(got)
    return float(x), float(y)


def _mouse_click(session: str, x: float, y: float) -> None:
    for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
        _call("Input.dispatchMouseEvent", {"type": kind, "x": x, "y": y, "button": "left", "clickCount": 1}, session)


def browser_read(tab: str = "") -> str:
    """The current tab (or the one matching `tab`) as numbered buttons, links and fields plus some visible text."""
    return _read(_tab(tab))


_opened: dict[str, str] = {}  # site -> the tab Iris opened for it, reused for the next page on that site


def open_page(url: str, wait: float = 4) -> str:
    """Open `url` in front and return that page as browser_read does, so the next step can act on it at once instead
    of spending a step on reading it. A site Iris already opened a tab for gets that tab again (three searches on
    Netflix are one tab, not three); the user's own tabs are never taken over. Raises BrowserError only when no tab
    could be opened."""
    site = urlparse(url).hostname or ""
    tid = _opened.get(site, "")
    alive = {t["targetId"] for t in _call("Target.getTargets").get("targetInfos", [])} if tid else set()
    before = None
    if tid in alive:
        if tid not in _sessions:
            _sessions[tid] = _call("Target.attachToTarget", {"targetId": tid, "flatten": True})["sessionId"]
        before = _eval(_sessions[tid], "location.href", 2)
        _call("Page.navigate", {"url": url}, _sessions[tid])
        _call("Target.activateTarget", {"targetId": tid})
    else:
        tid = _opened[site] = _call("Target.createTarget", {"url": url})["targetId"]
    try:
        session = _sessions.get(tid) or _call("Target.attachToTarget", {"targetId": tid, "flatten": True})["sessionId"]
        _sessions[tid] = session
        deadline = time.perf_counter() + wait
        text = ""
        while time.perf_counter() < deadline:
            time.sleep(0.4)
            try:
                if _eval(session, "document.readyState", 2) != "complete":
                    continue
                if before is not None and before != url and _eval(session, "location.href", 2) == before:
                    continue  # the reused tab still shows the old page
                text = _read(session)
            except BrowserError:
                continue  # still navigating
            if text.count("\n[") >= 3:  # a page that draws itself after loading (Netflix) has its links by now
                break
        if not text:
            text = _read(session)
    except BrowserError as exc:
        return f"opened {url} (could not read it yet: {exc}; call browser_read)"
    return f"opened {url}\n\n{text}"


def browser_click(index: int, tab: str = "") -> str:
    """Click item `index` from the last browser_read and return the page as it is now."""
    session = _tab(tab)
    _same_tab(session)
    what = item(index)
    _mouse_click(session, *_point(session, index))
    _settle(session)
    return f'clicked [{index}] {what.get("role", "")} "{what.get("name", "")}"\n\n' + _read(session)


def browser_type(index: int, text: str, enter: bool = False, tab: str = "") -> str:
    """Replace the text in field `index` with `text`; enter=True presses Enter after (to search). Returns the page."""
    session = _tab(tab)
    _same_tab(session)
    _mouse_click(session, *_point(session, index))
    _eval(session, """(() => { const el = (window.__iris || [])[INDEX]; if (!el) return;
      el.focus(); if (el.select) el.select(); else document.execCommand('selectAll'); })()"""
          .replace("INDEX", str(int(index) - 1)))
    _call("Input.insertText", {"text": text}, session)
    if enter:
        for kind in ("keyDown", "keyUp"):
            _call("Input.dispatchKeyEvent", {"type": kind, "key": "Enter", "code": "Enter",
                                             "windowsVirtualKeyCode": 13, "text": "\r"}, session)
    _settle(session)
    return f"typed {len(text)} characters into [{index}]" + (" and pressed Enter" if enter else "") + "\n\n" + \
        _read(session)


def browser_scroll(direction: str = "down", tab: str = "") -> str:
    """Scroll the page by most of a screen and return what is visible then."""
    if direction not in ("down", "up"):
        return "error: direction must be down or up"
    session = _tab(tab)
    _eval(session, "window.scrollBy(0, %s * innerHeight * 0.8)" % ("1" if direction == "down" else "-1"))
    time.sleep(0.3)
    return f"scrolled {direction}\n\n" + _read(session)


# Sites where Enter sends a message: typing there with enter=True is sending.
_SENDS_ON_ENTER = re.compile(r"(mail\.google|outlook\.(live|office)|web\.whatsapp|discord\.com|slack\.com|"
                             r"teams\.microsoft|messenger\.com|web\.telegram|linkedin\.com/messaging|x\.com/messages)",
                             re.I)


def sends_on_enter(index: int) -> bool:
    """Whether the page of item `index` is one where pressing Enter sends what was typed (mail or chat)."""
    return bool(_SENDS_ON_ENTER.search(item(index).get("url", "")))


def host(index: int) -> str:
    """The site name of item `index`, for confirm questions; 'the browser' when it has no url."""
    return urlparse(item(index).get("url", "")).hostname or "the browser"


# --- web search in a background tab -----------------------------------------------------------------------------------
SEARCH_URL = os.environ.get("IRIS_SEARCH_URL", "https://search.brave.com/search?q={q}")

# Results are the links whose text is a heading or a title, on other sites than the search engine; the snippet is the
# text of the smallest block around the link that holds more than its title. No engine-specific class names, so a
# redesign of the result page does not break it.
_RESULTS = r"""(() => {
  const text = (document.body ? document.body.innerText : '').slice(0, 4000).toLowerCase();
  const human = /captcha|not a robot|unusual traffic|verify you are (a )?human|bots use|geen robot/.test(text);
  const site = location.hostname.split('.').slice(-2).join('.');
  const out = [], seen = new Set();
  for (const a of document.querySelectorAll('a[href]')) {
    let url;
    try { url = new URL(a.href); } catch (e) { continue; }
    if (!/^https?:$/.test(url.protocol) || url.hostname.endsWith(site) || !a.offsetParent) continue;
    const head = a.querySelector('h1, h2, h3, h4, [class*="title"]');
    const title = ((head || a).innerText || '').replace(/\s+/g, ' ').trim();
    if (!head || title.length < 6) continue;
    const key = url.href.split('#')[0];
    if (seen.has(key)) continue;
    seen.add(key);
    let box = a, snippet = '';
    for (let i = 0; i < 6 && box.parentElement; i++) {
      box = box.parentElement;
      const t = (box.innerText || '').replace(/\s+/g, ' ').trim();
      if (t.length > title.length + 40) { snippet = t.replace(title, '').trim().slice(0, 300); break; }
    }
    out.push({title: title.slice(0, 150), url: key, snippet});
    if (out.length >= MAX) break;
  }
  return JSON.stringify({human, results: out});
})()"""


def search(query: str, max_results: int = 5, wait: float = 8) -> list[dict]:
    """Search the web in a background tab of the user's own browser and return [{title, url, snippet}]. The tab is
    closed afterwards. A search engine that asks for a human check raises BrowserError: Iris never solves those."""
    from urllib.parse import quote_plus

    target = _call("Target.createTarget", {"url": SEARCH_URL.format(q=quote_plus(query)), "background": True})
    tid = target["targetId"]
    try:
        session = _call("Target.attachToTarget", {"targetId": tid, "flatten": True})["sessionId"]
        deadline = time.perf_counter() + wait
        found: dict = {"human": False, "results": []}
        while time.perf_counter() < deadline:  # the results are often drawn after the page itself has loaded
            time.sleep(0.25)
            try:
                found = json.loads(_eval(session, _RESULTS.replace("MAX", str(max_results)), wait=3) or "{}")
            except (BrowserError, ValueError):
                continue  # still navigating: the next look will see the page
            if found.get("human") or found.get("results"):
                break
    finally:
        try:
            _call("Target.closeTarget", {"targetId": tid})
        except BrowserError:
            pass
    if found.get("human"):
        raise BrowserError("the search engine asks for a human check; Iris does not solve those")
    return found.get("results") or []
