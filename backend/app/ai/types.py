from typing import Literal

from pydantic import BaseModel, Field


class AnalysisOut(BaseModel):
    complaint_summary: str
    category: str
    related_categories: list[str] = []
    sentiment: Literal["Negative", "Neutral", "Positive"]
    severity: Literal["Low", "Medium", "High", "Critical"]
    entities: dict = {}
    issues: list[str] = []  # allegations made by the complainant, not verified facts
    requires_investigation: bool = True
    recommended_action: str = ""
    confidence: float = Field(ge=0.0, le=1.0)
