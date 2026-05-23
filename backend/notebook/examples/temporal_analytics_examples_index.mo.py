import marimo

__generated_with = "0.11.0"
app = marimo.App(width="medium")


@app.cell
def __(mo):
    mo.md(
        r"""
        # Lightmap Temporal Analytics Examples

        This notebook indexes example notebooks/scripts for the v2 lightmap streaming pipeline:

        - `NativeReduce` mode: native C# computes the temporal reduction and streams compact 2D outputs.
        - `SignalStream` mode: native C# streams selected signals in chunks; Python performs custom reductions.
        """
    )
    return


@app.cell
def __():
    from pathlib import Path

    examples_dir = Path(__file__).resolve().parent
    examples = [
        {
            "file": "lightmap_average_sun_fraction_native_reduce.mo.py",
            "scenario": "Average sun fraction raster",
            "mode": "NativeReduce",
            "why": "Fast path for common lighting-average analysis.",
        },
        {
            "file": "lightmap_earth_above_terrain_duration_native_reduce.mo.py",
            "scenario": "Cumulative Earth-above-terrain duration",
            "mode": "NativeReduce",
            "why": "Uses Earth center-margin threshold + limb-reference semantics.",
        },
        {
            "file": "lightmap_combined_sun_earth_max_contiguous_native_reduce.mo.py",
            "scenario": "Max contiguous interval with Sun + Earth constraints",
            "mode": "NativeReduce",
            "why": "Efficient built-in state-machine reducer for a core 6.5 case.",
        },
        {
            "file": "lightmap_custom_chunked_signal_stream.mo.py",
            "scenario": "Custom chunked Python reducer (Sun + Earth signals)",
            "mode": "SignalStream",
            "why": "Notebook-first path for LLM-authored/custom reductions.",
        },
        {
            "file": "psr_raster_native_mapops.mo.py",
            "scenario": "Permanent shadow (PSR-style) raster via native mapops",
            "mode": "Native mapops",
            "why": "Exposes the existing native PSR generator (not the new v2 temporal reducer path).",
        },
    ]

    for item in examples:
        item["path"] = str((examples_dir / item["file"]).resolve())

    examples
    return examples, examples_dir


@app.cell
def __(examples, mo):
    rows = []
    for item in examples:
        rows.append(
            {
                "Scenario": item["scenario"],
                "Mode": item["mode"],
                "Why": item["why"],
                "File": item["file"],
            }
        )
    mo.ui.table(rows)
    return


@app.cell
def __(mo):
    mo.md(
        r"""
        ## Choosing a Mode

        **Use `NativeReduce` when:**
        - your metric matches a built-in reducer (average, cumulative duration, max contiguous duration, combined Sun+Earth contiguous)
        - you want the smallest IPC/memory footprint
        - you are running long time ranges repeatedly

        **Use `SignalStream` when:**
        - you need a custom reducer not built into native code
        - you want to iterate quickly in Python/notebooks
        - you want an LLM to generate or modify the reduction logic

        ## Execution Notes

        In each example notebook:
        - edit time range / thresholds / output path in the parameter cell
        - set `RUN_JOB = True`
        - run all cells
        """
    )
    return


@app.cell
def __(examples, mo):
    mo.md(
        "\n".join(
            [
                "## Absolute File Paths",
                *[f"- `{item['path']}`" for item in examples],
            ]
        )
    )
    return


if __name__ == "__main__":
    app.run()
