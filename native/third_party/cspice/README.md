Pinned CSPICE native artifacts live here.

Expected layout:

- `windows-x64/cspice.dll`
- `linux-x64/libcspice.so`

Policy:

- These files are app-managed native dependencies for `moonlib`.
- Python and .NET should use the same kernel files and metakernel, but each process loads and furnishes kernels independently.
- The next acquisition step is to download the official NAIF CSPICE source/toolkit for the pinned version and build `libcspice.so` for Linux, then place the resulting artifact in `linux-x64/`.
