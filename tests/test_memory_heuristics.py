import unittest

from app.core.memory_heuristics import MemoryHeuristicEngine


class MemoryHeuristicEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = MemoryHeuristicEngine()

    def test_detects_unbounded_accumulation(self) -> None:
        content = (
            "def collect(rows):\n"
            "    results = []\n"
            "    for row in rows:\n"
            "        results.append(process(row))\n"
            "    return results\n"
        )
        findings = self.engine.scan_chunk(content)
        rule_ids = {f.rule_id for f in findings}
        self.assertIn("MEM-UNBOUNDED-ACCUMULATION", rule_ids)

    def test_capped_accumulation_not_flagged(self) -> None:
        content = (
            "def collect(rows):\n"
            "    results = []\n"
            "    for row in rows:\n"
            "        if len(results) >= 100:\n"
            "            break\n"
            "        results.append(process(row))\n"
            "    return results\n"
        )
        findings = self.engine.scan_chunk(content)
        rule_ids = {f.rule_id for f in findings}
        self.assertNotIn("MEM-UNBOUNDED-ACCUMULATION", rule_ids)

    def test_detects_string_concat_in_loop(self) -> None:
        content = (
            "def build(parts):\n"
            "    out = ''\n"
            "    for p in parts:\n"
            "        out += p\n"
            "    return out\n"
        )
        findings = self.engine.scan_chunk(content)
        rule_ids = {f.rule_id for f in findings}
        self.assertIn("MEM-STRING-CONCAT-LOOP", rule_ids)

    def test_detects_full_file_read(self) -> None:
        content = (
            "def load(path):\n"
            "    with open(path) as f:\n"
            "        return f.read()\n"
        )
        findings = self.engine.scan_chunk(content)
        rule_ids = {f.rule_id for f in findings}
        self.assertIn("MEM-FULL-FILE-LOAD", rule_ids)

    def test_detects_pandas_full_load(self) -> None:
        content = "df = pd.read_csv('huge_file.csv')\n"
        findings = self.engine.scan_chunk(content)
        rule_ids = {f.rule_id for f in findings}
        self.assertIn("MEM-PANDAS-FULL-LOAD", rule_ids)

    def test_detects_global_unbounded_cache(self) -> None:
        content = "_CACHE = {}\n\ndef get(key):\n    return _CACHE[key]\n"
        findings = self.engine.scan_chunk(content)
        rule_ids = {f.rule_id for f in findings}
        self.assertIn("MEM-GLOBAL-UNBOUNDED-CACHE", rule_ids)

    def test_detects_no_pagination_function(self) -> None:
        content = "def get_all_users():\n    return db.query(User).all()\n"
        findings = self.engine.scan_chunk(content)
        rule_ids = {f.rule_id for f in findings}
        self.assertIn("MEM-NO-PAGINATION", rule_ids)

    def test_clean_code_produces_no_findings(self) -> None:
        content = (
            "def add(a, b):\n"
            "    return a + b\n"
        )
        findings = self.engine.scan_chunk(content)
        self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
