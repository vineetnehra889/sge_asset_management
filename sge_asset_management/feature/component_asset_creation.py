import frappe
from frappe import _
from frappe.utils import cint, flt
from erpnext.assets.doctype.asset.asset import get_item_details

# Each consumed table on Asset Capitalization contributes component Assets, but the
# fieldnames carrying quantity/value differ per table. Keep the differences in one
# place so the submit handler below stays a straight loop.
COMPONENT_TABLES = (
	# (table fieldname, source label, qty field, amount field)
	("stock_items", "Stock Item", "stock_qty", "amount"),
	("service_items", "Service Item", "qty", "amount"),
)


# ─────────────────────────────────────────────────────────────────
#  Asset Capitalization submit  →  Item + Asset per consumed row
# ─────────────────────────────────────────────────────────────────
def create_component_assets(doc, method=None):
	"""On Asset Capitalization submit, turn every consumed row into a child Asset
	of the Target Asset.

	For a Stock Item / Service Item row an Asset cannot be raised against the
	consumed Item itself (an Asset needs `is_fixed_asset`, and a stock or service
	Item is by definition not one), so a mirror fixed-asset Item is created first
	— `FA-<item code>` — and the Asset is raised against that.

	A Consumed Asset row already *is* an Asset, so no Item/Asset is created for it;
	it is only tagged with the Target Asset as its Parent Asset. Creating a second
	Asset for it would duplicate the same asset in the register.

	Every Asset produced here carries `custom_parent_asset = target asset`, which is
	what the Fixed Asset Depreciation Schedule tree report reads to nest components
	under the asset they were capitalized into.
	"""
	if not doc.target_asset:
		return

	target = frappe.get_doc("Asset", doc.target_asset)
	created, tagged = [], []

	for table, source, qty_field, amount_field in COMPONENT_TABLES:
		for row in doc.get(table) or []:
			asset_name = _create_component_asset(
				doc,
				target,
				row,
				source,
				qty=flt(row.get(qty_field)),
				amount=flt(row.get(amount_field)),
			)
			if asset_name:
				created.append(asset_name)

	for row in doc.get("asset_items") or []:
		if _tag_consumed_asset(target, row):
			tagged.append(row.asset)

	_notify(target, created, tagged)


# ─────────────────────────────────────────────────────────────────
#  Asset Capitalization cancel  →  unwind what submit created
# ─────────────────────────────────────────────────────────────────
def cancel_component_assets(doc, method=None):
	"""On Asset Capitalization cancel, cancel the component Assets this document
	created and drop the Parent Asset tag off the Consumed Assets it tagged, so a
	cancelled capitalization leaves no live children hanging off the Target Asset.

	The component Assets are cancelled rather than deleted — they are submitted
	documents in their own right and may already be referenced elsewhere (Asset
	Movement, Asset Activity). Core's `on_cancel` lists "Asset" in
	`ignore_linked_doctypes`, so this runs after the capitalization itself is
	already cancelled and nothing blocks on the link.
	"""
	for name in frappe.get_all(
		"Asset",
		filters={"custom_asset_capitalization": doc.name, "docstatus": ["<", 2]},
		pluck="name",
	):
		asset = frappe.get_doc("Asset", name)
		if asset.docstatus == 1:
			asset.flags.ignore_permissions = True
			asset.cancel()
		else:
			frappe.delete_doc("Asset", name, force=True, ignore_permissions=True)

	if doc.target_asset:
		for row in doc.get("asset_items") or []:
			if row.asset and frappe.db.get_value("Asset", row.asset, "custom_parent_asset") == doc.target_asset:
				frappe.db.set_value("Asset", row.asset, "custom_parent_asset", None, update_modified=False)


# ─────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────
def _create_component_asset(doc, target, row, source, qty, amount):
	if not row.item_code:
		return None

	# Amending or re-running the hook must not raise the same component twice.
	if frappe.db.exists(
		"Asset",
		{"custom_asset_capitalization": doc.name, "custom_capitalization_row": row.name, "docstatus": ["<", 2]},
	):
		return None

	if not target.location:
		frappe.throw(
			_("Target Asset {0} has no Location set — component Assets cannot be created without one.").format(
				frappe.bold(target.name)
			)
		)

	asset_category = _resolve_asset_category(row.item_code, target)
	component_item = get_or_create_component_item(row.item_code, asset_category)

	asset = frappe.new_doc("Asset")
	asset.update(
		{
			"company": doc.company,
			"item_code": component_item,
			"asset_name": row.item_name or row.item_code,
			"asset_category": asset_category,
			# "Existing Asset" is the only type core exempts from needing a Purchase
			# Receipt/Invoice while still allowing depreciation — "Composite Component"
			# would block depreciation (see the Asset property setters) and would also
			# re-enter append_composite_component_to_asset_capitalization on submit.
			"asset_type": "Existing Asset",
			"asset_quantity": cint(qty) or 1,
			"location": target.location,
			"cost_center": row.get("cost_center") or target.cost_center or doc.cost_center,
			"purchase_date": doc.posting_date,
			"available_for_use_date": doc.posting_date,
			"net_purchase_amount": amount,
			"custom_parent_asset": target.name,
			"custom_asset_capitalization": doc.name,
			"custom_capitalization_row": row.name,
			"custom_component_source": source,
		}
	)
	_apply_depreciation(asset, target, component_item, asset_category, amount)
	asset.flags.ignore_permissions = True
	asset.insert()
	asset.submit()

	return asset.name


def _tag_consumed_asset(target, row):
	"""Point an already-existing Consumed Asset at the Target Asset as its parent.

	db_set on the submitted Asset rather than a save — `custom_parent_asset` is not
	allow-on-submit, and the consumed asset has just been marked Capitalized by
	core's set_consumed_asset_status(), so re-validating it here would be both
	pointless and fragile.
	"""
	if not row.asset:
		return False
	if frappe.db.get_value("Asset", row.asset, "custom_parent_asset") == target.name:
		return False

	frappe.db.set_value("Asset", row.asset, "custom_parent_asset", target.name, update_modified=False)
	return True


def get_or_create_component_item(source_item_code, asset_category):
	"""Return a fixed-asset Item usable as the component Asset's `item_code`.

	A source Item that is already a fixed-asset Item is used as-is. Anything else
	gets a mirrored `FA-<item code>` Item — non-stock and fixed-asset, which is what
	Item.validate_fixed_asset demands — created once and reused on later
	capitalizations of the same item.
	"""
	source = frappe.get_cached_doc("Item", source_item_code)
	if source.is_fixed_asset:
		return source.name

	component_code = f"FA-{source.name}"[:140]
	if frappe.db.exists("Item", component_code):
		return component_code

	item = frappe.new_doc("Item")
	item.update(
		{
			"item_code": component_code,
			"item_name": source.item_name,
			"description": source.description or source.item_name,
			"item_group": source.item_group,
			"stock_uom": source.stock_uom,
			"gst_hsn_code": source.get("gst_hsn_code"),
			"is_fixed_asset": 1,
			"is_stock_item": 0,
			"asset_category": asset_category,
		}
	)
	# Item Tax rows are mandatory on this bench (a Property Setter marks Item.taxes reqd), and
	# a component keeps the tax treatment of the thing it was built from — copy the source's
	# rows across rather than leaving the mirror Item unsaveable.
	for tax in source.get("taxes") or []:
		item.append(
			"taxes",
			{
				"item_tax_template": tax.item_tax_template,
				"tax_category": tax.tax_category,
				"valid_from": tax.valid_from,
				"minimum_net_rate": tax.minimum_net_rate,
				"maximum_net_rate": tax.maximum_net_rate,
			},
		)
	item.flags.ignore_permissions = True
	item.insert()

	return item.name


def _resolve_asset_category(item_code, target):
	category = frappe.get_cached_value("Item", item_code, "asset_category") or target.asset_category
	if not category:
		frappe.throw(
			_("Cannot determine an Asset Category for Item {0} — set one on the Item or on Target Asset {1}.").format(
				frappe.bold(item_code), frappe.bold(target.name)
			)
		)
	return category


def _apply_depreciation(asset, target, item_code, asset_category, amount):
	"""Depreciate a component only when the Target Asset itself does not.

	Asset Capitalization adds every consumed row's value to the Target Asset's own net purchase
	amount (update_target_asset), so when the target is being depreciated as a whole,
	depreciating the components as well would book the same charge twice. Otherwise the
	component picks up the Asset Category's finance-book defaults.

	The finance book rows have to be seeded here rather than left to Asset.set_missing_values —
	validate() runs validate_asset_values() (which rejects calculate_depreciation with no books)
	several steps before set_missing_values() would fill them in.
	"""
	if cint(target.calculate_depreciation):
		return
	if frappe.db.get_value("Asset Category", asset_category, "non_depreciable_category"):
		return

	books = [b for b in get_item_details(item_code, asset_category, amount) if b.get("depreciation_method")]
	if not books:
		return

	asset.calculate_depreciation = 1
	asset.set("finance_books", books)


def _notify(target, created, tagged):
	if not created and not tagged:
		return

	lines = []
	if created:
		lines.append(
			_("Created {0} component Asset(s) under {1}: {2}").format(
				len(created), frappe.bold(target.name), ", ".join(_asset_link(a) for a in created)
			)
		)
	if tagged:
		lines.append(
			_("Tagged {0} as Parent Asset on {1}").format(
				frappe.bold(target.name), ", ".join(_asset_link(a) for a in tagged)
			)
		)

	frappe.msgprint("<br>".join(lines), title=_("Component Assets"), indicator="green")


def _asset_link(name):
	return f'<a href="/app/asset/{name}"><b>{name}</b></a>'
