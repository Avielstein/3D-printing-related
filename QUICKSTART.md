# Quick Start Guide

## 🚀 Getting Started

### 1. Activate the Virtual Environment

Every time you open a new terminal, activate the virtual environment:

```bash
source venv/bin/activate
```

You'll see `(venv)` at the start of your prompt when it's active.

### 2. Process Your Scans

Your STL files from Scaniverse appear to be extremely tiny (0.44mm). This is a common scaling issue. Here's how to fix it:

#### Scale to Proper Size (e.g., 80mm tall statue)

```bash
python3 scale_to_size.py Statue_w_hat_y_gafas.stl 80
```

This creates: `Statue_w_hat_y_gafas_80mm.stl`

#### For 4x4x4 inch build volume (100mm safe max):

```bash
python3 scale_to_size.py Statue_w_hat_y_gafas.stl 100
```

#### With smoothing for better surface finish:

```bash
python3 scale_to_size.py Statue_w_hat_y_gafas.stl 80 --smooth 3
```

### 3. Open in MeshMixer

1. **Launch MeshMixer**
2. **Import** → Select your `*_80mm.stl` or `*_100mm.stl` file
3. **Analysis → Inspector** to verify watertight
4. Make any manual adjustments you need
5. **Export** → STL for your slicer

### 4. Common Workflows

#### Just Analyze a File
```bash
python3 mesh_processor.py yourfile.stl --analyze
```

#### Repair Only
```bash
python3 mesh_processor.py yourfile.stl --repair --output fixed.stl
```

#### Full Processing Pipeline
```bash
python3 mesh_processor.py scan.stl \
  --repair \
  --smooth 3 \
  --scale 80 \
  --center \
  --output ready.stl
```

#### Batch Process Multiple Files
```bash
python3 batch_process.py input_folder/ output_folder/ --repair --scale 80 --center
```

## 📏 Size Reference

- **4x4x4 inches = 101.6mm** (use 100mm for safety margin)
- **3x3x3 inches = 76.2mm** (use 75mm for safety)
- **2x2x2 inches = 50.8mm** (use 50mm)

Typical statue sizes:
- Small desk piece: 50-80mm
- Medium display: 80-120mm
- Large print: 150-200mm

## 🔧 Troubleshooting

### "Mesh is not watertight"
**Solution**: Use `--repair` flag
```bash
python3 scale_to_size.py file.stl 80 --repair
```

### File appears tiny in slicer
**Problem**: Scaling issue from scan
**Solution**: Use `scale_to_size.py` to set proper dimensions

### Surface looks rough/noisy
**Solution**: Add smoothing
```bash
python3 scale_to_size.py file.stl 80 --smooth 5
```

### File too large (slow slicing)
**Solution**: Reduce mesh complexity
```bash
python3 mesh_processor.py file.stl --reduce 50000 --output smaller.stl
```

## 📝 Notes

- **Original scans are preserved** - processed files have different names
- **Git ignores processed files** - keeps your repo clean
- **All operations are non-destructive** - original files never modified
- **Activate venv first** - always run `source venv/bin/activate` before using scripts

## 🎯 Recommended First Steps

1. Scale your statues to a printable size (80-100mm):
   ```bash
   python3 scale_to_size.py Statue_w_hat_y_gafas.stl 80 --smooth 3
   python3 scale_to_size.py Statue_w_hat_y_gafas_printable_solid.stl 80 --smooth 3
   ```

2. Open the `*_80mm.stl` files in MeshMixer to verify

3. Export from MeshMixer and slice in your slicer software

## 💡 Tips

- Start with smaller sizes for test prints
- Use smoothing sparingly (3-5 iterations) to preserve detail
- Check dimensions in MeshMixer before final export
- Keep originals in git, processed files are git-ignored
- For miniatures, aim for 30-50mm height
- For display pieces, 80-120mm is ideal

## 🔗 More Help

See `README.md` for complete documentation and advanced features.
