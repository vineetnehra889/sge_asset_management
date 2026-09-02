frappe.provide("sge_sehyog.asset_capitalization_preview");

frappe.ui.form.on('Asset Capitalization', {
	refresh(frm) {
		// frm.set_query replaces whatever query was previously registered for
		// this field/table rather than merging with it, and core's own
		// setup_queries() (asset_capitalization.js) also runs on refresh, so
		// re-declaring here (after core's handler runs) is the only way to
		// add our condition without losing core's status/docstatus/target_asset
		// filters. Keep this in sync with core if it changes.
		frm.set_query("asset", "asset_items", function () {
			var filters = {
				status: ["not in", ["Draft", "Scrapped", "Sold", "Capitalized"]],
				docstatus: 1,
				// Assets worth ₹0 produce a zero-value GL entry that core silently
				// fails to post (see restrict_zero_value_consumed_assets), so keep
				// them out of the picker entirely.
				net_purchase_amount: [">", 0],
				// A Parent Asset is a grouping record, not something you can consume:
				// its value is already itemised across the Assets hanging off it, and
				// consuming it would drag that whole subtree under the new target.
				asset_type: ["!=", "Parent Asset"],
			};

			if (frm.doc.target_asset) {
				filters["name"] = ["!=", frm.doc.target_asset];
			}

			return { filters: filters };
		});

		if (frm.is_new() || frm.doc.docstatus !== 0) {
			return;
		}

		sge_sehyog.asset_capitalization_preview.add_button(
			frm,
			__("Accounting Ledger"),
			"erpnext.controllers.stock_controller.show_accounting_ledger_preview",
			"gl_columns",
			"gl_data"
		);
		sge_sehyog.asset_capitalization_preview.add_button(
			frm,
			__("Stock Ledger"),
			"erpnext.controllers.stock_controller.show_stock_ledger_preview",
			"sl_columns",
			"sl_data"
		);
	},
});

Object.assign(sge_sehyog.asset_capitalization_preview, {
	add_button(frm, label, method, columns_key, data_key) {
		frm.add_custom_button(
			label,
			() => {
				frappe.call({
					type: "GET",
					method: method,
					args: {
						company: frm.doc.company,
						doctype: frm.doc.doctype,
						docname: frm.doc.name,
					},
					callback: (response) => {
						this.show_dialog(
							label,
							response.message[columns_key],
							response.message[data_key]
						);
					},
				});
			},
			__("Preview")
		);
	},

	show_dialog(label, columns, data) {
		if (!data || !data.length) {
			frappe.msgprint(`<strong>${__("No Impact on {0}", [label])}</strong>`);
			return;
		}

		const dialog = new frappe.ui.Dialog({
			size: "extra-large",
			title: __("{0} Preview", [label]),
			fields: [{ fieldtype: "HTML", fieldname: "preview_html" }],
			primary_action_label: __("Download Excel"),
			primary_action: () => {
				this.download_as_excel(label, columns, data);
			},
		});

		setTimeout(() => {
			columns.forEach((col) => {
				if (col.fieldtype === "Currency") {
					col.format = (value) => format_currency(value);
				}
			});

			new frappe.DataTable(dialog.get_field("preview_html").wrapper, {
				columns: columns,
				data: data,
				dynamicRowHeight: true,
				checkboxColumn: false,
				inlineFilters: true,
			});
		}, 200);

		dialog.show();
	},

	download_as_excel(label, columns, data) {
		open_url_post(frappe.request.url, {
			cmd: "sge_sehyog_customizations.sge_sehyog.api.ledger_preview_export.export_ledger_preview",
			label: label,
			columns: JSON.stringify(columns.map((col) => ({ name: col.name, fieldtype: col.fieldtype }))),
			data: JSON.stringify(data),
		});
	},
});
