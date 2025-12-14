#!/usr/bin/env python3
"""
Scale STL to Specific Size
Scale models to a target size, perfect for scans that have incorrect scale.
"""

import argparse
import sys
from pathlib import Path
from mesh_processor import MeshProcessor


def scale_model(input_file: str, target_size: float, axis: str = 'auto', 
                output_file: str = None, smooth: int = 0):
    """
    Scale a model to a specific target size.
    
    Args:
        input_file: Input STL file
        target_size: Target size in mm for the specified axis
        axis: Which axis to scale ('x', 'y', 'z', or 'auto' for largest)
        output_file: Output filename (optional)
        smooth: Number of smoothing iterations
    """
    input_path = Path(input_file)
    
    if not output_file:
        output_file = input_path.stem + f"_{int(target_size)}mm.stl"
    
    output_path = Path(output_file)
    
    print("="*60)
    print("SCALING MODEL TO SPECIFIC SIZE")
    print("="*60)
    print(f"Input: {input_path.name}")
    print(f"Output: {output_path.name}")
    print(f"Target: {target_size}mm")
    print("="*60)
    
    try:
        # Load mesh
        processor = MeshProcessor(str(input_path))
        analysis = processor.analyze()
        
        # Determine axis to scale
        if axis == 'auto':
            max_idx = analysis['extents'].argmax()
            axis = ['x', 'y', 'z'][max_idx]
            print(f"\n📏 Auto-detected largest axis: {axis.upper()}")
        
        current_size = analysis['extents'][['x', 'y', 'z'].index(axis.lower())]
        scale_factor = target_size / current_size
        
        print(f"\nCurrent {axis.upper()} size: {current_size:.3f} mm")
        print(f"Target {axis.upper()} size: {target_size:.1f} mm")
        print(f"Scale factor: {scale_factor:.1f}x")
        
        # Check if mesh needs repair
        if not analysis['is_watertight']:
            print("\n⚠️  Repairing mesh...")
            processor.repair()
        
        # Apply smoothing if requested
        if smooth > 0:
            print(f"\n✨ Smoothing mesh ({smooth} iterations)...")
            processor.smooth(iterations=smooth)
        
        # Scale
        processor.scale_to_size(target_size, axis)
        
        # Center on bed
        processor.center_on_bed()
        
        # Export
        processor.export(str(output_path))
        
        print("\n" + "="*60)
        print("✅ MODEL SCALED AND READY!")
        print("="*60)
        print(f"\nYou can now open '{output_path.name}' in MeshMixer to:")
        print("  • Verify the size looks correct")
        print("  • Make any manual adjustments")
        print("  • Add supports if needed")
        print("  • Export for your slicer")
        print("="*60)
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description='Scale STL model to specific size',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Scale to 80mm tall statue
  python scale_to_size.py statue.stl 80
  
  # Scale to 100mm height with smoothing
  python scale_to_size.py statue.stl 100 --smooth 3
  
  # Scale width (X axis) to 50mm
  python scale_to_size.py statue.stl 50 --axis x
  
  # For 4x4 inch bed (use 100mm for safety margin)
  python scale_to_size.py statue.stl 100
        """
    )
    
    parser.add_argument('input', help='Input STL file')
    parser.add_argument('size', type=float, help='Target size in mm')
    parser.add_argument('--axis', default='auto', choices=['auto', 'x', 'y', 'z'],
                       help='Axis to scale (auto = largest dimension)')
    parser.add_argument('-o', '--output', help='Output file path (optional)')
    parser.add_argument('--smooth', type=int, default=0, help='Smoothing iterations (0-10)')
    
    args = parser.parse_args()
    
    scale_model(args.input, args.size, args.axis, args.output, args.smooth)


if __name__ == '__main__':
    main()
