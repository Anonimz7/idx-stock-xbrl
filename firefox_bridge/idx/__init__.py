"""IDX domain layer: page semantics, selectors, and browser flow."""

from __future__ import annotations

from .browser_flow import (
    PROFILE_PATH_PREFIX,
    PROFILE_URL,
    ensure_open,
    find_reusable_profile_tab,
    open_laporan_keuangan,
    prepare_stock_year,
    select_year_dropdown,
    wait_for_detected_links,
    wait_for_load,
)
from .link_parser import (
    find_inline_xbrl_link,
    find_inline_xbrl_links,
    quarter_from_report_href,
    report_links,
)
from .models import ReportLink, ReportTarget
from .selectors import (
    find_dropdown_option_ref,
    find_laporan_keuangan_ref,
    find_year_dropdown_ref,
)

__all__ = [
    "PROFILE_PATH_PREFIX",
    "PROFILE_URL",
    "ReportLink",
    "ReportTarget",
    "ensure_open",
    "find_dropdown_option_ref",
    "find_inline_xbrl_link",
    "find_inline_xbrl_links",
    "find_laporan_keuangan_ref",
    "find_reusable_profile_tab",
    "find_year_dropdown_ref",
    "open_laporan_keuangan",
    "prepare_stock_year",
    "quarter_from_report_href",
    "report_links",
    "select_year_dropdown",
    "wait_for_detected_links",
    "wait_for_load",
]
