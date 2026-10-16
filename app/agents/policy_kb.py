"""Policy knowledge base: retrieval over the company's policy corpus (grounding, design principle D2).

Hybrid retrieval: BM25 keyword scoring, blended with Gemini embedding similarity when the
embedding model is reachable. Every result carries a retrieval_id, source file, page and the
verbatim section text, so findings can cite it and the verifier can check the citation.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from collections import Counter

from ..config import DATA_DIR, settings
from ..schemas import RetrievedSnippet

log = logging.getLogger("operateguard.retrieval")
_TOK = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?")
STOP = set("a an the of to and or in on for by with is are be any at from that this as it its must may shall under".split())


def tokenize(s: str) -> list[str]:
    return [t for t in _TOK.findall(s.lower()) if t not in STOP]


class PolicyIndex:
    def __init__(self, corpus: list[dict]) -> None:
        self.docs = corpus
        self.toks = [tokenize(d["heading"] + " " + d["heading"] + " " + d["text"]) for d in corpus]
        self.df = Counter(t for toks in self.toks for t in set(toks))
        self.avgdl = sum(len(t) for t in self.toks) / max(1, len(self.toks))
        self.vectors: list[list[float]] | None = None
        self._embed_lock = asyncio.Lock()
        self._embed_failed = False

    @classmethod
    def load(cls) -> "PolicyIndex":
        return cls(json.loads((DATA_DIR / "policies" / "corpus.json").read_text(encoding="utf-8")))

    def _bm25(self, query: str, k1: float = 1.4, b: float = 0.75) -> list[float]:
        q = tokenize(query)
        n = len(self.docs)
        scores = []
        for toks in self.toks:
            tf = Counter(toks)
            s = 0.0
            for t in q:
                if t not in tf:
                    continue
                idf = math.log(1 + (n - self.df[t] + 0.5) / (self.df[t] + 0.5))
                s += idf * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * len(toks) / self.avgdl))
            scores.append(s)
        return scores

    async def _ensure_vectors(self, llm) -> bool:
        if not settings.use_embeddings or self._embed_failed or llm is None:
            return False
        if self.vectors is not None:
            return True
        async with self._embed_lock:
            if self.vectors is None and not self._embed_failed:
                try:
                    texts = [f"{d['policy_title']} {d['section']} {d['heading']}: {d['text']}" for d in self.docs]
                    self.vectors = await llm.embed(texts, "RETRIEVAL_DOCUMENT")
                except Exception as e:  # keyword retrieval still works
                    log.warning("embedding index unavailable, using BM25 only: %s", e)
                    self._embed_failed = True
        return self.vectors is not None

    async def search(self, query: str, llm=None, k: int = 3) -> tuple[list[RetrievedSnippet], str]:
        bm = self._bm25(query)
        mx = max(bm) or 1.0
        scores = [s / mx for s in bm]
        retriever = "bm25"
        if await self._ensure_vectors(llm):
            try:
                qv = (await llm.embed([query], "RETRIEVAL_QUERY"))[0]
                cos = [_cos(qv, v) for v in self.vectors]
                scores = [0.5 * a + 0.5 * c for a, c in zip(scores, cos)]
                retriever = f"hybrid(bm25+{settings.embedding_model})"
            except Exception as e:
                log.warning("query embedding failed, BM25 only: %s", e)
        ranked = sorted(range(len(self.docs)), key=lambda i: scores[i], reverse=True)[:k]
        out = []
        for i in ranked:
            if bm[i] <= 0 and retriever == "bm25":
                continue
            d = self.docs[i]
            out.append(RetrievedSnippet(retrieval_id=d["retrieval_id"], source_file=d["source_file"],
                                        policy_title=d["policy_title"], page=d["page"], section=d["section"],
                                        heading=d["heading"], snippet=d["text"], score=round(scores[i], 4), owner=d["owner"]))
        return out, retriever


def _cos(a: list[float], b: list[float]) -> float:
    num = sum(x * y for x, y in zip(a, b))
    den = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return num / den if den else 0.0


_index: PolicyIndex | None = None


def get_index() -> PolicyIndex:
    global _index
    if _index is None:
        _index = PolicyIndex.load()
    return _index
