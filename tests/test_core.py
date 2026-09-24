"""Lightweight unit tests for domain matching and HTML parsing (no network)."""

from __future__ import annotations

import unittest

from backlink_checker.domain import (
    extract_registrable_host,
    href_points_to_domain,
    normalize_host,
)
from backlink_checker.pagination import (
    extract_pagination_links,
    synthesize_next_page,
)
from backlink_checker.parser import find_backlinks, looks_js_heavy


class DomainTests(unittest.TestCase):
    def test_normalize_www(self):
        self.assertEqual(normalize_host("WWW.Example.COM"), "example.com")
        self.assertEqual(extract_registrable_host("https://www.mysite.com/path"), "mysite.com")

    def test_href_variations(self):
        page = "https://directory.example/list"
        me = "mysite.com"
        self.assertIsNotNone(href_points_to_domain("https://mysite.com", page, me))
        self.assertIsNotNone(href_points_to_domain("http://www.mysite.com/", page, me))
        self.assertIsNotNone(href_points_to_domain("https://mysite.com/blog/post", page, me))
        self.assertIsNotNone(href_points_to_domain("https://blog.mysite.com/", page, me))
        self.assertIsNone(href_points_to_domain("https://notmysite.com", page, me))
        self.assertIsNone(href_points_to_domain("https://example.com", page, me))
        self.assertIsNone(href_points_to_domain("#section", page, me))
        self.assertIsNone(href_points_to_domain("mailto:hi@mysite.com", page, me))


class ParserTests(unittest.TestCase):
    def test_find_dofollow_and_nofollow(self):
        html = """
        <html><body>
          <a href="https://www.mysite.com/about">About us</a>
          <a href="http://mysite.com/" rel="noopener nofollow">Home</a>
          <a href="/local">Ignore</a>
        </body></html>
        """
        matches = find_backlinks(html, "https://news.example/article", "mysite.com")
        self.assertEqual(len(matches), 2)
        by_text = {m.anchor_text: m for m in matches}
        self.assertEqual(by_text["About us"].follow_status, "dofollow")
        self.assertEqual(by_text["Home"].follow_status, "nofollow")

    def test_js_heuristic(self):
        spa = '<div id="root"></div><script src="app.js"></script>' * 3
        self.assertTrue(looks_js_heavy(spa))
        normal = "<html><body>" + "".join(f'<a href="/p{i}">t</a>' for i in range(10)) + "</body></html>"
        self.assertFalse(looks_js_heavy(normal))


class PaginationTests(unittest.TestCase):
    def test_rel_next_and_label(self):
        html = """
        <html><head><link rel="next" href="/blog/page/2/"></head>
        <body>
          <a href="/blog?page=2">Next</a>
          <a href="/about">About</a>
          <a class="nav-previous" href="/blog/page/3/">Older posts</a>
        </body></html>
        """
        links = extract_pagination_links(
            html, "https://example.com/blog/", "example.com"
        )
        joined = " ".join(links)
        self.assertIn("/blog/page/2", joined)
        self.assertIn("page=2", joined)
        self.assertIn("/blog/page/3", joined)
        self.assertNotIn("/about", joined)

    def test_synthesize_query_and_path(self):
        self.assertEqual(
            synthesize_next_page("https://example.com/blog?page=2"),
            "https://example.com/blog?page=3",
        )
        self.assertTrue(
            synthesize_next_page("https://example.com/blog/page/4/").endswith(
                "/blog/page/5/"
            )
            or "/blog/page/5" in (synthesize_next_page("https://example.com/blog/page/4/") or "")
        )
        self.assertIsNone(synthesize_next_page("https://example.com/about"))


if __name__ == "__main__":
    unittest.main()
