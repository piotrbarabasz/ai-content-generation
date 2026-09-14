"""Reuse D012's sentence identity for planning, independent of technical limits."""

from dataclasses import replace

from .chunking import sentence_chunks


def sentence_sources(text):
    spans, seen = [], set()
    for chunk in sentence_chunks(text):
        source = chunk.source_span
        if source.sentence_id not in seen:
            spans.append(replace(source, start=source.sentence_start, end=source.sentence_end))
            seen.add(source.sentence_id)
    return tuple(spans)
