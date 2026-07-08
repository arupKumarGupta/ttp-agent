import json
import asyncio
from abc import ABC, abstractmethod
from typing import Dict, Any
from app.config import gemini_available
from app.models import A2UISchemaModel

# ---------------------------------------------------------------------
# BASE GENERATOR INTERFACE (SRP, OCP, LSP)
# ---------------------------------------------------------------------
class BaseGenerator(ABC):
    @abstractmethod
    async def generate(self, prompt: str) -> Dict[str, Any]:
        """Generate a layout schema matching the user's prompt."""
        pass


# ---------------------------------------------------------------------
# SCHEMAS AND MOCK TEMPLATES
# ---------------------------------------------------------------------
WELCOME_SCHEMA = {
    "id": "welcome_root",
    "surface": "Stack",
    "data": {"gap": "16px"},
    "children": [
        {
            "id": "welcome_card",
            "surface": "Card",
            "data": {
                "title": "A2UI Unified Agent Server",
                "subtitle": "Generative Agent API endpoint",
                "body": "Welcome! The Unified Python server is connected. To unlock true natural language generative UI, launch this backend with your GEMINI_API_KEY set in the environment. For now, you can trigger rule-based layout streams using the input prompts below.",
                "iconName": "Sparkles",
                "statusText": "Agent Active",
                "statusType": "success"
            }
        },
        {
            "id": "intro_checklist",
            "surface": "TaskBoard",
            "data": {
                "title": "Agent Server Tasklist",
                "tasks": [
                    {"taskId": "task_1", "label": "Establish WebSocket connection", "completed": True, "priority": "high"},
                    {"taskId": "task_2", "label": "Set GEMINI_API_KEY env variable", "completed": False, "priority": "high"},
                    {"taskId": "task_3", "label": "Submit a text prompt to generate layouts", "completed": False, "priority": "medium"}
                ]
            }
        }
    ]
}

MOCK_TEMPLATES = {
    "flight": {
        "id": "root",
        "surface": "Stack",
        "data": {"gap": "20px"},
        "children": [
            {
                "id": "ticket_1",
                "surface": "FlightCard",
                "data": {
                    "airline": "Atlantic Air",
                    "flightNumber": "AA-409",
                    "fromCode": "JFK",
                    "fromCity": "New York",
                    "toCode": "LHR",
                    "toCity": "London",
                    "duration": "7h 10m",
                    "departureTime": "07:30 PM",
                    "arrivalTime": "07:40 AM",
                    "date": "July 18, 2026",
                    "gate": "B3",
                    "seat": "22C",
                    "boardingClass": "Economy Plus",
                    "boardingTime": "06:45 PM",
                    "status": "Delayed"
                }
            }
        ]
    },
    "weather": {
        "id": "root",
        "surface": "Stack",
        "data": {"gap": "20px"},
        "children": [
            {
                "id": "seattle_weather",
                "surface": "WeatherWidget",
                "data": {
                    "location": "Seattle, WA",
                    "date": "Wednesday, July 15",
                    "temperature": 68,
                    "unit": "F",
                    "condition": "Cloudy",
                    "humidity": "64%",
                    "wind": "8 mph NNW",
                    "uvIndex": "4 (Moderate)",
                    "recommendations": ["Overcast day. Bring a light sweater.", "Good temperature for outdoor walks."]
                }
            }
        ]
    },
    "form": {
        "id": "root",
        "surface": "Stack",
        "data": {"gap": "20px"},
        "children": [
            {
                "id": "support_form",
                "surface": "FormWidget",
                "data": {
                    "title": "Contact Customer Relations",
                    "subtitle": "Let us know how your dynamic experience went.",
                    "submitLabel": "Send Message",
                    "fields": [
                        {"name": "full_name", "label": "Full Name", "type": "text", "placeholder": "Enter your name"},
                        {"name": "feedback_type", "label": "Feedback Type", "type": "select", "defaultValue": "General", "options": ["General", "Bug Report", "Feature Request"]},
                        {"name": "comments", "label": "Comments/Suggestions", "type": "text", "placeholder": "Enter details here..."}
                    ]
                }
            }
        ]
    }
}


# ---------------------------------------------------------------------
# CONCRETE GENERATOR IMPLEMENTATIONS (SRP)
# ---------------------------------------------------------------------
class FallbackGenerator(BaseGenerator):
    async def generate(self, prompt: str) -> Dict[str, Any]:
        prompt_lower = prompt.lower()
        for key, schema in MOCK_TEMPLATES.items():
            if key in prompt_lower:
                return schema
        return WELCOME_SCHEMA


class GeminiGenerator(BaseGenerator):
    def __init__(self):
        # Configuration is already established in app.config
        import google.generativeai as genai
        self.genai = genai

    async def generate(self, prompt: str) -> Dict[str, Any]:
        system_instruction = """
        You are an AI UI Agent. Your job is to output a single structured JSON schema that defines a user interface layout to answer the user's prompt.
        You must construct the UI from these registered surfaces only:
        1. Stack (A vertical container): Has data: { "gap": "16px" }
        2. Grid (A grid container): Has data: { "columns": 2, "gap": "20px" }
        3. Card (Information card): Has data: { "title": str, "subtitle": str, "body": str, "iconName": str, "statusText": str, "statusType": 'success'|'warning'|'info'|'danger', "actions": list }
           - Note: iconName must be a valid Lucide icon string like: 'Info', 'Sparkles', 'Activity', 'CheckCircle', 'TrendingUp', 'Compass'.
           - actions can contain action buttons with structure: { "label": str, "actionType": str, "payload": dict, "primary": bool }
        4. FlightCard (Airline Boarding Pass): Has data: { "airline": str, "flightNumber": str, "fromCode": str, "fromCity": str, "toCode": str, "toCity": str, "duration": str, "departureTime": str, "arrivalTime": str, "date": str, "gate": str, "seat": str, "boardingClass": str, "boardingTime": str, "status": 'On Time'|'Delayed'|'Boarding' }
        5. WeatherWidget (Weather card): Has data: { "location": str, "date": str, "temperature": int, "unit": 'C'|'F', "condition": 'Sunny'|'Cloudy'|'Rainy'|'Snowy'|'Stormy', "humidity": str, "wind": str, "uvIndex": str, "recommendations": list[str] }
        6. TaskBoard (Checklist): Has data: { "title": str, "tasks": list }
           - tasks list contains: { "taskId": str, "label": str, "completed": bool, "priority": 'high'|'medium'|'low' }
        7. FormWidget (Inputs form): Has data: { "title": str, "subtitle": str, "submitLabel": str, "fields": list }
           - fields list contains: { "name": str, "label": str, "type": 'text'|'number'|'select', "placeholder": str, "options": list[str], "defaultValue": str }

        Assemble the UIs logically. Lay them out in a Stack or Grid as the root. For example, if they want weather and flight details, use a Stack with a WeatherWidget and a FlightCard.
        """
        
        model = self.genai.GenerativeModel(
            model_name="gemini-2.5-flash",
            system_instruction=system_instruction
        )
        
        # Gemini call is blocking; wrap in a threadpool to remain async-compliant
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None,
            lambda: model.generate_content(
                prompt,
                generation_config=self.genai.GenerationConfig(
                    response_mime_type="application/json",
                    response_schema=A2UISchemaModel
                )
            )
        )
        return json.loads(response.text)


# ---------------------------------------------------------------------
# UI GENERATOR FACTORY / DEPENDENCY INVERSION (DIP)
# ---------------------------------------------------------------------
def get_ui_generator() -> BaseGenerator:
    """Return the configured UI generator depending on API key availability."""
    if gemini_available:
        return GeminiGenerator()
    return FallbackGenerator()
