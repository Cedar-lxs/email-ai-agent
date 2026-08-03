"""知识检索器工厂。"""
import os

from email_agent.infrastructure.knowledge.embeddings import OpenAICompatibleEmbeddingClient
from email_agent.infrastructure.knowledge.hybrid import HybridKnowledgeRetriever
from email_agent.infrastructure.knowledge.lexical import LexicalKnowledgeRetriever
from email_agent.infrastructure.knowledge.vector_index import SQLiteVectorIndex
from email_agent.paths import resolve_from_root


def create_retriever(config: dict, knowledge_dir):
    rag = config.get("rag", {})
    mode = rag.get("mode", "lexical")
    if mode == "lexical":
        return LexicalKnowledgeRetriever(
            knowledge_dir, chunk_size=int(rag.get("chunk_size", 900)),
            chunk_overlap=int(rag.get("chunk_overlap", 120)),
        )
    if mode not in {"vector", "hybrid"}:
        raise ValueError(f"不支持的检索模式: {mode}")
    embedding = rag.get("embedding", {})
    client = OpenAICompatibleEmbeddingClient(
        api_key=os.getenv("EMBEDDING_API_KEY", embedding.get("api_key", "")),
        base_url=os.getenv("EMBEDDING_BASE_URL", embedding.get("api_base", "")),
        model=os.getenv("EMBEDDING_MODEL", embedding.get("model", "text-embedding-v3")),
        dimensions=int(embedding.get("dimensions", 1024)),
        batch_size=int(embedding.get("batch_size", 10)),
        timeout=float(embedding.get("timeout", 60)),
        retries=int(embedding.get("retries", 3)),
    )
    index = SQLiteVectorIndex(resolve_from_root(rag.get("index_path", "./data/vector_store/index.sqlite")), client)
    return HybridKnowledgeRetriever(knowledge_dir, rag, client, index)
