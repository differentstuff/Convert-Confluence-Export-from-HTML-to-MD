# Confluence HTML to Markdown Converter
A specialized Python tool for converting Confluence HTML exports to Markdown format, preserving attachments, links, and hierarchy.

*Perfect for people who like markdown and hate broken links*


## Features

### Core Conversion
- HTML to Markdown transformation with structure preservation
- Batch processing of directory trees
- XML metadata integration for enhanced relationships
- Numeric ID cleanup (`12345-page.md` → `page.md`)

### Intelligent Link Management
- Cross-document link resolution
- Confluence URL pattern translation
- Link validation and repair system
- Redirect handling for moved content

### File System Management
- Content-type mapping for attachments, including images, documents, and other file types
- File renaming with conflict resolution and existence verification
- Preserves file references and descriptions for local and web-hosted images
- CSV mapping reports for audit trails

### Operational Features
- Real-time conversion progress display
- Summary statistics after completion
- Granular logging system
- Dual configuration (CLI + PowerShell)


## Prerequisites
- Python 3.8 or higher
- PowerShell (for Windows users)


## Quick Start
1. Clone this repository
2. Run `install.bat` to set up the Python environment
3. Place your Confluence HTML export files in the `input` folder
4. Place your Confluence XML export files in the `input-xml` folder
5. Run `run.bat` to start the conversion
6. Find converted files in the `output` folder


## Installation
```powershell
# 1. Clone & install
git clone https://github.com/differentstuff/Convert-Confluence-Export-from-HTML-to-MD.git
cd Convert-Confluence-Export-from-HTML-to-MD
.\install.bat

# 2. Add your exports
📂 input/         ← HTML exports here
📂 input-xml/     ← XML exports here

# 3. Convert
.\run.bat

The installer will:
- Create a Python virtual environment
- Install required dependencies (html2text,requests,bs4)
- Set up input/output directories

## Usage
1. Export your Confluence space/pages as HTML and XML
2. Copy the exported files to the `input` and `input-xml` directory 
  - Can be in root or subdirectory
  - Use one dir for each space (unzipped confluence export)
3. Change Configuration in `.\config.ps1` (see 'Custom Options' in Line 45-130)
4. Run the converter:
   - Double-click `run.bat`
   - OR: Execute with PowerShell: `.\convert.ps1`

Depending on your input size the conversion will take several minutes (estimate 15 Min for 10k files (20GB of attachments)).

### Command Line Options

```bash
python converter.py [options]

Options:
  --input FOLDER     Input folder name (default: "input")
  --input-xml FOLDER Input folder name (default: "input-xml")
  --output FOLDER    Output folder name (default: "output")
  --base-url URL     Confluence base URL
  --rename-all       Enable renaming of all files with numeric suffixes
```

### Configuration

Edit `convert.ps1` to customize:
```powershell
$inputFolder = "path/to/input"
$outputFolder = "path/to/output"
$CONFLUENCE_BASE_URL = "https://your-confluence-url"
```


## Directory Structure

Place your export files in input and input-xml. 
Paste the whole folder after unzipping. Do not rename them.

```plaintext
Confluence_Converter_html_to_md/
├── 📁 input/                  # Raw HTML exports
│   └── 📁 ABC/                # Space directory (Place your HTML export here)
│       ├── 📁 attachments     # Native Confluence attachments
│       ├── 📁 images          # Embedded media files
│       └── 📁 styles          # Auto-generated CSS
├── 📁 input-xml/              # XML metadata exports (Place your XML export here)
│   └── 📁 Confluence-space-export-ABC-.../  # Dated XML export
│       ├── 📄 entities.xml    # Core metadata
│       └── 📁 attachments     # XML-linked attachments
├── 📁 output/                 # Processed content
│   └── 📁 ABC/                # Converted space (Created during conversion)
│       ├── 📁 attachments     # Normalized attachments
│       ├── 📁 blogposts       # Converted blog content
│       └── 📁 images          # Mapped media files
├── 📁 venv/                   # Python environment (Created by setup)
├── 📄 README.md               # This document
├── 📄 converter.py            # Main conversion logic
├── 📄 xmlprocessor.py         # XML metadata handler
├── 📄 attachmentprocessor.py  # Asset pipeline manager
├── 📄 linkchecker.py          # Link integrity system
├── 📄 conversionstats.py      # Metrics collector
├── 📄 config.py               # Program settings (Do not modify this)
├── 📄 config.ps1              # User configuration (You can modify here)
├── 📄 convert.ps1             # PowerShell entrypoint
├── 📄 install.bat             # Environment setup
└── 📄 run.bat                 # Conversion launcher
```

## Common Issues
- **Missing Attachments**: Ensure attachment folders are included
- **Broken Links**: Check if referenced pages are included in the conversion


## Error Handling

**Debugging Process**
  - Examine output/logs/html2mdConverter.log
  - Reproduce with --verbose flag
  - Check mapping report CSV


## Known Issues

### Confluence Export Limitations
Some elements are not included in the Confluence export by design, which affects the conversion process.

**Missing Elements**
The following items are not part of the HTML export:
  - Label management pages
  - User profile directories

**Impact on Conversion**:
  - Missing elements result in empty Markdown files
  - Some files may be renamed to 'viewlabel.md' if their source does not exist
  - Links to non-exported content may redirect to wrong or empty pages
  - Certain dynamic content cannot be preserved

These limitations are inherent to Confluence's export functionality and cannot be addressed by the conversion script. 
Users should be aware that manual cleanup might be needed for these cases.

A cleanup using a Linter is recommended after the conversion.

### Workarounds
- Review 'viewlabel.md' files after conversion
- Check for empty pages in the output
- Document missing content in a separate index file
