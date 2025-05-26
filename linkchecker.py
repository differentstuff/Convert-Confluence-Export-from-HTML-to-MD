
import requests
import os
import re
import logging
from typing import Set, Tuple, Optional
from bs4 import BeautifulSoup

from config import Config
from attachmentprocessor import AttachmentProcessor

# CONSTANTS REGEX (DO NOT CHANGE)
FILENAME_PATTERN = re.compile(r'^(.+)_(\d+)(\.md)$')
UNDERSCORE_DIGITS_PATTERN = re.compile(r'_\d+$')
LINK_PATTERN = re.compile(r'\[([^\]]+)\]\(<?([^>)]+)>?\)')
URL_PATTERN = re.compile(r'\[([^\]]+)\]\((https?://[^)]+)\)')

class LinkChecker:
    def __init__(self, config: Config, logger: logging.Logger, attachment_processor: AttachmentProcessor) -> None:
        """Setup logging configuration"""
        self.config = config
        self.logger = logger
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        self.checked_urls: Set[str] = set()
        self.input_folder = config.INPUT_FOLDER
        self.input_folder_xml = config.INPUT_FOLDER_XML
        self.output_folder = config.OUTPUT_FOLDER
        self.renamed_files = {}  # Cache for renamed files
        self.filename_mapping = {}  # Cache for renamed files reference
        self.basename_dir_mapping = {}  # Directory-aware mapping for basenames
        self.file_cache = {}     # Cache for file existence checks
        self.attachment_processor = attachment_processor
        self._build_file_cache()
        
    def _build_file_cache(self) -> None:
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
        if url.startswith(self.config.CONFLUENCE_BASE_URL):
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
        for thumbnail in self.config.THUMBNAIL_PATH:
            if thumbnail in src_path:
                return src_path, True, "Thumbnail path"
            
        # Normalize the source path
        rel_path = self.make_relative_path(src_path).replace('/', os.sep)

        # Check output folder first (as files should be copied by now)
        output_path = os.path.normpath(os.path.join(self.output_folder, rel_path))
        self.logger.debug(f"Checking output path: {output_path}")
        if os.path.exists(output_path) and os.path.isfile(output_path):
            self.logger.debug(f"Image found in output folder: {output_path}")
            return rel_path.replace(os.sep, '/'), True, "Local image exists"

        # Try to extract space key from current_file_path
        # The space key might be the first part of current_file_path if it contains directory info
        space_key = None
        if os.sep in current_file_path:
            parts = current_file_path.split(os.sep)
            if len(parts) > 1:
                space_key = parts[0]
                self.logger.debug(f"Extracted space key from path: {space_key}")
                
        # Check if we have a page ID in the src_path
        page_id_match = re.search(rf'/{self.config.ATTACHMENTS_PATH}/(\d+)/', src_path)
        page_id = page_id_match.group(1) if page_id_match else None
        
        # If we have both space_key and page_id, construct a reliable path
        if space_key and page_id:
            rel_path = os.path.join(space_key, self.config.ATTACHMENTS_PATH, page_id, os.path.basename(rel_path))
            #self.logger.debug(f"Constructed reliable path: {rel_path}")
        # Otherwise if we just have basic directory info
        elif os.sep in current_file_path:
            # Get directory of the current file (first part only if it's a space key)
            file_dir = os.path.dirname(current_file_path)
            if file_dir and file_dir != ".":
                rel_path = os.path.join(file_dir, rel_path)
                #self.logger.debug(f"Using file directory for path: {rel_path}")
        
        # Return the path without checking existence - at this point in processing
        # the files may not exist yet, but we want to use the correct relative path
        return rel_path.replace(os.sep, '/'), True, "Path constructed from mapping"
      
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

    def clean_filename(self, md_output: str) -> str:
        """
        Remove numeric suffixes from filename and use proper title from XML if available.
        Falls back to original method if XML checker is not available.

        - If RENAME_ALL_FILES
          - is True: Remove all numeric suffixes, renames numeric filenames to their first header.
          - is False: Only remove when corresponding attachment folder exists

        Example: 'CNC_8355908.md' -> 'CNC.md' (only if '8355908' exists as attachment subfolder)
        Example: '12345.md' -> 'First Header Title.md' (if RENAME_ALL_FILES is True)

        Args:
            md_output: The target markdown file path
        
        Returns:
            The cleaned file path
        """
        self.logger.debug(f"Checking filename for cleanup: '{md_output}'")
        if md_output in self.renamed_files:
            return self.renamed_files[md_output]

        # Get the filename and directory path
        dir_path = os.path.dirname(md_output)
        filename = os.path.basename(md_output)
        base_name, extension = os.path.splitext(filename)

        # Check if filename is purely numeric (excluding extension)
        if self.config.RENAME_ALL_FILES and base_name.isdigit():
            # For numeric filenames, we need to extract the first H1 header from the HTML file
            # This will be done in the convert_html_to_md function
            # For now, we'll just return the original path and handle it later
            return md_output
        
        # Regular expression to match filename_numbers.md pattern
        match = FILENAME_PATTERN.match(filename)

        # Process non-numeric filenames or if header extraction failed
        if not match:
            self.logger.debug(f"No numeric suffix found in filename: '{filename}'")
            return md_output
        
        # At this point, we have a valid match
        base_name, number, extension = match.groups()

        if self.config.RENAME_ALL_FILES:
            # Always rename files that match the pattern
            new_filename = f"{base_name}{extension}"
            new_path = os.path.join(dir_path, new_filename)
            self.logger.debug(f"Renaming '{filename}' to '{new_filename}'")
        else:
            # Check in output directory for attachment folder
            attachment_path = os.path.join(dir_path, self.config.ATTACHMENTS_PATH, number)
            if not (os.path.exists(attachment_path) and os.path.isdir(attachment_path)):
                self.logger.debug(f"No matching attachment folder found for number: {number}")
                return md_output
            new_filename = f"{base_name}{extension}"
            new_path = os.path.join(dir_path, new_filename)
            self.logger.debug(f"Found matching attachment folder. Renaming '{filename}' to '{new_filename}'")
            
        # If the file already exists, we need to handle it
        if os.path.exists(new_path):
            self.logger.warning(f"Target file '{new_filename}' already exists. Keeping original name.")
            return md_output

        try:
            self.renamed_files[md_output] = new_path
            self.logger.info(f"Successfully renamed '{filename}' to '{new_filename}'")
            return new_path
        except OSError as e:
            self.logger.error(f"Failed to rename file '{filename}': {str(e)}")
            return md_output

    def fix_crosslinks(self, markdown_content: str, current_file_path: str) -> str:
        """
        Fix internal links in markdown content.
        Handles numeric suffixes and ensures consistent link formatting.
        """
        self.logger.debug(f"Fixing crosslinks in {current_file_path}")

        # Get the directory of the current file for context
        current_dir = os.path.dirname(os.path.relpath(current_file_path, self.output_folder))

        def process_link(match):
            description = match.group(1)
            link = match.group(2).strip('<>')
            original_link = link  # Store original for logging

            # Skip if it's a web URL or an attachment/image link
            if self.is_web_url(link) or self.config.ATTACHMENTS_PATH in link or self.config.IMAGES_PATH in link:
                return match.group(0)

            # Process internal links
            new_link = link

            # Remove common prefixes
            for prefix in self.config.PREFIXES:
                if new_link.startswith(prefix):
                    #self.logger.debug(f"Link found for prefix {prefix} to remove: {new_link}")
                    new_link = new_link[len(prefix):]
                    # remove URL parameters (everything after '?')
                    if '?' in new_link:
                        new_link = new_link.split('?', 1)[0]
                        # remove URL parameters (everything after '&')
                    if '&' in new_link:
                        new_link = new_link.split('&', 1)[0]
                    self.logger.debug(f"Link changed to: {new_link}")
                    break  # Break only if a prefix match was found

            # Remove Link
            for prefix in self.config.PREFIXES_TO_REMOVE:
                base_url_prefix = self.config.CONFLUENCE_BASE_URL + prefix
                if new_link.startswith(prefix) or new_link.startswith(base_url_prefix):
                    self.logger.debug(f"Link found for prefix {prefix}, {base_url_prefix} to remove: {new_link}")
                    new_link = ""
                    break  # Break only if a prefix match was found

            # Returning empty link if removed
            if new_link == "":
                self.logger.debug(f"Modified Link: {new_link}")
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
                        self.logger.debug(f"Found directory-specific mapping for index: {link_dir}/{basename} -> {new_link}")
                        return self.convert_wikilink(description, new_link)

                    # If no exact match but we're in the same directory, try current directory
                    if current_dir in dir_mappings:
                        new_link = dir_mappings[current_dir]
                        # Extract just the filename if the link is in the same directory
                        new_link = os.path.basename(new_link)
                        self.logger.debug(f"Using current directory mapping for index: {current_dir}/{basename} -> {new_link}")
                        return self.convert_wikilink(description, new_link)
                    
                # Check if we have a mapping for this specific index file
                if full_path in self.filename_mapping:
                    #self.logger.debug(f"Found match for full_path: {full_path}")
                    new_link = self.filename_mapping[full_path]
                    self.logger.debug(f"Replaced index link with directory context: {link} -> {new_link}")
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
                    self.logger.debug(f"Found directory-specific mapping: {current_dir}/{base_name} -> {new_link}")
                    return self.convert_wikilink(description, new_link)

            # Fall back to regular mapping if directory-specific mapping not found
            if new_link in self.filename_mapping:
                #self.logger.debug(f"Found match for new_link: {new_link}")
                new_link = self.filename_mapping[new_link]
                # Check if the target is in the same directory
                target_dir = os.path.dirname(new_link)
                if target_dir == current_dir or not target_dir:
                    new_link = os.path.basename(new_link)
                self.logger.debug(f"Direct mapping found for: {original_link} -> {new_link}")
                return self.convert_wikilink(description, new_link)

            # Try with .md extension explicitly
            md_link = f"{base_name}.md"
            if md_link in self.filename_mapping:
                #self.logger.debug(f"Found match for md_link: {md_link}")
                new_link = self.filename_mapping[md_link]
                # Check if the target is in the same directory
                target_dir = os.path.dirname(new_link)
                if target_dir == current_dir or not target_dir:
                    new_link = os.path.basename(new_link)
                self.logger.debug(f"MD mapping found for: {original_link} -> {new_link}")
                return self.convert_wikilink(description, new_link)

            # Try with .html extension explicitly
            html_link = f"{base_name}.html"
            if html_link in self.filename_mapping:
                #self.logger.debug(f"Found match for html_link: {html_link}")
                new_link = self.filename_mapping[html_link]
                # Check if the target is in the same directory
                target_dir = os.path.dirname(new_link)
                if target_dir == current_dir or not target_dir:
                    new_link = os.path.basename(new_link)
                self.logger.debug(f"HTML mapping found for: {original_link} -> {new_link}")
                return self.convert_wikilink(description, new_link)

            # Try with just the base name (no extension)
            if base_name in self.filename_mapping:
                #self.logger.debug(f"Found match for base_name: {base_name}")
                new_link = self.filename_mapping[base_name]
                # Check if the target is in the same directory
                target_dir = os.path.dirname(new_link)
                if target_dir == current_dir or not target_dir:
                    new_link = os.path.basename(new_link)
                self.logger.debug(f"Base name mapping found for: {original_link} -> {new_link}")
                return self.convert_wikilink(description, new_link)
            
            # If we get here, no mapping was found
            self.logger.debug(f"No mapping found for link: {original_link}")
            
            # Keep page IDs unchanged but ensure they have .md extension
            if base_name.isdigit():
                self.logger.debug(f"Numeric link found: {base_name}")
                if f"{base_name}.md" in self.filename_mapping:
                    self.logger.debug(f"Found match for base_name: {base_name}")
                    new_link = self.filename_mapping[f"{base_name}.md"]
                    # Check if the target is in the same directory
                    target_dir = os.path.dirname(new_link)
                    if target_dir == current_dir or not target_dir:
                        new_link = os.path.basename(new_link)
                    self.logger.debug(f"Found mapping for numeric ID: {base_name}.md -> {new_link}")
                    return self.convert_wikilink(description, new_link)
                elif f"{base_name}.html" in self.filename_mapping:
                    self.logger.debug(f"Found match for {base_name}.html: {base_name}.html")
                    new_link = self.filename_mapping[f"{base_name}.html"]
                    # Check if the target is in the same directory
                    target_dir = os.path.dirname(new_link)
                    if target_dir == current_dir or not target_dir:
                        new_link = os.path.basename(new_link)
                    self.logger.debug(f"Found mapping for numeric ID: {base_name}.html -> {new_link}")
                    return self.convert_wikilink(description, new_link)
                else:
                    self.logger.debug(f"No mapping found for numeric ID: {base_name}")
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

            self.logger.debug(f"Using default link format for: {original_link} -> {base_name}.md")
            base_name = base_name + ".md"
            return self.convert_wikilink(description, base_name)

        # Process link with as regex
        return LINK_PATTERN.sub(process_link, markdown_content)

    def convert_wikilink(self, description: Optional[str], link: str, is_embedded: bool = False) -> str:
        """
        Convert a link to the appropriate format based on configuration and link type.
        
        Args:
        description: The link text/description (optional)
        link: The URL or path
        is_embedded: Whether the link is an embedded link (default is False)
        
        Returns:
        Formatted link in wiki or markdown format
        """
        if link.startswith(("http://", "https://", "ftp://")):
            # Keep external links in standard markdown format
            if is_embedded:
                return f"![{description or link}]({link})"
            else:
                return f"[{description or link}]({link})"

        if link.startswith("file://"):
            # Fix protocol if needed (ensure three slashes)
            if not link.startswith("file:///"):
                link = "file:///" + link[7:]

            # Get the path part and handle formatting
            path_part = link[8:]  # Remove file:///
            
            # URL encode only spaces (leave other characters untouched)
            path_part = path_part.replace(" ", "%20")
            path_part = path_part.replace("\\", "/")  # Replace all backslashes with forward slashes

            # Prepend double backslash to UNC Path if config allows
            if self.config.FILESERVER_REPLACEMENT_ENABLED and path_part.startswith(self.config.FILESERVER_INDICATOR):
                self.logger.debug(f"Converting to UNC path: {path_part}")
                path_part = path_part.replace("/", "\\")  # normalize single forward slashes to single backslashes
                path_part = "\\\\" + path_part  # prepend double backslash for UNC path

            normalized_link = f"file:///{path_part}"
            self.logger.debug(f"Normalized link: {normalized_link}")

            # Create the markdown link
            if description:
                return f"[{description}]({normalized_link})"
            else:
                # Use original path as description if none provided
                return f"[{path_part}]({normalized_link})"

        # Handle internal links based on configuration
        if self.config.USE_WIKI_LINKS:
            # Return Wikilink Link
            if self.config.USE_ESCAPING_FOR_WIKI_LINKS:
                # Escape "|" as "\\|" to avoid broken tables in MD content
                if description:
                    if is_embedded:
                        return f"![[{link}\\|{description}]]"
                    else:
                        return f"[[{link}\\|{description}]]"
                else:
                    if is_embedded:
                        return f"![[{link}]]"
                    else:
                        return f"[[{link}]]"
            else:
                if description:
                    if is_embedded:
                        return f"![[{link}|{description}]]"
                    else:
                        return f"[[{link}|{description}]]"
                else:
                    if is_embedded:
                        return f"![[{link}]]"
                    else:
                        return f"[[{link}]]"
        else:
            # Return regular MD Link with angle brackets for internal links
            if description:
                if is_embedded:
                    return f"![{description}](<{link}>)"
                else:
                    return f"[{description}](<{link}>)"
            else:
                if is_embedded:
                    return f"![{link}](<{link}>)"
                else:
                    return f"[{link}](<{link}>"

    def find_and_rename_attachments(self, page_id: str) -> dict:
        """
        Find attachments for a page and create a mapping for renaming.

        Args:
            page_id: The ID of the page

        Returns:
            dict: Mapping of original attachment filenames to new filenames
        """
        attachment_mapping = {}
        
        # Ensure page_id is a string
        page_id_str = str(page_id)

        # Check if we need to map to a newer version of the page
        if hasattr(self.attachment_processor, 'xml_processor') and \
           hasattr(self.attachment_processor.xml_processor, 'page_id_mapping') and \
           page_id_str in self.attachment_processor.xml_processor.page_id_mapping:
            mapped_id = self.attachment_processor.xml_processor.page_id_mapping[page_id_str]
            if mapped_id != page_id_str:
                self.logger.debug(f"Mapped old page ID {page_id_str} to newest version {mapped_id}")
                page_id_str = mapped_id

        # Directly get attachments for this page from the XmlProcessor
        attachments = []
        if hasattr(self.attachment_processor, 'xml_processor'):
            try:
                attachments = self.attachment_processor.xml_processor.get_attachments_by_page_id(page_id_str)
                self.logger.debug(f"Found {len(attachments)} attachments in XML data for page {page_id_str}")
            except Exception as e:
                # Log potential errors during XML data retrieval
                self.logger.error(f"Error retrieving attachments from XmlProcessor for page {page_id_str}: {e}")
                attachments = [] # Ensure attachments is empty on error
        else:
            self.logger.warning("XmlProcessor not available on AttachmentProcessor, cannot get attachments from XML.")
            attachments = []

        # If no attachments were found via the XmlProcessor, log it and proceed.
        if not attachments:
            self.logger.info(f"No attachments found in XML data for page {page_id_str}. Mapping will be empty.")

        # Process the attachments found (primarily from XML)
        for attachment in attachments:
            att_id = attachment.get("id")
            original_title = attachment.get("title", "") # This is the title from XML

            if not att_id or not original_title:
                self.logger.warning(f"Skipping attachment with missing ID or title for page {page_id_str}: {attachment}")
                continue

            # Map the attachment ID to the sanitized filename
            attachment_mapping[att_id] = original_title
            self.logger.debug(f"Mapped attachment ID from XML: {att_id} -> {original_title}")

        if not attachment_mapping and attachments:
             # This case might indicate all attachments had missing IDs/titles or sanitization issues
             self.logger.warning(f"Processed {len(attachments)} attachments from XML for page {page_id_str}, but mapping is empty (check warnings above).")
        elif not attachment_mapping:
             # This confirms no attachments were found or processed successfully
             self.logger.debug(f"No attachment mappings created for page {page_id_str} (no attachments found in XML).")


        return attachment_mapping

    def process_images(self, html_content: str, markdown_content: str) -> str:
        """
        Process content and verify all links
        
        Args:
        html_content: The HTML content to process
        markdown_content: The markdown content to process
        
        Returns:
        Updated markdown content with processed links
        """
        # Extract and verify image sources from HTML
        image_sources = self.extract_image_src(html_content)
        for img in image_sources:
            src = img['src']
            description = img['description']

            # Skip empty sources
            if not src:
                self.logger.warning("Skipping empty image source")
                continue

            if self.is_web_url(src):
                url, is_valid, status = self.verify_web_url(src)
                if not is_valid:
                    self.logger.warning(f"Image verification failed but keeping link: {url} - {status}")

                # Create the correct markdown image link regardless of validity, but only if URL is not empty
                if url:
                    new_link = self.convert_wikilink(description, url, is_embedded=True)
                    old_pattern = f'\\[.*?\\]\\(<{re.escape(src)}>\\)(?: \\[BROKEN IMAGE\\])?(?: \\(image/[^)]+\\))?'
                    markdown_content = re.sub(old_pattern, new_link, markdown_content)
                else:
                    self.logger.warning(f"Cannot create link for empty URL, original src: {src}")

        # Process web URLs in markdown
        for match in re.finditer(URL_PATTERN, markdown_content):
            url = match.group(2)
            
            # Skip empty URLs
            if not url:
                self.logger.warning("Skipping empty URL in markdown")
                continue

            if url not in self.checked_urls:
                _, is_valid, status = self.verify_web_url(url)
                if not is_valid:
                    self.logger.warning(f"Web URL verification failed but keeping link: {url} - {status}")

        return markdown_content

    def process_invalid_video_links(self, html_content: str, markdown_content: str) -> str:
        """
        Process video links in markdown content
        """
        # Use BeautifulSoup for HTML parsing (needed for INVALID_VIDEO_INDICATOR detection)
        soup = BeautifulSoup(html_content, 'html.parser')
        videos = []

        # Find all video elements in the HTML
        for video in soup.find_all('video'):
            src = video.get('src', '')
            if src:
                # Extract filename from src path
                filename = src.split('/')[-1]
                
                # Try to extract attachment ID from the src
                attachment_id = None
                page_id = None
                
                # Remove the 'download/' prefix from the src
                src = re.sub(r'^(/?)download/', '', src)
                # Look for patterns like attachments/PAGE_ID/ATTACHMENT_ID or attachments/PAGE_ID/ATTACHMENT_NAME
                id_match = re.search(r'attachments/(\d+)/(\d+)', src)
                if id_match:
                    page_id = id_match.group(1)
                    attachment_id = id_match.group(2)
                else:
                    # If no ID match, try to extract filename
                    filename_match = re.search(r'attachments/(\d+)/([^/]+)$', src)
                    if filename_match:
                        page_id = filename_match.group(1)
                        filename = filename_match.group(2)

                videos.append({
                    'src': src,
                    'filename': filename,
                    'attachment_id': attachment_id,
                    'page_id': page_id
                })
        
        # Early return if no videos found (optimization)
        if len(videos) == 0:
            self.logger.debug("No videos found in markdown body")
            return markdown_content

        # Replace the placeholder text with proper wiki links
        for video in videos:
            if self.config.INVALID_VIDEO_INDICATOR in markdown_content:
                # Try to find the attachment in XML data
                link_path = None
                
                # Method 1: Try by attachment ID if available
                if video['attachment_id']:
                    self.logger.debug(f"Processing video attachment ID: {video['attachment_id']}")
                    attachment = self.attachment_processor.xml_processor.get_attachment_by_id(video['attachment_id'])
                    if attachment:
                        parent_page_id = attachment.get('containerContent_id')
                        space_key = self.attachment_processor.xml_processor.get_space_key_by_page_id(parent_page_id)
                        link_path = f"{space_key}/attachments/{parent_page_id}/{attachment['title']}"
                        self.logger.debug(f"Found video attachment by ID: {video['attachment_id']} -> {link_path}")
                
                # Method 2: If no ID or not found, try to match by filename
                if not link_path:
                    # Look for the attachment by filename across all attachments
                    filename = video['filename']
                    decoded_filename = self.attachment_processor.xml_processor._sanitize_filename(filename)
                    
                    # Try to get page ID from the video source if available
                    if video['page_id']:
                        self.logger.debug(f"Processing video page ID '{video['page_id']}' with filename: {filename}")
                        # First check attachments on the source page
                        attachments = self.attachment_processor.xml_processor.get_attachments_by_page_id(video['page_id'])
                        for att in attachments:
                            # Compare filename (case-insensitive) to handle encoding differences
                            if att.get('title', '').lower() == filename.lower() or att.get('title', '').lower() == decoded_filename.lower():
                                parent_page_id = att.get('containerContent_id')
                                space_key = self.attachment_processor.xml_processor.get_space_key_by_page_id(parent_page_id)
                                link_path = f"{space_key}/attachments/{parent_page_id}/{att['title']}"
                                self.logger.debug(f"Found video attachment by filename on source page: {filename} -> {link_path}")
                                break
                    
                    # If still not found, try to find by ID in the filename
                    if not link_path:
                        self.logger.debug(f"No link path found, attempting to find ID in filename: {filename}")
                        id_match = re.search(r'/(\d+)(?:\.\w+)?$', filename)
                        if id_match:
                            potential_id = id_match.group(1)
                            attachment = self.attachment_processor.xml_processor.get_attachment_by_id(potential_id)
                            if attachment:
                                parent_page_id = attachment.get('containerContent_id')
                                space_key = self.attachment_processor.xml_processor.get_space_key_by_page_id(parent_page_id)
                                link_path = f"{space_key}/attachments/{parent_page_id}/{attachment['title']}"
                                self.logger.debug(f"Found video attachment by ID in filename: {potential_id} -> {link_path}")
                
                # Method 3: If still not found, use the relative path from the source
                if not link_path:
                    self.logger.debug(f"No link path found, attempting to use relative path from source: '{video['src']}'")
                    link_path = self.make_relative_path(video['src'])
                    self.logger.debug(f"No attachment found, using source path: {link_path}")
                
                # Create the wiki link and replace the indicator
                wiki_link = self.convert_wikilink(video['filename'], link_path)
                markdown_content = markdown_content.replace(self.config.INVALID_VIDEO_INDICATOR, wiki_link, 1)
                self.logger.debug(f"Replaced video indicator with link: {wiki_link}")

        return markdown_content

    def process_attachment_links(self, markdown_content: str) -> str:
        """
        Process attachment links in markdown content using a reliable link finder approach.
        
        Args:
        markdown_content: The markdown content to process
        
        Returns:
        Updated markdown content with processed attachment links
        """
        self.logger.debug("Processing attachment links in markdown content")
        
        # First find all download paths - the most reliable identifier
        download_pattern = re.compile(r'download/attachments/(\d+)/([^?]+)(?:\?[^>)]*)?')
        
        # Pattern for direct attachment links
        attachment_pattern = re.compile(r'!\[(.*?)\]\((attachments/\d+/\d+\.[^)]+)\)|\[(.*?)\]\((attachments/\d+/\d+\.[^)]+)\)')

        def replace_attachments_link(match):
            # Determine if this is an image/embedded link (images never use a description)
            is_image_link = match.group(1) is not None

            # Extract description and link based on which group matched
            if is_image_link:
                description = match.group(1)
                link = match.group(2)
            else:
                description = match.group(3)
                link = match.group(4)
            
            original_link = link
            
            # Extract attachment ID from the link
            attachment_match = re.search(r'attachments/(\d+)/(\d+)', link)
            if attachment_match:
                page_id = attachment_match.group(1)
                attachment_id = attachment_match.group(2)

                # Method 1: Try by direct attachment ID lookup
                attachment = self.attachment_processor.xml_processor.get_attachment_by_id(attachment_id)
                if attachment:
                    # Get the actual parent page ID from the attachment data
                    parent_page_id = attachment.get('containerContent_id')
                    # Get the space key for the parent page
                    space_key = self.attachment_processor.xml_processor.get_space_key_by_page_id(parent_page_id)
                    
                    # Construct the new link path
                    new_link = f"{space_key}/attachments/{parent_page_id}/{attachment['title']}"
                    self.logger.debug(f"Found attachment by ID: {attachment_id}. Replacing link: {original_link} -> {new_link}")
                else:
                    # Method 2: Try to find by filename in the page's attachments
                    filename = os.path.basename(link)
                    decoded_filename = self.attachment_processor.xml_processor._sanitize_filename(filename)
                    
                    # First check attachments on the source page
                    attachments = self.attachment_processor.xml_processor.get_attachments_by_page_id(page_id)
                    attachment_found = False
                    
                    for att in attachments:
                        # Compare filename (case-insensitive) to handle encoding differences
                        if att.get('title', '').lower() == filename.lower() or att.get('title', '').lower() == decoded_filename.lower():
                            parent_page_id = att.get('containerContent_id')
                            space_key = self.attachment_processor.xml_processor.get_space_key_by_page_id(parent_page_id)
                            new_link = f"{space_key}/attachments/{parent_page_id}/{att['title']}"
                            self.logger.debug(f"Found attachment by filename on source page: {filename} -> {new_link}")
                            attachment_found = True
                            break
                    
                    # Method 3: If still not found, try to find by ID in the filename
                    if not attachment_found:
                        id_match = re.search(r'/(\d+)(?:\.\w+)?$', filename)
                        if id_match:
                            potential_id = id_match.group(1)
                            attachment = self.attachment_processor.xml_processor.get_attachment_by_id(potential_id)
                            if attachment:
                                parent_page_id = attachment.get('containerContent_id')
                                space_key = self.attachment_processor.xml_processor.get_space_key_by_page_id(parent_page_id)
                                new_link = f"{space_key}/attachments/{parent_page_id}/{attachment['title']}"
                                self.logger.debug(f"Found attachment by ID in filename: {potential_id} -> {new_link}")
                                attachment_found = True
                    
                    # Method 4: If still not found, use the original link structure but with space key
                    if not attachment_found:
                        space_key = self.attachment_processor.xml_processor.get_space_key_by_page_id(page_id)
                        new_link = f"{space_key}/{original_link}"
                        self.logger.debug(f"No attachment found, using original link with space key: {new_link}")
                
                # Handle embedded vs. non-embedded links differently
                if is_image_link:
                    return self.convert_wikilink(description, new_link, is_embedded=True)
                else:
                    return self.convert_wikilink(description, new_link)
            else:
                self.logger.debug(f"Link does not contain attachment ID: {original_link}")
                return match.group(0)

        # Find all matches and store their positions
        downloads = []
        for match in download_pattern.finditer(markdown_content):
            attachment_page_id = match.group(1)
            filename = match.group(2)
            sanitized_filename = self.attachment_processor.xml_processor._sanitize_filename(filename)
            downloads.append((match.start(), match.end(), attachment_page_id, sanitized_filename))

        if downloads:
            # Process each download link by finding its surrounding link structure
            processed_content = markdown_content
            replacements = []
            
            for start_pos, end_pos, attachment_page_id, filename in downloads:
                # Look for the complete link pattern around our match
                before_text = processed_content[:start_pos]
                after_text = processed_content[end_pos:]
                
                # Try to find the beginning of the link
                link_start = -1
                # Check for wiki links [[...]] pattern first
                wiki_start = before_text.rfind("[[")
                if wiki_start > -1 and "]]" in before_text[wiki_start:]:
                    link_start = wiki_start
                else:
                    # Check for standard markdown links
                    bracket_start = before_text.rfind("[")
                    if bracket_start > -1 and "](" in before_text[bracket_start:]:
                        link_start = bracket_start
                
                if link_start == -1:
                    #self.logger.warning(f"Could not find opening of link for: {filename}")
                    continue
                
                # Find the end of the link (closing parenthesis)
                close_paren_pos = after_text.find(")")
                if close_paren_pos == -1:
                    self.logger.warning(f"Could not find closing parenthesis for: {filename}")
                    continue
                
                # Calculate full link boundaries
                link_end = end_pos + close_paren_pos + 1
                
                # Extract the full link
                full_link = processed_content[link_start:link_end]

                # Check if this is a complex nested structure (like [![...](...)](/download/...))
                is_complex_nested = full_link.startswith('![') and '[' in full_link[2:10]
                
                # Get space key for page
                space_key = self.attachment_processor.xml_processor.get_space_key_by_page_id(attachment_page_id)
                
                # Decode filename using the XML processor's function
                decoded_filename = self.attachment_processor.xml_processor._sanitize_filename(filename)
    
                # Look for the attachment in XML data by filename and page ID
                attachment = None
                attachments = self.attachment_processor.xml_processor.get_attachments_by_page_id(attachment_page_id)
                for att in attachments:
                    if att.get('title', '') == filename:
                        self.logger.debug(f"Attachment found by filename: {filename}")
                        attachment = att
                        break
                    if att.get('title', '') == decoded_filename:
                        self.logger.debug(f"Attachment found by decoded filename: {decoded_filename}")
                        attachment = att
                        break
                
                # If not found by ID, try filename lookup
                if not attachment:
                    self.logger.debug(f"Attachment not found by page ID: {attachment_page_id}, attempting name lookup: '{filename}'")
                    attachment = self.attachment_processor.xml_processor.get_attachment_by_filename(filename)

                if attachment:
                    #self.logger.debug(f"Found attachment: {attachment}")
                    attachment_title = attachment['title']  # Use the sanitized filename from the attachment
                    new_link = f"{space_key}/attachments/{attachment_page_id}/{attachment_title}"
                    self.logger.debug(f"Found attachment filename: {filename}")
                else:
                    # Create the new clean link path with the original filename
                    new_link = f"{space_key}/attachments/{attachment_page_id}/{decoded_filename or filename}"
                    self.logger.debug(f"No attachment found, using original filename: {decoded_filename or filename}")

                # Determine if this is a special link that should have no description
                is_thumbnail = "rest/documentConversion/latest/conversion/thumbnail" in full_link
                
                # Extract description if present
                if "[[" in full_link and "]]" in full_link:
                    # Wiki-style link
                    desc_match = re.search(r'\[\[(.*?)\]\]', full_link)
                    description = desc_match.group(1) if desc_match else ""
                else:
                    # Regular markdown link
                    desc_match = re.search(r'\[(.*?)\]', full_link)
                    description = desc_match.group(1) if desc_match else ""
                
                # Determine if description should be ignored
                ignore_description = is_thumbnail or any([
                    'rest/documentConversion' in description,
                    'download/resources' in description,
                    description.strip() in ['![]', '[]'],
                    'thumbnail' in description.lower(),
                    '![' in description  # Nested image in description
                ])
                
                # Create replacement based on link type
                if ignore_description:
                    replacement = f"[[{new_link}]]"
                    self.logger.debug(f"Replacing with simple link: {replacement}")
                else:
                    # Determine if this is an image/embedded link
                    is_embedded = '!' in full_link[:2]  # Check if ! appears in the first 2 characters
                    
                    # For complex nested structures, always use a simple embedded link
                    if is_complex_nested:
                        replacement = f"![[{new_link}]]"
                        self.logger.debug(f"Replacing complex nested structure with simple embedded link: {replacement}")
                    else:
                        replacement = self.convert_wikilink(description, new_link, is_embedded=is_embedded)
                        self.logger.debug(f"Replacing with described link: {replacement}")
                
                # Store replacement for later application
                replacements.append((link_start, link_end, replacement))
            
            # Apply all replacements in reverse order to avoid index shifting
            for start, end, replacement in sorted(replacements, key=lambda x: x[0], reverse=True):
                # Extract the text being replaced for analysis
                text_to_replace = processed_content[start:end]
                
                # Log more details about what we're replacing
                #self.logger.debug(f"Replacing text with '{replacement}'")
                
                # Check if there's an extra opening bracket right before our replacement
                if start > 1:  # Need at least 2 characters before
                    char_before1 = processed_content[start-1]
                    char_before2 = processed_content[start-2]
                    
                    # Check for the pattern '[!' before the replacement
                    if char_before2 == '[' and char_before1 == '!':
                        # This is an embedded link pattern that wasn't fully captured
                        # Include both characters by adjusting the start position
                        start -= 2
                        # Make sure the replacement is an embedded link
                        if not replacement.startswith('![['):
                            replacement = '!' + replacement
                        #self.logger.debug(f"Adjusted start position to include '[!' before: '{replacement}'")
                    # Also check for just a single '[' before the replacement
                    elif char_before1 == '[' and not text_to_replace.startswith('['):
                        # Include the extra bracket by adjusting the start position
                        start -= 1
                        #self.logger.debug(f"Adjusted start position to include '[' before: '{replacement}'")

                processed_content = processed_content[:start] + replacement + processed_content[end:]
        else:
            processed_content = markdown_content
        
        # Process all direct attachment links after the download links
        processed_content = attachment_pattern.sub(replace_attachments_link, processed_content)
        
        return processed_content
