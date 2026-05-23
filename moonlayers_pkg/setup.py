"""
Quick setup script for MoonLayers package.
Checks prerequisites and guides through setup.
"""

import sys
import subprocess
import os
from pathlib import Path

def run_command(cmd, description):
    """Run a command and report success/failure."""
    print(f"\n{description}...")
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if result.returncode == 0:
            print(f"✓ {description} completed")
            return True
        else:
            print(f"✗ {description} failed:")
            print(result.stderr)
            return False
    except Exception as e:
        print(f"✗ {description} failed: {e}")
        return False

def check_command(cmd, name):
    """Check if a command is available."""
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True)
        return result.returncode == 0
    except:
        return False

def main():
    print("=" * 60)
    print("MoonLayers Setup Script")
    print("=" * 60)
    
    # Check prerequisites
    print("\n[1/6] Checking prerequisites...")
    
    has_python = check_command("python --version", "Python")
    has_node = check_command("node --version", "Node.js")
    has_npm = check_command("npm --version", "npm")
    
    if has_python:
        result = subprocess.run("python --version", shell=True, capture_output=True, text=True)
        print(f"  ✓ Python: {result.stdout.strip()}")
    else:
        print("  ✗ Python not found")
    
    if has_node:
        result = subprocess.run("node --version", shell=True, capture_output=True, text=True)
        print(f"  ✓ Node.js: {result.stdout.strip()}")
    else:
        print("  ✗ Node.js not found")
    
    if has_npm:
        result = subprocess.run("npm --version", shell=True, capture_output=True, text=True)
        print(f"  ✓ npm: {result.stdout.strip()}")
    else:
        print("  ✗ npm not found")
    
    if not (has_python and has_node and has_npm):
        print("\n✗ Prerequisites missing. Please install:")
        if not has_python:
            print("  - Python 3.8+: https://www.python.org/downloads/")
        if not has_node or not has_npm:
            print("  - Node.js 18+: https://nodejs.org/")
        sys.exit(1)
    
    # Install Python dependencies
    print("\n[2/6] Installing Python dependencies...")
    if not run_command("pip install -e .", "Installing Python package"):
        print("  Hint: Make sure you're in the moonlayers directory")
        sys.exit(1)
    
    # Install JavaScript dependencies
    print("\n[3/6] Installing JavaScript dependencies...")
    if not run_command("npm install", "Installing npm packages"):
        print("  Hint: Check your internet connection")
        sys.exit(1)
    
    # Build frontend
    print("\n[4/6] Building frontend...")
    if not run_command("npm run build", "Building JavaScript bundle"):
        print("  Hint: Check for errors in src/ files")
        sys.exit(1)
    
    # Verify build outputs
    print("\n[5/6] Verifying build outputs...")
    static_dir = Path("moonlayers/static")
    index_js = static_dir / "index.js"
    index_css = static_dir / "index.css"
    
    if index_js.exists():
        size = index_js.stat().st_size
        print(f"  ✓ index.js created ({size:,} bytes)")
    else:
        print(f"  ✗ index.js not found")
        sys.exit(1)
    
    if index_css.exists():
        size = index_css.stat().st_size
        print(f"  ✓ index.css created ({size:,} bytes)")
    else:
        print(f"  ✗ index.css not found")
        sys.exit(1)
    
    # Run quick tests
    print("\n[6/6] Running quick tests...")
    if not run_command("python -c \"from moonlayers import MoonMap; print('Import successful')\"", "Testing import"):
        sys.exit(1)
    
    # Success!
    print("\n" + "=" * 60)
    print("✓ Setup completed successfully!")
    print("=" * 60)
    
    print("\nNext steps:")
    print("  1. Run tests:")
    print("     pytest tests/ -v")
    print()
    print("  2. Run manual test:")
    print("     python tests/manual_test.py")
    print()
    print("  3. Try the demo:")
    print("     pip install marimo")
    print("     marimo edit examples/south_pole_demo.mo.py")
    print()
    print("  4. Read the documentation:")
    print("     - README.md - API reference and examples")
    print("     - BUILD.md - Detailed build instructions")
    print("     - CONTRIBUTING.md - Development guide")
    print()
    print("Happy mapping! 🌙")

if __name__ == "__main__":
    main()
