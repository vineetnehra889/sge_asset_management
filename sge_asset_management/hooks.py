app_name = "sge_asset_management"
app_title = "Asset Management"
app_publisher = "Nexity Consulting LLP"
app_description = "Customizations for Composite Asset, FAR register, and Parent Asset on the Asset doctype"
app_email = "codejr25@gmail.com"
app_license = "mit"

# Apps
# ------------------

required_apps = ["sge_sehyog_customizations"]

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "sge_asset_management",
# 		"logo": "/assets/sge_asset_management/logo.png",
# 		"title": "Asset Management",
# 		"route": "/sge_asset_management",
# 		"has_permission": "sge_asset_management.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/sge_asset_management/css/sge_asset_management.css"
# app_include_js = "/assets/sge_asset_management/js/sge_asset_management.js"

# include js, css files in header of web template
# web_include_css = "/assets/sge_asset_management/css/sge_asset_management.css"
# web_include_js = "/assets/sge_asset_management/js/sge_asset_management.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "sge_asset_management/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
doctype_js = {
    "Asset": "public/js/asset.js",
    "Asset Capitalization": "public/js/asset_capitalization.js",
}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Changing Specific Class pooFunction
# Patch the class method
from sge_asset_management.overrides.asset__patch import custom_update_target_asset, custom_validate_asset_values

from erpnext.assets.doctype.asset.asset import Asset
from erpnext.assets.doctype.asset_capitalization.asset_capitalization import AssetCapitalization

# Patch
Asset.validate_asset_values = custom_validate_asset_values
AssetCapitalization.update_target_asset = custom_update_target_asset

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "sge_asset_management/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# automatically load and sync documents of this doctype from downstream apps
# importable_doctypes = [doctype_1]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "sge_asset_management.utils.jinja_methods",
# 	"filters": "sge_asset_management.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "sge_asset_management.install.before_install"
# after_install = "sge_asset_management.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "sge_asset_management.uninstall.before_uninstall"
# after_uninstall = "sge_asset_management.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "sge_asset_management.utils.before_app_install"
# after_app_install = "sge_asset_management.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "sge_asset_management.utils.before_app_uninstall"
# after_app_uninstall = "sge_asset_management.utils.after_app_uninstall"

# Build
# ------------------
# To hook into the build process

# after_build = "sge_asset_management.build.after_build"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "sge_asset_management.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# Document Events
# ---------------
# Hook on document methods and events

doc_events = {
    "Asset Maintenance": {
        "before_save": "sge_asset_management.feature.asset_maintenance.validate",
    },
    "Asset": {
        "validate": "sge_asset_management.feature.parent_asset.set_defaults_for_parent_asset",
        "on_submit": [
            "sge_asset_management.feature.composite_component_capitalization.append_composite_component_to_asset_capitalization",
            # A Composite Asset is built up over several Asset Capitalizations while it sits in
            # Work In Progress; its components are raised here, when it is submitted and becomes
            # a real asset, rather than one capitalization at a time against a draft target.
            "sge_asset_management.feature.component_asset_creation.create_component_assets",
        ],
        "before_cancel": "sge_asset_management.feature.composite_component_capitalization.remove_composite_component_from_asset_capitalization",
        # on_cancel, not before_cancel: frappe runs on_cancel ahead of the "still linked" check,
        # and the component Assets link back through custom_parent_asset.
        "on_cancel": "sge_asset_management.feature.component_asset_creation.cancel_component_assets_for_target",
    },
    "Asset Capitalization": {
        "before_gl_preview": "sge_asset_management.overrides.asset_capitalization.enable_stock_ledger_for_preview",
        "before_sl_preview": "sge_asset_management.overrides.asset_capitalization.enable_stock_ledger_for_preview",
        "validate": "sge_asset_management.overrides.asset_capitalization.restrict_zero_value_consumed_assets",
        "before_submit": "sge_asset_management.overrides.asset_capitalization.restrict_zero_value_consumed_assets",
        # Cancelling a capitalization after its target was submitted takes the value back off
        # the target, so the components it contributed have to go with it.
        "on_cancel": "sge_asset_management.feature.component_asset_creation.cancel_component_assets",
    },
}

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"sge_asset_management.tasks.all"
# 	],
# 	"daily": [
# 		"sge_asset_management.tasks.daily"
# 	],
# 	"hourly": [
# 		"sge_asset_management.tasks.hourly"
# 	],
# 	"weekly": [
# 		"sge_asset_management.tasks.weekly"
# 	],
# 	"monthly": [
# 		"sge_asset_management.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "sge_asset_management.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "sge_asset_management.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "sge_asset_management.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "sge_asset_management.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["sge_asset_management.utils.before_request"]
# after_request = ["sge_asset_management.utils.after_request"]

# Job Events
# ----------
# before_job = ["sge_asset_management.utils.before_job"]
# after_job = ["sge_asset_management.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"sge_asset_management.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []

