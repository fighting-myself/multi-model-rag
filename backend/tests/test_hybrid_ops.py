"""混合检索公共算子单测（改造 C-3）。"""
import unittest

from app.infrastructure.rag.hybrid_ops import rrf_score


class TestHybridOps(unittest.TestCase):
    def test_rrf_score_rank1(self):
        self.assertAlmostEqual(rrf_score(1, k=60), 1.0 / 61.0)

    def test_rrf_score_higher_rank_lower_score(self):
        self.assertGreater(rrf_score(1, 60), rrf_score(5, 60))


if __name__ == "__main__":
    unittest.main()
