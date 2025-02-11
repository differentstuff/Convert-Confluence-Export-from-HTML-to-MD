import os
import sys
import logging
import shutil
import html2text
import re
import requests
from typing import Set, Tuple
import argparse
from dataclasses import dataclass
from bs4 import BeautifulSoup

# CONSTANTS
LINK_PATTERN = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')
FILENAME_PATTERN = re.compile(r'^(.+)_(\d+)(\.md)$')
UNDERSCORE_DIGITS_PATTERN = re.compile(r'_\d+$')
URL_PATTERN = re.compile(r'\[([^\]]+)\]\((https?://[^)]+)\)')

@dataclass
class Config:
    CONFLUENCE_BASE_URL: str = "https://confluence.myCompany.com"
    INPUT_FOLDER: str = "in"
    OUTPUT_FOLDER: str = "out"
    RENAME_ALL_FILES: bool = False
    ATTACHMENTS_PATH: str = "attachments"
    IMAGES_PATH: str = "images"
    STYLES_PATH: str = "styles" # will be skipped in output
    PREFIXES: list = None
    LOG_FOLDER: str = "logs"
    LOG_NAME: str = "html2mdConverter"
    LOG_FILE_NAME: str = f"{LOG_NAME}.log"

    def __post_init__(self):
        # Derived constants
        self.LOG_FOLDER = os.path.join(self.OUTPUT_FOLDER, self.LOG_FOLDER)
        self.LOG_FILE = os.path.join(self.LOG_FOLDER, self.LOG_FILE_NAME)
        if self.PREFIXES is None:
            self.PREFIXES = [
                '/pages/viewpage.action?pageId=',
                '/display/',
                '/'
            ]

        # Create necessary directories
        os.makedirs(self.LOG_FOLDER, exist_ok=True)

    @classmethod
    def from_args(cls, args):
        return cls(
            INPUT_FOLDER=args.input,
            OUTPUT_FOLDER=args.output,
            CONFLUENCE_BASE_URL= args.base_url,
            RENAME_ALL_FILES=args.rename_all
        )

class ConversionStats:
    def __init__(self):
        self.total = 0
        self.processed = 0
        self.success = 0
        self.failure = 0
        self.current_phase = ""

    def update_progress(self):
        """Update progress in terminal"""
        if self.current_phase:
            print(f"\r{self.processed}/{self.total} completed - {self.current_phase}", end='', flush=True)
        else:
            print(f"\r{self.processed}/{self.total} completed", end='', flush=True)

    def set_phase(self, phase: str):
        """Set current processing phase"""
        self.current_phase = phase
        self.update_progress()

    def print_final_report(self):
        """Print final statistics"""
        print("- Conversion Summary -")
        print(f"Processed: {self.total}")
        print(f"Success: {self.success}")
        print(f"Failure: {self.failure}\n")
        print(f"See {config.LOG_FILE} for details.")
        
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
        if 'rest/documentConversion' in src_path:
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

    def fix_internal_links(self, markdown_content: str, current_file_path: str) -> str:
        """Fix internal links in markdown content"""
        logger.debug(f"Fixing internal links in {current_file_path}")

        def process_link(match):
            description = match.group(1)
            link = match.group(2).strip('<>')  # Remove angle brackets if present

            # Skip special paths
            if 'rest/documentConversion' in link:
                return match.group(0)
                
            # If link starts with the Confluence base URL, remove it
            if link.startswith(config.CONFLUENCE_BASE_URL):
                link = link[len(config.CONFLUENCE_BASE_URL):]
                
            # Skip if it's a web URL or an attachment/image link
            if (self.is_web_url(link) or
                config.ATTACHMENTS_PATH in link or
                config.IMAGES_PATH in link):
                return match.group(0)

            # Process internal links
            new_link = link

            # Remove common prefixes
            for prefix in config.PREFIXES:
                if new_link.startswith(prefix):
                    new_link = new_link[len(prefix):]
                    break

            # Handle display paths (like IT/Hardware)
            if '/' in new_link:
                # Split path components
                components = new_link.split('/')
                # Use the last component as the base name
                base_name = components[-1]
            else:
                base_name = new_link
            
            # Extract the base name without extension
            base_name = os.path.splitext(base_name)[0]

            # If it ends with a number (pageId), use just that
            if base_name.split('/')[-1].isdigit():
                base_name = base_name.split('/')[-1]

            # Check in both input and output directories
            input_dir = os.path.dirname(os.path.join(self.input_folder, os.path.relpath(current_file_path, self.output_folder)))
            output_dir = os.path.dirname(current_file_path)
            
            # List of possible file patterns to check
            patterns = [
                (input_dir, f"{base_name}.html"),  # Input HTML
                (input_dir, f"{base_name}.md"),    # Input MD (if already converted)
                (output_dir, f"{base_name}.md")    # Output MD
            ]

            # Check for existence of any variant
            for dir_path, file_pattern in patterns:
                possible_file = os.path.join(dir_path, file_pattern)
                if os.path.exists(possible_file):
                    # Always use .md extension in the link
                    new_link = f"{base_name}.md"
                    logger.debug(f"Fixed link: {link} -> {new_link}")
                    return f"[{description}](<{new_link}>)"

            # If we couldn't find the file but have a valid base name
            new_link = f"{base_name}.md"
            logger.debug(f"Converting to MD link: {link} -> {new_link}")
            return f"[{description}](<{new_link}>)"

        # Replace all links
        new_content = re.sub(LINK_PATTERN, process_link, markdown_content)
        return new_content

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

            new_link = f'[{description}](<{url}>)'
            markdown_content = re.sub(old_pattern, new_link, markdown_content)

            if not is_valid:
                logger.warning(f"Image verification failed but keeping link: {url} - {status}")

        # Fix internal links
        markdown_content = self.fix_internal_links(markdown_content, current_file_path)

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
        - If RENAME_ALL_FILES is True: Remove all numeric suffixes
        - If False: Only remove when corresponding attachment folder exists

        Example: 'CNC_8355908.md' -> 'CNC.md' if '8355908' exists as attachment subfolder
        """
        logger.debug(f"Checking filename for cleanup: {md_output}")
        if md_output in self.renamed_files:
            return self.renamed_files[md_output]

        # Get the filename and directory path
        dir_path = os.path.dirname(md_output)
        filename = os.path.basename(md_output)

        # Regular expression to match filename_numbers.md pattern
        match = FILENAME_PATTERN.match(filename)
        if not match:
            return md_output

        if not match:
            logger.debug(f"No numeric suffix found in filename: {filename}")
            return md_output

        base_name, number, extension = match.groups()
        #base_name = match.group(1)  # Original name without numbers
        #number = match.group(2)     # The numeric part
        #extension = match.group(3)  # '.md'

        # character replacement for "+" placeholders
        base_name = self.sanitize_filename(base_name)

        if config.RENAME_ALL_FILES:
                # Always rename files that match the pattern
                new_filename = f"{base_name}{extension}"
                new_path = os.path.join(dir_path, new_filename)
                logger.debug(f"Renaming {filename} to {new_filename} (RENAME_ALL_FILES=True)")
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

        def process_link(match):
            description = match.group(1)
            link = match.group(2).strip('<>')

            # Skip if it's a web URL or an attachment/image link
            if self.is_web_url(link) or config.ATTACHMENTS_PATH in link or config.IMAGES_PATH in link:
                return match.group(0)

            # Get base filename without extension
            base_name = os.path.splitext(os.path.basename(link))[0]

            # character replacement for "+" placeholders
            base_name = self.sanitize_filename(base_name)
            
            # Keep page IDs unchanged
            if base_name.isdigit():
                return f"[{description}](<{base_name}.md>)"

            # Remove underscore_digits suffix if present
            if UNDERSCORE_DIGITS_PATTERN.search(base_name):
                base_name = base_name.rsplit('_', 1)[0]

            # Use file_cache to check existence
            potential_path = f"{base_name}.md"
            if potential_path in self.file_cache:
                return f"[{description}](<{potential_path}>)"
            
            return f"[{description}](<{base_name}.md>)"

        # Process link with as regex
        return LINK_PATTERN.sub(process_link, markdown_content)

    def sanitize_filename(self, filename: str) -> str:
        """Consistently sanitize filenames and links"""
        return filename.replace('+', '-')

def setup_logging(config: Config) -> logging.Logger:
    """Setup logging configuration"""
    logger = logging.getLogger(config.LOG_NAME)
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

def convert_html_to_md(html_file, md_output, link_checker: LinkChecker):
    try:
        logger.debug(f"Starting conversion of {html_file}")
        logger.debug(f"Target output: {md_output}")

        # Configure html2text
        h = html2text.HTML2Text()
        h.ignore_links = False
        h.ignore_images = False
        h.ignore_tables = False
        h.body_width = 0
        h.protect_links = True
        h.unicode_snob = True
        h.mark_code = True

        # Read HTML content
        with open(html_file, 'r', encoding='utf-8') as f:
            html_content = f.read()
            logger.debug(f"HTML file size: {len(html_content)} bytes")

        # Convert HTML to Markdown
        logger.debug("Converting HTML to Markdown")
        markdown_content = h.handle(html_content)

        # Verify links and images
        logger.debug("Starting link verification")
        markdown_content, results = link_checker.process_content(html_content, markdown_content, md_output)

        # Remove Confluence footer
        logger.debug("Removing Confluence footer")
        markdown_content = remove_confluence_footer(markdown_content)

        # Clean filename before saving
        logger.debug("Checking if filename needs cleanup")
        cleaned_md_output = link_checker.clean_filename(md_output)
        if cleaned_md_output != md_output:
            logger.debug(f"Filename cleaned: {md_output} -> {cleaned_md_output}")
            md_output = cleaned_md_output

        # Fix crosslinks using the filename mapping
        markdown_content = link_checker.fix_crosslinks(markdown_content, md_output)

        # Save the markdown with the correct filename
        logger.debug(f"Saving markdown to: {md_output}")
        with open(md_output, 'w', encoding='utf-8') as f:
            f.write(markdown_content)

        if not os.path.exists(md_output):
            raise FileNotFoundError(f"Output file not created: {md_output}")

        output_size = os.path.getsize(md_output)
        logger.debug(f"Conversion successful. Output file size: {output_size} bytes")
        return True

    except Exception as e:
        logger.error(f"Conversion failed for {html_file}", exc_info=True)
        logger.debug(f"Error details: {str(e)}")
        print_status(f"Failed to convert {os.path.basename(html_file)}", error=True)
        raise

def handle_special_folders(root: str, output_dir: str, config: Config) -> None:
    """Copy attachments and images folders with their contents"""
    logger.debug(f"Handling special folders in {root}")
    try:        
        # Skip if output directory would contain styles
        if config.STYLES_PATH in output_dir.split(os.sep):
            return
        
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

def process_html_files(root: str, files: list, output_dir: str, stats: ConversionStats, config: Config) -> None:
    """Process HTML files in the current directory"""
    link_checker = LinkChecker(config)
    link_checker._build_file_cache()

    html_files = [f for f in files if f.endswith('.html')]
    for filename in html_files:
        stats.processed += 1
        input_file = os.path.join(root, filename)
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

def remove_confluence_footer(markdown_content: str) -> str:
    """Remove the standard Confluence footer from markdown content"""
    # Pattern to match the footer with variable date/time
    footer_pattern = r'\nDocument generated by Confluence on [A-Za-z]+\. \d{1,2}, \d{4} \d{1,2}:\d{2}\n\n\[Atlassian\]\(<https://www\.atlassian\.com/>\)\n*$'
    
    # Remove the footer
    cleaned_content = re.sub(footer_pattern, '', markdown_content)
    return cleaned_content

def main(config: Config, logger: logging.Logger) -> None:
    try:
        logger.debug("=== Starting HTML to Markdown Conversion Process ===")
        logger.debug(f"Python version: {sys.version}")
        logger.debug(f"Input folder: {os.path.abspath(config.INPUT_FOLDER)}")
        logger.debug(f"Output folder: {os.path.abspath(config.OUTPUT_FOLDER)}")

        # Create output folder
        os.makedirs(config.OUTPUT_FOLDER, exist_ok=True)
        logger.debug(f"Output directory structure created: {config.OUTPUT_FOLDER}")

        # Initialize statistics
        stats = ConversionStats()
        print("\n")
        print_status("Scanning files...")
        stats.total = sum(1 for _, _, files in os.walk(config.INPUT_FOLDER) for f in files if f.endswith('.html'))
        logger.debug(f"Found {stats.total} HTML files to process")
        print_status(f"Found {stats.total} HTML files to process")

        # First pass: Handle special folders only
        print_status("Copying special folders (attachments and images)...")
        styles_dir = os.path.join(config.INPUT_FOLDER, config.STYLES_PATH)
        for root, _, _ in os.walk(config.INPUT_FOLDER):
            # Skip styles folder and its contents
            if root == styles_dir or root.startswith(styles_dir):
                continue
            
            rel_path = os.path.relpath(root, config.INPUT_FOLDER)
            output_dir = os.path.join(config.OUTPUT_FOLDER, rel_path)

            # Check if the path ends with 'styles' or contains 'styles/'
            path_parts = rel_path.split(os.sep)
            if config.STYLES_PATH in path_parts:
                logger.debug(f"Skipping styles directory: {output_dir}")
                continue

            os.makedirs(output_dir, exist_ok=True)

            if any(folder in root.split(os.sep) for folder in [config.ATTACHMENTS_PATH, config.IMAGES_PATH]):
                handle_special_folders(root, output_dir, config)

        # Second pass: Process HTML files
        print_status("Processing HTML files...")
        for root, _, files in os.walk(config.INPUT_FOLDER):       
            # Skip styles folder and its contents
            if root == styles_dir or root.startswith(styles_dir):
                continue

            if any(folder in root.split(os.sep) for folder in [config.ATTACHMENTS_PATH, config.IMAGES_PATH, config.STYLES_PATH]):
                continue

            rel_path = os.path.relpath(root, config.INPUT_FOLDER)
            if not rel_path.startswith(config.STYLES_PATH):
                output_dir = os.path.join(config.OUTPUT_FOLDER, rel_path)
                os.makedirs(output_dir, exist_ok=True)

                # Process HTML files
                process_html_files(root, files, output_dir, stats, config)
        
        print("\n")
        print_status("Finalizing and cleaning up...")
        logger.debug("=== Conversion Process Complete ===")
        print("\n")
        stats.print_final_report()

    except Exception as e:
        logger.error("Process failed", exc_info=True)
        print_status(str(e), error=True)
        sys.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', default="in", help="Input folder name")
    parser.add_argument('--output', default="out", help="Output folder name")
    parser.add_argument('--base-url', default="https://confluence.myCompany.com", help="Confluence Base URL")
    parser.add_argument('--rename-all', action='store_true', help="Rename all files with numeric suffixes")
    args = parser.parse_args()

    config = Config.from_args(args)
    logger = setup_logging(config)
    main(config, logger)
