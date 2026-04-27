"""Claim clustering with improved threshold and length-based early exit (fixes #33-34)."""
from __future__ import annotations
import re, logging
from collections import defaultdict

def _tokens(text: str) -> set[str]:
    return set(re.findall(r'\b[a-z]{4,}\b', text.lower()))

def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)

JACCARD_THRESHOLD = 0.40

def cluster_claims(claims: list[str]) -> list[dict]:
    if not claims:
        return []

    token_sets = [_tokens(c) for c in claims]
    n = len(claims)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        parent[find(x)] = find(y)

    for i in range(n):
        for j in range(i + 1, n):
            li, lj = len(token_sets[i]), len(token_sets[j])
            if li == 0 or lj == 0:
                continue
            if min(li, lj) / max(li, lj) < 0.3:
                continue
            if _jaccard(token_sets[i], token_sets[j]) >= JACCARD_THRESHOLD:
                union(i, j)

    groups = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)

    result = []
    for root, members in sorted(groups.items(), key=lambda x: -len(x[1])):
        representative = claims[members[0]]
        result.append({
            "representative_claim": representative,
            "all_claims": [claims[m] for m in members],
            "source_count": len(members),
        })
    return result
