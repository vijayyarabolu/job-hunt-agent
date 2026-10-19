"""discover_cc: cluster.idx binary search + block selection, strict token extraction, cached blocks (no network)."""
import gzip
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from job_hunt_agent import discover_cc as cc


def idx(key: str, shard: str = "cdx-00001.gz", offset: int = 0, length: int = 10) -> str:
    return f"{key} 20260901000000\t{shard}\t{offset}\t{length}\t1"


class TokenTest(unittest.TestCase):
    def test_greenhouse_hosts_and_embed_query(self):
        self.assertEqual(cc.token_from_url("greenhouse", "https://job-boards.greenhouse.io/Anthropic/jobs/1"),
                         "anthropic")
        self.assertEqual(cc.token_from_url("greenhouse", "https://boards.greenhouse.io/embed/job_board?for=acme&b=1"),
                         "acme")
        self.assertIsNone(cc.token_from_url("greenhouse", "https://boards.greenhouse.io/embed/job_app"))

    def test_rejects_tokens_that_fail_the_strict_pattern(self):
        self.assertIsNone(cc.token_from_url("lever", "https://jobs.lever.co/a%20b/123"))
        self.assertIsNone(cc.token_from_url("lever", "https://jobs.lever.co/" + "x" * 70))
        self.assertIsNone(cc.token_from_url("ashby", "https://jobs.ashbyhq.com/"))
        self.assertEqual(cc.token_from_url("ashby", "https://jobs.ashbyhq.com/openai/abc"), "openai")

    def test_workday_tenant_host_skips_locale(self):
        url = "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/x_JR1"
        self.assertEqual(cc.token_from_url("workday", url), "nvidia/wd5/NVIDIAExternalCareerSite")

    def test_workday_site_host_and_bad_hosts(self):
        url = "https://wd5.myworkdaysite.com/recruiting/acme/Careers/job/1"
        self.assertEqual(cc.token_from_url("workday", url), "acme/wd5/Careers")
        self.assertIsNone(cc.token_from_url("workday", "https://evil.com.wd5.myworkdayjobs.com/x"))
        self.assertIsNone(cc.token_from_url("workday", "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/x"))

    def test_tokens_in_reads_cdx_json(self):
        lines = ['co,lever,jobs)/acme/1 20260901 {"url": "https://jobs.lever.co/acme/1"}', "garbage",
                 'co,lever,jobs)/b 20260901 {not json}']
        self.assertEqual(list(cc.tokens_in("lever", lines)), ["acme"])


class BlockSearchTest(unittest.TestCase):
    def test_parse_idx_line(self):
        key, block = cc.parse_idx_line(idx("co,lever,jobs)/a", "cdx-00002.gz", 5, 7))
        self.assertEqual((key, block), ("co,lever,jobs)/a", cc.Block("cdx-00002.gz", 5, 7)))
        self.assertIsNone(cc.parse_idx_line("half a line"))

    def test_select_blocks_includes_the_block_before_the_prefix(self):
        lines = [idx("co,laser)/", offset=1), idx("co,lever,jobs)/a", offset=2), idx("co,lever,jobs)/m", offset=3),
                 idx("co,lexus)/", offset=4)]
        found, previous, done = cc.select_blocks(lines, "co,lever,jobs)/", None)
        self.assertEqual([b.offset for b in found], [1, 2, 3])
        self.assertTrue(done)
        self.assertIsNone(previous)

    def test_prefix_inside_one_block(self):
        found, _, done = cc.select_blocks([idx("co,a)/", offset=1), idx("co,z)/", offset=2)], "co,lever,jobs)/", None)
        self.assertEqual([b.offset for b in found], [1])
        self.assertTrue(done)

    def test_find_start_binary_search(self):
        keys = [f"k{i:05d}" for i in range(0, 100000, 10)]
        line_len = len(idx(keys[0])) + 1
        size = line_len * len(keys)

        def probe(offset):
            i = 0 if offset == 0 else offset // line_len + 1
            return idx(keys[i]) if i < len(keys) else None
        start = cc.find_start("k50000", size, probe)
        self.assertLessEqual(start, 5000 * line_len)
        self.assertGreater(start, 5000 * line_len - cc.SCAN_BYTES)


class BlockCacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(cc, "cache_dir", return_value=Path(self.tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_complete_block_is_read_from_cache_without_network(self):
        raw = gzip.compress(b"line1\nline2\n")
        block = cc.Block("cdx-00009.gz", 100, len(raw))
        with mock.patch.object(cc, "get_range", return_value=raw) as fetched:
            self.assertEqual(cc.read_block("CC-X", block), ["line1", "line2"])
            self.assertEqual(cc.read_block("CC-X", block), ["line1", "line2"])
        fetched.assert_called_once()

    def test_truncated_block_raises_and_is_not_cached(self):
        raw = gzip.compress(b"line1\n" * 100)
        block = cc.Block("cdx-00009.gz", 0, len(raw))
        with mock.patch.object(cc, "get_range", return_value=raw[:-8]):
            with self.assertRaises((EOFError, OSError)):
                cc.read_block("CC-X", block)
        self.assertFalse(list(Path(self.tmp.name).rglob("*.gz")))

    def test_harvest_unions_crawls(self):
        rows = [f'co,lever,jobs)/{t}/1 2026 {json.dumps({"url": f"https://jobs.lever.co/{t}/1"})}' for t in ("a", "b")]
        with mock.patch.object(cc, "idx_size", return_value=10), \
                mock.patch.object(cc, "blocks_for", return_value=[cc.Block("s", 0, 1)]), \
                mock.patch.object(cc, "read_block", side_effect=[rows, rows[:1]]):
            out = cc.harvest(("C1", "C2"), ("lever",))
        self.assertEqual(out["lever"], {"a": ["C1", "C2"], "b": ["C1"]})


if __name__ == "__main__":
    unittest.main()
