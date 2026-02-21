from fastapi import APIRouter, Depends, HTTPException, status

from app.config import settings
from app.dynamodb import get_dynamodb_client
from app.repositories.request_repository import RequestRepository
from app.schemas.request import RequestStatusResponse

router = APIRouter(prefix="/api/v1/requests", tags=["requests"])


@router.get(
    "/{request_id}",
    response_model=RequestStatusResponse,
    summary="Get trade request status",
    description=(
        "Poll the lifecycle status of a previously accepted trade request. "
        "Returns PENDING while the Kafka consumer is processing, "
        "SUCCESS once persisted to PostgreSQL, or FAILED with a reason."
    ),
)
async def get_request_status(
    request_id: str,
    dynamodb_client=Depends(get_dynamodb_client),
):
    repo = RequestRepository(dynamodb_client, settings.dynamodb_table_name)
    record = await repo.get_by_request_id(request_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Request '{request_id}' not found",
        )
    return record
