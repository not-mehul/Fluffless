"""Fluffless — point it at a folder of media, find the repeated bits
(ads, intros, outros), catalogue them, and trim them out.

The detection engine (``repetition``) is fingerprint-agnostic; audio comes from
Chromaprint and video from perceptual frame hashing, but the matching,
recurrence, and timestamp recovery are shared.
"""

__version__ = "1.0.0"
