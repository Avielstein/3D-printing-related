# 3D Printing Mesh Processing

Python toolkit for processing scanned STL files for 3D printing.

## Setup

```bash
# Create virtual environment (first time only)
python3 -m venv venv

# Activate it (do this every time)
source venv/bin/activate

# Install dependencies (first time only)
pip install -r requirements.txt
```

## Quick Start

Activate venv first:
```bash
source venv/bin/activate
```

Then process your files:
```bash
# Scale a tiny scan to proper size (e.g., 80mm)
python3 scale_to_size.py stl_files/original/scan.stl 80 \
  -o stl_files/processed/scan_80mm.stl --smooth 3

# Analyze a mesh
python3 mesh_processor.py stl_files/original/scan.stl --analyze

# Repair a mesh
python3 mesh_processor.py stl_files/original/scan.stl --repair \
  -o stl_files/processed/scan_fixed.stl

# Batch process multiple files
python3 batch_process.py stl_files/original/ stl_files/processed/ \
  --repair --scale 80 --center
```

## Folder Structure

```
stl_files/
├── original/   # Original scans from Scaniverse
└── processed/  # Processed/scaled versions
```

## Common Operations

**Scale to specific size:**
```bash
python3 scale_to_size.py input.stl 80 -o output.stl
```

**Repair mesh:**
```bash
python3 mesh_processor.py input.stl --repair -o output.stl
```

**Full pipeline (repair + smooth + scale + center):**
```bash
python3 mesh_processor.py input.stl --repair --smooth 3 --scale 80 --center -o output.stl
```

## Scripts

- `mesh_processor.py` - Main processing tool with all features
- `scale_to_size.py` - Quick scaling utility
- `batch_process.py` - Process multiple files at once

## Resources

- [Trimesh Docs](https://trimsh.org/)
- [MeshMixer](https://www.meshmixer.com/)
- [Scaniverse](https://scaniverse.com/)
