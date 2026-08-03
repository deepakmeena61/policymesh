# Async wrapper around Google's gemini-embedding-001 (3072-dim).
# The SDK is synchronous; calls are offloaded to a thread pool via asyncio.to_thread.
import asyncio
import os

from google import genai
from google.genai import types

_MODEL = "gemini-embedding-001"

# Module-level singleton — avoids re-authenticating on every embed call.
_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
    return _client


def _embed_sync(text: str, task_type: str) -> list[float]:
    # task_type matters: RETRIEVAL_QUERY and RETRIEVAL_DOCUMENT use different
    # training objectives, which measurably improves cosine similarity at retrieval time.
    result = _get_client().models.embed_content(
        model=_MODEL,
        contents=text,
        config=types.EmbedContentConfig(task_type=task_type),
    )
    return result.embeddings[0].values


async def embed_query(text: str) -> list[float]:
    # RETRIEVAL_QUERY optimises the vector for matching against indexed document chunks.
    return await asyncio.to_thread(_embed_sync, text, "RETRIEVAL_QUERY")


async def embed_document(text: str) -> list[float]:
    # RETRIEVAL_DOCUMENT optimises the vector for storage and cosine lookup.
    return await asyncio.to_thread(_embed_sync, text, "RETRIEVAL_DOCUMENT")
