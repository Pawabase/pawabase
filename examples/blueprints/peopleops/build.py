"""Build a production-style PeopleOps HRIS blueprint.

Run ``python build.py`` and import ``peopleops.blueprint.json`` from Studio's
new-project dialog.  It intentionally contains only portable configuration:
no infrastructure credentials, API keys, webhook secrets, or employee data.

The example models one company per Akountz organization.  Every company-owned
row carries ``organization_id`` and policies push that constraint into SQL.
"""

from __future__ import annotations

import json
from collections import defaultdict, deque
from pathlib import Path

HERE = Path(__file__).parent
ORG = "organization_id"


def F(name, type="string", **options):
    return {"name": name, "type": type, **options}


def ops(read="company_record", create="company_create", write="company_admin"):
    return {
        "list": {"enabled": bool(read), "policy": read},
        "get": {"enabled": bool(read), "policy": read},
        "create": {"enabled": bool(create), "policy": create},
        "update": {"enabled": bool(write), "policy": write},
        "delete": {"enabled": bool(write), "policy": write},
    }


def R(name, description, fields, *, tags, relations=(), read="company_record", create="company_create", write="company_admin", realtime=False, owner=None):
    return {
        "name": name, "description": description, "id_type": "integer", "fields": [
            F(ORG, required=True, max_length=63, description="Akountz organization slug"), *fields
        ], "operations": ops(read, create, write), "relations": list(relations), "timestamps": True,
        "events": True, "realtime": realtime, "cache_ttl": 0, "rate_limit": {},
        "owner_field": owner, "tags": tags,
    }


def BT(name, resource, field):
    return {"name": name, "type": "belongs_to", "resource": resource, "field": field}


def HM(name, resource, field):
    return {"name": name, "type": "has_many", "resource": resource, "field": field}


def flow(name, description, nodes, edges, timeout=120):
    """Produce Studio's visual flow shape, with a deterministic top-down layout."""
    children, indegree = defaultdict(list), defaultdict(int)
    for source, target, *_ in edges:
        children[source].append(target)
        indegree[target] += 1
    queue, depth = deque((node[0], 0) for node in nodes if not indegree[node[0]]), {}
    while queue:
        node, level = queue.popleft()
        if level <= depth.get(node, -1):
            continue
        depth[node] = level
        queue.extend((child, level + 1) for child in children[node])
    columns = defaultdict(int)
    graph_nodes = []
    for node_id, block, config in nodes:
        level = depth.get(node_id, 0)
        graph_nodes.append({"id": node_id, "position": {"x": 80 + columns[level] * 300, "y": 70 + level * 150}, "data": {"block": block, "config": config}})
        columns[level] += 1
    graph_edges = []
    for edge in edges:
        source, target = edge[0], edge[1]
        item = {"id": f"{source}-{edge[2] if len(edge) > 2 else 'next'}-{target}", "source": source, "target": target}
        if len(edge) > 2:
            item["sourceHandle"] = edge[2]
        graph_edges.append(item)
    return {"name": name, "description": description, "definition": {"nodes": graph_nodes, "edges": graph_edges}, "enabled": True, "timeout": timeout, "record_runs": True}


def event_flow(name, event, description, *, notification=None, channel=None, queues=()):
    """A reusable event fan-out: record an audit fact, queue independent work, notify and publish."""
    nodes = [("start", "trigger.event", {"event": event}), ("audit", "resource.create", {"resource": "audit_logs", "data": {ORG: "{{ input.organization_id }}", "event": event, "entity_type": "{{ input.entity_type }}", "entity_id": "{{ input.entity_id }}", "actor_id": "{{ input.actor_id }}", "payload": "{{ input }}"}})]
    edges = [("start", "audit")]
    previous = "audit"
    for index, queued in enumerate(queues):
        node = f"queue_{index}"
        nodes.append((node, "queue.flow", {"flow": queued, "input": "{{ input }}", "queue": "automation"}))
        edges.append((previous, node))
        previous = node
    if notification:
        nodes.append(("notify", "resource.create", {"resource": "notifications", "data": {ORG: "{{ input.organization_id }}", "user_id": "{{ input.user_id }}", "kind": notification, "title": "{{ input.title }}", "body": "{{ input.message }}", "data": "{{ input }}", "status": "unread"}}))
        edges.append((previous, "notify"))
        previous = "notify"
    if channel:
        nodes.append(("publish", "realtime.publish", {"channel": channel, "event": event, "payload": "{{ input }}"}))
        edges.append((previous, "publish"))
    return flow(name, description, nodes, edges)


# Roles are Akountz roles. Permission labels are intentionally granular so an
# installation can attach custom roles without changing the resource model.
ROLES = [
    {"name": "super_admin", "description": "Platform-wide company operator.", "permissions": ["*"]},
    {"name": "organization_owner", "description": "Owns company configuration.", "permissions": ["company.*", "roles.manage", "audit.read"]},
    {"name": "organization_admin", "description": "Company administrator.", "permissions": ["employees.*", "departments.*", "workspace.*", "reports.read"]},
    {"name": "hr_admin", "description": "Runs people operations.", "permissions": ["employees.*", "leave.*", "attendance.*", "documents.*", "onboarding.*", "offboarding.*"]},
    {"name": "hr_manager", "description": "Manages people and approvals.", "permissions": ["employees.read", "employees.update", "leave.approve", "attendance.read", "performance.*"]},
    {"name": "payroll_admin", "description": "Prepares payroll.", "permissions": ["payroll.*", "employees.salary.read", "employees.bank_details.read"]},
    {"name": "payroll_manager", "description": "Approves and finalizes payroll.", "permissions": ["payroll.read", "payroll.approve", "payroll.finalize"]},
    {"name": "finance", "description": "Approves expenses and reimbursements.", "permissions": ["expenses.*", "loans.*", "payroll.read"]},
    {"name": "recruiter", "description": "Operates the candidate pipeline.", "permissions": ["recruitment.*", "offers.create"]},
    {"name": "department_head", "description": "Leads a department.", "permissions": ["team.read", "leave.approve", "expenses.approve", "goals.read"]},
    {"name": "manager", "description": "Manages direct reports.", "permissions": ["team.read", "attendance.read", "leave.approve", "performance.review"]},
    {"name": "project_manager", "description": "Runs company projects.", "permissions": ["projects.*", "tasks.*"]},
    {"name": "workspace_admin", "description": "Manages internal knowledge.", "permissions": ["workspace.*", "announcements.*"]},
    {"name": "auditor", "description": "Read-only compliance reviewer.", "permissions": ["audit.read", "employees.read", "payroll.read"]},
    {"name": "employee", "description": "Default employee self-service role.", "permissions": ["profile.read", "profile.update", "leave.request", "expenses.submit", "tasks.read"]},
    {"name": "contractor", "description": "Restricted external worker.", "permissions": ["profile.read", "tasks.read", "workspace.read"]},
]

POLICIES = [
    {"name": "company_member", "description": "Authenticated member of a company.", "condition": {"all": [{"authenticated": True}, {"exists": "$auth.org"}]}},
    {"name": "company_record", "description": "Company member reading a row in their organization.", "condition": {"all": [{"authenticated": True}, {"eq": [f"$record.{ORG}", "$auth.org"]}]}},
    {"name": "company_create", "description": "Company member creates a row only for their organization.", "condition": {"all": [{"authenticated": True}, {"eq": [f"$input.{ORG}", "$auth.org"]}]}},
    {"name": "company_admin", "description": "Company administrators and HR operators.", "condition": {"all": [{"eq": [f"$record.{ORG}", "$auth.org"]}, {"role": ["super_admin", "organization_owner", "organization_admin", "hr_admin"]}]}},
    {"name": "employee_self", "description": "Employee may read/update rows they own.", "condition": {"owner": "user_id"}},
    {"name": "employee_or_hr", "description": "Employee's private data or an HR administrator.", "condition": {"any": [{"owner": "user_id"}, {"role": ["super_admin", "hr_admin", "payroll_admin"]}]}},
    {"name": "manager_or_hr", "description": "Manager, HR, or administrator.", "condition": {"role": ["super_admin", "organization_admin", "hr_admin", "hr_manager", "manager"]}},
    {"name": "finance_or_manager", "description": "Finance and managers may decide expenses.", "condition": {"role": ["super_admin", "finance", "manager", "department_head"]}},
    {"name": "payroll_private", "description": "Payroll roles only.", "condition": {"role": ["super_admin", "payroll_admin", "payroll_manager"]}},
    {"name": "recruitment_team", "description": "Recruitment team only.", "condition": {"role": ["super_admin", "recruiter", "hr_admin", "hr_manager"]}},
]


# Domains intentionally share the same organization key and relate through IDs;
# fields beyond identifiers are JSON where the business policy must remain
# configurable (tax, approval rules, forms, reporting dimensions).
RESOURCES = [
    # Core organization
    R("organizations", "Company profile and legal operating entity.", [F("slug", required=True, max_length=63), F("name", required=True, max_length=160), F("legal_name", max_length=200), F("country", max_length=2), F("currency", default="NGN"), F("timezone", default="Africa/Lagos"), F("status", enum=["active", "suspended"], default="active")], tags=["Core"], read="company_member", create=False, write="company_admin"),
    R("organization_settings", "Company-wide HR, payroll and workflow settings.", [F("key", required=True, max_length=100), F("value", "json", required=True), F("description", "text")], tags=["Core"], read="company_admin", create="company_admin", write="company_admin"),
    R("business_units", "Legal/business divisions.", [F("name", required=True), F("code", max_length=32), F("leader_employee_id", "integer")], tags=["Core"], relations=[BT("leader", "employees", "leader_employee_id")]),
    R("offices", "Physical offices and remote hubs.", [F("name", required=True), F("code", max_length=32), F("country", max_length=2), F("city"), F("address", "json"), F("timezone")], tags=["Core"]),
    R("departments", "Company departments.", [F("name", required=True), F("code", max_length=32), F("business_unit_id", "integer"), F("head_employee_id", "integer"), F("cost_center_id", "integer")], tags=["Core"], relations=[BT("business_unit", "business_units", "business_unit_id"), BT("head", "employees", "head_employee_id"), HM("employees", "employees", "department_id")]),
    R("teams", "Teams inside departments.", [F("name", required=True), F("department_id", "integer", required=True), F("manager_employee_id", "integer"), F("office_id", "integer")], tags=["Core"], relations=[BT("department", "departments", "department_id"), BT("manager", "employees", "manager_employee_id"), HM("employees", "employees", "team_id")]),
    R("positions", "Approved position catalog.", [F("title", required=True), F("department_id", "integer"), F("job_grade_id", "integer"), F("employment_type", enum=["employee", "contractor", "intern"], default="employee"), F("description", "text")], tags=["Core"], relations=[BT("department", "departments", "department_id")]),
    R("job_grades", "Salary/job-grade framework.", [F("name", required=True), F("level", "integer", required=True), F("salary_min", "integer"), F("salary_mid", "integer"), F("salary_max", "integer")], tags=["Core"], read="payroll_private", create="company_admin", write="company_admin"),
    R("cost_centers", "Financial ownership and budget codes.", [F("code", required=True), F("name", required=True), F("owner_employee_id", "integer"), F("active", "boolean", default=True)], tags=["Core"]),
    # People
    R("employees", "Authoritative employment directory.", [F("user_id", required=True, max_length=64), F("employee_number", required=True), F("legal_first_name", required=True), F("legal_last_name", required=True), F("preferred_name"), F("work_email", "email", required=True), F("personal_email", "email"), F("phone"), F("department_id", "integer"), F("team_id", "integer"), F("manager_employee_id", "integer"), F("position_id", "integer"), F("job_grade_id", "integer"), F("office_id", "integer"), F("employment_type", enum=["employee", "contractor", "intern"], default="employee"), F("status", enum=["draft", "active", "on_leave", "suspended", "offboarding", "terminated"], default="draft"), F("start_date", "date"), F("probation_end_date", "date"), F("photo_key")], tags=["People"], relations=[BT("department", "departments", "department_id"), BT("team", "teams", "team_id"), BT("manager", "employees", "manager_employee_id"), HM("leave_requests", "leave_requests", "employee_id")], realtime=True, owner="user_id"),
    R("employee_profiles", "Extended private demographic profile.", [F("employee_id", "integer", required=True), F("date_of_birth", "date"), F("nationality"), F("marital_status"), F("gender"), F("bio", "text"), F("pronouns")], tags=["People"], relations=[BT("employee", "employees", "employee_id")], read="employee_or_hr", create="employee_or_hr", write="employee_or_hr", owner="user_id"),
    R("employee_emergency_contacts", "Emergency contacts.", [F("employee_id", "integer", required=True), F("name", required=True), F("relationship"), F("phone", required=True), F("email", "email"), F("primary", "boolean", default=False)], tags=["People"], relations=[BT("employee", "employees", "employee_id")], read="employee_or_hr", create="employee_or_hr", write="employee_or_hr", owner="user_id"),
    R("employee_addresses", "Current and historical addresses.", [F("employee_id", "integer", required=True), F("kind", enum=["home", "mailing", "work"], default="home"), F("address", "json", required=True), F("is_primary", "boolean", default=True)], tags=["People"], relations=[BT("employee", "employees", "employee_id")], read="employee_or_hr", create="employee_or_hr", write="employee_or_hr", owner="user_id"),
    R("employee_skills", "Employee skills and proficiency.", [F("employee_id", "integer", required=True), F("name", required=True), F("level", enum=["beginner", "intermediate", "advanced", "expert"]), F("years", "number")], tags=["People"], relations=[BT("employee", "employees", "employee_id")]),
    R("employee_education", "Education history.", [F("employee_id", "integer", required=True), F("institution", required=True), F("qualification"), F("field_of_study"), F("started_on", "date"), F("completed_on", "date")], tags=["People"], relations=[BT("employee", "employees", "employee_id")]),
    R("employee_certifications", "Professional certifications.", [F("employee_id", "integer", required=True), F("name", required=True), F("issuer"), F("issued_on", "date"), F("expires_on", "date"), F("document_id", "integer")], tags=["People"], relations=[BT("employee", "employees", "employee_id")]),
    R("employee_employment_history", "Prior roles and internal employment history.", [F("employee_id", "integer", required=True), F("employer", required=True), F("title"), F("started_on", "date"), F("ended_on", "date"), F("summary", "text")], tags=["People"], relations=[BT("employee", "employees", "employee_id")]),
    R("employment_contracts", "Signed employment agreements.", [F("employee_id", "integer", required=True), F("type", enum=["permanent", "fixed_term", "consulting", "internship"], required=True), F("starts_on", "date", required=True), F("ends_on", "date"), F("status", enum=["draft", "active", "expired", "terminated"], default="draft"), F("document_id", "integer")], tags=["People"], relations=[BT("employee", "employees", "employee_id")], read="employee_or_hr", create="company_admin", write="company_admin"),
    R("compensation_records", "Effective-dated salary and bank/tax references.", [F("employee_id", "integer", required=True), F("effective_on", "date", required=True), F("base_salary", "integer", required=True), F("currency", default="NGN"), F("pay_frequency", enum=["monthly", "biweekly", "weekly"], default="monthly"), F("bank_details", "json"), F("tax_profile", "json"), F("reason")], tags=["Payroll"], relations=[BT("employee", "employees", "employee_id")], read="payroll_private", create="payroll_private", write="payroll_private"),
    # Documents, attendance and leave
    R("document_categories", "Required and optional document classes.", [F("name", required=True), F("required_for", "json"), F("retention_years", "integer"), F("expires", "boolean", default=False)], tags=["Documents"]),
    R("employee_documents", "Private employee files stored in the employee-documents bucket.", [F("employee_id", "integer", required=True), F("category_id", "integer"), F("name", required=True), F("storage_key", required=True), F("status", enum=["pending", "verified", "rejected", "expired"], default="pending"), F("expires_on", "date"), F("verified_by", "integer")], tags=["Documents"], relations=[BT("employee", "employees", "employee_id"), BT("category", "document_categories", "category_id")], read="employee_or_hr", create="employee_or_hr", write="company_admin"),
    R("work_schedules", "Working days, hours, and holiday calendar.", [F("name", required=True), F("timezone", required=True), F("weekly_rules", "json", required=True), F("holiday_calendar", "json")], tags=["Attendance"]),
    R("shifts", "Named scheduled shifts.", [F("name", required=True), F("work_schedule_id", "integer"), F("starts_at", "string"), F("ends_at", "string"), F("crosses_midnight", "boolean", default=False)], tags=["Attendance"], relations=[BT("schedule", "work_schedules", "work_schedule_id")]),
    R("attendance_policies", "Attendance rules and grace/overtime thresholds.", [F("name", required=True), F("work_schedule_id", "integer"), F("late_grace_minutes", "integer", default=10), F("overtime_after_minutes", "integer", default=480), F("require_location", "boolean", default=False)], tags=["Attendance"]),
    R("attendance_sessions", "Open presence/clock session.", [F("employee_id", "integer", required=True), F("clocked_in_at", "datetime", required=True), F("clocked_out_at", "datetime"), F("status", enum=["working", "on_break", "closed", "missing_clockout"], default="working"), F("location", "json")], tags=["Attendance"], relations=[BT("employee", "employees", "employee_id")], realtime=True, read="manager_or_hr", create="company_member", write="manager_or_hr"),
    R("attendance_breaks", "Break intervals within a clock session.", [F("session_id", "integer", required=True), F("started_at", "datetime", required=True), F("ended_at", "datetime"), F("kind", enum=["meal", "rest", "other"], default="rest")], tags=["Attendance"], relations=[BT("session", "attendance_sessions", "session_id")], realtime=True),
    R("attendance_records", "Daily calculated attendance ledger.", [F("employee_id", "integer", required=True), F("work_date", "date", required=True), F("scheduled_minutes", "integer"), F("worked_minutes", "integer"), F("break_minutes", "integer"), F("overtime_minutes", "integer"), F("late_minutes", "integer"), F("status", enum=["present", "absent", "late", "leave", "holiday"], default="present")], tags=["Attendance"], relations=[BT("employee", "employees", "employee_id")], realtime=True, read="manager_or_hr", create="manager_or_hr", write="manager_or_hr"),
    R("leave_types", "Leave categories and eligibility.", [F("name", required=True), F("code", required=True), F("paid", "boolean", default=True), F("requires_attachment", "boolean", default=False), F("default_days", "number"), F("active", "boolean", default=True)], tags=["Leave"]),
    R("leave_policies", "Accrual, carryover, and approval settings.", [F("name", required=True), F("leave_type_id", "integer", required=True), F("accrual_rule", "json"), F("carryover_rule", "json"), F("eligibility_rule", "json"), F("approval_rule", "json")], tags=["Leave"], relations=[BT("leave_type", "leave_types", "leave_type_id")]),
    R("leave_balances", "Current annual leave balance per employee/type.", [F("employee_id", "integer", required=True), F("leave_type_id", "integer", required=True), F("period", required=True), F("accrued", "number", default=0), F("used", "number", default=0), F("reserved", "number", default=0), F("carryover", "number", default=0)], tags=["Leave"], relations=[BT("employee", "employees", "employee_id"), BT("leave_type", "leave_types", "leave_type_id")], read="employee_or_hr", create="manager_or_hr", write="manager_or_hr"),
    R("leave_transactions", "Immutable balance accrual/reservation ledger.", [F("employee_id", "integer", required=True), F("leave_balance_id", "integer", required=True), F("kind", enum=["accrual", "reserve", "release", "deduct", "carryover", "adjustment"], required=True), F("days", "number", required=True), F("reference"), F("reason")], tags=["Leave"], relations=[BT("employee", "employees", "employee_id"), BT("balance", "leave_balances", "leave_balance_id")], read="employee_or_hr", create="manager_or_hr", write=False),
    R("leave_requests", "Employee leave requests with approval state.", [F("employee_id", "integer", required=True), F("leave_type_id", "integer", required=True), F("starts_on", "date", required=True), F("ends_on", "date", required=True), F("days", "number"), F("reason", "text"), F("attachment_key"), F("status", enum=["draft", "pending", "approved", "rejected", "cancelled"], default="draft"), F("approval_request_id", "integer")], tags=["Leave"], relations=[BT("employee", "employees", "employee_id"), BT("leave_type", "leave_types", "leave_type_id"), BT("approval", "approval_requests", "approval_request_id")], realtime=True, read="employee_or_hr", create="company_member", write="manager_or_hr", owner="user_id"),
    # Payroll and finance
    R("payroll_groups", "Employees paid together under one calendar.", [F("name", required=True), F("currency", default="NGN"), F("pay_frequency", enum=["monthly", "biweekly", "weekly"], default="monthly"), F("cutoff_rule", "json")], tags=["Payroll"], read="payroll_private", create="payroll_private", write="payroll_private"),
    R("payroll_runs", "Controlled payroll processing run.", [F("payroll_group_id", "integer", required=True), F("period_start", "date", required=True), F("period_end", "date", required=True), F("pay_date", "date"), F("status", enum=["draft", "calculating", "review", "approved", "finalized", "paid", "failed"], default="draft"), F("totals", "json"), F("approved_by", "integer")], tags=["Payroll"], relations=[BT("group", "payroll_groups", "payroll_group_id"), HM("items", "payroll_items", "payroll_run_id")], realtime=True, read="payroll_private", create="payroll_private", write="payroll_private"),
    R("payroll_items", "One employee's computed pay within a run.", [F("payroll_run_id", "integer", required=True), F("employee_id", "integer", required=True), F("gross", "integer"), F("tax", "integer"), F("deductions", "integer"), F("net", "integer"), F("components", "json"), F("status", enum=["pending", "calculated", "paid", "failed"], default="pending")], tags=["Payroll"], relations=[BT("run", "payroll_runs", "payroll_run_id"), BT("employee", "employees", "employee_id")], read="payroll_private", create="payroll_private", write="payroll_private"),
    R("payroll_adjustments", "Ad hoc earnings/deductions for payroll.", [F("employee_id", "integer", required=True), F("payroll_run_id", "integer"), F("kind", enum=["earning", "deduction"], required=True), F("amount", "integer", required=True), F("reason", required=True), F("status", enum=["pending", "approved", "rejected"], default="pending")], tags=["Payroll"], relations=[BT("employee", "employees", "employee_id")], read="payroll_private", create="payroll_private", write="payroll_private"),
    R("payslips", "Employee-specific generated payroll documents.", [F("employee_id", "integer", required=True), F("payroll_item_id", "integer", required=True), F("period", required=True), F("storage_key"), F("net", "integer"), F("published_at", "datetime")], tags=["Payroll"], relations=[BT("employee", "employees", "employee_id"), BT("item", "payroll_items", "payroll_item_id")], read="employee_or_hr", create="payroll_private", write="payroll_private", owner="user_id"),
    R("expense_categories", "Expense taxonomy and policy limits.", [F("name", required=True), F("code"), F("requires_receipt_over", "integer"), F("approval_rule", "json"), F("active", "boolean", default=True)], tags=["Finance"]),
    R("expenses", "Employee reimbursement claims.", [F("employee_id", "integer", required=True), F("category_id", "integer", required=True), F("amount", "integer", required=True), F("currency", default="NGN"), F("incurred_on", "date", required=True), F("merchant"), F("description", "text"), F("receipt_key"), F("status", enum=["draft", "submitted", "approved", "rejected", "reimbursed"], default="draft"), F("approval_request_id", "integer")], tags=["Finance"], relations=[BT("employee", "employees", "employee_id"), BT("category", "expense_categories", "category_id")], realtime=True, read="employee_or_hr", create="company_member", write="finance_or_manager", owner="user_id"),
    R("employee_loans", "Loans, salary advances, and repayment schedules.", [F("employee_id", "integer", required=True), F("principal", "integer", required=True), F("balance", "integer", required=True), F("interest_rate", "number"), F("starts_on", "date"), F("status", enum=["requested", "approved", "active", "closed", "rejected"], default="requested"), F("approval_request_id", "integer")], tags=["Finance"], relations=[BT("employee", "employees", "employee_id")], read="employee_or_hr", create="company_member", write="finance_or_manager", owner="user_id"),
    R("loan_repayments", "Loan repayment ledger.", [F("loan_id", "integer", required=True), F("payroll_item_id", "integer"), F("amount", "integer", required=True), F("paid_on", "date"), F("reference")], tags=["Finance"], relations=[BT("loan", "employee_loans", "loan_id")], read="finance_or_manager", create="finance_or_manager", write=False),
    R("benefits", "Benefit plans and eligibility rules.", [F("name", required=True), F("provider"), F("kind"), F("cost_rule", "json"), F("eligibility_rule", "json"), F("active", "boolean", default=True)], tags=["Benefits"]),
    R("employee_benefits", "Employee enrollment in benefit plans.", [F("employee_id", "integer", required=True), F("benefit_id", "integer", required=True), F("starts_on", "date"), F("ends_on", "date"), F("status", enum=["pending", "active", "ended"], default="pending")], tags=["Benefits"], relations=[BT("employee", "employees", "employee_id"), BT("benefit", "benefits", "benefit_id")], read="employee_or_hr", create="company_admin", write="company_admin"),
    # Performance, recruiting, lifecycle
    R("performance_cycles", "Time-boxed review cycles.", [F("name", required=True), F("starts_on", "date"), F("ends_on", "date"), F("status", enum=["draft", "active", "closed"], default="draft"), F("settings", "json")], tags=["Performance"]),
    R("goals", "Company/team/individual goals.", [F("employee_id", "integer"), F("parent_goal_id", "integer"), F("title", required=True), F("description", "text"), F("progress", "number", default=0), F("status", enum=["not_started", "on_track", "at_risk", "complete"], default="not_started"), F("due_on", "date")], tags=["Performance"], relations=[BT("employee", "employees", "employee_id"), BT("parent", "goals", "parent_goal_id")], realtime=True, read="company_record", create="company_member", write="manager_or_hr"),
    R("performance_reviews", "Manager/self/peer review artifact.", [F("cycle_id", "integer", required=True), F("employee_id", "integer", required=True), F("reviewer_employee_id", "integer"), F("kind", enum=["self", "manager", "peer", "360"], required=True), F("status", enum=["draft", "submitted", "acknowledged"], default="draft"), F("ratings", "json"), F("comments", "text")], tags=["Performance"], relations=[BT("cycle", "performance_cycles", "cycle_id"), BT("employee", "employees", "employee_id")], read="manager_or_hr", create="company_member", write="manager_or_hr"),
    R("feedback", "Continuous feedback and recognition.", [F("from_employee_id", "integer", required=True), F("to_employee_id", "integer", required=True), F("kind", enum=["recognition", "feedback"], default="feedback"), F("body", "text", required=True), F("visibility", enum=["private", "team", "company"], default="private")], tags=["Performance"], relations=[BT("recipient", "employees", "to_employee_id")], realtime=True, read="company_record", create="company_member", write=False),
    R("jobs", "Job requisitions and public openings.", [F("title", required=True), F("department_id", "integer"), F("position_id", "integer"), F("location"), F("employment_type"), F("status", enum=["draft", "open", "paused", "closed"], default="draft"), F("description", "text")], tags=["Recruitment"], relations=[BT("department", "departments", "department_id")], read="recruitment_team", create="recruitment_team", write="recruitment_team"),
    R("candidates", "Candidate profile and consent record.", [F("first_name", required=True), F("last_name", required=True), F("email", "email", required=True), F("phone"), F("source"), F("resume_key"), F("portfolio_url", "url"), F("consent_at", "datetime")], tags=["Recruitment"], read="recruitment_team", create="recruitment_team", write="recruitment_team"),
    R("applications", "Candidate application and pipeline stage.", [F("job_id", "integer", required=True), F("candidate_id", "integer", required=True), F("stage", enum=["applied", "screen", "interview", "offer", "hired", "rejected"], default="applied"), F("rating", "number"), F("owner_employee_id", "integer"), F("notes", "text")], tags=["Recruitment"], relations=[BT("job", "jobs", "job_id"), BT("candidate", "candidates", "candidate_id")], realtime=True, read="recruitment_team", create="recruitment_team", write="recruitment_team"),
    R("interviews", "Interview schedule, scorecard, and feedback.", [F("application_id", "integer", required=True), F("starts_at", "datetime", required=True), F("ends_at", "datetime"), F("interviewers", "json"), F("meeting_url", "url"), F("status", enum=["scheduled", "completed", "cancelled", "no_show"], default="scheduled"), F("scorecard", "json")], tags=["Recruitment"], relations=[BT("application", "applications", "application_id")], read="recruitment_team", create="recruitment_team", write="recruitment_team"),
    R("offers", "Compensation offer and approval state.", [F("application_id", "integer", required=True), F("employee_id", "integer"), F("salary", "integer"), F("currency", default="NGN"), F("starts_on", "date"), F("expires_on", "date"), F("status", enum=["draft", "pending_approval", "sent", "accepted", "declined", "expired"], default="draft"), F("document_key")], tags=["Recruitment"], relations=[BT("application", "applications", "application_id")], read="recruitment_team", create="recruitment_team", write="recruitment_team"),
    R("onboarding_templates", "Reusable onboarding checklist.", [F("name", required=True), F("employment_type"), F("tasks", "json", required=True), F("active", "boolean", default=True)], tags=["Lifecycle"]),
    R("onboarding_runs", "Employee onboarding execution.", [F("employee_id", "integer", required=True), F("template_id", "integer"), F("status", enum=["not_started", "in_progress", "complete", "overdue"], default="not_started"), F("due_on", "date")], tags=["Lifecycle"], relations=[BT("employee", "employees", "employee_id"), HM("tasks", "onboarding_tasks", "onboarding_run_id")], realtime=True),
    R("onboarding_tasks", "Individual onboarding task and assignee.", [F("onboarding_run_id", "integer", required=True), F("title", required=True), F("assignee_employee_id", "integer"), F("due_on", "date"), F("status", enum=["todo", "in_progress", "done", "blocked"], default="todo"), F("required", "boolean", default=True)], tags=["Lifecycle"], relations=[BT("run", "onboarding_runs", "onboarding_run_id")], realtime=True),
    R("offboarding_runs", "Controlled offboarding case.", [F("employee_id", "integer", required=True), F("last_day", "date", required=True), F("reason"), F("status", enum=["planned", "in_progress", "complete", "cancelled"], default="planned")], tags=["Lifecycle"], relations=[BT("employee", "employees", "employee_id"), HM("tasks", "offboarding_tasks", "offboarding_run_id")], realtime=True),
    R("offboarding_tasks", "Access, asset, knowledge-transfer tasks.", [F("offboarding_run_id", "integer", required=True), F("title", required=True), F("assignee_employee_id", "integer"), F("status", enum=["todo", "done", "blocked"], default="todo"), F("due_on", "date")], tags=["Lifecycle"], relations=[BT("run", "offboarding_runs", "offboarding_run_id")], realtime=True),
    R("assets", "Company assets and inventory.", [F("asset_tag", required=True), F("name", required=True), F("kind"), F("serial_number"), F("status", enum=["available", "assigned", "repair", "retired"], default="available")], tags=["Assets"]),
    R("asset_assignments", "Employee asset custody history.", [F("asset_id", "integer", required=True), F("employee_id", "integer", required=True), F("assigned_on", "date"), F("returned_on", "date"), F("condition_notes", "text")], tags=["Assets"], relations=[BT("asset", "assets", "asset_id"), BT("employee", "employees", "employee_id")]),
    # Workspace, approvals and operations
    R("projects", "Internal delivery projects.", [F("name", required=True), F("code"), F("owner_employee_id", "integer"), F("department_id", "integer"), F("status", enum=["planned", "active", "on_hold", "complete", "cancelled"], default="planned"), F("starts_on", "date"), F("ends_on", "date")], tags=["Workspace"], realtime=True),
    R("milestones", "Project milestones.", [F("project_id", "integer", required=True), F("name", required=True), F("due_on", "date"), F("status", enum=["open", "complete", "at_risk"], default="open")], tags=["Workspace"], relations=[BT("project", "projects", "project_id")]),
    R("tasks", "Work items with assignees and state.", [F("project_id", "integer"), F("milestone_id", "integer"), F("title", required=True), F("description", "text"), F("assignee_ids", "json"), F("reporter_employee_id", "integer"), F("status", enum=["backlog", "todo", "in_progress", "blocked", "done"], default="todo"), F("priority", enum=["low", "medium", "high", "urgent"], default="medium"), F("due_on", "date")], tags=["Workspace"], relations=[BT("project", "projects", "project_id"), HM("comments", "task_comments", "task_id")], realtime=True),
    R("task_comments", "Task conversation and mentions.", [F("task_id", "integer", required=True), F("author_employee_id", "integer", required=True), F("body", "text", required=True), F("mentions", "json")], tags=["Workspace"], relations=[BT("task", "tasks", "task_id")], realtime=True),
    R("task_dependencies", "Directed task dependency graph.", [F("task_id", "integer", required=True), F("depends_on_task_id", "integer", required=True), F("kind", enum=["blocks", "relates_to"], default="blocks")], tags=["Workspace"], relations=[BT("task", "tasks", "task_id")]),
    R("workspace_pages", "Notion-like internal knowledge pages.", [F("parent_page_id", "integer"), F("title", required=True), F("slug"), F("owner_employee_id", "integer"), F("visibility", enum=["company", "department", "private"], default="company"), F("department_id", "integer"), F("status", enum=["draft", "published", "archived"], default="draft")], tags=["Workspace"], relations=[BT("parent", "workspace_pages", "parent_page_id"), HM("blocks", "workspace_blocks", "page_id")], realtime=True),
    R("workspace_blocks", "Structured blocks belonging to a workspace page.", [F("page_id", "integer", required=True), F("position", "integer", required=True), F("kind", required=True), F("content", "json", required=True)], tags=["Workspace"], relations=[BT("page", "workspace_pages", "page_id")], realtime=True),
    R("announcements", "Company/department announcements.", [F("title", required=True), F("body", "text", required=True), F("audience", "json"), F("published_at", "datetime"), F("expires_at", "datetime"), F("status", enum=["draft", "published", "archived"], default="draft")], tags=["Workspace"], realtime=True),
    R("approval_requests", "Generic approval engine request.", [F("type", required=True), F("subject_type", required=True), F("subject_id", "integer", required=True), F("requester_employee_id", "integer", required=True), F("status", enum=["pending", "approved", "rejected", "cancelled", "expired"], default="pending"), F("current_stage", "integer", default=1), F("rule", "json"), F("deadline", "datetime"), F("outcome", "json")], tags=["Approvals"], relations=[HM("steps", "approval_steps", "approval_request_id")], realtime=True, read="employee_or_hr", create="company_member", write="manager_or_hr"),
    R("approval_steps", "Approver decision and escalation state.", [F("approval_request_id", "integer", required=True), F("stage", "integer", required=True), F("approver_employee_id", "integer"), F("delegate_employee_id", "integer"), F("status", enum=["pending", "approved", "rejected", "skipped", "escalated"], default="pending"), F("comment", "text"), F("due_at", "datetime"), F("decided_at", "datetime")], tags=["Approvals"], relations=[BT("request", "approval_requests", "approval_request_id")], realtime=True, read="employee_or_hr", create="manager_or_hr", write="manager_or_hr"),
    R("notifications", "In-app notification center.", [F("user_id", required=True, max_length=64), F("kind", required=True), F("title", required=True), F("body", "text"), F("data", "json"), F("status", enum=["unread", "read", "archived"], default="unread"), F("read_at", "datetime")], tags=["Operations"], realtime=True, read="employee_self", create="company_member", write="employee_self", owner="user_id"),
    R("audit_logs", "Immutable business audit trail.", [F("event", required=True), F("entity_type"), F("entity_id", "integer"), F("actor_id", max_length=64), F("payload", "json"), F("request_id")], tags=["Operations"], read="company_admin", create="company_member", write=False),
    R("integration_connections", "Metadata for external HR/payroll/accounting integrations.", [F("provider", required=True), F("name", required=True), F("status", enum=["active", "disabled", "error"], default="disabled"), F("config", "json"), F("last_synced_at", "datetime")], tags=["Integrations"], read="company_admin", create="company_admin", write="company_admin"),
]


FLOWS = [
    event_flow("employee_created", "employees.created", "Fan out a new employee event into lifecycle and notification work.", notification="employee_welcome", channel="organization:{{ input.organization_id }}", queues=("onboarding_start", "employee_policy_assign", "employee_directory_sync")),
    event_flow("attendance_clocked_in", "attendance.clocked_in", "Update operational attendance views without exposing private details.", channel="department:{{ input.department_id }}"),
    event_flow("attendance_clocked_out", "attendance.clocked_out", "Queue attendance calculation and publish a team-safe update.", channel="department:{{ input.department_id }}", queues=("attendance_calculate",)),
    event_flow("leave_requested", "leave.requested", "Create approval work and notify the next approver.", notification="leave_pending", channel="employee:{{ input.employee_id }}", queues=("approval_route",)),
    event_flow("leave_approved", "leave.approved", "Publish approved leave and update availability.", notification="leave_approved", channel="department:{{ input.department_id }}", queues=("leave_calendar_sync",)),
    event_flow("expense_submitted", "expense.submitted", "Route an expense through the generic approval engine.", notification="expense_pending", queues=("approval_route",)),
    event_flow("payroll_finalized", "payroll.finalized", "Fan out private employee payslip notifications.", queues=("payslip_publish", "payroll_payment_export")),
    event_flow("candidate_hired", "candidate.hired", "Convert a hired candidate into controlled onboarding.", queues=("employee_provision", "onboarding_start")),
    event_flow("task_assigned", "task.assigned", "Notify an assignee and publish a personal task update.", notification="task_assigned", channel="employee:{{ input.assignee_employee_id }}"),
    event_flow("document_expiring", "document.expiring", "Escalate document expiry to employee and HR.", notification="document_expiring", channel="employee:{{ input.employee_id }}"),
    flow("onboarding_start", "Create an onboarding run from the selected template, then notify the employee.", [("start", "trigger.job", {"queue": "automation"}), ("create", "resource.create", {"resource": "onboarding_runs", "data": {ORG: "{{ input.organization_id }}", "employee_id": "{{ input.employee_id }}", "template_id": "{{ input.template_id }}", "status": "in_progress"}}), ("emit", "event.emit", {"event": "onboarding.started", "payload": "{{ steps.create.output }}"}), ("publish", "realtime.publish", {"channel": "employee:{{ input.employee_id }}", "event": "onboarding.started", "payload": "{{ steps.create.output }}"})], [("start", "create"), ("create", "emit"), ("emit", "publish")]),
    flow("approval_route", "Resolve approval stages through a reusable asynchronous queue boundary.", [("start", "trigger.job", {"queue": "automation"}), ("retry", "control.retry", {"attempts": 4, "base_delay": 1}), ("create", "resource.create", {"resource": "approval_requests", "data": {ORG: "{{ input.organization_id }}", "type": "{{ input.type }}", "subject_type": "{{ input.subject_type }}", "subject_id": "{{ input.subject_id }}", "requester_employee_id": "{{ input.employee_id }}", "status": "pending", "rule": "{{ input.rule }}"}}), ("emit", "event.emit", {"event": "approval.requested", "payload": "{{ steps.create.output }}"})], [("start", "retry"), ("retry", "create", "attempt"), ("retry", "emit", "next"), ("create", "emit")]),
    flow("attendance_calculate", "Calculate daily attendance asynchronously; replace with a project function for jurisdiction-specific rules.", [("start", "trigger.job", {"queue": "attendance"}), ("load", "resource.get", {"resource": "attendance_sessions", "id": "{{ input.session_id }}"}), ("calculate", "function.call", {"function": "attendance.calculate", "input": "{{ steps.load.output }}"}), ("emit", "event.emit", {"event": "attendance.calculated", "payload": "{{ steps.calculate.output }}"})], [("start", "load"), ("load", "calculate"), ("calculate", "emit")]),
    flow("payslip_publish", "Generate and publish employee-specific payslips via a dedicated payroll worker.", [("start", "trigger.job", {"queue": "payroll"}), ("retry", "control.retry", {"attempts": 5, "base_delay": 2}), ("generate", "function.call", {"function": "payroll.generate_payslips", "input": "{{ input }}"}), ("emit", "event.emit", {"event": "payroll.payslips_published", "payload": "{{ steps.generate.output }}"})], [("start", "retry"), ("retry", "generate", "attempt"), ("generate", "emit")], timeout=900),
    flow("document_expiry_sweep", "Scheduled document expiry check with retry and HR escalation.", [("start", "trigger.schedule", {"cron": "0 7 * * *"}), ("find", "resource.list", {"resource": "employee_documents", "filters": {"status": "verified"}, "limit": 500}), ("retry", "control.retry", {"attempts": 3, "base_delay": 2}), ("process", "function.call", {"function": "documents.expiry_sweep", "input": "{{ steps.find.output }}"}), ("emit", "event.emit", {"event": "documents.expiry_sweep.completed", "payload": "{{ steps.process.output }}"})], [("start", "find"), ("find", "retry"), ("retry", "process", "attempt"), ("process", "emit")]),
    flow("missing_clockout_sweep", "Find and close stale sessions every night.", [("start", "trigger.schedule", {"cron": "0 1 * * *"}), ("run", "function.call", {"function": "attendance.close_missing_sessions", "input": {}}), ("emit", "event.emit", {"event": "attendance.missing_clockout_detected", "payload": "{{ steps.run.output }}"})], [("start", "run"), ("run", "emit")]),
    flow("leave_accrual", "Monthly leave accrual, carryover and ledger creation.", [("start", "trigger.schedule", {"cron": "5 0 1 * *"}), ("retry", "control.retry", {"attempts": 3, "base_delay": 5}), ("run", "function.call", {"function": "leave.accrue", "input": {}}), ("emit", "event.emit", {"event": "leave.accrual_completed", "payload": "{{ steps.run.output }}"})], [("start", "retry"), ("retry", "run", "attempt"), ("run", "emit")], timeout=900),
    flow("task_overdue_sweep", "Daily overdue-task reminder fan-out.", [("start", "trigger.schedule", {"cron": "0 8 * * 1-5"}), ("run", "function.call", {"function": "workspace.task_overdue", "input": {}}), ("emit", "event.emit", {"event": "task.overdue", "payload": "{{ steps.run.output }}"})], [("start", "run"), ("run", "emit")]),
]

SUBSCRIPTIONS = [
    {"name": "employee_created_fanout", "description": "Starts employee lifecycle provisioning.", "event": "employees.created", "target_type": "flow", "target": "employee_created", "condition": None, "enabled": True},
    {"name": "leave_request_fanout", "description": "Starts leave approval routing.", "event": "leave.requested", "target_type": "flow", "target": "leave_requested", "condition": None, "enabled": True},
    {"name": "expense_fanout", "description": "Starts expense approval routing.", "event": "expense.submitted", "target_type": "flow", "target": "expense_submitted", "condition": None, "enabled": True},
    {"name": "task_fanout", "description": "Delivers assigned-task notifications.", "event": "task.assigned", "target_type": "flow", "target": "task_assigned", "condition": None, "enabled": True},
    {"name": "notification_realtime", "description": "Private notification stream.", "event": "notifications.created", "target_type": "realtime", "target": "user:{{ event.user_id }}", "condition": None, "enabled": True},
]

SCHEDULES = [
    {"name": "missing_clockout_sweep", "description": "Close stale attendance sessions.", "cron": "0 1 * * *", "interval_seconds": None, "target_type": "flow", "target": "missing_clockout_sweep", "payload": {}, "enabled": True},
    {"name": "document_expiry_sweep", "description": "Warn of expiring employee documents.", "cron": "0 7 * * *", "interval_seconds": None, "target_type": "flow", "target": "document_expiry_sweep", "payload": {}, "enabled": True},
    {"name": "leave_accrual", "description": "Monthly leave accrual.", "cron": "5 0 1 * *", "interval_seconds": None, "target_type": "flow", "target": "leave_accrual", "payload": {}, "enabled": True},
    {"name": "task_overdue_sweep", "description": "Weekday task reminders.", "cron": "0 8 * * 1-5", "interval_seconds": None, "target_type": "flow", "target": "task_overdue_sweep", "payload": {}, "enabled": True},
]

BUCKETS = [
    {"name": "employee-documents", "description": "Contracts, identity and employee documents.", "public": False, "read_policy": "employee_or_hr", "write_policy": "employee_or_hr", "accepts": ["application/pdf", "image/*"], "max_bytes": 20 * 1024 * 1024, "signed_uploads": True},
    {"name": "expense-receipts", "description": "Private expense receipts.", "public": False, "read_policy": "employee_or_hr", "write_policy": "company_member", "accepts": ["application/pdf", "image/*"], "max_bytes": 15 * 1024 * 1024, "signed_uploads": True},
    {"name": "payslips", "description": "Employee-specific generated payslips.", "public": False, "read_policy": "employee_or_hr", "write_policy": "payroll_private", "accepts": ["application/pdf"], "max_bytes": 10 * 1024 * 1024, "signed_uploads": True},
    {"name": "recruitment-files", "description": "Candidate resumes and interview attachments.", "public": False, "read_policy": "recruitment_team", "write_policy": "recruitment_team", "accepts": ["application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"], "max_bytes": 20 * 1024 * 1024, "signed_uploads": True},
]

WEBHOOKS = [
    {"name": "accounting_sync", "description": "Disabled accounting export; configure a company endpoint before enabling.", "url": "https://accounting.example.invalid/peopleops", "events": ["payroll.finalized", "expense.reimbursed"], "headers": {"X-Source": "pawabase-peopleops"}, "enabled": False, "max_attempts": 8},
]

INBOUND = [
    {"slug": "payroll-provider", "name": "Payroll provider events", "description": "Verified payment results and payroll reconciliation.", "verification": "hmac-sha256", "signature_header": "x-signature", "target_type": "flow", "target": "payroll_provider_event", "enabled": True},
]

SETTINGS = {"public_docs": False, "realtime": {"allow_client_publish": False, "channels": [
    {"pattern": "organization:{{ auth.org }}", "subscribe": "company_member", "publish": "deny", "presence": True, "history": 100},
    {"pattern": "department:*", "subscribe": "manager_or_hr", "publish": "deny", "presence": True, "history": 100},
    {"pattern": "employee:*", "subscribe": "employee_or_hr", "publish": "deny", "presence": True, "history": 100},
    {"pattern": "approval:*", "subscribe": "employee_or_hr", "publish": "deny", "presence": True, "history": 100},
    {"pattern": "payroll:*", "subscribe": "payroll_private", "publish": "deny", "presence": False, "history": 30},
    {"pattern": "user:{{ auth.user_id }}", "subscribe": "employee_self", "publish": "deny", "presence": False, "history": 100},
]}}

AUTH = {"signup_enabled": False, "require_email_verification": True, "password_policy": "strong", "password_min_length": 12, "access_ttl": 900, "refresh_ttl": 2592000, "magic_link_enabled": True, "mfa_enabled": True, "default_roles": ["employee"]}


def build():
    return {"format": "pawabase.blueprint", "version": 1, "name": "PeopleOps Enterprise HR & Company Operations", "description": "A production-style, event-driven HRIS, payroll, finance, recruiting, workspace and operations platform built entirely from Pawabase definitions.", "source": {"project": "peopleops", "env": "blueprint", "generator": "examples/blueprints/peopleops/build.py"}, "definitions": {"policies": POLICIES, "resources": RESOURCES, "flows": FLOWS, "buckets": BUCKETS, "subscriptions": SUBSCRIPTIONS, "webhooks": WEBHOOKS, "inbound-hooks": INBOUND, "schedules": SCHEDULES}, "roles": ROLES, "auth": AUTH, "settings": SETTINGS, "data": {}}


if __name__ == "__main__":
    document = build()
    output = HERE / "peopleops.blueprint.json"
    output.write_text(json.dumps(document, indent=2) + "\n")
    counts = {kind: len(items) for kind, items in document["definitions"].items()}
    print(f"wrote {output.name}: {sum(counts.values())} definitions {counts}; {len(ROLES)} roles")
