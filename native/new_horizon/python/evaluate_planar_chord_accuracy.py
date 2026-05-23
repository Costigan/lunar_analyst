import argparse
import csv
import math
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

try:
    import numpy as np
except ImportError:
    np = None


@dataclass
class TraceRow:
    row_index: int
    raw: Dict[str, str]
    pixel_x: float
    pixel_y: float
    distance_m: float
    segment_key: Optional[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate how well planar pixel distances approximate true chord "
            "lengths recorded in a trace CSV."
        )
    )
    parser.add_argument(
        "--input",
        required=True,
        type=Path,
        help="CSV file that contains the trace (e.g., quadtree_trace.csv).",
    )
    parser.add_argument(
        "--pixel-x-col",
        default="pixel_x",
        help="Column name for the horizontal pixel coordinate.",
    )
    parser.add_argument(
        "--pixel-y-col",
        default="pixel_y",
        help="Column name for the vertical pixel coordinate.",
    )
    parser.add_argument(
        "--distance-col",
        default="dist_m",
        help="Column that stores the true chord length in meters.",
    )
    parser.add_argument(
        "--segment-col",
        default=None,
        help="Optional column that identifies segment IDs. "
        "If omitted, segments are inferred when the distance decreases.",
    )
    parser.add_argument(
        "--step-col",
        default="step_index",
        help="Optional column used only for reporting. Ignored if missing.",
    )
    parser.add_argument(
        "--reset-threshold",
        type=float,
        default=0.5,
        help=(
            "When --segment-col is not provided, start a new segment whenever "
            "the distance decreases by at least this many meters."
        ),
    )
    parser.add_argument(
        "--scale-x",
        type=float,
        default=1.0,
        help="Meters per pixel along the X axis (ignored if --dem is supplied).",
    )
    parser.add_argument(
        "--scale-y",
        type=float,
        default=1.0,
        help="Meters per pixel along the Y axis (ignored if --dem is supplied).",
    )
    parser.add_argument(
        "--dem",
        type=Path,
        default=None,
        help=(
            "Optional DEM path. If provided, the script uses its GeoTransform "
            "to convert pixel deltas into map-space meters."
        ),
    )
    parser.add_argument(
        "--max-rows",
        type=int,
        default=None,
        help="Optional cap on the number of data rows to process.",
    )
    parser.add_argument(
        "--write-details",
        type=Path,
        default=None,
        help="Optional CSV output with per-sample errors.",
    )
    parser.add_argument(
        "--poly-degree",
        type=int,
        default=None,
        help="Fit a polynomial of this degree that maps planar distance to true chord distance.",
    )
    parser.add_argument(
        "--poly-sample-count",
        type=int,
        default=5,
        help="Number of samples per segment used to fit the polynomial (must exceed degree).",
    )
    return parser.parse_args()


def load_geotransform(dem_path: Optional[Path]) -> Optional[Tuple[float, ...]]:
    if dem_path is None:
        return None

    try:
        from osgeo import gdal  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "--dem was specified but GDAL is not available in this interpreter."
        ) from exc

    ds = gdal.Open(str(dem_path), gdal.GA_ReadOnly)
    if ds is None:
        raise RuntimeError(f"Failed to open DEM at {dem_path}")
    gt = ds.GetGeoTransform()
    if gt is None:
        raise RuntimeError("DEM is missing a GeoTransform.")
    return tuple(gt)


def compute_planar_delta(
    dx: float,
    dy: float,
    geotransform: Optional[Sequence[float]],
    scale_x: float,
    scale_y: float,
) -> float:
    if geotransform:
        map_dx = geotransform[1] * dx + geotransform[2] * dy
        map_dy = geotransform[4] * dx + geotransform[5] * dy
        return math.hypot(map_dx, map_dy)
    return math.hypot(dx * scale_x, dy * scale_y)


def load_trace_rows(args: argparse.Namespace) -> List[TraceRow]:
    rows: List[TraceRow] = []
    with args.input.open(newline="") as f:
        reader = csv.DictReader(f)
        for idx, raw in enumerate(reader):
            if args.max_rows is not None and idx >= args.max_rows:
                break
            try:
                px = float(raw[args.pixel_x_col])
                py = float(raw[args.pixel_y_col])
                dist = float(raw[args.distance_col])
            except KeyError as exc:
                raise KeyError(f"CSV is missing required column {exc!s}") from exc
            except ValueError as exc:
                raise ValueError(
                    f"Failed to parse numeric values on row {idx + 2}"
                ) from exc

            seg_key = raw.get(args.segment_col) if args.segment_col else None
            rows.append(
                TraceRow(
                    row_index=idx,
                    raw=raw,
                    pixel_x=px,
                    pixel_y=py,
                    distance_m=dist,
                    segment_key=seg_key,
                )
            )
    if not rows:
        raise RuntimeError(f"No rows found in {args.input}")
    return rows


def split_segments(
    rows: Sequence[TraceRow], args: argparse.Namespace
) -> List[List[TraceRow]]:
    segments: List[List[TraceRow]] = []
    current: List[TraceRow] = []
    prev_dist: Optional[float] = None
    prev_seg_key: Optional[str] = None

    for row in rows:
        dist = row.distance_m
        seg_key = row.segment_key

        new_segment = False
        if args.segment_col:
            if prev_seg_key is None or seg_key != prev_seg_key:
                new_segment = True
        else:
            if prev_dist is not None and dist + args.reset_threshold < prev_dist:
                new_segment = True

        if new_segment and current:
            segments.append(current)
            current = []

        current.append(row)
        prev_dist = dist
        prev_seg_key = seg_key

    if current:
        segments.append(current)
    return segments


def percentile(values: Sequence[float], pct: float) -> float:
    if not values:
        return math.nan
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    k = (pct / 100.0) * (len(ordered) - 1)
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return ordered[int(k)]
    return ordered[lo] * (hi - k) + ordered[hi] * (k - lo)


def evaluate_segments(
    segments: Sequence[List[TraceRow]],
    args: argparse.Namespace,
    geotransform: Optional[Sequence[float]],
) -> Tuple[List[Dict[str, float]], List[Tuple[int, int, float]]]:
    per_sample: List[Dict[str, float]] = []
    seg_stats: List[Tuple[int, int, float]] = []
    scale_x = args.scale_x
    scale_y = args.scale_y

    for seg_idx, seg in enumerate(segments):
        start_x = seg[0].pixel_x
        start_y = seg[0].pixel_y
        start_dist = seg[0].distance_m
        seg_max_err = 0.0

        for local_idx, row in enumerate(seg):
            dx = row.pixel_x - start_x
            dy = row.pixel_y - start_y
            planar = compute_planar_delta(dx, dy, geotransform, scale_x, scale_y)
            true_increment = row.distance_m - start_dist
            error = planar - true_increment
            seg_max_err = max(seg_max_err, abs(error))

            per_sample.append(
                {
                    "segment_index": seg_idx,
                    "segment_row": local_idx,
                    "input_row": row.row_index,
                    "step_index": float(row.raw.get(args.step_col, local_idx)),
                    "true_distance_m": row.distance_m,
                    "true_increment_m": true_increment,
                    "planar_increment_m": planar,
                    "error_m": error,
                }
            )

        seg_stats.append((seg_idx, len(seg), seg_max_err))

    return per_sample, seg_stats


def select_sample_indices(length: int, desired: int) -> List[int]:
    desired = max(1, min(desired, length))
    if desired == length:
        return list(range(length))
    if desired == 1:
        return [0]
    return sorted(
        {
            min(
                length - 1,
                round(i * (length - 1) / (desired - 1)),
            )
            for i in range(desired)
        }
    )


def summarize_errors(per_sample: Sequence[Dict[str, float]]) -> None:
    if not per_sample:
        print("No samples to summarize.")
        return

    errors = [abs(item["error_m"]) for item in per_sample]
    signed = [item["error_m"] for item in per_sample]
    rms = math.sqrt(sum(err * err for err in signed) / len(signed))
    print(f"Samples analyzed: {len(per_sample)}")
    print(f"Mean abs error: {statistics.fmean(errors):.6f} m")
    print(f"RMS error: {rms:.6f} m")
    print(f"Max abs error: {max(errors):.6f} m")
    print(f"95th percentile abs error: {percentile(errors, 95.0):.6f} m")
    print(f"99th percentile abs error: {percentile(errors, 99.0):.6f} m")


def summarize_poly_errors(samples: Sequence[Dict[str, float]]) -> None:
    if not samples:
        print("  No polynomial samples to summarize.")
        return
    errors = [abs(item["error_m"]) for item in samples]
    signed = [item["error_m"] for item in samples]
    rms = math.sqrt(sum(err * err for err in signed) / len(signed))
    print(f"  Samples analyzed: {len(samples)}")
    print(f"  Mean abs error: {statistics.fmean(errors):.6f} m")
    print(f"  RMS error: {rms:.6f} m")
    print(f"  Max abs error: {max(errors):.6f} m")
    print(f"  95th percentile abs error: {percentile(errors, 95.0):.6f} m")
    print(f"  99th percentile abs error: {percentile(errors, 99.0):.6f} m")


def dump_details(path: Path, per_sample: Sequence[Dict[str, float]]) -> None:
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "segment_index",
                "segment_row",
                "input_row",
                "step_index",
                "true_distance_m",
                "true_increment_m",
                "planar_increment_m",
                "error_m",
            ]
        )
        for item in per_sample:
            writer.writerow(
                [
                    int(item["segment_index"]),
                    int(item["segment_row"]),
                    int(item["input_row"]),
                    item["step_index"],
                    f"{item['true_distance_m']:.9f}",
                    f"{item['true_increment_m']:.9f}",
                    f"{item['planar_increment_m']:.9f}",
                    f"{item['error_m']:.9f}",
                ]
            )


def main() -> None:
    args = parse_args()
    geotransform = load_geotransform(args.dem)
    rows = load_trace_rows(args)
    segments = split_segments(rows, args)
    print(f"Segments detected: {len(segments)}")
    per_sample, seg_stats = evaluate_segments(segments, args, geotransform)
    summarize_errors(per_sample)

    if seg_stats:
        top = max(seg_stats, key=lambda item: item[2])
        print(
            f"Worst segment #{top[0]} had {top[1]} samples "
            f"with max abs error {top[2]:.6f} m"
        )

    if args.write_details:
        dump_details(args.write_details, per_sample)
        print(f"Wrote per-sample details to {args.write_details}")

    if args.poly_degree is not None:
        if np is None:
            raise RuntimeError(
                "--poly-degree requested but numpy is not available in this environment."
            )
        degree = args.poly_degree
        sample_count = max(args.poly_sample_count, degree + 1)
        poly_samples: List[Dict[str, float]] = []
        for seg_idx, seg in enumerate(segments):
            seg_planar = [
                item["planar_increment_m"]
                for item in per_sample
                if int(item["segment_index"]) == seg_idx
            ]
            seg_true = [
                item["true_increment_m"]
                for item in per_sample
                if int(item["segment_index"]) == seg_idx
            ]
            if not seg_planar:
                continue
            idxs = select_sample_indices(len(seg_planar), sample_count)
            xs = np.array([seg_planar[i] for i in idxs], dtype=np.float64)
            ys = np.array([seg_true[i] for i in idxs], dtype=np.float64)
            coeffs = np.polyfit(xs, ys, degree)
            pred = np.polyval(coeffs, np.array(seg_planar, dtype=np.float64))
            for local_idx, (p, t, estimate) in enumerate(zip(seg_planar, seg_true, pred)):
                poly_samples.append(
                    {
                        "segment_index": seg_idx,
                        "segment_row": local_idx,
                        "planar_increment_m": p,
                        "true_increment_m": t,
                        "estimate_m": float(estimate),
                        "error_m": float(estimate - t),
                    }
                )
        print(
            f"Polynomial degree {degree} fit using {sample_count} samples per segment:"
        )
        summarize_poly_errors(poly_samples)


if __name__ == "__main__":
    main()
