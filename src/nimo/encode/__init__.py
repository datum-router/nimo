"""Screenshot encoding for vision backends (CARBON annotated bounding boxes).

The annotator draws numbered bounding boxes over detected UI elements so a
vision LLM can reference them by number — fewer, denser calls than raw
screenshots. Pillow-backed; imported lazily so the null/CI path needs no
image deps.
"""
__all__: list[str] = []
