# Confluence HTML to Markdown Converter

A specialized Python tool for converting Confluence HTML exports to Markdown format, with intelligent handling of attachments, images, and cross-links.

## Features

* Converts Confluence HTML exports to clean Markdown
* Preserves attachment and image directories
* Maintains cross-links between documents
* Handles Confluence-specific URL patterns
* Supports numeric page ID cleanup
* Skips unnecessary style directories
* Provides detailed logging
* Verifies internal and external links
* Supports batch processing of entire directory structures

## Prerequisites

* Python 3.8 or higher
* PowerShell (for Windows users)

## Quick Start

1. Clone this repository
2. Run `install.bat` to set up the Python environment
3. Place your Confluence HTML export files in the `in` folder
4. Run `run.bat` to start the conversion
5. Find converted files in the `out` folder

## Installation

Double-click `install.bat`:
```powershell
.\install.bat
```

The installer will:
* Create a Python virtual environment
* Install required dependencies (html2text,requests,bs4)
* Set up input/output directories

## Usage

1. Export your Confluence space/pages as HTML
2. Copy the exported files to the `in` directory 
  - Can be in root or subdirectory
  - Use one dir for each space (unzipped confluence export)
3. Change Configuration in `.\convert.ps1` (Line 2-4)
4. Run the converter:
   * Double-click `run.bat`
   * OR: Execute with PowerShell: `.\convert.ps1`

### Command Line Options

```bash
python converter.py [options]

Options:
  --input FOLDER     Input folder name (default: "in")
  --output FOLDER    Output folder name (default: "out")
  --base-url URL     Confluence base URL
  --rename-all       Enable renaming of all files with numeric suffixes
```

### Configuration

Edit `convert.ps1` to customize:
```powershell
$inputFolder = "path/to/input"
$outputFolder = "path/to/output"
$CONFLUENCE_BASE_URL = "https://your-confluence-url"
$RENAME_ALL = $true  # Set to false to keep original filenames
```

## Directory Structure

```
project/
├── in/                    # Place HTML exports here
│   └── ABC/               # Confluence space
│       ├── attachments/   # Attachment files
│       └── images/        # Image files
├── out/                   # Converted markdown files
│   ├── logs/              # Conversion logs
│   └── ABC/               # Confluence space (exported)
│       ├── attachments/   # Attachment files (exported)
│       └── images/        # Image files (exported)
├── venv/                  # Python virtual environment
├── install.bat            # Installation script
├── run.bat                # Execution script
├── convert.ps1            # PowerShell configuration
└── converter.py           # Main conversion script
```

## Features in Detail

### File Handling
* Preserves directory structure from input to output
* Maintains attachments and images in their original locations
* Skips unnecessary style directories
* Optional cleanup of numeric suffixes in filenames

### Link Processing
* Converts Confluence page links to relative Markdown links
* Verifies and maintains cross-references between documents
* Handles both internal and external links
* Supports various Confluence URL patterns

### Image Handling
* Preserves image references and descriptions
* Maintains relative paths to images
* Verifies image file existence
* Supports both local and web-hosted images

### Logging and Progress
* Detailed logging in `out/logs/html2md.log`
* Real-time conversion progress display
* Summary statistics after completion
* Error reporting and handling

## Common Issues

1. **Missing Images**: Ensure all images are included in the HTML export
2. **Broken Links**: Check if referenced pages are included in the conversion
3. **Style Directory**: The `styles` directory is intentionally skipped
4. **File Renaming**: Use `--rename-all` to clean up numeric suffixes

## Error Handling

* Check `out/logs/html2md.log` for detailed error messages
* Common errors include:
  * Missing input files
  * Invalid file permissions
  * Broken links or references
  * Missing attachments

## Known Issues

### Confluence Export Limitations

Some Confluence elements are not included in the HTML export by design, which affects the conversion process:

* **Missing Elements**: The following items are not part of the HTML export:
  * Blog entries
  * Labels and label views
  * User profile pages

* **Impact on Conversion**:
  * Missing elements result in empty Markdown files
  * Some files may be renamed to 'viewlabel.md'
  * Links to non-exported content may redirect to pages with similar names
  * Certain dynamic content cannot be preserved

These limitations are inherent to Confluence's export functionality and cannot be addressed by the conversion script. Users should be aware that manual cleanup might be needed for these cases.

### Workarounds

* Review 'viewlabel.md' files after conversion
* Check for empty pages in the output
* Consider manually exporting blog posts separately
* Document missing content in a separate index file