import frappe

def validate(doc,method):
    for task in doc.asset_maintenance_tasks:
        if frappe.utils.getdate(task.next_due_date) < frappe.utils.getdate(task.last_completion_date):
            frappe.throw("Last Completion Date cannot be greater than Next Due Date in Asset Maintenance.")
