import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from app.api.dependencies import get_ask_service
from app.models.ask import AskRequest, AskResponse, AskSource
from app.services.ask_service import AskIndexNotFoundError, AskService, AskServiceError


router = APIRouter(tags=["ask"])
logger = logging.getLogger(__name__)


@router.post("/ask", response_model=AskResponse, summary="Ask a question about an indexed repository")
def ask_repository(
    request: AskRequest,
    ask_service: AskService = Depends(get_ask_service),
) -> AskResponse:
    """Return a Chat Completions answer grounded in indexed repository code."""
    try:
        result = ask_service.ask(request.index_id, request.question)
    except AskIndexNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except AskServiceError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return AskResponse(
        answer=result.answer,
        sources=[
            AskSource(
                file_path=source.file_path,
                symbol_name=source.symbol_name,
                score=source.score,
            )
            for source in result.sources
        ],
    )

@router.post(
    "/ask/stream",
    summary="Ask a question and stream the response",
)
def ask_question_stream(
    request: AskRequest,
    ask_service: AskService = Depends(get_ask_service),
) -> StreamingResponse:
    """Stream the answer and its citations using Server-Sent Events (SSE)."""
    return StreamingResponse(
        ask_service.stream_ask(str(request.index_id), request.question),
        media_type="text/event-stream"
    )


