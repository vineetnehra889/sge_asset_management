"""Backend for the Asset tree view.

Asset is not a Nested Set doctype — it has no `lft`/`rgt` and no `is_group`, and it belongs to
erpnext so it cannot be given them. Frappe does not actually require any of that: the tree view
is gated by the `treeviews` hook, and `frappe.treeview_settings` can point `get_tree_nodes` at a
method of our own. This is that method, walking `custom_parent_asset` instead of a nested set.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

from sge_asset_management.asset_management.report.fixed_asset_depreciation_schedule.fixed_asset_depreciation_schedule import execute as run_schedule

# Cancelled assets are excluded outright; a branch of cancelled records is noise in a register.
_DOCSTATUS_FILTER = ["<", 2]

# The schedule is recomputed from scratch for the whole company on every call, and the tree
# fires one call per branch the user opens. Cached briefly so opening five branches costs one
# computation rather than five.
_SCHEDULE_TTL = 300


@frappe.whitelist()
def get_asset_tree_nodes(
	doctype: str | None = None,
	parent: str | None = None,
	company: str | None = None,
	is_root: bool | str = False,
	asset_category: str | None = None,
	location: str | None = None,
	from_date: str | None = None,
	to_date: str | None = None,
) -> list[dict]:
	"""Children of `parent`, or the roots when `is_root`.

	Signature is fully annotated because several installed apps set
	`require_type_annotated_api_methods`, which rejects unannotated whitelisted methods.
	`is_root` arrives from the client as the string "true"/"false", hence the sbool.
	"""
	if isinstance(is_root, str):
		is_root = frappe.sbool(is_root)

	filters = {"docstatus": _DOCSTATUS_FILTER}
	if company:
		filters["company"] = company
	if asset_category:
		filters["asset_category"] = asset_category
	if location:
		filters["location"] = location

	if is_root or not parent:
		# A root is an asset with no parent, or one whose parent was filtered out of this
		# view — otherwise a company filter would hide a whole branch behind a missing root.
		filters["custom_parent_asset"] = ["in", [None, ""] + _out_of_scope_parents(filters)]
	else:
		filters["custom_parent_asset"] = parent

	assets = frappe.get_all(
		"Asset",
		filters=filters,
		fields=[
			"name as value",
			"asset_name",
			"asset_type",
			"status",
			"net_purchase_amount",
			"docstatus",
		],
		order_by="asset_name asc, name asc",
	)
	if not assets:
		return []

	children = _child_counts([a.value for a in assets])
	schedule = _closing_gross(company, from_date, to_date, asset_category, location)

	for asset in assets:
		# Closing Balance Gross as the Fixed Asset Depreciation Schedule computes it, so the
		# tree and the report never disagree. An asset the schedule does not cover — no WDV
		# finance book, not yet in service, still a draft — keeps its own purchase amount
		# rather than reading as worthless.
		asset.own_value = flt(schedule.get(asset.value, asset.net_purchase_amount))

	rollups = _rollup_values([a.value for a in assets if not a.own_value], filters, schedule)

	for asset in assets:
		asset.title = asset.asset_name or asset.value
		asset.expandable = 1 if children.get(asset.value) else 0
		# The default toolbar offers "Add Child" on anything expandable, and its add_node()
		# saves a bare doc — which an Asset can never be. The tree JS drops that button; this
		# flag makes the intent explicit for any other consumer of these nodes.
		asset.hide_add = 1

		# A Parent Asset holds no value of its own — its worth is what hangs under it. Show the
		# total of the first valued node on each branch below, so the top of the tree is not a
		# blank line. First valued, not every descendant: a Composite Asset already carries its
		# components' value, so adding both would count the same money twice.
		asset.display_value = asset.own_value
		asset.is_rollup = 0
		if not asset.display_value and rollups.get(asset.value):
			asset.display_value = rollups[asset.value]
			asset.is_rollup = 1

	return assets


def _closing_gross(
	company: str | None,
	from_date: str | None,
	to_date: str | None,
	asset_category: str | None,
	location: str | None,
) -> dict:
	"""{asset: closing_balance_gross} from the Fixed Asset Depreciation Schedule.

	Returns {} when there is no company to scope by — the schedule is per company and computing
	it across all of them would be both meaningless and slow.
	"""
	if not company:
		return {}

	if not from_date or not to_date:
		from_date, to_date = _fiscal_year(company)

	key = f"asset_tree_closing_gross::{company}::{from_date}::{to_date}::{asset_category or ''}::{location or ''}"
	cached = frappe.cache().get_value(key)
	if cached is not None:
		return cached

	report_filters = frappe._dict(
		company=company, from_date=getdate(from_date), to_date=getdate(to_date)
	)
	if asset_category:
		report_filters.asset_category = asset_category
	if location:
		report_filters.location = location

	# The assembled report, not the raw register rows underneath it. The report demotes a
	# Parent Asset to a heading carrying the roll-up of its branch, so its Closing Balance Gross
	# differs from the flat row for the same asset — reading the raw rows would put a different
	# number against a parent here than the report shows for it.
	_columns, rows, _message, _chart = run_schedule(report_filters)
	values = {row["asset"]: flt(row.get("closing_balance_gross")) for row in rows if row.get("asset")}

	frappe.cache().set_value(key, values, expires_in_sec=_SCHEDULE_TTL)
	return values


def _fiscal_year(company: str) -> tuple:
	from erpnext.accounts.utils import get_fiscal_year

	try:
		_name, start, end = get_fiscal_year(nowdate(), company=company, as_dict=False)
		return start, end
	except frappe.ValidationError:
		today = getdate(nowdate())
		year = today.year if today.month >= 4 else today.year - 1
		return getdate(f"{year}-04-01"), getdate(f"{year + 1}-03-31")


def _rollup_values(names: list[str], filters: dict, schedule: dict) -> dict:
	"""Total of the first valued node on each branch below each of `names`.

	Walks down level by level rather than recursing per node, so the whole subtree costs one
	query per depth instead of one per branch. A node that is itself worth something ends the
	walk on that branch — its value already accounts for everything below it, so descending
	past it would count the same money twice.
	"""
	if not names:
		return {}

	scoped = {k: v for k, v in filters.items() if k != "custom_parent_asset"}
	totals = {name: 0.0 for name in names}
	# Which root each frontier node rolls up into.
	owner = {name: name for name in names}
	frontier = list(names)
	seen = set(names)

	while frontier:
		rows = frappe.get_all(
			"Asset",
			filters={**scoped, "custom_parent_asset": ["in", frontier]},
			fields=["name", "custom_parent_asset", "net_purchase_amount"],
		)
		frontier = []
		for row in rows:
			if row.name in seen:
				continue
			seen.add(row.name)
			root = owner.get(row.custom_parent_asset)
			if not root:
				continue
			value = flt(schedule.get(row.name, row.net_purchase_amount))
			if value:
				totals[root] += value
			else:
				owner[row.name] = root
				frontier.append(row.name)

	return {name: total for name, total in totals.items() if total}


def _out_of_scope_parents(filters: dict) -> list[str]:
	"""Parents that exist but fall outside the current filters.

	An asset whose parent belongs to another company (or another category, when that filter is
	on) would otherwise never appear: it is not a root, and its parent is never rendered for it
	to hang under. Treating it as a root keeps the branch reachable.
	"""
	scoped = {k: v for k, v in filters.items() if k != "custom_parent_asset"}

	referenced = frappe.get_all(
		"Asset",
		filters={**scoped, "custom_parent_asset": ["is", "set"]},
		pluck="custom_parent_asset",
		distinct=True,
	)
	if not referenced:
		return []

	in_scope = set(
		frappe.get_all("Asset", filters={**scoped, "name": ["in", referenced]}, pluck="name")
	)
	return [name for name in referenced if name not in in_scope]


def _child_counts(names: list[str]) -> dict:
	"""How many live children each of `names` has, in one query rather than one per node.

	Raw SQL rather than get_all: this frappe rejects aggregate expressions in `fields`.
	"""
	if not names:
		return {}

	rows = frappe.db.sql(
		"""select custom_parent_asset, count(*) from `tabAsset`
		   where custom_parent_asset in %(names)s and docstatus < 2
		   group by custom_parent_asset""",
		{"names": names},
	)
	return {parent: cint(count) for parent, count in rows}


@frappe.whitelist()
def get_asset_tree_summary(company: str | None = None) -> dict:
	"""Counts for the tree's header — how much of the register is actually in a hierarchy."""
	filters = {"docstatus": _DOCSTATUS_FILTER}
	if company:
		filters["company"] = company

	total = frappe.db.count("Asset", filters)
	parented = frappe.db.count("Asset", {**filters, "custom_parent_asset": ["is", "set"]})

	return {
		"total": total,
		"parented": parented,
		"roots": total - parented,
		"label": _("{0} assets — {1} nested under a parent").format(total, parented),
	}
