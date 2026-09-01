from frappe.utils import getdate, nowdate


def set_defaults_for_parent_asset(doc, method=None):
	"""Parent Asset is a lightweight grouping/hierarchy record — it isn't
	purchased or depreciated on its own, so Purchase Receipt/Invoice,
	Maintenance Required, Calculate Depreciation and the Purchase/Available
	for Use dates are hidden on the form (see asset.json property setters).
	Purchase Date and Available for Use Date are still read by core Asset
	logic, so default them to the record's creation date instead of asking
	the user to fill them in.
	"""
	if doc.asset_type != "Parent Asset":
		return

	doc.maintenance_required = 0
	doc.calculate_depreciation = 0
	doc.purchase_receipt = None
	doc.purchase_invoice = None

	creation_date = getdate(doc.creation) if doc.creation else getdate(nowdate())
	if not doc.purchase_date:
		doc.purchase_date = creation_date
	if not doc.available_for_use_date:
		doc.available_for_use_date = creation_date
