#!/usr/bin/env python3
"""
Mesh Inspection Tool
Helps identify problem areas like holes and thin regions in scanned models.
"""

import trimesh
import numpy as np
import sys
from pathlib import Path


def inspect_mesh(filepath: str):
    """Analyze mesh for common scan artifacts."""
    
    print("="*60)
    print("DETAILED MESH INSPECTION")
    print("="*60)
    
    mesh = trimesh.load(str(filepath))
    
    print(f"\nFile: {Path(filepath).name}")
    print(f"Vertices: {len(mesh.vertices):,}")
    print(f"Faces: {len(mesh.faces):,}")
    
    # Check edges
    edges = mesh.edges_unique
    edge_lengths = np.linalg.norm(
        mesh.vertices[edges[:, 0]] - mesh.vertices[edges[:, 1]], 
        axis=1
    )
    
    print(f"\n📏 Edge Statistics:")
    print(f"   Average edge length: {edge_lengths.mean():.3f} mm")
    print(f"   Min edge length: {edge_lengths.min():.3f} mm")
    print(f"   Max edge length: {edge_lengths.max():.3f} mm")
    
    # Find potentially problematic long edges (might indicate holes)
    threshold = edge_lengths.mean() + 3 * edge_lengths.std()
    long_edges = edges[edge_lengths > threshold]
    
    if len(long_edges) > 0:
        print(f"\n⚠️  Found {len(long_edges)} unusually long edges")
        print(f"   (edges > {threshold:.2f}mm)")
        print(f"   These might indicate holes or thin regions")
    
    # Check face areas
    face_areas = mesh.area_faces
    print(f"\n📐 Face Areas:")
    print(f"   Average: {face_areas.mean():.3f} mm²")
    print(f"   Max: {face_areas.max():.3f} mm²")
    
    large_faces = face_areas > (face_areas.mean() + 3 * face_areas.std())
    if np.any(large_faces):
        print(f"\n⚠️  Found {np.sum(large_faces)} unusually large faces")
        print(f"   These might be repair patches over missing scan data")
    
    # Check if watertight
    print(f"\n🔍 Topology:")
    print(f"   Watertight: {'✓ YES' if mesh.is_watertight else '✗ NO'}")
    print(f"   Components: {len(mesh.split())}")
    
    if len(mesh.split()) > 1:
        print(f"   ⚠️  Multiple separate pieces detected")
        components = mesh.split()
        for i, comp in enumerate(components, 1):
            print(f"      Component {i}: {len(comp.faces)} faces")
    
    # Identify boundary edges (edges with only one adjacent face)
    # These indicate holes even in "watertight" meshes
    face_adjacency = mesh.face_adjacency
    all_edges_set = set(map(tuple, map(sorted, mesh.edges)))
    boundary_edges_set = all_edges_set - set(map(tuple, map(sorted, mesh.face_adjacency_edges)))
    
    if len(boundary_edges_set) > 0:
        print(f"\n⚠️  BOUNDARIES DETECTED:")
        print(f"   {len(boundary_edges_set)} boundary edges found")
        print(f"   These are likely the holes you're seeing")
    
    print("\n" + "="*60)
    print("RECOMMENDATIONS:")
    print("="*60)
    
    if len(boundary_edges_set) > 0 or len(long_edges) > 0:
        print("\n🔧 For holes in the hat and missing sunglasses lenses:")
        print("\n   Option 1: Use MeshMixer (Recommended)")
        print("   1. Open file in MeshMixer")
        print("   2. Analysis → Inspector")
        print("   3. Click 'Auto Repair All' for structural holes")
        print("   4. Edit → Select for holes you want to fill manually")
        print("   5. Edit → Erase and Fill to patch specific areas")
        print("   6. For sunglasses lenses, use Edit → Plane Cut")
        print("      to create flat surfaces where lenses should be")
        
        print("\n   Option 2: Try aggressive repair here")
        print(f"   python3 mesh_processor.py {filepath} --repair -o {Path(filepath).stem}_heavily_repaired.stl")
        
        print("\n   Option 3: Re-scan with better coverage")
        print("   - Scan from multiple angles")
        print("   - Get closer to problem areas")
        print("   - Ensure good lighting")
    else:
        print("\n✓ Mesh looks structurally sound!")
        print("  Visual artifacts may be part of the original scan geometry")
    
    print("\n" + "="*60)


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python3 inspect_mesh.py <file.stl>")
        sys.exit(1)
    
    inspect_mesh(sys.argv[1])
