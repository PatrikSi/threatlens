"""Responses for accepted AI operations waiting for provider capacity."""

import uuid
from typing import Literal
from pydantic import BaseModel


class AIWorkflowDeferredResponse(BaseModel):
    status: Literal["queued"] = "queued"
    reason: str
    run_id: uuid.UUID
