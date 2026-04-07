"""混合检索公共算子：RRF 贡献分等（无 I/O）。"""


def rrf_score(rank: int, k: int = 60) -> float:
    """RRF（Reciprocal Rank Fusion）单项贡献：rank 从 1 开始。"""
    return 1.0 / (k + rank)
