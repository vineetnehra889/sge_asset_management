import frappe
from frappe import _
from frappe.utils import flt


def restrict_zero_value_consumed_assets(doc, method=None):
	"""Block save when a consumed Asset has zero value.

	erpnext.get_gl_entries_for_target_item only adds the target-asset debit
	GL entry when total_value is non-zero, and get_gl_entries_on_asset_disposal
	still emits a (now zero-value) credit entry for the consumed asset. When
	every consumed row is worth 0, the GL map collapses to a single zero-value
	entry and core throws the generic "Incorrect number of General Ledger
	Entries found" error at submit time. Catch it earlier with a message that
	names the actual asset at fault, since that error gives no such context.
	"""
	zero_value_assets = [d.asset for d in doc.get("asset_items", []) if d.asset and not flt(d.asset_value)]
	if zero_value_assets:
		frappe.throw(
			_(
				"Consumed Asset(s) {0} have zero current value and cannot be capitalized. "
				"Fix their Gross/Net Purchase Amount (e.g. via an Asset Value Adjustment) before saving."
			).format(", ".join(frappe.bold(a) for a in zero_value_assets))
		)


def enable_stock_ledger_for_preview(doc, method=None):
	"""Make the draft-state ledger preview include consumed stock items.

	erpnext.controllers.stock_controller.get_accounting_ledger_preview /
	get_stock_ledger_preview only call update_stock_ledger() before building
	the preview when `doc.get("update_stock")` is truthy or the doctype is
	one of a hard-coded tuple (Purchase Receipt, Delivery Note, Stock Entry).
	Asset Capitalization has neither, so its stock_items would be silently
	skipped. Both preview endpoints call doc.run_method("before_gl_preview"
	/ "before_sl_preview") first, so setting update_stock here is enough to
	make the existing core logic pick up consumed stock items too.
	"""
	doc.update_stock = 1
