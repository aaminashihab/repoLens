from app.services.ask_service import AskService
from app.services.chunk_service import ChunkService
from app.services.clone_service import CloneService
from app.services.embedding_service import EmbeddingService
from app.services.index_service import IndexService
from app.services.job_service import JobService
from app.services.retrieval_service import RetrievalService

def get_clone_service() -> CloneService:
    return CloneService()

def get_chunk_service() -> ChunkService:
    return ChunkService()

def get_embedding_service() -> EmbeddingService:
    return EmbeddingService()

def get_index_service() -> IndexService:
    return IndexService()

def get_job_service() -> JobService:
    return JobService()

def get_retrieval_service() -> RetrievalService:
    return RetrievalService(
        index_service=get_index_service(),
        embedding_service=get_embedding_service(),
    )

def get_ask_service() -> AskService:
    return AskService(
        retrieval_service=get_retrieval_service()
    )
