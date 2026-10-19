"""The polite reader: its User-Agent, the gzipped sitemap, one retry, and a
removed page told apart from a failing one."""

from __future__ import annotations

import gzip
import sys
import unittest
from pathlib import Path

try:
    from doha_directory.fetch import UA, PageGone, Site, SiteError
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from doha_directory.fetch import UA, PageGone, Site, SiteError

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fake_site import Response  # noqa: E402


class Scripted:
    """Answers each request with the next response from a list, and remembers the headers."""

    def __init__(self, *responses):
        self.responses, self.headers = list(responses), []

    def get(self, url, headers=None, timeout=None):
        self.headers.append(headers)
        return self.responses.pop(0)


class Reader(unittest.TestCase):
    def test_says_who_it_is(self):
        http = Scripted(Response(200, "ok"))
        Site(delay=0, session=http).get("https://www.doha.directory/x/i/1")
        self.assertEqual(http.headers[0], UA)
        self.assertIn("qdb-alt/doha_directory", UA["User-Agent"])

    def test_unpacks_the_gzipped_sitemap(self):
        http = Scripted(Response(200, gzip.compress(b"<urlset/>")))
        self.assertEqual(Site(delay=0, session=http).sitemap(), "<urlset/>")

    def test_tries_once_more_then_gives_up(self):
        http = Scripted(Response(503, ""), Response(200, "second time"))
        self.assertEqual(Site(delay=0, session=http).get("u"), "second time")
        http = Scripted(Response(503, ""), Response(502, ""))
        with self.assertRaises(SiteError):
            Site(delay=0, session=http).get("u")

    def test_a_removed_page_is_not_retried(self):
        http = Scripted(Response(404, ""))
        with self.assertRaises(PageGone):
            Site(delay=0, session=http).get("u")
        self.assertEqual(len(http.headers), 1)


if __name__ == "__main__":
    unittest.main()
