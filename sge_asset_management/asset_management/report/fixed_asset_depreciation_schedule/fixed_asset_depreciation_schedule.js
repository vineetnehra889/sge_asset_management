// Copyright (c) 2026, Nexity Consulting LLP and contributors
// For license information, please see license.txt

frappe.query_reports["Fixed Asset Depreciation Schedule"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			reqd: 1,
		},
		{
			fieldname: "from_date",
			label: __("Beginning Date"),
			fieldtype: "Date",
			default: frappe.datetime.year_start(),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("Ending Date"),
			fieldtype: "Date",
			default: frappe.datetime.year_end(),
			reqd: 1,
		},
		{
			fieldname: "asset_category",
			label: __("Asset Category"),
			fieldtype: "Link",
			options: "Asset Category",
		},
		{
			fieldname: "location",
			label: __("Location"),
			fieldtype: "Link",
			options: "Location",
		},
	],

	// Rows are nested by the Asset's Parent Asset (custom_parent_asset) — the link that
	// Asset Capitalization sets on every component Asset it raises. The server returns the
	// rows already in depth-first order with `indent`, which is what frappe-datatable draws
	// the expand/collapse control from; `parent_field` drives the tree level controls.
	tree: true,
	name_field: "asset",
	parent_field: "parent_asset",
	initial_depth: 2,

	formatter: function (value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);

		// A group row is an ancestor with no depreciation schedule of its own, so its figures
		// are a roll-up of the components below it — set them apart from real asset rows.
		const is_group = data && data.is_group;
		if (is_group && column.fieldname === "asset") {
			value = `<span style="font-weight:600">${value}</span>`;
		}

		if (column.fieldname === "opening_asset" && data.opening_asset) {
			const color = data.opening_asset === "Y" ? "blue" : "green";
			value = `<span class="indicator-pill ${color}" style="display:inline-block;">${value}</span>`;
		}

		if (
			column.fieldname === "closing_wdv_after_residual_adjustment" &&
			data.closing_wdv_after_residual_adjustment
		) {
			value = `<span style="color: var(--orange-600)">${value}</span>`;
		}

		if (column.fieldname === "remarks" && data.remarks) {
			const color = is_group ? "var(--text-muted)" : "var(--red-600)";
			value = `<span style="color: ${color}">${value}</span>`;
		}

		return value;
	},
};
