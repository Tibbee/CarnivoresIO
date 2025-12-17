import bpy

def get_debug_mode():
    """
    Retrieves the debug mode setting from addon preferences.
    """
    try:
        # Extract the base package name (e.g., 'CarnivoresIO')
        addon_name = __package__.split('.')[0]
        prefs = bpy.context.preferences.addons[addon_name].preferences
        return prefs.debug_mode
    except Exception:
        # Fallback to True if preferences are not yet available or registered
        return True

def log(message, level='INFO'):
    """
    Centralized logging function.
    """
    msg_str = str(message)
    
    # Auto-detect level from message prefix if not explicitly set to something else than INFO
    if level == 'INFO':
        if msg_str.startswith('[Debug]'):
            level = 'DEBUG'
            msg_str = msg_str[7:].strip()
        elif msg_str.startswith('[DEBUG]'):
            level = 'DEBUG'
            msg_str = msg_str[7:].strip()
        elif msg_str.startswith('[Warning]'):
            level = 'WARNING'
            msg_str = msg_str[9:].strip()
        elif msg_str.startswith('[Error]'):
            level = 'ERROR'
            msg_str = msg_str[7:].strip()
        elif msg_str.startswith('[Info]'):
            level = 'INFO'
            msg_str = msg_str[6:].strip()

    if level == 'DEBUG' and not get_debug_mode():
        return
        
    print(f"[CarnivoresIO:{level}] {msg_str}")

def debug(message):
    log(message, level='DEBUG')

def info(message):
    log(message, level='INFO')

def warn(message):
    log(message, level='WARNING')

def error(message):
    log(message, level='ERROR')
