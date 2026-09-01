"""Worked example of the Asset Capitalization → component Asset tree, for Nexity Tech.

Builds one complete hierarchy, four levels deep, so the Fixed Asset Depreciation Schedule has
something to show in tree form:

    NT Data Centre Block                Parent Asset     — grouping record, no value of its own
    └── NT Data Centre Hall             Composite Asset  — target of the outer capitalization
        ├── In-Row Cooling Unit         from a Stock Item row
        ├── Hall Commissioning …        from a Service Item row
        └── NT Server Rack Cluster      Composite Asset  — itself capitalized, then consumed
            ├── Legacy UPS 10 kVA       consumed Asset
            ├── Rack Commissioning …    from a Service Item row
            ├── Rack PDU Module 32A     from a Stock Item row
            └── Server Rack Frame 42U   from a Stock Item row

The third level is the interesting one: NT Server Rack Cluster is built and submitted as a
Composite Asset in its own right, and is then consumed as a Consumed Asset by the capitalization
that builds NT Data Centre Hall. That re-parents it — and everything already hanging off it —
under the Hall, which is what gives the register a tree deeper than two.

Every Asset below a Composite Asset is produced by
sge_asset_management.feature.component_asset_creation when that Composite Asset is *submitted*;
this script only sets up the masters, the capitalizations, and the submits.

Run with:

    bench --site mysite.local execute \\
        sge_asset_management.demo.nexity_tech_asset_tree.create_example_entries

Re-running is safe: every record is looked up before it is created. To rebuild from scratch, run
remove_example_entries() first.
"""

import frappe
from frappe.utils import flt, nowdate
from erpnext.assets.doctype.asset.asset import get_item_details

COMPANY = "Nexity Tech"
ASSET_CATEGORY = "NT Data Centre Equipment"
# 25 years at 5% residual — the Schedule II shape the Fixed Asset Depreciation Schedule is
# built around. A category with 0% salvage makes core solve the WDV rate to a flat 100%, which
# would make the example unreadable.
USEFUL_LIFE_MONTHS = 300
SALVAGE_PERCENTAGE = 5.0
FIXED_ASSET_ACCOUNT = "Asset Account - NT"
ACCUMULATED_DEPRECIATION_ACCOUNT = "Accumulated Depreciation - NT"
DEPRECIATION_EXPENSE_ACCOUNT = "Depreciation Expense - NT"

LOCATION = "Nelore"
WAREHOUSE = "Nelore - NT"
COST_CENTER = "Main - NT"
# Branch is mandatory on Stock Entry Detail on this bench.
BRANCH = "Nelore"
INVENTORY_ACCOUNT = "Stock In Hand - NT"
EXPENSE_PARENT = "Expenses - NT"
SERVICE_EXPENSE_ACCOUNT = "Asset Installation Charges - NT"

# india_compliance makes HSN/SAC mandatory on every Item and GST Settings here requires at
# least 6 digits, so the example masters carry a plausible full-length code: 84716030 (data
# processing units) for the hardware, 998719 (installation/maintenance services) for the
# commissioning service.
GOODS_HSN = "84716030"
SERVICE_SAC = "998719"

# Item.description and Item.taxes are both marked mandatory on this bench (Property Setters),
# so every example Item needs a specification line and a tax template.
ITEM_TAX_TEMPLATE = "11% - NT"

PARENT_ASSET = {"item_code": "FA-NT-DATA-CENTRE", "asset_name": "NT Data Centre Block"}
OUTER_ASSET = {"item_code": "FA-NT-DATA-HALL", "asset_name": "NT Data Centre Hall"}
INNER_ASSET = {"item_code": "FA-NT-SERVER-RACK", "asset_name": "NT Server Rack Cluster"}
CONSUMED_ASSET = {"item_code": "FA-NT-LEGACY-UPS", "asset_name": "Legacy UPS 10 kVA", "amount": 120000}

INNER_STOCK_COMPONENTS = [
	{"item_code": "NT-RACK-FRAME", "item_name": "Server Rack Frame 42U", "qty": 2, "rate": 45000},
	{"item_code": "NT-PDU-MODULE", "item_name": "Rack PDU Module 32A", "qty": 4, "rate": 18500},
]
INNER_SERVICE_COMPONENTS = [
	{"item_code": "NT-COMMISSIONING", "item_name": "Rack Commissioning & Cabling", "qty": 1, "rate": 75000},
]
OUTER_STOCK_COMPONENTS = [
	{"item_code": "NT-COOLING-UNIT", "item_name": "In-Row Cooling Unit", "qty": 1, "rate": 210000},
]
OUTER_SERVICE_COMPONENTS = [
	{"item_code": "NT-HALL-CERT", "item_name": "Hall Commissioning & Certification", "qty": 1, "rate": 95000},
]

ALL_STOCK_COMPONENTS = INNER_STOCK_COMPONENTS + OUTER_STOCK_COMPONENTS
ALL_SERVICE_COMPONENTS = INNER_SERVICE_COMPONENTS + OUTER_SERVICE_COMPONENTS


def create_example_entries():
	posting_date = nowdate()

	ensure_location()
	ensure_service_expense_account()
	ensure_asset_category()
	ensure_items()
	receive_stock(posting_date)

	parent = ensure_asset(
		PARENT_ASSET["asset_name"], PARENT_ASSET["item_code"], "Parent Asset", 0, posting_date, submit=True
	)
	consumed = ensure_asset(
		CONSUMED_ASSET["asset_name"],
		CONSUMED_ASSET["item_code"],
		"Existing Asset",
		CONSUMED_ASSET["amount"],
		posting_date,
		submit=True,
		calculate_depreciation=True,
	)

	# ── Level 1: build and submit the rack cluster ──────────────────────────────
	inner = ensure_asset(
		INNER_ASSET["asset_name"],
		INNER_ASSET["item_code"],
		"Composite Asset",
		0,
		posting_date,
		parent_asset=parent,
	)
	inner_cap = build_capitalization(
		inner, INNER_ASSET["item_code"], INNER_STOCK_COMPONENTS, INNER_SERVICE_COMPONENTS, [consumed], posting_date
	)
	# Submitting the composite is what raises its components — and it has to be submitted before
	# the outer capitalization below can consume it (core rejects a Draft Consumed Asset).
	submit_asset(inner)

	# ── Level 2: consume the whole rack cluster into the hall ───────────────────
	outer = ensure_asset(
		OUTER_ASSET["asset_name"],
		OUTER_ASSET["item_code"],
		"Composite Asset",
		0,
		posting_date,
		parent_asset=parent,
	)
	outer_cap = build_capitalization(
		outer, OUTER_ASSET["item_code"], OUTER_STOCK_COMPONENTS, OUTER_SERVICE_COMPONENTS, [inner], posting_date
	)
	submit_asset(outer)

	frappe.db.commit()

	print(f"Parent Asset          : {parent}")
	print(f"Outer Composite Asset : {outer}   (capitalization {outer_cap})")
	print(f"Inner Composite Asset : {inner}   (capitalization {inner_cap})")
	print(f"Consumed Asset        : {consumed}")
	print_tree(parent, 0)

	return outer


def print_tree(asset, depth):
	for name, asset_name in frappe.get_all(
		"Asset",
		filters={"custom_parent_asset": asset, "docstatus": ["<", 2]},
		fields=["name", "asset_name"],
		order_by="asset_name",
		as_list=True,
	):
		print(f"{'    ' * (depth + 1)}└── {name}  {asset_name}")
		print_tree(name, depth + 1)


# ─────────────────────────────────────────────────────────────────
#  Masters
# ─────────────────────────────────────────────────────────────────
def ensure_location():
	if not frappe.db.exists("Location", LOCATION):
		frappe.get_doc({"doctype": "Location", "location_name": LOCATION}).insert(ignore_permissions=True)


def ensure_service_expense_account():
	"""Nexity Tech's chart has no plain expense account fit for installation charges, and the
	Consumed Service Item row needs one that isn't a stock/depreciation control account."""
	if frappe.db.exists("Account", SERVICE_EXPENSE_ACCOUNT):
		return

	frappe.get_doc(
		{
			"doctype": "Account",
			"account_name": "Asset Installation Charges",
			"parent_account": EXPENSE_PARENT,
			"company": COMPANY,
			"root_type": "Expense",
			"report_type": "Profit and Loss",
			"account_type": "Expense Account",
			"is_group": 0,
		}
	).insert(ignore_permissions=True)


def ensure_asset_category():
	"""A category of its own keeps the example self-contained — no shared master is reshaped to
	make the numbers come out, and the WDV rate below lands at a realistic ~11.29%."""
	if frappe.db.exists("Asset Category", ASSET_CATEGORY):
		return ASSET_CATEGORY

	category = frappe.get_doc(
		{
			"doctype": "Asset Category",
			"asset_category_name": ASSET_CATEGORY,
			"custom_depreciation_classification": "Plant and Machinery",
			"finance_books": [
				{
					"depreciation_method": "Written Down Value",
					"total_number_of_depreciations": USEFUL_LIFE_MONTHS,
					"frequency_of_depreciation": 1,
					"salvage_value_percentage": SALVAGE_PERCENTAGE,
				}
			],
			"accounts": [
				{
					"company_name": COMPANY,
					"fixed_asset_account": FIXED_ASSET_ACCOUNT,
					"accumulated_depreciation_account": ACCUMULATED_DEPRECIATION_ACCOUNT,
					"depreciation_expense_account": DEPRECIATION_EXPENSE_ACCOUNT,
				}
			],
		}
	)
	category.insert(ignore_permissions=True)
	return category.name


def ensure_items():
	for spec in ALL_STOCK_COMPONENTS:
		ensure_item(
			spec["item_code"], spec["item_name"], is_stock_item=1, item_group="Raw Material", stock_defaults=True
		)
	for spec in ALL_SERVICE_COMPONENTS:
		ensure_item(
			spec["item_code"], spec["item_name"], is_stock_item=0, item_group="Services", hsn=SERVICE_SAC
		)
	for spec in (PARENT_ASSET, OUTER_ASSET, INNER_ASSET, CONSUMED_ASSET):
		ensure_item(
			spec["item_code"], spec["asset_name"], is_stock_item=0, item_group="Fixed Assets", is_fixed_asset=1
		)


def ensure_item(
	item_code, item_name, is_stock_item, item_group, is_fixed_asset=0, hsn=GOODS_HSN, stock_defaults=False
):
	if frappe.db.exists("Item", item_code):
		return item_code

	item = frappe.get_doc(
		{
			"doctype": "Item",
			"item_code": item_code,
			"item_name": item_name,
			"item_group": item_group,
			"stock_uom": "Nos",
			"is_stock_item": is_stock_item,
			"is_fixed_asset": is_fixed_asset,
			"asset_category": ASSET_CATEGORY if is_fixed_asset else None,
			"gst_hsn_code": hsn,
			"description": item_name,
			"taxes": [{"item_tax_template": ITEM_TAX_TEMPLATE}],
		}
	)
	if stock_defaults:
		# Nexity Tech has item-wise inventory accounting on, so a stock item with no Item
		# Default inventory account cannot post a stock GL entry at all (see
		# StockController.get_inventory_account_dict).
		item.append(
			"item_defaults",
			{
				"company": COMPANY,
				"default_warehouse": WAREHOUSE,
				"default_inventory_account": INVENTORY_ACCOUNT,
				"buying_cost_center": COST_CENTER,
			},
		)
	item.insert(ignore_permissions=True)
	return item.name


def receive_stock(posting_date):
	"""Top the example stock items up to the quantity the capitalizations consume — each
	consumed row posts a stock ledger entry and negative stock is off on this site."""
	shortfalls = []
	for spec in ALL_STOCK_COMPONENTS:
		on_hand = flt(
			frappe.db.get_value(
				"Bin", {"item_code": spec["item_code"], "warehouse": WAREHOUSE}, "actual_qty"
			)
		)
		if on_hand < spec["qty"]:
			shortfalls.append((spec, spec["qty"] - on_hand))

	if not shortfalls:
		return

	entry = frappe.new_doc("Stock Entry")
	entry.update({"stock_entry_type": "Material Receipt", "company": COMPANY, "posting_date": posting_date})
	for spec, qty in shortfalls:
		entry.append(
			"items",
			{
				"item_code": spec["item_code"],
				"qty": qty,
				"t_warehouse": WAREHOUSE,
				"basic_rate": spec["rate"],
				"cost_center": COST_CENTER,
				"branch": BRANCH,
			},
		)
	entry.insert(ignore_permissions=True)
	entry.submit()
	return entry.name


def ensure_asset(
	asset_name,
	item_code,
	asset_type,
	amount,
	posting_date,
	parent_asset=None,
	submit=False,
	calculate_depreciation=False,
):
	existing = frappe.db.exists(
		"Asset", {"asset_name": asset_name, "company": COMPANY, "docstatus": ["<", 2]}
	)
	if existing:
		return existing

	asset = frappe.new_doc("Asset")
	asset.update(
		{
			"company": COMPANY,
			"item_code": item_code,
			"asset_name": asset_name,
			"asset_category": ASSET_CATEGORY,
			"asset_type": asset_type,
			"location": LOCATION,
			"cost_center": COST_CENTER,
			"purchase_date": posting_date,
			"available_for_use_date": posting_date,
			"net_purchase_amount": amount,
			"custom_parent_asset": parent_asset,
			"calculate_depreciation": 1 if calculate_depreciation else 0,
		}
	)
	if calculate_depreciation:
		# Asset.validate_asset_values() rejects calculate_depreciation with no finance books and
		# runs before set_missing_values() would fill them in, so seed them from the category.
		asset.set("finance_books", get_item_details(item_code, ASSET_CATEGORY, amount))

	asset.insert(ignore_permissions=True)
	if submit:
		asset.submit()
	return asset.name


def submit_asset(name):
	"""Submit a Composite Asset once its capitalizations are in — this is what raises its
	component Assets (feature/component_asset_creation.create_component_assets)."""
	asset = frappe.get_doc("Asset", name)
	if asset.docstatus != 0:
		return name

	asset.flags.ignore_permissions = True
	asset.submit()
	return name


# ─────────────────────────────────────────────────────────────────
#  The capitalizations
# ─────────────────────────────────────────────────────────────────
def build_capitalization(target, target_item_code, stock_specs, service_specs, consumed_assets, posting_date):
	existing = frappe.db.exists("Asset Capitalization", {"target_asset": target, "docstatus": ["<", 2]})
	if existing:
		return existing

	cap = frappe.new_doc("Asset Capitalization")
	cap.update(
		{
			"company": COMPANY,
			"target_asset": target,
			"target_item_code": target_item_code,
			"posting_date": posting_date,
			"cost_center": COST_CENTER,
		}
	)

	for spec in stock_specs:
		cap.append(
			"stock_items",
			{
				"item_code": spec["item_code"],
				"stock_qty": spec["qty"],
				"warehouse": WAREHOUSE,
				"valuation_rate": spec["rate"],
				"cost_center": COST_CENTER,
			},
		)

	for asset in consumed_assets:
		cap.append("asset_items", {"asset": asset})

	for spec in service_specs:
		cap.append(
			"service_items",
			{
				"item_code": spec["item_code"],
				"qty": spec["qty"],
				"rate": spec["rate"],
				"expense_account": SERVICE_EXPENSE_ACCOUNT,
				"cost_center": COST_CENTER,
			},
		)

	cap.insert(ignore_permissions=True)
	cap.submit()
	return cap.name


# ─────────────────────────────────────────────────────────────────
#  Teardown
# ─────────────────────────────────────────────────────────────────
# Deepest first. Cancelling an Asset that a live Asset still points at through
# custom_parent_asset is refused for being linked, so the tree has to come apart leaf-upwards.
TEARDOWN_ORDER = (
	[spec["item_name"] for spec in ALL_STOCK_COMPONENTS + ALL_SERVICE_COMPONENTS]
	+ [CONSUMED_ASSET["asset_name"], INNER_ASSET["asset_name"], OUTER_ASSET["asset_name"]]
	+ [PARENT_ASSET["asset_name"]]
)


def remove_example_entries():
	"""Undo create_example_entries(), so the example can be rebuilt from scratch.

	Cancelling the capitalizations first is deliberate: that is what unwinds the ledgers, and an
	Asset Capitalization pointing at a submitted Asset blocks that Asset from being cancelled.

	Run with:

	    bench --site mysite.local execute \\
	        sge_asset_management.demo.nexity_tech_asset_tree.remove_example_entries
	"""
	# Outer before inner — cancelling the outer capitalization releases the inner Composite
	# Asset it consumed, restoring the Parent Asset the inner had before.
	for asset_name in (OUTER_ASSET["asset_name"], INNER_ASSET["asset_name"]):
		target = frappe.db.exists("Asset", {"asset_name": asset_name, "company": COMPANY})
		if not target:
			continue
		for name in frappe.get_all(
			"Asset Capitalization", filters={"target_asset": target, "docstatus": 1}, pluck="name"
		):
			frappe.get_doc("Asset Capitalization", name).cancel()
		for name in frappe.get_all("Asset Capitalization", filters={"target_asset": target}, pluck="name"):
			frappe.delete_doc("Asset Capitalization", name, force=True, ignore_permissions=True)

	for asset_name in TEARDOWN_ORDER:
		for name in frappe.get_all(
			"Asset", filters={"company": COMPANY, "asset_name": asset_name}, pluck="name"
		):
			asset = frappe.get_doc("Asset", name)
			if asset.docstatus == 1:
				asset.flags.ignore_permissions = True
				asset.cancel()
			frappe.delete_doc("Asset", name, force=True, ignore_permissions=True)

	for entry in frappe.get_all(
		"Stock Entry Detail",
		filters={"item_code": ["in", [spec["item_code"] for spec in ALL_STOCK_COMPONENTS]]},
		pluck="parent",
		distinct=True,
	):
		doc = frappe.get_doc("Stock Entry", entry)
		if doc.docstatus == 1:
			doc.cancel()
		frappe.delete_doc("Stock Entry", entry, force=True, ignore_permissions=True)

	item_codes = [spec["item_code"] for spec in ALL_STOCK_COMPONENTS + ALL_SERVICE_COMPONENTS] + [
		spec["item_code"] for spec in (PARENT_ASSET, OUTER_ASSET, INNER_ASSET, CONSUMED_ASSET)
	]
	# The mirror fixed-asset Items the capitalizations created for the consumed rows.
	item_codes += [f"FA-{code}" for code in item_codes]
	for code in item_codes:
		if frappe.db.exists("Item", code):
			frappe.delete_doc("Item", code, force=True, ignore_permissions=True)

	frappe.db.commit()
	print("Example entries removed.")
