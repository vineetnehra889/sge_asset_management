import frappe
from frappe import _
from frappe.utils import flt, get_link_to_form
from erpnext.assets.doctype.asset.asset import Asset

_original_validate_asset_values = Asset.validate_asset_values


def custom_validate_asset_values(self):
	"""Parent Asset is a lightweight grouping record with no purchase
	documents or depreciation of its own (see feature/parent_asset.py and
	the Parent Asset property setters on Asset). Core's validate_asset_values
	hard-throws if Net Purchase Amount or (when CWIP accounting is on)
	Purchase Receipt/Invoice are missing regardless of docfield mandatory
	settings — skip those checks for Parent Asset, keeping only the
	Asset Category backfill the original does first.
	"""
	if self.asset_type == "Parent Asset":
		if not self.asset_category:
			self.asset_category = frappe.get_cached_value("Item", self.item_code, "asset_category")
		# before_save() unconditionally does net_purchase_amount + additional_asset_cost —
		# default the None left by skipping the mandatory check above to 0 so that doesn't crash.
		self.net_purchase_amount = flt(self.net_purchase_amount)
		return
	_original_validate_asset_values(self)

def custom_update_target_asset(self):
	total_target_asset_value = flt(self.total_value, self.precision("total_value"))
	asset_doc = frappe.get_doc("Asset", self.target_asset)

	if self.docstatus == 2:
		net_purchase_amount = asset_doc.net_purchase_amount - total_target_asset_value
		purchase_amount     = asset_doc.purchase_amount     - total_target_asset_value
		total_asset_cost    = asset_doc.total_asset_cost    - total_target_asset_value
	else:
		net_purchase_amount = asset_doc.net_purchase_amount + total_target_asset_value
		purchase_amount     = asset_doc.purchase_amount     + total_target_asset_value
		total_asset_cost    = asset_doc.total_asset_cost    + total_target_asset_value

	# Outside the else: the docstatus==2 branch computed the reversal and then never wrote it,
	# so cancelling an Asset Capitalization left its value sitting on the Target Asset. Core
	# db_sets unconditionally after the same if/else.
	asset_doc.db_set(
		{
			"net_purchase_amount": net_purchase_amount,
			"purchase_amount": purchase_amount,
			"total_asset_cost": total_asset_cost,
		},
		update_modified=False,
	)

	frappe.msgprint(
		_("""
			<div>
				Asset <a href="/app/asset/{1}"
				onclick="setTimeout(() => {{
						if(cur_frm) {{
							cur_frm.reload_doc();
						}}
				}}, 500)">
				{0}
				</a>
				 has been updated. Please set the depreciation details if any and submit it.
			</div>
		""").format(
			asset_doc.name,
			self.target_asset
		)
	)
