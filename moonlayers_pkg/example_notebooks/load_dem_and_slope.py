import marimo

__generated_with = "0.17.3"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo
    import matplotlib.pyplot as plt
    import numpy as np
    from typing import Tuple
    from lunarsiteeval import LunarDataset

    #print(plt.__version__)
    print(plt.get_backend())

    dataset = LunarDataset(r'/d/datasets/viper_v71_2025_medium')

    dem_array = dataset.load_dem_tif()

    x, y = np.meshgrid(np.arange(dem_array.shape[1]), np.arange(dem_array.shape[0]))
    z = dem_array.flatten()

    # Create a contour plot
    contours = plt.contour(x, y, z.reshape(dem_array.shape), levels=20)

    # Add contour labels if desired
    plt.clabel(contours, inline=True, fontsize=8)

    # Set plot title and labels
    plt.title('Contour Plot of DEM')
    plt.xlabel('X Coordinate')
    plt.ylabel('Y Coordinate')

    # Display the plot using marimo's stop function to keep it reactive
    #mo.stop(predicate=None, output=plt.gca())
    contours
    return Tuple, contours, dem_array, plt


@app.cell
def _(Tuple, contours, dem_array, plt):
    # Ensure this runs after the contour plot
    _ = contours

    _hw: Tuple[int, int] = dem_array.shape
    _h, _w = _hw
    _cx: float = (_w - 1) / 2
    _cy: float = (_h - 1) / 2

    plt.plot([_cx], [_cy],
             marker='*', markersize=14,
             color='red', markeredgecolor='black', markeredgewidth=0.8,
             zorder=5)

    plt.gca()
    return


if __name__ == "__main__":
    app.run()
