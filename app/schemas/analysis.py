"""
SCHÉMAS D'ANALYSE
"""

from pydantic import BaseModel
from typing import Optional, Any, List


class UploadResponse(BaseModel):
    analysis_id: int
    dataset_id: str
    file_name: str
    status: str
    metadata: dict
    # Insights générés par GPT-4o-mini pendant l'upload — prêts pour l'étape 3
    suggested_insights: List[dict] = []
    ai_summary: str = ""


class AnalysisResult(BaseModel):
    analysis_id: int
    dataset_id: str
    file_name: str
    status: str
    metadata: Optional[dict] = None
    # Rapport final généré par OpenAI
    summary: Optional[dict] = None
    kpis: Optional[list] = None
    charts: Optional[list] = None
    insights: Optional[list] = None
    explanations: Optional[list] = None
    ai_instructions: Optional[dict] = None
    suggested_insights: Optional[list] = None
    tokens_consumed: Optional[int] = None
    created_at: Optional[str] = None

    model_config = {"from_attributes": True}


class AnalysisListItem(BaseModel):
    id: int
    file_name: str
    status: str
    tokens_consumed: int
    created_at: Optional[str] = None

    model_config = {"from_attributes": True}