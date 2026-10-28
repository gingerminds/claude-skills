import gzip
import unittest

from cqa import crawl_budget as budget

ORIGIN = "https://preprod.example.org"
PROD = "https://www.example.org"


class ProposalTest(unittest.TestCase):
    def test_proposal_of_the_design_example(self):
        self.assertEqual(60, budget.propose_crawl_minutes(850, 0.9))
        self.assertEqual(4000, budget.crawl_max_urls(60))

    def test_proposal_bounds_and_rounding(self):
        self.assertEqual(30, budget.propose_crawl_minutes(None, 1.0))
        self.assertEqual(30, budget.propose_crawl_minutes(0, 1.0))
        self.assertEqual(30, budget.propose_crawl_minutes(10, 1.0))
        self.assertEqual(120, budget.propose_crawl_minutes(5000, 1.0))
        self.assertEqual(30, budget.propose_crawl_minutes(850, 0.1))
        self.assertEqual(105, budget.propose_crawl_minutes(1500, 1.0, rate=1.0))
        self.assertEqual(120, budget.propose_crawl_minutes(1501, 1.0, rate=1.0))
        self.assertEqual(120, budget.propose_crawl_minutes(850, None, rate=0.5))

    def test_max_urls_follow_the_duration(self):
        expected = {5: 500, 30: 2000, 45: 3000, 120: 8000, 240: 8000}
        for minutes, urls in expected.items():
            self.assertEqual(urls, budget.crawl_max_urls(minutes), minutes)


class Site:
    def __init__(self, pages):
        self.pages, self.urls = pages, []

    def __call__(self, url):
        self.urls.append(url)
        body = self.pages.get(url)
        return (200, body, 0.1) if body is not None else (404, b"", 0.1)


def urlset(*locations):
    return ("<urlset>" + "".join(f"<url><loc>{item}</loc></url>" for item in locations) + "</urlset>").encode()


def index(*locations):
    return ("<sitemapindex>" + "".join(f"<sitemap><loc>{item}</loc></sitemap>" for item in locations) + "</sitemapindex>").encode()


class SitemapTest(unittest.TestCase):
    def count(self, pages, **kwargs):
        site = Site(pages)
        result = budget.count_sitemap_urls(ORIGIN, site, PROD, sleep=lambda _: None, **kwargs)
        return result, site

    def test_no_sitemap_gives_none(self):
        self.assertIsNone(self.count({})[0])

    def test_an_empty_sitemap_gives_zero(self):
        self.assertEqual(0, self.count({ORIGIN + "/sitemap.xml": urlset()})[0])

    def test_urls_are_counted_once_and_other_hosts_ignored(self):
        pages = {
            ORIGIN + "/sitemap.xml": urlset(
                ORIGIN + "/a", PROD + "/a", PROD + "/b?x=1", PROD + "/b?x=2", "https://other.example.net/z"
            )
        }
        self.assertEqual(3, self.count(pages)[0])

    def test_robots_seeds_and_prod_addresses_are_read_on_the_target(self):
        pages = {
            ORIGIN + "/robots.txt": f"Sitemap: {PROD}/map.xml\n".encode(),
            ORIGIN + "/map.xml": urlset(PROD + "/a"),
        }
        result, site = self.count(pages)
        self.assertEqual(1, result)
        self.assertTrue(all(url.startswith(ORIGIN) for url in site.urls), site.urls)

    def test_indexes_are_followed_to_depth_two_and_gz_is_unpacked(self):
        pages = {
            ORIGIN + "/sitemap.xml": index(PROD + "/l1.xml"),
            ORIGIN + "/l1.xml": index(PROD + "/l2.xml.gz"),
            ORIGIN + "/l2.xml.gz": gzip.compress(urlset(PROD + "/a", PROD + "/b") + b""),
        }
        self.assertEqual(2, self.count(pages)[0])
        deeper = dict(pages)
        deeper[ORIGIN + "/l2.xml.gz"] = gzip.compress(index(PROD + "/l3.xml"))
        deeper[ORIGIN + "/l3.xml"] = urlset(PROD + "/c")
        self.assertEqual(0, self.count(deeper)[0])

    def test_files_are_limited(self):
        names = [f"{ORIGIN}/m{number}.xml" for number in range(80)]
        pages = {ORIGIN + "/sitemap.xml": index(*names)}
        pages.update({name: urlset(name + "/p") for name in names})
        result, site = self.count(pages)
        self.assertEqual(49, result)
        self.assertEqual(50, len([u for u in site.urls if u.endswith(".xml")]))

    def test_an_oversized_file_is_ignored(self):
        big = b"<urlset>" + b" " * (budget.MAX_SITEMAP_BYTES + 1) + b"</urlset>"
        self.assertEqual(0, self.count({ORIGIN + "/sitemap.xml": big})[0])


class ResponseTimeTest(unittest.TestCase):
    def test_mean_of_the_answers_below_400(self):
        answers = {"a": (200, b"", 1.0), "b": (404, b"", 9.0), "c": (301, b"", 2.0), "d": (None, b"", 5.0)}
        waits = []
        mean = budget.mean_response_time(list(answers), lambda url: answers[url], sleep=waits.append)
        self.assertEqual(1.5, mean)
        self.assertEqual([0.5, 0.5, 0.5], waits)

    def test_none_without_an_answer(self):
        self.assertIsNone(budget.mean_response_time(["a"], lambda url: (500, b"", 1.0), sleep=lambda _: None))
        self.assertIsNone(budget.mean_response_time([], lambda url: (200, b"", 1.0)))


if __name__ == "__main__":
    unittest.main()
