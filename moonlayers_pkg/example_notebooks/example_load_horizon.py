import marimo

__generated_with = "0.17.3"
app = marimo.App(width="medium")


@app.cell
def _():
    from lunarsiteeval import LunarDataset
    import matplotlib.pyplot as plt

    dataset = LunarDataset(r'/d/datasets/viper_v71_2025_medium')
    horizon = dataset.fetch_horizon_for_pixel(100, 100, 0)
    azimuth_deg: list[float] = [i / 4.0 for i in range(len(horizon))]

    # This cell will automatically update when 'horizon' value changes.
    plt.figure(figsize=(10, 6))
    plt.title('Horizon Elevation vs. Azimuth')
    plt.xlabel('Azimuth (degrees)')
    plt.ylabel('Elevation (degrees)')
    plt.plot(azimuth_deg, horizon, marker='o', markersize=2, linewidth=1)
    plt.xlim(0, 360)
    plt.grid(True, alpha=0.3)
    ax = plt.gca()
    ax.set_xticks([0, 45, 90, 135, 180, 225, 270, 315, 360])
    plt.gca()
    return


if __name__ == "__main__":
    app.run()
