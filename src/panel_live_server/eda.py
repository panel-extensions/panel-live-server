"""Native HoloViz exploratory-data-analysis engine.

This module builds a comprehensive, non-redundant, interactive EDA report from a
pandas ``DataFrame`` and returns it as a servable Panel object. It is executed as
a small generated snippet through the existing ``show`` render pipeline (see the
``auto_eda`` MCP tool in :mod:`panel_live_server.server`), so all of the heavy
analysis logic lives here in trusted package code rather than in generated code.

Design rules (enforced across the section builders):

* Every fact about the dataset has exactly one home. The dense summary table is
  the backbone; nothing else repeats per-column numbers.
* Sections render only when applicable and are skipped otherwise.
* Only ``pandas`` and ``numpy`` are assumed available. Richer measures
  (``phik``, ``scipy``) are used opportunistically when importable and degrade
  gracefully when not.
"""

import re
import warnings
from collections import Counter
from contextlib import contextmanager
from itertools import combinations
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import hvplot.pandas  # noqa: F401  (registers the .hvplot accessor on DataFrame/Series)
import numpy as np
import pandas as pd
import panel as pn
from pandas.api.types import is_bool_dtype
from pandas.api.types import is_datetime64_any_dtype
from pandas.api.types import is_numeric_dtype

# --- Tuning constants -------------------------------------------------------

#: Rows above this are sampled before profiling to stay within the render budget.
MAX_PROFILE_ROWS = 100_000

#: At most this many columns are fully profiled; the rest are noted, never dropped silently.
MAX_PROFILE_COLS = 50

#: A non-numeric column with unique-ratio above this is flagged high-cardinality.
HIGH_CARDINALITY_RATIO = 0.5

#: ...and also flagged high-cardinality when it exceeds this absolute distinct count.
HIGH_CARDINALITY_ABS = 50

#: Z-score magnitude above which a numeric value is counted as an outlier.
OUTLIER_Z_THRESHOLD = 3.0

#: Maximum number of distribution charts rendered (curated, not exhaustive).
MAX_DISTRIBUTION_PLOTS = 12

#: Per-column missing fraction above which a data-quality alert is raised.
MISSING_ALERT_THRESHOLD = 0.2

#: Absolute correlation above which two numeric columns are flagged as redundant.
HIGH_CORR_THRESHOLD = 0.9

#: ``|skew|`` above which a numeric column is flagged as highly skewed.
SKEW_THRESHOLD = 2.0

#: Top-class fraction above which a categorical column is flagged as imbalanced.
IMBALANCE_THRESHOLD = 0.95

#: Column names (case-insensitive) that mark a column as an identifier.
_ID_NAME = re.compile(r"(^|[_\s])(id|uuid|guid|key)$", re.IGNORECASE)

#: Column type labels produced by :func:`classify_columns`.
ColType = Literal[
    "numeric",
    "categorical",
    "datetime",
    "boolean",
    "text",
    "constant",
    "high_cardinality",
    "id",
]


# --- Data layer -------------------------------------------------------------


def load_source(source: str) -> pd.DataFrame:
    """Load a tabular source into a pandas ``DataFrame``.

    Parameters
    ----------
    source : str
        A local file path or ``http(s)`` URL pointing at a CSV, Parquet, or JSON
        file. The format is inferred from the extension.

    Returns
    -------
    pandas.DataFrame
        The loaded table.

    Raises
    ------
    ValueError
        If the source cannot be read or the format is unsupported.
    """
    if not isinstance(source, str) or not source.strip():
        raise ValueError("No data source provided.")
    source = source.strip()

    parsed = urlparse(source)
    is_url = parsed.scheme in ("http", "https")
    # Infer the format from the path component, ignoring any query string.
    path_part = parsed.path if is_url else source
    suffix = Path(path_part).suffix.lower()

    if not is_url and not Path(source).exists():
        raise ValueError(f"Data source not found: {source!r}")

    readers = {
        ".csv": lambda s: pd.read_csv(s),
        ".tsv": lambda s: pd.read_csv(s, sep="\t"),
        ".txt": lambda s: pd.read_csv(s),
        ".parquet": lambda s: pd.read_parquet(s),
        ".pq": lambda s: pd.read_parquet(s),
        ".json": lambda s: pd.read_json(s),
        ".jsonl": lambda s: pd.read_json(s, lines=True),
        ".ndjson": lambda s: pd.read_json(s, lines=True),
    }
    reader = readers.get(suffix)
    if reader is None:
        raise ValueError(f"Unsupported data format {suffix or '(none)'!r} for {source!r}. Supported: .csv, .tsv, .parquet, .json, .jsonl.")

    try:
        df = reader(source)
    except ImportError as e:
        raise ValueError(f"Reading {suffix} files needs an extra package that is not installed ({e}). For Parquet, install 'pyarrow' (pip install pyarrow).") from e
    except Exception as e:
        raise ValueError(f"Failed to read {source!r}: {e}") from e

    if not isinstance(df, pd.DataFrame):
        df = pd.DataFrame(df)
    return df


def classify_columns(df: pd.DataFrame) -> dict[str, ColType]:
    """Classify every column into a single semantic type.

    Parameters
    ----------
    df : pandas.DataFrame
        The (already sampled/capped) frame to classify.

    Returns
    -------
    dict[str, ColType]
        Mapping of column name to its inferred :data:`ColType`.
    """
    types: dict[str, ColType] = {}
    for col in df.columns:
        s = df[col]
        non_null = s.dropna()
        nunique = int(non_null.nunique())

        if nunique <= 1:
            types[col] = "constant"
        elif is_bool_dtype(s) or _looks_boolean(s):
            types[col] = "boolean"
        elif is_datetime64_any_dtype(s) or (not is_numeric_dtype(s) and _looks_datetime(s)):
            types[col] = "datetime"
        elif _is_id_like(str(col), s):
            types[col] = "id"
        elif is_numeric_dtype(s):
            types[col] = "numeric"
        else:
            # Remaining object / category / string columns.
            unique_ratio = nunique / len(non_null) if len(non_null) else 0.0
            avg_len = float(non_null.astype(str).str.len().mean()) if len(non_null) else 0.0
            if avg_len > 50:
                types[col] = "text"
            elif nunique > HIGH_CARDINALITY_ABS or unique_ratio > HIGH_CARDINALITY_RATIO:
                types[col] = "high_cardinality"
            else:
                types[col] = "categorical"
    return types


def _looks_boolean(s: pd.Series) -> bool:
    """Return True if a two-value series encodes a boolean."""
    if is_bool_dtype(s):
        return True
    vals = set(s.dropna().unique())
    if len(vals) != 2:
        return False
    lowered = {str(v).strip().lower() for v in vals}
    return lowered in ({"true", "false"}, {"0", "1"}, {"yes", "no"}, {"t", "f"}, {"y", "n"})


def _looks_datetime(s: pd.Series) -> bool:
    """Return True if an object series parses as datetimes for most values."""
    sample = s.dropna()
    if sample.empty:
        return False
    if len(sample) > 1000:
        sample = sample.head(1000)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            parsed = pd.to_datetime(sample, errors="coerce")
    except Exception:
        return False
    return bool(parsed.notna().mean() >= 0.9)


def _is_id_like(name: str, s: pd.Series) -> bool:
    """Return True if a column looks like a unique identifier."""
    non_null = s.dropna()
    if len(non_null) < 20:
        # Too few rows to be confident from the values alone; trust the name.
        return bool(_ID_NAME.search(name)) and bool(non_null.is_unique)
    unique_ratio = non_null.nunique() / len(non_null)
    if _ID_NAME.search(name) and unique_ratio >= 0.99:
        return True
    # Unlabelled: only treat perfectly-unique, non-numeric columns as IDs when the
    # values look like identifier tokens (short, whitespace-free — UUIDs, hashes,
    # codes). Free text and multi-word names (which are also often unique) are NOT ids.
    if unique_ratio == 1.0 and not is_numeric_dtype(s):
        return _token_like(non_null)
    return False


def _token_like(s: pd.Series) -> bool:
    """Return True if the sampled string values look like identifier tokens."""
    sample = s.head(200).astype(str)
    if sample.empty:
        return False
    has_space = float(sample.str.contains(r"\s", regex=True).mean())
    max_len = int(sample.str.len().max())
    return has_space < 0.01 and max_len <= 64


def _sample_and_cap(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Sample rows and cap columns to keep profiling within the render budget.

    Parameters
    ----------
    df : pandas.DataFrame
        The full input frame.

    Returns
    -------
    tuple[pandas.DataFrame, list[str]]
        The reduced frame and a list of human-readable notes describing any
        sampling or column truncation that was applied (never silent).
    """
    notes: list[str] = []
    out = df
    if len(out) > MAX_PROFILE_ROWS:
        notes.append(f"Sampled {MAX_PROFILE_ROWS:,} of {len(out):,} rows for profiling.")
        out = out.sample(MAX_PROFILE_ROWS, random_state=0)
    if out.shape[1] > MAX_PROFILE_COLS:
        notes.append(f"Profiling the first {MAX_PROFILE_COLS} of {out.shape[1]} columns.")
        out = out.iloc[:, :MAX_PROFILE_COLS]
    return out, notes


# --- Report assembly --------------------------------------------------------


@contextmanager
def _quiet():
    """Suppress library warnings while building charts (keeps a report robust)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        yield


def _human_bytes(n: float) -> str:
    """Format a byte count as a short human-readable string."""
    x = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if x < 1024:
            return f"{x:.1f} {unit}"
        x /= 1024
    return f"{x:.1f} TB"


def _cols_of(types: dict, *wanted: str) -> list:
    """Return the column names whose classified type is one of ``wanted``."""
    return [c for c, t in types.items() if t in wanted]


# --- Section builders (each returns a Panel component or None to skip) ------


def overview_section(df: pd.DataFrame, types: dict, notes: list | None = None) -> "object":
    """Aggregate dataset overview: shape, memory, duplicates, missingness, type mix."""
    n_rows, n_cols = df.shape
    mem = df.memory_usage(deep=True).sum()
    dup = int(df.duplicated().sum())
    total_cells = n_rows * n_cols
    missing = int(df.isna().sum().sum())
    missing_pct = (100 * missing / total_cells) if total_cells else 0.0
    type_line = ", ".join(f"{v}× {k}" for k, v in sorted(Counter(types.values()).items()))
    md = [
        f"### {n_rows:,} rows × {n_cols:,} columns",
        "",
        f"- **Memory:** {_human_bytes(mem)}",
        f"- **Duplicate rows:** {dup:,} ({(100 * dup / n_rows) if n_rows else 0:.1f}%)",
        f"- **Missing cells:** {missing:,} ({missing_pct:.1f}%)",
        f"- **Column types:** {type_line}",
    ]
    if notes:
        md += ["", *[f"> {note}" for note in notes]]
    return pn.pane.Markdown("\n".join(md), sizing_mode="stretch_width")


def summary_table(df: pd.DataFrame, types: dict) -> "object":
    """Per-column backbone table (one row per column) — the single home for stats."""
    rows = []
    for col in df.columns:
        s = df[col]
        t = types.get(col, "categorical")
        non_null = s.dropna()
        row = {
            "column": str(col),
            "type": t,
            "non_null": int(non_null.count()),
            "missing_%": round(float(s.isna().mean()) * 100, 1),
            "unique": int(non_null.nunique()),
            "mean": None,
            "std": None,
            "min": None,
            "max": None,
            "top": "",
        }
        if t == "numeric" and len(non_null):
            row["mean"] = round(float(non_null.mean()), 3)
            row["std"] = round(float(non_null.std()), 3)
            row["min"] = round(float(non_null.min()), 3)
            row["max"] = round(float(non_null.max()), 3)
        elif len(non_null):
            row["top"] = str(non_null.value_counts().index[0])[:40]
        rows.append(row)
    with _quiet():
        return pn.widgets.Tabulator(
            pd.DataFrame(rows),
            disabled=True,
            show_index=False,
            sizing_mode="stretch_width",
            layout="fit_data_stretch",
        )


def _interest_score(non_null: pd.Series, t: str) -> float:
    """Heuristic informativeness score in ``[0, 1]`` used to prioritize which distributions to show.

    Numeric columns score on shape (skew + heavy/peaked tails via kurtosis) — a
    plain bell curve is less interesting than a skewed or multi-modal one.
    Categorical columns score on how many distinct levels they carry.
    """
    if non_null.empty:
        return 0.0
    if t == "numeric":
        if len(non_null) <= 2:
            return 0.0
        skew = abs(float(non_null.skew()))
        kurt = abs(float(non_null.kurt()))
        return min(1.0, (skew + kurt / 2.0) / 3.0)
    return min(1.0, (int(non_null.nunique()) - 1) / 9.0)


def distributions_section(df: pd.DataFrame, types: dict) -> "object | None":
    """Curated distribution charts — ranked by informativeness, capped, collapsed."""
    plottable = _cols_of(types, "numeric") + _cols_of(types, "categorical", "boolean")
    if not plottable:
        return None
    with _quiet():
        ranked = sorted(plottable, key=lambda c: _interest_score(df[c].dropna(), types[c]), reverse=True)
    charts = []
    with _quiet():
        for col in ranked[:MAX_DISTRIBUTION_PLOTS]:
            s = df[col].dropna()
            if s.empty:
                continue
            if types[col] == "numeric":
                bins = int(min(30, max(5, s.nunique())))
                chart = s.hvplot.hist(bins=bins, width=400, height=290, title=str(col))
            else:
                # Cast to str so booleans/ints become a categorical axis. A boolean
                # value_counts index makes HoloViews treat the axis as numeric and
                # compute ``True - False`` in get_padding, which numpy rejects.
                counts = s.astype(str).value_counts().head(10)
                # Truncate long category labels (e.g. free-text) so the axis stays readable.
                counts.index = [lbl if len(lbl) <= 18 else lbl[:17] + "…" for lbl in counts.index.astype(str)]
                chart = counts.hvplot.bar(width=400, height=290, rot=45, title=str(col))
            charts.append(pn.pane.HoloViews(chart))
    if not charts:
        return None
    # Arrange fixed-size chart panes as plain Rows inside a Column (2 per row).
    # Plain Panel Row/Column layouts of HoloViews panes render reliably inside
    # dynamic Tabs (this is exactly what the Missingness section does); pn.GridBox
    # and a multi-element hv.Layout both leave the charts blank.
    rows = [pn.Row(*charts[i : i + 2]) for i in range(0, len(charts), 2)]
    grid = pn.Column(*rows)
    if len(ranked) > MAX_DISTRIBUTION_PLOTS:
        return pn.Column(
            pn.pane.Markdown(f"_Showing the {MAX_DISTRIBUTION_PLOTS} most informative of {len(ranked)} distributions._"),
            grid,
            sizing_mode="stretch_width",
        )
    return grid


def missing_section(df: pd.DataFrame) -> "object | None":
    """Cross-column missingness structure (nullity correlation), not per-column %."""
    miss = df.isna()
    cols = [c for c in df.columns if bool(miss[c].any())]
    if not cols:
        return None
    if len(cols) == 1:
        c = cols[0]
        return pn.pane.Markdown(
            f"Only **{c}** has missing values ({100 * float(miss[c].mean()):.1f}%). No cross-column missingness structure to show.",
            sizing_mode="stretch_width",
        )
    with _quiet():
        corr = miss[cols].astype(int).corr()
        m = corr.stack().rename("nullity_corr").reset_index()
        m.columns = ["var1", "var2", "nullity_corr"]
        hm = m.hvplot.heatmap(x="var1", y="var2", C="nullity_corr", cmap="coolwarm", clim=(-1, 1), rot=45, height=350, responsive=True, title="Nullity correlation")
        pane = pn.pane.HoloViews(hm, sizing_mode="stretch_width")
    note = pn.pane.Markdown("Nullity correlation: **+1** two columns go missing together, **−1** one present implies the other missing.")
    return pn.Column(note, pane, sizing_mode="stretch_width")


def _corr_heatmap(corr: pd.DataFrame, title: str) -> "object":
    """Render a correlation matrix as a heatmap pane."""
    m = corr.stack().rename("corr").reset_index()
    m.columns = ["var1", "var2", "corr"]
    hm = m.hvplot.heatmap(x="var1", y="var2", C="corr", cmap="coolwarm", clim=(-1, 1), rot=45, height=380, responsive=True, title=title)
    return pn.pane.HoloViews(hm, sizing_mode="stretch_width")


def _top_pairs(pearson: pd.DataFrame, spearman: pd.DataFrame, k: int = 8) -> "object":
    """Extract the strongest numeric relationships from an already-computed matrix."""
    pairs = [(a, b, float(pearson.loc[a, b]), float(spearman.loc[a, b])) for a, b in combinations(pearson.columns, 2)]
    pairs.sort(key=lambda t: abs(t[2]), reverse=True)
    lines = ["| Pair | Pearson | Spearman | Note |", "|---|---|---|---|"]
    for a, b, r, rs in pairs[:k]:
        note = "nonlinear?" if abs(r - rs) >= 0.3 else ""
        lines.append(f"| {a} ↔ {b} | {r:+.2f} | {rs:+.2f} | {note} |")
    return pn.pane.Markdown("\n".join(lines), sizing_mode="stretch_width")


def _phik_heatmap(frame: pd.DataFrame) -> "object | None":
    """Render a phik (φk) matrix if the optional ``phik`` package is importable."""
    try:
        import phik  # noqa: F401
    except Exception:
        return None
    try:
        with _quiet():
            pm = frame.phik_matrix()
            return _corr_heatmap(pm, "φk (phik)")
    except Exception:
        return None


def _chi2(observed: np.ndarray) -> float:
    """Pearson chi-square statistic for a contingency table (no scipy needed)."""
    observed = observed.astype(float)
    n = observed.sum()
    if n == 0:
        return 0.0
    expected = observed.sum(axis=1, keepdims=True) @ observed.sum(axis=0, keepdims=True) / n
    with np.errstate(divide="ignore", invalid="ignore"):
        terms = np.where(expected > 0, (observed - expected) ** 2 / expected, 0.0)
    return float(terms.sum())


def _cramers_v(x: pd.Series, y: pd.Series) -> float:
    """Bias-corrected Cramér's V association between two categorical series."""
    ct = pd.crosstab(x, y)
    if ct.shape[0] < 2 or ct.shape[1] < 2:
        return float("nan")
    chi2 = _chi2(ct.to_numpy())
    n = ct.to_numpy().sum()
    r, k = ct.shape
    phi2 = max(0.0, chi2 / n - (r - 1) * (k - 1) / (n - 1))
    rcorr = r - (r - 1) ** 2 / (n - 1)
    kcorr = k - (k - 1) ** 2 / (n - 1)
    denom = min(rcorr - 1, kcorr - 1)
    if denom <= 0:
        return float("nan")
    return float(np.sqrt(phi2 / denom))


def _cat_association(df: pd.DataFrame, catish: list) -> "object | None":
    """Ranked categorical associations (Cramér's V) — a list, not a full matrix."""
    cols = [c for c in catish if df[c].nunique(dropna=True) <= 30][:12]
    if len(cols) < 2:
        return None
    pairs = []
    for a, b in combinations(cols, 2):
        v = _cramers_v(df[a], df[b])
        if not np.isnan(v):
            pairs.append((a, b, v))
    if not pairs:
        return None
    pairs.sort(key=lambda t: t[2], reverse=True)
    lines = ["Cramér's V (0 = independent, 1 = perfectly associated):", "", "| Pair | Cramér's V |", "|---|---|"]
    lines += [f"| {a} ↔ {b} | {v:.2f} |" for a, b, v in pairs[:8]]
    return pn.pane.Markdown("\n".join(lines), sizing_mode="stretch_width")


def correlations_section(df: pd.DataFrame, types: dict) -> "object | None":
    """Relationships: one heatmap (toggle) + extracted top pairs + categorical assoc."""
    numeric = _cols_of(types, "numeric")
    catish = _cols_of(types, "categorical", "boolean")
    tabs = []
    with _quiet():
        if len(numeric) >= 2:
            pearson = df[numeric].corr(method="pearson")
            spearman = df[numeric].corr(method="spearman")
            tabs.append(("Pearson", _corr_heatmap(pearson, "Pearson")))
            tabs.append(("Spearman", _corr_heatmap(spearman, "Spearman")))
            phik_hm = _phik_heatmap(df[numeric + catish])
            if phik_hm is not None:
                tabs.append(("φk (phik)", phik_hm))
            tabs.append(("Top relationships", _top_pairs(pearson, spearman)))
    cat_assoc = _cat_association(df, catish)
    if cat_assoc is not None:
        tabs.append(("Categorical association", cat_assoc))
    if not tabs:
        return None
    return pn.Tabs(*tabs, sizing_mode="stretch_width", dynamic=True)


def outliers_section(df: pd.DataFrame, types: dict) -> "object | None":
    """Per-column outlier counts (IQR and z-score)."""
    numeric = _cols_of(types, "numeric")
    if not numeric:
        return None
    rows = []
    for col in numeric:
        s = df[col].dropna()
        if s.empty:
            continue
        q1, q3 = s.quantile(0.25), s.quantile(0.75)
        iqr = q3 - q1
        n_iqr = int(((s < q1 - 1.5 * iqr) | (s > q3 + 1.5 * iqr)).sum()) if iqr > 0 else 0
        std = s.std()
        n_z = int((((s - s.mean()).abs() / std) > OUTLIER_Z_THRESHOLD).sum()) if std and std > 0 else 0
        if n_iqr or n_z:
            rows.append({"column": str(col), "iqr_outliers": n_iqr, "z>3_outliers": n_z, "outlier_%": round(100 * max(n_iqr, n_z) / len(s), 2)})
    if not rows:
        return pn.pane.Markdown("No outliers detected by IQR or z-score.", sizing_mode="stretch_width")
    with _quiet():
        return pn.widgets.Tabulator(pd.DataFrame(rows), disabled=True, show_index=False, sizing_mode="stretch_width")


def _collect_alerts(df: pd.DataFrame, types: dict) -> list:
    """Collect ranked (severity, message) data-quality alerts; shared with the summary."""
    alerts: list[tuple[int, str]] = []
    n = len(df)
    dup = int(df.duplicated().sum())
    if dup:
        alerts.append((2, f"{dup:,} duplicate rows ({100 * dup / n:.1f}%)"))
    for col, t in types.items():
        s = df[col]
        non_null = s.dropna()
        miss = float(s.isna().mean())
        if miss > MISSING_ALERT_THRESHOLD:
            alerts.append((3, f"'{col}' is {miss * 100:.0f}% missing"))
        if t == "constant":
            alerts.append((1, f"'{col}' is constant (a single value)"))
        elif t in ("high_cardinality", "id"):
            alerts.append((1, f"'{col}' has very high cardinality ({non_null.nunique():,} unique)"))
        if t == "numeric" and len(non_null) > 2:
            sk = float(non_null.skew())
            if abs(sk) > SKEW_THRESHOLD:
                alerts.append((1, f"'{col}' is highly skewed (skew={sk:.1f})"))
        if t in ("categorical", "boolean") and len(non_null):
            top_frac = float(non_null.value_counts(normalize=True).iloc[0])
            if top_frac > IMBALANCE_THRESHOLD:
                alerts.append((2, f"'{col}' is imbalanced (top class {top_frac * 100:.0f}%)"))
    numeric = _cols_of(types, "numeric")
    if len(numeric) >= 2:
        with _quiet():
            corr = df[numeric].corr().abs()
        for a, b in combinations(corr.columns, 2):
            r = float(corr.loc[a, b])
            if r > HIGH_CORR_THRESHOLD:
                alerts.append((3, f"'{a}' and '{b}' are highly correlated (|r|={r:.2f}) — possible redundancy/leakage"))
    alerts.sort(key=lambda a: a[0], reverse=True)
    return alerts


def alerts_section(df: pd.DataFrame, types: dict) -> "object":
    """Consolidated, ranked data-quality triage that points to the other sections."""
    alerts = _collect_alerts(df, types)
    if not alerts:
        return pn.pane.Markdown("✓ No major data-quality issues detected.", sizing_mode="stretch_width")
    icon = {3: "\U0001f534", 2: "\U0001f7e0", 1: "\U0001f7e1"}
    lines = [f"- {icon.get(sev, '•')} {text}" for sev, text in alerts]
    return pn.pane.Markdown("\n".join(lines), sizing_mode="stretch_width")


def timeseries_section(df: pd.DataFrame, types: dict) -> "object | None":
    """Trend of numeric columns over the first datetime column (if one exists)."""
    dt_cols = _cols_of(types, "datetime")
    numeric = _cols_of(types, "numeric")
    if not dt_cols or not numeric:
        return None
    tcol = dt_cols[0]
    ycols = numeric[:6]
    with _quiet():
        d = df[[tcol, *ycols]].dropna(subset=[tcol]).sort_values(tcol)
        if len(d) > 2000:
            d = d.iloc[:: len(d) // 2000 + 1]
        # Small multiples: one subplot per column, each with its OWN y-axis, so
        # columns on different scales stay readable (no shared-axis distortion).
        layout = d.hvplot.line(x=tcol, y=ycols, subplots=True, shared_axes=False, height=200, responsive=True).cols(1)
        pane = pn.pane.HoloViews(layout, sizing_mode="stretch_width")
    if len(ycols) < len(numeric):
        return pn.Column(pn.pane.Markdown(f"_Showing {len(ycols)} of {len(numeric)} numeric columns._"), pane, sizing_mode="stretch_width")
    return pane


def _discretize(s: pd.Series, bins: int = 10) -> np.ndarray:
    """Map a series to integer codes: quantile bins for numeric, factorize otherwise."""
    s = s.reset_index(drop=True)
    if is_numeric_dtype(s):
        nun = int(s.nunique())
        if nun <= 1:
            return np.zeros(len(s), dtype=int)
        try:
            codes = pd.qcut(s, q=min(bins, nun), duplicates="drop", labels=False)
        except Exception:
            codes = pd.cut(s, bins=min(bins, max(2, nun)), labels=False, include_lowest=True)
        return pd.Series(codes).fillna(-1).astype(int).to_numpy()
    return pd.factorize(s, sort=False)[0]


def _mutual_info(x: pd.Series, y: pd.Series) -> float:
    """Return normalized mutual information in ``[0, 1]`` between two series (numpy only).

    Numeric columns are quantile-binned; categorical columns are used as-is. Unlike
    correlation, this captures non-linear and non-monotonic dependence. Returns 0
    for independence and ~1 when one variable essentially determines the other.
    """
    pair = pd.DataFrame({"x": x.reset_index(drop=True), "y": y.reset_index(drop=True)}).dropna()
    if len(pair) < 5:
        return float("nan")
    ct = pd.crosstab(_discretize(pair["x"]), _discretize(pair["y"])).to_numpy().astype(float)
    if ct.shape[0] < 2 or ct.shape[1] < 2:
        return 0.0
    pxy = ct / ct.sum()
    px = pxy.sum(axis=1, keepdims=True)
    py = pxy.sum(axis=0, keepdims=True)
    with np.errstate(divide="ignore", invalid="ignore"):
        mi = float(np.nansum(np.where(pxy > 0, pxy * (np.log(pxy) - np.log(px) - np.log(py)), 0.0)))
        hx = float(-np.nansum(np.where(px > 0, px * np.log(px), 0.0)))
        hy = float(-np.nansum(np.where(py > 0, py * np.log(py), 0.0)))
    denom = min(hx, hy)
    if denom <= 0:
        return 0.0
    return float(max(0.0, min(1.0, mi / denom)))


def focus_section(df: pd.DataFrame, types: dict, focus: str) -> "object | None":
    """Target analysis: rank features by mutual information to ``focus`` + leakage hints."""
    if not focus or focus not in df.columns:
        return None
    target_type = types.get(focus, "categorical")
    if target_type not in ("numeric", "categorical", "boolean"):
        return pn.pane.Markdown(f"Target analysis for a '{target_type}' column ({focus}) is not supported.", sizing_mode="stretch_width")

    features = [c for c in df.columns if c != focus and types.get(c) in ("numeric", "categorical", "boolean")]
    parts = [pn.pane.Markdown(f"### Target: **{focus}** ({target_type})", sizing_mode="stretch_width")]

    with _quiet():
        ranked = []
        for c in features:
            mi = _mutual_info(df[c], df[focus])
            if np.isnan(mi):
                continue
            if types[c] == "numeric" and target_type == "numeric":
                r = float(df[[c, focus]].corr().iloc[0, 1])
                sec = f"r={r:+.2f}" if not np.isnan(r) else "—"
            elif types[c] in ("categorical", "boolean") and target_type in ("categorical", "boolean"):
                v = _cramers_v(df[c], df[focus])
                sec = f"V={v:.2f}" if not np.isnan(v) else "—"
            else:
                sec = "—"
            ranked.append((c, mi, sec))
        ranked.sort(key=lambda t: t[1], reverse=True)

        if ranked:
            header = "**Relationship to target** — mutual information (0–1, captures non-linear dependence):"
            lines = [header, "", "| Feature | MI | Linear / assoc |", "|---|---|---|"]
            lines += [f"| {c} | {mi:.2f} | {sec} |" for c, mi, sec in ranked[:10]]
            parts.append(pn.pane.Markdown("\n".join(lines)))
            leak = [c for c, mi, _ in ranked if mi > 0.95]
            if leak:
                parts.append(pn.pane.Markdown(f"⚠ Possible leakage: {', '.join(leak)} almost perfectly determine the target."))

            top = ranked[0][0]
            if target_type == "numeric":
                plot = df.hvplot.scatter(x=top, y=focus, height=320, responsive=True, title=f"{top} vs {focus}")
                parts.append(pn.pane.HoloViews(plot, sizing_mode="stretch_width"))
            elif df[focus].nunique() <= 12:
                numeric_top = next((c for c, _, _ in ranked if types[c] == "numeric"), None)
                if numeric_top:
                    # Cast the grouping column to str so a boolean/int target becomes a
                    # categorical axis (avoids the numpy boolean-subtract render error).
                    box_df = df[[numeric_top, focus]].copy()
                    box_df[focus] = box_df[focus].astype(str)
                    plot = box_df.hvplot.box(y=numeric_top, by=focus, height=320, responsive=True, title=f"{numeric_top} by {focus}")
                    parts.append(pn.pane.HoloViews(plot, sizing_mode="stretch_width"))

    if len(parts) == 1:
        parts.append(pn.pane.Markdown("_No usable feature relationships to the target were found._"))
    return pn.Column(*parts, sizing_mode="stretch_width")


def next_steps_section(df: pd.DataFrame, types: dict) -> "object":
    """Rule-based, deterministic 'what to look at next' derived from the alerts."""
    texts = " ".join(t for _, t in _collect_alerts(df, types)).lower()
    steps = []
    if "missing" in texts:
        steps.append("Decide how to handle missing values (impute or drop) for the flagged columns.")
    if "duplicate" in texts:
        steps.append("Investigate and possibly remove duplicate rows.")
    if "highly correlated" in texts or "leakage" in texts:
        steps.append("Drop or combine redundant, highly-correlated columns before modeling.")
    if "skewed" in texts:
        steps.append("Consider a log or Box-Cox transform for the highly-skewed numeric columns.")
    if "imbalanced" in texts:
        steps.append("Account for class imbalance (resampling or class weights) if you model this.")
    if "high cardinality" in texts:
        steps.append("Encode or reduce high-cardinality columns (target/frequency encoding) before modeling.")
    if not steps:
        steps.append("Data looks clean — proceed to feature engineering or modeling.")
    md = "### Suggested next steps\n\n" + "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1))
    return pn.pane.Markdown(md, sizing_mode="stretch_width")


def advanced_section(df: pd.DataFrame, types: dict) -> "object | None":
    """Advanced numeric diagnostics: PCA variance-explained and multicollinearity (VIF).

    Both are computed with numpy only (no scipy/sklearn) and rendered as tables, so
    they add depth without new chart-rendering risk.
    """
    numeric = _cols_of(types, "numeric")
    if len(numeric) < 2:
        return None
    parts = []
    with _quiet():
        x = df[numeric].dropna()
        if len(x) > 5000:
            x = x.sample(5000, random_state=0)
        if len(x) < 3:
            return None
        xv = x.to_numpy(dtype=float)
        sd = xv.std(axis=0)
        sd[sd == 0] = 1.0
        z = (xv - xv.mean(axis=0)) / sd

        # PCA via SVD.
        try:
            s = np.linalg.svd(z, full_matrices=False, compute_uv=False)
            ratio = s**2 / (s**2).sum()
            cum = np.cumsum(ratio)
            lines = ["### Principal components (variance explained)", "", "| Component | % variance | cumulative |", "|---|---|---|"]
            lines += [f"| PC{i + 1} | {ratio[i] * 100:.1f}% | {cum[i] * 100:.1f}% |" for i in range(min(len(ratio), 8))]
            n90 = int(np.searchsorted(cum, 0.9) + 1)
            lines += ["", f"_{n90} component(s) capture 90% of the variance across {len(numeric)} numeric columns._"]
            parts.append(pn.pane.Markdown("\n".join(lines), sizing_mode="stretch_width"))
        except Exception:
            pass  # PCA is best-effort

        # Variance Inflation Factor (multicollinearity).
        try:
            vifs = []
            for i, col in enumerate(numeric):
                other = np.delete(z, i, axis=1)
                coef, *_ = np.linalg.lstsq(other, z[:, i], rcond=None)
                resid = z[:, i] - other @ coef
                ss_tot = float(((z[:, i] - z[:, i].mean()) ** 2).sum())
                r2 = 1 - float((resid**2).sum()) / ss_tot if ss_tot > 0 else 0.0
                vifs.append((col, float("inf") if r2 >= 0.9999 else 1 / (1 - r2)))
            vifs.sort(key=lambda t: (t[1] if np.isfinite(t[1]) else 1e18), reverse=True)
            lines = ["### Multicollinearity (VIF)", "", "| Column | VIF |", "|---|---|"]
            lines += [f"| {c} | {'∞' if not np.isfinite(v) else f'{v:.1f}'}{' ⚠' if (not np.isfinite(v) or v > 5) else ''} |" for c, v in vifs[:12]]
            lines += ["", "_VIF > 5 (⚠) means a column is largely predictable from the others — a redundancy/multicollinearity signal._"]
            parts.append(pn.pane.Markdown("\n".join(lines), sizing_mode="stretch_width"))
        except Exception:
            pass
    return pn.Column(*parts, sizing_mode="stretch_width") if parts else None


def _narratives(df: pd.DataFrame, types: dict, focus: str = "") -> dict:
    """Compute a short 2-3 line findings summary for each report tab."""
    numeric = _cols_of(types, "numeric")
    catish = _cols_of(types, "categorical", "boolean")
    out: dict[str, str] = {}

    miss_by = (df.isna().mean() * 100).round(1).sort_values(ascending=False)
    top_miss = miss_by.index[0] if len(miss_by) and miss_by.iloc[0] > 0 else None
    out["Summary"] = f"**In brief:** {len(numeric)} numeric and {len(catish)} categorical/boolean columns. " + (
        f"The most incomplete column is `{top_miss}` ({miss_by.iloc[0]:.0f}% missing)." if top_miss is not None else "No missing values."
    )

    with _quiet():
        skewed = [c for c in numeric if len(df[c].dropna()) > 2 and abs(float(df[c].dropna().skew())) > 2]
    eg = f" (e.g. {', '.join('`' + c + '`' for c in skewed[:3])})" if skewed else ""
    dist_txt = f"**In brief:** {len(skewed)} of {len(numeric)} numeric columns are strongly skewed{eg}; "
    out["Distributions"] = dist_txt + f"{len(catish)} categorical columns shown as bar charts."

    miss_cols = [c for c in df.columns if bool(df[c].isna().any())]
    if miss_cols:
        txt = f"**In brief:** {len(miss_cols)} column(s) contain missing values."
        if len(miss_cols) >= 2:
            with _quiet():
                mm = df[miss_cols].isna().astype(int).corr()
            comissing = [(a, b, float(mm.loc[a, b])) for a, b in combinations(mm.columns, 2)]
            best = max(comissing, key=lambda t: t[2], default=None)
            if best and best[2] > 0.5:
                txt += f" `{best[0]}` and `{best[1]}` tend to go missing together (nullity corr {best[2]:.2f}) — likely a shared cause."
        out["Missingness"] = txt

    if len(numeric) >= 2:
        with _quiet():
            corr = df[numeric].corr().abs()
        pairs = [(a, b, float(corr.loc[a, b])) for a, b in combinations(corr.columns, 2)]
        pairs = [p for p in pairs if not np.isnan(p[2])]
        if pairs:
            pairs.sort(key=lambda t: t[2], reverse=True)
            a, b, r = pairs[0]
            n_high = sum(1 for p in pairs if p[2] > HIGH_CORR_THRESHOLD)
            corr_txt = f"**In brief:** strongest linear relationship is `{a}`~`{b}` (|r|={r:.2f}); "
            out["Correlations"] = corr_txt + f"{n_high} pair(s) exceed the |r|>{HIGH_CORR_THRESHOLD} redundancy threshold."

    if numeric:
        out["Outliers"] = "**In brief:** outlier counts per numeric column by IQR and z-score. Strongly-skewed columns typically carry the most."

    dt_cols = _cols_of(types, "datetime")
    if dt_cols and numeric:
        span = pd.to_datetime(df[dt_cols[0]], errors="coerce").dropna()
        if len(span):
            lo, hi = span.min().date(), span.max().date()
            out["Time series"] = f"**In brief:** up to {min(6, len(numeric))} numeric columns over `{dt_cols[0]}`, from {lo} to {hi}."

    if focus and focus in df.columns:
        target_txt = f"**In brief:** features ranked by mutual information to `{focus}` "
        out[f"Target: {focus}"] = target_txt + "(captures non-linear links, not just correlation); MI near 1 flags possible leakage."

    if len(numeric) >= 2:
        out["Advanced"] = "**In brief:** dimensionality (PCA variance explained) and multicollinearity (VIF) diagnostics for the numeric columns."

    alerts = _collect_alerts(df, types)
    if alerts:
        n_high = sum(1 for sev, _ in alerts if sev == 3)
        out["Alerts"] = f"**In brief:** {len(alerts)} data-quality alert(s), {n_high} high-severity — ranked below."

    return out


def build_report(df: pd.DataFrame, focus: str = "", title: str = "") -> "object":
    """Build the full interactive EDA report as a servable Panel object.

    Runs :func:`classify_columns`, invokes each applicable section builder,
    drops sections that do not apply, and assembles the survivors into a tabbed
    layout. Degenerate inputs (empty, single-column, all-null) yield a friendly
    placeholder rather than an error.

    Parameters
    ----------
    df : pandas.DataFrame
        The dataset to analyse.
    focus : str, optional
        A target/focus column name. When given, a target-analysis section is added.
    title : str, optional
        A title shown at the top of the report.

    Returns
    -------
    object
        A Panel viewable on which ``.servable()`` can be called.
    """
    if df is None or df.shape[1] == 0 or df.empty:
        return pn.Column(
            pn.pane.Markdown("### No data to analyze\nThe provided source produced an empty table."),
            sizing_mode="stretch_width",
        )

    sampled, notes = _sample_and_cap(df)
    types = classify_columns(sampled)

    narratives = _narratives(sampled, types, focus)

    def _with_summary(name: str, comp: "object | None") -> "object | None":
        """Prepend the tab's 2-3 line findings summary above its content."""
        if comp is None:
            return None
        summary = narratives.get(name)
        if not summary:
            return comp
        return pn.Column(pn.pane.Markdown(summary, sizing_mode="stretch_width"), comp, sizing_mode="stretch_width")

    raw_sections = [
        ("Overview", overview_section(sampled, types, notes)),
        ("Summary", summary_table(sampled, types)),
        ("Distributions", distributions_section(sampled, types)),
        ("Missingness", missing_section(sampled)),
        ("Correlations", correlations_section(sampled, types)),
        ("Outliers", outliers_section(sampled, types)),
        ("Time series", timeseries_section(sampled, types)),
        (f"Target: {focus}" if focus else "Target", focus_section(sampled, types, focus)),
        ("Advanced", advanced_section(sampled, types)),
        ("Alerts", alerts_section(sampled, types)),
        ("Next steps", next_steps_section(sampled, types)),
    ]
    sections = [(name, _with_summary(name, comp)) for name, comp in raw_sections]
    tabs = pn.Tabs(*[(name, comp) for name, comp in sections if comp is not None], sizing_mode="stretch_width", dynamic=True)
    header = pn.pane.Markdown(f"# {title or 'Exploratory Data Analysis'}", sizing_mode="stretch_width")
    return pn.Column(header, tabs, sizing_mode="stretch_width")


def compute_summary(df: pd.DataFrame, focus: str = "") -> dict:
    """Compute a compact, structured findings summary for the calling model.

    This is returned by the ``auto_eda`` tool alongside the report URL so the
    assistant can narrate what the data looks like without a screenshot.

    Parameters
    ----------
    df : pandas.DataFrame
        The dataset to summarise.
    focus : str, optional
        A target/focus column name, echoed into the summary when provided.

    Returns
    -------
    dict
        Shape, dtype mix, top alerts, strongest relationships, and a missingness
        headline.
    """
    sampled, notes = _sample_and_cap(df)
    types = classify_columns(sampled)
    n_rows, n_cols = sampled.shape
    total_cells = n_rows * n_cols
    missing_total = int(sampled.isna().sum().sum())

    miss_by_col = (sampled.isna().mean() * 100).round(1)
    top_missing = {str(c): float(p) for c, p in miss_by_col[miss_by_col > 0].sort_values(ascending=False).head(5).items()}

    strongest = []
    numeric = _cols_of(types, "numeric")
    if len(numeric) >= 2:
        with _quiet():
            corr = sampled[numeric].corr()
        pairs = [(a, b, float(corr.loc[a, b])) for a, b in combinations(corr.columns, 2)]
        pairs.sort(key=lambda t: abs(t[2]) if not np.isnan(t[2]) else 0.0, reverse=True)
        strongest = [{"pair": f"{a} ~ {b}", "pearson": round(r, 2)} for a, b, r in pairs[:5] if not np.isnan(r)]

    summary = {
        "rows": int(n_rows),
        "columns": int(n_cols),
        "column_types": dict(Counter(types.values())),
        "missing_cells_pct": round(100 * missing_total / total_cells, 2) if total_cells else 0.0,
        "top_missing_columns": top_missing,
        "duplicate_rows": int(sampled.duplicated().sum()),
        "alerts": [t for _, t in _collect_alerts(sampled, types)][:8],
        "strongest_relationships": strongest,
        "notes": notes,
    }
    if focus:
        summary["focus"] = focus
    return summary
