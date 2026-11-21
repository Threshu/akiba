# Stockfish Engine Setup Instructions

## Required File

This folder should contain a file named **`stockfish.exe`**.

## Tested Version

The application has been tested with:
- `stockfish_15_x64_avx2.exe`
- `stockfish_20011801_x64.exe` (Stockfish 11, generic x64, 1.2 MB) - small engine
  bundled into the Windows build so result verification works out of the box.
  From `stockfish-11-win.zip` in the official "older versions" archive linked
  on https://stockfishchess.org/download/. Put its licence next to it as
  `Stockfish-COPYING.txt` (GPL v3, from `Copying.txt` in the same zip).

## Lookup order

`helpers.get_engine_path()` uses the first file that exists:
1. `engine/stockfish.exe` next to the built program (drop in a newer engine here)
2. the engine bundled inside the .exe
3. `engine/stockfish.exe` in the project folder (running from source)

## Setup Steps

1. Download Stockfish chess engine from the official website: https://stockfishchess.org/download/
2. Rename the downloaded file to **`stockfish.exe`**
3. Place the renamed file in this folder (`engine/`)

## Compatibility Notes

### Recommended Versions
- **Stockfish 15+** (64-bit AVX2 versions) - Fully tested and recommended
- **Stockfish 16/17** - Should work without issues
- Any modern Stockfish version supporting the UCI protocol should be compatible

### Potentially Incompatible Versions
- **32-bit versions** - May have performance limitations or compatibility issues on modern systems
- **Very old versions (pre-Stockfish 10)** - May lack features or have different UCI command syntax
- **Non-standard builds** - Custom or modified builds that deviate significantly from standard UCI protocol may not work correctly
- **macOS/Linux versions** - This application requires the Windows executable (`stockfish.exe`)

### Version Selection Tips
When downloading Stockfish, look for:
- **x64** or **64-bit** architecture
- **AVX2** support if your CPU supports it (better performance)
- Official releases from stockfishchess.org are recommended over third-party builds

## Verification

After placing `stockfish.exe` in this folder, the application should automatically detect and use the engine. If you encounter any issues:
1. Verify the file is named exactly `stockfish.exe` (case-insensitive on Windows)
2. Ensure the file is not blocked by Windows (right-click → Properties → Unblock if present)
3. Check that your Stockfish version supports the UCI protocol (all modern versions do)
