import frappe
from frappe import _

from sge_sehyog_customizations.sge_sehyog.feature.mr_expenditure import _get_or_create_asset_cap


# ─────────────────────────────────────────────────────────────────
#  Asset (Composite Component) submit  →  append to Asset Capitalization
# ─────────────────────────────────────────────────────────────────
def append_composite_component_to_asset_capitalization(doc, method=None):
    """On Asset submit: if this is a Composite Component tagged with a
    Target Asset (custom_target_asset), add it as a Consumed Asset row on
    that Target Asset's draft Asset Capitalization — reusing an existing
    draft for the same target if one exists, else creating a new one.

    Mirrors append_to_asset_capitalization_from_pr / _from_ses in
    mr_expenditure.py, just keyed off the Asset itself rather than a PR/SES
    line item. asset_value / current_asset_value are left for
    Asset Capitalization's own validate() (set_asset_values) to fill in —
    it recomputes them from doc.asset alone, and for a Composite Component
    consumed asset no depreciation/disposal GL entries are raised anyway
    (see get_gl_entries_for_consumed_asset_items in erpnext core), so
    nothing else needs to be set on the row here.
    """
    if doc.asset_type != "Composite Component":
        return
    if not doc.custom_target_asset:
        return

    asset_cap, existed = _get_or_create_asset_cap(
        target_asset=doc.custom_target_asset,
        company=doc.company,
        posting_date=frappe.utils.nowdate(),
        cost_center=doc.get("cost_center"),
        finance_book=None,
    )

    already_added = any(row.asset == doc.name for row in asset_cap.get("asset_items", []))
    if not already_added:
        asset_cap.append("asset_items", {"asset": doc.name})

    if existed:
        asset_cap.save(ignore_permissions=True)
        action = "updated"
    else:
        asset_cap.insert(ignore_permissions=True)
        action = "created"

    frappe.msgprint(
        _(
            'Asset Capitalization <a href="/app/asset-capitalization/{0}"><b>{0}</b></a> {1} — '
            "{2} added as a Consumed Asset."
        ).format(asset_cap.name, action, doc.name),
        alert=True,
    )


# ─────────────────────────────────────────────────────────────────
#  Asset (Composite Component) cancel  →  remove from Asset Capitalization
# ─────────────────────────────────────────────────────────────────
def remove_composite_component_from_asset_capitalization(doc, method=None):
    """On Asset cancel: pull this Asset back out of any draft Asset
    Capitalization it was added to as a Consumed Asset, so a cancelled
    component doesn't linger on a draft capitalization waiting to be
    submitted. Mirrors remove_purchase_receipt_from_asset_capitalization /
    remove_service_entry_sheet_from_asset_capitalization in
    mr_expenditure.py."""
    asset_caps = frappe.get_all("Asset Capitalization", filters={"docstatus": 0}, pluck="name")

    for cap_name in asset_caps:
        asset_cap = frappe.get_doc("Asset Capitalization", cap_name)

        remaining_rows = [row for row in asset_cap.get("asset_items", []) if row.asset != doc.name]

        if len(remaining_rows) == len(asset_cap.get("asset_items", [])):
            continue

        asset_cap.set("asset_items", remaining_rows)

        if asset_cap.get("stock_items") or asset_cap.get("service_items") or asset_cap.get("asset_items"):
            asset_cap.save(ignore_permissions=True)
        else:
            frappe.delete_doc("Asset Capitalization", asset_cap.name, force=True, ignore_permissions=True)

        frappe.msgprint(
            _("Removed Asset <b>{0}</b> from Asset Capitalization <b>{1}</b>.").format(doc.name, cap_name),
            alert=True,
        )
