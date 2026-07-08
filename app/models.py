from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

class A2UISchemaModel(BaseModel):
    id: str = Field(description="Unique identifier for the element, e.g., 'root', 'card_1'")
    surface: str = Field(description="Name of the component. Must be one of: Stack, Grid, Card, FlightCard, WeatherWidget, TaskBoard, FormWidget")
    data: Dict[str, Any] = Field(description="Content properties for the component. Do not put children here.")
    children: Optional[List['A2UISchemaModel']] = Field(default=None, description="Optional nested components (e.g. inside Stack or Grid)")

A2UISchemaModel.model_rebuild()
