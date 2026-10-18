import importlib.util
import json
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "scripts" / "collect.py"
SPEC = importlib.util.spec_from_file_location("curation_collect", SCRIPT)
collect = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(collect)


class Headers:
    def __init__(self, content_type):
        self.content_type = content_type

    def get_content_type(self):
        return self.content_type


class Response:
    def __init__(self, value, content_type="application/json"):
        self.value = value.encode("utf-8")
        self.headers = Headers(content_type)

    def read(self):
        return self.value

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class CollectTest(unittest.TestCase):
    def test_collects_bodies_source_errors_and_fallback(self):
        pages = {
            "https://zenn.dev/feed": "<rss><channel><item><title>Zenn</title><link>https://zenn.dev/a</link><pubDate>today</pubDate></item></channel></rss>",
            "https://dev.to/api/articles/latest?per_page=2": json.dumps([{"url": "https://dev.to/a", "title": "DEV", "description": "desc", "published_at": "2026-10-18T00:00:00Z"}]),
            "https://hacker-news.firebaseio.com/v0/topstories.json": "[1]",
            "https://hacker-news.firebaseio.com/v0/newstories.json": "[1]",
            "https://hacker-news.firebaseio.com/v0/showstories.json": "[1]",
            "https://hacker-news.firebaseio.com/v0/item/1.json": json.dumps({"type": "story", "id": 1, "title": "HN", "url": "https://example.test/hn", "time": 1, "score": 10}),
            "https://github.com/trending?since=weekly": '<a href="/navigation/ignored">nav</a><article class="Box-row"><h2><a href="/owner/repo">repo</a></h2>42 stars this week</article>',
            "https://zenn.dev/a": "<html><body><article>" + "Zenn body " * 20 + "</article></body></html>",
            "https://dev.to/a": "<html><body><article>" + "DEV body " * 20 + "</article></body></html>",
            "https://example.test/hn": "<html><body><article>" + "HN body " * 20 + "</article></body></html>",
            "https://raw.githubusercontent.com/owner/repo/HEAD/README.md": "GitHub README",
        }

        def opener(request, timeout):
            url = request.full_url
            if url not in pages:
                raise collect.urllib.error.URLError(url)
            return Response(pages[url], "text/html" if url.startswith("https://") and "/api/" not in url and "firebase" not in url else "application/json")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "TARGET.md"
            target.write_text("## 対象ページ\n" + "\n".join(["https://zenn.dev/", "https://dev.to/", "https://news.ycombinator.com/", "https://note.com/", "https://medium.com/", "https://github.com/"]), encoding="utf-8")
            fallback = root / "fallback.json"
            fallback.write_text(json.dumps({"candidates": [{"url": "https://note.com/a?utm_source=x", "source": "note", "title": "note", "content": {"status": "fetched", "text": "note body"}}]}), encoding="utf-8")
            output = root / "collected.json"
            result = collect.collect(target, output, 2, fallback, opener)

            self.assertTrue(output.exists())
            self.assertEqual({"zenn", "dev", "hacker_news", "note", "medium", "github"}, set(result["sources"]))
            self.assertIn("fallback_required", result["sources"]["note"]["error"])
            self.assertEqual(5, len(result["candidates"]))
            self.assertEqual("fetched", next(item for item in result["candidates"] if item["source"] == "dev")["content"]["status"])
            self.assertEqual(["https://note.com/a"], result["fallback_imported_urls"])

    def test_target_section_fallback_repair_hn_and_github_rows(self):
        target = Path(tempfile.mkstemp()[1])
        self.addCleanup(target.unlink)
        target.write_text("## 関心事\nhttps://medium.com/not-a-target\n## 対象ページ\nhttps://zenn.dev/one\nhttps://zenn.dev/two\n## 出力先\nhttps://github.com/not-a-target", encoding="utf-8")
        self.assertEqual(["https://zenn.dev/one", "https://zenn.dev/two"], collect.target_urls(target))
        self.assertEqual(["https://zenn.dev/one/feed", "https://zenn.dev/two/feed"], collect.configured_feeds("zenn", collect.target_urls(target)))

        candidates = [collect.candidate("zenn", "https://example.test/a", "old", None, evidence_url="https://feed")]
        candidates[0]["content"] = {"url": "https://example.test/a", "status": "unreadable", "text": None, "error": "failed"}
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as fallback_file:
            json.dump({"candidates": [{"url": "https://example.test/a?utm_source=x", "content": {"status": "fetched", "text": "verified body", "error": None}, "discovery": {"url": "https://search.example/a"}}]}, fallback_file)
            fallback = Path(fallback_file.name)
        self.addCleanup(fallback.unlink)
        collect.merge_fallback(candidates, fallback)
        self.assertEqual("fetched", candidates[0]["content"]["status"])
        self.assertEqual("https://search.example/a", candidates[0]["discovery"]["url"])

        github, error = collect.collect_github(2, lambda request, timeout: Response('<a href="/nav/x">bad</a><article class="Box-row"><h2><a href="/a/one">one</a></h2>11 stars this week</article><article class="Box-row"><h2><a href="/b/two">two</a></h2>22 stars this week</article>', "text/html"))
        self.assertIsNone(error)
        self.assertEqual(["a/one", "b/two"], [item["title"] for item in github])
        self.assertEqual(["11", "22"], [item["metrics"]["stars_this_week"] for item in github])

        hn_pages = {f"https://hacker-news.firebaseio.com/v0/{name}.json": json.dumps([number]) for name, number in (("topstories", 1), ("newstories", 2), ("showstories", 3))}
        hn_pages.update({f"https://hacker-news.firebaseio.com/v0/item/{number}.json": json.dumps({"type": "story", "id": number, "title": str(number), "url": f"https://example.test/{number}"}) for number in (1, 2, 3)})
        def hn_opener(request, timeout):
            return Response(hn_pages[request.full_url])
        hn, _ = collect.collect_hn(3, hn_opener)
        self.assertEqual(["1", "2", "3"], [item["title"] for item in hn])

    def test_article_body_rejects_navigation_and_binary_content(self):
        self.assertEqual("partial", collect.article_body("https://example.test", lambda request, timeout: Response("<html><body><article>short</article></body></html>", "text/html"))["status"])
        self.assertEqual("unreadable", collect.article_body("https://example.test", lambda request, timeout: Response("png", "image/png"))["status"])
        self.assertEqual("unreadable", collect.article_body("file:///etc/passwd", lambda request, timeout: self.fail("must not open local files"))["status"])

    def test_public_url_validation_and_hn_text_only_story(self):
        private = lambda host, port, type: [(None, None, None, None, ("127.0.0.1", 0))]
        self.assertFalse(collect.is_public_url("https://localhost./", private)[0])
        self.assertFalse(collect.is_public_url("https://2130706433/", private)[0])
        self.assertFalse(collect.is_public_url("https://public.example/", private)[0])

        pages = {f"https://hacker-news.firebaseio.com/v0/{name}.json": "[1]" for name in ("topstories", "newstories", "showstories")}
        pages["https://hacker-news.firebaseio.com/v0/item/1.json"] = json.dumps({"type": "story", "id": 1, "title": "Ask", "text": "<p>body from HN</p>"})
        hn, _ = collect.collect_hn(1, lambda request, timeout: Response(pages[request.full_url]))
        self.assertEqual("body from HN", hn[0]["content"]["text"])
        self.assertEqual("fetched", hn[0]["content"]["status"])

        fallback = Path(tempfile.mkstemp()[1])
        self.addCleanup(fallback.unlink)
        fallback.write_text(json.dumps({"candidates": [{"url": "https://example.test/empty", "content": {"status": "fetched", "text": " "}}]}), encoding="utf-8")
        candidates = []
        collect.merge_fallback(candidates, fallback)
        self.assertEqual("unreadable", candidates[0]["content"]["status"])

    def test_collect_default_opener_validates_redirect_destinations(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "TARGET.md"
            output = root / "collected.json"
            target.write_text("## 対象ページ\nhttps://zenn.dev/", encoding="utf-8")
            calls = []
            headers = Message()
            headers["Location"] = "http://127.0.0.1/private"

            class RedirectingOpener:
                def open(self, request, timeout):
                    calls.append(request.full_url)
                    raise collect.urllib.error.HTTPError(request.full_url, 302, "redirect", headers, None)

            def resolver(host, port, type):
                address = "93.184.216.34" if host == "zenn.dev" else "127.0.0.1"
                return [(None, None, None, None, (address, 0))]

            with mock.patch.object(collect.urllib.request, "build_opener", return_value=RedirectingOpener()), mock.patch.object(collect.socket, "getaddrinfo", side_effect=resolver):
                result = collect.collect(target, output, 1, None)

            self.assertEqual(["https://zenn.dev/feed"], calls)
            self.assertIn("non-public address", result["sources"]["zenn"]["error"])

    def test_dev_tag_and_author_targets_select_public_api_endpoints(self):
        seen = []
        def opener(request, timeout):
            seen.append(request.full_url)
            return Response("[]")
        collect.collect_dev(3, ["https://dev.to/t/python"], opener)
        self.assertEqual("https://dev.to/api/articles?tag=python&per_page=3", seen[0])
        seen.clear()
        collect.collect_dev(3, ["https://dev.to/alice"], opener)
        self.assertEqual("https://dev.to/api/articles?username=alice&per_page=3", seen[0])


if __name__ == "__main__":
    unittest.main()
