"""One-off: delete every Asset for Shree Ganesh Edibles Private Limited, the depreciation
Journal Entries that reference them, and everything left dangling behind both.

Documents are deleted rather than cancelled-then-deleted. Cancelling an Asset exists to take
its depreciation back out of the books by cancelling the Journal Entries and reversing their
GL Entries — but those Journal Entries and GL Entries are themselves deleted here, so the
cancel does several minutes of work per hundred assets that is then thrown away. The GL Entries
are removed directly instead (Accounts Settings has delete_linked_ledger_entries off, so
deleting a Journal Entry would otherwise strand them).
"""

import time
import traceback

import frappe

COMPANY = "Shree Ganesh Edibles Private Limited"


def _log(msg):
    print(msg, flush=True)


def _chunks(seq, size=500):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def run(limit=None):
    limit = int(limit) if limit else None
    started = time.time()
    frappe.db.auto_commit_on_many_writes = 1
    frappe.flags.ignore_links = True

    assets = frappe.get_all("Asset", filters={"company": COMPANY}, pluck="name", order_by="creation")
    if limit:
        assets = assets[:limit]
    _log(f"Assets in scope: {len(assets)}")
    if not assets:
        return

    jes = frappe.db.sql(
        """select distinct je.name from `tabJournal Entry` je
           inner join `tabJournal Entry Account` jea on jea.parent = je.name
           where jea.reference_type='Asset' and jea.reference_name in %(n)s""",
        {"n": assets},
        pluck=True,
    )
    _log(f"Journal Entries referencing them: {len(jes)}")

    failures = []

    # A draft Asset Capitalization pointing at one of these Assets would be left referencing a
    # record that no longer exists.
    for cap in set(
        frappe.get_all("Asset Capitalization", filters={"target_asset": ["in", assets]}, pluck="name")
    ) | set(
        frappe.db.sql(
            "select distinct parent from `tabAsset Capitalization Asset Item` where asset in %(n)s",
            {"n": assets},
            pluck=True,
        )
    ):
        if frappe.db.exists("Asset Capitalization", cap):
            frappe.delete_doc("Asset Capitalization", cap, force=True, ignore_permissions=True)
            _log(f"  deleted Asset Capitalization {cap}")

    _log("\n[1/4] deleting GL Entries")
    removed_gl = 0
    for chunk in _chunks(jes):
        removed_gl += frappe.db.count("GL Entry", {"voucher_type": "Journal Entry", "voucher_no": ["in", chunk]})
        frappe.db.sql(
            "delete from `tabGL Entry` where voucher_type='Journal Entry' and voucher_no in %(j)s",
            {"j": chunk},
        )
    for chunk in _chunks(assets):
        removed_gl += frappe.db.count("GL Entry", {"voucher_type": "Asset", "voucher_no": ["in", chunk]})
        frappe.db.sql(
            "delete from `tabGL Entry` where voucher_type='Asset' and voucher_no in %(a)s", {"a": chunk}
        )
    frappe.db.commit()
    _log(f"    removed {removed_gl} GL Entry rows")

    _log("\n[2/4] deleting Journal Entries")
    failures += _delete_docs("Journal Entry", jes, started, every=1000)

    _log("\n[3/4] deleting Assets")
    failures += _delete_docs("Asset", assets, started, every=250)

    _log("\n[4/4] clearing records orphaned by the Asset deletion")
    schedules = frappe.get_all("Asset Depreciation Schedule", filters={"asset": ["in", assets]}, pluck="name")
    failures += _delete_docs("Asset Depreciation Schedule", schedules, started, every=1000)

    movements = frappe.db.sql(
        "select distinct parent from `tabAsset Movement Item` where asset in %(n)s", {"n": assets}, pluck=True
    )
    failures += _delete_docs("Asset Movement", movements, started, every=1000)

    # Asset Activity is a flat log with no children and nothing linking to it.
    activity = 0
    for chunk in _chunks(assets):
        activity += frappe.db.count("Asset Activity", {"asset": ["in", chunk]})
        frappe.db.sql("delete from `tabAsset Activity` where asset in %(a)s", {"a": chunk})
    frappe.db.commit()
    _log(f"    Asset Activity: {activity}")

    frappe.db.commit()
    _log("\n=== RESULT ===")
    _log(f"Assets remaining for {COMPANY}: {frappe.db.count('Asset', {'company': COMPANY})}")
    _log(f"Assets for other companies (untouched): {frappe.db.count('Asset', {'company': ['!=', COMPANY]})}")
    _log(f"Elapsed: {time.time() - started:.0f}s")
    if failures:
        _log(f"\nFAILURES ({len(failures)}):")
        for name, tb in failures[:10]:
            _log(f"  {name}: {tb.strip().splitlines()[-1]}")
    else:
        _log("No failures.")


def _delete_docs(doctype, names, started, every):
    """frappe.delete_doc refuses a submitted document even with force=True — the guard in
    frappe.model.delete_doc reads the loaded doc's docstatus and has no override. Everything
    here is being destroyed along with its ledger entries, so the docstatus is moved to
    Cancelled in the database first rather than running each document through a real cancel
    whose entire output (reversal GL Entries, restored movements) is deleted moments later."""
    failures = []
    if frappe.get_meta(doctype).is_submittable:
        for chunk in _chunks(names):
            frappe.db.sql(
                f"update `tab{doctype}` set docstatus = 2 where name in %(n)s and docstatus = 1",
                {"n": chunk},
            )
        frappe.db.commit()

    for index, name in enumerate(names, 1):
        try:
            if frappe.db.exists(doctype, name):
                frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
        except Exception:
            failures.append((f"{doctype} {name}", traceback.format_exc(limit=2)))
        if index % every == 0 or index == len(names):
            rate = index / max(time.time() - started, 0.001)
            _log(f"    {doctype}: {index}/{len(names)}  ({rate:.0f}/s cumulative, {len(failures)} failed)")
    return failures
