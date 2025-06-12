# Confluence HTML to Markdown Converter Configuration
# Edit this file to customize the conversion process
# >>> check 'Custom Options'

# Converter Settings
[hashtable]$Config = @{
    ###########################################################################
    # These Settings usually need no change >>> check 'Custom Options' instead #
    ###########################################################################

    # Basic Settings
    #---------------
    

    ## Input/Output Folders
    InputFolder = "input"                               # Input folder containing HTML files
	  InputFolderXml = "input-xml"                        # Input folder containing XML files
    OutputFolder = "output"                             # Output folder for Markdown files
    LogPathName = "html2mdConverter"                    # Default name for log file
    XmlFile = "entities.xml"                            # Default filename for Confluence-XML is entities.xml

    ## Conversion Options
    RenameAllFiles = $true                              # Rename numeric files to their header names
    UseUnderscoreInFilenames = $false                   # Replace spaces with underscores in filenames
    UseWikiLinks = $True                                # Replace []() Markdown Links with [[]] Wikilink format (consider enabling UseEscapingForWikiLinks too)
    UseEscapingForWikiLinks = $False                    # Add Escape char in links when using Wikilinks. (Prevents broken tables, as Links and Tables both use "|".)
    UnderscoreHomepageTitles = $True                    # Prepend an underscore "_" in front of index file, to always sort it as first item alphabetically
    RemoveAllTagsFromIndex = $True                      # Removes all lines that contain tags in the index/homepage file (does not apply to other pages)

    ## Folder Names (default names don't need to be changed usually)
    AttachmentsPath = "attachments"                     # Attachments folder name
    ImagesPath = "images"                               # Images folder name
    StylesPath = "styles"                               # Styles folder name
    BlogpostPath = "blogposts"                          # Blogposts folder name
    LogFolder = "logs"                                  # Log folder name
    SpaceDetailsSection = "#  Space Details"

    ## Debug Options
    LogLinkMapping = $false                             # Log all link mappings for debugging

    ###########################################################################
    # (Everything below SHOULD be adapted to your Confluence style)           #
    ###########################################################################

    # Custom Options 
    #---------------
   
    ## Confluence Base URL
    ConfluenceBaseUrl = "https://confluence.company.com"  # Your company's Confluence URL

    ## Sections to remove
    SectionsToRemove = @(
        #"#  Space Details", # Do not uncomment this line! will produce errors!
        "# Zusatzinformation auf Filesystem",
        "## Verwandte Artikel",
        "## Attachments",
        "## Space contributors",
        "## Recent space activity",
        "## Kürzlich aktualisierte Artikel",
        "## Nach Thema durchsuchen"
    )
    ### Do not include "#  Space Details" Section in SectionsToRemove

    ## Lines to remove
    LinesToRemove = @(
        "Merken",
        "Save",
        "Suche",
        "* Seite:",
        "Seiten",
        "Siehe mehr"
    )
    
    ## Fileserver Config
    FileserverReplacementEnabled = $true  # Prepends double backslashes to server UNC paths if this is set to true
    FileserverIndicator = "MY-FILESERVER-NAME"

    ## Video Config
    InvalidVideoIndicator = "Your browser does not support the HTML5 video element"
    
    ## Blogpost Config
    BlogpostLinkReplacementEnabled = $true  # BlogpostLinkIndicator will be replaced by BlogpostLinkReplacement if this is set to true
    BlogpostLinkIndicator = "Bearbeiten"  # Replace this
    BlogpostLinkReplacement = " (Blogpost)"  # By this

    ## Thumbnail paths to remove (SHOULD be adapted to your Confluence style)
    ThumbnailsToRemove = @(
        '![Home Page](images/icons/contenttypes/home_page_16.png)',
        '![](images/icons/contenttypes/home_page_16.png)',
        '![Bitte warten](images/icons/wait.gif)',
        '![[images/icons/wait.gif|Bitte warten]]'
    )
    
    ## Thumbnail paths to identify (MIGHT need to be adapted to your Confluence style)
    ThumbnailPath = @(
        'resources/com.atlassian.confluence.plugins.confluence-view-file-macro:',
        'rest/documentConversion'
    )

    ## YAML Header Config
    YamlHeader = @"
---
alias:
  - ""
  - ""
author: [username]
dateCreated: [date_created]
up:
  - "[[up_field]]"
tags:
  - ""
---
"@

    YamlHeaderBlog = @"
---
alias:
  - ""
  - ""
author: [username]
dateCreated: [date_created]
type: blogpost
up:
  - "[[up_field]]"
tags:
  - ""
---
"@

    ###########################################################################
    # End of Config File                                                      #
    ###########################################################################
}