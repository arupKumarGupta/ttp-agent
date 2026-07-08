import time
from typing import List, Dict, Any, Optional
from app.database import get_initial_time_entries

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


class TimesheetSessionState:
    def __init__(self):
        self.time_entries: List[Dict[str, Any]] = get_initial_time_entries()
        self.date_preset: str = "all"
        self.start_date: str = ""
        self.end_date: str = ""
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
            entry_date = entry.get("startDate")
            if self.start_date and self.end_date:
                if not (self.start_date <= entry_date <= self.end_date):
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
                    "datePreset": self.date_preset,
                    "startDate": self.start_date,
                    "endDate": self.end_date,
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
                        "expandedGroups": self.expanded_groups,
                        "currentPage": self.current_page,
                        "totalPages": total_pages,
                        "totalItemsCount": total_items_count
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
                "gap": "24px",
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
