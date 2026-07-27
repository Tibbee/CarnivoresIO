# Audio System Test Checklist

Manual verification for audio improvements from commits `cc7d431` and `b78f49d`.

**Setup for all tests:** Enable Debug Mode in addon preferences and open the System Console (`Window > Toggle System Console` on Windows). Use a `.car` file with at least 3 animations and 2 distinct sounds for full coverage.

---

## 1. Sound Resolution (cc7d431)

| # | Test | Expected |
|---|------|----------|
| 1.1 | Assign a sound via the panel `Sound` picker on an NLA track action. Scrub playback. | Sound plays. |
| 1.2 | Create an action manually: `action["carnivores_sound"] = "SoundName"` (no pointer property). Start playback. | Sound plays. |
| 1.3 | Rename the sound datablock after assigning a legacy string link. Scrub. | Console warns but does not crash. |
| 1.4 | Open the `Sound` picker and click X to clear it. Scrub. | No sound plays. No Python error in console. |

## 2. Export Conversion (cc7d431)

| # | Test | Expected |
|---|------|----------|
| 2.1 | Link a short sound (< 1s) and export. Re-import the exported file. | Sound imports with correct sample count. Console shows matching byte length. |
| 2.2 | Link a long sound (> 30s). Export, re-import. | Sound imports without truncation or blocklist error. |
| 2.3 | Link a stereo 48kHz WAV. Export. Re-import and play. | Sound is mono 22050Hz; round-trip preserves content. |
| 2.4 | Link an MP3/OGG file. Export. Re-import. | Sound exports correctly; no dtype warning in parse log. |
| 2.5 | Check exported `.car` parse log for `sfx_count` and each sound's byte length. | `sfx_count` equals the number of unique linked sounds. Declared byte length matches payload size. |

## 3. Parse Validation (cc7d431)

| # | Test | Expected |
|---|------|----------|
| 3.1 | Import a known-good `.car` with sounds. | No sound-related warnings in parse output. |
| 3.2 | Hex-edit a test `.car`: change one sound's length to an odd value. Import. | Parser warns about the odd length, repairs by subtracting 1, and continues. Remaining sounds parse correctly. |
| 3.3 | Truncate the last 200 bytes of a `.car` with sounds. Import. | Parser warns about truncated data. Prior sounds import without corruption. |
| 3.4 | Zero out `sfx_count` in a hex-edited `.car`. Import. | No sounds imported. No error or crash. |
| 3.5 | Set a cross-ref entry to an out-of-range index (e.g., 255). Import. | Parser clamps it to -1 and warns. |

## 4. Handler Lifecycle (cc7d431)

| # | Test | Expected |
|---|------|----------|
| 4.1 | Disable and re-enable the addon 3 times in Preferences. Check console. | No "handler already present" or duplicate-registration messages. Handler count in `frame_change_post` is stable. |
| 4.2 | Enable NLA Sound in the panel. Save the `.blend`. Close Blender. Reopen. | NLA Sound setting is preserved (enabled). |
| 4.3 | Disable NLA Sound. Save. Close. Reopen. | NLA Sound setting is preserved (disabled). |
| 4.4 | During playback, toggle NLA Sound off via the panel button. | All playing handles stop immediately. No audio residue. |
| 4.5 | Disable the addon while a sound is playing. | Addon unregisters cleanly. No background audio after disable. |

## 5. Temp File Lifecycle (cc7d431)

| # | Test | Expected |
|---|------|----------|
| 5.1 | Import a `.car` with sounds. Note the temp dir path in the console (`Created temp sound directory: ...`). | Dir appears in `%TEMP%` under a `carnivores_io_sounds_*` name. |
| 5.2 | File → New. | Console shows "Removed temp sound" and "Removed empty temp sound directory". Dir is gone from `%TEMP%`. |
| 5.3 | Import 3 different `.car` files in one session. Check `%TEMP%`. | Only one temp dir exists at a time (or none if previous were cleaned). |
| 5.4 | Import a `.car`, play sounds, then close Blender (not File → New). Restart Blender. | No orphan files in the extension temp dir path from the last session (optional: may have remnants from the crash case, which is expected). |
| 5.5 | Disable the addon. Check `%TEMP%`. | All `carnivores_io_sounds_*` directories are removed. |

## 6. Focused Playback Policy

| # | Test | Expected |
|---|------|----------|
| 6.1 | Create an NLA track with a linked sound and start normal playback without entering Tweak Mode. | No managed linked audio plays. |
| 6.2 | Enter NLA Tweak Mode for that strip and start playback. | Only the focused animation's linked sound plays. |
| 6.3 | Exit Tweak Mode while its sound is active. | The managed sound stops and no unrelated strip starts playing. |
| 6.4 | Use the extension preview on several tracks that share the same sound. | Each explicitly selected preview starts the sound for that animation. |
| 6.5 | Mute unrelated tracks while previewing or using Tweak Mode. | No unrelated track sound is selected. |
| 6.6 | Delete an object while its linked sound is playing. | Its managed handle stops and no `ReferenceError` appears in the console. |

## 7. Authored Audio Timing

| # | Test | Expected |
|---|------|----------|
| 7.1 | Start focused playback in the middle of an unscaled sub-range strip. | Audio starts at the offset corresponding to the focused action range. |
| 7.2 | Switch the explicitly focused preview from action A to action B. | Action A's sound stops and action B's sound starts once. |
| 7.3 | Scale an NLA strip or change animation KPS. | Audio remains at its authored speed; no pitch or time stretching is applied. |
| 7.4 | In focused playback, set NLA strip repeat greater than one. | The authored clip restarts once at each detected visual repeat boundary without pitch or time stretching. |
| 7.5 | Use a scaled or fractional repeated strip. | Playback remains stable and best-effort; no guarantee of sample-accurate synchronization is implied. |
| 7.6 | Reverse an NLA strip. | No guarantee of reversed or time-stretched audio is implied; behavior follows the documented focused-preview limitation. |

## 8. Completed-Sound Guard (b78f49d)

| # | Test | Expected |
|---|------|----------|
| 8.1 | Link a very short sound (< 1s) to a long animation strip. Play from the start. | Sound plays once. After it finishes, the console does **not** flood with re-trigger messages. |
| 8.2 | While the short sound from 8.1 has finished, jump the timeline backward to the strip start. | Sound re-triggers (frame regression triggers a new cycle). |
| 8.3 | Use a normal-length sound. Let the strip finish and transition to a different strip, then jump back to the first strip. | Sound re-triggers on the jump-back. |

## 9. Preview Mode (b78f49d)

| # | Test | Expected |
|---|------|----------|
| 9.1 | Click the Play button on a track in the Carnivores Animation panel. | Preview starts, mutes other tracks, and plays only that strip in a loop with its linked sound. |
| 9.2 | Let the preview loop 3+ times. | Audio rewinds cleanly on each loop. No glitch or dropped sound. |
| 9.3 | During preview of Track A, click Play on Track B. | Preview switches to Track B. Track A's audio stops, Track B's starts. |
| 9.4 | During preview, click the Play button again (Pause icon). | Preview stops. All tracks restored to their original mute states. No audio plays. |
| 9.5 | During preview, disable NLA Sound in the panel. | Preview audio stops. Preview continues with no sound. |

## 10. Round Trip (both commits)

| # | Test | Expected |
|---|------|----------|
| 10.1 | Import a `.car` with 3+ animations and 2+ sounds. Verify sounds play in the panel. | All sounds play. |
| 10.2 | Export back to `.car`. | Export completes without error. |
| 10.3 | Re-import the exported file. | Sound count matches the original. Animation count matches. |
| 10.4 | Check cross-reference table in the parse log. | Mapping matches original: each animation's linked sound index is correct. |
| 10.5 | Play each re-imported animation's sound. | All sounds play. No silence. No corruption. |
| 10.6 | Export the re-imported file again. Re-import. | Sound byte lengths are identical across both export rounds. |

## Result Log

| Date | Tester | Blender Version | Tests Passed | Notes |
|------|--------|----------------|-------------|-------|
|      |        |                | /43         |       |
