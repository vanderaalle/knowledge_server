"""
Qdrant vector database client.
Handles collection management, upsert, search, and scroll operations.
"""

import os
from typing import List, Dict, Any, Set

import ollama
from qdrant_client import QdrantClient
from qdrant_client.models import VectorParams, Distance, PointStruct
from qdrant_client.http import models

OLLAMA_MODEL = os.getenv('OLLAMA_MODEL', 'mxbai-embed-large')
QDRANT_HOST = os.getenv('QDRANT_HOST', 'localhost')
QDRANT_PORT = int(os.getenv('QDRANT_PORT', '6333'))
COLLECTION_NAME = os.getenv('COLLECTION_NAME', 'pdf_library')


class VectorDBClient:
    """Client Qdrant - Singleton-ish"""

    def __init__(self, host: str = QDRANT_HOST, port: int = QDRANT_PORT):
        self.client = QdrantClient(host=host, port=port)
        self._ensure_collection_exists()

    def _ensure_collection_exists(self):
        try:
            collections = self.client.get_collections().collections
            collection_names = [c.name for c in collections]
            if COLLECTION_NAME not in collection_names:
                try:
                    test_embedding = ollama.embeddings(model=OLLAMA_MODEL, prompt="test")["embedding"]
                    vector_size = len(test_embedding)
                    self.client.create_collection(
                        collection_name=COLLECTION_NAME,
                        vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE)
                    )
                except Exception:
                    pass
        except Exception:
            pass

    def upsert_points(self, points: List[PointStruct]):
        try:
            self.client.upsert(collection_name=COLLECTION_NAME, points=points)
        except Exception:
            pass

    def get_all_indexed_hashes(self) -> Set[str]:
        hashes = set()
        try:
            offset = None
            while True:
                result, next_offset = self.client.scroll(
                    collection_name=COLLECTION_NAME,
                    limit=1000,
                    offset=offset,
                    with_payload=["metadata.file_hash"],
                    with_vectors=False
                )
                for point in result:
                    h = (point.payload or {}).get("metadata", {}).get("file_hash")
                    if h:
                        hashes.add(h)
                if next_offset is None:
                    break
                offset = next_offset
        except:
            pass
        return hashes

    def is_file_indexed(self, file_hash: str) -> bool:
        try:
            result = self.client.scroll(
                collection_name=COLLECTION_NAME,
                scroll_filter=models.Filter(
                    must=[models.FieldCondition(
                        key="metadata.file_hash",
                        match=models.MatchValue(value=file_hash)
                    )]
                ),
                limit=1,
                with_payload=False,
                with_vectors=False
            )
            return len(result[0]) > 0
        except:
            return False

    def search(self, query_vector: List[float], limit: int = 5) -> List[Dict[str, Any]]:
        try:
            search_result = self.client.search(
                collection_name=COLLECTION_NAME,
                query_vector=query_vector,
                limit=limit
            )
            return [
                {
                    "text": r.payload.get("text", ""),
                    "metadata": r.payload.get("metadata", {}),
                    "score": r.score
                }
                for r in search_result
            ]
        except Exception:
            return []

    def get_chunks_by_file(self, file_hash: str) -> List[Dict[str, Any]]:
        try:
            all_chunks = []
            offset = None
            while True:
                result = self.client.scroll(
                    collection_name=COLLECTION_NAME,
                    scroll_filter=models.Filter(
                        must=[models.FieldCondition(
                            key="metadata.file_hash",
                            match=models.MatchValue(value=file_hash)
                        )]
                    ),
                    limit=100,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False
                )
                chunks, next_offset = result
                for chunk in chunks:
                    all_chunks.append({
                        "text": chunk.payload.get("text", ""),
                        "metadata": chunk.payload.get("metadata", {})
                    })
                if not next_offset:
                    break
                offset = next_offset
            all_chunks.sort(key=lambda x: x["metadata"].get("chunk_index", 0))
            return all_chunks
        except Exception:
            return []

    def get_head_chunks_by_file(self, file_hash: str, limit: int = 5) -> List[Dict[str, Any]]:
        try:
            result, _ = self.client.scroll(
                collection_name=COLLECTION_NAME,
                scroll_filter=models.Filter(
                    must=[models.FieldCondition(
                        key="metadata.file_hash",
                        match=models.MatchValue(value=file_hash)
                    )]
                ),
                limit=limit * 2,
                with_payload=True,
                with_vectors=False
            )
            chunks = [
                {"text": c.payload.get("text", ""), "metadata": c.payload.get("metadata", {})}
                for c in result
            ]
            chunks.sort(key=lambda x: x["metadata"].get("chunk_index", 0))
            return chunks[:limit]
        except Exception:
            return []

    def delete_collection(self):
        try:
            self.client.delete_collection(collection_name=COLLECTION_NAME)
            self._ensure_collection_exists()
            return True
        except Exception:
            return False


# Module-level singleton
_vector_db = None


def get_db() -> VectorDBClient:
    global _vector_db
    if _vector_db is None:
        _vector_db = VectorDBClient()
    return _vector_db
