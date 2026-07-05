"""Tests for the EDA engine (:mod:`panel_live_server.eda`)."""

import importlib.util

import numpy as np
import pandas as pd
import panel as pn
import pytest

from panel_live_server import eda


@pytest.fixture
def mixed_df():
    n = 80
    rng = np.random.default_rng(0)
    return pd.DataFrame(
        {
            "num1": np.linspace(0, 10, n),
            "num2": np.linspace(10, 0, n) + rng.normal(0, 0.1, n),
            "cat": (["a", "b", "c", "d"] * n)[:n],
            "flag": [True, False] * (n // 2),
            "when": pd.date_range("2020-01-01", periods=n, freq="D"),
        }
    )


# --- load_source ------------------------------------------------------------


def test_load_source_csv(tmp_path):
    p = tmp_path / "data.csv"
    pd.DataFrame({"a": [1, 2], "b": ["x", "y"]}).to_csv(p, index=False)
    df = eda.load_source(str(p))
    assert list(df.columns) == ["a", "b"]
    assert len(df) == 2


def test_load_source_parquet(tmp_path):
    pytest.importorskip("pyarrow")
    p = tmp_path / "data.parquet"
    pd.DataFrame({"a": [1, 2, 3]}).to_parquet(p)
    df = eda.load_source(str(p))
    assert len(df) == 3


def test_load_source_json(tmp_path):
    p = tmp_path / "data.json"
    pd.DataFrame({"a": [1, 2]}).to_json(p)
    df = eda.load_source(str(p))
    assert "a" in df.columns


def test_load_source_missing_file_raises():
    with pytest.raises(ValueError, match="not found"):
        eda.load_source("/no/such/file.csv")


def test_load_source_unsupported_format_raises(tmp_path):
    p = tmp_path / "data.xyz"
    p.write_text("nope")
    with pytest.raises(ValueError, match="Unsupported"):
        eda.load_source(str(p))


def test_load_source_empty_input_raises():
    with pytest.raises(ValueError, match="No data source"):
        eda.load_source("")


# --- classify_columns -------------------------------------------------------


def test_classify_columns_covers_each_type():
    n = 100
    df = pd.DataFrame(
        {
            "num": [float(i) for i in range(n)],
            "cat": (["a", "b", "c"] * n)[:n],
            "dt_native": pd.date_range("2020-01-01", periods=n, freq="D"),
            "dt_string": [(pd.Timestamp("2021-01-01") + pd.Timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n)],
            "flag": [True, False] * (n // 2),
            "yesno": (["yes", "no"] * n)[:n],
            "const": [7] * n,
            "user_id": list(range(1000, 1000 + n)),
            "hi_card": [f"c{i % 60}" for i in range(n)],
            "text": [f"this is a fairly long free text field number {i % 40} with plenty of words in it" for i in range(n)],
        }
    )
    types = eda.classify_columns(df)
    assert types["num"] == "numeric"
    assert types["cat"] == "categorical"
    assert types["dt_native"] == "datetime"
    assert types["dt_string"] == "datetime"
    assert types["flag"] == "boolean"
    assert types["yesno"] == "boolean"
    assert types["const"] == "constant"
    assert types["user_id"] == "id"
    assert types["hi_card"] == "high_cardinality"
    assert types["text"] == "text"


def test_classify_all_null_is_constant():
    df = pd.DataFrame({"x": [None, None, None]})
    assert eda.classify_columns(df)["x"] == "constant"


def test_classify_empty_frame_returns_empty():
    assert eda.classify_columns(pd.DataFrame()) == {}


def test_classify_zero_one_ints_are_boolean():
    df = pd.DataFrame({"flag": [0, 1, 1, 0, 1]})
    assert eda.classify_columns(df)["flag"] == "boolean"


def test_classify_two_value_ints_not_boolean():
    df = pd.DataFrame({"rating": [1, 2, 2, 1, 2]})
    assert eda.classify_columns(df)["rating"] == "numeric"


# --- _sample_and_cap --------------------------------------------------------


def test_sample_and_cap_rows(monkeypatch):
    monkeypatch.setattr(eda, "MAX_PROFILE_ROWS", 10)
    df = pd.DataFrame({"a": range(50)})
    out, notes = eda._sample_and_cap(df)
    assert len(out) == 10
    assert any("Sampled" in note for note in notes)


def test_sample_and_cap_columns(monkeypatch):
    monkeypatch.setattr(eda, "MAX_PROFILE_COLS", 3)
    df = pd.DataFrame({f"c{i}": [1, 2] for i in range(10)})
    out, notes = eda._sample_and_cap(df)
    assert out.shape[1] == 3
    assert any("columns" in note for note in notes)


def test_sample_and_cap_noop_small_frame():
    df = pd.DataFrame({"a": [1, 2, 3]})
    out, notes = eda._sample_and_cap(df)
    assert notes == []
    assert out.equals(df)


# --- Section builders + build_report (Phase 2) ------------------------------


def test_overview_section_is_markdown(mixed_df):
    types = eda.classify_columns(mixed_df)
    comp = eda.overview_section(mixed_df, types, ["a sampling note"])
    assert isinstance(comp, pn.pane.Markdown)
    assert "rows" in comp.object
    assert "a sampling note" in comp.object


def test_summary_table_one_row_per_column(mixed_df):
    types = eda.classify_columns(mixed_df)
    tab = eda.summary_table(mixed_df, types)
    assert isinstance(tab, pn.widgets.Tabulator)
    assert len(tab.value) == mixed_df.shape[1]
    # Per-column numeric stats have their single home here (non-redundancy rule 1).
    assert "mean" in tab.value.columns


def test_distributions_capped(monkeypatch, mixed_df):
    monkeypatch.setattr(eda, "MAX_DISTRIBUTION_PLOTS", 2)
    comp = eda.distributions_section(mixed_df, eda.classify_columns(mixed_df))
    assert comp is not None


def test_distributions_none_when_nothing_plottable():
    df = pd.DataFrame({"user_id": [f"x{i}" for i in range(30)]})
    assert eda.distributions_section(df, eda.classify_columns(df)) is None


def test_missing_section_none_without_missing(mixed_df):
    assert eda.missing_section(mixed_df) is None


def test_missing_section_present_with_missing():
    df = pd.DataFrame({"a": [1, None, 3, None], "b": [None, 2, None, 4], "c": [1, 2, 3, 4]})
    assert eda.missing_section(df) is not None


def test_correlations_none_with_single_numeric():
    df = pd.DataFrame({"num": [1.0, 2, 3, 4, 5], "cat": ["a", "b", "a", "b", "a"]})
    assert eda.correlations_section(df, eda.classify_columns(df)) is None


def test_correlations_present_with_two_numeric(mixed_df):
    comp = eda.correlations_section(mixed_df, eda.classify_columns(mixed_df))
    assert isinstance(comp, pn.Tabs)
    assert len(comp) >= 3  # Pearson, Spearman, Top relationships


def test_outliers_section(mixed_df):
    assert eda.outliers_section(mixed_df, eda.classify_columns(mixed_df)) is not None


def test_alerts_flags_constant_column():
    df = pd.DataFrame({"k": [1, 1, 1, 1], "v": [1, 2, 3, 4]})
    comp = eda.alerts_section(df, eda.classify_columns(df))
    assert isinstance(comp, pn.pane.Markdown)
    assert "constant" in comp.object


def test_build_report_returns_servable_with_tabs(mixed_df):
    rep = eda.build_report(mixed_df, title="My Report")
    assert hasattr(rep, "servable")
    tabs = [o for o in rep if isinstance(o, pn.Tabs)]
    assert tabs and len(tabs[0]) >= 4


def test_build_report_empty_frame_is_placeholder():
    rep = eda.build_report(pd.DataFrame())
    assert hasattr(rep, "servable")


def test_build_report_numeric_only():
    df = pd.DataFrame({"a": [1.0, 2, 3, 4, 5, 6], "b": [2.0, 4, 6, 8, 10, 12]})
    assert hasattr(eda.build_report(df), "servable")


def test_cramers_v_perfect_and_independent():
    x = pd.Series(["a", "b", "c"] * 40)
    assert eda._cramers_v(x, x.copy()) > 0.9
    xi = pd.Series(["a", "a", "b", "b"] * 30)
    yi = pd.Series(["p", "q", "p", "q"] * 30)
    assert eda._cramers_v(xi, yi) < 0.15


def test_chi2_zero_for_uniform_table():
    obs = np.array([[10, 10], [10, 10]], dtype=float)
    assert eda._chi2(obs) == pytest.approx(0.0, abs=1e-9)


# --- Conditional sections (Phase 3) -----------------------------------------


def test_timeseries_section_present(mixed_df):
    assert eda.timeseries_section(mixed_df, eda.classify_columns(mixed_df)) is not None


def test_timeseries_section_none_without_datetime():
    df = pd.DataFrame({"a": [1.0, 2, 3], "b": [4.0, 5, 6]})
    assert eda.timeseries_section(df, eda.classify_columns(df)) is None


def test_focus_section_none_when_column_missing(mixed_df):
    assert eda.focus_section(mixed_df, eda.classify_columns(mixed_df), "nope") is None


def test_focus_section_numeric_target(mixed_df):
    comp = eda.focus_section(mixed_df, eda.classify_columns(mixed_df), "num1")
    assert comp is not None and hasattr(comp, "servable")


def test_focus_section_categorical_target(mixed_df):
    assert eda.focus_section(mixed_df, eda.classify_columns(mixed_df), "cat") is not None


def test_next_steps_flags_missing():
    df = pd.DataFrame({"a": [1, None, None, None, 5], "b": [1, 2, 3, 4, 5]})
    assert "missing" in eda.next_steps_section(df, eda.classify_columns(df)).object.lower()


def test_next_steps_clean_data_has_capstone():
    df = pd.DataFrame({"a": [1.0, 2, 3, 4, 5, 6, 7, 8], "b": [8.0, 7, 6, 5, 4, 3, 2, 1]})
    assert "next steps" in eda.next_steps_section(df, eda.classify_columns(df)).object.lower()


def test_build_report_focus_adds_exactly_one_tab(mixed_df):
    def n_tabs(rep):
        return len([o for o in rep if isinstance(o, pn.Tabs)][0])

    assert n_tabs(eda.build_report(mixed_df, focus="cat")) == n_tabs(eda.build_report(mixed_df)) + 1


# --- compute_summary (Phase 4) ----------------------------------------------


def test_compute_summary_basic(mixed_df):
    s = eda.compute_summary(mixed_df, focus="num1")
    assert s["rows"] == len(mixed_df)
    assert s["columns"] == mixed_df.shape[1]
    assert isinstance(s["column_types"], dict)
    assert "strongest_relationships" in s
    assert s["focus"] == "num1"


def test_compute_summary_missing_and_alerts():
    df = pd.DataFrame({"a": [1, None, None, None, 5], "k": [1, 1, 1, 1, 1]})
    s = eda.compute_summary(df)
    assert s["top_missing_columns"]
    assert any("constant" in al for al in s["alerts"])


# --- Weakness fixes: heuristics (W2) ----------------------------------------


def test_unique_spaced_strings_not_misclassified_as_id():
    # Fully-unique but multi-word values are NOT identifiers (W2a).
    df = pd.DataFrame({"product": [f"Blue Widget {i} XL" for i in range(60)]})
    assert eda.classify_columns(df)["product"] != "id"


def test_unique_token_strings_are_id():
    df = pd.DataFrame({"code": [f"{i:08x}deadbeef" for i in range(60)]})
    assert eda.classify_columns(df)["code"] == "id"


def test_mutual_info_catches_nonlinear_where_correlation_fails():
    rng = np.random.default_rng(0)
    x = pd.Series(rng.uniform(-1, 1, 2000))
    y = x**2  # zero linear correlation, strong dependence
    assert abs(float(x.corr(y))) < 0.1
    assert eda._mutual_info(x, y) > 0.3


def test_mutual_info_zero_for_independent():
    rng = np.random.default_rng(1)
    x = pd.Series(rng.uniform(0, 1, 2000))
    y = pd.Series(rng.uniform(0, 1, 2000))
    assert eda._mutual_info(x, y) < 0.1


def test_focus_section_uses_mutual_information_label(mixed_df):
    comp = eda.focus_section(mixed_df, eda.classify_columns(mixed_df), "num1")
    text = " ".join(p.object for p in comp if isinstance(p, pn.pane.Markdown))
    assert "mutual information" in text.lower()


def test_interest_score_prefers_skewed_over_uniform():
    rng = np.random.default_rng(2)
    uniform = pd.Series(rng.uniform(0, 1, 1000))
    skewed = pd.Series(rng.exponential(2.0, 1000))
    assert eda._interest_score(skewed, "numeric") > eda._interest_score(uniform, "numeric")


def test_timeseries_uses_small_multiples_layout(mixed_df):
    import holoviews as hv

    comp = eda.timeseries_section(mixed_df, eda.classify_columns(mixed_df))
    hv_obj = comp.object if isinstance(comp, pn.pane.HoloViews) else comp[-1].object
    # Small multiples => a HoloViews Layout of per-column subplots, not one overlay.
    assert isinstance(hv_obj, (hv.Layout, hv.NdLayout))


# --- Weakness fixes: robustness (W3) ----------------------------------------


@pytest.mark.skipif(
    importlib.util.find_spec("pyarrow") is not None or importlib.util.find_spec("fastparquet") is not None,
    reason="a parquet engine is installed, so the missing-engine path can't be exercised",
)
def test_parquet_missing_engine_gives_actionable_error(tmp_path):
    p = tmp_path / "x.parquet"
    p.write_bytes(b"not a real parquet file")
    with pytest.raises(ValueError, match="pyarrow"):
        eda.load_source(str(p))


def test_load_source_from_http_url(tmp_path):
    import functools
    import threading
    from http.server import SimpleHTTPRequestHandler
    from http.server import ThreadingHTTPServer

    pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]}).to_csv(tmp_path / "d.csv", index=False)
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        df = eda.load_source(f"http://127.0.0.1:{port}/d.csv")
        assert list(df.columns) == ["a", "b"]
        assert len(df) == 3
    finally:
        server.shutdown()
        server.server_close()


def test_build_report_large_frame_respects_caps_and_budget():
    import time

    rng = np.random.default_rng(0)
    n, m = 150_000, 60
    df = pd.DataFrame({f"c{i}": rng.normal(size=n) for i in range(m)})
    sampled, notes = eda._sample_and_cap(df)
    assert len(sampled) == eda.MAX_PROFILE_ROWS
    assert sampled.shape[1] == eda.MAX_PROFILE_COLS
    assert any("Sampled" in x for x in notes)
    assert any("columns" in x for x in notes)
    t0 = time.perf_counter()
    rep = eda.build_report(df)
    pn.panel(rep).get_root()  # force full model-tree build
    assert time.perf_counter() - t0 < 25.0


# --- Regression: render-time errors masked by dynamic Tabs ------------------


def test_boolean_column_distribution_renders():
    """A boolean column's bar chart must render (used to raise 'numpy boolean subtract')."""
    df = pd.DataFrame({"flag": [True, False] * 50, "x": list(range(100))})
    types = eda.classify_columns(df)
    pn.panel(eda.distributions_section(df, types)).get_root()  # forces Bokeh build; must not raise


def test_every_report_tab_force_renders():
    """Force-render EACH tab's content.

    ``build_report`` uses ``dynamic=True`` Tabs, which only build the active tab —
    so a render-time error in another tab (e.g. the boolean-bar bug) slips past
    ``get_root`` on the whole report and past ``validate_code``. This iterates the
    tabs and renders each, which is what actually catches such errors.
    """
    rng = np.random.default_rng(0)
    n = 120
    df = pd.DataFrame(
        {
            "num": rng.normal(size=n),
            "skew": rng.exponential(2, n),
            "cat": rng.choice(list("abcd"), n),
            "flag": [True, False] * (n // 2),
            "when": pd.date_range("2021-01-01", periods=n, freq="D"),
            "target": rng.integers(0, 2, n),
        }
    )
    df.loc[rng.random(n) < 0.1, "num"] = np.nan
    rep = eda.build_report(df, focus="target", title="T")
    tabs = [o for o in rep if isinstance(o, pn.Tabs)][0]
    for obj in tabs:
        pn.panel(obj).get_root()  # must not raise for any tab
