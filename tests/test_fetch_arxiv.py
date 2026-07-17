import tempfile
import unittest
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


class AtomParsingTests(unittest.TestCase):
    def test_default_query_limits_every_topic_term_to_title_and_abstract(self):
        self.assertNotIn("all:", fetch_arxiv.DEFAULT_QUERY)
        for term in fetch_arxiv.TOPIC_TERMS:
            self.assertIn(f'ti:"{term}"', fetch_arxiv.DEFAULT_QUERY)
            self.assertIn(f'abs:"{term}"', fetch_arxiv.DEFAULT_QUERY)
        for term in fetch_arxiv.AGENT_TERMS:
            self.assertIn(f'ti:"{term}"', fetch_arxiv.DEFAULT_QUERY)
            self.assertIn(f'abs:"{term}"', fetch_arxiv.DEFAULT_QUERY)

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
