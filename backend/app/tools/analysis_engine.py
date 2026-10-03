"""Application-owned analysis algorithms, copied into the networkless guest.

No expressions are evaluated. Decimal arithmetic preserves financial values;
statistical estimates explicitly convert to floating point. Numeric inputs are
limited to 60 significant digits; exact decimal results are limited to 120.
Repeating decimal divisions are rejected instead of rounded.
"""

# mypy: ignore-errors

import csv
import json
import math
import platform
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal, localcontext
from fractions import Fraction
from pathlib import Path

MAX_ROWS = 100_000
MAX_CELLS = 1_000_000
MAX_INPUT_DIGITS = 60
MAX_RESULT_DIGITS = 120
MAX_RESULT_ADJUSTED = 120
DECIMAL_CONTEXT_PRECISION = 512


class AnalysisError(ValueError):
    pass


def number(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise AnalysisError("nonfinite_numeric_value")
    max_digits = MAX_RESULT_DIGITS if isinstance(value, Decimal) else MAX_INPUT_DIGITS
    max_adjusted = MAX_RESULT_ADJUSTED if isinstance(value, Decimal) else 60
    if (
        len(result.as_tuple().digits) > max_digits
        or abs(result.adjusted()) > max_adjusted
    ):
        raise AnalysisError("numeric_precision_limit")
    return result


def _bounded_decimal(value):
    if (
        not value.is_finite()
        or len(value.as_tuple().digits) > MAX_RESULT_DIGITS
        or abs(value.adjusted()) > MAX_RESULT_ADJUSTED
    ):
        raise AnalysisError("numeric_precision_limit")
    return value


def _terminating_decimal(value):
    """Convert a rational to an exact Decimal or reject a repeating quotient."""
    numerator = value.numerator
    denominator = value.denominator
    twos = fives = 0
    while denominator % 2 == 0:
        denominator //= 2
        twos += 1
    while denominator % 5 == 0:
        denominator //= 5
        fives += 1
    if denominator != 1:
        raise AnalysisError("nonterminating_decimal_result")
    scale = max(twos, fives)
    coefficient = numerator * (2 ** (scale - twos)) * (5 ** (scale - fives))
    sign = 1 if coefficient < 0 else 0
    digits = tuple(int(char) for char in str(abs(coefficient)))
    return _bounded_decimal(Decimal((sign, digits, -scale)))


def _decimal_operation(action, left, right):
    exact_left = Fraction(left)
    exact_right = Fraction(right)
    try:
        exact = {
            "add": lambda: exact_left + exact_right,
            "subtract": lambda: exact_left - exact_right,
            "multiply": lambda: exact_left * exact_right,
            "divide": lambda: exact_left / exact_right,
        }[action]()
    except ZeroDivisionError as exc:
        raise AnalysisError("division_by_zero") from exc
    exact_decimal = _terminating_decimal(exact)
    # Use a local context for the calculation, then verify that its value equals
    # the exact rational. The context is wide enough for bounded intermediate
    # operands; a changed result is rejected rather than silently rounded.
    with localcontext() as context:
        context.prec = DECIMAL_CONTEXT_PRECISION
        calculated = {
            "add": lambda: left + right,
            "subtract": lambda: left - right,
            "multiply": lambda: left * right,
            "divide": lambda: left / right,
        }[action]()
    if Fraction(calculated) != exact:
        raise AnalysisError("decimal_precision_loss")
    return _bounded_decimal(calculated)


def _sum_decimals(values):
    total = Decimal(0)
    for value in values:
        total = _decimal_operation("add", total, number(value))
    return total


def _derived_unit(action, left, right):
    if not left and not right:
        return None
    left = left or "1"
    right = right or "1"
    if action == "multiply":
        if left == "1":
            return right
        if right == "1":
            return left
        return f"{left}*{right}"
    if left == right:
        return "1"
    if right == "1":
        return left
    return f"{left}/{right}"


def cell(value):
    if isinstance(value, Decimal):
        return str(value) if value.is_finite() else None
    if isinstance(value, int) and abs(value) > 2**53 - 1:
        return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def bounded(rows):
    if len(rows) > MAX_ROWS or sum(len(r) for r in rows) > MAX_CELLS:
        raise AnalysisError("analysis_expansion_limit")
    return rows


def require_columns(rows, columns, schema=None):
    available = set(schema if schema is not None else (rows[0] if rows else {}))
    if any(c not in available for c in columns):
        raise AnalysisError("unknown_column")


def apply_operation(frames, units, op, schemas=None):
    alias = op["frame"]
    rows = frames[alias]
    if schemas is None:
        schemas = {
            frame: list(frame_rows[0]) if frame_rows else []
            for frame, frame_rows in frames.items()
        }
    before = len(rows)
    kind = op["kind"]
    columns = op.get("columns") or []
    require_columns(rows, columns, schemas.get(alias, []))
    note = {"operation": kind, "frame": alias, "input_rows": before}
    if kind == "convert":
        target = op["data_type"]
        for row in rows:
            for col in columns:
                v = row[col]
                if v is None or v == "":
                    row[col] = None
                elif target == "number":
                    row[col] = number(v)
                elif target == "integer":
                    n = number(v)
                    if n != n.to_integral_value():
                        raise AnalysisError("fractional_integer")
                    row[col] = int(n)
                elif target == "date":
                    row[col] = datetime.strptime(str(v), op["date_format"])
                else:
                    row[col] = str(v)
    elif kind == "missing":
        counts = {c: sum(r[c] in (None, "") for r in rows) for c in columns}
        note["missing_counts"] = counts
        policy = op["policy"]
        if policy == "reject" and any(counts.values()):
            raise AnalysisError("missing_values_require_policy")
        if policy == "drop":
            rows = [r for r in rows if all(r[c] not in (None, "") for c in columns)]
        elif policy == "fill":
            for row in rows:
                for col in columns:
                    if row[col] in (None, ""):
                        row[col] = op["value"]
        note["policy"] = policy
    elif kind == "duplicates":
        counts = Counter(tuple(r[c] for c in columns) for r in rows)
        note["duplicate_rows"] = sum(n - 1 for n in counts.values())
        if note["duplicate_rows"] and op["policy"] == "reject":
            raise AnalysisError("duplicate_identifiers")
        if op["policy"] == "drop":
            seen = set()
            rows = [
                r
                for r in rows
                if not (
                    tuple(r[c] for c in columns) in seen
                    or seen.add(tuple(r[c] for c in columns))
                )
            ]
    elif kind == "filter":
        c = columns[0]
        v = op["value"]

        def compare(a):
            if a is None or a == "":
                return False
            b = number(v) if isinstance(a, (Decimal, int)) else v
            comparator = op["comparison"]
            return {
                "eq": lambda: a == b,
                "ne": lambda: a != b,
                "gt": lambda: a > b,
                "ge": lambda: a >= b,
                "lt": lambda: a < b,
                "le": lambda: a <= b,
            }[comparator]()

        rows = [r for r in rows if compare(r[c])]
    elif kind == "join":
        right_alias = op["right_frame"]
        right = frames[right_alias]
        keys = op["right_on"]
        require_columns(right, keys, schemas.get(right_alias, []))
        if any(any(r[c] in (None, "") for c in columns) for r in rows) or any(
            any(r[c] in (None, "") for c in keys) for r in right
        ):
            raise AnalysisError("join_null_keys_require_cleaning")
        left_counts = Counter(tuple(r[c] for c in columns) for r in rows)
        right_counts = Counter(tuple(r[c] for c in keys) for r in right)
        validate = op["relationship"]
        if validate in ("one_to_one", "one_to_many") and any(
            n > 1 for n in left_counts.values()
        ):
            raise AnalysisError("join_left_keys_not_unique")
        if validate in ("one_to_one", "many_to_one") and any(
            n > 1 for n in right_counts.values()
        ):
            raise AnalysisError("join_right_keys_not_unique")
        for a, b in zip(columns, keys):
            if units[alias].get(a) != units[right_alias].get(b):
                raise AnalysisError("join_key_unit_mismatch")
            left_types = {type(r[a]) for r in rows}
            right_types = {type(r[b]) for r in right}
            if left_types != right_types and rows and right:
                raise AnalysisError("join_key_types_require_mapping")
        groups = defaultdict(list)
        for r in right:
            groups[tuple(r[c] for c in keys)].append(r)
        right_columns = list(schemas.get(right_alias, []))
        mapping = {
            c: (c + "_right" if c in schemas.get(alias, []) else c)
            for c in right_columns
        }
        if len(set(mapping.values())) != len(mapping) or (
            any(c in schemas.get(alias, []) for c in mapping.values())
        ):
            raise AnalysisError("join_column_collision")
        estimate = sum(
            max(len(groups[tuple(r[c] for c in columns)]), int(op["how"] == "left"))
            for r in rows
        )
        if (
            estimate > MAX_ROWS
            or estimate * (len(schemas.get(alias, [])) + len(right_columns)) > MAX_CELLS
        ):
            raise AnalysisError("join_expansion_limit")
        output = []
        matched = 0
        for r in rows:
            matches = groups[tuple(r[c] for c in columns)]
            matched += bool(matches)
            for s in matches or (
                [dict.fromkeys(right_columns)] if op["how"] == "left" else []
            ):
                output.append({**r, **{mapping[c]: s[c] for c in right_columns}})
        note.update(
            right_frame=right_alias,
            relationship=validate,
            unmatched_left=before - matched,
            unmatched_right=sum(
                n for key, n in right_counts.items() if key not in left_counts
            ),
            left_unique_keys=len(left_counts),
            right_unique_keys=len(right_counts),
            right_rows=len(right),
        )
        if note["unmatched_left"] or note["unmatched_right"]:
            note["warning"] = (
                "Join has unmatched keys; inspect entity mapping and row loss."
            )
        if validate == "many_to_many":
            note["warning"] = (
                "Explicit many-to-many join can multiply observations; confirm entity grain."
            )
        units[alias].update(
            {mapping[c]: u for c, u in units[right_alias].items() if c in mapping}
        )
        schemas[alias] = [*schemas.get(alias, []), *mapping.values()]
        rows = output
    elif kind == "aggregate":
        metric_columns = [metric["column"] for metric in op["metrics"]]
        require_columns(rows, metric_columns, schemas.get(alias, []))
        groups = defaultdict(list)
        for r in rows:
            groups[tuple(r[c] for c in columns)].append(r)
        if not columns and not rows:
            groups[()] = []
        output_columns = [*columns, *(metric["output"] for metric in op["metrics"])]
        if len(groups) > MAX_ROWS or len(groups) * len(output_columns) > MAX_CELLS:
            raise AnalysisError("aggregate_expansion_limit")
        output = []
        for key, group in groups.items():
            r = dict(zip(columns, key))
            for metric in op["metrics"]:
                vals = [x[metric["column"]] for x in group]
                fn = metric["function"]
                if fn == "count":
                    value = len(vals)
                elif not vals:
                    value = Decimal(0) if fn == "sum" else None
                else:
                    if any(v in (None, "") for v in vals):
                        raise AnalysisError("aggregate_missing_values_require_policy")
                    nums = [number(v) for v in vals]
                    value = {
                        "sum": lambda: _sum_decimals(nums),
                        "mean": lambda: _decimal_operation(
                            "divide", _sum_decimals(nums), Decimal(len(nums))
                        ),
                        "min": lambda: min(nums),
                        "max": lambda: max(nums),
                    }[fn]()
                r[metric["output"]] = value
            output.append(r)
        old_units = units[alias]
        units[alias] = {
            **{column: old_units[column] for column in columns if column in old_units},
            **{
                metric["output"]: old_units[metric["column"]]
                for metric in op["metrics"]
                if metric["function"] != "count" and metric["column"] in old_units
            },
        }
        schemas[alias] = output_columns
        rows = output
    elif kind == "derive":
        out = op["output"]
        if out in schemas.get(alias, []):
            raise AnalysisError("derived_column_already_exists")
        action = op["function"]
        operand_units = [units[alias].get(c) for c in columns]
        known_operand_units = {unit for unit in operand_units if unit}
        if action in ("add", "subtract") and len(known_operand_units) > 1:
            raise AnalysisError("derived_unit_mismatch")
        inferred_unit = None
        if action in ("add", "subtract"):
            inferred_unit = next(iter(known_operand_units), None)
        elif action in ("multiply", "divide"):
            inferred_unit = _derived_unit(
                action,
                units[alias].get(columns[0]),
                units[alias].get(columns[1]),
            )
        if op.get("unit") and inferred_unit and op["unit"] != inferred_unit:
            raise AnalysisError("derived_unit_mismatch")
        for row in rows:
            if any(row[c] in (None, "") for c in columns):
                raise AnalysisError("derive_missing_values_require_policy")
            if action in ("year", "month", "fiscal_year"):
                v = row[columns[0]]
                if not isinstance(v, datetime):
                    raise AnalysisError("date_conversion_required")
                value = v.month if action == "month" else v.year
                if action == "fiscal_year":
                    value = (
                        v.year if v.month >= op["fiscal_start_month"] else v.year - 1
                    )
                    note["fiscal_label"] = "start year"
            else:
                a, b = (number(row[c]) for c in columns)
                value = _decimal_operation(action, a, b)
            row[out] = value
        schemas[alias] = [*schemas.get(alias, []), out]
        result_unit = op.get("unit") or inferred_unit
        if result_unit:
            units[alias][out] = result_unit
    elif kind == "melt":
        value_columns = op["value_columns"]
        require_columns(rows, value_columns, schemas.get(alias, []))
        if before * len(value_columns) > MAX_ROWS:
            raise AnalysisError("reshape_expansion_limit")
        output_columns = [*columns, "variable", "value"]
        if before * len(value_columns) * len(output_columns) > MAX_CELLS:
            raise AnalysisError("reshape_expansion_limit")
        if len({units[alias].get(c) for c in value_columns}) > 1:
            raise AnalysisError("reshape_unit_mismatch")
        rows = [
            {**{c: r[c] for c in columns}, "variable": v, "value": r[v]}
            for r in rows
            for v in value_columns
        ]
        old_units = units[alias]
        units[alias] = {
            **{column: old_units[column] for column in columns if column in old_units}
        }
        if value_columns and old_units.get(value_columns[0]):
            units[alias]["value"] = old_units[value_columns[0]]
        schemas[alias] = output_columns
    elif kind == "pivot":
        names, values = op["value_columns"]
        require_columns(rows, [names, values], schemas.get(alias, []))
        groups = defaultdict(dict)
        for r in rows:
            key = tuple(r[c] for c in columns)
            label = str(r[names])
            if label in groups[key] or label in columns:
                raise AnalysisError("pivot_duplicate_or_collision")
            groups[key][label] = r[values]
        labels = sorted({label for g in groups.values() for label in g})
        if len(groups) * (len(labels) + len(columns)) > MAX_CELLS:
            raise AnalysisError("reshape_expansion_limit")
        if len(groups) > MAX_ROWS:
            raise AnalysisError("reshape_expansion_limit")
        rows = [
            {**dict(zip(columns, key)), **{label: g.get(label) for label in labels}}
            for key, g in groups.items()
        ]
        old_units = units[alias]
        units[alias] = {
            **{column: old_units[column] for column in columns if column in old_units},
            **(
                {label: old_units[values] for label in labels}
                if old_units.get(values)
                else {}
            ),
        }
        schemas[alias] = [*columns, *labels]
    elif kind == "statistics":
        if any(r[c] in (None, "") for r in rows for c in columns):
            raise AnalysisError("statistics_missing_values_require_policy")
        if not rows:
            raise AnalysisError("statistics_empty_sample")
        note.update(
            sample_size=len(rows),
            approximation="IEEE-754 floating point for statistical estimates",
            causal_claim=False,
        )
        import numpy as np

        data = np.array(
            [[float(number(r[c])) for c in columns] for r in rows], dtype=float
        )
        if not np.isfinite(data).all():
            raise AnalysisError("statistics_nonfinite_conversion")
        function = op["function"]
        if function == "describe":
            note["statistics"] = {
                c: {
                    "count": len(rows),
                    "mean": cell(float(data[:, i].mean())),
                    "min": cell(float(data[:, i].min())),
                    "max": cell(float(data[:, i].max())),
                    "std_sample": (
                        cell(float(data[:, i].std(ddof=1))) if len(rows) > 1 else None
                    ),
                    "quartiles": [
                        cell(float(v))
                        for v in np.quantile(data[:, i], [0.25, 0.5, 0.75])
                    ],
                }
                for i, c in enumerate(columns)
            }
        elif function == "correlation":
            if len(rows) < 3:
                raise AnalysisError("statistics_insufficient_sample")
            note["correlation"] = [
                [cell(float(v)) for v in row]
                for row in np.atleast_2d(np.corrcoef(data.T))
            ]
        elif function == "regression":
            if len(rows) <= len(columns) or len(columns) < 2:
                raise AnalysisError("regression_insufficient_sample")
            matrix = np.column_stack([np.ones(len(rows)), data[:, 1:]])
            coefficients, _, rank, _ = np.linalg.lstsq(matrix, data[:, 0], rcond=None)
            if rank < matrix.shape[1]:
                raise AnalysisError("regression_rank_deficient")
            residual = data[:, 0] - matrix @ coefficients
            total = np.sum((data[:, 0] - data[:, 0].mean()) ** 2)
            note["regression"] = {
                "target": columns[0],
                "predictors": columns[1:],
                "intercept": cell(float(coefficients[0])),
                "coefficients": [cell(float(v)) for v in coefficients[1:]],
                "r_squared": (
                    cell(float(1 - np.sum(residual**2) / total)) if total else None
                ),
                "assumption": "OLS with intercept; association only; no causal identification",
            }
        elif function == "outliers":
            q1, q3 = np.quantile(data[:, 0], [0.25, 0.75])
            low, high = q1 - 1.5 * (q3 - q1), q3 + 1.5 * (q3 - q1)
            note["outliers"] = {
                "method": "Tukey 1.5 IQR",
                "column": columns[0],
                "count": int(((data[:, 0] < low) | (data[:, 0] > high)).sum()),
                "lower": cell(float(low)),
                "upper": cell(float(high)),
            }
        else:
            raise AnalysisError("unknown_statistic")
    else:
        raise AnalysisError("unknown_operation")
    frames[alias] = bounded(rows)
    note["output_rows"] = len(rows)
    note["rows_removed"] = max(0, before - len(rows))
    return note


def run_analysis(config, input_root="/workspace/inputs", output_root="."):
    root = Path(output_root)
    frames, units, input_notes, schemas = {}, {}, [], {}
    metadata = {
        "schema_version": 1,
        "status": "failed",
        "assumptions": config["assumptions"],
        "limitations": [
            "Statistics describe retained observations, not causal effects."
        ],
        "operations": [],
        "python_version": platform.python_version(),
        "sampling": "none within staged inputs; DB snapshots can be bounded",
    }
    try:
        for item in config["inputs"]:
            path = Path(input_root) / item["path"]
            with path.open(newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                if not reader.fieldnames or len(set(reader.fieldnames)) != len(
                    reader.fieldnames
                ):
                    raise AnalysisError("input_headers_invalid")
                rows = []
                for row in reader:
                    if None in row or any(v is None for v in row.values()):
                        raise AnalysisError("input_row_width_invalid")
                    rows.append(row)
                    if (
                        len(rows) > MAX_ROWS
                        or len(rows) * len(reader.fieldnames) > MAX_CELLS
                    ):
                        raise AnalysisError("input_expansion_limit")
            schemas[item["alias"]] = list(reader.fieldnames)
            frames[item["alias"]] = rows
            units[item["alias"]] = dict(item.get("units", {}))
            input_notes.append(
                {"alias": item["alias"], "rows": len(rows), "reference": item["path"]}
            )
        for op in config["operations"]:
            metadata["operations"].append(apply_operation(frames, units, op, schemas))
        rows = frames[config["result_frame"]]
        headers = schemas[config["result_frame"]]
        selected = rows[: config["max_rows"]]
        with (root / "result.csv").open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            writer.writerows({k: cell(v) for k, v in r.items()} for r in selected)
        metadata.update(
            status="ok",
            inputs=input_notes,
            units={
                key: value
                for key, value in units[config["result_frame"]].items()
                if key in headers
            },
            row_count=len(rows),
            retained_rows=len(selected),
            truncated=len(selected) < len(rows),
            columns=headers,
            rows=[{k: cell(v) for k, v in r.items()} for r in selected[:10]],
        )
        chart = config.get("chart")
        if chart:
            require_columns(
                rows, [chart["x"], chart["y"]], schemas[config["result_frame"]]
            )
            points = selected[:1000]
            values = [float(number(r[chart["y"]])) for r in points]
            if not all(math.isfinite(v) for v in values):
                raise AnalysisError("chart_nonfinite_values")
            chart_unit = units[config["result_frame"]].get(chart["y"])
            if chart_unit and chart["unit"] != chart_unit:
                raise AnalysisError("chart_unit_mismatch")
            spec = {
                "schema_version": 1,
                "data": [
                    {
                        "type": chart["kind"],
                        "x": [str(cell(r[chart["x"]])) for r in points],
                        "y": values,
                    }
                ],
                "layout": {
                    "title": chart["title"],
                    "xaxis": {"title": chart["x"]},
                    "yaxis": {
                        "title": chart["y"]
                        + (" (" + chart["unit"] + ")" if chart["unit"] else "")
                    },
                },
                "config": {"responsive": True, "displayModeBar": False},
            }
            (root / "chart.json").write_text(
                json.dumps(spec, allow_nan=False), encoding="utf-8"
            )
            import matplotlib

            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            fig, ax = plt.subplots(figsize=(10, 5))
            if chart["kind"] == "bar":
                ax.bar(spec["data"][0]["x"], values)
            else:
                ax.plot(spec["data"][0]["x"], values, marker="o")
            ax.set(
                title=chart["title"],
                xlabel=chart["x"],
                ylabel=spec["layout"]["yaxis"]["title"],
            )
            if len(points) > 15:
                ax.set_xticks([])
            fig.tight_layout()
            fig.savefig(root / "chart.png", dpi=120)
            plt.close(fig)
            metadata["chart_points"] = len(points)
            metadata["chart_truncated"] = len(points) < len(rows)
            metadata["limitations"].append(
                "Chart coordinates use float approximations; CSV retains exact decimal strings."
            )
    except Exception as error:
        metadata.update(
            status="failed", error=str(error)[:200], clarification_required=True
        )
    (root / "analysis.json").write_text(
        json.dumps(metadata, default=cell, allow_nan=False), encoding="utf-8"
    )
    print(json.dumps({"status": metadata["status"], "error": metadata.get("error")}))
    return metadata
