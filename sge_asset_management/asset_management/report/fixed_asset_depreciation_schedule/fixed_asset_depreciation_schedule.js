// Copyright (c) 2026, Nexity Consulting LLP and contributors
// For license information, please see license.txt

// Namespaced rather than top-level consts: query_report.js re-evals this script through
// frappe.dom.eval() each time the report is opened, and a repeated `const` would throw.
frappe.provide("sge_asset_management.fads");

Object.assign(sge_asset_management.fads, {
	// Colour lands on the figures only — never on the labels, which carry hierarchy through
	// weight instead. Each tint matches the chart series it belongs to, so a bar and the column
	// it came from read as the same number. CSS variables, so the report follows the desk theme.
	value_colours: {
		opening_wdv: "var(--blue-600)",
		addition: "var(--green-600)",
		dep_current_year: "var(--red-600)",
		addition_current_year: "var(--red-600)",
		closing_wdv: "var(--purple-600)",
		deletion: "var(--orange-600)",
		deletion_dep: "var(--orange-600)",
	},

	// Remarks are a "; "-joined list of independent notes. Each becomes its own pill, coloured
	// by what it means rather than all-red — a roll-up total is information, not a warning.
	remark_pills: [
		[/group total/i, "gray"],
		[/itemised in components/i, "blue"],
		[/disposed/i, "orange"],
		[/non-wdv/i, "yellow"],
	],

	pill(text, colour) {
		// No `display` in the inline style: .indicator-pill sets `display:inline-flex`, and
		// overriding it with inline-block is what flattens the badge into a dot plus text
		// instead of a filled pill. Only the gap between stacked pills is set here.
		return `<span class="indicator-pill ${colour}" style="margin-right:4px;">${text}</span>`;
	},

	remark_html(remarks) {
		return remarks
			.split(";")
			.map((note) => note.trim())
			.filter(Boolean)
			.map((note) => {
				const match = this.remark_pills.find(([pattern]) => pattern.test(note));
				return this.pill(note, match ? match[1] : "red");
			})
			.join("");
	},
});

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
		{
			// Drives the table and the chart together. "Asset Hierarchy" nests rows the way the
			// Assets are nested, by Parent Asset; anything else adds a heading row per distinct
			// value with those hierarchies underneath, and the chart draws one bar per heading.
			fieldname: "group_by",
			label: __("Group By"),
			fieldtype: "Select",
			options: ["Asset Hierarchy", "Classification", "Asset Category", "Location"],
			default: "Asset Hierarchy",
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
		if (!data) return value;

		const fads = sge_asset_management.fads;
		// A group row is a heading, or an ancestor with no schedule of its own — its whole row
		// is a subtotal, so everything on it goes semi-bold.
		const is_group = !!data.is_group;

		if (column.fieldname === "opening_asset" && data.opening_asset) {
			return fads.pill(value, data.opening_asset === "Y" ? "blue" : "green");
		}

		if (column.fieldname === "remarks" && data.remarks) {
			return fads.remark_html(data.remarks);
		}

		// Only tint a figure that is actually there — a column of coloured zeros is noise.
		const colour = fads.value_colours[column.fieldname];
		if (colour && flt(data[column.fieldname])) {
			return `<span style="color:${colour};font-weight:${is_group ? 600 : 400};">${value}</span>`;
		}

		// This closing figure is not the formula's result: depreciation was capped so the asset
		// would not be written below its residual value. Worth a reviewer's eye.
		if (
			column.fieldname === "closing_wdv_after_residual_adjustment" &&
			data.closing_wdv_after_residual_adjustment
		) {
			return `<span style="color:var(--orange-600);font-weight:${is_group ? 600 : 400};">${value}</span>`;
		}

		// Labels and everything else: hierarchy through weight, no colour.
		if (is_group) {
			return `<span style="font-weight:600;">${value}</span>`;
		}
		if (
			(data.indent || 0) <= 1 &&
			["asset", "tree_label", "asset_description"].includes(column.fieldname)
		) {
			return `<span style="font-weight:500;">${value}</span>`;
		}

		return value;
	},
};
