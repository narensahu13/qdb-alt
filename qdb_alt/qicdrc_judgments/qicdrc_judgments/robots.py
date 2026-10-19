"""robots.txt read the way RFC 9309 says, because Python's own parser does not.

urllib.robotparser takes the first rule that matches and knows no wildcards,
so on qicdrc.gov.qa it would get the paging rules (`/*?page=*&`) wrong. Here:

* the groups for our product token are merged; if there are none, the `*`
  groups are merged and used instead;
* `*` matches any run of characters and a final `$` anchors the end;
* of the rules that match a path, the longest pattern wins, and on a tie
  Allow wins (the least restrictive);
* `/robots.txt` itself is always allowed.

Crawl-delay is not in the RFC, but the site asks for one, so it is read too.
"""

from __future__ import annotations

import re
from urllib.parse import quote, unquote, urlsplit


def _norm(s: str) -> str:
    """Percent-encoding made comparable: '/judgments/caf%C3%A9' == '/judgments/café'."""
    return quote(unquote(s), safe="/?&=*$:@!,;+~-._")


def _pattern(path: str) -> re.Pattern:
    anchored = path.endswith("$")
    body = path[:-1] if anchored else path
    rx = ".*".join(re.escape(part) for part in _norm(body).split("*"))
    return re.compile(rx + (r"\Z" if anchored else ""))


class Robots:
    def __init__(self, text: str, agent: str):
        self.agent = agent.lower()
        groups: list[tuple[list[str], list[tuple[bool, str]], list[float]]] = []
        agents: list[str] = []
        rules: list[tuple[bool, str]] = []
        delays: list[float] = []
        in_agents = False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, value = (x.strip() for x in line.split(":", 1))
            key = key.lower()
            if key == "user-agent":
                if not in_agents:                     # a new group starts
                    agents, rules, delays = [], [], []
                    groups.append((agents, rules, delays))
                    in_agents = True
                agents.append(value.lower())
                continue
            in_agents = False
            if not groups:
                continue                              # rules before any user-agent line apply to nobody
            if key in ("allow", "disallow") and value:
                rules.append((key == "allow", value))
            elif key == "crawl-delay":
                try:
                    delays.append(float(value))
                except ValueError:
                    pass
        mine = [g for g in groups if self.agent in g[0]] or [g for g in groups if "*" in g[0]]
        self.rules = [(allow, len(_norm(p)), _pattern(p)) for g in mine for allow, p in g[1]]
        all_delays = [d for g in mine for d in g[2]]
        self.crawl_delay = max(all_delays) if all_delays else None

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        target = _norm((parts.path or "/") + (f"?{parts.query}" if parts.query else ""))
        if target == "/robots.txt":
            return True
        best: tuple[int, bool] | None = None          # (pattern length, allow)
        for allow, length, rx in self.rules:
            if rx.match(target) and (best is None or (length, allow) > best):
                best = (length, allow)
        return True if best is None else best[1]
