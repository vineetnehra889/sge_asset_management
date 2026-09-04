frappe.provide("frappe.treeview_settings");
frappe.provide("sge_asset_management.asset_tree");

// Parent-ish types get a colour; the leaves stay grey. The point of the pill is to tell at a
// glance which rows hold a hierarchy and which are the assets themselves.
// Only the value column is fixed width -- it is the one meant to line up like a Chart of
// Accounts balance. The type pill sits at whatever width its own label needs (nowrap, so a
// two-word type like "Composite Component" never breaks across lines); a fixed box narrower
// than that text was what wrapped it in the first place.
sge_asset_management.asset_tree.col = { value: 130 };

// The framework never assigns frappe.treeview_settings["Asset"].treeview -- Account gets its
// instance handed to onload(). Stash it there and read the live filter values off the page,
// so a button reflects whatever the user currently has selected.
sge_asset_management.asset_tree.instance = null;

sge_asset_management.asset_tree.current_filters = function () {
	const tree = sge_asset_management.asset_tree.instance;
	if (!tree || !tree.page || !tree.page.fields_dict) return {};
	const value = (fieldname) =>
		tree.page.fields_dict[fieldname] && tree.page.fields_dict[fieldname].get_value();
	return {
		company: value("company"),
		from_date: value("from_date"),
		to_date: value("to_date"),
	};
};

sge_asset_management.asset_tree.open_schedule = function () {
	frappe.set_route("query-report", "Fixed Asset Depreciation Schedule", {
		...sge_asset_management.asset_tree.current_filters(),
		group_by: "Asset Hierarchy",
	});
};

sge_asset_management.asset_tree.type_colours = {
	"Parent Asset": "blue",
	"Composite Asset": "purple",
	"Composite Component": "orange",
	"Existing Asset": "gray",
};

// The amount picks up its row's type colour, so a subtotal is scannable down the column
// without a second legend. Leaves stay the default text colour -- colouring every row would
// be noise, and a nil or rolled-up figure is muted regardless of type.
sge_asset_management.asset_tree.amount_colours = {
	"Parent Asset": "var(--blue-600)",
	"Composite Asset": "var(--purple-600)",
	"Composite Component": "var(--orange-600)",
};

frappe.treeview_settings["Asset"] = {
	breadcrumb: "Assets",
	title: __("Asset Tree"),
	// Asset has no `is_group` and no nested-set columns, so the framework's default
	// frappe.desk.treeview.get_children cannot walk it. This method walks
	// custom_parent_asset instead — the same link the Fixed Asset Depreciation Schedule
	// nests on, so the two views always agree.
	get_tree_nodes: "sge_asset_management.feature.asset_tree.get_asset_tree_nodes",
	// There is no single root record — roots are every asset without a parent — so the
	// framework must not try to resolve one.
	get_tree_root: false,
	root_label: __("Assets"),

	onload: function (treeview) {
		sge_asset_management.asset_tree.instance = treeview;

		// The page's New button is the framework's, and it opens the add-node dialog -- which
		// cannot build a valid Asset. disable_add_node stops the framework installing it, and
		// this puts a New back that opens the real form instead. Order matters: onload runs
		// inside make_page(), before set_primary_action(), so this would be overwritten if
		// disable_add_node were not set.
		treeview.page.set_primary_action(__("New"), () => frappe.new_doc("Asset"), "add");
	},
	show_expand_all: true,
	// Belt and braces with the replaced toolbar: the framework's add-node dialog cannot
	// produce a valid Asset, so it should not be reachable at all.
	disable_add_node: true,

	menu_items: [
		{
			label: __("Open Fixed Asset Depreciation Schedule"),
			action: () => sge_asset_management.asset_tree.open_schedule(),
		},
	],

	filters: [
		{
			fieldname: "company",
			fieldtype: "Link",
			options: "Company",
			label: __("Company"),
			render_on_toolbar: true,
			default: frappe.defaults.get_user_default("Company"),
		},
		{
			fieldname: "asset_category",
			fieldtype: "Link",
			options: "Asset Category",
			label: __("Asset Category"),
		},
		{
			fieldname: "location",
			fieldtype: "Link",
			options: "Location",
			label: __("Location"),
		},
		// The value column is Closing Balance Gross out of the Fixed Asset Depreciation
		// Schedule, which is computed for a period — so the tree needs the same two dates the
		// report takes. Left blank, the server uses the company's current fiscal year.
		{
			fieldname: "from_date",
			fieldtype: "Date",
			label: __("Beginning Date"),
			default: frappe.datetime.year_start(),
		},
		{
			fieldname: "to_date",
			fieldtype: "Date",
			label: __("Ending Date"),
			default: frappe.datetime.year_end(),
		},
	],

	// Replaces the default toolbar rather than extending it. The framework's "Add Child"
	// posts a bare doc through frappe.desk.treeview.add_node, which an Asset can never be —
	// it needs an Item, a Location, a purchase date and a category before it will validate.
	// Sending users to the real form is the only honest option.
	extend_toolbar: false,
	toolbar: [
		{
			label: __("Open"),
			condition: (node) => !node.is_root,
			click: (node) => frappe.set_route("Form", "Asset", node.label),
		},
	],

	// Show what each node is worth and what kind of record it is, so the tree reads like a
	// register rather than a list of document ids. Status is deliberately not shown -- a pill
	// on most rows adds noise without telling you anything the form does not.
	get_label: function (node) {
		// Name only. node.label stays the document id, so the toolbar's Open still routes
		// correctly -- this changes what is displayed, not what the node is.
		if (node.is_root) return node.label;
		return frappe.utils.escape_html(node.data?.asset_name || node.label);
	},

	onrender: function (node) {
		if (node.is_root || !node.data) return;

		const data = node.data;

		// Type and value go into one right-floated block, the way the Chart of Accounts floats
		// a balance before node.$ul. Appending them into the tree link instead would leave both
		// trailing the name, so their position would shift with every name length and neither
		// would form a column.
		node.parent && node.parent.find(".asset-meta-area").remove();

		let type = "";
		if (data.asset_type) {
			const colour = sge_asset_management.asset_tree.type_colours[data.asset_type] || "gray";
			type = `<span class="indicator-pill ${colour}" style="white-space:nowrap;">${frappe.utils.escape_html(
				data.asset_type
			)}</span>`;
		}

		// Always render the figure, zero included. A blank cell reads as missing data, when a
		// nil Closing Balance Gross is a real answer — a consumed or disposed asset genuinely
		// closes the period at nothing. Zero and rolled-up totals are muted: neither is the
		// asset's own live figure.
		const amount = flt(data.display_value);
		const muted = !amount || data.is_rollup;
		const colour = muted ? "" : sge_asset_management.asset_tree.amount_colours[data.asset_type];
		const style = colour ? `color:${colour};font-weight:600;` : "";
		const value = `<span class="${muted ? "text-muted" : ""}" style="${style}">${format_currency(
			amount
		)}</span>`;

		const col = sge_asset_management.asset_tree.col;
		$(
			`<span class="asset-meta-area pull-right" style="display:flex;align-items:center;gap:12px;">
				<span style="white-space:nowrap;">${type}</span>
				<span style="display:inline-block;width:${col.value}px;text-align:right;white-space:nowrap;">${value}</span>
			</span>`
		).insertBefore(node.$ul);
	},
};
