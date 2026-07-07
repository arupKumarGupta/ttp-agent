import asyncio
import json
import os
import time
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

app = FastAPI(title="Unified A2UI Agent Server")

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global Timesheet Database loaded from json on start
TIME_ENTRIES_DB: List[Dict[str, Any]] = []

def load_database():
    global TIME_ENTRIES_DB
    path = os.path.join(os.path.dirname(__file__), "data", "timeEntries.json")
    try:
        with open(path, "r") as f:
            TIME_ENTRIES_DB = json.load(f)
            print(f"Loaded {len(TIME_ENTRIES_DB)} time entries from database.")
    except Exception as e:
        # Fallback database structure if not found (or for a2ui-demo local folder)
        print(f"timeEntries.json not found in data/ (falling back to empty database): {e}")
        TIME_ENTRIES_DB = []

# Load data on launch
load_database()

# Helper: compute minutes logged in a single entry
def get_entry_minutes(entry: Dict[str, Any]) -> int:
    duration = entry.get("duration")
    if duration:
        try:
            hours, minutes = map(int, duration.split(":"))
            return hours * 60 + minutes
        except ValueError:
            return 0
    
    start_time = entry.get("startTime")
    end_time = entry.get("endTime")
    if start_time and end_time:
        try:
            start_h, start_m = map(int, start_time.split(":"))
            end_h, end_m = map(int, end_time.split(":"))
            start_min = start_h * 60 + start_m
            end_min = end_h * 60 + end_m
            return max(0, end_min - start_min)
        except ValueError:
            return 0
    return 0


# =====================================================================
# TIMESHEET ENDPOINT SUPPORT
# =====================================================================

class TimesheetSessionState:
    def __init__(self):
        self.time_entries: List[Dict[str, Any]] = list(TIME_ENTRIES_DB)
        self.selected_date: str = ""
        self.search_query: str = ""
        self.view_by: str = "none"
        self.current_page: int = 1
        self.expanded_groups: Dict[str, bool] = {}
        self.modal_mode: Optional[str] = None
        self.selected_entry: Optional[Dict[str, Any]] = None
        self.toast: Optional[Dict[str, str]] = None
        self.active_tab: str = "timesheet"

    def get_autocompletes(self) -> Dict[str, List[Any]]:
        workers = []
        customers = []
        departments = []
        
        seen_workers = set()
        seen_customers = set()
        seen_depts = set()
        
        for entry in self.time_entries:
            w = entry.get("worker", {})
            w_id = w.get("id")
            if w_id and w_id not in seen_workers:
                seen_workers.add(w_id)
                workers.append(w)
                
            c = entry.get("timeAgainst", {})
            c_id = c.get("id")
            if c_id and c_id not in seen_customers:
                seen_customers.add(c_id)
                customers.append(c)
                
            d = entry.get("department", {})
            d_id = d.get("id")
            if d_id and d_id not in seen_depts:
                seen_depts.add(d_id)
                departments.append(d)
                
        return {"workers": workers, "customers": customers, "departments": departments}

    def compile_schema(self, show_table_loading: bool = False) -> Dict[str, Any]:
        items_per_page = 10 if self.view_by == "none" else 5

        # 1. Filter entries
        filtered = []
        for entry in self.time_entries:
            if self.selected_date and entry.get("startDate") != self.selected_date:
                continue
            if self.search_query:
                q = self.search_query.lower()
                worker_name = entry.get("worker", {}).get("name", "").lower()
                worker_email = entry.get("worker", {}).get("email", "").lower()
                customer_name = entry.get("timeAgainst", {}).get("customer", "").lower()
                location = entry.get("department", {}).get("location", "").lower()
                
                if (q not in worker_name and 
                    q not in worker_email and 
                    q not in customer_name and 
                    q not in location):
                    continue
            filtered.append(entry)

        # 2. Stats
        total_count = len(filtered)
        total_minutes = sum(get_entry_minutes(e) for e in filtered)
        approved_count = sum(1 for e in filtered if e.get("status") == "APPROVED")
        submitted_count = sum(1 for e in filtered if e.get("status") == "SUBMITTED")
        open_count = sum(1 for e in filtered if e.get("status") == "OPEN")
        
        stats = {
            "totalCount": total_count,
            "totalHours": f"{total_minutes / 60:.1f}",
            "approvedCount": approved_count,
            "submittedCount": submitted_count,
            "openCount": open_count
        }

        # 3. Group by Worker
        grouped_workers = []
        if self.view_by == "worker":
            worker_map = {}
            for entry in filtered:
                w_id = entry["worker"]["id"]
                if w_id not in worker_map:
                    worker_map[w_id] = {"worker": entry["worker"], "entries": []}
                worker_map[w_id]["entries"].append(entry)
                
            for w_id, w_data in worker_map.items():
                date_map = {}
                for entry in w_data["entries"]:
                    d = entry["startDate"]
                    if d not in date_map:
                        date_map[d] = []
                    date_map[d].append(entry)
                    
                date_groups = []
                for d, d_entries in date_map.items():
                    d_mins = sum(get_entry_minutes(e) for e in d_entries)
                    date_groups.append({
                        "date": d,
                        "entries": d_entries,
                        "totalMinutes": d_mins
                    })
                date_groups.sort(key=lambda x: x["date"], reverse=True)
                total_mins = sum(dg["totalMinutes"] for dg in date_groups)
                grouped_workers.append({
                    "worker": w_data["worker"],
                    "totalMinutes": total_mins,
                    "dateGroups": date_groups,
                    "totalEntriesCount": len(w_data["entries"])
                })
            grouped_workers.sort(key=lambda x: x["worker"]["name"])

        # 4. Group by Customer
        grouped_customers = []
        if self.view_by == "customer":
            customer_map = {}
            for entry in filtered:
                c_id = entry["timeAgainst"]["id"]
                if c_id not in customer_map:
                    customer_map[c_id] = {"customer": entry["timeAgainst"], "entries": []}
                customer_map[c_id]["entries"].append(entry)
                
            for c_id, c_data in customer_map.items():
                worker_map = {}
                for entry in c_data["entries"]:
                    w_id = entry["worker"]["id"]
                    if w_id not in worker_map:
                        worker_map[w_id] = {"worker": entry["worker"], "entries": []}
                    worker_map[w_id]["entries"].append(entry)
                    
                worker_groups = []
                for w_id, w_data in worker_map.items():
                    w_mins = sum(get_entry_minutes(e) for e in w_data["entries"])
                    worker_groups.append({
                        "worker": w_data["worker"],
                        "entries": w_data["entries"],
                        "totalMinutes": w_mins
                    })
                worker_groups.sort(key=lambda x: x["worker"]["name"])
                total_mins = sum(wg["totalMinutes"] for wg in worker_groups)
                grouped_customers.append({
                    "customer": c_data["customer"],
                    "totalMinutes": total_mins,
                    "workerGroups": worker_groups,
                    "totalEntriesCount": len(c_data["entries"])
                })
            grouped_customers.sort(key=lambda x: x["customer"]["customer"])

        # 5. Slices and Totals
        start_idx = (self.current_page - 1) * items_per_page
        end_idx = start_idx + items_per_page
        
        paginated_data = []
        total_items_count = 0
        if self.view_by == "none":
            paginated_data = filtered[start_idx:end_idx]
            total_items_count = len(filtered)
        elif self.view_by == "worker":
            paginated_data = grouped_workers[start_idx:end_idx]
            total_items_count = len(grouped_workers)
        else:
            paginated_data = grouped_customers[start_idx:end_idx]
            total_items_count = len(grouped_customers)

        total_pages = max(1, (total_items_count + items_per_page - 1) // items_per_page)

        # 6. Form State for Modals
        initial_form_state = None
        if self.modal_mode and (self.modal_mode in ["edit", "view"]) and self.selected_entry:
            is_time_based = bool(self.selected_entry.get("startTime") and self.selected_entry.get("endTime"))
            initial_form_state = {
                "workerName": self.selected_entry["worker"]["name"],
                "workerEmail": self.selected_entry["worker"]["email"],
                "customerName": self.selected_entry["timeAgainst"]["customer"],
                "startDate": self.selected_entry["startDate"],
                "duration": self.selected_entry.get("duration") or "08:00",
                "startTime": self.selected_entry.get("startTime") or "",
                "endTime": self.selected_entry.get("endTime") or "",
                "status": self.selected_entry["status"],
                "location": self.selected_entry["department"]["location"],
                "useStartEndTimes": is_time_based
            }
        elif self.modal_mode == "create":
            today = time.strftime("%Y-%m-%d")
            initial_form_state = {
                "workerName": "",
                "workerEmail": "",
                "customerName": "",
                "startDate": today,
                "duration": "08:00",
                "startTime": "09:00",
                "endTime": "17:00",
                "status": "OPEN",
                "location": "New York",
                "useStartEndTimes": False
            }

        # 7. Layout Tree nodes
        children = []

        # TabBar component definition (Prepend to all views)
        tab_bar_node = {
            "id": "timesheet_tab_bar",
            "surface": "TabBar",
            "data": {
                "tabs": ["timesheet", "kiosk"],
                "activeTab": self.active_tab
            }
        }
        children.append(tab_bar_node)

        if self.active_tab == "timesheet":
            children.append({
                "id": "dashboard_stats_panel",
                "surface": "DashboardStats",
                "data": {"stats": stats}
            })
            children.append({
                "id": "control_filter_bar",
                "surface": "ControlBar",
                "data": {
                    "searchQuery": self.search_query,
                    "selectedDate": self.selected_date,
                    "viewBy": self.view_by
                }
            })
            
            if show_table_loading:
                children.append({
                    "id": "main_entries_table_loading",
                    "surface": "Card",
                    "data": {
                        "title": "Agent Processing",
                        "subtitle": "Crunching timesheet data...",
                        "body": "Your Python Agent is fetching and grouping records according to your new filter query.",
                        "iconName": "Clock",
                        "statusText": "Processing...",
                        "statusType": "warning"
                    }
                })
            else:
                table_surface = "FlatTable" if self.view_by == "none" else ("WorkerGroupTable" if self.view_by == "worker" else "CustomerGroupTable")
                children.append({
                    "id": "main_entries_table",
                    "surface": table_surface,
                    "data": {
                        "data": paginated_data,
                        "expandedGroups": self.expanded_groups
                    }
                })

            if self.modal_mode and initial_form_state:
                children.append({
                    "id": "time_entry_form_modal",
                    "surface": "TimeEntryFormModal",
                    "data": {
                        "modalMode": self.modal_mode,
                        "initialFormState": initial_form_state,
                        "autocompletes": self.get_autocompletes()
                    }
                })
        else:
            children.append({
                "id": "kiosk_coming_soon",
                "surface": "Card",
                "data": {
                    "title": "Kiosk Interface",
                    "subtitle": "Agent Generated Surface",
                    "body": "The worker kiosk clock-in/out visual experience is currently under development. Powered by A2UI generative layouts.",
                    "iconName": "Info",
                    "statusText": "Coming Soon",
                    "statusType": "info"
                }
            })

        return {
            "id": "timetrack_a2ui_root",
            "surface": "Stack",
            "data": {
                "gap": "0px",
                "metadata": {
                    "totalItemsCount": total_items_count,
                    "totalPages": total_pages,
                    "currentPage": self.current_page,
                    "viewBy": self.view_by,
                    "filteredCount": len(filtered),
                    "groupedWorkersCount": len(grouped_workers),
                    "groupedCustomersCount": len(grouped_customers),
                    "loading": False,
                    "error": None,
                    "autocompletes": self.get_autocompletes(),
                    "toast": self.toast
                }
            },
            "children": children
        }


# =====================================================================
# GENERATIVE UI ENDPOINT SUPPORT
# =====================================================================

class A2UISchemaModel(BaseModel):
    id: str = Field(description="Unique identifier for the element, e.g., 'root', 'card_1'")
    surface: str = Field(description="Name of the component. Must be one of: Stack, Grid, Card, FlightCard, WeatherWidget, TaskBoard, FormWidget")
    data: Dict[str, Any] = Field(description="Content properties for the component. Do not put children here.")
    children: Optional[List['A2UISchemaModel']] = Field(default=None, description="Optional nested components (e.g. inside Stack or Grid)")

A2UISchemaModel.model_rebuild()

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

def generate_fallback_schema(prompt: str) -> Dict[str, Any]:
    prompt_lower = prompt.lower()
    for key, schema in MOCK_TEMPLATES.items():
        if key in prompt_lower:
            return schema
    return WELCOME_SCHEMA

async def generate_gemini_schema(prompt: str) -> Dict[str, Any]:
    import google.generativeai as genai
    
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


# =====================================================================
# WEBSOCKET ROUTING ENDPOINTS
# =====================================================================

@app.websocket("/ws/timesheet")
async def timesheet_websocket(websocket: WebSocket):
    await websocket.accept()
    print("Timesheet client connected.")
    
    session = TimesheetSessionState()
    
    async def send_progressive_ui():
        session.toast = None
        loading_schema = session.compile_schema(show_table_loading=True)
        await websocket.send_text(json.dumps(loading_schema))
        await asyncio.sleep(0.4)
        resolved_schema = session.compile_schema(show_table_loading=False)
        await websocket.send_text(json.dumps(resolved_schema))

    try:
        await send_progressive_ui()
        
        while True:
            data = await websocket.receive_text()
            event = json.loads(data)
            action_type = event.get("actionType")
            payload = event.get("payload", {})
            print(f"Timesheet Action: {action_type}")
            
            session.toast = None

            if action_type == "change_search":
                session.search_query = payload.get("query", "")
                session.current_page = 1
                await send_progressive_ui()
                
            elif action_type == "change_date":
                session.selected_date = payload.get("date", "")
                session.current_page = 1
                await send_progressive_ui()
                
            elif action_type == "change_view":
                session.view_by = payload.get("view", "none")
                session.current_page = 1
                session.expanded_groups = {}
                await send_progressive_ui()
                
            elif action_type == "toggle_group":
                g_key = payload.get("groupKey", "")
                session.expanded_groups[g_key] = not session.expanded_groups.get(g_key, False)
                await websocket.send_text(json.dumps(session.compile_schema()))
                
            elif action_type == "open_view":
                session.modal_mode = "view"
                session.selected_entry = payload.get("entry")
                await websocket.send_text(json.dumps(session.compile_schema()))
                
            elif action_type == "open_edit":
                session.modal_mode = "edit"
                session.selected_entry = payload.get("entry")
                await websocket.send_text(json.dumps(session.compile_schema()))
                
            elif action_type == "close_modal":
                session.modal_mode = None
                session.selected_entry = None
                await websocket.send_text(json.dumps(session.compile_schema()))
                
            elif action_type == "open_create":
                session.modal_mode = "create"
                session.selected_entry = None
                await websocket.send_text(json.dumps(session.compile_schema()))
                
            elif action_type == "set_current_page":
                session.current_page = payload.get("page", 1)
                await websocket.send_text(json.dumps(session.compile_schema()))
                
            elif action_type == "delete_entry":
                entry_id = payload.get("id")
                session.time_entries = [e for e in session.time_entries if e["id"] != entry_id]
                session.toast = {"message": "Time entry successfully deleted.", "type": "danger"}
                await send_progressive_ui()
                
            elif action_type == "save_entry":
                form = payload.get("formState", {})
                
                if not form.get("workerName", "").strip():
                    continue
                if not form.get("customerName", "").strip():
                    continue
                if not form.get("startDate"):
                    continue

                autocompletes = session.get_autocompletes()
                
                worker_name = form["workerName"].strip()
                matched_w = next((w for w in autocompletes["workers"] if w["name"].lower() == worker_name.lower()), None)
                if not matched_w:
                    matched_w = {
                        "id": f"w-{int(time.time() * 1000)}",
                        "name": worker_name,
                        "email": form.get("workerEmail", "").strip() or f"{worker_name.lower().replace(' ', '.')}@example.com"
                    }
                elif form.get("workerEmail", "").strip():
                    matched_w["email"] = form["workerEmail"].strip()

                cust_name = form["customerName"].strip()
                matched_c = next((c for c in autocompletes["customers"] if c["customer"].lower() == cust_name.lower()), None)
                if not matched_c:
                    matched_c = {
                        "id": f"c-{int(time.time() * 1000)}",
                        "customer": cust_name
                    }

                loc = form["location"]
                matched_d = next((d for d in autocompletes["departments"] if d["location"].lower() == loc.lower()), None)
                if not matched_d:
                    matched_d = {
                        "id": f"d-{int(time.time() * 1000)}",
                        "location": loc
                    }

                new_entry = {
                    "id": session.selected_entry["id"] if session.modal_mode == "edit" and session.selected_entry else f"te-{int(time.time() * 1000)}",
                    "worker": matched_w,
                    "timeAgainst": matched_c,
                    "status": form["status"],
                    "startDate": form["startDate"],
                    "duration": None if form["useStartEndTimes"] else form["duration"],
                    "startTime": form["startTime"] if form["useStartEndTimes"] else None,
                    "endTime": form["endTime"] if form["useStartEndTimes"] else None,
                    "department": matched_d
                }

                if session.modal_mode == "edit":
                    session.time_entries = [new_entry if e["id"] == new_entry["id"] else e for e in session.time_entries]
                    session.toast = {"message": "Time entry successfully updated.", "type": "success"}
                else:
                    session.time_entries.insert(0, new_entry)
                    session.toast = {"message": "New time entry successfully logged.", "type": "success"}

                session.modal_mode = None
                session.selected_entry = None
                await send_progressive_ui()
                
            elif action_type == "change_tab":
                session.active_tab = payload.get("tab", "timesheet")
                await websocket.send_text(json.dumps(session.compile_schema()))

    except WebSocketDisconnect:
        print("Timesheet client disconnected.")
    except Exception as e:
        print(f"Error in timesheet websocket: {e}")


@app.websocket("/ws/agent")
async def agent_websocket(websocket: WebSocket):
    await websocket.accept()
    print("Agent playground client connected.")
    
    await websocket.send_json({
        "type": "schema",
        "schema": WELCOME_SCHEMA
    })

    try:
        while True:
            data = await websocket.receive_text()
            payload = json.loads(data)
            prompt = payload.get("prompt", "")
            print(f"Agent Prompt: {prompt}")

            await websocket.send_json({
                "type": "status",
                "message": "Analyzing prompt...",
                "schema": {
                    "id": "agent_thinking",
                    "surface": "Card",
                    "data": {
                        "title": "Agent Reasoning Flow",
                        "subtitle": "Generating layout structure...",
                        "body": f"The Agent is interpreting: '{prompt}' and calling Gemini API. Please wait while the responsive surfaces resolve...",
                        "iconName": "Cpu",
                        "statusText": "Thinking...",
                        "statusType": "warning"
                    }
                }
            })

            await asyncio.sleep(0.6)

            try:
                if gemini_available:
                    schema = await generate_gemini_schema(prompt)
                else:
                    schema = generate_fallback_schema(prompt)
                
                await websocket.send_json({
                    "type": "schema",
                    "schema": schema
                })
            except Exception as e:
                await websocket.send_json({
                    "type": "schema",
                    "schema": {
                        "id": "error_card",
                        "surface": "Card",
                        "data": {
                            "title": "Agent Reasoning Error",
                            "subtitle": "Failed to compile generated layout",
                            "body": f"An error occurred while compiling the LLM layout: {e}.",
                            "iconName": "AlertTriangle",
                            "statusText": "API Error",
                            "statusType": "danger"
                        }
                    }
                })

    except WebSocketDisconnect:
        print("Agent playground client disconnected.")
    except Exception as e:
        print(f"Error in agent playground websocket: {e}")
