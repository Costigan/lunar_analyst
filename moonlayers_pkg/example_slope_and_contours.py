import marimo

__generated_with = "0.17.3"
app = marimo.App(width="medium")


@app.cell
def _():
    from lunarsiteeval import LunarDataset
    import matplotlib.pyplot as plt
    import marimo as mo

    dataset = LunarDataset(r'/d/datasets/viper_v71_2025_medium')
    dem_array = dataset.load_dem_tif()

    fig, ax = plt.subplots(figsize=(10, 8))
    contour = ax.contourf(dem_array, levels=20, cmap='viridis')
    plt.colorbar(contour)
    ax.set_xlabel('X-axis')
    ax.set_ylabel('Y-axis')
    ax.set_title('Digital Elevation Model Contour Plot')
    mo.mpl.interactive(fig)
    fig.gca()
    return LunarDataset, dataset, dem_array, mo, plt


@app.cell
def _(dem_array, mo, plt):
    # Assuming dem_array is already loaded from the dataset
    # Create a figure and an axes object
    fig2, ax2 = plt.subplots(figsize=(10, 8))

    # Plot contour lines using plt.contour
    contour2 = ax2.contour(dem_array, levels=20, cmap='viridis')

    # Add labels and title (optional)
    ax2.set_xlabel('X-axis')
    ax2.set_ylabel('Y-axis')
    ax2.set_title('Digital Elevation Model Contour Plot')

    # Ensure that matplotlib plots are interactive within Marimo
    mo.mpl.interactive(fig2)

    # Show the plot
    plt.gca()
    return


@app.cell
def _(dataset, mo, plt):
    import numpy as np
    from scipy.ndimage import sobel
    from matplotlib.colors import ListedColormap, BoundaryNorm

    slope_array = dataset.load_slope_tif()

    fig4, ax4 = plt.subplots(figsize=(18, 16))

    colors = ['white', 'green', 'yellow', 'red']     # Define your desired colors
    bounds = [0, 5, 10, 15, 50]                  # Define the boundaries for your data ranges (1 more than # of colors)
    cmap = ListedColormap(colors)                    # Create a colormap from the list of colors
    norm = BoundaryNorm(bounds, cmap.N)              # Create a normalization object

    im = ax4.imshow(slope_array, norm=norm, cmap=cmap, interpolation='nearest')
    cbar = fig4.colorbar(im)  # Add a colorbar to the plot
    cbar.set_label('Slope Magnitude')  # Label the colorbar

    # Set labels and title for clarity
    ax4.set_xlabel('X-axis')
    ax4.set_ylabel('Y-axis')
    ax4.set_title('Slope Magnitude')

    mo.mpl.interactive(fig4)  # Make the plot interactive

    fig4.gca()  # Return the Axes object for display
    return BoundaryNorm, ListedColormap, np, slope_array


@app.cell
def _(dataset, np, slope_array):
    from osgeo import gdal, osr

    projection_info = dataset.projection_info

    # Fetch the dataset's driver to create a new GeoTIFF file
    driver = gdal.GetDriverByName('GTiff')

    # Create a memory dataset with an empty raster
    mem_driver = gdal.GetDriverByName("MEM")
    mem_ds = mem_driver.Create('', slope_array.shape[1], slope_array.shape[0], 1, gdal.GDT_Float32)

    # Set georeferencing information to match the original dataset
    mem_ds.SetGeoTransform(projection_info.transform)
    mem_ds.SetProjection(projection_info.projection)

    # Write the slope array into the memory raster band 1 (first and only band in a single-band GeoTIFF)
    band = mem_ds.GetRasterBand(1)
    band.WriteArray(slope_array.astype(np.float32))

    # Create and write a GeoTIFF from the memory dataset
    output_tiff_name = '/d/projects/slope_map.tif'
    driver.Register()
    gdal.Translate(output_tiff_name, mem_ds)

    # Close the datasets properly to free up resources
    mem_ds.FlushCache()
    mem_ds.GetDriver().DeleteDataSource('')

    # Confirm completion with print statement
    print(f"Slope map written to {output_tiff_name}")
    return


@app.cell
def _(np, slope_array):
    # Apply thresholding to the slope_array
    thresholded_array = np.where(slope_array <= 10, 1, 0).astype(np.int32)
    thresholded_array  # Display the thresholded array
    return (thresholded_array,)


@app.cell
def _():
    from lunarsiteeval.site_analyzer import SiteAnalyzer
    neighborhood_count = SiteAnalyzer.count_circular_neighborhood(100)
    neighborhood_count
    return SiteAnalyzer, neighborhood_count


@app.cell
def _(SiteAnalyzer, neighborhood_count, thresholded_array):
    cpu_circular_neighborhoods = SiteAnalyzer.sum_circular_neighborhoods(thresholded_array, 100)
    candidate_circular_threshold = int(.95*neighborhood_count)
    return candidate_circular_threshold, cpu_circular_neighborhoods


@app.cell
def _(BoundaryNorm, ListedColormap, mo, neighborhood_count, np):
    # Function to apply thresholding with a parameter
    def apply_threshold(array, threshold_percent):
        threshold = int(neighborhood_count * (threshold_percent / 100.0))
        return np.where(array <= threshold, 1, 0).astype(np.int32)

    # Create a colormap where green represents 1 and gray represents 0
    colors2 = ['gray', 'green']
    cmap2 = ListedColormap(colors2)
    bounds2 = [-1, 0.5, 2]  # Mapping values to colors
    norm2 = BoundaryNorm(bounds2, cmap2.N)

    # Create a slider for the threshold
    threshold = mo.ui.slider(50, 110, value=103, label="Required safe pixels (%)")
    threshold
    return cmap2, norm2, threshold


@app.cell
def _(candidate_circular_threshold, threshold):
    f"threshold: {threshold.value}  pixel_threshold: {candidate_circular_threshold * (threshold.value / 100.0)}"
    return


@app.cell
def _(cpu_circular_neighborhoods):
    cpu_circular_neighborhoods[0,0]
    return


@app.cell
def _(
    candidate_circular_threshold,
    cmap2,
    cpu_circular_neighborhoods,
    mo,
    norm2,
    np,
    plt,
    threshold,
):
    def visualize_thresholded_array(array, threshold):
        # Apply the threshold
        pixel_threshold = candidate_circular_threshold * (threshold.value / 100.0)
        thresholded = np.where(array >= pixel_threshold, 1, 0).astype(np.int32)

        # Create a figure and plot the array as a raster
        plt.figure(figsize=(16, 16))
        plt.imshow(thresholded, cmap=cmap2, norm=norm2)
        plt.title('Candidate Landing Sites')
        plt.axis('off')  # Optional: remove axis for cleaner look

        return thresholded, plt.gca()

    candidate_sites, plt3 = visualize_thresholded_array(cpu_circular_neighborhoods, threshold)

    mo.mpl.interactive(plt3)  # Ensure Matplotlib plots are interactive in Marimo
    return (candidate_sites,)


@app.cell
def _(SiteAnalyzer, candidate_sites):
    labeled, sites = SiteAnalyzer.get_region_centroids(candidate_sites, struct_elem_radius=5, distance_threshold=2.0)
    sites
    return labeled, sites


@app.cell
def _(labeled, mo, plt, sites):
    _dpi = 300
    _fig_width = labeled.shape[1] / _dpi
    _fig_height = labeled.shape[0] / _dpi
    _fig, _ax = plt.subplots(figsize=(_fig_width, _fig_height), dpi=_dpi)
    _ax.imshow(labeled, cmap="gray", origin="upper")
    if sites:
        ys = [c[1] for c in sites]
        xs = [c[2] for c in sites]
        _ax.scatter(xs, ys, c="red", s=40, marker="o", edgecolors="white", linewidths=0.5)
    _ax.set_axis_off()
    plt.subplots_adjust(0, 0, 1, 1)

    mo.mpl.interactive(_fig) 
    return


@app.cell
def _(LunarDataset, candidate_sites, dataset):
    green = LunarDataset.pack_argb(255, 0, 255, 0)
    transparent = LunarDataset.pack_argb(0, 0, 0, 0)
    colorizer_colors = [transparent, green]
    def colorize(pixel_value):
        return colorizer_colors[pixel_value]

    dataset.write_array_as_rgb_geotiff(candidate_sites, colorize=colorize, filename="landing_candidates.tif", overwrite=True)
    return (transparent,)


@app.cell
def _(LunarDataset, dataset, np, slope_array, transparent):
    _slope_threshold = 10.0
    low_slope = np.where(slope_array <= _slope_threshold, 1, 0)

    green2 = LunarDataset.pack_argb(255, 138, 207, 134)
    _colors = [transparent, green2]
    def _colorize(pixel_value):
        return _colors[pixel_value]

    dataset.write_array_as_rgb_geotiff(low_slope, colorize=_colorize, filename="slope_less_than_10_deg.tif", overwrite=True)
    return


if __name__ == "__main__":
    app.run()
