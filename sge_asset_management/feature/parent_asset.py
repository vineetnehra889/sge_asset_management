import frappe
from frappe import _
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


def restrict_parent_asset_cancellation(doc, method=None):
	"""A submitted Parent Asset can never be cancelled.

	It is a pure grouping record — everything nested under it links back only through
	custom_parent_asset, a link core's own "still linked" check does not know about (Asset is
	not in core's dynamic-link map for Asset). Cancelling it would leave every child asset
	pointing at a record that no longer means anything, with nothing to say so. The rule is
	unconditional rather than "only while it has children": a Parent Asset carries no value and
	no accounting of its own, so there is nothing a cancel could legitimately undo.

	A Parent Asset created in error is still removable as a draft — delete works normally
	before it is ever submitted, and never reaches this hook.
	"""
	if doc.asset_type != "Parent Asset":
		return

	frappe.throw(
		_(
			"{0} is a Parent Asset and cannot be cancelled — it carries no value or accounting "
			"of its own for a cancellation to reverse. To remove it, reparent the Assets nested "
			"under it first and ask an Administrator to delete the record directly."
		).format(frappe.bold(doc.name))
	)
