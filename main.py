import asyncio
import json
import os
import time
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="A2UI Streaming Python Agent Backend")

# Enable CORS for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global database loaded from json on start
TIME_ENTRIES_DB: List[Dict[str, Any]] = []

def load_database():
    global TIME_ENTRIES_DB
    # Load database relative to this script directory
    path = os.path.join(os.path.dirname(__file__), "data", "timeEntries.json")
    try:
        with open(path, "r") as f:
            TIME_ENTRIES_DB = json.load(f)
            print(f"Loaded {len(TIME_ENTRIES_DB)} time entries from database.")
    except Exception as e:
        print(f"Error loading timeEntries.json: {e}")
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

# Python State Class representing the Agent's Context
class AgentSessionState:
    def __init__(self):
        self.time_entries: List[Dict[str, Any]] = list(TIME_ENTRIES_DB)
        self.selected_date: str = ""
        self.search_query: str = ""
        self.view_by: str = "none"
        self.current_page: int = 1
        self.expanded_groups: Dict[str, bool] = {}
        self.modal_mode: Optional[str] = None # "create", "edit", "view"
        self.selected_entry: Optional[Dict[str, Any]] = None
        self.toast: Optional[Dict[str, str]] = None # {"message": "...", "type": "..."}

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
        
        # Stats Cards
        children.append({
            "id": "dashboard_stats_panel",
            "surface": "DashboardStats",
            "data": {"stats": stats}
        })
        
        # Control Filters
        children.append({
            "id": "control_filter_bar",
            "surface": "ControlBar",
            "data": {
                "searchQuery": self.search_query,
                "selectedDate": self.selected_date,
                "viewBy": self.view_by
            }
        })
        
        # Table widget (or progressive loader widget)
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

        # Modal
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

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("WebSocket client connected.")
    
    session = AgentSessionState()
    
    async def send_progressive_ui():
        session.toast = None
        
        # progressive stream: Frame 1 - loading visual loader
        loading_schema = session.compile_schema(show_table_loading=True)
        await websocket.send_text(json.dumps(loading_schema))
        
        # artificial thinking delay
        await asyncio.sleep(0.4)
        
        # progressive stream: Frame 2 - actual content table
        resolved_schema = session.compile_schema(show_table_loading=False)
        await websocket.send_text(json.dumps(resolved_schema))

    try:
        await send_progressive_ui()
        
        while True:
            data = await websocket.receive_text()
            event = json.loads(data)
            action_type = event.get("actionType")
            payload = event.get("payload", {})
            print(f"Received Event: {action_type} - Payload: {payload}")
            
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

    except WebSocketDisconnect:
        print("WebSocket client disconnected.")
    except Exception as e:
        print(f"Error inside websocket connection: {e}")
