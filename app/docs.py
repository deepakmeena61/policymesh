# Vector search over the docs knowledge base + context construction.
# Both callables are used by the MCP tool and the agent's _execute_tool —
# one implementation, two callers, identical behaviour.
from app.db import get_pool
from app.embed import embed_query


async def search_raw(query: str, k: int = 5) -> list[dict]:
    # asyncpg has no native pgvector encoder, so the embedding is passed as a
    # bracket-notation string ('[0.1, 0.2, ...]') with an explicit ::vector cast.
    k = min(max(k, 1), 10)
    embedding = await embed_query(query)
    embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, title, source, content,
                   (1 - (embedding <=> $1::vector))::numeric(4,3) AS score
            FROM   docs
            ORDER  BY embedding <=> $1::vector
            LIMIT  $2
            """,
            embedding_str,
            k,
        )
    return [dict(r) for r in rows]


def build_context(chunks: list[dict]) -> str:
    # Numbered [N] markers here correspond to cite tags the LLM is instructed
    # to use in its answer, making every factual claim traceable to a source.
    if not chunks:
        return "No relevant documents found."

    parts = []
    for i, c in enumerate(chunks, 1):
        parts.append(
            f"[{i}] {c['title']}\n"
            f"Source: {c['source']}  |  Relevance: {float(c['score']):.0%}\n"
            f"{c['content']}"
        )
    return "\n\n---\n\n".join(parts)
