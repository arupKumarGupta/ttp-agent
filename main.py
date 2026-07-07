import asyncio
import json
import os
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# Check if Gemini API is available
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
gemini_available = False

if GEMINI_API_KEY:
    try:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        gemini_available = True
        print("Gemini API configured successfully.")
    except Exception as e:
        print(f"Failed to configure Gemini API: {e}")

app = FastAPI(title="A2UI Demo Agent Server")

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pydantic schema model matching the A2UISchema typescript interface
class A2UISchemaModel(BaseModel):
    id: str = Field(description="Unique identifier for the element, e.g., 'root', 'card_1'")
    surface: str = Field(description="Name of the component. Must be one of: Stack, Grid, Card, FlightCard, WeatherWidget, TaskBoard, FormWidget")
    data: Dict[str, Any] = Field(description="Content properties for the component. Do not put children here.")
    children: Optional[List['A2UISchemaModel']] = Field(default=None, description="Optional nested components (e.g. inside Stack or Grid)")

A2UISchemaModel.model_rebuild()

# Welcome Fallback UI when API key is missing
WELCOME_SCHEMA = {
    "id": "welcome_root",
    "surface": "Stack",
    "data": {"gap": "16px"},
    "children": [
        {
            "id": "welcome_card",
            "surface": "Card",
            "data": {
                "title": "A2UI Python Backend Online",
                "subtitle": "Generative Agent API endpoint",
                "body": "Welcome! The Python server is connected. To unlock true natural language generative UI, launch this backend with your GEMINI_API_KEY set in the environment. For now, you can trigger rule-based layout streams using the input prompts below.",
                "iconName": "Sparkles",
                "statusText": "Fallback Mode Active",
                "statusType": "warning"
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

# Heuristic mock templates for fallback keywords
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

def generate_fallback_schema(prompt: str) -> Dict[str, Any]:
    prompt_lower = prompt.lower()
    for key, schema in MOCK_TEMPLATES.items():
        if key in prompt_lower:
            return schema
    return WELCOME_SCHEMA

async def generate_gemini_schema(prompt: str) -> Dict[str, Any]:
    import google.generativeai as genai
    
    # System instruction guiding the model on how A2UI works and which components exist
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
    
    model = genai.GenerativeModel(
        model_name="gemini-2.5-flash",
        system_instruction=system_instruction
    )
    
    response = model.generate_content(
        prompt,
        generation_config=genai.GenerationConfig(
            response_mime_type="application/json",
            response_schema=A2UISchemaModel
        )
    )
    
    return json.loads(response.text)

@app.websocket("/ws/agent")
async def agent_websocket(websocket: WebSocket):
    await websocket.accept()
    print("Agent WebSocket client connected.")
    
    # Send initial welcome UI
    await websocket.send_json({
        "type": "schema",
        "schema": WELCOME_SCHEMA
    })

    try:
        while True:
            # Receive text prompts from the frontend
            data = await websocket.receive_text()
            payload = json.loads(data)
            prompt = payload.get("prompt", "")
            print(f"Received agent prompt: {prompt}")

            # Progressive Stream Step 1: Send reasoning loader state
            await websocket.send_json({
                "type": "status",
                "message": "Analyzing prompt...",
                "schema": {
                    "id": "agent_thinking",
                    "surface": "Card",
                    "data": {
                        "title": "Agent Reasoning Flow",
                        "subtitle": "Generating layout structure...",
                        "body": f"The Agent is interpreting: '{prompt}' and calling LLM APIs. Please wait while the responsive surfaces resolve...",
                        "iconName": "Cpu",
                        "statusText": "Thinking...",
                        "statusType": "warning"
                    }
                }
            })

            # Simulate reasoning step delay
            await asyncio.sleep(0.6)

            # Progressive Stream Step 2: Compile the final generated schema
            try:
                if gemini_available:
                    print("Calling Gemini API...")
                    schema = await generate_gemini_schema(prompt)
                else:
                    print("Gemini API not available. Using keyword mock fallback...")
                    schema = generate_fallback_schema(prompt)
                
                await websocket.send_json({
                    "type": "schema",
                    "schema": schema
                })
            except Exception as e:
                print(f"Error compiling Agent schema: {e}")
                # Stream fallback error UI
                await websocket.send_json({
                    "type": "schema",
                    "schema": {
                        "id": "error_card",
                        "surface": "Card",
                        "data": {
                            "title": "Agent Reasoning Error",
                            "subtitle": "Failed to compile generated layout",
                            "body": f"An error occurred while compiling the LLM layout: {e}. If calling Gemini, check your API key and quota.",
                            "iconName": "AlertTriangle",
                            "statusText": "API Error",
                            "statusType": "danger"
                        }
                    }
                })

    except WebSocketDisconnect:
        print("Agent WebSocket disconnected.")
    except Exception as e:
        print(f"WebSocket session error: {e}")
