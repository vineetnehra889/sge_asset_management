import frappe
from frappe import _
from frappe.utils import cint, flt, getdate
from erpnext.assets.doctype.asset.asset import get_item_details
from erpnext.assets.doctype.asset_depreciation_schedule.asset_depreciation_schedule import (
	cancel_asset_depr_schedules,
)

# Each consumed table on Asset Capitalization contributes component Assets, but the fieldnames
# carrying quantity/value differ per table. Keep the differences in one place so the submit
# handler below stays a straight loop.
COMPONENT_TABLES = (
	# (table fieldname, source label, qty field, amount field)
	("stock_items", "Stock Item", "stock_qty", "amount"),
	("service_items", "Service Item", "qty", "amount"),
)


# ─────────────────────────────────────────────────────────────────
#  Composite Asset submit  →  Item + Asset per consumed row
# ─────────────────────────────────────────────────────────────────
def create_component_assets(doc, method=None):
	"""On Composite Asset submit, turn every consumed row of the Asset Capitalizations that
	built this asset into a child Asset of it.

	This hangs off the Asset rather than the Asset Capitalization on purpose. A Composite Asset
	is built up over several capitalizations while it sits in Work In Progress, and only becomes
	a real asset when it is submitted — that is the point at which its components should exist
	too, dated from the day the composite went into service. Raising them at capitalization time
	would create live component Assets under a target that is still a draft, and one per
	capitalization rather than one coherent set.

	For a Stock Item / Service Item row an Asset cannot be raised against the consumed Item
	itself (an Asset needs `is_fixed_asset`, and a stock or service Item is by definition not
	one), so a mirror fixed-asset Item is created first — `FA-<item code>` — and the Asset is
	raised against that.

	A Consumed Asset row already *is* an Asset, and it is tagged with this asset as its Parent
	Asset — both for traceability (opening the original still shows where it went) and so a
	previously capitalized Composite Asset nests under the one it was consumed into, giving the
	register a tree more than two deep. But core's own Asset Capitalization already closes that
	original asset out — set_asset_values() computes its value as of the disposal date and books
	it to the target via GL entries, then set_consumed_asset_status() marks it "Capitalized",
	after which its own depreciation schedule carries no further value. Left at that, the
	register tree would show the original nested under its new parent at ₹0 with the value it
	actually contributed nowhere on the branch. A second, value-bearing Asset is raised for it
	exactly as for a Stock/Service row — against the same Item, since the source is already a
	fixed asset — carrying the row's `asset_value` so the branch's rollup is correct.

	Every Asset produced here carries `custom_parent_asset = this asset`, which is what the
	Fixed Asset Depreciation Schedule tree report reads to nest components under the asset they
	were capitalized into.
	"""
	if doc.asset_type != "Composite Asset":
		return

	created, tagged, undepreciated = [], [], []

	for name in capitalizations_for(doc):
		capitalization = frappe.get_doc("Asset Capitalization", name)

		for table, source, qty_field, amount_field in COMPONENT_TABLES:
			for row in capitalization.get(table) or []:
				amount = flt(row.get(amount_field))
				asset_name, skipped_depreciation = _create_component_asset(
					capitalization,
					doc,
					row,
					source,
					qty=flt(row.get(qty_field)),
					amount=amount,
				)
				if asset_name:
					created.append(asset_name)
					if skipped_depreciation:
						undepreciated.append((asset_name, amount))

		for row in capitalization.get("asset_items") or []:
			if _tag_consumed_asset(doc, row):
				tagged.append(row.asset)

			# Nothing to represent when the consumed asset had already depreciated to nothing
			# before it was capitalized — an Existing Asset needs a positive Net Purchase Amount
			# (see Asset.validate_asset_values), so this would otherwise fail the whole submit.
			if flt(row.get("asset_value")):
				asset_value = flt(row.get("asset_value"))
				asset_name, skipped_depreciation = _create_component_asset(
					capitalization, doc, row, "Consumed Asset", qty=1, amount=asset_value
				)
				if asset_name:
					created.append(asset_name)
					if skipped_depreciation:
						undepreciated.append((asset_name, asset_value))

	if created or tagged:
		_convert_to_parent_asset(doc)

	_notify(doc, created, tagged)
	_warn_undepreciated(doc, undepreciated)


def _convert_to_parent_asset(target):
	"""Once its components exist, the composite stops being an asset in its own right and
	becomes the grouping record for them.

	Its value is now itemised across the Assets beneath it, so leaving it typed as a Composite
	Asset makes the register show the same money twice — once here and once spread below. As a
	Parent Asset the Fixed Asset Depreciation Schedule renders it as a heading carrying the
	roll-up of its children instead of a figure of its own.

	Calculate Depreciation goes off with it, and the schedules core activated moments ago in
	Asset.on_submit are cancelled — the charge now belongs to the components, and leaving the
	schedules live would book it on both sides. The Asset property setters hide the checkbox on
	a Parent Asset, so it cannot be switched back on afterwards.

	db_set rather than a save: neither field is allow-on-submit, and the document has just been
	submitted, so re-running validate() here would fight core's own submit handling.
	"""
	if cint(target.calculate_depreciation):
		cancel_asset_depr_schedules(target)
		target.db_set("calculate_depreciation", 0, update_modified=False)

	target.db_set("asset_type", "Parent Asset", update_modified=False)


def capitalizations_for(target):
	"""The submitted Asset Capitalizations that built this Composite Asset, oldest first."""
	return frappe.get_all(
		"Asset Capitalization",
		filters={"target_asset": target.name, "docstatus": 1},
		order_by="posting_date asc, creation asc",
		pluck="name",
	)


# ─────────────────────────────────────────────────────────────────
#  Unwinding
# ─────────────────────────────────────────────────────────────────
def cancel_component_assets_for_target(doc, method=None):
	"""On Composite Asset cancel, unwind everything its capitalizations produced.

	Runs on `on_cancel`, which frappe calls before check_no_back_links_exist() — the component
	Assets link back to this one through `custom_parent_asset`, so they have to be cancelled
	first or the cancel is refused for being linked.
	"""
	# Deliberately not keyed on asset_type: create_component_assets() retypes the target to
	# "Parent Asset" once its components exist, so checking for "Composite Asset" here would
	# skip exactly the assets that have something to unwind.
	_cancel_assets(
		frappe.get_all(
			"Asset",
			filters={
				"custom_parent_asset": doc.name,
				"custom_asset_capitalization": ["is", "set"],
				"docstatus": ["<", 2],
			},
			pluck="name",
		)
	)

	for name in capitalizations_for(doc):
		_restore_consumed_parents(frappe.get_doc("Asset Capitalization", name))


def cancel_component_assets(doc, method=None):
	"""On Asset Capitalization cancel, unwind what this one contributed.

	Reachable when a capitalization is cancelled after its target was submitted — the components
	it produced are still live and would otherwise keep value the capitalization has just taken
	back off the target.
	"""
	_cancel_assets(
		frappe.get_all(
			"Asset",
			filters={"custom_asset_capitalization": doc.name, "docstatus": ["<", 2]},
			pluck="name",
		)
	)
	_restore_consumed_parents(doc)


# Core's Asset.validate_cancellation() only lets these through. A component that has since been
# scrapped, sold or capitalized elsewhere carries disposal accounting of its own.
CANCELLABLE_STATUSES = ("Submitted", "Partially Depreciated", "Fully Depreciated")


def _cancel_assets(names):
	"""Cancel rather than delete: these are submitted documents in their own right and may
	already be referenced elsewhere (Asset Movement, Asset Activity). A component still in draft
	has no such history, so it goes.

	A component that has been disposed of since it was created is left alone — reversing it here
	would quietly undo its own disposal entries, and attempting it throws "Asset cannot be
	cancelled, as it is already Scrapped", which aborts the entire capitalization cancel. Its
	parent link is cleared instead, both so it stops blocking the parent's own cancel and
	because it is no longer part of the build being unwound.
	"""
	skipped = []

	for name in names:
		asset = frappe.get_doc("Asset", name)

		if asset.docstatus != 1:
			frappe.delete_doc("Asset", name, force=True, ignore_permissions=True)
			continue

		if asset.status not in CANCELLABLE_STATUSES:
			skipped.append((name, asset.status))
			frappe.db.set_value("Asset", name, "custom_parent_asset", None, update_modified=False)
			continue

		asset.flags.ignore_permissions = True
		asset.cancel()

	if skipped:
		frappe.msgprint(
			_("Left in place because they can no longer be cancelled: {0}. They are detached from their Parent Asset — reverse them yourself if that is wrong.").format(
				", ".join(f"{_asset_link(name)} ({status})" for name, status in skipped)
			),
			title=_("Component Assets Not Reversed"),
			indicator="orange",
		)


def _restore_consumed_parents(capitalization):
	"""Put each Consumed Asset back under the Parent Asset it had before this capitalization
	moved it, rather than leaving it orphaned at the top of the tree."""
	if not capitalization.target_asset:
		return

	for row in capitalization.get("asset_items") or []:
		if not row.asset:
			continue
		if frappe.db.get_value("Asset", row.asset, "custom_parent_asset") != capitalization.target_asset:
			continue

		frappe.db.set_value(
			"Asset",
			row.asset,
			"custom_parent_asset",
			row.get("custom_previous_parent_asset") or None,
			update_modified=False,
		)


# ─────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────
def _create_component_asset(capitalization, target, row, source, qty, amount):
	"""Returns `(asset_name, skipped_depreciation)`. `skipped_depreciation` is True when the
	component was created without depreciation solely because the target's own Calculate
	Depreciation happened to be off at submit time, despite the component carrying real cost —
	see `_apply_depreciation()`."""
	if not row.item_code:
		return None, False

	# Re-submitting the target, or amending it, must not raise the same component twice.
	if frappe.db.exists(
		"Asset",
		{
			"custom_asset_capitalization": capitalization.name,
			"custom_capitalization_row": row.name,
			"docstatus": ["<", 2],
		},
	):
		return None, False

	if not target.location:
		frappe.throw(
			_("Asset {0} has no Location set — component Assets cannot be created without one.").format(
				frappe.bold(target.name)
			)
		)

	purchase_date = getdate(capitalization.posting_date)
	# The components enter service with the composite they are part of, not on the day their
	# cost happened to be booked — and never before that cost was incurred.
	in_use_date = max(getdate(target.available_for_use_date or purchase_date), purchase_date)

	asset_category = _resolve_asset_category(row.item_code, target)
	component_item = get_or_create_component_item(row.item_code, asset_category)

	asset = frappe.new_doc("Asset")
	asset.update(
		{
			"company": target.company,
			"item_code": component_item,
			"asset_name": _component_asset_name(target, row),
			"asset_category": asset_category,
			# "Existing Asset" is the only type core exempts from needing a Purchase
			# Receipt/Invoice while still allowing depreciation — "Composite Component" would
			# block depreciation (see the Asset property setters) and would also re-enter
			# append_composite_component_to_asset_capitalization on submit.
			"asset_type": "Existing Asset",
			"asset_quantity": cint(qty) or 1,
			"location": target.location,
			"cost_center": row.get("cost_center") or target.cost_center or capitalization.cost_center,
			"purchase_date": purchase_date,
			"available_for_use_date": in_use_date,
			"net_purchase_amount": amount,
			"custom_parent_asset": target.name,
			"custom_asset_capitalization": capitalization.name,
			"custom_capitalization_row": row.name,
			"custom_component_source": source,
		}
	)
	skipped_depreciation = _apply_depreciation(asset, target, component_item, asset_category, amount, in_use_date)
	asset.flags.ignore_permissions = True
	asset.insert()
	asset.submit()

	return asset.name, skipped_depreciation


def _component_asset_name(target, item_row):
	"""`<composite> - <item>`, so a component reads as belonging to something rather than being
	one of several identically named Assets across the register. Asset Name is a Data field, so
	the result is clipped to its 140-character limit."""
	return f"{target.asset_name} - {item_row.item_name or item_row.item_code}"[:140]


def _tag_consumed_asset(target, row):
	"""Point an already-existing Consumed Asset at the Target Asset as its parent.

	Consuming an asset that is itself a capitalized Composite Asset moves it under its new
	target, so whatever parent it had before is stashed on the row first — cancelling has to put
	it back where it was, not just leave it orphaned.

	db_set on the submitted Asset rather than a save: `custom_parent_asset` is not
	allow-on-submit, and the consumed asset has already been marked Capitalized by core's
	set_consumed_asset_status(), so re-validating it here would be both pointless and fragile.
	"""
	if not row.asset:
		return False

	previous_parent = frappe.db.get_value("Asset", row.asset, "custom_parent_asset")
	if previous_parent == target.name:
		return False

	frappe.db.set_value(
		"Asset Capitalization Asset Item",
		row.name,
		"custom_previous_parent_asset",
		previous_parent,
		update_modified=False,
	)
	frappe.db.set_value("Asset", row.asset, "custom_parent_asset", target.name, update_modified=False)
	return True


def get_or_create_component_item(source_item_code, asset_category):
	"""Return a fixed-asset Item usable as the component Asset's `item_code`.

	A source Item that is already a fixed-asset Item is used as-is. Anything else gets a mirrored
	`FA-<item code>` Item — non-stock and fixed-asset, which is what Item.validate_fixed_asset
	demands — created once and reused on later capitalizations of the same item.
	"""
	source = frappe.get_cached_doc("Item", source_item_code)
	if source.is_fixed_asset:
		return source.name

	component_code = f"FA-{source.name}"[:140]
	if frappe.db.exists("Item", component_code):
		return component_code

	# Item Tax rows are mandatory on this bench (a Property Setter marks Item.taxes reqd), and
	# the mirror below carries the source's rows across. With none to copy the mirror is
	# unsaveable, and letting it fail inside item.insert() surfaces as a bare MandatoryError
	# against "FA-<code>" — a name the user never entered and would not recognise as the cause.
	if not source.get("taxes"):
		frappe.throw(
			_(
				"Item {0} has no Item Tax Template set, so a fixed-asset copy of it cannot be "
				"created for capitalization. Add a tax row on the Item and try again."
			).format(frappe.bold(source.name))
		)

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
	# Item Tax rows are mandatory on this bench (a Property Setter marks Item.taxes reqd), and a
	# component keeps the tax treatment of the thing it was built from — copy the source's rows
	# across rather than leaving the mirror Item unsaveable.
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
			_("Cannot determine an Asset Category for Item {0} — set one on the Item or on Asset {1}.").format(
				frappe.bold(item_code), frappe.bold(target.name)
			)
		)
	return category


def _apply_depreciation(asset, target, item_code, asset_category, amount, in_use_date):
	"""A component depreciates exactly when the composite it was built into said it should.

	Calculate Depreciation is ticked on the Composite Asset while it is being built; on submit
	that decision moves down here, to the Assets that actually hold the value, and is switched
	off on the composite itself (see _convert_to_parent_asset). One tick, applied at the level
	where depreciation belongs, so the charge is never booked on both.

	The finance book rows have to be seeded here rather than left to Asset.set_missing_values —
	validate() runs validate_asset_values() (which rejects calculate_depreciation with no books)
	several steps before set_missing_values() would fill them in.

	Returns True when a component carrying real cost was left without depreciation purely
	because the target's Calculate Depreciation happened to be off at submit time. That flag is
	a statement about the composite as a single line item, not about whether the value now being
	spread across its components should depreciate — copying it verbatim here is the best default
	available (the alternative, inferring intent from the Asset Category, is no more reliable),
	but a component with money attached and no depreciation schedule is invisible to registers
	like the Fixed Asset Depreciation Schedule, so the caller surfaces this rather than letting it
	pass silently. False in every other case, including the legitimate "category doesn't
	depreciate" and "off on purpose, zero value" ones below.
	"""
	if not cint(target.calculate_depreciation):
		return bool(flt(amount))
	if frappe.db.get_value("Asset Category", asset_category, "non_depreciable_category"):
		return False

	books = [b for b in get_item_details(item_code, asset_category, amount) if b.get("depreciation_method")]
	if not books:
		return False

	for book in books:
		# Core defaults this to today, which trips the "cannot be before Available-for-use Date"
		# check whenever the composite goes into service on a future date.
		book["depreciation_start_date"] = in_use_date

	asset.calculate_depreciation = 1
	asset.set("finance_books", books)
	return False


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


def _warn_undepreciated(target, undepreciated):
	"""Flag components that carry real cost but were created without depreciation because
	`target.calculate_depreciation` was off at submit time (see `_apply_depreciation`).

	Separate, orange msgprint rather than folding into `_notify()`'s green one: this is not part
	of the "here's what was built" confirmation, it's a heads-up that something worth a second
	look happened — mirrors how `_cancel_assets()` keeps its own "not reversed" warning apart from
	the normal flow.
	"""
	if not undepreciated:
		return

	frappe.msgprint(
		_(
			"{0} has Calculate Depreciation off, so these component Asset(s) were created without "
			"a depreciation schedule even though they carry cost: {1}. If that cost should "
			"depreciate, enable Calculate Depreciation on them directly."
		).format(
			frappe.bold(target.name),
			", ".join(f"{_asset_link(name)} ({frappe.format(amount, {'fieldtype': 'Currency'})})" for name, amount in undepreciated),
		),
		title=_("Components Created Without Depreciation"),
		indicator="orange",
	)


def _asset_link(name):
	return f'<a href="/app/asset/{name}"><b>{name}</b></a>'
