import os
import subprocess
import json
from dataclasses import dataclass, field
from typing import List, Dict, Any

@dataclass
class Config:
    CONFLUENCE_BASE_URL: str = "https://confluence.myCompany.com"
    INPUT_FOLDER: str = "in"
    OUTPUT_FOLDER: str = "out"
    ATTACHMENTS_PATH: str = "attachments"
    IMAGES_PATH: str = "images"
    STYLES_PATH: str = "styles"
    LOG_FOLDER_NAME: str = "logs"
    LOG_PATH_NAME: str = "html2mdConverter"
    YAML_HEADER: str = ""
    SPACE_DETAILS_SECTION: str = ""
    INVALID_VIDEO_INDICATOR: str = "Your browser does not support the HTML5 video element"
    RENAME_ALL_FILES: bool = False
    LOG_LINK_MAPPING: bool = False
    USE_UNDERSCORE_IN_FILENAMES: bool = False
    INSERT_YAML_HEADER: bool = False
    USE_WIKI_LINKS: bool = True
    USE_ESCAPING_FOR_WIKI_LINKS: bool = True
    SECTIONS_TO_REMOVE: List[str] = field(default_factory=list)
    THUMBNAILS_TO_REMOVE: List[str] = field(default_factory=list)
    THUMBNAIL_PATH: List[str] = field(default_factory=list)
    PREFIXES: List[str] = field(default_factory=list)
    PREFIXES_TO_REMOVE: List[str] = field(default_factory=list)

    # Derived properties
    LOG_FOLDER: str = None
    LOG_FILE_NAME: str = None
    LOG_FILE: str = None

    def __post_init__(self):
        # Set derived properties
        self.LOG_FOLDER = os.path.join(self.OUTPUT_FOLDER, self.LOG_FOLDER_NAME)
        self.LOG_FILE_NAME = f"{self.LOG_PATH_NAME}.log"
        self.LOG_FILE = os.path.join(self.LOG_FOLDER, self.LOG_FILE_NAME)

        # Set default lists if they're None
        if not self.SECTIONS_TO_REMOVE:
            self.SECTIONS_TO_REMOVE = [
                "# Zusatzinformation auf TC-Filesystem",
                "## Verwandte Artikel",
                "## Attachments",
                "## Space contributors",
                "## Recent space activity"
            ]

        if not self.THUMBNAILS_TO_REMOVE:
            self.THUMBNAILS_TO_REMOVE = [
                '![Home Page](images/icons/contenttypes/home_page_16.png)',
                '![](images/icons/contenttypes/home_page_16.png)',
                '![Bitte warten](images/icons/wait.gif)'
            ]

        if not self.THUMBNAIL_PATH:
            self.THUMBNAIL_PATH = [
                'resources/com.atlassian.confluence.plugins.confluence-view-file-macro:',
                'rest/documentConversion'
            ]
        
        if not self.PREFIXES:
            self.PREFIXES = [
                '/pages/viewpage.action?pageId=',
                '/display/',
                '/download/',
                '/'
            ]

        if not self.PREFIXES_TO_REMOVE:
            self.PREFIXES_TO_REMOVE = [
                '?createDialogSpaceKey=',
                '/pages/editblogpost.action?pageId=',
                '/labels/viewlabel.action?ids=',
                '/label/',
            ]
        
        # Create necessary directories
        os.makedirs(self.LOG_FOLDER, exist_ok=True)

def load_config_from_powershell() -> Dict[str, Any]:
    """Load configuration from config.ps1 using PowerShell"""
    try:
        # Run PowerShell to export config to JSON
        ps_command = """
        . ./config.ps1
        $Config | ConvertTo-Json -Depth 5
        """

        result = subprocess.run(
            ["powershell", "-Command", ps_command],
            capture_output=True,
            text=True,
            check=True
        )

        # Parse the JSON output
        config_data = json.loads(result.stdout)
        return config_data
    except Exception as e:
        print(f"Warning: Failed to load config from PowerShell: {str(e)}")
        return {}

def load_config(args) -> Config:
    """Load configuration from PowerShell and override with command line args"""
    # Create default config
    config = Config()

    # Try to load from PowerShell
    try:
        ps_config = load_config_from_powershell()

        # Update config from PowerShell values
        if ps_config:
            for ps_key, ps_value in ps_config.items():
                # Convert camelCase to UPPER_SNAKE_CASE
                config_attr = ''.join(['_' + c.upper() if c.isupper() else c.upper() for c in ps_key]).lstrip('_')

                # Check if this attribute exists in config
                if hasattr(config, config_attr):
                    setattr(config, config_attr, ps_value)
    except Exception as e:
        print(f"Warning: Error loading PowerShell config: {str(e)}")

    # Override with command line arguments if provided
    if args.input:
        config.INPUT_FOLDER = args.input
    if args.output:
        config.OUTPUT_FOLDER = args.output
    if args.base_url:
        config.CONFLUENCE_BASE_URL = args.base_url
    if args.rename_all:
        config.RENAME_ALL_FILES = args.rename_all
    if args.debug_link_mapping:
        config.LOG_LINK_MAPPING = args.debug_link_mapping
    if args.use_underscore:
        config.USE_UNDERSCORE_IN_FILENAMES = args.use_underscore

    # Re-initialize derived properties with the new values
    config.__post_init__()

    return config