"""Daily metric series straight from generated frames, without running dbt.

A world set needs every metric of every world (measured effects are the
difference between a single world and its null twin). Running ``dbt build``
plus one ``mf query`` per metric costs about 5 s + 2 s x metrics per world on a
90-day smoke config, which is too slow for a set. This module evaluates the
vertical's *own* dbt project in-process instead:

1. the raw frames are loaded into an in-memory DuckDB through the same
   :class:`~fake_companies.output.DuckDBWriter` (same DDL, same types);
2. the project's staging and mart models are run as written — the only Jinja
   they use is ``source()``, ``ref()``, ``config()`` and ``var(name, default)``,
   which are rendered here; anything else is refused by name;
3. the metrics in ``models/semantic/metrics.yml`` are evaluated from
   ``semantic_models.yml`` with MetricFlow's day-grain semantics.

So the aggregation is the dbt project's, not a second copy of it.
``tests/test_semantic_matches_metricflow.py`` checks every metric this module
computes against ``mf query`` on each vertical's smoke config. What it cannot
reproduce exactly it does not compute: :meth:`SemanticLayer.unsupported` lists
those metrics with the reason, and they are left out of every effects table.

Supported: ``simple`` metrics over ``sum`` / ``average`` / ``count`` /
``count_distinct`` / ``min`` / ``max`` / ``sum_boolean`` measures, ``ratio``
and ``derived`` metrics over supported metrics (no filters, no offsets), and
``cumulative`` metrics with a day window. Dimensions: categorical dimensions
declared on the measure's own semantic model (no joins).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    import duckdb
    import pandas as pd

    from .core.calendar import Calendar
    from .output.schemas import TableSpec

# src/fake_companies/semantic.py -> repo root. The dbt projects are not shipped
# in the wheel; metric series need a checkout (or an explicit ``dbt_root``).
REPO_DBT_ROOT = Path(__file__).resolve().parents[2] / "dbt"

_AGGS = {
    "sum": "sum({e})",
    "average": "avg({e})",
    "count": "count({e})",
    "count_distinct": "count(distinct {e})",
    "min": "min({e})",
    "max": "max({e})",
    "sum_boolean": "sum(case when {e} then 1 else 0 end)",
}
NULL_VALUE = "(null)"  # how a NULL dimension value is spelled in a series


class UnsupportedSemantics(ValueError):
    """The dbt project uses something this module does not reproduce."""


# --------------------------------------------------------------------------- #
# dbt model rendering
# --------------------------------------------------------------------------- #
_JINJA = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)
_SOURCE = re.compile(r"""^source\(\s*['"](\w+)['"]\s*,\s*['"](\w+)['"]\s*\)$""")
_REF = re.compile(r"""^ref\(\s*['"](\w+)['"]\s*\)$""")
_VAR = re.compile(r"""^var\(\s*['"](\w+)['"]\s*,\s*(.+?)\s*\)$""", re.DOTALL)
_CONFIG = re.compile(r"^config\(.*\)$", re.DOTALL)


def render_model(sql: str, name: str) -> tuple[str, list[str]]:
    """Render one dbt model's Jinja; return ``(sql, referenced model names)``."""
    refs: list[str] = []

    def sub(m: re.Match) -> str:
        expr = m.group(1).strip()
        if s := _SOURCE.match(expr):
            return f"{s.group(1)}.{s.group(2)}"
        if r := _REF.match(expr):
            refs.append(r.group(1))
            return f"dbt.{r.group(1)}"
        if v := _VAR.match(expr):
            default = v.group(2).strip()
            if default[:1] in "'\"":
                return default[1:-1]
            return default
        if _CONFIG.match(expr):
            return ""
        raise UnsupportedSemantics(f"dbt model {name!r} uses unsupported Jinja: {{{{ {expr} }}}}")

    if "{%" in sql:
        raise UnsupportedSemantics(f"dbt model {name!r} uses a Jinja block ({{% ... %}})")
    return _JINJA.sub(sub, sql), refs


def _load_models(project_dir: Path) -> dict[str, tuple[str, list[str]]]:
    models: dict[str, tuple[str, list[str]]] = {}
    for path in sorted((project_dir / "models").rglob("*.sql")):
        models[path.stem] = render_model(path.read_text(), path.stem)
    return models


# --------------------------------------------------------------------------- #
# Semantic manifest
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Measure:
    name: str
    model: str  # semantic model name
    relation: str  # dbt model the semantic model reads
    agg: str
    expr: str
    time_expr: str
    entity: str | None  # primary entity (MetricFlow dimension prefix)
    dimensions: dict[str, str]  # categorical dimension name -> sql expr


@dataclass(frozen=True)
class Metric:
    name: str
    kind: str  # simple | ratio | derived | cumulative
    measure: Measure | None = None
    fill: float | None = None  # fill_nulls_with (None: a missing day is NULL)
    inputs: dict[str, str] = field(default_factory=dict)  # alias -> metric name
    expr: str | None = None  # derived: sql over the aliases
    window_days: int | None = None  # cumulative


class SemanticLayer:
    """A vertical's metrics, read from its dbt project's semantic YAML."""

    def __init__(self, project_dir: str | Path):
        self.project_dir = Path(project_dir)
        sem_dir = self.project_dir / "models" / "semantic"
        if not sem_dir.is_dir():
            raise FileNotFoundError(
                f"no dbt semantic layer at {sem_dir}; metric series need the repo's "
                "dbt projects (run from a checkout or pass dbt_root)"
            )
        sm = yaml.safe_load((sem_dir / "semantic_models.yml").read_text())["semantic_models"]
        raw_metrics = yaml.safe_load((sem_dir / "metrics.yml").read_text())["metrics"]

        self.measures: dict[str, Measure] = {}
        self._bad_measures: dict[str, str] = {}
        for model in sm:
            self._read_model(model)

        self.metrics: dict[str, Metric] = {}
        self._unsupported: dict[str, str] = {}
        self._raw = {m["name"]: m for m in raw_metrics}
        for name in self._raw:
            self._resolve(name)
        self.models = _load_models(self.project_dir)

    @classmethod
    def for_vertical(cls, vertical, dbt_root: str | Path | None = None) -> SemanticLayer:
        return cls(Path(dbt_root or REPO_DBT_ROOT) / vertical.dbt_project())

    # -- parsing ----------------------------------------------------------- #
    def _read_model(self, model: dict) -> None:
        ref = _REF.match(str(model["model"]).strip())
        if ref is None:
            raise UnsupportedSemantics(f"semantic model {model['name']!r}: model is not a ref()")
        dims = model.get("dimensions", [])
        time_dims = {d["name"]: d for d in dims if d["type"] == "time"}
        cat_dims = {d["name"]: d.get("expr") or d["name"] for d in dims if d["type"] != "time"}
        primary = next(
            (e["name"] for e in model.get("entities", []) if e["type"] == "primary"), None
        )
        default_time = (model.get("defaults") or {}).get("agg_time_dimension")
        for meas in model.get("measures", []):
            name = meas["name"]
            reason = None
            time_name = meas.get("agg_time_dimension") or default_time
            if meas["agg"] not in _AGGS:
                reason = f"measure agg {meas['agg']!r}"
            elif meas.get("non_additive_dimension"):
                reason = "non_additive_dimension"
            elif time_name not in time_dims:
                reason = f"agg_time_dimension {time_name!r} is not a time dimension of the model"
            elif (time_dims[time_name].get("type_params") or {}).get("time_granularity") != "day":
                reason = "time dimension is not day-grain"
            if reason:
                self._bad_measures[name] = reason
                continue
            self.measures[name] = Measure(
                name=name,
                model=model["name"],
                relation=ref.group(1),
                agg=meas["agg"],
                expr=str(meas.get("expr", name)),
                time_expr=str(time_dims[time_name].get("expr") or time_name),
                entity=primary,
                dimensions=cat_dims,
            )

    def _resolve(self, name: str) -> Metric | None:
        if name in self.metrics:
            return self.metrics[name]
        if name in self._unsupported:
            return None
        raw = self._raw.get(name)
        try:
            if raw is None:
                raise UnsupportedSemantics(f"input metric {name!r} is not defined")
            metric = self._build(raw)
        except UnsupportedSemantics as e:
            self._unsupported[name] = str(e)
            return None
        self.metrics[name] = metric
        return metric

    def _measure(self, spec: dict | str) -> tuple[Measure, float | None]:
        spec = {"name": spec} if isinstance(spec, str) else spec
        extra = set(spec) - {"name", "fill_nulls_with"}
        if extra:
            raise UnsupportedSemantics(f"measure options {sorted(extra)}")
        name = spec["name"]
        if name in self._bad_measures:
            raise UnsupportedSemantics(self._bad_measures[name])
        if name not in self.measures:
            raise UnsupportedSemantics(f"measure {name!r} is not defined")
        fill = spec.get("fill_nulls_with")
        return self.measures[name], None if fill is None else float(fill)

    def _input(self, spec: dict | str) -> tuple[str, str]:
        spec = {"name": spec} if isinstance(spec, str) else spec
        extra = set(spec) - {"name", "alias"}
        if extra:
            raise UnsupportedSemantics(f"metric input options {sorted(extra)}")
        if self._resolve(spec["name"]) is None:
            raise UnsupportedSemantics(
                f"input metric {spec['name']!r}: {self._unsupported[spec['name']]}"
            )
        return spec.get("alias") or spec["name"], spec["name"]

    def _build(self, raw: dict) -> Metric:
        name, kind, tp = raw["name"], raw["type"], raw.get("type_params") or {}
        extra = set(raw) - {"name", "description", "label", "type", "type_params", "meta", "config"}
        if extra:
            raise UnsupportedSemantics(f"metric options {sorted(extra)}")
        if kind == "simple":
            if set(tp) - {"measure"}:
                raise UnsupportedSemantics(f"type_params {sorted(set(tp) - {'measure'})}")
            measure, fill = self._measure(tp["measure"])
            return Metric(name, kind, measure=measure, fill=fill)
        if kind == "ratio":
            if set(tp) - {"numerator", "denominator"}:
                raise UnsupportedSemantics("ratio type_params beyond numerator/denominator")
            _, num = self._input(tp["numerator"])
            _, den = self._input(tp["denominator"])
            return Metric(name, kind, inputs={"num": num, "den": den})
        if kind == "derived":
            if set(tp) - {"expr", "metrics"}:
                raise UnsupportedSemantics("derived type_params beyond expr/metrics")
            inputs = dict(self._input(m) for m in tp["metrics"])
            return Metric(name, kind, inputs=inputs, expr=str(tp["expr"]))
        if kind == "cumulative":
            ctp = tp.get("cumulative_type_params") or {}
            if set(tp) - {"measure", "cumulative_type_params"} or set(ctp) - {"window"}:
                raise UnsupportedSemantics("cumulative options beyond a window")
            m = re.fullmatch(r"(\d+) days?", str(ctp.get("window", "")).strip())
            if m is None:
                raise UnsupportedSemantics(f"cumulative window {ctp.get('window')!r} is not N days")
            measure, fill = self._measure(tp["measure"])
            return Metric(name, kind, measure=measure, fill=fill, window_days=int(m.group(1)))
        raise UnsupportedSemantics(f"metric type {kind!r}")

    # -- catalog ----------------------------------------------------------- #
    def unsupported(self) -> dict[str, str]:
        """Metrics the dbt project defines that this module does not compute."""
        return dict(self._unsupported)

    def _leaf_measures(self, name: str) -> list[Measure]:
        metric = self.metrics[name]
        if metric.measure is not None:
            return [metric.measure]
        return [m for inp in metric.inputs.values() for m in self._leaf_measures(inp)]

    def dimensions(self, name: str) -> dict[str, str | None]:
        """``{dimension: MetricFlow group-by id}`` a metric can be sliced by here.

        A dimension qualifies when every measure under the metric declares it
        on its own semantic model. The id is ``<primary entity>__<dimension>``
        when all of those models share a primary entity; otherwise ``None`` —
        this module can slice it (``return_rate`` by ``category``) but
        MetricFlow cannot address it with one group-by.
        """
        leaves = self._leaf_measures(name)
        common = set(leaves[0].dimensions)
        for leaf in leaves[1:]:
            common &= set(leaf.dimensions)
        entities = {leaf.entity for leaf in leaves}
        entity = next(iter(entities)) if len(entities) == 1 else None
        return {d: (f"{entity}__{d}" if entity else None) for d in sorted(common)}

    def fill_value(self, name: str) -> float | None:
        """What a day with no rows is worth: the metric's ``fill_nulls_with``.

        A derived or ratio metric has no fill of its own: its inputs are filled
        and the expression decides (a ratio over zero sessions is NULL).
        """
        return self.metrics[name].fill

    # -- evaluation -------------------------------------------------------- #
    def build_marts(self, frames: dict[str, pd.DataFrame], tables: list[TableSpec]):
        """Load ``frames`` and run the dbt models; return the DuckDB connection."""
        from .output.duckdb_writer import DuckDBWriter

        writer = DuckDBWriter(":memory:", tables)
        for fqn, df in frames.items():
            writer.write(fqn, df)
        con = writer.con
        con.execute("SET enable_progress_bar = false")  # no terminal noise per world
        con.execute("CREATE SCHEMA dbt")
        needed: list[str] = []

        def visit(model: str) -> None:
            if model in needed:
                return
            if model not in self.models:
                raise UnsupportedSemantics(f"dbt model {model!r} not found in {self.project_dir}")
            for dep in self.models[model][1]:
                visit(dep)
            needed.append(model)

        for relation in sorted({m.relation for m in self.measures.values()}):
            visit(relation)
        for model in needed:
            con.execute(f"CREATE TABLE dbt.{model} AS {self.models[model][0]}")
        return con

    def series(
        self,
        frames: dict[str, pd.DataFrame],
        tables: list[TableSpec],
        cal: Calendar,
        dimension_sets: list[tuple[str, ...]] | None = None,
        metrics: list[str] | None = None,
    ) -> pd.DataFrame:
        """Daily series for every supported metric, long format.

        Columns: ``date``, ``metric``, ``dimension``, ``value``, ``v``.
        ``dimension``/``value`` are NULL for the total; for a slice they are
        the dimension name and value (comma-joined for a multi-dimension set,
        e.g. ``channel,device`` / ``paid_search,mobile``). Days are the
        scenario timeline, every one present: a day with no rows carries the
        metric's ``fill_nulls_with``, or NULL when it has none (MetricFlow
        omits such a day instead).
        """
        con = self.build_marts(frames, tables)
        try:
            return self.series_from(con, cal, dimension_sets, metrics)
        finally:
            con.close()

    def series_from(
        self,
        con: duckdb.DuckDBPyConnection,
        cal: Calendar,
        dimension_sets: list[tuple[str, ...]] | None = None,
        metrics: list[str] | None = None,
    ) -> pd.DataFrame:
        import pandas as pd

        names = sorted(self.metrics) if metrics is None else list(metrics)
        con.execute(
            "CREATE OR REPLACE TEMP TABLE _spine AS SELECT CAST(unnest(generate_series("
            f"DATE '{cal.start.isoformat()}', DATE '{cal.end.isoformat()}', INTERVAL 1 DAY)) "
            "AS DATE) AS d"
        )
        out: list[pd.DataFrame] = []
        for dims in [(), *(dimension_sets or [])]:
            for name in names:
                if any(d not in self.dimensions(name) for d in dims):
                    continue
                sql = self._metric_sql(name, dims)
                label = ",".join(dims) if dims else None
                value = " || ',' || ".join(f"g{i}" for i in range(len(dims))) if dims else "NULL"
                df = con.execute(
                    f"SELECT d AS date, CAST({value} AS VARCHAR) AS value, CAST(v AS DOUBLE) AS v "
                    f"FROM ({sql}) WHERE d BETWEEN DATE '{cal.start.isoformat()}' "
                    f"AND DATE '{cal.end.isoformat()}' ORDER BY 1, 2"
                ).df()
                df.insert(1, "metric", name)
                df.insert(2, "dimension", label)
                out.append(df)
        if not out:
            return pd.DataFrame(columns=["date", "metric", "dimension", "value", "v"])
        res = pd.concat(out, ignore_index=True)
        res["date"] = pd.to_datetime(res["date"])
        return res

    def _metric_sql(self, name: str, dims: tuple[str, ...]) -> str:
        """SQL yielding ``(d, g0..gN, v, p)`` for one metric: one row per
        timeline day (and per dimension value seen). ``p`` says whether the
        day had any rows; where it had none, ``v`` is the fill or NULL."""
        metric = self.metrics[name]
        keys = ["d", *(f"g{i}" for i in range(len(dims)))]
        using = ", ".join(keys)
        gcols = "".join(f", g{i}" for i in range(len(dims)))
        if metric.measure is not None:
            m = metric.measure
            gsel = "".join(
                f", coalesce(CAST({m.dimensions[d]} AS VARCHAR), '{NULL_VALUE}') AS g{i}"
                for i, d in enumerate(dims)
            )
            agg = _AGGS[m.agg].format(e=m.expr)
            if metric.kind == "simple":
                sparse = (
                    f"SELECT CAST({m.time_expr} AS DATE) AS d{gsel}, {agg} AS v "
                    f"FROM dbt.{m.relation} GROUP BY ALL"
                )
            else:  # cumulative: each day aggregates the trailing window of rows
                sparse = (
                    f"SELECT s.d AS d{gcols}, {agg} AS v FROM _spine s JOIN "
                    f"(SELECT CAST({m.time_expr} AS DATE) AS _t{gsel}, * FROM dbt.{m.relation}) r "
                    f"ON r._t > s.d - INTERVAL {metric.window_days} DAY AND r._t <= s.d "
                    "GROUP BY ALL"
                )
            grid = "_spine"
            if dims:
                grid = f"_spine CROSS JOIN (SELECT DISTINCT {gcols[2:]} FROM x)"
            v = "x.v" if metric.fill is None else f"coalesce(x.v, {metric.fill!r})"
            return (
                f"WITH x AS (SELECT *, TRUE AS p FROM ({sparse})) "
                f"SELECT {using}, {v} AS v, coalesce(x.p, FALSE) AS p "
                f"FROM {grid} LEFT JOIN x USING ({using})"
            )
        # ratio / derived: full outer join of the (dense) inputs on (day, dims),
        # each input filled with its own fill_nulls_with, then the expression.
        # As in MetricFlow, a day on which no input has a row has no value —
        # not the expression over the fills (net_new_mrr is NULL, not 0, on a
        # day with no MRR movement).
        parts, cols, present = [], [], []
        for alias, inp in metric.inputs.items():
            fill = self.metrics[inp].fill
            parts.append(
                f"(SELECT d{gcols}, v AS {alias}, p AS p_{alias} "
                f"FROM ({self._metric_sql(inp, dims)}))"
            )
            cols.append(alias if fill is None else f"coalesce({alias}, {fill!r}) AS {alias}")
            present.append(f"coalesce(p_{alias}, FALSE)")
        joined = parts[0]
        for part in parts[1:]:
            joined += f" FULL OUTER JOIN {part} USING ({using})"
        if metric.kind == "ratio":
            expr = "CAST(num AS DOUBLE) / CAST(nullif(den, 0) AS DOUBLE)"
        else:
            expr = metric.expr
        inner = f"SELECT {using}, {', '.join(cols)}, {' OR '.join(present)} AS p FROM {joined}"
        return f"SELECT {using}, CASE WHEN p THEN {expr} END AS v, p FROM ({inner})"
