# 3D Printing Mesh Processing Toolkit

A Python-based toolkit for processing scanned STL files for 3D printing. Designed to work with files from Scaniverse and other 3D scanning apps.

## Features

- **Mesh Analysis**: Comprehensive diagnostics for mesh quality
- **Mesh Repair**: Fix holes, non-manifold edges, and other printing issues
- **Scaling & Sizing**: Precisely scale models to fit your print bed
- **Smoothing**: Remove scanning artifacts
- **Mesh Simplification**: Reduce file size while preserving detail
- **Batch Processing**: Process multiple files at once
- **Print Bed Centering**: Automatically position models for printing

## Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

This will install:
- `trimesh` - Primary mesh manipulation library
- `pymeshfix` - Mesh repair and fixing
- `meshio` - Mesh I/O for various formats
- `numpy` - Numerical operations
- `scipy` - Scientific computing
- `pyvista` - 3D visualization (optional)
- And other utilities

### 2. Verify Installation

```bash
python3 mesh_processor.py --help
```

## Usage

### Basic Commands

#### Analyze a Mesh
```bash
python3 mesh_processor.py Statue_w_hat_y_gafas.stl --analyze
```

This will show:
- Mesh dimensions
- Face/vertex counts
- Quality issues (watertight, manifold, etc.)
- Surface area and volume

#### Repair a Mesh
```bash
python3 mesh_processor.py Statue_w_hat_y_gafas.stl --repair --output fixed.stl
```

#### Scale to Specific Size (4x4x4 inches = 101.6mm)
```bash
# Scale the largest dimension to fit in 4 inches (101.6mm)
python3 mesh_processor.py Statue_w_hat_y_gafas.stl --scale 101.6 --output scaled.stl
```

#### Full Processing Pipeline
```bash
python3 mesh_processor.py Statue_w_hat_y_gafas.stl \
  --repair \
  --smooth 5 \
  --scale 101.6 \
  --center \
  --output printable.stl
```

### Advanced Features

#### Reduce Mesh Complexity
```bash
# Reduce to 50,000 faces
python3 mesh_processor.py input.stl --reduce 50000 --output simplified.stl
```

#### Scale on Different Axis
```bash
# Scale X axis to 100mm
python3 mesh_processor.py input.stl --scale 100 --axis x --output scaled.stl
```

### Batch Processing

Process multiple files at once:

```bash
python3 batch_process.py input_folder/ output_folder/ --repair --scale 101.6 --center
```

## Workflow Examples

### For Scanned Models (Scaniverse → 3D Printer)

```bash
# 1. Analyze the scan
python3 mesh_processor.py scan.stl --analyze

# 2. Repair and prepare for printing (fit in 4x4x4 inch build volume)
python3 mesh_processor.py scan.stl \
  --repair \
  --smooth 3 \
  --scale 101.6 \
  --center \
  --output ready_to_print.stl

# 3. Open in MeshMixer to verify
# The output file can now be opened in MeshMixer for inspection
```

### For Multiple Files

```bash
# Process all STL files in a directory
python3 batch_process.py scans/ processed/ \
  --repair \
  --smooth 3 \
  --scale 101.6 \
  --center
```

## Size Constraints

### 4x4x4 Inch Build Volume
- 4 inches = 101.6 mm
- Use `--scale 101.6` to fit models within this constraint
- The script scales the longest dimension, maintaining aspect ratio

### Other Common Sizes
- Ender 3: 220x220x250mm → use `--scale 220`
- Prusa Mini: 180x180x180mm → use `--scale 180`
- Prusa MK3: 250x210x210mm → use `--scale 210`

## Common Issues & Solutions

### "Mesh is not watertight"
**Solution**: Use `--repair` flag
```bash
python3 mesh_processor.py input.stl --repair --output fixed.stl
```

### File Too Large for Slicer
**Solution**: Reduce mesh complexity
```bash
python3 mesh_processor.py input.stl --reduce 50000 --output smaller.stl
```

### Rough/Noisy Surface from Scan
**Solution**: Apply smoothing
```bash
python3 mesh_processor.py input.stl --smooth 5 --output smoothed.stl
```

### Model Too Large for Print Bed
**Solution**: Scale to fit
```bash
python3 mesh_processor.py input.stl --scale 101.6 --output sized.stl
```

## Understanding Each Operation

### Repair (`--repair`)
- Fixes holes in the mesh
- Resolves non-manifold edges
- Removes degenerate faces
- **Why**: 3D printers require watertight meshes to slice properly

### Smooth (`--smooth N`)
- Applies Laplacian smoothing
- N = number of iterations (3-10 typical)
- **Why**: Removes scanning artifacts and noise

### Scale (`--scale SIZE`)
- Resizes mesh to exact dimensions
- Maintains aspect ratio
- **Why**: Ensures model fits on print bed

### Center (`--center`)
- Centers model on XY plane
- Places bottom at Z=0
- **Why**: Proper bed positioning for slicing

### Reduce (`--reduce N`)
- Simplifies mesh to N faces
- Uses quadric decimation (quality preserving)
- **Why**: Smaller files, faster slicing, less memory

## File Formats Supported

- **STL** (Binary and ASCII)
- **OBJ**
- **PLY**
- **OFF**
- **3MF** (with additional libraries)

## MeshMixer Workflow

After processing with this toolkit:

1. Open MeshMixer
2. Import → Select your processed `.stl` file
3. Use Analysis → Inspector to verify watertight
4. Make additional manual edits if needed
5. Export → STL for final printing

## Tips for Best Results

1. **Always analyze first**: `--analyze` shows what needs fixing
2. **Repair early**: Fix mesh issues before other operations
3. **Smooth carefully**: Too much smoothing loses detail (5 iterations max)
4. **Check dimensions**: Verify size fits your printer's build volume
5. **Test print small**: Scale down first to test print quality

## Scripts Overview

- **mesh_processor.py**: Main processing tool for single files
- **batch_process.py**: Process multiple files at once
- **requirements.txt**: Python dependencies

## Contributing

Feel free to add more processing utilities:
- Hollowing scripts
- Support generation
- Custom orientation tools
- Generative design helpers

## Future Enhancements

- [ ] Automatic hollowing with drainage holes
- [ ] Custom support structure generation
- [ ] Mesh analysis visualization
- [ ] Integration with generative AI for custom designs
- [ ] Scaniverse integration scripts
- [ ] Automatic print time/material estimation

## Resources

- [Trimesh Documentation](https://trimsh.org/)
- [PyMeshFix](https://github.com/pyvista/pymeshfix)
- [MeshMixer Download](https://www.meshmixer.com/)
- [Scaniverse App](https://scaniverse.com/)
