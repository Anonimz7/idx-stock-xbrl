"""Browser flow: tab reuse, Laporan Keuangan, and year selection."""

from __future__ import annotations

from typing import Any

import pytest
from firefox_bridge.idx import browser_flow
from firefox_bridge.idx.browser_flow import (
    ensure_open,
    find_reusable_profile_tab,
    open_laporan_keuangan,
    prepare_stock_year,
    reload_tab,
    select_year_dropdown,
    wait_for_detected_links,
    wait_for_load,
)

from conftest import make_snapshot


def test_ensure_open_reuses_existing_profile_tab(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    navigations: list[tuple[str, str]] = []

    class FakeClient:
        def tabs(self) -> list[dict[str, Any]]:
            return [
                {
                    "id": 9,
                    "url": (
                        "https://www.idx.co.id/id/perusahaan-tercatat/"
                        "profil-perusahaan-tercatat/BBCA"
                    ),
                }
            ]

        def navigate(self, tab_id: str, url: str) -> None:
            navigations.append((tab_id, url))

        def open_tab(self, *_args: Any, **_kwargs: Any) -> None:
            raise AssertionError("must reuse the existing profile tab")

    monkeypatch.setattr(
        browser_flow,
        "wait_for_load",
        lambda _client, _fragment, _timeout: "9",
    )

    tab_id = ensure_open(FakeClient(), "NCKL")  # type: ignore[arg-type]

    assert tab_id == "9"
    assert navigations == [
        (
            "9",
            "https://www.idx.co.id/id/perusahaan-tercatat/"
            "profil-perusahaan-tercatat/NCKL",
        )
    ]


def test_ensure_open_opens_a_tab_when_none_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[tuple[str, bool]] = []

    class FakeClient:
        def tabs(self) -> list[dict[str, Any]]:
            return [{"id": 1, "url": "https://example.invalid/"}]

        def navigate(self, *_args: Any, **_kwargs: Any) -> None:
            raise AssertionError("must not navigate an unrelated tab")

        def open_tab(self, url: str, *, active: bool = True) -> None:
            opened.append((url, active))

    monkeypatch.setattr(browser_flow, "wait_for_load", lambda *_a, **_k: "15")

    ensure_open(FakeClient(), "NCKL")  # type: ignore[arg-type]

    assert opened == [
        (
            "https://www.idx.co.id/id/perusahaan-tercatat/"
            "profil-perusahaan-tercatat/NCKL",
            True,
        )
    ]


def test_find_reusable_profile_tab_ignores_other_tabs() -> None:
    class FakeClient:
        def tabs(self) -> list[dict[str, Any]]:
            return [
                {"id": 1, "url": "https://www.idx.co.id/id/data-pasar/ringkasan"},
                {
                    "id": 2,
                    "url": (
                        "https://www.idx.co.id/id/perusahaan-tercatat/"
                        "profil-perusahaan-tercatat/NCKL"
                    ),
                },
            ]

    assert find_reusable_profile_tab(FakeClient()) == "2"  # type: ignore[arg-type]


def test_reload_tab_navigates_to_the_current_url() -> None:
    navigations: list[tuple[str, str]] = []

    class FakeClient:
        def tabs(self) -> list[dict[str, Any]]:
            return [{"id": 15, "url": "https://www.idx.co.id/page"}]

        def navigate(self, tab_id: str, url: str) -> None:
            navigations.append((tab_id, url))

    reload_tab(FakeClient(), "15")  # type: ignore[arg-type]

    assert navigations == [("15", "https://www.idx.co.id/page")]


def test_reload_tab_requires_a_known_tab() -> None:
    class FakeClient:
        def tabs(self) -> list[dict[str, Any]]:
            return []

    with pytest.raises(RuntimeError):
        reload_tab(FakeClient(), "15")  # type: ignore[arg-type]


def test_wait_for_load_returns_when_the_tab_is_complete(
    no_sleep: None,
) -> None:
    class FakeClient:
        def tabs(self) -> list[dict[str, Any]]:
            return [{"id": 15, "url": "https://www.idx.co.id/page", "status": "complete"}]

    assert wait_for_load(FakeClient(), "/page") == "15"  # type: ignore[arg-type]


def test_wait_for_load_reloads_a_stuck_loading_tab(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"now": 0.0}
    monkeypatch.setattr(
        browser_flow.time,
        "sleep",
        lambda seconds: clock.__setitem__("now", clock["now"] + seconds),
    )
    monkeypatch.setattr(browser_flow.time, "monotonic", lambda: clock["now"])
    navigations: list[str] = []

    class FakeClient:
        def __init__(self) -> None:
            self.reloaded = False

        def tabs(self) -> list[dict[str, Any]]:
            return [
                {
                    "id": 15,
                    "url": "https://www.idx.co.id/page",
                    "status": "complete" if self.reloaded else "loading",
                }
            ]

        def navigate(self, tab_id: str, _url: str) -> None:
            navigations.append(tab_id)
            self.reloaded = True

    assert wait_for_load(FakeClient(), "/page") == "15"  # type: ignore[arg-type]
    assert navigations == ["15"]


def test_wait_for_load_times_out(no_sleep: None) -> None:
    class FakeClient:
        def tabs(self) -> list[dict[str, Any]]:
            return [{"id": 15, "url": "https://www.idx.co.id/page", "status": "loading"}]

        def navigate(self, *_args: Any, **_kwargs: Any) -> None:
            return None

    with pytest.raises(TimeoutError):
        wait_for_load(FakeClient(), "/page", timeout=0.01)  # type: ignore[arg-type]


def test_select_year_dropdown_uses_only_year_searchbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    year_control = {"ref": "e35", "role": "searchbox", "name": "2026 Loading..."}
    typed_year_control = {**year_control, "value": "2025"}
    selected_control = {"ref": "e35", "role": "searchbox", "name": "2025 Loading..."}
    snapshots = iter(
        [
            make_snapshot([typed_year_control, {"ref": "e37", "role": "option", "name": "2025"}]),
            make_snapshot([selected_control]),
        ]
    )

    class FakeClient:
        def __init__(self) -> None:
            self.opened: list[tuple[str, str]] = []
            self.clicked: list[str] = []

        def snapshot(self, **_kwargs: Any) -> dict[str, Any]:
            return next(snapshots)

        def select_dropdown(self, *, ref: str, value: str, **_kwargs: Any) -> dict[str, Any]:
            self.opened.append((ref, value))
            return {"opened": True, "value": value}

        def click(self, *, ref: str, **_kwargs: Any) -> dict[str, Any]:
            self.clicked.append(ref)
            return {"clicked": True, "ref": ref}

    monkeypatch.setattr(browser_flow.time, "sleep", lambda _seconds: None)
    client = FakeClient()

    assert select_year_dropdown(client, "15", 2025, initial_dropdown=year_control)  # type: ignore[arg-type]
    assert client.opened == [("e35", "2025")]
    assert client.clicked == ["e37"]


def test_select_year_dropdown_short_circuits_when_year_already_selected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        def snapshot(self, **_kwargs: Any) -> dict[str, Any]:
            raise AssertionError("must not re-read the page")

    monkeypatch.setattr(browser_flow.time, "sleep", lambda _seconds: None)
    dropdown = {"ref": "e35", "role": "searchbox", "name": "2025 Loading..."}

    assert select_year_dropdown(FakeClient(), "15", 2025, initial_dropdown=dropdown)  # type: ignore[arg-type]


def test_select_year_dropdown_reports_a_missing_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        def snapshot(self, **_kwargs: Any) -> dict[str, Any]:
            return make_snapshot([])

    monkeypatch.setattr(browser_flow.time, "sleep", lambda _seconds: None)

    assert not select_year_dropdown(FakeClient(), "15", 2025)  # type: ignore[arg-type]


def test_open_laporan_keuangan_returns_an_already_open_panel(
    no_sleep: None,
) -> None:
    class FakeClient:
        def snapshot(self, **_kwargs: Any) -> dict[str, Any]:
            return make_snapshot(
                [{"ref": "e35", "role": "searchbox", "name": "2026 Loading..."}]
            )

        def click(self, **_kwargs: Any) -> dict[str, Any]:
            raise AssertionError("must not click when the panel is open")

    control = open_laporan_keuangan(FakeClient(), "15", "NCKL")  # type: ignore[arg-type]

    assert control["ref"] == "e35"


def test_open_laporan_keuangan_clicks_the_button_when_closed(
    no_sleep: None,
) -> None:
    snapshots = iter(
        [
            make_snapshot([{"ref": "e28", "role": "button", "name": "Laporan Keuangan"}]),
            make_snapshot([{"ref": "e35", "role": "searchbox", "name": "2026 Loading..."}]),
        ]
    )
    clicked: list[str] = []

    class FakeClient:
        def snapshot(self, **_kwargs: Any) -> dict[str, Any]:
            return next(snapshots)

        def click(self, *, ref: str, **_kwargs: Any) -> dict[str, Any]:
            clicked.append(ref)
            return {"clicked": True, "ref": ref}

    control = open_laporan_keuangan(FakeClient(), "15", "NCKL")  # type: ignore[arg-type]

    assert clicked == ["e28"]
    assert control["ref"] == "e35"


def test_open_laporan_keuangan_requires_the_button(no_sleep: None) -> None:
    class FakeClient:
        def snapshot(self, **_kwargs: Any) -> dict[str, Any]:
            return make_snapshot([])

    with pytest.raises(RuntimeError):
        open_laporan_keuangan(FakeClient(), "15", "NCKL")  # type: ignore[arg-type]


def test_open_laporan_keuangan_requires_the_year_control(no_sleep: None) -> None:
    class FakeClient:
        def snapshot(self, **_kwargs: Any) -> dict[str, Any]:
            return make_snapshot(
                [{"ref": "e28", "role": "button", "name": "Laporan Keuangan"}]
            )

        def click(self, **_kwargs: Any) -> dict[str, Any]:
            return {"clicked": True}

    # `TimeoutError` specifically, not `RuntimeError`: the page layer must not
    # import the file layer, so it raises a built-in and the CLI translates it
    # into the downloader taxonomy at the boundary. Asserting the built-in pins
    # that layering down, so a future import shortcut breaks here.
    with pytest.raises(TimeoutError, match="Kontrol tahun tidak muncul"):
        open_laporan_keuangan(FakeClient(), "15", "NCKL", timeout=0.01)  # type: ignore[arg-type]


def test_prepare_stock_year_opens_laporan_keuangan_before_year(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    year_control = {"ref": "e35", "role": "searchbox", "name": "2026 Loading..."}
    monkeypatch.setattr(browser_flow.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        browser_flow,
        "ensure_open",
        lambda *_args, **_kwargs: events.append("profile") or "15",
    )
    monkeypatch.setattr(
        browser_flow,
        "open_laporan_keuangan",
        lambda *_args, **_kwargs: events.append("laporan") or year_control,
    )
    monkeypatch.setattr(
        browser_flow,
        "select_year_dropdown",
        lambda *_args, **_kwargs: events.append("year") or True,
    )

    assert prepare_stock_year(None, "NCKL", 2025) == "15"  # type: ignore[arg-type]
    assert events == ["profile", "laporan", "year"]


def test_prepare_stock_year_fails_when_the_year_cannot_be_selected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(browser_flow.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(browser_flow, "ensure_open", lambda *_a, **_k: "15")
    monkeypatch.setattr(browser_flow, "open_laporan_keuangan", lambda *_a, **_k: {})
    monkeypatch.setattr(browser_flow, "select_year_dropdown", lambda *_a, **_k: False)

    with pytest.raises(RuntimeError):
        prepare_stock_year(None, "NCKL", 2025)  # type: ignore[arg-type]


def test_wait_for_detected_links_returns_sorted_links(
    no_sleep: None,
) -> None:
    base = "https://www.idx.co.id/Laporan%20Keuangan%20Tahun%202025"
    snapshot = make_snapshot(
        [
            {"ref": "e2", "href": f"{base}/TW2/NCKL/inlineXBRL.zip"},
            {"ref": "e1", "href": f"{base}/TW1/NCKL/inlineXBRL.zip"},
        ]
    )

    class FakeClient:
        def snapshot(self, **_kwargs: Any) -> dict[str, Any]:
            return snapshot

    links, returned = wait_for_detected_links(FakeClient(), "15", 2025)  # type: ignore[arg-type]

    assert [link["ref"] for link in links] == ["e1", "e2"]
    assert returned is snapshot


def test_wait_for_detected_links_times_out(no_sleep: None) -> None:
    class FakeClient:
        def snapshot(self, **_kwargs: Any) -> dict[str, Any]:
            return make_snapshot([])

    with pytest.raises(RuntimeError):
        wait_for_detected_links(FakeClient(), "15", 2025, timeout=0.01)  # type: ignore[arg-type]
