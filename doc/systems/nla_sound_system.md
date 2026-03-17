# NLA Sound System Implementation Details

## Overview

This document details the implementation of a robust Non-Linear Animation (NLA) sound playback system for the CarnivoresIO addon. The primary goal was to synchronize sound playback with corresponding animation strips from imported `.car` files.

This was achieved by solving two core challenges:
1.  **Reliable Playback Detection:** Ensuring that sound only plays during active animation playback (e.g., when the user presses the spacebar), and not when the user is simply scrubbing the timeline.
2.  **Clean File Management:** Handling temporary sound files that are necessarily created during the import process, ensuring they don't permanently clutter the user's project folder.

---

## Part 1: Achieving Reliable Playback-Only Sound

### The Challenge
The initial implementation attempts were plagued by an issue where sounds would play while the user was just dragging the playhead (scrubbing) through the timeline. Our investigation, aided by diagnostic logging, revealed that the standard Blender property `bpy.context.screen.is_animation_playing` was unreliable for our purpose. It would return `True` simply when an NLA strip was in "Tweak Mode" and the playhead was over it, even if no animation was actively playing.

### The Solution: A Custom Playback State Machine
To overcome this, we stopped relying on Blender's ambiguous property and implemented our own explicit state machine. This system uses a custom global flag, `_is_real_playback`, which is unambiguously controlled by Blender's dedicated playback start/stop events.

This approach involves three distinct handler functions:

**1. `playback_started_handler` (The "On" Switch)**
This function is registered to the `animation_playback_pre` event, which fires the instant animation playback begins. Its only job is to set our global flag to `True`.

*File: `operators.py`*
```python
_is_real_playback = False # Our reliable flag

def playback_started_handler(scene):
    """This handler is called by Blender right before animation playback starts."""
    global _is_real_playback
    _is_real_playback = True
    print("DEBUG: Playback STARTED. _is_real_playback = True")
```

**2. `playback_stopped_handler` (The "Off" Switch & Cleanup Crew)**
This function is registered to the `animation_playback_post` event, which fires the instant playback stops. It has two jobs: set our global flag back to `False`, and immediately stop any sounds that were currently playing.

*File: `operators.py`*
```python
def playback_stopped_handler(scene):
    """This handler is called by Blender right after animation playback stops."""
    global _is_real_playback, _playing_sounds
    _is_real_playback = False
    print("DEBUG: Playback STOPPED. _is_real_playback = False")
    
    # If there are any lingering sounds, stop them now.
    if _playing_sounds:
        print("DEBUG: Playback stopped, stopping all managed sounds.")
        for handle, _ in _playing_sounds.values():
            handle.stop()
        _playing_sounds.clear()
```

**3. `carnivores_nla_sound_handler` (The Playback Logic)**
This is the main handler that runs on every frame change (`frame_change_post`). It has been simplified to do nothing unless our reliable `_is_real_playback` flag is `True`. This single check effectively prevents it from ever running during timeline scrubbing.

*File: `operators.py`*
```python
def carnivores_nla_sound_handler(scene):
    global _playing_sounds, _is_real_playback
    
    # This handler should ONLY run when our flag indicates real playback is happening.
    if not _is_real_playback:
        return

    # ... (rest of the logic to find the active strip and play the sound)
```

### Registration
Finally, the addon's main `register` and `unregister` functions were updated to manage the lifecycle of all three handlers, ensuring the system is always active when the addon is enabled.

*File: `__init__.py`*
```python
def register():
    # ... (other registrations)
    # Ensure the handlers are always registered while the addon is enabled.
    if operators.carnivores_nla_sound_handler not in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.append(operators.carnivores_nla_sound_handler)
    if operators.playback_started_handler not in bpy.app.handlers.animation_playback_pre:
        bpy.app.handlers.animation_playback_pre.append(operators.playback_started_handler)
    if operators.playback_stopped_handler not in bpy.app.handlers.animation_playback_post:
        bpy.app.handlers.animation_playback_post.append(operators.playback_stopped_handler)

def unregister():
    # ... (other unregistrations)
    if operators.carnivores_nla_sound_handler in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(operators.carnivores_nla_sound_handler)
    if operators.playback_started_handler in bpy.app.handlers.animation_playback_pre:
        bpy.app.handlers.animation_playback_pre.remove(operators.playback_started_handler)
    if operators.playback_stopped_handler in bpy.app.handlers.animation_playback_post:
        bpy.app.handlers.animation_playback_post.remove(operators.playback_stopped_handler)
    # ...
```

---

## Part 2: Managing Temporary Sound Files

### The Challenge
We discovered that to make a newly imported sound playable by Blender's `aud` module, a specific `unpack`/`repack` cycle was required. However, the `unpack` operation would write a `.wav` file to a `sounds/` folder in the user's project directory, and this file would not be automatically deleted. Deleting it immediately after import would break playback, as the `aud` module paradoxically still required the file on disk, even if the sound was packed.

### The Solution: Deferred Cleanup on Unregister
The solution was to allow the temporary file to exist for as long as the addon is enabled, but to automatically clean it up when the addon is disabled or Blender is closed.

**1. Tracking Temporary Files**
A global `set` was created to keep track of the file paths of all temporary sounds created during an addon session. In the `import_car_sounds` function, after the file is unpacked, its absolute path is added to this set.

*File: `operators.py`*
```python
_temp_sound_files = set()
```
*File: `utils.py`*
```python
# ... inside import_car_sounds()
            if sound_block.packed_file:
                sound_block.unpack(method='USE_LOCAL')
                # Add the path of the created file to our set for later cleanup
                unpacked_filepath = bpy.path.abspath(sound_block.filepath)
                operators._temp_sound_files.add(unpacked_filepath)
                sound_block.pack()
# ...
```

**2. Implementing Cleanup**
The addon's main `unregister` function was modified to iterate through this set and delete each tracked file from the disk. This ensures that no temporary files are left behind.

*File: `__init__.py`*
```python
def unregister():
    # ...
    # Cleanup temporary sound files
    print("DEBUG: Cleaning up temporary sound files...")
    for filepath in operators._temp_sound_files:
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
                print(f"DEBUG: Removed temp sound file: {filepath}")
            except Exception as e:
                print(f"WARNING: Failed to remove temp sound file {filepath}: {e}")
    operators._temp_sound_files.clear()
    
    for cls in reversed(classes):
        # ...
```

## Final Result
The combination of these two solutions results in an NLA sound system that is both functionally correct and clean. It behaves as the user expects—playing sound only during active animation playback—and it diligently cleans up after itself, providing a seamless user experience.
