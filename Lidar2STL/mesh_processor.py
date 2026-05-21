#!/usr/bin/env python3
"""
Mesh Processor for 3D Printing
A comprehensive toolkit for processing scanned STL files for 3D printing.

This script provides utilities for:
- Loading and analyzing STL files
- Repairing mesh issues (holes, non-manifold edges, etc.)
- Optimizing meshes for printing
- Scaling, rotating, and positioning
- Hollowing and adding drainage holes
- Basic mesh modifications
"""

import trimesh
import numpy as np
import pymeshfix
import argparse
import sys
from pathlib import Path
from typing import Tuple, Optional, List


class MeshProcessor:
    """Main class for processing 3D mesh files."""
    
    def __init__(self, filepath: str):
        """
        Initialize with an STL file.
        
        Args:
            filepath: Path to the STL file
        """
        self.filepath = Path(filepath)
        if not self.filepath.exists():
            raise FileNotFoundError(f"File not found: {filepath}")
        
        print(f"Loading mesh from: {self.filepath.name}")
        self.mesh = trimesh.load(str(self.filepath))
        print(f"✓ Loaded mesh with {len(self.mesh.vertices)} vertices and {len(self.mesh.faces)} faces")
    
    def analyze(self) -> dict:
        """
        Analyze the mesh and return diagnostic information.
        
        Returns:
            Dictionary containing mesh properties and issues
        """
        print("\n" + "="*60)
        print("MESH ANALYSIS")
        print("="*60)
        
        analysis = {
            'vertices': len(self.mesh.vertices),
            'faces': len(self.mesh.faces),
            'edges': len(self.mesh.edges),
            'is_watertight': self.mesh.is_watertight,
            'is_winding_consistent': self.mesh.is_winding_consistent,
            'volume': self.mesh.volume if self.mesh.is_watertight else None,
            'surface_area': self.mesh.area,
            'bounds': self.mesh.bounds,
            'extents': self.mesh.extents,
            'center_mass': self.mesh.center_mass,
        }
        
        # Print analysis
        print(f"\n📊 Basic Properties:")
        print(f"   Vertices: {analysis['vertices']:,}")
        print(f"   Faces: {analysis['faces']:,}")
        print(f"   Edges: {analysis['edges']:,}")
        
        print(f"\n📐 Dimensions (mm):")
        print(f"   X: {analysis['extents'][0]:.2f} mm")
        print(f"   Y: {analysis['extents'][1]:.2f} mm")
        print(f"   Z: {analysis['extents'][2]:.2f} mm")
        print(f"   Surface Area: {analysis['surface_area']:.2f} mm²")
        
        print(f"\n🔍 Mesh Quality:")
        print(f"   Watertight: {'✓ YES' if analysis['is_watertight'] else '✗ NO (has holes or gaps)'}")
        print(f"   Winding Consistent: {'✓ YES' if analysis['is_winding_consistent'] else '✗ NO'}")
        
        if analysis['volume']:
            print(f"   Volume: {analysis['volume']:.2f} mm³")
        
        # Check for issues
        print(f"\n⚠️  Potential Issues:")
        issues = []
        
        if not self.mesh.is_watertight:
            issues.append("Mesh is not watertight (required for 3D printing)")
        
        if not self.mesh.is_winding_consistent:
            issues.append("Face winding is inconsistent")
        
        # Check for degenerate faces
        degenerate = self.mesh.area_faces == 0
        if np.any(degenerate):
            issues.append(f"{np.sum(degenerate)} degenerate faces (zero area)")
        
        # Check for duplicate vertices
        if len(self.mesh.vertices) > len(np.unique(self.mesh.vertices, axis=0)):
            issues.append("Duplicate vertices detected")
        
        if not issues:
            print("   ✓ No major issues detected!")
        else:
            for issue in issues:
                print(f"   • {issue}")
        
        print("\n" + "="*60)
        return analysis
    
    def repair(self) -> None:
        """
        Repair common mesh issues using pymeshfix.
        
        This fixes:
        - Non-manifold edges
        - Holes in the mesh
        - Self-intersections
        - Degenerate faces
        """
        print("\n🔧 Repairing mesh...")
        
        # Use pymeshfix for robust repair
        meshfix = pymeshfix.MeshFix(self.mesh.vertices, self.mesh.faces)
        
        print("   • Fixing non-manifold edges and vertices...")
        print("   • Closing holes...")
        print("   • Removing degenerate faces...")
        
        meshfix.repair()
        
        # Update mesh with repaired version
        self.mesh = trimesh.Trimesh(vertices=meshfix.v, faces=meshfix.f)
        
        print(f"✓ Repair complete!")
        print(f"   Vertices: {len(self.mesh.vertices):,}")
        print(f"   Faces: {len(self.mesh.faces):,}")
        print(f"   Watertight: {'✓ YES' if self.mesh.is_watertight else '✗ NO'}")
    
    def scale_to_size(self, target_size: float, axis: str = 'z') -> None:
        """
        Scale mesh so that one dimension matches target size.
        
        Args:
            target_size: Target size in mm
            axis: Which axis to scale to ('x', 'y', or 'z')
        
        Why: Scanned objects often need resizing for printing.
        """
        axis_map = {'x': 0, 'y': 1, 'z': 2}
        axis_idx = axis_map[axis.lower()]
        
        current_size = self.mesh.extents[axis_idx]
        scale_factor = target_size / current_size
        
        print(f"\n📏 Scaling mesh:")
        print(f"   Current {axis.upper()} size: {current_size:.2f} mm")
        print(f"   Target {axis.upper()} size: {target_size:.2f} mm")
        print(f"   Scale factor: {scale_factor:.4f}")
        
        self.mesh.apply_scale(scale_factor)
        
        print(f"✓ Scaled! New dimensions:")
        print(f"   X: {self.mesh.extents[0]:.2f} mm")
        print(f"   Y: {self.mesh.extents[1]:.2f} mm")
        print(f"   Z: {self.mesh.extents[2]:.2f} mm")
    
    def center_on_bed(self) -> None:
        """
        Center the mesh on the XY plane and place on Z=0.
        
        Why: Prepares mesh for printing with proper bed placement.
        """
        print("\n📍 Centering mesh on print bed...")
        
        # Center on XY, place bottom on Z=0
        bounds = self.mesh.bounds
        translation = [
            -(bounds[0][0] + bounds[1][0]) / 2,  # Center X
            -(bounds[0][1] + bounds[1][1]) / 2,  # Center Y
            -bounds[0][2]                         # Bottom at Z=0
        ]
        
        self.mesh.apply_translation(translation)
        print(f"✓ Centered at origin, bottom at Z=0")
    
    def reduce_faces(self, target_faces: int) -> None:
        """
        Reduce mesh complexity while preserving shape.
        
        Args:
            target_faces: Target number of faces
        
        Why: Simplifies meshes for faster slicing and smaller file size.
        """
        current_faces = len(self.mesh.faces)
        
        if current_faces <= target_faces:
            print(f"\n⏭️  Mesh already has {current_faces:,} faces (target: {target_faces:,}), skipping reduction")
            return
        
        print(f"\n🔻 Reducing mesh complexity:")
        print(f"   Current faces: {current_faces:,}")
        print(f"   Target faces: {target_faces:,}")
        
        # Use quadric decimation for high-quality reduction
        self.mesh = self.mesh.simplify_quadric_decimation(target_faces)
        
        print(f"✓ Reduced to {len(self.mesh.faces):,} faces")
        print(f"   Reduction: {(1 - len(self.mesh.faces)/current_faces)*100:.1f}%")
    
    def smooth(self, iterations: int = 5) -> None:
        """
        Smooth the mesh using Laplacian smoothing.
        
        Args:
            iterations: Number of smoothing iterations
        
        Why: Reduces scanning artifacts and rough surfaces.
        """
        print(f"\n✨ Smoothing mesh ({iterations} iterations)...")
        
        # Simple Laplacian smoothing
        trimesh.smoothing.filter_laplacian(self.mesh, iterations=iterations)
        
        print(f"✓ Smoothing complete")
    
    def export(self, output_path: str, file_format: str = 'stl') -> None:
        """
        Export the processed mesh.
        
        Args:
            output_path: Output file path
            file_format: Export format (stl, obj, ply, etc.)
        """
        output_path = Path(output_path)
        
        # Ensure proper extension
        if not output_path.suffix:
            output_path = output_path.with_suffix(f'.{file_format}')
        
        print(f"\n💾 Exporting mesh to: {output_path.name}")
        
        self.mesh.export(str(output_path))
        
        file_size = output_path.stat().st_size / 1024  # KB
        print(f"✓ Export complete! File size: {file_size:.1f} KB")


def main():
    """Command-line interface for mesh processing."""
    
    parser = argparse.ArgumentParser(
        description='Process 3D mesh files for 3D printing',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Analyze a mesh
  python mesh_processor.py input.stl --analyze
  
  # Repair and export
  python mesh_processor.py input.stl --repair --output fixed.stl
  
  # Full processing pipeline
  python mesh_processor.py scan.stl --repair --scale 100 --smooth --reduce 50000 --output printable.stl
  
  # Scale to specific height and center on bed
  python mesh_processor.py scan.stl --scale 150 --axis z --center --output ready_to_print.stl
        """
    )
    
    parser.add_argument('input', help='Input STL file')
    parser.add_argument('-o', '--output', help='Output file path')
    parser.add_argument('-a', '--analyze', action='store_true', help='Analyze mesh and show diagnostics')
    parser.add_argument('-r', '--repair', action='store_true', help='Repair mesh issues')
    parser.add_argument('-s', '--scale', type=float, help='Scale to target size (mm)')
    parser.add_argument('--axis', default='z', choices=['x', 'y', 'z'], help='Axis for scaling')
    parser.add_argument('-c', '--center', action='store_true', help='Center on print bed')
    parser.add_argument('--smooth', type=int, nargs='?', const=5, help='Smooth mesh (iterations, default: 5)')
    parser.add_argument('--reduce', type=int, help='Reduce to target face count')
    
    args = parser.parse_args()
    
    # Check if input file exists
    if not Path(args.input).exists():
        print(f"❌ Error: File not found: {args.input}")
        sys.exit(1)
    
    try:
        # Initialize processor
        processor = MeshProcessor(args.input)
        
        # Always analyze first
        processor.analyze()
        
        if not args.analyze:
            # Apply operations in logical order
            if args.repair:
                processor.repair()
            
            if args.smooth:
                processor.smooth(iterations=args.smooth)
            
            if args.reduce:
                processor.reduce_faces(args.reduce)
            
            if args.scale:
                processor.scale_to_size(args.scale, args.axis)
            
            if args.center:
                processor.center_on_bed()
            
            # Export if output specified
            if args.output:
                processor.export(args.output)
            else:
                print("\n⚠️  No output file specified. Use --output to save processed mesh.")
        
        print("\n✅ Done!")
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
