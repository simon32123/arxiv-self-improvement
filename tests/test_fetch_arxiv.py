import tempfile
import unittest
import urllib.parse
from pathlib import Path

import fetch_arxiv


SAMPLE_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2607.12345v2</id>
    <updated>2026-07-16T10:00:00Z</updated>
    <published>2026-07-15T09:00:00Z</published>
    <title>  A Self-Improving Agent\n  with Reliable Feedback </title>
    <summary>We introduce an agent that improves\n its own workflow.</summary>
    <author><name>Alice Example</name></author>
    <author><name>Bob Example</name></author>
    <category term="cs.AI" />
    <category term="cs.LG" />
    <arxiv:primary_category term="cs.AI" />
    <arxiv:comment>12 pages</arxiv:comment>
    <link href="http://arxiv.org/abs/2607.12345v2" rel="alternate" type="text/html" />
    <link href="http://arxiv.org/pdf/2607.12345v2" rel="related" type="application/pdf" />
  </entry>
</feed>
"""

SAMPLE_OPENREVIEW = {
    "count": 1,
    "notes": [
        {
            "id": "openreview-test-123",
            "forum": "openreview-test-123",
            "cdate": 1735689600000,
            "tmdate": 1735776000000,
            "invitations": ["ICLR.cc/2025/Conference/-/Submission"],
            "content": {
                "title": {"value": "A Self-Improving Agent with Reliable Feedback"},
                "abstract": {"value": "An agent uses self-reflection to improve its own workflow."},
                "authors": {"value": ["Alice Example", "Bob Example"]},
                "venue": {"value": "ICLR 2025 poster"},
                "venueid": {"value": "ICLR.cc/2025/Conference"},
            },
        }
    ],
}


class AtomParsingTests(unittest.TestCase):
    def test_default_query_limits_every_topic_term_to_title_and_abstract(self):
        self.assertNotIn("all:", fetch_arxiv.DEFAULT_QUERY)
        for term in fetch_arxiv.TOPIC_TERMS:
            self.assertIn(f'ti:"{term}"', fetch_arxiv.DEFAULT_QUERY)
            self.assertIn(f'abs:"{term}"', fetch_arxiv.DEFAULT_QUERY)
        for term in fetch_arxiv.AGENT_TERMS:
            self.assertIn(f'ti:"{term}"', fetch_arxiv.DEFAULT_QUERY)
            self.assertIn(f'abs:"{term}"', fetch_arxiv.DEFAULT_QUERY)

    def test_openreview_batches_cover_every_topic_and_agent_term(self):
        combined = " ".join(fetch_arxiv.OPENREVIEW_QUERY_BATCHES)
        for term in fetch_arxiv.TOPIC_TERMS:
            self.assertIn(f'"{term}"', combined)
        for query in fetch_arxiv.OPENREVIEW_QUERY_BATCHES:
            for term in fetch_arxiv.AGENT_TERMS:
                self.assertIn(f'"{term}"', query)

    def test_parse_atom_extracts_metadata(self):
        papers = fetch_arxiv.parse_atom(SAMPLE_FEED, "2026-07-17")
        self.assertEqual(len(papers), 1)
        paper = papers[0]
        self.assertEqual(paper["id"], "2607.12345")
        self.assertEqual(paper["versioned_id"], "2607.12345v2")
        self.assertEqual(paper["title"], "A Self-Improving Agent with Reliable Feedback")
        self.assertEqual(paper["authors"], ["Alice Example", "Bob Example"])
        self.assertEqual(paper["primary_category"], "cs.AI")
        self.assertEqual(paper["categories"], ["cs.AI", "cs.LG"])
        self.assertTrue(paper["pdf_url"].startswith("https://"))

    def test_parse_openreview_extracts_submission_metadata(self):
        papers = fetch_arxiv.parse_openreview_notes(SAMPLE_OPENREVIEW, "2026-07-19")
        self.assertEqual(len(papers), 1)
        paper = papers[0]
        self.assertEqual(paper["id"], "openreview:openreview-test-123")
        self.assertEqual(paper["source"], "OpenReview")
        self.assertEqual(paper["authors"], ["Alice Example", "Bob Example"])
        self.assertEqual(paper["venue"], "ICLR 2025 poster")
        self.assertEqual(paper["published"][:10], "2025-01-01")
        self.assertEqual(paper["openreview_url"], "https://openreview.net/forum?id=openreview-test-123")

    def test_parse_openreview_excludes_dblp_records(self):
        payload = {"notes": [dict(SAMPLE_OPENREVIEW["notes"][0], invitations=["DBLP.org/-/record"])]}
        self.assertEqual(fetch_arxiv.parse_openreview_notes(payload), [])

    def test_cross_source_deduplication_prefers_arxiv_and_keeps_links(self):
        arxiv_paper = fetch_arxiv.parse_atom(SAMPLE_FEED, "2026-07-17")[0]
        openreview_paper = fetch_arxiv.parse_openreview_notes(SAMPLE_OPENREVIEW, "2026-07-19")[0]
        papers = fetch_arxiv.deduplicate_papers_by_title([openreview_paper, arxiv_paper])
        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0]["source"], "arXiv")
        self.assertEqual(papers[0]["sources"], ["arXiv", "OpenReview"])
        self.assertEqual(
            papers[0]["openreview_url"],
            "https://openreview.net/forum?id=openreview-test-123",
        )

    def test_openreview_url_requests_forum_notes_and_title_abstract_candidates(self):
        url = fetch_arxiv.build_openreview_url("self-improvement AND agent", 200, 20)
        query = urllib.parse.urlparse(url).query
        params = urllib.parse.parse_qs(query)
        self.assertEqual(params["source"], ["forum"])
        self.assertEqual(params["content"], ["all"])
        self.assertEqual(params["offset"], ["20"])

    def test_merge_replaces_new_version_but_preserves_discovery_date(self):
        old = fetch_arxiv.parse_atom(SAMPLE_FEED, "2026-07-15")[0]
        new = dict(old, versioned_id="2607.12345v3", updated="2026-07-17T09:00:00Z")
        papers, new_count = fetch_arxiv.merge_papers([old], [new])
        self.assertEqual(new_count, 0)
        self.assertEqual(papers[0]["versioned_id"], "2607.12345v3")
        self.assertEqual(papers[0]["discovered_on"], "2026-07-15")

    def test_merge_counts_truly_new_paper(self):
        paper = fetch_arxiv.parse_atom(SAMPLE_FEED, "2026-07-17")[0]
        papers, new_count = fetch_arxiv.merge_papers([], [paper])
        self.assertEqual(new_count, 1)
        self.assertEqual(len(papers), 1)

    def test_agent_topic_match_uses_only_title_and_abstract(self):
        paper = {
            "title": "A General Agent Study",
            "abstract": "We study reliable planning.",
            "authors": ["Self-Improvement Research Group"],
            "categories": ["agent evolution"],
            "comment": "iterative refinement",
        }
        self.assertFalse(fetch_arxiv.matches_agent_self_improvement(paper))
        paper["abstract"] = "We introduce autonomous improvement for agents."
        self.assertTrue(fetch_arxiv.matches_agent_self_improvement(paper))

    def test_topic_match_requires_agent_context(self):
        paper = {
            "title": "Iterative Refinement for Language Models",
            "abstract": "A general self-improvement training method.",
        }
        self.assertFalse(fetch_arxiv.matches_agent_self_improvement(paper))
        paper["title"] = "Iterative Refinement for Agentic Language Models"
        self.assertTrue(fetch_arxiv.matches_agent_self_improvement(paper))

    def test_relevance_prioritizes_title_matches(self):
        title_match = {
            "title": "Self-Improving Agent with Reliable Reflection",
            "abstract": "We evaluate the system.",
            "primary_category": "cs.AI",
        }
        abstract_match = {
            "title": "A General Language Model Study",
            "abstract": "We evaluate self-improvement methods for an agent.",
            "primary_category": "cs.CL",
        }
        title_score, title_reasons = fetch_arxiv.calculate_relevance(title_match)
        abstract_score, _ = fetch_arxiv.calculate_relevance(abstract_match)
        self.assertGreater(title_score, abstract_score)
        self.assertLessEqual(title_score, 100)
        self.assertIn("标题命中自进化关键词", title_reasons)
        self.assertIn("标题命中 Agent 关键词", title_reasons)

    def test_relevance_is_explainable_and_bounded(self):
        paper = {
            "title": "Agent Evolution through Self-Improvement and Self-Reflection",
            "abstract": "An agentic multi-agent system performs iterative refinement.",
            "primary_category": "cs.MA",
        }
        score, reasons = fetch_arxiv.calculate_relevance(paper)
        self.assertEqual(score, 100)
        self.assertGreaterEqual(len(reasons), 5)


class SiteBuildTests(unittest.TestCase):
    def test_build_site_writes_file_protocol_compatible_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "public"
            database = {"papers": [], "last_checked": "2026-07-17T01:00:00+00:00"}
            fetch_arxiv.build_site(database, output, fetch_arxiv.ROOT / "src")
            self.assertTrue((output / "index.html").exists())
            self.assertTrue((output / "styles.css").exists())
            self.assertTrue((output / "app.js").exists())
            self.assertTrue((output / "papers-data.js").read_text().startswith("window.PAPERS_DATA="))


if __name__ == "__main__":
    unittest.main()
