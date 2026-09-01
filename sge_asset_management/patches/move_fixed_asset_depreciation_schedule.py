import frappe

REPORT = "Fixed Asset Depreciation Schedule"


def execute():
	"""Repoint the Fixed Asset Depreciation Schedule report at the Asset Management module.

	The report moved out of sge_sehyog_customizations into this app. A standard Report resolves
	its .py/.js path from its module's app, so a record still pointing at "SGE Sehyog" would look
	for the script under the folder that move deleted. Migrate's own JSON sync normally handles
	this, but only when it decides the file is newer than the record — this makes it certain.
	"""
	if not frappe.db.exists("Report", REPORT):
		return

	if frappe.db.get_value("Report", REPORT, "module") != "Asset Management":
		frappe.db.set_value("Report", REPORT, "module", "Asset Management", update_modified=False)
