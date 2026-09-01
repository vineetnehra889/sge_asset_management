import frappe
from frappe import _
from frappe.utils import flt, getdate

from sge_sehyog_customizations.sge_sehyog.feature.fixed_asset_register import (
	get_category_meta,
	get_columns,
	get_register_rows,
)

# Group rows (an ancestor Asset that has no depreciation schedule of its own — a Parent Asset,
# or a Composite Asset that isn't being depreciated) carry the total of everything nested under
# them. An ancestor that *does* depreciate keeps its own figures instead of a roll-up: Asset
# Capitalization already adds each consumed row's value to the Target Asset, so adding the
# components' numbers on top would count the same money twice.
ROLLUP_FIELDS = (
	"purchase_amount",
	"interest_capitalised",
	"grant_subsidy",
	"net_amount_capitalised",
	"residual_value",
	"sale_amount",
	"opening_balance",
	"addition",
	"deletion",
	"closing_balance_gross",
	"opening_balance_dep",
	"addition_current_year",
	"dep_current_year",
	"deletion_dep",
	"closing_balance_dep",
	"opening_wdv",
	"closing_wdv",
	"residual_value_recap",
	"wdv_minus_residual",
)


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.get("company"):
		frappe.throw(_("Company is mandatory"))
	if not filters.get("from_date") or not filters.get("to_date"):
		frappe.throw(_("From Date and To Date are mandatory"))

	filters.from_date = getdate(filters.from_date)
	filters.to_date = getdate(filters.to_date)

	# This schedule computes pro-rata WDV depreciation per Schedule II - applying that formula to
	# a Straight Line (or other) asset would misstate it, so such assets are excluded rather than
	# silently shown with the wrong method's numbers.
	rows = get_register_rows(filters, depreciation_methods={"Written Down Value"})
	return get_tree_columns(), build_asset_tree(rows, filters)


def get_tree_columns():
	"""Same columns as the flat register, with the Asset link promoted to the front —
	frappe-datatable draws the expand/collapse control on the first column only."""
	columns = get_columns()
	asset_column = next(c for c in columns if c["fieldname"] == "asset")
	rest = [c for c in columns if c["fieldname"] != "asset"]
	return [dict(asset_column, width=240)] + rest


# ─────────────────────────────────────────────────────────────────
#  Parent/child assembly
# ─────────────────────────────────────────────────────────────────
def build_asset_tree(rows, filters):
	"""Nest the flat register rows under their Parent Asset (`custom_parent_asset`).

	Assets that are only ancestors — a Parent Asset grouping record, or the Composite Asset a
	set of components was capitalized into — have no depreciation row of their own and so never
	come back from get_register_rows(). They are pulled in here as group rows carrying the
	roll-up of their descendants, otherwise the components below them would show up as
	unconnected top-level rows.
	"""
	if not rows:
		return []

	rows_by_asset = {row["asset"]: row for row in rows}

	# A Parent Asset is a pure grouping record with no value or depreciation of its own (see
	# feature/parent_asset.py), but Asset.set_missing_values still copies the Asset Category's
	# finance books onto it, which is enough to pull it into the register as a row of zeros.
	# Drop it here so it comes back below as a group row carrying the total of what hangs under it.
	for name in frappe.get_all(
		"Asset", filters={"name": ["in", list(rows_by_asset)], "asset_type": "Parent Asset"}, pluck="name"
	):
		rows_by_asset.pop(name, None)

	if not rows_by_asset:
		return []

	parent_of = resolve_ancestors(rows_by_asset, filters)

	children = {}
	roots = []
	for asset in parent_of:
		parent = parent_of[asset]
		if parent:
			children.setdefault(parent, []).append(asset)
		else:
			roots.append(asset)

	group_rows = build_group_rows(set(parent_of) - set(rows_by_asset), filters)
	nodes = {**rows_by_asset, **group_rows}

	def sort_key(asset):
		return ((nodes[asset].get("asset_description") or "").lower(), asset)

	ordered = []
	for root in sorted(roots, key=sort_key):
		walk(root, None, 0, nodes, children, sort_key, ordered)

	# Deepest first, so a group row's total already includes any sub-group beneath it.
	for row in reversed(ordered):
		if row.get("is_group"):
			roll_up(row, [nodes[c] for c in children.get(row["asset"], [])])

	return ordered


def walk(asset, parent, indent, nodes, children, sort_key, ordered):
	row = nodes[asset]
	row["indent"] = indent
	# `parent_asset` is what query_report.js is told to use as parent_field; frappe-datatable
	# nests off `indent`, but the tree collapse/expand and export paths read this.
	row["parent_asset"] = parent
	ordered.append(row)
	for child in sorted(children.get(asset, []), key=sort_key):
		walk(child, asset, indent + 1, nodes, children, sort_key, ordered)


def resolve_ancestors(rows_by_asset, filters):
	"""Walk `custom_parent_asset` upwards from the register rows until every ancestor is known.

	Returns {asset: parent asset or None}. An ancestor that is cancelled or belongs to another
	company is treated as absent, so its children surface as roots rather than nesting under a
	record this report has no business showing.
	"""
	parent_of = {}
	pending = set(rows_by_asset)
	seen = set()

	while pending:
		details = {
			d.name: d
			for d in frappe.db.get_all(
				"Asset", filters={"name": ["in", list(pending)]}, fields=["name", "custom_parent_asset"]
			)
		}
		next_level = set()

		for asset in pending:
			row = details.get(asset)
			parent = (row.custom_parent_asset if row else None) or None
			if parent == asset:
				parent = None
			parent_of[asset] = parent
			if parent and parent not in parent_of:
				next_level.add(parent)

		seen |= pending

		usable = set()
		if next_level:
			usable = set(
				frappe.db.get_all(
					"Asset",
					filters={
						"name": ["in", list(next_level)],
						"docstatus": ["!=", 2],
						"company": filters.company,
					},
					pluck="name",
				)
			)
		for child, parent in parent_of.items():
			if parent in next_level - usable:
				parent_of[child] = None

		pending = usable - seen

	break_cycles(parent_of)
	return parent_of


def break_cycles(parent_of):
	"""A `custom_parent_asset` loop would leave every node in it unreachable from any root.
	Cut the first link that closes a loop so the members still render, just un-nested."""
	for asset in list(parent_of):
		chain = {asset}
		node = parent_of.get(asset)
		while node is not None:
			if node in chain:
				parent_of[asset] = None
				break
			chain.add(node)
			node = parent_of.get(node)


def build_group_rows(asset_names, filters):
	"""Minimal register row for an ancestor Asset that has no depreciation figures of its own."""
	if not asset_names:
		return {}

	category_meta = get_category_meta(filters.company)
	assets = frappe.db.get_all(
		"Asset",
		filters={"name": ["in", list(asset_names)]},
		fields=["name", "asset_name", "item_name", "asset_category", "location", "available_for_use_date"],
	)

	group_rows = {}
	for asset in assets:
		category = asset.asset_category or ""
		meta = category_meta.get(category, {})
		group_rows[asset.name] = {
			"asset": asset.name,
			"from_date": filters.from_date,
			"to_date": filters.to_date,
			"nature": category,
			"ledger_name": category,
			"classification": meta.get("classification") or category,
			"location": asset.location,
			"asset_description": asset.asset_name or asset.item_name,
			"ready_to_use_date": asset.available_for_use_date,
			"remarks": _("Group total"),
			"is_group": 1,
		}
	return group_rows


def roll_up(row, child_rows):
	for field in ROLLUP_FIELDS:
		total = sum(flt(child.get(field)) for child in child_rows)
		if total:
			row[field] = total
