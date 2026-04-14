from __future__ import annotations
import re

STOP = {
    "the","a","an","and","or","of","to","in","on","for","with","from","at","by",
    "is","are","was","were","be","been","being","that","this","it","as","after",
    "before","about","into","over","under","during","officials","report","reported",
}


def _tok(s: str) -> set:
    return {t for t in re.findall(r"[A-Za-z0-9]+", (s or "").lower()) if len(t) > 3 and t not in STOP}


def _jaccard(a: str, b: str) -> float:
    ta, tb = _tok(a), _tok(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(1, len(ta | tb))


def cluster_claims(evidence: list[dict], threshold: float = 0.22) -> list[dict]:
    claims = [
        {"claim": c.get("claim",""), "url": e.get("url",""), "title": e.get("title",""),
         "score": e.get("score",0), "source_domain": e.get("source_domain","")}
        for e in evidence for c in e.get("claims",[])
    ]
    used = [False] * len(claims)
    groups: list[list[dict]] = []
    for i, c in enumerate(claims):
        if used[i]:
            continue
        cluster = [c]; used[i] = True
        for j in range(i+1, len(claims)):
            if not used[j] and _jaccard(c["claim"], claims[j]["claim"]) >= threshold:
                cluster.append(claims[j]); used[j] = True
        groups.append(cluster)
    out = []
    for cluster in groups:
        domains = sorted({x["source_domain"] for x in cluster if x["source_domain"]})
        rep = max(cluster, key=lambda x: (x["score"], len(x["claim"])))
        out.append({"representative_claim": rep["claim"], "items": cluster,
                    "source_count": len(cluster), "domain_count": len(domains), "domains": domains})
    out.sort(key=lambda x: (x["domain_count"], x["source_count"]), reverse=True)
    return out
