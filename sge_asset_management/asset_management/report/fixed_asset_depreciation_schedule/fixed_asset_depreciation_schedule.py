from collections import Counter

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
	data = build_asset_tree(rows, filters)

	group_field = GROUP_BY_FIELDS.get(filters.get("group_by"))
	if group_field:
		data = apply_group_by(data, group_field, filters)

	return get_tree_columns(filters), data, None, get_chart(data, filters)


LEAD_COLUMNS = ("asset", "asset_description")


def get_tree_columns(filters):
	"""Same columns as the flat register, led by whatever the tree nests on.

	frappe-datatable draws the expand/collapse control on the first column only, so that column
	has to carry the tree label. Nesting on the Asset hierarchy it is the Asset link itself, with
	the name beside it; grouped by a dimension the heading rows are not Assets at all, so a plain
	text column leads and the Asset link follows it.
	"""
	columns = {c["fieldname"]: c for c in get_columns()}
	rest = [c for c in get_columns() if c["fieldname"] not in LEAD_COLUMNS]

	group_by = filters.get("group_by")
	if GROUP_BY_FIELDS.get(group_by):
		return [
			{
				"label": _("{0} / Asset").format(_(group_by)),
				"fieldname": "tree_label",
				"fieldtype": "Data",
				"width": 280,
			},
			dict(columns["asset"], width=180),
		] + rest

	return [
		dict(columns["asset"], width=200),
		dict(columns["asset_description"], label=_("Asset Name"), width=220),
	] + rest


# ─────────────────────────────────────────────────────────────────
#  Grouping and chart
# ─────────────────────────────────────────────────────────────────
# The Group By filter drives both the table and the chart. Leaving it on "Asset Hierarchy"
# nests rows the way the Assets themselves are nested, by Parent Asset; picking a dimension
# adds a heading row per distinct value with those hierarchies underneath.
GROUP_BY_FIELDS = {
	"Classification": "classification",
	"Asset Category": "ledger_name",
	"Location": "location",
}
# A register routinely holds several thousand Assets, so a chart of one bar per Asset is only
# ever a fallback — used when the table is in hierarchy mode and there is nothing else to
# compare by.
CHART_ASSET_FIELD = "asset_description"

# Past this many bars a grouped four-series chart stops being readable. The tail is summed into
# a single "Others" bar rather than dropped, so the chart still totals to the report.
CHART_LIMIT = 12

CHART_SERIES = (
	("Opening WDV", "opening_wdv", "#7cd6fd"),
	("Additions", "addition", "#4bc0a2"),
	("Depreciation for the Year", "dep_current_year", "#ff5858"),
	("Closing WDV", "closing_wdv", "#743ee2"),
)
CHART_FIELDS = tuple(fieldname for _label, fieldname, _color in CHART_SERIES)


def apply_group_by(rows, fieldname, filters):
	"""Wrap the hierarchy in one heading row per distinct value of `fieldname`.

	The dimension is read off each *root* and its whole subtree moves with it — a component
	belongs under the same heading as the asset it was capitalized into, so splitting one tree
	across headings by each row's own category would defeat the hierarchy.

	A heading totals the *leaves* beneath it rather than the rows directly under it: a submitted
	Composite Asset carries its components' value as well as the components themselves, so adding
	the roots would count that money twice. It also makes every heading and its chart bar the
	same number, by construction.
	"""
	subtrees = []
	for row in rows:
		if row["indent"] == 0:
			subtrees.append([])
		subtrees[-1].append(row)

	buckets = {}
	for subtree in subtrees:
		buckets.setdefault(subtree[0].get(fieldname) or _("Unclassified"), []).extend(subtree)

	grouped = []
	for key in sorted(buckets):
		bucket_rows = buckets[key]

		heading = {
			"tree_label": key,
			"indent": 0,
			"parent_asset": None,
			"from_date": filters.from_date,
			"to_date": filters.to_date,
			"is_group": 1,
			"is_group_header": 1,
			"remarks": _("Group total"),
		}
		roll_up(heading, leaf_rows(bucket_rows))
		grouped.append(heading)

		for row in bucket_rows:
			row["indent"] += 1
			row["tree_label"] = row.get("asset_description") or row["asset"]
			grouped.append(row)

	return grouped


def get_chart(rows, filters):
	"""Grouped bars of opening WDV → additions → depreciation → closing WDV per bucket.

	Buckets, not Assets: a company with six thousand Assets would otherwise get six thousand is Not Same UI as 
	bars, and picking the largest handful of them says nothing about the register as a whole.
	When the table is grouped the bars are its heading rows, so the two always agree.
	"""
	if GROUP_BY_FIELDS.get(filters.get("group_by")):
		buckets = [
			dict({field: flt(row.get(field)) for field in CHART_FIELDS}, key=row["tree_label"], label=row["tree_label"])
			for row in rows
			if row.get("is_group_header")
		]
	else:
		# A bar chart cannot draw a hierarchy, so aggregate the leaves by classification —
		# the same figures summed the way the Schedule II summary wants them — and drop back
		# to individual assets only when there is a single classification to compare.
		buckets = group_for_chart(rows, GROUP_BY_FIELDS["Classification"])
		if len(buckets) < 2:
			buckets = group_for_chart(rows, CHART_ASSET_FIELD)

	if not buckets:
		return None

	# Ranked by the value actually in play this year, so a fully depreciated bucket with a zero
	# closing WDV does not outrank a large addition.
	buckets.sort(key=lambda b: b["opening_wdv"] + b["addition"], reverse=True)
	shown, others = buckets[:CHART_LIMIT], buckets[CHART_LIMIT:]

	labels = chart_labels(shown)
	if others:
		labels.append(_("Others ({0})").format(len(others)))

	datasets = []
	for label, fieldname, _color in CHART_SERIES:
		values = [flt(bucket[fieldname], 2) for bucket in shown]
		if others:
			values.append(flt(sum(bucket[fieldname] for bucket in others), 2))
		datasets.append({"name": _(label), "values": values})

	return {
		"data": {"labels": labels, "datasets": datasets},
		"type": "bar",
		"colors": [color for _label, _fieldname, color in CHART_SERIES],
		"fieldtype": "Currency",
		"barOptions": {"stacked": 0},
	}


def group_for_chart(rows, fieldname):
	"""Sum the chart series over `fieldname`, one bucket per distinct value."""
	buckets = {}

	for row in leaf_rows(rows):
		if fieldname == CHART_ASSET_FIELD:
			# Two Assets can carry the same name ("Water Cooled Ducts"); they are still two
			# separate records, so key on the id and let chart_labels() tell them apart.
			key = row["asset"]
			label = row.get("asset_description") or row["asset"]
		else:
			key = label = row.get(fieldname) or _("Unclassified")

		bucket = buckets.get(key)
		if bucket is None:
			bucket = buckets[key] = dict.fromkeys(CHART_FIELDS, 0.0)
			bucket.update({"key": key, "label": label})

		for chart_field in CHART_FIELDS:
			bucket[chart_field] += flt(row.get(chart_field))

	return list(buckets.values())


def leaf_rows(rows):
	"""The rows with nothing nested under them.

	Only leaves can be summed. A row that has children either *is* a roll-up of them (a group
	row) or carries the same money as them — Asset Capitalization adds every consumed row's
	value to the Target Asset, so a submitted Composite Asset holds its components' value a
	second time. Leaves are mutually exclusive, so the totals always come to the register
	whatever shape the hierarchy takes. Depth-first order means a row has children exactly when
	the row after it sits deeper.
	"""
	return [
		row
		for index, row in enumerate(rows)
		if index + 1 >= len(rows) or rows[index + 1]["indent"] <= row["indent"]
	]


def chart_labels(buckets):
	"""Bucket labels, trimmed to fit an axis and suffixed with the key wherever two read alike."""
	counts = Counter(bucket["label"] for bucket in buckets)
	return [
		truncate(f"{bucket['label']} #{bucket['key'].rsplit('-', 1)[-1]}")
		if counts[bucket["label"]] > 1
		else truncate(bucket["label"])
		for bucket in buckets
	]


def truncate(label, length=28):
	return label if len(label) <= length else label[: length - 1] + "…"


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

	flag_restated_rows(ordered)

	return ordered


def flag_restated_rows(rows):
	"""Mark any row that carries its own figures *and* has rows nested under it.

	Asset Capitalization adds every consumed row's value to the Target Asset, so once that target
	is submitted the same money appears twice in the table — once on the composite, once itemised
	across its components. The chart sums leaves only and is unaffected; the table shows both, so
	say which rows are the restatement rather than leaving the reader to work it out.
	"""
	for index, row in enumerate(rows):
		has_children = index + 1 < len(rows) and rows[index + 1]["indent"] > row["indent"]
		if not has_children or row.get("is_group"):
			continue

		note = _("Value also itemised in components below")
		row["remarks"] = f"{row['remarks']}; {note}" if row.get("remarks") else note


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
	"""Every field is written, zero included — a currency column that reads 0.00 on detail rows
	and blank on the row totalling them looks like a rendering fault, not a nil balance."""
	for field in ROLLUP_FIELDS:
		row[field] = sum(flt(child.get(field)) for child in child_rows)
