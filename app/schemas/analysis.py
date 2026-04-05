"""
SCHÉMAS D'ANALYSE
"""

from pydantic import BaseModel
from typing import Optional, Any


class UploadResponse(BaseModel):
    analysis_id: int
    dataset_id: str
    file_name: str
    status: str
    metadata: dict


class AnalysisResult(BaseModel):
    analysis_id: int
    dataset_id: str
    file_name: str
    status: str
    metadata: Optional[dict] = None
    charts: Optional[list] = None
    insights: Optional[list] = None
    explanations: Optional[list] = None
    ai_instructions: Optional[dict] = None  # ← AJOUT
    created_at: Optional[str] = None

    model_config = {"from_attributes": True}


class AnalysisListItem(BaseModel):
    id: int
    file_name: str
    status: str
    tokens_consumed: int
    created_at: Optional[str] = None

    model_config = {"from_attributes": True}