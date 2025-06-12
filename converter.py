import os
import io
import sys
import logging
import re
import argparse

from attachmentprocessor import AttachmentProcessor
from linkchecker import LinkChecker
from xmlprocessor import XmlProcessor
from conversionstats import ConversionStats
from config import Config, load_config
from htmlprocessor import HtmlProcessor

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
        html_processor = HtmlProcessor(config, logger)

        # Count total HTML files across all input folders
        print_status("Scanning Attachments...")
        total_html_count = html_processor.count_html_files(input_folders)

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
                if html_processor._get_special_folder_type(subfolder) is not None:
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
                if html_processor._get_special_folder_type(subfolder) == config.STYLES_PATH:
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

        # Create tag mapping from HTML content-by-label sections
        print_status("Mapping Tags to pages...")
        html_processor.create_tag_mapping_from_html(input_folders, link_checker)

        # Third pass: Convert HTML files to Markdown for all input folders
        print_status("Converting HTML files to Markdown...")
        link_checker.attachment_processor.xml_processor.stats.set_phase("Converting")  # Start conversion phase
        link_checker.attachment_processor.xml_processor.stats.total = total_html_count

        for input_folder in input_folders:
            for root, _, files in os.walk(input_folder):
                # Skip special folders
                if html_processor._get_special_folder_type(root) is not None:
                    continue

                rel_path = os.path.relpath(root, input_folder)
                output_dir = os.path.join(config.OUTPUT_FOLDER, rel_path)
                os.makedirs(output_dir, exist_ok=True)

                # Process HTML files (convert only)
                _process_html_files(root, files, output_dir, config, link_checker, html_processor)

        # Update phase stats after converting
        link_checker.attachment_processor.xml_processor.stats.update_phase_stats()

        # Fourth pass: Process blog posts
        print("") # add newline to prevent cluttering
        print_status("Processing blog posts...")
        link_checker.attachment_processor.xml_processor.stats.set_phase("Blog Posts")
        html_processor._process_blog_posts(link_checker)
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

        print("") # add newline to prevent cluttering
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

def _process_html_files(root: str, files: list, output_dir: str, config: Config, link_checker: LinkChecker, html_processor: HtmlProcessor) -> None:
    """Convert HTML files to Markdown and collect filename mappings"""
    html_files: str = [f for f in files if f.endswith('.html')]

    # Log all HTML files found in this directory
    logger.info(f"Found {len(html_files)} HTML files in {root}")
    
    for filename in html_files:
        input_file = os.path.join(root, filename)
        logger.debug(f"Processing HTML file: {input_file}")

        # Check if file should be skipped (e.g., in special folders)
        if html_processor._is_special_folder(input_file):
            logger.info(f"Skipping file in special folder: {input_file}")
            link_checker.attachment_processor.xml_processor.stats.skip_file("Converting")
            continue

        link_checker.attachment_processor.xml_processor.stats.processed += 1
        md_output_name = os.path.join(output_dir, filename[:-5] + ".md")

        logger.info(f"Processing file {link_checker.attachment_processor.xml_processor.stats.processed}/{link_checker.attachment_processor.xml_processor.stats.total}: {filename}")

        try:
            if html_processor.convert_html_to_md(input_file, md_output_name, link_checker):
                link_checker.attachment_processor.xml_processor.stats.success += 1
            else:
                print_status(f"Failed to convert {os.path.basename(input_file)}", error=True)
                link_checker.attachment_processor.xml_processor.stats.failure += 1
        except Exception as e:
            logger.error(f"Failed to convert {filename}: {str(e)}")
            link_checker.attachment_processor.xml_processor.stats.failure += 1

        link_checker.attachment_processor.xml_processor.stats.update_progress()

    # Update phase stats after processing
    link_checker.attachment_processor.xml_processor.stats.update_phase_stats()

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
                        return new_link[1:]  # remove @ sign
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

    logger.debug(f"Processing link: '{link}'")

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

        # Use file_mapping to find attachments
        if page_id and link_checker.attachment_processor.file_mapping:
            for orig_path, new_path in link_checker.attachment_processor.file_mapping.items():
                if page_id in orig_path and (link_filename in os.path.basename(new_path) or decoded_filename in os.path.basename(new_path)):
                    rel_path = os.path.relpath(new_path, link_checker.attachment_processor.config.OUTPUT_FOLDER)
                    logger.debug(f"Found in attachment mapping: '{link}' -> '{rel_path}'")
                    return rel_path.replace(os.sep, '/')

        # Fallback: Try to find attachment by filename in the page's attachments if other methods failed
        if page_id:
            attachments = link_checker.attachment_processor.xml_processor.get_attachments_by_page_id(page_id)
            for att in attachments:
                if att.get('title') == link_filename or att.get('title') == decoded_filename:
                    # Use the actual parent page ID from the attachment data
                    parent_page_id = att.get('containerContent_id', page_id)
                    space_key = link_checker.attachment_processor.xml_processor.get_space_key_by_page_id(parent_page_id)
                    new_link = f"{space_key}/{config.ATTACHMENTS_PATH}/{parent_page_id}/{att['title']}"
                    logger.debug(f"Found attachment by filename: {link_filename} -> {new_link}")
                    return new_link

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
                        # Insert blogpost subfolder
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

    # Handle user links that start with /userkey/{userkey}
    if '/userkey/' in link:
        userkey = link.split('/userkey/')[1].strip()
        logger.debug(f"Userkey link detected from path: '{userkey}'")
        
        # Look up the user by userkey to get the actual username
        user_info = link_checker.attachment_processor.xml_processor.get_user_by_id(userkey)
        if user_info:
            username = user_info.get('name')
            if username:
                logger.debug(f"Found username for userkey '{userkey}': '{username}'")
                return f"@{username}"
            else:
                logger.warning(f"User found but no name available for userkey '{userkey}'")
                return f"@{userkey}"  # Fallback to userkey
        else:
            logger.warning(f"No user found for userkey '{userkey}'")
            return f"@{userkey}"  # Fallback to userkey

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
    if link_sanitized:
        return link_sanitized
    else:
        return link

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

if __name__ == "__main__":
    # Parse command line arguments
    args = parse_args()

    # Merge command line arguments with config from file
    config = load_config(args)

    # Setup logging
    logger = setup_logging(config)

    # Run main function
    main(config, logger)
