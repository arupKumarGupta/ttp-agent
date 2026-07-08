import json
import asyncio
import time
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.timesheet import TimesheetSessionState
from app.services.generator import get_ui_generator, WELCOME_SCHEMA

router = APIRouter()

@router.websocket("/ws/timesheet")
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


@router.websocket("/ws/agent")
async def agent_websocket(websocket: WebSocket):
    await websocket.accept()
    print("Agent playground client connected.")
    
    await websocket.send_json({
        "type": "schema",
        "schema": WELCOME_SCHEMA
    })

    generator = get_ui_generator()

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
                schema = await generator.generate(prompt)
                
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
