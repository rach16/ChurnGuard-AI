"""
The assistant (4.6, ADR-0016).

A supervisor routes a question to up to three tool-calling agents; their tools
read the served snapshot, so the assistant can only quote what the site shows;
an answer checker requires every number to come from a cited tool result.

    tools.py        the six read-only, tenant-scoped tools and their schemas
    notes_index.py  the versioned notes-and-cases index (Qdrant, alias swap)
    privacy.py      what is removed before anything reaches a model
"""
