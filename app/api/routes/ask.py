import logging
import os

from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.responses import StreamingResponse

from app.api.dependencies import get_ask_service, require_api_key, limiter
from app.models.ask import AskRequest, AskResponse, AskSource
from app.services.ask_service import AskIndexNotFoundError, AskService, AskServiceError

ASK_RATE_LIMIT = os.getenv("ASK_RATE_LIMIT", "30/minute")

def get_ask_rate_limit() -> str:
    return ASK_RATE_LIMIT

router = APIRouter(tags=["ask"], dependencies=[Depends(require_api_key)])
logger = logging.getLogger(__name__)


@router.post("/ask", response_model=AskResponse, summary="Ask a question about an indexed repository")
@limiter.limit(get_ask_rate_limit)
def ask_repository(
    request: Request,
    ask_request: AskRequest,
    ask_service: AskService = Depends(get_ask_service),
) -> AskResponse:
    """Return a Chat Completions answer grounded in indexed repository code."""
    try:
        result = ask_service.ask(
            ask_request.index_id,
            ask_request.question,
            history=[turn.model_dump() for turn in ask_request.history],
        )
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
@limiter.limit(get_ask_rate_limit)
def ask_question_stream(
    request: Request,
    ask_request: AskRequest,
    ask_service: AskService = Depends(get_ask_service),
) -> StreamingResponse:
    """Stream the answer and its citations using Server-Sent Events (SSE)."""
    return StreamingResponse(
        ask_service.stream_ask(
            str(ask_request.index_id),
            ask_request.question,
            history=[turn.model_dump() for turn in ask_request.history],
        ),
        media_type="text/event-stream"
    )


