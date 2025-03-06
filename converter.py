import os
import sys
import logging
import shutil
import html2text
import re
import requests
from typing import Set, Tuple, List
import argparse
from dataclasses import dataclass
from bs4 import BeautifulSoup


# CONSTANTS REGEX (DO NOT CHANGE)
FILENAME_PATTERN = re.compile(r'^(.+)_(\d+)(\.md)$')
UNDERSCORE_DIGITS_PATTERN = re.compile(r'_\d+$')
LINK_PATTERN = re.compile(r'\[([^\]]+)\]\(<?([^>)]+)>?\)')
URL_PATTERN = re.compile(r'\[([^\]]+)\]\((https?://[^)]+)\)')
INVALID_CHARS = re.compile(r'[+/\\:*?@"<>|^\[\]]')

# Define comprehensive multilingual month mapping
MONTH_PATTERNS = {
    # English
    "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04", "May": "05",
    "Jun": "06", "Jul": "07", "Aug": "08", "Sep": "09", "Oct": "10",
    "Nov": "11", "Dec": "12",

    # German
    "Mai": "05", "Mär": "03", "Mrz": "03", "Okt": "10", "Dez": "12",

    # French
    "Janv": "01", "Févr": "02", "Fév": "02", "Mars": "03", "Avr": "04",
    "Juin": "06", "Juil": "07", "Août": "08", "Sept": "09", "Déc": "12",

    # Spanish
    "Ene": "01", "Abr": "04", "Ago": "08", "Dic": "12",

    # Italian
    "Gen": "01", "Mag": "05", "Giu": "06", "Lug": "07", "Ago": "08",
    "Set": "09", "Ott": "10", "Dic": "12",

    # Dutch
    "Mei": "05", "Mrt": "03", "Okt": "10"
}

@dataclass
class Config:
    CONFLUENCE_BASE_URL: str
    INPUT_FOLDER: str
    OUTPUT_FOLDER: str
    ATTACHMENTS_PATH: str
    IMAGES_PATH: str
    STYLES_PATH: str
    LOG_FOLDER_NAME: str
    LOG_PATH_NAME: str
    YAML_HEADER: str
    SPACE_DETAILS_SECTION: str
    INVALID_VIDEO_INDICATOR: str
    RENAME_ALL_FILES: bool
    LOG_LINK_MAPPING: bool
    USE_UNDERSCORE_IN_FILENAMES: bool
    INSERT_YAML_HEADER: bool
    USE_WIKI_LINKS: bool
    USE_ESCAPING_FOR_WIKI_LINKS: bool
    SECTIONS_TO_REMOVE: List[str]
    THUMBNAILS_TO_REMOVE: List[str]
    THUMBNAIL_PATH: List[str]
    PREFIXES: List[str]
    PREFIXES_TO_REMOVE: List[str]

    # Derived properties
    LOG_FOLDER: str = None
    LOG_FILE_NAME: str = None
    LOG_FILE: str = None

    def __post_init__(self):
        # Set derived properties
        self.LOG_FOLDER = os.path.join(self.OUTPUT_FOLDER, self.LOG_FOLDER_NAME)
        self.LOG_FILE_NAME = f"{self.LOG_PATH_NAME}.log"
        self.LOG_FILE = os.path.join(self.LOG_FOLDER, self.LOG_FILE_NAME)

        # Create necessary directories
        os.makedirs(self.LOG_FOLDER, exist_ok=True)

class ConversionStats:
    def __init__(self):
        self.total = 0
        self.processed = 0
        self.success = 0
        self.failure = 0
        self.skipped = 0
        self.current_phase = ""
        # Track stats per phase
        self.phase_stats = {
            "Preprocessing": {"total": 0, "success": 0, "failure": 0, "skipped": 0},
            "Converting": {"total": 0, "success": 0, "failure": 0, "skipped": 0},
            "Fixing links": {"total": 0, "success": 0, "failure": 0, "skipped": 0}
        }

    def update_progress(self):
        """Update progress in terminal"""
        if self.current_phase:
            if self.current_phase == "Preprocessing":
                print(f"\rPhase completed - {self.current_phase}", end='', flush=True)
            else:
                # Include skipped files in the display
                total_processed = self.processed + self.skipped
                print(f"\r{total_processed}/{self.total} completed - {self.current_phase}", end='', flush=True)
        else:
            print(f"\r{self.processed}/{self.total} completed", end='', flush=True)

    def set_phase(self, phase: str):
        """Set current processing phase and reset counters"""
        self.total = 0
        self.processed = 0
        self.success = 0
        self.failure = 0
        self.skipped = 0
        self.current_phase = phase
        # Initialize phase stats if not already present
        if phase not in self.phase_stats:
            self.phase_stats[phase] = {"total": 0, "success": 0, "failure": 0, "skipped": 0}
        self.update_progress()

    def update_phase_stats(self):
        """Update stats for the current phase"""
        if self.current_phase:
            self.phase_stats[self.current_phase]["total"] = self.total
            self.phase_stats[self.current_phase]["success"] = self.success
            self.phase_stats[self.current_phase]["failure"] = self.failure
            self.phase_stats[self.current_phase]["skipped"] = self.skipped

    def skip_file(self, phase="Preprocessing"):
        """Track a skipped file"""
        # Ensure the phase exists in phase_stats
        if phase not in self.phase_stats:
            self.phase_stats[phase] = {"total": 0, "success": 0, "failure": 0, "skipped": 0}
        
        # If this is the first skipped file for this phase, set the total
        if self.phase_stats[phase]["total"] == 0:
            self.phase_stats[phase]["total"] = self.total
            
        # Increment the skipped count for this phase
        self.phase_stats[phase]["skipped"] += 1

        # If we're in the current phase, also update the instance variable
        if phase == self.current_phase:
            self.skipped += 1
        
    def print_final_report(self):
        """Print final statistics by phase"""
        print("- Conversion Summary -")

        # Print stats for each phase
        for phase, stats in self.phase_stats.items():
            if stats["total"] > 0 or stats["skipped"] > 0:  # Show phases with activity
                print(f"\n{phase}:")
                if stats["total"] > 0:
                    print(f"  Processed: {stats['total']}")
                    print(f"  Success: {stats['success']}")
                    print(f"  Failure: {stats['failure']}")
                    print(f"  Skipped: {stats['skipped']}")

        print(f"\nSee {config.LOG_FILE} for details.")

class LinkChecker:
    def __init__(self, config: Config):
        """Setup logging configuration"""
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        self.checked_urls: Set[str] = set()
        self.input_folder = config.INPUT_FOLDER
        self.output_folder = config.OUTPUT_FOLDER
        self.renamed_files = {}  # Cache for renamed files
        self.filename_mapping = {}  # Cache for renamed files reference
        self.basename_dir_mapping = {}  # Directory-aware mapping for basenames
        self.file_cache = {}     # Cache for file existence checks
        
    def _build_file_cache(self):
        """Build cache of existing files in input and output directories"""
        for root, _, files in os.walk(self.output_folder):
            for file in files:
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, self.output_folder)
                self.file_cache[rel_path] = full_path

    def extract_image_src(self, html_content: str) -> list:
        """Extract image sources and metadata from HTML content"""
        # Use BeautifulSoup for more reliable HTML parsing
        soup = BeautifulSoup(html_content, 'html.parser')
        images = []

        for img in soup.find_all('img'):
            src = img.get('src', '')
            if src:
                # Get the correct description from data-linked-resource-default-alias
                # or fallback to the last part of the src path
                description = (img.get('data-linked-resource-default-alias') or
                             src.split('/')[-1])
                images.append({
                    'src': src,
                    'description': description
                })
        return images

    def process_video_links(self, html_content: str, markdown_content: str) -> str:
        """Process video links in markdown content"""
        # Use BeautifulSoup for HTML parsing
        soup = BeautifulSoup(html_content, 'html.parser')
        videos = []

        # Find all video elements in the HTML
        for video in soup.find_all('video'):
            src = video.get('src', '')
            if src:
                # Extract filename from src path
                filename = src.split('/')[-1]
                videos.append({
                    'src': src,
                    'filename': filename,
                    'attachment_path': None  # Will be filled if we find a matching attachment
                })

        # Check attachments section for video files
        attachments_section = soup.find('h2', {'id': 'attachments'})
        if attachments_section:
            attachment_div = attachments_section.find_next('div', {'class': 'greybox'})
            if attachment_div:
                for link in attachment_div.find_all('a'):
                    href = link.get('href', '')
                    text = link.text.strip()

                    # Match videos by filename
                    for video in videos:
                        if video['filename'] == text:
                            video['attachment_path'] = href
                            logger.debug(f"Matched video {text} with attachment {href}")
                            break

        # Replace the placeholder text with proper wiki links
        for video in videos:
            if config.INVALID_VIDEO_INDICATOR in markdown_content:
                # Use attachment path if available, otherwise use source
                link_path = video.get('attachment_path') or self.make_relative_path(video['src'])
                wiki_link = self.convert_wikilink(video['filename'], link_path)

                # Replace just one occurrence of the indicator
                markdown_content = markdown_content.replace(config.INVALID_VIDEO_INDICATOR, wiki_link, 1)
                logger.debug(f"Replaced video indicator with link: {wiki_link}")

        return markdown_content

    def is_web_url(self, url: str) -> bool:
        """
        Check if the URL is a web URL, excluding internal Confluence URLs
        Returns False for internal Confluence URLs, True for other web URLs
        """
        if url.startswith(config.CONFLUENCE_BASE_URL):
            return False
        return url.startswith(('http://', 'https://'))

    def make_relative_path(self, path: str) -> str:
        """Convert path to relative format"""
        # Remove any leading slashes or directory references
        path = path.lstrip('/')
        # Remove any base URL parts if present
        if '://' in path:
            path = path.split('://', 1)[1]
            if '/' in path:
                path = path.split('/', 1)[1]
        return path
 
    def verify_local_image(self, src_path: str, current_file_path: str) -> Tuple[str, bool, str]:
        """Verify a local image path"""
        # Skip verification for special paths like thumbnails
        for thumbnail in config.THUMBNAIL_PATH:
            if thumbnail in src_path:
                return src_path, True, "Thumbnail path"
            
        rel_path = self.make_relative_path(src_path).replace('/', os.sep)

        # Get the current subfolder from the file being processed
        current_subfolder = os.path.relpath(os.path.dirname(current_file_path), self.output_folder)
        if current_subfolder != '.':
            rel_path = os.path.join(current_subfolder, rel_path)

        # Check output folder first (as files should be copied by now)
        output_path = os.path.normpath(os.path.join(self.output_folder, rel_path))
        logger.debug(f"Checking output path: {output_path}")
        if os.path.exists(output_path) and os.path.isfile(output_path):
            logger.debug(f"Image found in output folder: {output_path}")
            return rel_path.replace(os.sep, '/'), True, "Local image exists"

        # Fallback to input folder
        input_path = os.path.normpath(os.path.join(self.input_folder, rel_path))
        logger.debug(f"Checking input path: {input_path}")
        if os.path.exists(input_path) and os.path.isfile(input_path):
            logger.debug(f"Image found in input folder: {input_path}")
            return rel_path.replace(os.sep, '/'), True, "Local image exists"

        logger.warning(f"Image not found in either location: {rel_path}")
        return rel_path.replace(os.sep, '/'), False, "Image not found"
      
    def verify_web_url(self, url: str) -> Tuple[str, bool, str]:
        """Verify a web URL"""
        if url in self.checked_urls:
            return url, True, "Already checked"

        self.checked_urls.add(url)
        try:
            response = self.session.head(url, timeout=10, allow_redirects=True)
            if response.status_code == 405:  # Method not allowed, try GET
                response = self.session.get(url, timeout=10)

            is_valid = 200 <= response.status_code < 400
            status = f"Status: {response.status_code}"
            return url, is_valid, status
        except requests.exceptions.RequestException as e:
            return url, False, f"Error: {str(e)}"

    def process_content(self, html_content: str, markdown_content: str, current_file_path: str) -> Tuple[str, list]:
        """Process content and verify all links"""
        results = []

        # Extract and verify image sources from HTML
        image_sources = self.extract_image_src(html_content)
        for img in image_sources:
            src = img['src']
            description = img['description']

            if self.is_web_url(src):
                url, is_valid, status = self.verify_web_url(src)
            else:
                # Always use relative path for local images
                url, is_valid, status = self.verify_local_image(src, current_file_path)
                # Even if verification fails, keep the relative path
                url = self.make_relative_path(src)

            results.append((url, is_valid, status))

            # Create the correct markdown image link regardless of validity
            old_pattern = f'\\[.*?\\]\\(<{re.escape(src)}>\\)(?: \\[BROKEN IMAGE\\])?(?: \\(image/[^)]+\\))?'
            #old_pattern = rf'\[.*?\]\(<{re.escape(src)}>\)'

            new_link = self.convert_wikilink(description, url)
            markdown_content = re.sub(old_pattern, new_link, markdown_content)

            if not is_valid:
                logger.warning(f"Image verification failed but keeping link: {url} - {status}")

        # Process web URLs in markdown (unchanged)
        for match in re.finditer(URL_PATTERN, markdown_content):
            url = match.group(2)
            if url not in self.checked_urls:
                _, is_valid, status = self.verify_web_url(url)
                results.append((url, is_valid, status))

        return markdown_content, results

    def clean_filename(self, md_output: str) -> str:
        """
        Remove numeric suffixes from filename based on configuration:
        - If RENAME_ALL_FILES is True: Remove all numeric suffixes, renames numeric filenames to their first header.
        - If False: Only remove when corresponding attachment folder exists

        Example: 'CNC_8355908.md' -> 'CNC.md' (only if '8355908' exists as attachment subfolder)
        Example: '12345.md' -> 'First Header Title.md' (if RENAME_ALL_FILES is True)

        Args:
            md_output: The target markdown content
        """
        logger.debug(f"Checking filename for cleanup: {md_output}")
        if md_output in self.renamed_files:
            return self.renamed_files[md_output]

        # Get the filename and directory path
        dir_path = os.path.dirname(md_output)
        filename = os.path.basename(md_output)

        # Regular expression to match filename_numbers.md pattern
        match = FILENAME_PATTERN.match(filename)

        # Process non-numeric filenames or if header extraction failed
        if not match:
            logger.debug(f"No numeric suffix found in filename: {filename}")
            return md_output
        
        # At this point, we have a valid match
        base_name, number, extension = match.groups()

        # Check if filename is purely numeric (excluding extension)
        if config.RENAME_ALL_FILES and base_name.isdigit():
            # For numeric filenames, we need to extract the first H1 header from the HTML file
            # This will be done in the convert_html_to_md function
            # For now, we'll just return the original path and handle it later
            return md_output

        logger.debug(f"base_name before sanitize_filename: {base_name}")
        # character replacement for "+" placeholders
        base_name = self.sanitize_filename(base_name)
        logger.debug(f"base_name after sanitize_filename: {base_name}")

        if config.RENAME_ALL_FILES:
            # Always rename files that match the pattern
            new_filename = f"{base_name}{extension}"
            new_path = os.path.join(dir_path, new_filename)
            logger.debug(f"Renaming {filename} to {new_filename}")
        else:
            # Check in output directory for attachment folder
            attachment_path = os.path.join(dir_path, config.ATTACHMENTS_PATH, number)
            if not (os.path.exists(attachment_path) and os.path.isdir(attachment_path)):
                logger.debug(f"No matching attachment folder found for number: {number}")
                return md_output
            new_filename = f"{base_name}{extension}"
            new_path = os.path.join(dir_path, new_filename)
            logger.debug(f"Found matching attachment folder. Renaming {filename} to {new_filename}")
            
        # If the file already exists, we need to handle it
        if os.path.exists(new_path):
            logger.warning(f"Target file {new_filename} already exists. Keeping original name.")
            return md_output

        try:
            self.renamed_files[md_output] = new_path
            logger.info(f"Successfully renamed {filename} to {new_filename}")
            return new_path
        except OSError as e:
            logger.error(f"Failed to rename file {filename}: {str(e)}")
            return md_output

    def fix_crosslinks(self, markdown_content: str, current_file_path: str) -> str:
        """
        Fix internal links in markdown content.
        Handles numeric suffixes and ensures consistent link formatting.
        """
        logger.debug(f"Fixing crosslinks in {current_file_path}")

        # Get the directory of the current file for context
        current_dir = os.path.dirname(os.path.relpath(current_file_path, self.output_folder))

        def process_link(match):
            description = match.group(1)
            link = match.group(2).strip('<>')
            original_link = link  # Store original for logging

            # Skip if it's a web URL or an attachment/image link
            if self.is_web_url(link) or config.ATTACHMENTS_PATH in link or config.IMAGES_PATH in link:
                return match.group(0)

            # Process internal links
            new_link = link

            # Remove common prefixes
            for prefix in config.PREFIXES:
                if new_link.startswith(prefix):
                    logger.debug(f"Link found for prefix {prefix} to remove: {new_link}")
                    new_link = new_link[len(prefix):]
                    # remove URL parameters (everything after '?')
                    if '?' in new_link:
                        new_link = new_link.split('?', 1)[0]
                        # remove URL parameters (everything after '&')
                    if '&' in new_link:
                        new_link = new_link.split('&', 1)[0]
                    logger.debug(f"Link changed to: {new_link}")
                    break  # Break only if a prefix match was found

            # Remove Link
            for prefix in config.PREFIXES_TO_REMOVE:
                base_url_prefix = config.CONFLUENCE_BASE_URL + prefix
                if new_link.startswith(prefix) or new_link.startswith(base_url_prefix):
                    logger.debug(f"Link found for prefix {prefix}, {base_url_prefix} to remove: {new_link}")
                    logger.debug(f"Link found to remove: {new_link}")
                    new_link = ""
                    break  # Break only if a prefix match was found

            # Returning empty link if removed
            if new_link == "":
                logger.debug(f"Modified Link: {new_link}")
                return new_link

            # Check if this is a link to index.md or index.html
            basename = os.path.basename(new_link)
            if basename in ['index.md', 'index.html']:
                # Get the directory part of the link
                link_dir = os.path.dirname(new_link)

                # If link_dir is empty, use the current directory
                if not link_dir:
                    link_dir = current_dir

                # Construct the full path to check in mappings
                full_path = os.path.join(link_dir, basename).replace('\\', '/')

                # Try to find the index file in the same directory
                if basename in self.basename_dir_mapping:
                    dir_mappings = self.basename_dir_mapping[basename]

                    # First try exact directory match
                    if link_dir in dir_mappings:
                        new_link = dir_mappings[link_dir]
                        # Extract just the filename if the link is in the same directory
                        if link_dir == current_dir or not link_dir:
                            new_link = os.path.basename(new_link)
                        logger.debug(f"Found directory-specific mapping for index: {link_dir}/{basename} -> {new_link}")
                        return self.convert_wikilink(description, new_link)

                    # If no exact match but we're in the same directory, try current directory
                    if current_dir in dir_mappings:
                        new_link = dir_mappings[current_dir]
                        # Extract just the filename if the link is in the same directory
                        new_link = os.path.basename(new_link)
                        logger.debug(f"Using current directory mapping for index: {current_dir}/{basename} -> {new_link}")
                        return self.convert_wikilink(description, new_link)
                    
                # Check if we have a mapping for this specific index file
                if full_path in self.filename_mapping:
                    logger.debug(f"Found match for full_path: {full_path}")
                    new_link = self.filename_mapping[full_path]
                    logger.debug(f"Replaced index link with directory context: {link} -> {new_link}")
                    return self.convert_wikilink(description, new_link)

            # Get base filename without extension
            base_name = os.path.splitext(os.path.basename(link))[0]

            # Try directory-aware mapping first for non-index files
            if base_name in self.basename_dir_mapping:
                dir_mappings = self.basename_dir_mapping[base_name]

                # First check if we have a mapping for the file in the current directory
                if current_dir in dir_mappings:
                    new_link = dir_mappings[current_dir]
                    # Extract just the filename if the link is in the same directory
                    new_link = os.path.basename(new_link)
                    logger.debug(f"Found directory-specific mapping: {current_dir}/{base_name} -> {new_link}")
                    return self.convert_wikilink(description, new_link)

            # Fall back to regular mapping if directory-specific mapping not found
            if new_link in self.filename_mapping:
                logger.debug(f"Found match for new_link: {new_link}")
                new_link = self.filename_mapping[new_link]
                # Check if the target is in the same directory
                target_dir = os.path.dirname(new_link)
                if target_dir == current_dir or not target_dir:
                    new_link = os.path.basename(new_link)
                logger.debug(f"Direct mapping found for: {original_link} -> {new_link}")
                return self.convert_wikilink(description, new_link)

            # Try with .md extension explicitly
            md_link = f"{base_name}.md"
            if md_link in self.filename_mapping:
                logger.debug(f"Found match for md_link: {md_link}")
                new_link = self.filename_mapping[md_link]
                # Check if the target is in the same directory
                target_dir = os.path.dirname(new_link)
                if target_dir == current_dir or not target_dir:
                    new_link = os.path.basename(new_link)
                logger.debug(f"MD mapping found for: {original_link} -> {new_link}")
                return self.convert_wikilink(description, new_link)

            # Try with .html extension explicitly
            html_link = f"{base_name}.html"
            if html_link in self.filename_mapping:
                logger.debug(f"Found match for html_link: {html_link}")
                new_link = self.filename_mapping[html_link]
                # Check if the target is in the same directory
                target_dir = os.path.dirname(new_link)
                if target_dir == current_dir or not target_dir:
                    new_link = os.path.basename(new_link)
                logger.debug(f"HTML mapping found for: {original_link} -> {new_link}")
                return self.convert_wikilink(description, new_link)

            # Try with just the base name (no extension)
            if base_name in self.filename_mapping:
                logger.debug(f"Found match for base_name: {base_name}")
                new_link = self.filename_mapping[base_name]
                # Check if the target is in the same directory
                target_dir = os.path.dirname(new_link)
                if target_dir == current_dir or not target_dir:
                    new_link = os.path.basename(new_link)
                logger.debug(f"Base name mapping found for: {original_link} -> {new_link}")
                return self.convert_wikilink(description, new_link)
            
            # If we get here, no mapping was found
            logger.debug(f"No mapping found for link: {original_link}")

            # character replacement for "+" placeholders
            base_name = self.sanitize_filename(base_name)
            
            # Keep page IDs unchanged but ensure they have .md extension
            if base_name.isdigit():
                logger.debug(f"Numeric link found: {base_name}")
                if f"{base_name}.md" in self.filename_mapping:
                    logger.debug(f"Found match for base_name: {base_name}")
                    new_link = self.filename_mapping[f"{base_name}.md"]
                    # Check if the target is in the same directory
                    target_dir = os.path.dirname(new_link)
                    if target_dir == current_dir or not target_dir:
                        new_link = os.path.basename(new_link)
                    logger.debug(f"Found mapping for numeric ID: {base_name}.md -> {new_link}")
                    return self.convert_wikilink(description, new_link)
                elif f"{base_name}.html" in self.filename_mapping:
                    logger.debug(f"Found match for {base_name}.html: {base_name}.html")
                    new_link = self.filename_mapping[f"{base_name}.html"]
                    # Check if the target is in the same directory
                    target_dir = os.path.dirname(new_link)
                    if target_dir == current_dir or not target_dir:
                        new_link = os.path.basename(new_link)
                    logger.debug(f"Found mapping for numeric ID: {base_name}.html -> {new_link}")
                    return self.convert_wikilink(description, new_link)
                else:
                    logger.debug(f"No mapping found for numeric ID: {base_name}")
                    base_name = base_name + ".md"
                    return self.convert_wikilink(description, base_name)

            # Remove underscore_digits suffix if present
            if UNDERSCORE_DIGITS_PATTERN.search(base_name):
                base_name = base_name.rsplit('_', 1)[0]

            # Use file_cache to check existence
            potential_path = f"{base_name}.md"
            if potential_path in self.file_cache:
                # Use just the filename for same-directory links
                return self.convert_wikilink(description, os.path.basename(potential_path))

            logger.debug(f"Using default link format for: {original_link} -> {base_name}.md")
            base_name = base_name + ".md"
            return self.convert_wikilink(description, base_name)

        # Process link with as regex
        return LINK_PATTERN.sub(process_link, markdown_content)

    def sanitize_filename(self, filename: str) -> str:
        """
        Consistently sanitize filenames for Obsidian compatibility.

        Combines regex efficiency with specific character handling for optimal
        performance and accuracy.
        """
        if not filename:
            logger.debug(f"Could not find a filename to sanitize: '{filename}'")
            return "unnamed"

        # Replace problematic characters with dashes
        sanitized = re.sub(INVALID_CHARS, '-', filename)

        # Remove characters that should be eliminated
        sanitized = re.sub(r'[#,]', '', sanitized)

        # Handle spaces according to configuration
        if config.USE_UNDERSCORE_IN_FILENAMES:
            sanitized = sanitized.replace(' ', '_')

        # Trim leading/trailing periods and spaces
        sanitized = sanitized.strip('. ')

        # Ensure the filename is not empty
        if not sanitized:
            logger.debug(f"Could not sanitize filename: '{filename}' -> '{sanitized}'")
            return "unnamed"

        return sanitized

    def add_filename_mapping(self, old_path, new_path):
        """
        Add a mapping from an old filename to a new filename.
        This is used when files are renamed during the conversion process.
        """
        logger.debug(f"Adding filename mapping for: '{new_path}'")

        # Get relative paths for both old and new paths
        try:
            old_rel_path = os.path.relpath(old_path, self.output_folder)
        except ValueError:
            # If paths are on different drives, use the basename
            old_rel_path = os.path.basename(old_path)

        try:
            new_rel_path = os.path.relpath(new_path, self.output_folder)
        except ValueError:
            # If paths are on different drives, use the basename
            new_rel_path = os.path.basename(new_path)

        # Get directory parts for context
        old_dir = os.path.dirname(old_rel_path)
        
        # Add mappings for the basenames as well (for simple links)
        old_basename = os.path.basename(old_path)
        new_basename = os.path.basename(new_path)

        # Store directory context for this basename
        if old_basename not in self.basename_dir_mapping:
            self.basename_dir_mapping[old_basename] = {}
        self.basename_dir_mapping[old_basename][old_dir] = new_basename
        logger.debug(f"Added mapping basename_dir: {old_basename}[{old_dir}] -> {new_basename}")

        # Add mappings for both .html and .md versions of the file
        old_basename_noext, _ = os.path.splitext(old_basename)
        
        # Add mapping for just the basename without extension (for links that might not include extension)
        if old_basename_noext not in self.basename_dir_mapping:
            self.basename_dir_mapping[old_basename_noext] = {}
        self.basename_dir_mapping[old_basename_noext][old_dir] = new_basename
        logger.debug(f"Added mapping basename_dir: {old_basename_noext}[{old_dir}] -> {new_basename}")

        # Add mapping for .md version with directory context
        if f"{old_basename_noext}.md" not in self.basename_dir_mapping:
            self.basename_dir_mapping[f"{old_basename_noext}.md"] = {}
        self.basename_dir_mapping[f"{old_basename_noext}.md"][old_dir] = new_basename
        logger.debug(f"Added mapping basename_dir: {old_basename_noext}.md[{old_dir}] -> {new_basename}")

        # Add mapping for .html version with directory context
        if f"{old_basename_noext}.html" not in self.basename_dir_mapping:
            self.basename_dir_mapping[f"{old_basename_noext}.html"] = {}
        self.basename_dir_mapping[f"{old_basename_noext}.html"][old_dir] = new_basename
        logger.debug(f"Added mapping basename_dir: {old_basename_noext}.html[{old_dir}] -> {new_basename}")

        # Add to the filename mapping
        self.filename_mapping[old_rel_path] = new_rel_path
        logger.debug(f"Added mapping new_rel_path: {old_rel_path} -> {new_rel_path}")

        # Also add with normalized slashes for cross-platform compatibility
        old_rel_path_norm = old_rel_path.replace('\\', '/')
        new_rel_path_norm = new_rel_path.replace('\\', '/')

        # Add to the filename mapping
        self.filename_mapping[old_rel_path_norm] = new_rel_path_norm
        logger.debug(f"Added mapping new_rel_path_norm: {old_rel_path_norm} -> {new_rel_path_norm}")

        # Special handling for index.md files
        if old_basename == "index.md" or old_basename == "index.html":
            # Add mappings for various ways index might be referenced
            # 1. Just the filename - IMPORTANT: Map to basename only, not full path
            self.filename_mapping["index.md"] = new_basename
            logger.debug(f"Added mapping index.md: index.md -> {new_basename}")
            self.filename_mapping["index.html"] = new_basename
            logger.debug(f"Added mapping index.html: index.html -> {new_basename}")

            # 2. With directory structure - use full path for directory-specific mappings
            old_dir = os.path.dirname(old_rel_path)
            if old_dir:
                # For directory-specific index files, map to the full path
                self.filename_mapping[f"{old_dir}/index.md"] = new_rel_path
                logger.debug(f"Added mapping old_dir.md: {old_dir}/index.md -> {new_rel_path}")
                self.filename_mapping[f"{old_dir}/index.html"] = new_rel_path
                logger.debug(f"Added mapping old_dir.html: {old_dir}/index.html -> {new_rel_path}")
                self.filename_mapping[f"{old_dir}\\index.md"] = new_rel_path
                logger.debug(f"Added mapping old_dir.md: {old_dir}\\index.md -> {new_rel_path}")
                self.filename_mapping[f"{old_dir}\\index.html"] = new_rel_path
                logger.debug(f"Added mapping old_dir.html: {old_dir}\\index.html -> {new_rel_path}")
                
                # Also add directory-specific mappings to the basename_dir_mapping
                if "index.md" not in self.basename_dir_mapping:
                    self.basename_dir_mapping["index.md"] = {}
                self.basename_dir_mapping["index.md"][old_dir] = new_basename
                logger.debug(f"Added mapping basename_dir: index.md[{old_dir}] -> {new_basename}")

                if "index.html" not in self.basename_dir_mapping:
                    self.basename_dir_mapping["index.html"] = {}
                self.basename_dir_mapping["index.html"][old_dir] = new_basename
                logger.debug(f"Added mapping basename_dir: index.html[{old_dir}] -> {new_basename}")

    def convert_wikilink(self, description: str, link: str):
        if config.USE_WIKI_LINKS:
            # Return Wikilink Link
            if config.USE_ESCAPING_FOR_WIKI_LINKS:
                ## Escape "|" as "\\|" to avoid broken tables in MD content
                return f"[[{link}\\|{description}]]"
            else:
                return f"[[{link}|{description}]]"
        else:
            # Return regular MD Link
            return f"[{description}](<{link}>)"

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', default="in", help="Input folder name")
    parser.add_argument('--output', default="out", help="Output folder name")
    parser.add_argument('--base-url', default="https://confluence.myCompany.com", help="Confluence Base URL")
    parser.add_argument('--rename-all', action='store_true', help="Rename all files with numeric suffixes")
    parser.add_argument('--use-underscore', action='store_true', help="Replace spaces with underscores in filenames")
    parser.add_argument('--debug-link-mapping', action='store_true', help="Write all Link mappings found in log file for debug")
    return parser.parse_args()

def setup_logging(config: Config) -> logging.Logger:
    """Setup logging configuration"""
    logger = logging.getLogger(config.LOG_PATH_NAME)
    logger.setLevel(logging.DEBUG)
    
    # File handler
    file_handler = logging.FileHandler(config.LOG_FILE)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - [%(funcName)s] - %(message)s'))

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.ERROR)
    console_handler.setFormatter(logging.Formatter('%(message)s'))

    # Setup logger
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger

def print_status(message, error=False, log_only=False):
    """Print user-friendly messages to console"""
    if not log_only:
        if error:
            print(f"Error: {message}", file=sys.stderr)
        else:
            print(message)
    logger.error(message) if error else logger.info(message)

def copy_directory(src: str, dst: str):
    """Copy a directory and its contents"""
    try:
        if os.path.exists(src):
            logger.debug(f"Copying directory from {src} to {dst}")
            shutil.copytree(src, dst, dirs_exist_ok=True)
            logger.debug(f"Successfully copied directory: {src}")
            print_status(f"Copied directory: {os.path.basename(src)}")
        else:
            logger.debug(f"Source directory does not exist: {src}")
    except Exception as e:
        logger.error(f"Failed to copy directory {src}", exc_info=True)
        print_status(f"Failed to copy directory {os.path.basename(src)}", error=True)
        raise

def is_special_folder(path: str, config: Config) -> bool:
    """Check if a path contains any special folder names"""
    special_folders = [config.ATTACHMENTS_PATH, config.IMAGES_PATH, config.STYLES_PATH]
    return any(folder in path.split(os.sep) for folder in special_folders)

def get_special_folder_type(path: str, config: Config) -> str:
    """Determine which type of special folder this is"""
    path_parts = path.split(os.sep)
    if config.STYLES_PATH in path_parts:
        return "styles"
    elif config.ATTACHMENTS_PATH in path_parts:
        return "attachments"
    elif config.IMAGES_PATH in path_parts:
        return "images"
    return None

def count_html_files(input_folders: list, config: Config) -> int:
    """Count HTML files excluding special folders"""
    total_count = 0
    for input_folder in input_folders:
        for root, _, files in os.walk(input_folder):
            # Skip special folders when counting
            if is_special_folder(root, config):
                continue

            # Count HTML files in this directory
            html_files = [f for f in files if f.endswith('.html')]
            total_count += len(html_files)

    return total_count

def handle_special_folders(root: str, output_dir: str) -> None:
    """Copy attachments and images folders with their contents"""
    logger.debug(f"Handling special folders in {root}")
    try:        
        # Copy the entire directory structure and files
        for dir_path, _, filenames in os.walk(root):
            # Get the relative path from the special folder
            rel_subpath = os.path.relpath(dir_path, root)

            # Handle the case where rel_subpath is "."
            if rel_subpath == '.':
                dst_dir = output_dir
            else:
                dst_dir = os.path.join(output_dir, rel_subpath)

            os.makedirs(dst_dir, exist_ok=True)

            # Copy all files in current directory
            for file in filenames:
                src_file = os.path.join(dir_path, file)
                dst_file = os.path.join(dst_dir, file)
                shutil.copy2(src_file, dst_file)
                logger.debug(f"Copied: {src_file} -> {dst_file}")
    except Exception as e:
        logger.error(f"Failed to handle special folders: {str(e)}")
        raise

def process_html_files(root: str, files: list, output_dir: str, stats: ConversionStats, config: Config, link_checker: LinkChecker) -> None:
    """Convert HTML files to Markdown and collect filename mappings"""
    html_files = [f for f in files if f.endswith('.html')]

    # Log all HTML files found in this directory
    logger.debug(f"Found {len(html_files)} HTML files in {root}")
    for filename in html_files:
        input_file = os.path.join(root, filename)
        logger.debug(f"Processing HTML file: {input_file}")

        # Check if file should be skipped (e.g., in special folders)
        if is_special_folder(input_file, config):
            logger.info(f"Skipping file in special folder: {input_file}")
            stats.skip_file("Converting")
            continue

        stats.processed += 1
        md_output = os.path.join(output_dir, filename[:-5] + ".md")
        logger.debug(f"Processing file {stats.processed}/{stats.total}: {filename}")

        try:
            if convert_html_to_md(input_file, md_output, link_checker):
                stats.success += 1
            else:
                stats.failure += 1
        except Exception as e:
            logger.error(f"Failed to convert {filename}: {str(e)}")
            stats.failure += 1

        stats.update_progress()

    # Update phase stats after processing
    stats.update_phase_stats()

def convert_html_to_md(html_file, md_output, link_checker: LinkChecker):
    """Convert HTML to Markdown without fixing crosslinks"""
    try:
        logger.debug(f"Starting conversion of {html_file}")
        logger.debug(f"Target output: {md_output}")

        # Read HTML content
        with open(html_file, 'r', encoding='utf-8') as f:
            html_content = f.read()
        logger.debug(f"HTML file size: {len(html_content)} bytes")

        # Parse with BeautifulSoup
        soup = BeautifulSoup(html_content, 'html.parser')

        # Initialize result buffer
        markdown_sections = []

        # Process main content (preserving document structure)
        for section in identify_document_sections(soup):
            section_type = section["type"]
            element = section["element"]

            if section_type == "main_content":
                # Use html2text for regular content
                markdown_sections.append(convert_plain_section(element))

            elif section_type == "greybox":
                # Direct conversion to wiki links
                markdown_sections.append(convert_greybox_section(element, link_checker))

            elif section_type == "video":
                # Handle video elements
                markdown_sections.append(convert_video_section(element, link_checker))

            elif section_type == "image":
                # Process images with proper paths
                markdown_sections.append(convert_image_section(element, link_checker, md_output))

            elif section_type == "table":
                # Better table handling
                markdown_sections.append(convert_table_section(element))

            elif section_type == "attachment_section":
                # Remove unwanted sections
                if config.SECTIONS_TO_REMOVE:
                    logger.debug("Removing common unwanted sections")
                    for section in config.SECTIONS_TO_REMOVE:
                        element = remove_markdown_section(element, section)

        # Join sections into complete markdown
        markdown_content = "\n\n".join(markdown_sections)

        # Still need some post-processing
        markdown_content = remove_header_link_list(markdown_content)
        markdown_content = remove_confluence_footer(markdown_content)
        markdown_content, created_by_line = remove_created_by(markdown_content, return_line=True)

        # Add YAML header
        if config.YAML_HEADER:
            if os.path.basename(md_output) == "index.md" or os.path.basename(md_output) == "index.html":
                markdown_content = insert_yaml_header_md_index(markdown_content, md_output, config)
            else:
                markdown_content = insert_yaml_header_md(markdown_content, created_by_line, md_output, config)

        # Special handling for numeric filenames
        filename = os.path.basename(md_output)
        base_name, extension = os.path.splitext(filename)
        is_numeric_filename = base_name.isdigit()

        if config.RENAME_ALL_FILES and is_numeric_filename:
            # Extract the first H1 header from markdown content
            header_match = re.search(r'^# (.+?)$', markdown_content, re.MULTILINE)
            if header_match:
                # Use the header as the new filename
                header_text = header_match.group(1).strip()

                # Sanitize the header for use as a filename
                new_base_name = link_checker.sanitize_filename(header_text)
                new_filename = f"{new_base_name}{extension}"
                dir_path = os.path.dirname(md_output)
                new_path = os.path.join(dir_path, new_filename)
                
                # Check if we've already renamed a file to this target path
                rename_conflict = False
                for original, renamed in link_checker.renamed_files.items():
                    if renamed == new_path and original != md_output:
                        rename_conflict = True
                        break

                if not rename_conflict:
                    logger.debug(f"Renaming numeric file '{filename}' to '{new_filename}' based on first H1 header")
                    link_checker.renamed_files[md_output] = new_path
                    cleaned_md_output = new_path
                    # Add explicit mapping for the MD file
                    link_checker.add_filename_mapping(md_output, new_path)
                    
                else:
                    logger.warning(f"Target file '{new_filename}' already mapped. Keeping original name.")
            else:
                logger.debug(f"No H1 header found in '{filename}', keeping original name for numeric file")

        new_md_output = cleaned_md_output if cleaned_md_output != md_output else md_output

        # Handle index.md files - rename based on Space Details table, then remove Space Details section from index
        if os.path.basename(md_output) == "index.md" or os.path.basename(md_output) == "index.html":
            space_name = extract_space_name(markdown_content)
            if space_name:
                sanitized_name = link_checker.sanitize_filename(space_name)
                dir_path = os.path.dirname(md_output)
                new_filename = f"_{sanitized_name}.md"
                new_md_output = os.path.join(dir_path, new_filename)

                # Check if the target file already exists
                if os.path.exists(new_md_output):
                    logger.warning(f"Target file {new_filename} already exists. Using original name: {md_output}.")
                    new_md_output = md_output
                # Update the link mapping in the link checker
                link_checker.add_filename_mapping(md_output, new_md_output)

            # Remove Space Details section from index
            if config.SPACE_DETAILS_SECTION:
                logger.debug("Removing Space Details sections from index file")
                markdown_content = remove_space_details(markdown_content)

        # Add mapping for the original HTML file to the final markdown file
        link_checker.add_filename_mapping(html_file, new_md_output)

        # Save the markdown with the correct filename
        logger.debug(f"Saving markdown to: {new_md_output}")

        # Ensure the directory exists
        os.makedirs(os.path.dirname(new_md_output), exist_ok=True)

        with open(new_md_output, 'w', encoding='utf-8') as f:
            f.write(markdown_content)

        if not os.path.exists(new_md_output):
            raise FileNotFoundError(f"Output file not created: {new_md_output}")

        output_size = os.path.getsize(new_md_output)
        logger.debug(f"Conversion successful. Output file size: {output_size} bytes")
        return True

    except Exception as e:
        logger.error(f"Conversion failed for {html_file}", exc_info=True)
        logger.debug(f"Error details: {str(e)}")
        print_status(f"Failed to convert {os.path.basename(html_file)}", error=True)
        raise

def identify_document_sections(soup):
    """Identify and categorize sections of a Confluence HTML document"""
    sections = []

    # Process body content in order
    for element in soup.body.find_all(recursive=False):
        # Greybox sections (attachments)
        if element.name == "div" and element.get("class") and "greybox" in element.get("class"):
            sections.append({"type": "greybox", "element": element})

        # Video elements
        elif element.find("video") is not None:
            sections.append({"type": "video", "element": element})

        # Image elements
        elif element.name == "img" or element.find("img") is not None:
            sections.append({"type": "image", "element": element})

        # Table elements
        elif element.name == "table" or element.find("table") is not None:
            sections.append({"type": "table", "element": element})

        # Attachments section (to be skipped)
        elif element.name == "h2" and element.get("id") == "attachments":
            # Include this and the following div.greybox as one section
            attachment_section = element
            next_element = element.find_next_sibling()
            if next_element and next_element.name == "div" and next_element.get("class") and "greybox" in next_element.get("class"):
                attachment_section = [element, next_element]
            sections.append({"type": "attachment_section", "element": attachment_section})

        # Regular content
        else:
            sections.append({"type": "main_content", "element": element})

    return sections

def convert_plain_section(element):
    """Convert regular HTML content to Markdown using html2text"""
    h = html2text.HTML2Text()
    h.ignore_links = False
    h.ignore_images = False
    h.ignore_tables = False
    h.body_width = 0
    h.protect_links = True
    h.unicode_snob = True
    h.mark_code = True

    # Convert just this element to string
    html_str = str(element)
    return h.handle(html_str)

def convert_greybox_section(element, link_checker: LinkChecker):
    """Directly convert greybox attachments to wiki links"""
    result = []

    for link in element.find_all('a'):
        href = link.get('href', '').strip()
        text = link.text.strip()

        if href and text and href.startswith('attachments/'):
            wiki_link = link_checker.convert_wikilink(text, href)
            result.append(wiki_link)

    return '\n'.join(result)

def convert_video_section(element, link_checker: LinkChecker):
    return

def convert_image_section(element, link_checker: LinkChecker, md_output):
    return

def convert_table_section(element):
    return

def fix_md_crosslinks(output_dir: str, link_checker: LinkChecker, stats: ConversionStats) -> None:
    """Second pass: Fix all crosslinks in all Markdown files"""
    md_files = []
    for root, _, files in os.walk(output_dir):
        for file in files:
            if file.endswith('.md'):
                md_files.append(os.path.join(root, file))

    stats.total = len(md_files)
    stats.phase_stats[stats.current_phase]["total"] = stats.total
    logger.debug(f"Found {stats.total} Markdown files to process for crosslink fixing")
    
    # Special debug for numeric links
    numeric_links_found = []

    for md_file in md_files:
        stats.processed += 1
        try:
            logger.debug(f"Fixing crosslinks in {md_file}")

            # Read the markdown content
            with open(md_file, 'r', encoding='utf-8') as f:
                content = f.read()

            # Debug: Find all numeric links before fixing
            for match in re.finditer(r'\[([^\]]+)\]\(<?((\d+)\.md)>?\)', content):
                numeric_links_found.append({
                    'file': md_file,
                    'link_text': match.group(1),
                    'link_target': match.group(2),
                    'numeric_id': match.group(3)
                })

            # Fix crosslinks
            logger.debug(f"Using mapping basename_dir_mapping: {link_checker.basename_dir_mapping}")
            fixed_content = link_checker.fix_crosslinks(content, md_file)

            # Save the file if changes were made
            if fixed_content != content:
                with open(md_file, 'w', encoding='utf-8') as f:
                    f.write(fixed_content)
                logger.debug(f"Updated crosslinks in {md_file}")

            stats.success += 1
        except Exception as e:
            logger.error(f"Failed to fix crosslinks in {md_file}: {str(e)}")
            stats.failure += 1

        stats.update_progress()

    # Log all numeric links found
    if numeric_links_found:
        logger.debug(f"=== Found {len(numeric_links_found)} numeric links ===")
        for link in numeric_links_found:
            logger.debug(f"File: {link['file']}, Link: [{link['link_text']}]({link['link_target']})")
            # Check if we have a mapping for this numeric ID
            if f"{link['numeric_id']}.md" in link_checker.filename_mapping:
                logger.debug(f"  Mapping exists: {link['numeric_id']}.md -> {link_checker.filename_mapping[f'{link['numeric_id']}.md']}")
            elif f"{link['numeric_id']}.html" in link_checker.filename_mapping:
                logger.debug(f"  Mapping exists: {link['numeric_id']}.html -> {link_checker.filename_mapping[f'{link['numeric_id']}.html']}")
            else:
                logger.debug(f"  No mapping found for {link['numeric_id']}")
                    
    # Update phase stats after processing
    stats.update_phase_stats()

def remove_header_link_list(markdown_content):
    """
    Removes any content that appears before the first heading in markdown content.
    Finds the first line starting with '#' and returns all content from that point forward.

    Args:
        markdown_content (str): The original markdown content

    Returns:
        str: The cleaned markdown content starting with the first heading
    """
    # Find the first line that starts with '#'
    lines = markdown_content.split('\n')
    start_index = 0

    for i, line in enumerate(lines):
        if line.strip().startswith('#'):
            start_index = i
            break

    # Return all content starting from the first heading
    result = '\n'.join(lines[start_index:])

    return result

def remove_space_details(markdown_content):
    """
    Removes the "#  Space Details:" section from markdown content, stopping at the first H2 or next H1.
    Preserves all other sections, including "## Available Pages:" and other H1/H2 headers.

    Args:
        markdown_content (str): The original markdown content

    Returns:
        str: The cleaned markdown content with the Space Details section removed
    """
    space_header = config.SPACE_DETAILS_SECTION

    # Check if the Space Details header exists
    if space_header not in markdown_content:
        # If not found, return the original content unchanged
        return markdown_content
    
    # Split the content at the Space Details header
    parts = markdown_content.split(space_header, 1)
    before_header = parts[0]
    after_header = parts[1]

    # Find the next H1 or H2 header
    lines = after_header.split('\n')
    end_index = len(lines)  # Default to end of section

    for i, line in enumerate(lines):
        # Check if this line starts a new H1 or H2 section
        if line.strip().startswith('#'):
            end_index = i
            break

    # Reconstruct the content without the Space Details section
    if end_index < len(lines):
        # There is another section after Space Details
        result = before_header + '\n'.join(lines[end_index:])
    else:
        # Space Details was the only section
        result = before_header.rstrip()

    return result

def remove_confluence_footer(markdown_content: str) -> str:
    """Remove the standard Confluence footer from markdown content"""
    # Pattern to match the footer with variable date/time
    footer_pattern = r'\nDocument generated by Confluence on [A-Za-z]+\. \d{1,2}, \d{4} \d{1,2}:\d{2}\n\n\[Atlassian\]\(<https://www\.atlassian\.com/>\)\n*$'
    
    # Remove the footer
    cleaned_content = re.sub(footer_pattern, '', markdown_content)
    return cleaned_content

def remove_markdown_section(markdown_content: str, section_header: str) -> str:
    """
    Remove a specific markdown section and all its content including subsections.
    Stops when it encounters another section at the same level or higher.

    Args:
        markdown_content: The markdown content to process
        section_header: The section header to remove (e.g., "## Attachments:")
                        Must include the heading markers (# or ##)

    Returns:
        The markdown content with the specified section removed

    Example:
        remove_markdown_section(content, "## Attachments:")
        remove_markdown_section(content, "## Space contributors")
        remove_markdown_section(content, "# Any other Header")
    """
    logger.debug(f"Removing section '{section_header}' from markdown content")

    # Check if the section exists
    if section_header not in markdown_content:
        logger.debug(f"No '{section_header}' section found")
        return markdown_content

    # Determine the heading level (count the leading # symbols)
    heading_level = 0
    for char in section_header:
        if char == '#':
            heading_level += 1
        elif char != '#':
            # Stop if we hit any other character
            break

    # Split content at the section header
    parts = markdown_content.split(section_header, 1)
    before_section = parts[0]
    section_and_after = section_header + parts[1]

    # Find the next section at the same level or higher
    lines = section_and_after.split('\n')
    end_index = len(lines)  # Default to end of document

    for i, line in enumerate(lines[1:], 1):  # Skip the section header line
        # Check if this line starts a new section
        line_stripped = line.strip()
        if line_stripped.startswith('#'):
            # Count the # symbols until the first space
            line_heading_level = 0
            for char in line_stripped:
                if char == '#':
                    line_heading_level += 1
                elif char == ' ':
                    # Stop counting when we hit a space
                    break
                else:
                    # Stop if we hit any other character
                    break

            # If this heading is at the same level or higher, stop here
            if line_heading_level <= heading_level:
                end_index = i
                break

    # Reconstruct the content without the removed section
    if end_index < len(lines):
        # There is a section after the removed one
        cleaned_content = before_section + '\n'.join(lines[end_index:])
    else:
        # The removed section was the last section
        cleaned_content = before_section.rstrip()

    # Check if we made changes
    if cleaned_content != markdown_content:
        logger.debug(f"'{section_header}' section removed")

    return cleaned_content

def extract_space_name(markdown_content: str) -> str:
    """
    Extract the 'Name' value from the Space Details table in the markdown content.
    Returns None if not found.
    """
    logger.debug("Extracting space name from markdown content")

    # Look for the Space Details header
    if "#  Space Details:" not in markdown_content:
        logger.debug("Space Details header not found")
        return None

    # Split content to get the part after the header
    parts = markdown_content.split("#  Space Details:", 1)
    if len(parts) < 2:
        return None

    table_section = parts[1].strip().split("\n\n", 1)[0]

    # Try to match different table formats

    # Format 1: | Key | Value |
    pipe_pattern = re.compile(r'\|\s*Name\s*\|\s*([^|]+)\s*\|', re.MULTILINE)
    match = pipe_pattern.search(table_section)
    if match:
        name = match.group(1).strip()
        logger.debug(f"Found space name using pipe format: {name}")
        return name

    # Format 2: Key | Value
    alt_pattern = re.compile(r'Name\s*\|\s*(.+?)(?:\n|$)', re.MULTILINE)
    match = alt_pattern.search(table_section)
    if match:
        name = match.group(1).strip()
        logger.debug(f"Found space name: {name}")
        return name

    logger.debug("Space name not found in table")
    return None

def extract_space_metadata(markdown_content: str) -> tuple[str, str]:
    """
    Extract author, date information and space name from the Space Details table in markdown content.
    Returns a tuple of (author, date_created, space_name) or (None, None, None) if not found.
    """
    logger.debug("Extracting space metadata from markdown content")

    author = "unknown"
    date_created = None
    space_name = None
    space_header = config.SPACE_DETAILS_SECTION

    # Look for the Space Details header
    if space_header not in markdown_content:
        logger.debug("Space Details header not found")
        return None, None, None

    # Define regex patterns for different metadata fields
    name_pattern = re.compile(r'Name\s*\|\s*([^\n|]+)', re.MULTILINE)
    creator_pattern = re.compile(r'Created by\s*\|\s*([^\n|]+)', re.MULTILINE)

    # Extract space name
    name_match = name_pattern.search(markdown_content)
    if name_match:
        space_name = name_match.group(1).strip()
        logger.debug(f"Found space name: {space_name}")

    # Extract creator information
    creator_match = creator_pattern.search(markdown_content)
    if creator_match:
        creator_text = creator_match.group(1).strip()

        # Extract author name (before parentheses)
        if '(' in creator_text:
            author = creator_text.split('(')[0].strip()
        else:
            author = creator_text
        logger.debug(f"Found space creator: {author}")

        # Extract date
        date_match = re.search(r'\(([^)]+)\)', creator_text)
        if date_match:
            date_text = date_match.group(1).strip()

            # Handle various date formats
            # Format: "Feb. 03, 2017"
            month_abbr_pattern = re.compile(r'(\w+)\.?\s+(\d{1,2}),\s+(\d{4})')
            month_abbr_match = month_abbr_pattern.search(date_text)

            if month_abbr_match:
                month_name = month_abbr_match.group(1)
                day = month_abbr_match.group(2).zfill(2)  # Pad with leading zero if needed
                year = month_abbr_match.group(3)
    
            if month_name in MONTH_PATTERNS:
                    month = MONTH_PATTERNS[month_name]
                    date_created = f"{year}-{month}-{day}"
                    logger.debug(f"Found space creation date: {date_created}")

    return author, date_created, space_name

def remove_created_by(markdown_content: str, return_line: bool = True) -> tuple[str, str]:
    """
    Remove the 'Created by' line from markdown content and return both the cleaned content
    and the removed line.

    There is only one such line in the document, which can appear anywhere.

    Args:
        markdown_content: The original markdown content
        return_line: If True, return the removed line as the second element of the tuple

    Returns:
        A tuple containing (cleaned_content, removed_line)
        If no line was removed or return_line is False, removed_line will be an empty string

    Examples of lines to remove:
    - 'Created by any name goes here, last modified on Dec 29, 2021'
    - 'Created by Unbekannter Benutzer (abc123), last modified on Feb. 01, 2017'
    - 'Created by Unbekannter Benutzer (otherusername) on Apr 25, 2019'
    - 'Created by Unbekannter Benutzer (anyone), last modified by other user name on Jan. 30, 2025'
    """
    logger.debug("Removing 'Created by' line from markdown content")

    # Pattern to match lines starting with 'Created by' (with one or more spaces) and containing date information
    created_by_pattern = r'Created by\s+.*(?:on|last modified).*\d+.*'

    created_by_line = ""
    lines = markdown_content.splitlines()
    i = 0

    # Find the 'Created by' line
    while i < len(lines):
        if re.match(created_by_pattern, lines[i]):
            if return_line:
                created_by_line = lines[i]

            # Remove the line
            lines.pop(i)

            # Also remove any blank line that follows the 'Created by' line
            if i < len(lines) and lines[i].strip() == "":
                lines.pop(i)

            logger.debug("'Created by' line removed")
            break  # Exit loop after finding the first match
        i += 1

    # Reconstruct the cleaned content
    cleaned_content = '\n'.join(lines)

    return cleaned_content, created_by_line

def insert_yaml_header_md(markdown_content: str, created_by_line: str, md_output: str, config: Config) -> str:
    """
    Insert a YAML header at the beginning of the markdown content with information
    extracted from the 'Created by' line and file path.

    Args:
        markdown_content: The original markdown content
        created_by_line: The 'Created by' line that was removed from the content
        md_output: The output file path
        config: The configuration object containing YAML_HEADER template

    Returns:
        The markdown content with the YAML header added

    Examples of created_by_line formats:
    - 'Created by Unbekannter Benutzer (abc123), last modified on Feb. 01, 2017'
    - 'Created by Unbekannter Benutzer (anyone), last modified by other user name on Jan. 30, 2025'
    - 'Created by Unbekannter Benutzer (otherusername) on Apr 25, 2019'
    """
    logger.debug(f"Inserting YAML header into markdown content for {md_output}")

    # Start with the template from config
    yaml_header = config.YAML_HEADER

    # Extract author from created_by_line
    author = "unknown"
    if created_by_line:
        # Try to extract username from parentheses like "(abc123)"
        author_match = re.search(r'\(([^)]+)\)', created_by_line)
        if author_match:
            author = author_match.group(1)
            logger.debug(f"Extracted author: {author}")
        else:
            logger.debug(f"No author found in parentheses in: {created_by_line}")

    # Extract date from created_by_line
    date_created = "1999-12-31"  # Default date
    if created_by_line:
        # Build a regex pattern that includes all month names from MONTH_PATTERNS
        month_names = '|'.join(MONTH_PATTERNS.keys())
        date_pattern = rf'({month_names})\.?\s+(\d{{1,2}}),?\s+(\d{{4}})'
        
        date_match = re.search(date_pattern, created_by_line)
        if date_match:
            logger.debug(f"DEBUG date_match: {date_match}")
            month = MONTH_PATTERNS[date_match.group(1)]
            day = date_match.group(2).zfill(2)  # Pad with leading zero if needed
            year = date_match.group(3)
            date_created = f"{year}-{month}-{day}"
            logger.debug(f"Extracted date: {date_created}")
        else:
            logger.debug(f"No date found in: {created_by_line}")

    # Get parent folder name for the up link
    parent_folder = "Knowledge Base"  # Default value
    dir_path = os.path.dirname(md_output)
    if dir_path:
        parent_folder = os.path.basename(dir_path)
        if not parent_folder:  # If the directory is empty (root folder)
            parent_folder = "Knowledge Base"
        logger.debug(f"Extracted parent folder: {parent_folder}")

    # Replace placeholders in the YAML header
    yaml_header = yaml_header.replace('author: username', f'author: {author}')
    yaml_header = re.sub(r'dateCreated:\s*\d{4}-\d{2}-\d{2}', f'dateCreated: {date_created}', yaml_header)
    yaml_header = yaml_header.replace('"[[Knowledge Base]]"', f'"[[{parent_folder}]]"')

    # Add the YAML header to the markdown content
    updated_content = yaml_header + '\n\n' + markdown_content

    return updated_content

def insert_yaml_header_md_index(markdown_content: str, md_output: str, config: Config) -> str:
    """
    Insert a YAML header at the beginning of index markdown files with information
    extracted from the Space Details table and file path.

    Args:
        markdown_content: The original markdown content
        md_output: The output file path
        config: The configuration object containing YAML_HEADER template

    Returns:
        The markdown content with the YAML header added
    """
    logger.debug(f"Inserting YAML header into index markdown content for {md_output}")

    # Start with the template from config
    yaml_header = config.YAML_HEADER

    # Extract author, date and name from the Space Details table
    author, date_created, _ = extract_space_metadata(markdown_content)

    if not author:
        author = "unknown"
        logger.debug(f"No author found in Space Details, using default: {author}")

    if not date_created:
        date_created = "1999-12-31"  # Default date
        logger.debug(f"No date found in Space Details, using default: {date_created}")

    # Get parent folder name for the up link
    parent_folder = "Knowledge Base"  # Default value
    dir_path = os.path.dirname(md_output)
    if dir_path:
        parent_folder = os.path.basename(dir_path)
        if not parent_folder:  # If the directory is empty (root folder)
            parent_folder = "Knowledge Base"
        logger.debug(f"Extracted parent folder: {parent_folder}")

    # Replace placeholders in the YAML header
    yaml_header = yaml_header.replace('author: username', f'author: {author}')
    yaml_header = re.sub(r'dateCreated:\s*\d{4}-\d{2}-\d{2}', f'dateCreated: {date_created}', yaml_header)
    yaml_header = yaml_header.replace('"[[Knowledge Base]]"', f'"[[{parent_folder}]]"')

    # Add the YAML header to the markdown content
    updated_content = yaml_header + '\n\n' + markdown_content

    return updated_content

def main(config: Config, logger: logging.Logger) -> None:
    try:
        logger.debug("=== Starting HTML to Markdown Conversion Process ===")
        logger.debug(f"Python version: {sys.version}")

        # Get list of input folders
        input_folders = []
        if os.path.isdir(config.INPUT_FOLDER):
            # If INPUT_FOLDER is a directory, use it directly
            input_folders = [config.INPUT_FOLDER]
        else:
            # If INPUT_FOLDER is a pattern or list, expand it
            input_folders = [folder for folder in config.INPUT_FOLDER.split(',') if os.path.isdir(folder)]

        logger.debug(f"Input folders: {input_folders}")
        logger.debug(f"Output folder: {os.path.abspath(config.OUTPUT_FOLDER)}")

        # Create output folder
        os.makedirs(config.OUTPUT_FOLDER, exist_ok=True)
        logger.debug(f"Output directory structure created: {config.OUTPUT_FOLDER}")

        # Initialize statistics
        stats = ConversionStats()

        # Initialize link checker
        link_checker = LinkChecker(config)
        link_checker._build_file_cache()

        # Count total HTML files across all input folders
        print_status("Scanning files...")
        total_html_count = count_html_files(input_folders, config)

        logger.debug(f"Found {total_html_count} HTML files to process across all input folders")
        print_status(f"Found {total_html_count} HTML files to process")

        # First pass: Handle special folders for all input folders
        print_status("Copying special folders (attachments and images)...")        
        for input_folder in input_folders:
            for root, _, files in os.walk(input_folder):
                # Get relative path
                rel_path = os.path.relpath(root, input_folder)
                output_dir = os.path.join(config.OUTPUT_FOLDER, rel_path)

                # Check if this is a special folder
                folder_type = get_special_folder_type(root, config)

                # Ignoring 'styles' folder on purpose
                if folder_type:
                    # Handle special folders based on type
                    if folder_type in ["attachments", "images"]:
                        # Create output directory for attachments and images
                        os.makedirs(output_dir, exist_ok=True)
                        # Copy special folders
                        handle_special_folders(root, output_dir)
                    if folder_type in ["styles"]:
                        for file in files:
                            file_path = os.path.join(root, file)
                            logger.info(f"SKIPPED: File in styles folder: {file_path}")

        # Second pass: Convert HTML files to Markdown for all input folders
        print_status("Converting HTML files to Markdown...")
        stats.set_phase("Converting")  # Start conversion phase
        stats.total = total_html_count

        for input_folder in input_folders:
            for root, _, files in os.walk(input_folder):
                # Skip special folders
                if get_special_folder_type(root, config):
                    continue

                rel_path = os.path.relpath(root, input_folder)
                output_dir = os.path.join(config.OUTPUT_FOLDER, rel_path)
                os.makedirs(output_dir, exist_ok=True)

                # Process HTML files (convert only)
                process_html_files(root, files, output_dir, stats, config, link_checker)

        # Third pass: Fix all crosslinks using the complete mapping
        print_status("\nFixing crosslinks in all Markdown files...")
        stats.update_phase_stats()  # Save converting stats
        stats.set_phase("Fixing links")  # Start conversion phase

        # Fix md crosslinks
        fix_md_crosslinks(config.OUTPUT_FOLDER, link_checker, stats)

        # Update phase stats after fixing links
        stats.update_phase_stats()

        # Debug print mappings
        if config.LOG_LINK_MAPPING == True:
            debug_print_mappings(link_checker)

        # Log summary of skipped files
        if stats.phase_stats["Converting"]["skipped"] > 0:
            logger.info(f"=== Summary: {stats.phase_stats['Converting']['skipped']} Files were skipped ===")

        print("\n")
        print_status("Finalizing and cleaning up...")
        logger.info("To find all skipped files, search the log for 'SKIPPED:'")
        logger.debug("=== Conversion Process Complete ===")
        print("\n")
        stats.print_final_report()

    except Exception as e:
        logger.error("Process failed", exc_info=True)
        print_status(str(e), error=True)
        sys.exit(1)

def debug_print_mappings(link_checker: LinkChecker):
    """Print all filename mappings for debugging"""
    logger.debug("=== Filename Mappings ===")
    for old_path, new_path in link_checker.filename_mapping.items():
        logger.debug(f"{old_path} -> {new_path}")

    logger.debug("=== Directory-Aware Basename Mappings ===")
    for basename, dir_mappings in link_checker.basename_dir_mapping.items():
        for directory, mapped_name in dir_mappings.items():
            logger.debug(f"{directory}/{basename} -> {mapped_name}")

    logger.debug("=== End of Mappings ===")

if __name__ == "__main__":
    # Parse command line arguments
    args = parse_args()

    # Load configuration from config.py (which will load from config.ps1)
    from config import load_config

    # Merge command line arguments with config from file
    config = load_config(args)

    # Setup logging
    logger = setup_logging(config)

    # Run main function
    main(config, logger)
