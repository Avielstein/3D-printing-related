#!/usr/bin/env python3
"""
Batch Processor for Multiple STL Files
Process multiple STL files at once with consistent settings.
"""

import argparse
import sys
from pathlib import Path
from mesh_processor import MeshProcessor


def batch_process(
    input_dir: str,
    output_dir: str,
    repair: bool = False,
    scale: float = None,
    axis: str = 'z',
    center: bool = False,
    smooth: int = 0,
    reduce: int = None,
    pattern: str = "*.stl"
):
    """
    Process multiple STL files with the same operations.
    
    Args:
        input_dir: Directory containing STL files
        output_dir: Directory for processed files
        repair: Whether to repair meshes
        scale: Target size for scaling
        axis: Axis for scaling
        center: Whether to center on bed
        smooth: Number of smoothing iterations
        reduce: Target face count
        pattern: Glob pattern for finding files
    """
    input_path = Path(input_dir)
    output_path = Path(output_dir)
    
    if not input_path.exists():
        print(f"❌ Error: Input directory not found: {input_dir}")
        sys.exit(1)
    
    # Create output directory if it doesn't exist
    output_path.mkdir(parents=True, exist_ok=True)
    
    # Find all STL files
    stl_files = list(input_path.glob(pattern))
    
    if not stl_files:
        print(f"❌ No files found matching pattern '{pattern}' in {input_dir}")
        sys.exit(1)
    
    print(f"Found {len(stl_files)} files to process")
    print("="*60)
    
    success_count = 0
    error_count = 0
    
    for i, stl_file in enumerate(stl_files, 1):
        print(f"\n[{i}/{len(stl_files)}] Processing: {stl_file.name}")
        print("-"*60)
        
        try:
            # Load mesh
            processor = MeshProcessor(str(stl_file))
            
            # Apply operations
            if repair:
                processor.repair()
            
            if smooth > 0:
                processor.smooth(iterations=smooth)
            
            if reduce:
                processor.reduce_faces(reduce)
            
            if scale:
                processor.scale_to_size(scale, axis)
            
            if center:
                processor.center_on_bed()
            
            # Export with same name
            output_file = output_path / stl_file.name
            processor.export(str(output_file))
            
            success_count += 1
            
        except Exception as e:
            print(f"❌ Error processing {stl_file.name}: {e}")
            error_count += 1
            continue
    
    print("\n" + "="*60)
    print(f"✅ Batch processing complete!")
    print(f"   Successful: {success_count}")
    print(f"   Errors: {error_count}")
    print(f"   Output directory: {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description='Batch process multiple STL files',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Repair all STL files in a directory
  python batch_process.py scans/ processed/ --repair
  
  # Full processing pipeline for all files
  python batch_process.py scans/ output/ --repair --scale 100 --center --smooth 3
  
  # Process specific pattern
  python batch_process.py scans/ output/ --repair --pattern "*_scan.stl"
        """
    )
    
    parser.add_argument('input_dir', help='Input directory containing STL files')
    parser.add_argument('output_dir', help='Output directory for processed files')
    parser.add_argument('-r', '--repair', action='store_true', help='Repair mesh issues')
    parser.add_argument('-s', '--scale', type=float, help='Scale to target size (mm)')
    parser.add_argument('--axis', default='z', choices=['x', 'y', 'z'], help='Axis for scaling')
    parser.add_argument('-c', '--center', action='store_true', help='Center on print bed')
    parser.add_argument('--smooth', type=int, default=0, help='Smooth mesh (iterations)')
    parser.add_argument('--reduce', type=int, help='Reduce to target face count')
    parser.add_argument('--pattern', default='*.stl', help='File pattern to match (default: *.stl)')
    
    args = parser.parse_args()
    
    batch_process(
        args.input_dir,
        args.output_dir,
        repair=args.repair,
        scale=args.scale,
        axis=args.axis,
        center=args.center,
        smooth=args.smooth,
        reduce=args.reduce,
        pattern=args.pattern
    )


if __name__ == '__main__':
    main()
