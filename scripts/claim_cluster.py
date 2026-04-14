"""Clusters related claims and flags potential contradictions."""
from __future__ import annotations
import re
from collections import defaultdict

STOP = {
    "the","a","an","and","or","of","to","in","on","for","with","from","at","by",
    "is","are","was","were","be","been","being","that","this","it","as","after",
    "before","about","into","over","under","during","officials","report","reported",
}


def tokenize(s: str) -> set[str]:
    toks = re.findall(r"[A-Za-z0-9']+", (s or "").lower())
    return {t for t in toks if len(t) >= 4 and t not in STOP}


def jaccard(a: str, b: str) -> float:
    ta, tb = tokenize(a), tokenize(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(1, len(ta | tb))


def cluster_claims(evidence: list[dict], threshold: float = 0.22) -> list[dict]:
    claims = []
    for e in evidence:
        for c in e.get("claims", []):
            claims.append({
                "claim":         c.get("claim", ""),
                "url":           e.get("url", ""),
                "title":         e.get("title", ""),
                "score":         e.get("score", 0),
                "source_domain": e.get("source_domain", ""),
            })

    groups = []
    used   = [False] * len(claims)
    for i, c in enumerate(claims):
        if used[i]:
            continue
        cluster = [c]
        used[i] = True
        for j in range(i + 1, len(claims)):
            if not used[j] and jaccard(c["claim"], claims[j]["claim"]) >= threshold:
                cluster.append(claims[j])
                used[j] = True
        groups.append(cluster)

    out = []
    for cluster in groups:
        domains = sorted({x["source_domain"] for x in cluster if x["source_domain"]})
        out.append({
            "representative_claim": max(
                cluster, key=lambda x: (x["score"], len(x["claim"]))
            ).get("claim", ""),
            "items":        cluster,
            "source_count": len(cluster),
            "domain_count": len(domains),
            "domains":      domains,
        })
    out.sort(key=lambda x: (x["domain_count"], x["source_count"]), reverse=True)
    return out


def contradiction_flags(clusters: list[dict]) -> list[dict]:
    flags = []
    markers = [
        ("killed", "injured"),
        ("approved", "blocked"),
        ("won", "lost"),
        ("increase", "decrease"),
        ("guilty", "not guilty"),
    ]
    flat = [c for c in clusters if c.get("representative_claim")]
    for i in range(len(flat)):
        a = flat[i]["representative_claim"].lower()
        for j in range(i + 1, len(flat)):
            b = flat[j]["representative_claim"].lower()
            for x, y in markers:
                if (x in a and y in b) or (y in a and x in b):
                    shared = set(flat[i].get("domains", [])) & set(flat[j].get("domains", []))
                    flags.append({
                        "a": flat[i]["representative_claim"],
                        "b": flat[j]["representative_claim"],
                        "shared_domains": sorted(shared),
                    })
    return flags
