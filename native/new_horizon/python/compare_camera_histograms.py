#!/usr/bin/env python3
import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from osgeo import gdal


def parse_args():
    parser = argparse.ArgumentParser(
        description="Compare synthetic and observed camera-image brightness histograms."
    )
    parser.add_argument(
        "--synthetic",
        default=r"/d/projects/new_horizon/output_camera/camera_image_2014-09-08T04-42-04.tif",
        help="Path to synthetic camera GeoTIFF.",
    )
    parser.add_argument(
        "--observed",
        default=r"/d/datasets/new_horizon/sun_tif/2014-09-08T04-42-04.c.nac_image.tif",
        help="Path to observed camera GeoTIFF.",
    )
    parser.add_argument(
        "--bins",
        type=int,
        default=256,
        help="Number of histogram bins.",
    )
    parser.add_argument(
        "--out-plot",
        default=str(Path(__file__).with_name("camera_histogram_comparison.png")),
        help="Path to output PNG plot.",
    )
    return parser.parse_args()


def dataset_bounds(gt, width, height):
    corners = [(0, 0), (width, 0), (0, height), (width, height)]
    world = [gdal.ApplyGeoTransform(gt, x, y) for (x, y) in corners]
    xs = [p[0] for p in world]
    ys = [p[1] for p in world]
    return min(xs), min(ys), max(xs), max(ys)


def read_band_with_mask(ds):
    band = ds.GetRasterBand(1)
    arr = band.ReadAsArray().astype(np.float32)
    nodata = band.GetNoDataValue()
    mask = np.isfinite(arr)
    if nodata is not None:
        mask &= arr != nodata
    return arr, mask, nodata


def load_and_align(observed_path, synthetic_ds):
    src_ds = gdal.Open(observed_path, gdal.GA_ReadOnly)
    if src_ds is None:
        raise RuntimeError(f"Failed to open observed raster: {observed_path}")

    ref_gt = synthetic_ds.GetGeoTransform()
    ref_proj = synthetic_ds.GetProjection()
    ref_w = synthetic_ds.RasterXSize
    ref_h = synthetic_ds.RasterYSize
    xmin, ymin, xmax, ymax = dataset_bounds(ref_gt, ref_w, ref_h)

    src_nodata = src_ds.GetRasterBand(1).GetNoDataValue()
    warp_opts = gdal.WarpOptions(
        format="MEM",
        width=ref_w,
        height=ref_h,
        dstSRS=ref_proj,
        outputBounds=(xmin, ymin, xmax, ymax),
        resampleAlg="bilinear",
        srcNodata=src_nodata,
        dstNodata=np.nan,
        outputType=gdal.GDT_Float32,
    )
    aligned = gdal.Warp("", src_ds, options=warp_opts)
    if aligned is None:
        raise RuntimeError("GDAL warp failed while aligning observed raster.")
    return aligned


def robust_normalize(values, lo=1.0, hi=99.0):
    p_lo, p_hi = np.percentile(values, [lo, hi])
    if not np.isfinite(p_lo) or not np.isfinite(p_hi) or p_hi <= p_lo:
        return np.zeros_like(values, dtype=np.float32), p_lo, p_hi
    scaled = (values - p_lo) / (p_hi - p_lo)
    return np.clip(scaled, 0.0, 1.0).astype(np.float32), p_lo, p_hi


def hist_metrics(a_norm, b_norm, bins):
    c1, edges = np.histogram(a_norm, bins=bins, range=(0.0, 1.0))
    c2, _ = np.histogram(b_norm, bins=bins, range=(0.0, 1.0))
    p = c1.astype(np.float64) / max(1, c1.sum())
    q = c2.astype(np.float64) / max(1, c2.sum())
    m = 0.5 * (p + q)

    eps = 1e-15
    p_safe = np.clip(p, eps, 1.0)
    q_safe = np.clip(q, eps, 1.0)
    m_safe = np.clip(m, eps, 1.0)

    kl_pm = np.sum(p_safe * np.log(p_safe / m_safe))
    kl_qm = np.sum(q_safe * np.log(q_safe / m_safe))
    js_div = 0.5 * (kl_pm + kl_qm)

    hist_intersection = np.sum(np.minimum(p, q))
    bc = np.sum(np.sqrt(p * q))
    bhatta_dist = -np.log(max(bc, eps))

    cdf_p = np.cumsum(p)
    cdf_q = np.cumsum(q)
    ks_dist = np.max(np.abs(cdf_p - cdf_q))

    return {
        "edges": edges,
        "p": p,
        "q": q,
        "js_divergence": js_div,
        "hist_intersection": hist_intersection,
        "bhattacharyya_distance": bhatta_dist,
        "ks_distance": ks_dist,
    }


def summarize(name, values):
    return (
        f"{name}: min={np.min(values):.6g}, max={np.max(values):.6g}, "
        f"mean={np.mean(values):.6g}, std={np.std(values):.6g}, median={np.median(values):.6g}"
    )


def quality_label(js_div, intersection, ks_dist):
    if js_div < 0.03 and intersection > 0.85 and ks_dist < 0.12:
        return "good histogram agreement"
    if js_div < 0.10 and intersection > 0.65 and ks_dist < 0.25:
        return "moderate histogram agreement"
    return "poor histogram agreement"


def main():
    gdal.UseExceptions()
    args = parse_args()

    synth_ds = gdal.Open(args.synthetic, gdal.GA_ReadOnly)
    if synth_ds is None:
        raise RuntimeError(f"Failed to open synthetic raster: {args.synthetic}")

    aligned_obs_ds = load_and_align(args.observed, synth_ds)
    print(f"Synthetic file: {Path(args.synthetic).resolve()}")
    print(f"Observed file:  {Path(args.observed).resolve()}")

    synth_arr, synth_mask, _ = read_band_with_mask(synth_ds)
    obs_arr, obs_mask, _ = read_band_with_mask(aligned_obs_ds)

    valid = synth_mask & obs_mask
    valid_count = int(np.count_nonzero(valid))
    if valid_count == 0:
        raise RuntimeError("No overlapping valid pixels after alignment.")

    synth_vals = synth_arr[valid].astype(np.float64)
    obs_vals = obs_arr[valid].astype(np.float64)

    synth_norm, s_lo, s_hi = robust_normalize(synth_vals)
    obs_norm, o_lo, o_hi = robust_normalize(obs_vals)
    metrics = hist_metrics(synth_norm, obs_norm, bins=args.bins)

    if synth_norm.size > 1 and obs_norm.size > 1:
        corr = float(np.corrcoef(synth_norm, obs_norm)[0, 1])
    else:
        corr = float("nan")

    label = quality_label(
        metrics["js_divergence"], metrics["hist_intersection"], metrics["ks_distance"]
    )

    print(f"Valid overlapping pixels: {valid_count}")
    print(summarize("Synthetic (raw)", synth_vals))
    print(summarize("Observed  (raw)", obs_vals))
    print(
        f"Synthetic normalize p1/p99: {s_lo:.6g}, {s_hi:.6g}; "
        f"Observed normalize p1/p99: {o_lo:.6g}, {o_hi:.6g}"
    )
    print(f"Pearson corr (normalized paired pixels): {corr:.6f}")
    print(f"Jensen-Shannon divergence: {metrics['js_divergence']:.6f}")
    print(f"Histogram intersection: {metrics['hist_intersection']:.6f}")
    print(f"Bhattacharyya distance: {metrics['bhattacharyya_distance']:.6f}")
    print(f"KS distance (CDF max gap): {metrics['ks_distance']:.6f}")
    print(f"Result: {label}")

    edges = metrics["edges"]
    centers = 0.5 * (edges[:-1] + edges[1:])
    cdf_s = np.cumsum(metrics["p"])
    cdf_o = np.cumsum(metrics["q"])

    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5), dpi=120)
    axes[0].plot(centers, metrics["q"], label="Observed", linewidth=0.9, zorder=2)
    axes[0].plot(centers, metrics["p"], label="Synthetic", linewidth=0.9, zorder=3)
    axes[0].set_title("Brightness Histogram (normalized)")
    axes[0].set_xlabel("Normalized brightness [0,1]")
    axes[0].set_ylabel("Probability")
    axes[0].grid(True, alpha=0.25)
    axes[0].legend()

    axes[1].plot(centers, cdf_o, label="Observed CDF", linewidth=0.9, zorder=2)
    axes[1].plot(centers, cdf_s, label="Synthetic CDF", linewidth=0.9, zorder=3)
    axes[1].set_title("CDF Comparison")
    axes[1].set_xlabel("Normalized brightness [0,1]")
    axes[1].set_ylabel("Cumulative probability")
    axes[1].grid(True, alpha=0.25)
    axes[1].legend()

    fig.suptitle(
        "Camera Brightness Comparison\n"
        f"JS={metrics['js_divergence']:.4f}, Intersection={metrics['hist_intersection']:.4f}, "
        f"KS={metrics['ks_distance']:.4f}, Corr={corr:.4f} -> {label}"
    )
    fig.tight_layout()
    fig.savefig(args.out_plot)
    print(f"Saved plot: {args.out_plot}")
    plt.show()


if __name__ == "__main__":
    main()
