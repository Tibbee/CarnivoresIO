import bpy
import os
import shutil
from ..utils.logger import info, warn

def deploy_presets():
    # Source path in the addon
    source_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "presets", "operator")
    
    # Target path in Blender's user configuration
    # Example: C:\Users\Name\AppData\Roaming\Blender Foundation\Blender\4.2\scripts\presets\operator
    target_dir = os.path.join(bpy.utils.user_resource('SCRIPTS'), "presets", "operator")
    
    if not os.path.exists(source_dir):
        warn(f"Presets source directory not found: {source_dir}")
        return
        
    for op_id in os.listdir(source_dir):
        op_source = os.path.join(source_dir, op_id)
        if os.path.isdir(op_source):
            op_target = os.path.join(target_dir, op_id)
            
            # Create target folder if it doesn't exist
            if not os.path.exists(op_target):
                os.makedirs(op_target)
                info(f"Created preset directory: {op_target}")
                
            # Copy all .py preset files
            for preset_file in os.listdir(op_source):
                if preset_file.endswith(".py"):
                    src_file = os.path.join(op_source, preset_file)
                    dst_file = os.path.join(op_target, preset_file)
                    
                    # Only copy if it doesn't already exist to avoid overwriting user changes
                    if not os.path.exists(dst_file):
                        shutil.copy2(src_file, dst_file)
                        info(f"Deployed preset: {preset_file} to {op_target}")
                    else:
                        debug(f"Preset already exists, skipping: {preset_file}")
