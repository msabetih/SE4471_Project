"""
RAG (Retrieval-Augmented Generation) module.
Embeds the travel corpus into a ChromaDB vector store on startup,
then exposes a retrieve() function for semantic search over the documents.
"""

import os
import glob
import hashlib
from pathlib import Path
from typing import Optional

import chromadb
from chromadb.utils import embedding_functions

CORPUS_DIR = Path(__file__).parent / "corpus"
CHROMA_PERSIST_DIR = Path(__file__).parent / ".chromadb"
COLLECTION_NAME = "travel_corpus"
CHUNK_SIZE = 400 # Words per chunk
CHUNK_OVERLAP = 50  # Words of overlap between chunks
EMBED_MODEL = "all-MiniLM-L6-v2"   # Sentence-transformer

# ChromaDB client & collection
_client: Optional[chromadb.PersistentClient] = None
_collection = None

def _get_collection():
    """Initialise the ChromaDB client and return the collection."""
    global _client, _collection
    if _collection is not None:
        return _collection

    CHROMA_PERSIST_DIR.mkdir(parents=True, exist_ok=True)

    _client = chromadb.PersistentClient(path=str(CHROMA_PERSIST_DIR))

    embed_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name=EMBED_MODEL
    )

    _collection = _client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=embed_fn,
        metadata={"hnsw:space": "cosine"},
    )

    return _collection

# Text chunking
def _chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """
    Split *text* into overlapping word-based chunks.
    Returns a list of chunk strings.
    """
    words = text.split()
    chunks = []
    start = 0
    while start < len(words):
        end = min(start + chunk_size, len(words))
        chunk = " ".join(words[start:end])
        chunks.append(chunk)
        if end == len(words):
            break
        start += chunk_size - overlap
    return chunks

def _doc_id(source_file: str, chunk_index: int) -> str:
    """Generate a stable, unique ID for a chunk."""
    raw = f"{source_file}::chunk{chunk_index}"
    return hashlib.md5(raw.encode()).hexdigest()

# Corpus embedding
def embed_corpus(force_reload: bool = False) -> int:
    """
    Read all .md files from CORPUS_DIR, chunk them, and upsert into ChromaDB.
    Idempotent — already-embedded chunks are skipped unless force_reload=True.
    Returns the total number of chunks in the collection after embedding.
    """
    collection = _get_collection()

    if force_reload:
        # Delete and recreate the collection
        global _collection
        _client.delete_collection(COLLECTION_NAME)
        _collection = None
        collection = _get_collection()

    md_files = sorted(glob.glob(str(CORPUS_DIR / "*.md")))
    if not md_files:
        raise FileNotFoundError(f"No .md files found in {CORPUS_DIR}")

    all_ids: list[str] = []
    all_docs: list[str] = []
    all_metas: list[dict] = []

    for filepath in md_files:
        filename = os.path.basename(filepath)
        with open(filepath, "r", encoding="utf-8") as f:
            text = f.read()

        # Extract a title from the first heading, fall back to filename
        title = filename
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("# "):
                title = stripped[2:].strip()
                break

        chunks = _chunk_text(text)

        for i, chunk in enumerate(chunks):
            doc_id = _doc_id(filename, i)
            all_ids.append(doc_id)
            all_docs.append(chunk)
            all_metas.append({
                "source": filename,
                "title": title,
                "chunk_index": i,
                "total_chunks": len(chunks),
            })

    # Upsert in batches of 100 
    batch_size = 100
    for i in range(0, len(all_ids), batch_size):
        collection.upsert(
            ids=all_ids[i : i + batch_size],
            documents=all_docs[i : i + batch_size],
            metadatas=all_metas[i : i + batch_size],
        )

    total = collection.count()
    print(f"[RAG] Corpus embedded: {len(md_files)} files → {total} chunks in ChromaDB.")
    return total

# Retrieval
def retrieve(query: str, top_k: int = 3) -> list[dict]:
    """
    Semantic search over the embedded corpus.
    Parameters
    ----------
    query : str
        The natural-language query (e.g. "budget travel tips for Tokyo").
    top_k : int
        Number of top results to return (default 3).

    Returns
    -------
    list of dicts, each with keys:
        - "text"       : the chunk text
        - "source"     : filename of the originating document
        - "title"      : document title
        - "chunk_index": position of chunk within the document
        - "distance"   : cosine distance (lower = more similar)
    """
    collection = _get_collection()

    if collection.count() == 0:
        # Autoembed if collection is empty
        embed_corpus()

    results = collection.query(
        query_texts=[query],
        n_results=min(top_k, collection.count()),
        include=["documents", "metadatas", "distances"],
    )

    output = []
    docs = results["documents"][0]
    metas = results["metadatas"][0]
    distances = results["distances"][0]

    for doc, meta, dist in zip(docs, metas, distances):
        output.append({
            "text": doc,
            "source": meta.get("source", "unknown"),
            "title": meta.get("title", "unknown"),
            "chunk_index": meta.get("chunk_index", 0),
            "distance": round(dist, 4),
        })

    return output


def format_retrieved_context(results: list[dict]) -> str:
    """
    Format retrieved chunks into a single context string suitable for
    injection into an LLM prompt.
    """
    if not results:
        return "No relevant travel information found."

    parts = []
    for i, r in enumerate(results, start=1):
        parts.append(
            f"[Source {i}: {r['title']}]\n{r['text']}"
        )
    return "\n\n---\n\n".join(parts)

# CLI test harness
if __name__ == "__main__":
    import sys

    print("Embedding corpus...")
    total = embed_corpus()
    print(f"Total chunks: {total}\n")

    test_queries = [
        "best time to visit Japan cherry blossom",
        "budget travel tips Southeast Asia",
        "getting around Paris by train",
        "things to do in Machu Picchu",
        "visa requirements for visiting Australia",
        "beach destinations Mediterranean Europe",
        "altitude sickness Cusco Peru",
        "tipping culture different countries",
    ]

    if len(sys.argv) > 1:
        test_queries = [" ".join(sys.argv[1:])]

    for query in test_queries:
        print(f"Query: {query!r}")
        results = retrieve(query, top_k=3)
        for r in results:
            print(f"  [{r['distance']:.4f}] {r['title']} (chunk {r['chunk_index']})")
            print(f"    {r['text'][:120]}...")
        print()
