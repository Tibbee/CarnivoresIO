# 🦖 CarnivoresIO: The Ultimate Blender Toolkit for Carnivores Modding

**CarnivoresIO** is a professional-grade Blender extension (4.2+) designed to bridge the gap between modern 3D workflows and the legacy engine of the *Carnivores* game series. Whether you're a veteran modder or a newcomer, CarnivoresIO provides the most advanced, high-performance tools for creating, animating, and exporting models for the Hunt.

---

### :warning: Compatibility & Requirements
> [!IMPORTANT]
> :white_check_mark: **Verified Version:** Blender **4.x-5.x** (all versions from 4.2 to 5.0+).  
> :x: **Blender 3.x or earlier:** Older versions are not officially supported due to the new Extensions architecture.

---

### :package: Installation

#### **Option A: Standard Installation**
1. Download `carnivores_io-2.x.x.zip` from the [**Releases**](https://github.com/Tibbee/CarnivoresIO/releases) page.
2. In Blender, go to **Edit > Preferences > Get Extensions**.
3. **Drag and drop** the `.zip` file into the Blender window, or use the menu to install from disk.

#### **Option B: Dev Channel (Full Auto-Updates)**
*This method ensures the addon updates itself without you having to lift a finger.*

1. Navigate to **Preferences > File Paths > Extensions**.
2. Click the **[ + ]** icon to add a new **Remote Repository**.
3. Name it "Carnivores Dev" and paste this URL:  
   `https://raw.githubusercontent.com/Tibbee/CarnivoresIO/refs/heads/main/public_repo/index.json`

> [!TIP]
> **Pro-Tip:** Under the **Extensions** tab in Preferences, ensure **"Check for updates on startup"** is enabled. This allows the addon to sync with the Dev Channel automatically every time you launch Blender!

---

### :sparkles: Key Features

**🚀 High-Performance I/O**
*   **Format Support:** Full support for `.3df` (Static), `.car` (Animated), and `.3dn` (Mobile/HD).
*   **Batch Export:** Export entire environment packs at once using the **Multi-Export** feature.
*   **Auto-Materials:** One-click setup for textures and custom world shaders.
*   **Handedness Handling:** Automatically fix mirroring with the **Flip Handedness** toggle to match the game engine's left-handed system.

**🦴 Pro Rigging & Animation**
*   **MST Reconstruction:** Advanced algorithm that infers a functional bone hierarchy from raw vertex groups—no manual rigging required!
*   **Weight Smoothing:** Laplacian smoothing for organic-looking joints and professional deformations.
*   **Fast Actions:** Automatically converts vertex animations into modern Shape Key Actions and NLA strips.
*   **KPS Control:** Manage game-specific Keys Per Second directly in Blender.

**🔊 Real-Time NLA Sound System**
*   **Synchronized Audio:** Link `.wav` files to animations. Hear your dinosaur's roar in real-time as you scrub the timeline or play animations in Blender.
*   **Solo Preview:** Rapidly iterate on timing with the **"Play Preview"** loop mode.
*   **Self-Healing:** Robust device management that automatically recovers from audio driver crashes.

**🚩 Face Flag Power Tools**
*   **Visualizer:** See your engine flags (Transparency, Double-Sided, etc.) directly in the viewport with automated color coding.
*   **Smart Selection:** Select or modify faces using complex bitmask logic (e.g., *"Select all transparent faces that are NOT mortal"*).
*   **Bulk Modification:** Apply, clear, or toggle flags across hundreds of faces simultaneously.

**🛡️ Professional Stability & Validation**
*   **Engine Safeguards:** Built-in checks for vertex/face limits (1024/2048) and UV range validation.
*   **Self-Healing:** Automatically repairs common legacy file errors during import.

---

### :tools: Usage

**Where to find the tools:**
*   **Import/Export:** `File > Import/Export > Carnivores Engine (.3df, .car, .3dn)`.
*   **Toolbox:** Press **[ N ]** in the 3D Viewport and look for the **"Carnivores"** tab.

**Core Workflow:**
1.  **Import:** Load your `.car` or `.3df`. Use **Flip Handedness** to match the game's orientation.
2.  **Rigging:** For `.car` models, click **"Reconstruct Rig"** in the sidebar to instantly generate a poseable skeleton.
3.  **Animation:** Use the **Animation Panel** to link `.wav` files to actions and sync your **KPS**.
4.  **Flags:** Use the **3DF Face Flags** panel and click **"Visualize Flags"** to see properties directly on the mesh.

---

### :beetle: Debugging
- If you encounter issues, go to `Preferences > Extensions > CarnivoresIO` and enable **Debug Mode**.
- Open the System Console (`Window > Toggle System Console` on Windows) to view detailed logs and NumPy-level execution timings.

---

### :balance_scale: License
Licensed under **GNU GPL v3**.  
Copyright © 2024-2025 Tibor Harsányi.
