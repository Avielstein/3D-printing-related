#!/usr/bin/env python3
"""
Quick Processing Script for 4x4x4 inch Print Bed
Processes STL files to fit within a 4x4x4 inch (101.6mm) build volume
"""

import argparse
import sys
from pathlib import Path
from mesh_processor import MeshProcessor


def process_for_4x4_bed(input_file: str, output_file: str = None, smooth_iterations: int = 0):
    """
    Process a mesh to fit in 4x4x4 inch build volume.
    
    Args:
        input_file: Input STL file
        output_file: Output STL file (optional, will auto-generate if not provided)
        smooth_iterations: Number of smoothing iterations (0 = no smoothing)
    """
    input_path = Path(input_file)
    
    if not output_file:
        # Auto-generate output filename
        output_file = input_path.stem + "_4x4_ready.stl"
    
    output_path = Path(output_file)
    
    print("="*60)
    print("PROCESSING FOR 4x4x4 INCH PRINT BED")
    print("="*60)
    print(f"Input: {input_path.name}")
    print(f"Output: {output_path.name}")
    print(f"Target: 4 inches (101.6mm) max dimension")
    if smooth_iterations > 0:
        print(f"Smoothing: {smooth_iterations} iterations")
    print("="*60)
    
    try:
        # Load and analyze
        processor = MeshProcessor(str(input_path))
        analysis = processor.analyze()
        
        # Check current size
        max_dimension = max(analysis['extents'])
        print(f"\nCurrent max dimension: {max_dimension:.2f} mm ({max_dimension/25.4:.2f} inches)")
        
        # Determine if we need to repair
        needs_repair = not analysis['is_watertight']
        
        if needs_repair:
            print("\n⚠️  Mesh needs repair before printing")
            processor.repair()
        
        # Apply smoothing if requested
        if smooth_iterations > 0:
            processor.smooth(iterations=smooth_iterations)
        
        # Scale to fit 4x4x4 inches (101.6mm)
        # Use 100mm to leave a small margin
        target_size = 100.0  # mm
        if max_dimension > target_size:
            print(f"\n📏 Scaling down from {max_dimension:.2f}mm to {target_size}mm")
            processor.scale_to_size(target_size, axis='z' if analysis['extents'][2] == max_dimension else 
                                                      ('y' if analysis['extents'][1] == max_dimension else 'x'))
        else:
            print(f"\n✓ Mesh already fits! (max dimension: {max_dimension:.2f}mm)")
        
        # Center on bed
        processor.center_on_bed()
        
        # Export
        processor.export(str(output_path))
        
        print("\n" + "="*60)
        print("✅ READY FOR MESHMIXER & PRINTING!")
        print("="*60)
        print(f"\nNext steps:")
        print(f"1. Open MeshMixer")
        print(f"2. Import → '{output_path.name}'")
        print(f"3. Use Analysis → Inspector to verify")
        print(f"4. Export for your slicer")
        print("="*60)
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description='Process STL for 4x4x4 inch print bed',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic processing
  python process_for_printing.py scan.stl
  
  # With smoothing
  python process_for_printing.py scan.stl --smooth 3
  
  # Custom output name
  python process_for_printing.py scan.stl -o ready_to_print.stl
        """
    )
    
    parser.add_argument('input', help='Input STL file')
    parser.add_argument('-o', '--output', help='Output file path (optional)')
    parser.add_argument('--smooth', type=int, default=0, help='Smoothing iterations (0-10)')
    
    args = parser.parse_args()
    
    process_for_4x4_bed(args.input, args.output, args.smooth)


if __name__ == '__main__':
    main()
