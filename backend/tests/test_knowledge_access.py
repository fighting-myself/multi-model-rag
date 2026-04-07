"""知识库 ID 归属过滤（改造 E-2）：纯函数可离线测；完整 sanitize 依赖 DB 栈。"""
import unittest

from app.services.knowledge_access import unique_positive_kb_ids


class TestUniquePositiveKbIds(unittest.TestCase):
    def test_dedup_and_positive(self):
        self.assertEqual(unique_positive_kb_ids([3, 3, 1, -1, 0, 2]), [3, 1, 2])

    def test_empty(self):
        self.assertEqual(unique_positive_kb_ids(None), [])
        self.assertEqual(unique_positive_kb_ids([]), [])


if __name__ == "__main__":
    unittest.main()
