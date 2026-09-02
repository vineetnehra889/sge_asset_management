frappe.ui.form.off("Asset", "asset_type");
frappe.ui.form.off("Asset", "toggle_reference_doc");
frappe.ui.form.on("Asset", {
	setup(frm) {
		// Only an in-progress Composite Asset build can receive this
		// component — same filter purchase_receipt.js / service_entry_sheet.js
		// already use for their wip_composite_asset pickers.
		frm.set_query("custom_target_asset", () => ({
			filters: { asset_type: "Composite Asset", docstatus: 0 },
		}));

		frm.set_query("custom_parent_asset", () => ({
			filters: {
				name: ["!=", frm.doc.name],
				company: frm.doc.company,
			},
		}));
	},

	// A Parent Asset is a pure grouping record (see feature/parent_asset.py) — it never holds
	// value, is never depreciated, and books no GL entries, so none of core's create/disposal
	// actions apply to it. This runs after core's own refresh (this file loads after erpnext's
	// asset.js, and both are bound to the same "refresh" event in script-load order), so the
	// buttons removed here have already been added by the time this fires.
	refresh(frm) {
		if (frm.doc.asset_type !== "Parent Asset") return;

		["Asset Value Adjustment", "Asset Repair", "Depreciation Entry"].forEach((label) =>
			frm.remove_custom_button(label, __("Create"))
		);
		["Maintain Asset", "Split Asset", "Transfer Asset", "Scrap Asset", "Sell Asset"].forEach(
			(label) => frm.remove_custom_button(label, __("Actions"))
		);
		frm.remove_custom_button(__("Restore Asset"));

		// Cancelling is blocked server-side too (restrict_parent_asset_cancellation) — hidden
		// here so the block isn't the user's first sign that a Parent Asset plays by different
		// rules. clear_secondary_action() is the same call core uses to remove Cancel itself:
		// it hides the button and unbinds the click, not just a CSS toggle.
		if (frm.doc.docstatus === 1) {
			frm.page.clear_secondary_action();
		}
	},

    asset_type: function (frm) {
		if (frm.doc.docstatus == 0) {
			if (frm.doc.asset_type == "Composite Asset") {
				if (!frm.doc.net_purchase_amount) {
					frm.set_value("net_purchase_amount", 0);
				}
			}else {
				frm.set_df_property("net_purchase_amount", "read_only", 0);
			}
		}

		if (frm.doc.asset_type == "Composite Component") {
			frm.set_value("calculate_depreciation", 0);
		}

		// Purchase Receipt / Purchase Invoice are hidden for Existing & Composite
		// assets (depends_on in asset.json), but their mandatory flag is only
		// recomputed in refresh — re-run it here so changing Asset Type does not
		// leave hidden fields marked as required.
		frm.trigger("toggle_reference_doc");
	},

	// Copy of erpnext core's Asset.toggle_reference_doc (asset.js) with
	// "Parent Asset" added to is_special_asset — core only exempted
	// Existing/Composite Asset from needing a Purchase Receipt/Invoice,
	// which forced them mandatory on Parent Asset too since core has no
	// concept of that type. Property-setter depends_on alone can't fix
	// this: toggle_reqd here runs client-side after depends_on and wins.
	toggle_reference_doc: function (frm) {
		const is_submitted = frm.doc.docstatus === 1;
		const is_special_asset =
			frm.doc.asset_type == "Existing Asset" ||
			frm.doc.asset_type == "Composite Asset" ||
			frm.doc.asset_type == "Parent Asset";

		const clear_field = (field) => {
			if (frm.doc[field]) {
				frm.set_value(field, "");
			}
		};

		["purchase_receipt", "purchase_receipt_item", "purchase_invoice", "purchase_invoice_item"].forEach(
			(field) => {
				frm.toggle_reqd(field, 0);
				frm.set_df_property(field, "read_only", 0);
			}
		);

		if (is_submitted) {
			[
				"purchase_receipt",
				"purchase_receipt_item",
				"purchase_invoice",
				"purchase_invoice_item",
			].forEach((field) => {
				frm.set_df_property(field, "read_only", 1);
			});
			return;
		}

		if (is_special_asset) {
			clear_field("purchase_receipt");
			clear_field("purchase_receipt_item");
			clear_field("purchase_invoice");
			clear_field("purchase_invoice_item");
			return;
		}

		if (frm.doc.purchase_receipt) {
			frm.toggle_reqd("purchase_receipt_item", 1);

			["purchase_invoice", "purchase_invoice_item"].forEach((field) => {
				clear_field(field);
				frm.set_df_property(field, "read_only", 1);
			});
			return;
		}

		if (frm.doc.purchase_invoice) {
			frm.toggle_reqd("purchase_invoice_item", 1);

			["purchase_receipt", "purchase_receipt_item"].forEach((field) => {
				clear_field(field);
				frm.set_df_property(field, "read_only", 1);
			});
			return;
		}

		frm.toggle_reqd("purchase_receipt", 1);
		frm.toggle_reqd("purchase_invoice", 1);
	},

	before_submit(frm) {
		if (frm.doc.asset_type === "Composite Asset" && !frm.doc.calculate_depreciation) {
			return new Promise((resolve) => {
				const d = frappe.warn(
					__("Depreciation Not Enabled"),
					__("Depreciation is not enabled for this asset. Depreciation will not be calculated."),
					() => {
						frappe.validated = true;
						resolve();
					},
					__("Proceed"),
					false
				);
				d.onhide = () => {
					if (!d.primary_action_fulfilled) {
						frappe.validated = false;
						resolve();
					}
				};
			});
		}

		// Target Asset is optional on a Composite Component (see
		// custom_target_asset), but leaving it blank means this component
		// never gets picked up by append_composite_component_to_asset_capitalization
		// on submit — it just sits there, unlinked to any Composite Asset build.
		// Confirm that's really what's wanted instead of silently letting it happen.
		if (frm.doc.asset_type === "Composite Component" && !frm.doc.custom_target_asset) {
			return new Promise((resolve) => {
				const d = frappe.warn(
					__("Target Asset Not Set"),
					__(
						"No Target Asset is set for this Composite Component. It will not be added to any Asset Capitalization until you link one."
					),
					() => {
						frappe.validated = true;
						resolve();
					},
					__("Submit Anyway"),
					false
				);
				d.onhide = () => {
					if (!d.primary_action_fulfilled) {
						frappe.validated = false;
						resolve();
					}
				};
			});
		}
	},
})
