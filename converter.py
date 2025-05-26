import os
import io
import sys
import logging
import re
import argparse
import html2text
from bs4 import BeautifulSoup

from attachmentprocessor import AttachmentProcessor
from linkchecker import LinkChecker
from xmlprocessor import XmlProcessor
from conversionstats import ConversionStats
from config import Config, load_config
from confluencetaghandler import convert_custom_tags_to_html

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
UNDERSCORE_DIGITS_PATTERN = re.compile(r'_\d+$')

def parse_args() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', default="input", help="Input folder name for HTML")
    parser.add_argument('--input-xml', default="input-xml", help="Input folder name for XML")
    parser.add_argument('--output', default="output", help="Output folder name")
    parser.add_argument('--base-url', default="https://confluence.myCompany.com", help="Confluence Base URL")
    parser.add_argument('--rename-all', action='store_true', help="Rename all files with numeric suffixes")
    parser.add_argument('--use-underscore', action='store_true', help="Replace spaces with underscores in filenames")
    parser.add_argument('--debug-link-mapping', action='store_true', help="Write all Link mappings found in log file for debug")
    return parser.parse_args()

def setup_logging(config: Config) -> logging.Logger:
    """Setup logging configuration"""
    logger = logging.getLogger(config.LOG_PATH_NAME)
    logger.setLevel(getattr(logging, config.LOG_LEVEL_GENERAL.upper()))
    
    # File handler
    file_handler = logging.FileHandler(config.LOG_FILE, encoding='utf-8')
    file_handler.setLevel(getattr(logging, config.LOG_LEVEL_FILES.upper()))
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - [%(funcName)s] - %(message)s'))

    # Console handler
    console_handler = logging.StreamHandler(io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8'))
    console_handler.setLevel(getattr(logging, config.LOG_LEVEL_CONSOLE.upper()))
    console_handler.setFormatter(logging.Formatter('%(message)s'))

    # Setup logger
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger

def print_status(message: str, error: bool=False, log_only: bool=False) -> None:
    """Print user-friendly messages to console"""
    if not log_only:
        if error:
            print(f"Error: {message}", file=sys.stderr)
        else:
            print(message)
    logger.error(message) if error else logger.info(message)

def _is_special_folder(path: str, config: Config) -> bool:
    """Check if a path contains any special folder names"""
    special_folders = [config.ATTACHMENTS_PATH, config.IMAGES_PATH, config.STYLES_PATH]
    return any(folder in path.split(os.sep) for folder in special_folders)

def _get_special_folder_type(path: str, config: Config) -> str:
    """Determine which type of special folder this is"""
    path_parts = path.split(os.sep)
    if config.STYLES_PATH in path_parts:
        return "styles"
    elif config.ATTACHMENTS_PATH in path_parts:
        return "attachments"
    elif config.IMAGES_PATH in path_parts:
        return "images"
    return None

def _count_html_files(input_folders: list, config: Config) -> int:
    """Count HTML files excluding special folders"""
    total_count = 0
    for input_folder in input_folders:
        for root, _, files in os.walk(input_folder):
            # Skip special folders when counting
            if _is_special_folder(root, config):
                continue

            # Count HTML files in this directory
            html_files = [f for f in files if f.endswith('.html')]
            total_count += len(html_files)

    return total_count

def _process_html_files(root: str, files: list, output_dir: str, config: Config, link_checker: LinkChecker) -> None:
    """Convert HTML files to Markdown and collect filename mappings"""
    html_files = [f for f in files if f.endswith('.html')]

    # Log all HTML files found in this directory
    logger.info(f"Found {len(html_files)} HTML files in {root}")
    
    for filename in html_files:
        input_file = os.path.join(root, filename)
        logger.debug(f"Processing HTML file: {input_file}")

        # Check if file should be skipped (e.g., in special folders)
        if _is_special_folder(input_file, config):
            logger.info(f"Skipping file in special folder: {input_file}")
            link_checker.attachment_processor.xml_processor.stats.skip_file("Converting")
            continue

        link_checker.attachment_processor.xml_processor.stats.processed += 1
        md_output_name = os.path.join(output_dir, filename[:-5] + ".md")

        logger.info(f"Processing file {link_checker.attachment_processor.xml_processor.stats.processed}/{link_checker.attachment_processor.xml_processor.stats.total}: {filename}")

        try:
            if _convert_html_to_md(input_file, md_output_name, link_checker):
                link_checker.attachment_processor.xml_processor.stats.success += 1
            else:
                link_checker.attachment_processor.xml_processor.stats.failure += 1
        except Exception as e:
            logger.error(f"Failed to convert {filename}: {str(e)}")
            link_checker.attachment_processor.xml_processor.stats.failure += 1

        link_checker.attachment_processor.xml_processor.stats.update_progress()

    # Update phase stats after processing
    link_checker.attachment_processor.xml_processor.stats.update_phase_stats()

def _preprocess_tables(html_content):
    """Enhanced preprocessing that preserves cell content"""
    soup = BeautifulSoup(html_content, 'html.parser')

    for table in soup.find_all('table'):
        # Remove any table attributes that might confuse parsers
        table.attrs = {}

        # Handle nested tables first (major cause of broken tables)
        nested_tables = table.find_all('table')
        for nested in nested_tables:
            # Convert nested table to simple text with separators
            nested_text = []
            for nested_row in nested.find_all('tr'):
                cells = [cell.get_text(strip=True) for cell in nested_row.find_all(['td', 'th'])]
                if cells:
                    nested_text.append(' | '.join(cells))

            if nested_text:
                replacement = soup.new_tag('div')
                replacement.string = '\n'.join(nested_text)
                nested.replace_with(replacement)

        # Ensure proper table structure
        if not table.find('tbody'):
            tbody = soup.new_tag('tbody')
            # Move all tr elements to tbody (except those in thead)
            thead = table.find('thead')
            for tr in table.find_all('tr', recursive=False):
                if not thead or tr.parent != thead:
                    tr.extract()
                    tbody.append(tr)
            table.append(tbody)

        # Fix header structure
        if not table.find('thead'):
            tbody = table.find('tbody')
            if tbody and tbody.find('tr'):
                first_row = tbody.find('tr')
                # Check if first row looks like a header (has th tags or bold text)
                has_th = bool(first_row.find('th'))
                has_bold = bool(first_row.find(['b', 'strong']))

                if has_th or has_bold:
                    thead = soup.new_tag('thead')
                    first_row.extract()
                    thead.append(first_row)
                    table.insert(0, thead)

                    # Convert all cells in header to th
                    for cell in first_row.find_all(['td', 'th']):
                        cell.name = 'th'

        # Fix empty cells and normalize structure WITHOUT touching content
        for row in table.find_all('tr'):
            cells = row.find_all(['td', 'th'])

            for cell in cells:
                # Remove problematic attributes but keep content intact
                cell.attrs = {}

                # Handle ONLY truly empty cells (no content at all)
                if not cell.get_text(strip=True) and not cell.find_all():
                    cell.string = " "
                # DO NOT modify cells that have content - leave them completely alone

        # Ensure all rows have the same number of columns
        rows = table.find_all('tr')
        if rows:
            max_cols = max(len(row.find_all(['td', 'th'])) for row in rows)

            for row in rows:
                cells = row.find_all(['td', 'th'])
                current_cols = len(cells)

                # Add missing cells
                while current_cols < max_cols:
                    cell_type = 'th' if row.parent and row.parent.name == 'thead' else 'td'
                    empty_cell = soup.new_tag(cell_type)
                    empty_cell.string = " "
                    row.append(empty_cell)
                    current_cols += 1

        # Remove any remaining problematic elements within tables (but not in cells)
        for element in table.find_all(['script', 'style', 'noscript']):
            element.decompose()

        # Handle colspan/rowspan by simplifying them
        for cell in table.find_all(['td', 'th']):
            if cell.get('colspan') or cell.get('rowspan'):
                # For now, just remove these attributes
                if 'colspan' in cell.attrs:
                    del cell.attrs['colspan']
                if 'rowspan' in cell.attrs:
                    del cell.attrs['rowspan']

    return str(soup)

def _convert_html_to_markdown(html_content):
    """Convert HTML to Markdown"""
    try:
        logger.debug("Starting HTML to Markdown conversion")

        # Configure html2text
        h = html2text.HTML2Text()
        h.ignore_links = False
        h.ignore_images = False
        h.ignore_tables = False
        h.body_width = 0
        h.protect_links = True
        h.unicode_snob = True
        h.mark_code = True

        # Enhanced table settings
        h.pad_tables = True
        h.single_line_break = False
        h.wrap_links = False
        h.wrap_list_items = False
        h.escape_all = False
        h.bypass_tables = False
        h.ignore_emphasis = False
        h.skip_internal_links = False
        h.decode_errors = 'ignore'
        h.default_image_alt = ''

        processed_html = _preprocess_tables(html_content)

        # convert and return
        return h.handle(processed_html)

    except Exception as e:
        logger.error(f"Failed to convert HTML to Markdown: {e}")
        raise Exception(f"HTML to Markdown conversion failed: {e}")

def _convert_html_to_md(html_file: str, md_output_name: str, link_checker: LinkChecker) -> bool:
    """
    Convert HTML to Markdown with intelligent filename handling.

    Args:
        html_file: Path to the HTML file to convert
        md_output_name: Target path for the Markdown output
        link_checker: LinkChecker instance for managing filename mappings

    Returns:
        bool: True if conversion was successful, False otherwise
    """
    try:
        logger.info(f"Starting conversion of {html_file}")

        # Extract page ID and name from filename
        filename = os.path.basename(html_file)

        # Skipping original index html file
        if filename == "index.html":
            logger.debug("Skipping original 'index.html' file. Index will be replaced by actual Homepage.")
            return True

        # Read HTML content
        with open(html_file, 'r', encoding='utf-8') as f:
            html_content = f.read()
        logger.debug(f"HTML file size: {len(html_content)} bytes")

        # Convert HTML to Markdown
        try:
            logger.debug("Converting HTML to Markdown")
            markdown_content = _convert_html_to_markdown(html_content)
        except Exception as e:
            logger.error(f"Failed to convert {filename}: {str(e)}")
            return False

        # Extract page ID using the XML processor
        page_id = link_checker.attachment_processor.xml_processor.get_page_id_by_filename(filename, md_output_name)

        # Check if it's the new index file
        space_key = os.path.basename(os.path.dirname(md_output_name))
        space_info = link_checker.attachment_processor.xml_processor.get_space_by_key(space_key)
        homePageId = space_info["homePageId"]
        is_new_index = homePageId == page_id
        if is_new_index:
            logger.debug("New index file detected.")

        # Get filename using XML data
        logger.debug(f"Attempting to get clean name for page '{filename}' from ID: '{page_id}'")

        page_title = link_checker.attachment_processor.xml_processor.get_page_title_by_id(page_id)
        logger.debug(f"Found new page title: '{page_title}'")
        final_md_output_name = f"{page_title}.md"

        # Remove header link list (except for index files)
        if is_new_index:
            logger.debug(f"Removing embedded icon in home link for: '{final_md_output_name}'")
            markdown_content = _remove_embedded_icon_in_home_link(markdown_content)

        # For all files
        logger.debug(f"Removing link list for: '{final_md_output_name}'")
        markdown_content = _remove_link_list_on_top(markdown_content)
        
        if page_title is not None:
            logger.debug(f"Matching h1 header text with new filename: '{page_title}'")
            markdown_content = _replace_first_header_name(markdown_content, page_title)
        else:
            logger.debug(f"Filename not found in cache - skipping ID: '{page_id}'")

        # Process video links
        logger.debug("Processing video links")
        markdown_content = link_checker.process_invalid_video_links(html_content, markdown_content)

        # Process images and external links
        logger.debug(f"Processing images, local attachments, and external links for page ID: '{page_id}'")
        markdown_content = link_checker.process_images(html_content, markdown_content)
        markdown_content = link_checker.process_attachment_links(markdown_content)

        # Remove Confluence footer
        markdown_content = _remove_confluence_footer(markdown_content)

        # Remove 'Created by' lines
        markdown_content, _ = _remove_created_by(markdown_content, return_line=True)

        # Add YAML header
        if config.YAML_HEADER:
            if is_new_index:
                logger.debug(f"Inserting YAML Header for index: '{final_md_output_name}'")
                markdown_content = _insert_yaml_header_md_index(markdown_content, page_id, config, link_checker)
            else:
                logger.debug(f"Inserting YAML Header for file: '{final_md_output_name}'")
                markdown_content = _insert_yaml_header_md(markdown_content, page_id, config, link_checker)

        # Remove space details for index files
        if is_new_index:
                logger.debug(f"Removing space details for index: '{final_md_output_name}'")
                markdown_content = _remove_space_details(markdown_content)

        # Remove unwanted sections
        if config.SECTIONS_TO_REMOVE:
            logger.debug("Removing unwanted sections")
            for section in config.SECTIONS_TO_REMOVE:
                markdown_content = _remove_markdown_section(markdown_content, section)

        # Remove unwanted lines
        if config.LINES_TO_REMOVE:
            logger.debug("Removing unwanted lines")
            markdown_content = _remove_markdown_lines(markdown_content, config.LINES_TO_REMOVE)

        # Save the markdown with the correct filename
        logger.debug(f"Saving page id '{page_id}' as filename: '{final_md_output_name}'")

        # Ensure the directory exists
        base_dir = os.path.dirname(md_output_name)
        final_out_path = os.path.join(base_dir, final_md_output_name)
        os.makedirs(os.path.dirname(final_out_path), exist_ok=True)

        with open(final_out_path, 'w', encoding='utf-8') as f:
            f.write(markdown_content)

        if not os.path.exists(final_out_path):
            raise FileNotFoundError(f"Output file not created: {final_out_path}")

        output_size = os.path.getsize(final_out_path)
        logger.info(f"Conversion successful. Output file size: {output_size} bytes")
        return True

    except Exception as e:
        logger.error(f"Conversion failed for {html_file}", exc_info=True)
        logger.debug(f"Error details: {str(e)}")
        print_status(f"Failed to convert {os.path.basename(html_file)}", error=True)
        return False

def _fix_md_crosslinks(output_dir: str, link_checker: LinkChecker) -> None:
    """
    Fix cross-references in Markdown files to use ID-based links.

    Args:
        output_dir (str): The output directory containing Markdown files
    """
    logger.info("Fixing cross-links in Markdown files using ID")

    # Get all markdown files
    md_files = []
    for root, _, files in os.walk(output_dir):
        for file in files:
            if file.endswith('.md'):
                md_files.append(os.path.join(root, file))

    # Set up statistics
    link_checker.attachment_processor.xml_processor.stats.total = len(md_files)
    link_checker.attachment_processor.xml_processor.stats.processed = 0
    link_checker.attachment_processor.xml_processor.stats.success = 0
    link_checker.attachment_processor.xml_processor.stats.failure = 0
    total_links_fixed = 0

    # Get all Homepage files
    all_spaces = link_checker.attachment_processor.xml_processor.spaces

    all_homepages = {}
    for _, space_data in all_spaces.items():
        homepage_id = space_data["homePageId"]
        space_name = space_data["name"]
        title = link_checker.attachment_processor.xml_processor.get_page_title_by_id(homepage_id)
        all_homepages[title] = {
            "space_name": space_name,
            "homepage_id": homepage_id,
            "title": title
        }
    
    # Create a set of homepage filenames (title + ".md")
    homepage_filenames = {f"{title}.md" for title in all_homepages}

    # Process each file
    for md_file in md_files:
        link_checker.attachment_processor.xml_processor.stats.processed += 1
        logger.info(f"Processing file {link_checker.attachment_processor.xml_processor.stats.processed}/{link_checker.attachment_processor.xml_processor.stats.total}: {md_file}")

        # Get the directory of the current file for context
        current_dir = os.path.dirname(os.path.relpath(md_file, output_dir))

        # Now check if md_file matches any homepage filename
        is_index_file = os.path.basename(md_file) in homepage_filenames

        try:
            with open(md_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            if md_file == 'output\\WER\\Arbeitssicherheit.md':
                logger.debug(f"Processing specific file: {md_file} with content")
                #logger.debug(f"--- Start of content ---")
                #logger.debug(content)
                #logger.debug(f"--- End of content ---")

            # Find all markdown links
            link_pattern = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')

            # Create a counter object that can be accessed by the nested function
            counter = {'links_fixed': 0}

            def replace_link(match):
                description = match.group(1)
                link = match.group(2).strip('<>')

                # Process the link
                new_link = _process_link(link, current_dir, link_checker)
                logger.debug(f"Processed link: '{link}' -> '{new_link}'")

                # Return the updated link
                if new_link:
                    counter['links_fixed'] += 1
                    # tag
                    if new_link.startswith('#'):
                        return new_link
                    # username
                    elif new_link.startswith('@'):
                        return new_link
                    # regular link
                    else:
                        if config.BLOGPOST_LINK_REPLACEMENT_ENABLED and description == config.BLOGPOST_LINK_INDICATOR:
                            new_description = os.path.splitext(os.path.basename(new_link))[0] + config.BLOGPOST_LINK_REPLACEMENT
                            logger.debug(f"Updated description from '{description}' to: '{new_description}'")
                            return link_checker.convert_wikilink(new_description, new_link)
                        return link_checker.convert_wikilink(description, new_link)
                else:
                    return description  # Just return the description if link was removed

            # Replace all links
            updated_content = link_pattern.sub(replace_link, content)

            def fix_label_lines(content):
                lines = content.splitlines()
                new_lines = []
                # Pattern: (indent)(optional tab)(spaces)(asterisk)(spaces)(#label)(rest)
                label_line_re = re.compile(r'^(\s*)(\t)?(\s*)\*\s+(#\S+)(.*)$')

                for line in lines:
                    m = label_line_re.match(line)
                    # replace labels for all pages
                    if m:
                        indent = m.group(1)        # leading whitespace
                        mid_space = m.group(3)     # spaces between tab/indent and asterisk
                        label = m.group(4)         # the #label
                        rest = m.group(5)          # anything after the label

                        # remove labels inside homepage
                        if config.REMOVE_ALL_TAGS_FROM_INDEX and is_index_file:
                            logger.debug(f"Removing label line in homepage: {line}")
                            new_line = ""
                        # Check if rest contains only whitespace/tabs
                        elif rest.strip():
                            # Rest contains non-whitespace characters - keep it
                            new_line = f"- {label}{rest}"
                            #new_line = f"{indent}{mid_space}- {label}{rest}"
                            new_lines.append(new_line)
                            logger.debug(f"Append label: {new_line}")
                        else:
                            # Rest is only whitespace/tabs - remove it
                            #new_line = f"{indent}{mid_space}- {label}"
                            new_line = f"- {label}"
                            new_lines.append(new_line)
                            logger.debug(f"Append label: {new_line}")
                    # remove special characters
                    else:
                        if line.endswith('  · '):
                            line.replace('  · ','')
                            logger.debug(f"Removed trailing special character from line: {line}")
                        new_lines.append(line)
                return '\n'.join(new_lines)

            # Apply the label line fix
            updated_content = fix_label_lines(updated_content)

            # Write the updated content
            with open(md_file, 'w', encoding='utf-8') as f:
                f.write(updated_content)

            # Get the count from our counter object
            links_fixed = counter['links_fixed']
            logger.debug(f"Updated {links_fixed} links in {md_file}")
            total_links_fixed += links_fixed

            # Update the stats with the number of links fixed
            if hasattr(link_checker.attachment_processor.xml_processor.stats, 'increment_links_fixed'):
                link_checker.attachment_processor.xml_processor.stats.increment_links_fixed(links_fixed)
            elif 'Fixing links' in link_checker.attachment_processor.xml_processor.stats.phase_stats and 'links_fixed' in link_checker.attachment_processor.xml_processor.stats.phase_stats['Fixing links']:
                link_checker.attachment_processor.xml_processor.stats.phase_stats['Fixing links']['links_fixed'] += links_fixed
                
            link_checker.attachment_processor.xml_processor.stats.success += 1

        except Exception as e:
            logger.error(f"Error fixing links in {md_file}: {str(e)}")
            link_checker.attachment_processor.xml_processor.stats.failure += 1

        link_checker.attachment_processor.xml_processor.stats.update_progress()

    if link_checker.attachment_processor.xml_processor.stats.processed > 0:
        avg_links = total_links_fixed / link_checker.attachment_processor.xml_processor.stats.processed
        logger.info(f"  Average links per file: {avg_links:.2f}")
    else:
        logger.info("  No files were processed for link fixing")

    logger.info(f"Link fixing summary:")
    logger.info(f"  Total files processed: {link_checker.attachment_processor.xml_processor.stats.processed}")
    logger.info(f"  Total links fixed: {total_links_fixed}")
    logger.info(f"  Average links per file: {total_links_fixed / link_checker.attachment_processor.xml_processor.stats.processed:.2f}")

def _process_link(link: str, current_dir: str, link_checker: LinkChecker) -> str:
    """
    Process a link to convert it to the correct format for markdown.

    Args:
        link (str): The original link to process
        current_dir (str): The directory of the current file for context

    Returns:
        str: The processed link in the correct format for markdown
    """
    # Skip if it's an empty link
    if not link or link.strip() == "":
        return ""

    logger.debug(f"Processing link: {link}")

    # Handle external links (excluding confluence links)
    if link.startswith(('http://', 'https://')) and not link.startswith('https://confluence'):
        logger.debug(f"External link detected: {link}")
        return link

    # Ignore local file links early
    if link.startswith("file://"):
        return link

    # Handle Homepage
    if link == "index.html":
        # Try to find the space by key from current directory
        space_key = current_dir.split(os.sep)[0] if os.sep in current_dir else current_dir
        logger.debug(f"Looking for home page in space: {space_key}")

        # Try to find the space by key
        space_info = link_checker.attachment_processor.xml_processor.get_space_by_key(space_key)
        if space_info and space_info.get("homePageId"):
            page_id = space_info["homePageId"]
            page_title = link_checker.attachment_processor.xml_processor.get_page_title_by_id(page_id)
            if page_title:
                logger.debug(f"Found home page for space {space_key}: {page_title}")
                return f"{page_title}.md"

        # If we couldn't find by space key, try all spaces
        for _, space_info in link_checker.attachment_processor.xml_processor.spaces.items():
            if space_info.get("homePageId"):
                page_id = space_info["homePageId"]
                page_title = link_checker.attachment_processor.xml_processor.get_page_title_by_id(page_id)
                if page_title:
                    logger.debug(f"Found home page: {page_title}")
                    return f"{page_title}.md"

        # Fallback to regular name if all other fail
        return "index.md"

    # Handle attachments and images
    if any(pattern in link for pattern in [f'{config.ATTACHMENTS_PATH}/', f'download/{config.ATTACHMENTS_PATH}/']):
        logger.debug(f"Attachment link detected: '{link}'")

        # Try to extract attachment ID first (most reliable method)
        attachment_id_match = re.search(r'attachments/\d+/(\d+)|download/attachments/\d+/(\d+)', link)
        if attachment_id_match:
            # Group 1 or 2 will contain the ID depending on which pattern matched
            attachment_id = attachment_id_match.group(1) if attachment_id_match.group(1) else attachment_id_match.group(2)
            
            # Look up attachment directly in XML data
            attachment = link_checker.attachment_processor.xml_processor.get_attachment_by_id(attachment_id)
            if attachment:
                # Get the actual parent page ID from the attachment data
                parent_page_id = attachment.get('containerContent_id')
                # Get the filename from attachment data
                attachment_filename = attachment['title']
                # Get the space key for the parent page
                space_key = link_checker.attachment_processor.xml_processor.get_space_key_by_page_id(parent_page_id)
                
                # Construct the new link path using the actual parent data
                new_link = f"{space_key}/{config.ATTACHMENTS_PATH}/{parent_page_id}/{attachment_filename}"
                logger.debug(f"Found attachment by ID: {attachment_id} -> {new_link}")
                return new_link
                
        # Fallback: Extract the actual filename and page ID from the link
        link_filename = os.path.basename(link.split('?')[0])
        decoded_filename = link_checker.attachment_processor.xml_processor._sanitize_filename(link_filename)
        page_id_match = re.search(rf'{config.ATTACHMENTS_PATH}/(\d+)/', link)
        page_id = page_id_match.group(1) if page_id_match else None

        if page_id:
            # Try to find attachment by filename in the page's attachments
            attachments = link_checker.attachment_processor.xml_processor.get_attachments_by_page_id(page_id)
            for att in attachments:
                if att.get('title') == link_filename or att.get('title') == decoded_filename:
                    # Use the actual parent page ID from the attachment data
                    parent_page_id = att.get('containerContent_id', page_id)
                    space_key = link_checker.attachment_processor.xml_processor.get_space_key_by_page_id(parent_page_id)
                    new_link = f"{space_key}/{config.ATTACHMENTS_PATH}/{parent_page_id}/{att['title']}"
                    logger.debug(f"Found attachment by filename: {link_filename} -> {new_link}")
                    return new_link

        # Only use file_mapping as last resort if other methods failed
        if page_id and link_checker.attachment_processor.file_mapping:
            # Current implementation fallback
            for orig_path, new_path in link_checker.attachment_processor.file_mapping.items():
                if page_id in orig_path and (link_filename in os.path.basename(new_path) or decoded_filename in os.path.basename(new_path)):
                    rel_path = os.path.relpath(new_path, link_checker.attachment_processor.config.OUTPUT_FOLDER)
                    logger.debug(f"Found in attachment mapping: '{link}' -> '{rel_path}'")
                    return rel_path.replace(os.sep, '/')

        # If no mapping found or no page ID, try to extract space key from the current directory
        if current_dir and page_id:
            # The current_dir might contain the space key
            space_key = current_dir.split(os.sep)[0] if os.sep in current_dir else current_dir
            new_path = f"{space_key}/{config.ATTACHMENTS_PATH}/{page_id}/{link_filename}"
            logger.debug(f"Created relative attachment link: '{link}' -> '{new_path}'")
            return new_path

        # If still no mapping found, return original link
        logger.debug(f"No mapping found for attachment link: '{link}'")
        return link

    # Handle /pages/viewpage.action?pageId=X links
    if '/pages/viewpage.action' in link and 'pageId=' in link:
        page_id_match = re.search(r'pageId=(\d+)', link)
        if page_id_match:
            page_id = page_id_match.group(1)
            page_info = link_checker.attachment_processor.xml_processor.get_page_by_id(page_id)
            if page_info:
                page_title = page_info.get('title')
                page_type = page_info.get('type')

                if page_type == 'BlogPost':
                    # Get space key for this page
                    space_id = page_info.get("spaceId")
                    space_info = link_checker.attachment_processor.xml_processor.get_space_by_id(space_id)
                    if space_info and space_info.get("key"):
                        target_space_key = space_info.get("key")
                        # Add space key prefix
                        logger.debug(f"Found blog post by ID: '{page_id}', title '{page_title}', space '{target_space_key}'")
                        return f"{target_space_key}/{config.BLOGPOST_PATH}/{page_title}.md"
                    else:
                        logger.debug(f"Space key not found for blog post ID '{page_id}'")
                        return f"{page_title}.md"
                else:
                    # Get space key for this page
                    space_id = page_info.get("spaceId")
                    space_info = link_checker.attachment_processor.xml_processor.get_space_by_id(space_id)
                    if space_info and space_info.get("key"):
                        target_space_key = space_info.get("key")
                        # Add space key prefix
                        logger.debug(f"Found page by ID: '{page_id}', title '{page_title}', space '{target_space_key}'")
                        return f"{target_space_key}/{page_title}.md"
                    else:
                        logger.debug(f"Space key not found for page ID '{page_id}'")
                        return f"{page_title}.md"
            else:
                logger.debug(f"Could not find page title for ID: '{page_id}'")
                return link

    # Handle /pages/editblogpost.action?pageId=X links
    if '/pages/editblogpost.action' in link and 'pageId=' in link:
        page_id_match = re.search(r'pageId=(\d+)', link)
        if page_id_match:
            page_id = page_id_match.group(1)
            page_info = link_checker.attachment_processor.xml_processor.get_page_by_id(page_id)
            if page_info:
                page_title = page_info.get('title')
                if page_info and page_info.get("spaceId"):
                    space_id = page_info.get("spaceId")
                    space_info = link_checker.attachment_processor.xml_processor.get_space_by_id(space_id)
                    if space_info and space_info.get("key"):
                        target_space_key = space_info.get("key")
                        # Add space key prefix
                        logger.debug(f"Found blog post by ID '{page_id}', title: '{page_title}', space '{target_space_key}'")
                        new_link =f"{target_space_key}/{config.BLOGPOST_PATH}/{page_title}.md"
                        return new_link
                else:
                    logger.debug(f"Space key not found for page ID '{page_id}'")
                logger.debug(f"Found blog post by ID '{page_id}', title: '{page_title}'")
                return f"{page_title}.md"
            else:
                logger.debug(f"Could not find blog post title for ID {page_id}")

    # Handle /label/ links (tags)
    if '/label/' in link:
        parts = link.split('/')
        if len(parts) >= 3:
            tag = parts[-1]
            return f"#{tag}"

    # Handle /labels/ links (tags) using ID
    if '/labels/' in link:
        # Extract label ID from label link
        label_id_match = re.search(r'ids=(\d+)', link)
        if label_id_match:
            label_id = label_id_match.group(1)
            
            # Use the new _label_by_id dictionary to look up label name
            label = link_checker.attachment_processor.xml_processor._label_by_id.get(label_id)
            if label:
                return f"#{label['name']}"
            
            # Fallback if label not found
            logger.warning(f"Label with ID {label_id} not found in XML data")
            return f"#label_{label_id}"

    # Handle user links that start with /display/~username
    if '/display/~' in link:
        username = link.split('/display/~')[1].strip()
        logger.debug(f"User link detected from path: '{username}'")
        return f"@{username}"

    # Handle /display/ links
    if '/display/' in link and not '/display/~' in link:
        #logger.debug(f"Display link detected: '{link}'")
        parts = link.split('/display/', 1)[1].split('/', 1)
        if len(parts) == 2:
            space_key, page_title = parts
            page_title = page_title.replace('+', ' ')  # Replace '+' with spaces in the page title
            page_title = link_checker.attachment_processor.xml_processor._sanitize_filename(page_title)
            
            # Verify this space and page combination exists in XML data
            space_info = link_checker.attachment_processor.xml_processor.get_space_by_key(space_key)
            if space_info:
                # Try to find the page by title in this space
                space_id = space_info.get('id')
                if space_id:
                    # Look through all pages in this space
                    for page_id, page_info in link_checker.attachment_processor.xml_processor.page.items():
                        if page_info.get('spaceId') == space_id and page_info.get('title') == page_title:
                            # Found the page, use its actual title from XML
                            logger.debug(f"Verified display link to space: '{space_key}', page: '{page_title}'")
                            return f"{space_key}/{page_title}.md"
            
            # If we couldn't verify, still use the link but log a warning
            logger.warning(f"Could not verify display link: space='{space_key}', page='{page_title}'")
            return f"{space_key}/{page_title}.md"
        elif len(parts) == 1:
            space_key = parts[0]
            # Verify this space exists in XML data
            space_info = link_checker.attachment_processor.xml_processor.get_space_by_key(space_key)
            if space_info:
                logger.debug(f"Verified display link to space only: '{space_key}'")
            else:
                logger.warning(f"Could not verify space in display link: '{space_key}'")
            return f"{space_key}.md"  # Default to space name

    # Handle userLogoLink
    if 'userLogoLink' in link or 'data-username' in link:
        # Extract username from data-username attribute if available
        username_match = re.search(r'data-username="([^"]+)"', link)
        if username_match:
            username = username_match.group(1).strip()
            logger.debug(f"User link detected from data-username: '{username}'")
            return f"@{username}"
        else:
            # Otherwise try to extract from href
            username_match = re.search(r'/display/~([^\s"]+)', link)
            if username_match:
                username = username_match.group(1).strip()
                logger.debug(f"User link detected from href: '{username}'")
                return f"@{username}"
            else:
                logger.debug(f"Could not extract username from: '{link}'")
                return ""

    # Handle relative links with page ID (e.g., Title_18317659.html)
    id_match = re.search(r'_(\d{6,10})\.html$', link)
    if id_match:
        page_id = id_match.group(1)
        page_info = link_checker.attachment_processor.xml_processor.get_page_by_id(page_id)
        if page_info:
            page_title = page_info.get('title')
            if page_info.get("spaceId"):
                space_id = page_info.get("spaceId")
                space_info = link_checker.attachment_processor.xml_processor.get_space_by_id(space_id)
                if space_info and space_info.get("key"):
                    target_space_key = space_info.get("key")
                    logger.debug(f"Found page by ID: '{page_id}', title: '{page_title}', space '{target_space_key}'")
                    return f"{target_space_key}/{page_title}.md"
            logger.debug(f"Found page by ID: '{page_id}', title: '{page_title}'")
            return f"{page_title}.md"

    # Handle relative links with numeric page ID (e.g., 18317659.html)
    id_match = re.search(r'(\d{6,10})\.html$', link)
    if id_match:
        page_id = id_match.group(1)
        page_info = link_checker.attachment_processor.xml_processor.get_page_by_id(page_id)
        if page_info:
            page_title = page_info.get('title')
            if page_info.get("spaceId"):
                space_id = page_info.get("spaceId")
                space_info = link_checker.attachment_processor.xml_processor.get_space_by_id(space_id)
                if space_info and space_info.get("key"):
                    target_space_key = space_info.get("key")
                    logger.debug(f"Found page by ID: '{page_id}', title: '{page_title}', space '{target_space_key}'")
                    return f"{target_space_key}/{page_title}.md"
            logger.debug(f"Found page by ID: '{page_id}', title: '{page_title}'")
            return f"{page_title}.md"

    # If we get here and the link still has HTML extension, convert to MD
    if link.endswith('.html'):
        base_name = os.path.splitext(link)[0]
        logger.debug(f"Converting HTML link to MD: '{link}'")
        return f"{base_name}.md"

    # If the link is a Confluence link to create a new space, remove it
    if link.startswith(f"{config.CONFLUENCE_BASE_URL}?createDialogSpaceKey"):
        logger.debug(f"Skipping createDialogSpaceKey link: '{link}'")
        return ""

    # If the link is a file from the mapping, replace by the new path
    link_sanitized = link_checker.attachment_processor.xml_processor._sanitize_filename(link)
    if link_sanitized:
        for attachment_title, new_path in link_checker.attachment_processor.file_mapping.items():
            if link_sanitized in attachment_title and (link_sanitized in os.path.basename(new_path)):
                # create absolute path and replace backslashes by forward slashes
                rel_path = os.path.relpath(new_path, link_checker.attachment_processor.config.OUTPUT_FOLDER).replace(os.sep, '/')
                logger.debug(f"Found in attachment mapping: '{link}' -> '{rel_path}'")
                return rel_path

    # Default case - return the link as is
    return link

def _process_blog_posts(config: Config, link_checker: LinkChecker) -> None:
    """
    Process all blog posts from XML and convert them to Markdown.

    Args:
        config: The configuration object
        stats: The statistics tracker
    """
    logger.info("Processing blog posts from XML...")

    # Collect all blog post IDs
    blog_post_ids = [
        page_id for page_id, page in link_checker.attachment_processor.xml_processor.page.items()
        if page.get("type") == "BlogPost"
    ]

    # Count total blog posts
    total_blog_posts = len(blog_post_ids)

    link_checker.attachment_processor.xml_processor.stats.total = total_blog_posts
    
    if total_blog_posts == 0:
        logger.info("No blog posts found to process")
        return
    else:
        logger.info(f"Found {total_blog_posts} blog posts to process")
    
    # Process each blog post
    for blog_id in blog_post_ids:
        blog_post = link_checker.attachment_processor.xml_processor.page[blog_id]
        space_id = blog_post.get("spaceId")

        # In case no spaceId is found
        if not space_id:
            logger.warning(f"Blog post {blog_id} has no space ID in page data")
            # Try to find space ID through other means
            space_id = link_checker.attachment_processor.xml_processor.find_space_id_for_blog(blog_id)
            if not space_id:
                logger.warning(f"Could not find space ID for blog post {blog_id}")
                link_checker.attachment_processor.xml_processor.stats.skip_file("Blog Posts")
                continue
        
        space_key = link_checker.attachment_processor.xml_processor.get_space_by_id(space_id)
        if not space_key:
            logger.warning(f"Could not find space info for ID {space_id} (blog {blog_id})")
            link_checker.attachment_processor.xml_processor.stats.skip_file("Blog Posts")
            continue
        
        space_key = space_key.get("key", "unknown")
        if space_key == "unknown":
            logger.warning(f"Could not determine space key for space ID {space_id} (blog {blog_id})")
            link_checker.attachment_processor.xml_processor.stats.skip_file("Blog Posts")
            continue

        # Create the blog posts directory for this space
        blog_dir = os.path.join(config.OUTPUT_FOLDER, space_key, config.BLOGPOST_PATH)
        os.makedirs(blog_dir, exist_ok=True)

        # Skip if no body content
        if not blog_post.get("bodypage") or not blog_post["bodypage"].get("body"):
            logger.warning(f"Blog post with ID {blog_id} has no body content")
            link_checker.attachment_processor.xml_processor.stats.skip_file("Blog Posts")
            continue

        # Convert the blog post to Markdown
        try:
            md_path = _convert_blog_html_to_md(blog_post, blog_dir, config, link_checker)
            link_checker.attachment_processor.xml_processor.stats.success += 1
            logger.debug(f"Successfully converted blog post {blog_id} to {md_path}")
        except Exception as e:
            logger.error(f"Failed to convert blog post {blog_id}: {str(e)}", exc_info=True)
            link_checker.attachment_processor.xml_processor.stats.failure += 1
            continue

        # Update progress
        link_checker.attachment_processor.xml_processor.stats.processed += 1
        link_checker.attachment_processor.xml_processor.stats.update_progress()

    # Update phase stats
    link_checker.attachment_processor.xml_processor.stats.update_phase_stats()
    logger.info(f"Blog post processing complete. Processed {link_checker.attachment_processor.xml_processor.stats.success} of {total_blog_posts} blog posts.")

def _convert_blog_html_to_md(blog_post: dict, output_dir: str, config: Config, link_checker: LinkChecker) -> str:
    """
    Convert a blog post's HTML body to Markdown and save it to a file.

    Args:
    blog_post: The blog post object with body content
    output_dir: The directory to save the Markdown file
    config: The configuration object

    Returns:
    The path to the created Markdown file
    """
    logger.info(f"Converting blog post {blog_post['id']} to Markdown")

    # Extract HTML content from the blog post
    html_content = blog_post["bodypage"]["body"]

    # Remove CDATA wrapper if present
    if html_content.startswith("<![CDATA[") and html_content.endswith("]]>"):
        html_content = html_content[9:-3]

    # Convert custom tags to HTML first
    html_content = convert_custom_tags_to_html(html_content, logger)

    # Convert HTML to Markdown
    try:
        logger.debug("Converting HTML to Markdown")
        markdown_content = _convert_html_to_markdown(html_content)
    except Exception as e:
        logger.error(f"Failed to convert blog post {blog_post['id']}: {str(e)}")
        raise Exception(f"Blog post conversion failed: {e}")

    # Create filename from blog post title
    filename = f"{blog_post['title']}.md"
    output_path = os.path.join(output_dir, filename)

    # Process attachment links in the blog post
    page_id = blog_post['id']

    logger.debug(f"Processing images, local attachments, and external links for blog post ID: {page_id}")    
    markdown_content = link_checker.process_invalid_video_links(html_content, markdown_content)
    markdown_content = link_checker.process_images(html_content, markdown_content)
    markdown_content = link_checker.process_attachment_links(markdown_content)

    # Add YAML header
    if config.YAML_HEADER_BLOG:
        # Combine YAML header and Markdown content
        markdown_content = _insert_yaml_header_md_blogpost(markdown_content, blog_post, config, link_checker)

    # Save the markdown file
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(markdown_content)

    logger.info(f"Saved blog post to: {output_path}")
    return output_path

def _remove_link_list_on_top(markdown_content: str) -> str:
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

def _remove_space_details(markdown_content: str) -> str:
    """
    Removes the "#  Space Details:" section from markdown content, stopping at the first H2 or next H1.
    Preserves all other sections, including "## Available Pages:" and other H1/H2 headers.

    Args:
        markdown_content (str): The original markdown content

    Returns:
        str: The cleaned markdown content with the Space Details section removed
    """
    logger.debug("Removing space details header")
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

def _remove_confluence_footer(markdown_content: str) -> str:
    """Remove the standard Confluence footer from markdown content"""
    logger.debug("Removing Confluence footer from markdown content")

    # Pattern to match the footer with variable date/time
    footer_pattern = r'\nDocument generated by Confluence on [A-Za-z]+\. \d{1,2}, \d{4} \d{1,2}:\d{2}\n\n\[Atlassian\]\(<https://www\.atlassian\.com/>\)\n*$'
    
    # Remove the footer
    cleaned_content = re.sub(footer_pattern, '', markdown_content)
    return cleaned_content

def _remove_markdown_section(markdown_content: str, section_header: str) -> str:
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
        _remove_markdown_section(content, "## Attachments:")
        _remove_markdown_section(content, "## Space contributors")
        _remove_markdown_section(content, "# Any other Header")
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

def _remove_markdown_lines(markdown_content: str, lines_to_remove: list[str]) -> str:
    """
    Removes specific lines from the markdown content, handling potential surrounding whitespace
    and ensuring correct newline handling. It removes all occurrences, including consecutive ones.

    Args:
        markdown_content: The original markdown content as a string.
        lines_to_remove: A list of exact string lines to be removed (e.g., ["Merken"]).

    Returns:
        The markdown content with the specified lines removed.
    """
    if not lines_to_remove or not markdown_content:
        # If there's nothing to remove, return the original content
        return markdown_content

    # Escape potential regex special characters in the lines to remove
    # and strip whitespace from the config values for robust matching.
    # Filter out any empty strings resulting from stripping.
    patterns = [re.escape(line.strip()) for line in lines_to_remove if line.strip()]

    if not patterns:
        # If lines_to_remove only contained whitespace or was empty after stripping
        logger.debug("No valid non-whitespace patterns provided in lines_to_remove.")
        return markdown_content

    # Construct the regex pattern:
    # ^                  - Anchor to the start of a line (due to re.MULTILINE flag).
    # \s*                - Match optional leading whitespace on the line.
    # (?:pattern1|...)   - Non-capturing group for all escaped patterns, joined by OR (|).
    # \s*                - Match optional trailing whitespace on the line.
    # (?:\r\n|\r|\n)?    - Match an optional universal newline sequence (\r\n, \r, or \n).
    #                      The '?' makes it optional, correctly handling the last line of the file
    #                      whether it has a trailing newline or not.
    combined_pattern = r'^\s*(?:' + '|'.join(patterns) + r')\s*(?:\r\n|\r|\n)?'

    # Store original length for comparison later
    original_length = len(markdown_content)

    # Perform the substitution using re.sub.
    # The re.MULTILINE flag ensures '^' matches the start of each line.
    # We replace the entire matched pattern (line + optional newline) with an empty string.
    try:
        cleaned_content = re.sub(combined_pattern, '', markdown_content, flags=re.MULTILINE)
    except re.error as e:
        logger.error(f"Regex error during line removal: {e} with pattern: {combined_pattern}")
        # Return original content if regex fails to prevent data loss
        return markdown_content

    # Log if changes were made
    if len(cleaned_content) < original_length:
        # We can't easily count lines removed with regex without splitting again,
        # so just log that *some* removal occurred based on the criteria.
        logger.debug(f"Removed some lines matching criteria: {lines_to_remove}")
    else:
        logger.debug(f"No lines found matching criteria: {lines_to_remove}")

    return cleaned_content

def _remove_embedded_icon_in_home_link(markdown_content: str) -> str:
    """
    Remove embedded icon in the home link in the h2 section.

    Before: "* [[Startpage.md|Startpage]] ![](images/icons/contenttypes/home_page_16.png)"
    After: "* [[Startpage.md|Startpage]]"

    Args:
        markdown_content: The markdown content to process

    Returns:
        The processed markdown content with embedded icons removed from home links
    """
    logger.debug("Removing embedded icons in home link")

    lines = markdown_content.splitlines()
    icon_link = ' ![](images/icons/contenttypes/home_page_16.png)'

    # Find the h2 section index
    h2_index = -1
    for i, line in enumerate(lines):
        if line.startswith('## '):
            h2_index = i
            logger.debug(f"Found h2 section at line {i}: {line}")
            break

    if h2_index == -1:
        logger.debug("No h2 section found")
        return markdown_content

    # Check the line after h2 (or the line after that if the next line is empty)
    target_index = h2_index + 1
    if target_index < len(lines) and not lines[target_index].strip():
        target_index += 1

    # Check if we have a valid line to process
    if target_index < len(lines):
        target_line = lines[target_index]
        logger.debug(f"Checking line {target_index}: {target_line}")

        # Check if this line contains the icon
        if icon_link in target_line:
            logger.debug(f"Found icon to remove in line: {target_line}")
            lines[target_index] = target_line.replace(icon_link, '')

    return '\n'.join(lines)

def _replace_first_header_name(markdown_content: str, new_filename: str) -> str:
    """
    Replace the first h1 section name with the filename.

    Before: "#  Spacename : YourHeaderText "
    After: "# YourHeaderText"

    Args:
        markdown_content: The markdown content to process
        new_filename: The export path of the file

    Returns:
        The processed markdown content with the first h1 header replaced
    """
    logger.debug(f"Replacing header name with: {new_filename}")
    # Extract the filename from the path
    # Strip the path prefix (output\SOMETHING\) and the .md extension
    filename = os.path.basename(new_filename)
    if filename.endswith('.md'):
        filename = filename[:-3]  # Remove .md extension

    # Process line by line
    lines = markdown_content.splitlines()
    for i, line in enumerate(lines):
        if line.startswith('# '):
            # Replace with just the filename as header
            lines[i] = f"# {filename}"
            break  # Only process the first h1 header

    return '\n'.join(lines)

def _extract_space_metadata(markdown_content: str) -> tuple[str, str]:
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

def _remove_created_by(markdown_content: str, return_line: bool = True) -> tuple[str, str]:
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

            break  # Exit loop after finding the first match
        i += 1

    # Reconstruct the cleaned content
    cleaned_content = '\n'.join(lines)

    return cleaned_content, created_by_line

def _insert_yaml_header_md(markdown_content: str, page_id: str, config: Config, link_checker: LinkChecker) -> str:
    """
    Insert a YAML header at the beginning of the markdown content with information
    extracted from XML data if available, or from the 'Created by' line and file path.

    Args:
        markdown_content: The original markdown content
        page_id: The ID of the page
        config: The configuration object containing YAML_HEADER template
        link_checker: Used to get the right name for the up field

    Returns:
        The markdown content with the YAML header prepended
    """
    logger.debug(f"Inserting YAML header into markdown content for page ID: {page_id}")

    # Start with the template from config
    yaml_header = config.YAML_HEADER

    # Extract author from created_by_line
    default_author = "unknown"
    default_date_created = "1999-12-31"  # Default date
    author = default_author
    date_created = default_date_created
    parent_folder = config.DEFAULT_UP_FIELD

    # If we found a page ID, get its information
    if page_id:
        page_info = link_checker.attachment_processor.xml_processor.get_page_by_id(page_id)

        if page_info:
            # Get creator name
            if page_info.get("creatorId"):
                author_id = page_info["creatorId"]
                author_info = link_checker.attachment_processor.xml_processor.get_user_by_id(author_id)
                author = author_info["name"]
                logger.debug(f"Got author name: {author}")

            # Get creation date
            if page_info.get("creationDate"):
                date_match = re.match(r'(\d{4})-(\d{2})-(\d{2})', page_info["creationDate"])
                if date_match:
                    year, month, day = date_match.groups()
                    date_created = f"{year}-{month}-{day}"
                    logger.debug(f"Got creation date from XML: {date_created}")

            # Get parent title directly from the cached information
            parent_title = link_checker.attachment_processor.xml_processor.get_parent_title_by_id(page_id)
            if parent_title:
                parent_folder = parent_title
                logger.debug(f"Updated parent name to: {parent_folder}")
    else:
        logger.debug(f"Could not find page info: {parent_folder}")

    # Replace placeholders in the YAML header
    yaml_header = yaml_header.replace('author: [username]', f'author: {author}')
    yaml_header = yaml_header.replace('dateCreated: [date_created]', f'dateCreated: {date_created}')
    yaml_header = yaml_header.replace('[[up_field]]', f'[[{parent_folder}]]')

    # Add the YAML header to the markdown content
    updated_content = yaml_header + '\n\n' + markdown_content

    return updated_content

def _insert_yaml_header_md_index(markdown_content: str, page_id: str, config: Config, link_checker: LinkChecker) -> str:
    """
    Insert a YAML header at the beginning of index markdown files with information
    extracted from the Space Details table and file path.

    Args:
        markdown_content: The original markdown content
        page_id: The ID of the page to which the YAML header will be added
        config: The configuration object containing YAML_HEADER template
        link_checker: Used to get the right name for the up field

    Returns:
        The markdown content with the YAML header added
    """
    logger.debug(f"Inserting YAML header into index markdown content for page ID: {page_id}")

    # Start with the template from config
    yaml_header = config.YAML_HEADER

    # Default values
    default_author = "unknown"
    default_date_created = "1999-12-31"  # Default date
    author = default_author
    date_created = default_date_created
    parent_folder = "" # should be empty, as it's the highest level (alt: config.DEFAULT_UP_FIELD)

    # Try to get information from XML if available
    if link_checker.attachment_processor.xml_processor is not None:
        logger.debug(f"Using XML Checker to get Header info")

        # If we found a page ID, get its information
        if page_id:
            space_id = link_checker.attachment_processor.xml_processor.get_space_id_by_page_id(page_id)
            logger.debug(f"Retrieved space ID: {space_id}")
            if space_id:
                space_info = link_checker.attachment_processor.xml_processor.get_space_by_id(space_id)
                logger.debug("Retrieved space info by space ID")
            if not space_info:
                space_info = link_checker.attachment_processor.xml_processor.get_page_by_id(page_id)
                logger.debug("Fallback to page info")
            if not space_info:
                logger.debug(f"No space or page info found for page ID: {page_id}")
            if space_info:
                # Get creator
                if space_info.get("creatorId"):
                    author_id = space_info["creatorId"]
                    author_info = link_checker.attachment_processor.xml_processor.get_user_by_id(author_id)
                    author = author_info["name"]
                    logger.debug(f"Got author name: {author}")

                # Get creation date
                if space_info.get("creationDate"):
                    date_match = re.match(r'(\d{4})-(\d{2})-(\d{2})', space_info["creationDate"])
                    if date_match:
                        year, month, day = date_match.groups()
                        date_created = f"{year}-{month}-{day}"
                        logger.debug(f"Got creation date from XML: {date_created}")

            else:
                logger.debug(f"Could not find page info for page ID: {page_id}")

    # Fall back to extracting from Space Details table if XML data wasn't available
    if author == default_author or date_created == default_date_created:
        extracted_author, extracted_date, _ = _extract_space_metadata(markdown_content)

        if extracted_author and author == default_author:
            author = extracted_author
            logger.debug(f"Extracted author from Space Details: {author}")
        elif not extracted_author and author == default_author:
            logger.debug(f"Could not extract author from Space Details for ID: {page_id}. Using default: {default_author}")

        if extracted_date and date_created == default_date_created:
            date_created = extracted_date
            logger.debug(f"Extracted date from Space Details: {date_created}")
        elif not extracted_date and date_created == default_date_created:
            logger.debug(f"Could not extract date from Space Details for ID: {page_id}. Using default: {default_date_created}")
    
    # Replace placeholders in the YAML header
    yaml_header = yaml_header.replace('author: [username]', f'author: {author}')
    yaml_header = yaml_header.replace('dateCreated: [date_created]', f'dateCreated: {date_created}')
    yaml_header = yaml_header.replace('[[up_field]]', f'[[{parent_folder}]]')

    # Add the YAML header to the markdown content
    updated_content = yaml_header + '\n\n' + markdown_content

    return updated_content

def _insert_yaml_header_md_blogpost(markdown_content: str, blog_post: str, config: Config, link_checker: LinkChecker) -> str:
    """
    Insert a YAML header at the beginning of blog post markdown content with information
    extracted from XML data or other metadata.
    Args:
        markdown_content: The original markdown content
        page_id: The ID of the page to retrieve metadata for
        config: The configuration object containing YAML_HEADER template
    """
    # Get author name
    author = "unknown"
    if blog_post.get("creatorId"):
        author_info = link_checker.attachment_processor.xml_processor.get_user_by_id(blog_post["creatorId"])
        if author_info:
            author = author_info["name"]

    # Get creation date
    date_created = "1900-12-31"
    if blog_post.get("creationDate"):
        date_match = re.match(r'(\d{4})-(\d{2})-(\d{2})', blog_post["creationDate"])
        if date_match:
            year, month, day = date_match.groups()
            date_created = f"{year}-{month}-{day}"

    # Get space name as parent folder
    space_id = link_checker.attachment_processor.xml_processor.get_space_id_by_page_id(blog_post['id'])
    space_info = link_checker.attachment_processor.xml_processor.get_space_by_id(space_id)
    parent_id = space_info.get('homePageId', '')
    parent_folder = link_checker.attachment_processor.xml_processor.get_page_title_by_id(parent_id)
    logger.debug(f"Space ID: {space_id}, Parent ID: {parent_id}, Parent Folder: {parent_folder}")
    if parent_folder == None:
        parent_folder = ""
    logger.debug(f"Parent folder determined as: {parent_folder}")
    
    # Create YAML header
    yaml_header = config.YAML_HEADER_BLOG
    yaml_header = yaml_header.replace('author: [username]', f'author: {author}')
    yaml_header = yaml_header.replace('dateCreated: [date_created]', f'dateCreated: {date_created}')
    yaml_header = yaml_header.replace('[[up_field]]', f'[[{parent_folder}]]')

    # Combine YAML header and Markdown content
    markdown_content = yaml_header + "\n\n" + markdown_content

    # return results
    return markdown_content

def _debug_print_mappings(link_checker: LinkChecker) -> None:
    """Print all filename mappings for debugging"""
    logger.debug("=== Filename Mappings ===")
    for old_path, new_path in link_checker.filename_mapping.items():
        logger.debug(f"{old_path} -> {new_path}")

    logger.debug("=== Directory-Aware Basename Mappings ===")
    for basename, dir_mappings in link_checker.basename_dir_mapping.items():
        for directory, mapped_name in dir_mappings.items():
            logger.debug(f"{directory}/{basename} -> {mapped_name}")

    logger.debug("=== End of Mappings ===")

def main(config: Config, logger: logging.Logger) -> None:
    try:
        logger.info("=== Starting HTML to Markdown Conversion Process ===")
        logger.debug(f"Python version: {sys.version}")

        # Get list of input folders
        input_folders = []
        if os.path.isdir(config.INPUT_FOLDER):
            # If INPUT_FOLDER is a directory, use it directly
            input_folders = [config.INPUT_FOLDER]
        else:
            # If INPUT_FOLDER is a pattern or list, expand it
            input_folders = [folder for folder in config.INPUT_FOLDER.split(',') if os.path.isdir(folder)]

        logger.info(f"Input folders: {input_folders}")
        logger.info(f"Output folder: {os.path.abspath(config.OUTPUT_FOLDER)}")

        # Create output folder
        os.makedirs(config.OUTPUT_FOLDER, exist_ok=True)
        logger.debug(f"Output directory structure created: {config.OUTPUT_FOLDER}")
        
        # Initialize statistics
        stats = ConversionStats()

        # Initialize both tools with the same reference
        xml_processor = XmlProcessor(config, logger, stats=stats)
        attachment_processor = AttachmentProcessor(config, logger, xml_processor)
        link_checker = LinkChecker(config, logger, attachment_processor)

        # Count total HTML files across all input folders
        print_status("Scanning Attachments...")
        total_html_count = _count_html_files(input_folders, config)

        logger.debug(f"Found {total_html_count} HTML files to process across all input folders")
        print_status(f"Found {total_html_count} HTML files to process...")

        # Process all XML files to build a complete cache
        print_status("Processing XML files...")
        
        # Set the phase to XML Processing before processing XML files
        link_checker.attachment_processor.xml_processor.stats.set_phase("XML Processing")
        
        # Count XML files first
        xml_files_to_process = []
        for input_folder in input_folders:
            _, subfolders, _ = next(os.walk(input_folder))
            for subfolder in subfolders:
                # Skip special folders
                if _get_special_folder_type(subfolder, config) is not None:
                    continue
                
                exists, xml_path = xml_processor.verify_xml_file(subfolder, config, logger=logger)
                if exists:
                    xml_files_to_process.append(xml_path)
        
        # Set total XML files to process
        link_checker.attachment_processor.xml_processor.stats.total = len(xml_files_to_process)
        logger.info(f"Found {link_checker.attachment_processor.xml_processor.stats.total} XML files to process")
        
        # Process each XML file
        for xml_path in xml_files_to_process:
            success = link_checker.attachment_processor.xml_processor.add_xml_file(xml_path)
            link_checker.attachment_processor.xml_processor.stats.processed += 1
            if success:
                link_checker.attachment_processor.xml_processor.stats.success += 1
            else:
                link_checker.attachment_processor.xml_processor.stats.failure += 1
            link_checker.attachment_processor.xml_processor.stats.update_progress()
        
        # Update phase stats after XML processing
        link_checker.attachment_processor.xml_processor.stats.update_phase_stats()

        # Create a mapping for attachments
        print("")  # add newline to prevent cluttering
        print_status("Mapping Attachments...")
        for input_folder in input_folders:
            _, subfolders, _ = next(os.walk(input_folder))
            for subfolder in subfolders:
                # Skip special folders
                if _get_special_folder_type(subfolder, config) == config.STYLES_PATH:
                    logger.debug(f"Skipping folder: {subfolder}")
                    continue

                # Verify XML file exists for this space
                exists, xml_path = link_checker.attachment_processor.xml_processor.verify_xml_file(subfolder, config, logger)

                if exists:
                    # Process attachments
                    logger.info(f"Building attachment mapping from XML: {xml_path}")
                    # Get the space directory name from subfolder
                    space_dir = os.path.basename(subfolder)
                    link_checker.attachment_processor.process_xml_attachments(xml_path)
                    link_checker.attachment_processor.process_space_attachments(space_dir)
                    link_checker.attachment_processor.generate_mapping_file()
                    link_checker.attachment_processor.copy_images_folder(subfolder, config, logger)

                else:
                    logger.warning(f"No XML file found for space: {subfolder}. Skipping attachment processing.")

        # Log the total number of pages found
        logger.info(f"Total pages found across all XML files: {len(link_checker.attachment_processor.xml_processor.page)}")

        # Third pass: Convert HTML files to Markdown for all input folders
        print_status("Converting HTML files to Markdown...")
        link_checker.attachment_processor.xml_processor.stats.set_phase("Converting")  # Start conversion phase
        link_checker.attachment_processor.xml_processor.stats.total = total_html_count

        for input_folder in input_folders:
            for root, _, files in os.walk(input_folder):
                # Skip special folders
                if _get_special_folder_type(root, config) is not None:
                    continue

                rel_path = os.path.relpath(root, input_folder)
                output_dir = os.path.join(config.OUTPUT_FOLDER, rel_path)
                os.makedirs(output_dir, exist_ok=True)

                # Process HTML files (convert only)
                _process_html_files(root, files, output_dir, config, link_checker)

        # Update phase stats after converting
        link_checker.attachment_processor.xml_processor.stats.update_phase_stats()

        # Fourth pass: Process blog posts
        print("") # add newline to prevent cluttering
        print_status("Processing blog posts...")
        link_checker.attachment_processor.xml_processor.stats.set_phase("Blog Posts")
        _process_blog_posts(config, link_checker)
        # Update phase stats after blog posts
        link_checker.attachment_processor.xml_processor.stats.update_phase_stats()

        # Fifth pass: Fix all crosslinks using the complete mapping
        print("") # add newline to prevent cluttering
        print_status("Fixing crosslinks in all Markdown files...")
        link_checker.attachment_processor.xml_processor.stats.set_phase("Fixing links")  # Start conversion phase
        _fix_md_crosslinks(config.OUTPUT_FOLDER, link_checker)
        # Update phase stats after fixing links
        link_checker.attachment_processor.xml_processor.stats.update_phase_stats()

        # Debug print mappings
        if config.LOG_LINK_MAPPING == True:
            _debug_print_mappings(link_checker)

        # Log summary of skipped files
        if link_checker.attachment_processor.xml_processor.stats.phase_stats["Converting"]["skipped"] > 0:
            logger.info(f"=== Summary: {link_checker.attachment_processor.xml_processor.stats.phase_stats['Converting']['skipped']} Files were skipped ===")

        #print("\n")
        print_status("Finalizing and cleaning up...")
        logger.info("=== Conversion Process Complete ===")
        print("\n")

        # Print and log the final report
        final_report = link_checker.attachment_processor.xml_processor.stats.print_final_report()
        logger.info(final_report)

    except Exception as e:
        logger.error("Process failed", exc_info=True)
        print_status(str(e), error=True)
        sys.exit(1)

if __name__ == "__main__":
    # Parse command line arguments
    args = parse_args()

    # Merge command line arguments with config from file
    config = load_config(args)

    # Setup logging
    logger = setup_logging(config)

    # Run main function
    main(config, logger)
