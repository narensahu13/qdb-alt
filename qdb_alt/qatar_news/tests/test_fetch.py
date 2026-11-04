"""The polite reader: its User-Agent, robots.txt read once per host and obeyed
before every request (a redirect included), the Crawl-delay kept per host,
one retry, and a removed page told apart from a failing one."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

try:
    from qatar_news.fetch import UA, Disallowed, PageGone, Site, SiteError, gt_search_url
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from qatar_news.fetch import UA, Disallowed, PageGone, Site, SiteError, gt_search_url

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fake_site import FakeTimer, Response  # noqa: E402

ROBOTS = "https://a.example/robots.txt"


class Scripted:
    """robots.txt from a dict; every other URL answered with the next response from a list."""

    def __init__(self, *responses, robots="User-agent: *\nDisallow: /private/\nCrawl-delay: 5\n"):
        self.responses, self.robots, self.urls, self.headers = list(responses), robots, [], []

    def get(self, url, headers=None, timeout=None):
        self.urls.append(url)
        self.headers.append(headers)
        if url.endswith("/robots.txt"):
            return self.robots if isinstance(self.robots, Response) else Response(200, self.robots, url)
        r = self.responses.pop(0)
        r.url = r.url or url
        return r


def reader(http, delay=0.0):
    timer = FakeTimer()
    return Site(delay=delay, session=http, timer=timer), timer


class Reader(unittest.TestCase):
    def test_says_who_it_is(self):
        http = Scripted(Response(200, "ok"))
        reader(http)[0].get("https://a.example/x")
        self.assertEqual(http.headers, [UA, UA])
        self.assertIn("qdb-alt/qatar_news", UA["User-Agent"])

    def test_robots_txt_is_read_once_per_host_before_anything_else(self):
        http = Scripted(Response(200, "1"), Response(200, "2"), Response(200, "3"))
        site, _ = reader(http)
        site.get("https://a.example/x")
        site.get("https://a.example/y")
        site.get("https://b.example/z")
        self.assertEqual(http.urls, [ROBOTS, "https://a.example/x", "https://a.example/y",
                                     "https://b.example/robots.txt", "https://b.example/z"])

    def test_a_disallowed_url_is_never_requested(self):
        http = Scripted()
        with self.assertRaises(Disallowed):
            reader(http)[0].get("https://a.example/private/list")
        self.assertEqual(http.urls, [ROBOTS])

    def test_a_redirect_into_a_disallowed_path_is_refused(self):
        http = Scripted(Response(200, "secret", "https://a.example/private/x"))
        with self.assertRaises(Disallowed):
            reader(http)[0].get("https://a.example/open")

    def test_the_crawl_delay_is_kept_between_requests_to_a_host(self):
        http = Scripted(Response(200, "1"), Response(200, "2"))
        site, timer = reader(http, delay=2)
        site.get("https://a.example/x")
        t0 = timer.now
        site.get("https://a.example/y")
        self.assertGreaterEqual(timer.now - t0, 5)          # Crawl-delay 5 beats our own 2

    def test_robots_txt_missing_means_no_rules_unreadable_means_not_read(self):
        http = Scripted(Response(200, "ok"), robots=Response(404, "nope"))
        self.assertEqual(reader(http)[0].get("https://a.example/private/x"), "ok")
        http = Scripted(Response(200, "ok"), robots=Response(503, "down"))
        with self.assertRaises(SiteError):
            reader(http)[0].get("https://a.example/x")
        self.assertNotIn("https://a.example/x", http.urls)

    def test_tries_once_more_then_gives_up(self):
        http = Scripted(Response(503, ""), Response(200, "second time"))
        self.assertEqual(reader(http)[0].get("https://a.example/u"), "second time")
        http = Scripted(Response(503, ""), Response(502, ""))
        with self.assertRaises(SiteError):
            reader(http)[0].get("https://a.example/u")

    def test_a_removed_page_or_a_refusal_is_not_retried(self):
        http = Scripted(Response(404, ""))
        with self.assertRaises(PageGone):
            reader(http)[0].get("https://a.example/u")
        http = Scripted(Response(403, ""))
        with self.assertRaises(SiteError):
            reader(http)[0].get("https://a.example/u")
        self.assertEqual(http.urls, [ROBOTS, "https://a.example/u"])

    def test_the_gulf_times_search_address(self):
        self.assertEqual(gt_search_url("Al Haya Waste & Treatment"),
                         "https://www.gulf-times.com/search?query=Al+Haya+Waste+%26+Treatment&match=exact")


if __name__ == "__main__":
    unittest.main()
