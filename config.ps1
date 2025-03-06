# Confluence HTML to Markdown Converter Configuration
# Edit this file to customize the conversion process

# Basic Settings
[hashtable]$Config = @{
    # Input/Output Folders
    InputFolder = "in"                                  # Input folder containing HTML files
    OutputFolder = "out"                                # Output folder for Markdown files
    
    # Confluence Settings
    ConfluenceBaseUrl = "https://confluence.company.com"  # Your company's Confluence URL
    
    # Conversion Options
    RenameAllFiles = $true                              # Rename numeric files to their header names
    UseUnderscoreInFilenames = $false                   # Replace spaces with underscores in filenames
    UseWikiLinks = $True                                # Replace []() Markdown Links with [[]] Wikilink format
    UseEscapingForWikiLinks = $True                     # Add Escape char in links when using Wikilinks. (Prevents broken tables, as Links and Tables both use "|".)

    # Cleanup Options (SHOULD be adapted to your Confluence style)
    ## Sections to remove
    SectionsToRemove = @(
        #"#  Space Details", # Do not allow this! will produce errors!
        "# Zusatzinformation auf TC-Filesystem",
        "## Verwandte Artikel",
        "## Attachments",
        "## Space contributors",
        "## Recent space activity"
    )
    ### Do not use "#  Space Details" Section in SectionsToRemove

    SpaceDetailsSection = "#  Space Details"
    
    InvalidVideoIndicator = "Your browser does not support the HTML5 video element"

    ## Thumbnail paths to remove (SHOULD be adapted to your Confluence style)
    ThumbnailsToRemove = @(
        '![Home Page](images/icons/contenttypes/home_page_16.png)',
        '![](images/icons/contenttypes/home_page_16.png)',
        '![Bitte warten](images/icons/wait.gif)'
    )
    
    ## Thumbnail paths to identify (MIGHT need to be adapted to your Confluence style)
    ThumbnailPath = @(
        'resources/com.atlassian.confluence.plugins.confluence-view-file-macro:',
        'rest/documentConversion'
    )

    # Folder Names (usually don't need to be changed)
    AttachmentsPath = "attachments"                     # Attachments folder name
    ImagesPath = "images"                               # Images folder name
    StylesPath = "styles"                               # Styles folder name
    LogFolder = "logs"                                  # Log folder name
    
    # Advanced Options
    LogLinkMapping = $false                             # Log all link mappings for debugging

    # YAML Header Config
    YamlHeader = @"
---
alias:
  - ""
  - ""
author: username
dateCreated: 2020-01-31
up:
  - "[[Knowledge Base]]"
---
"@
}