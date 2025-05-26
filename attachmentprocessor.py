import os
import logging
import shutil
from pathlib import Path

from config import Config
from xmlprocessor import XmlProcessor

class AttachmentProcessor:
    def __init__(self, config: Config, logger: logging.Logger, xml_processor: XmlProcessor):
        self.config = config
        self.logger = logger
        
        # Use the XmlProcessor for XML data if provided
        self.xml_processor = xml_processor
        
        # File processing structures
        self.file_mapping = {}  # Original path -> New path
        self.missing_files = set()
        self.skipped_files = set()
        
        # Track processed attachments
        self.processed_attachments = set()
        
    def copy_images_folder(self, space_key: str, config: Config, logger: logging.Logger) -> None:
        """
        Copy the images folder from the input directory to the output directory.

        Args:
        space_key: The input directory space key containing the images folder (Confluence space key)
        config: The configuration object
        logger: The logger instance
        """
        images_folder = os.path.join(config.INPUT_FOLDER, space_key, config.IMAGES_PATH)
        if os.path.exists(images_folder):
            output_folder = os.path.join(config.OUTPUT_FOLDER, space_key, config.IMAGES_PATH)
            logger.info(f"Copying images folder from '{images_folder}' to '{output_folder}'")
            shutil.copytree(images_folder, output_folder, dirs_exist_ok=True)
        else:
            logger.info(f"Images folder not found: '{space_key}'")

    def _get_extension_from_content_type(self, content_type: str) -> str:
        """Map content type to file extension."""
        if not content_type:
            return "bin"

        content_map = {
            "image/png": "png",
            "image/jpeg": "jpg",
            "image/jpg": "jpg",
            "application/pdf": "pdf",
            "text/plain": "txt",
            "application/msword": "doc",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
            "application/vnd.ms-excel": "xls",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx"
        }

        return content_map.get(content_type, "bin")

    def process_space_attachments(self, space_key: str) -> None:
        """Process all attachments in a space."""
        self.logger.info(f"Processing attachments for space: {space_key}")

        # Look for attachments directory
        attachments_dir = os.path.join(self.config.INPUT_FOLDER, space_key, self.config.ATTACHMENTS_PATH)
        if not os.path.exists(attachments_dir):
            self.logger.error(f"Attachments directory not found: '{attachments_dir}'")
            return None

        # Create output directory
        output_attachments_dir = os.path.join(self.config.OUTPUT_FOLDER, space_key, self.config.ATTACHMENTS_PATH)
        os.makedirs(output_attachments_dir, exist_ok=True)

        # Process each page directory found in the input attachments folder
        # e.g., input/SPACEKEY/attachments/123456/
        try:
            page_id_dirs = os.listdir(attachments_dir)
        except OSError as e:
            self.logger.error(f"Error listing directories in {attachments_dir}: {e}")
            return

        # Process each page directory
        for page_id in page_id_dirs:
            page_dir = os.path.join(attachments_dir, page_id)

            if not os.path.isdir(page_dir):
                self.logger.debug(f"Skipping non-directory item in attachments folder: {page_dir}")
                continue
            
            # Check if this page ID is known from XML data (optimization)
            page_info = self.xml_processor.get_page_by_id(page_id)
            if not page_info:
                self.logger.debug(f"Skipping page directory not found in XML data: {page_dir}")
                continue
            
            # Prepare output dir
            out_dir = os.path.join(output_attachments_dir, page_id)
            os.makedirs(out_dir, exist_ok=True)

            # Process each attachment file
            for filename in os.listdir(page_dir):
                src_path = os.path.join(page_dir, filename)

                # Skip if already processed
                if src_path in self.processed_attachments:
                    continue

                # Check if this source path is already in the file mapping
                if src_path in self.file_mapping:
                    dst_path = self.file_mapping[src_path]
                    # Check if we need to update the file
                    if self.should_copy_file(src_path, dst_path):
                        try:
                            shutil.copy2(src_path, dst_path)
                            self.logger.debug(f"Updated mapped file: {src_path} -> {dst_path}")
                        except Exception as e:
                            self.logger.error(f"Error updating mapped file {src_path} to {dst_path}: {str(e)}")
                            self.missing_files.add(src_path)
                    continue  # Skip further processing as it's already been mapped

                # Mark as processed
                self.processed_attachments.add(src_path)

                # Extract attachment ID from filename
                att_id = filename.split(".")[0] if "." in filename else filename

                # Skip if attachment not in our XML data
                att_details = self.xml_processor.get_attachment_by_id(att_id)
                if not att_details:
                    self.logger.debug(f"Skipping unknown attachment: {src_path}")
                    self.skipped_files.add(src_path)
                    continue

                proper_title = att_details["title"]

                # If no proper title found, use the original filename
                if not proper_title:
                    proper_title = filename

                # Get file extension
                if "." in filename:
                    extension = filename.split(".")[-1]
                elif "." in proper_title:
                    extension = proper_title.split(".")[-1]
                else:
                    # Use content type to determine extension
                    extension = self._get_extension_from_content_type(att_details.get("contentType"))

                # Build proper filename
                if "." not in proper_title:
                    new_filename = f"{proper_title}.{extension}"
                else:
                    new_filename = proper_title

                # Create output path
                dst_path = os.path.join(out_dir, new_filename)
                #self.logger.debug(f"Created dst_path: {dst_path}")

                # Copy file
                try:
                    if self.should_copy_file(src_path, dst_path):
                        self.logger.debug(f"Copied attachment: {src_path} -> {dst_path}")
                        self.file_mapping[src_path] = dst_path
                        shutil.copy2(src_path, dst_path)
                    else:
                        self.logger.debug(f"Skipped (destination is newer or identical): {src_path}")
                except Exception as e:
                    self.logger.error(f"Error copying {src_path} to {dst_path}: {str(e)}")
                    self.missing_files.add(src_path)

    def process_xml_attachments(self, xml_path: str = None) -> None:
        """Process supplementary attachments from input-xml folder."""
        self.logger.info(f"Processing supplementary attachments from XML for: {xml_path}")

        if not xml_path:
            self.logger.warning("No XML path provided for supplementary attachments")
            return None

        # Get the XML folder path (parent directory of the XML file)
        xml_folder = os.path.dirname(xml_path)

        # Look for attachments directory in this XML export
        attachments_dir = os.path.join(xml_folder, self.config.ATTACHMENTS_PATH)
        if not os.path.exists(attachments_dir):
            self.logger.error(f"Attachments directory cannot be created in: {xml_folder}")
            return None

        # Process each space we have in our XML data
        space_key = self.xml_processor.get_space_key_by_xml(xml_path)

        if not space_key:
            self.logger.warning(f"Space has no key, skipping: {space_key}")
            return None

        # Process each page directory in the attachments folder
        for page_id in os.listdir(attachments_dir):
            page_dir = os.path.join(attachments_dir, page_id)
            if not os.path.isdir(page_dir):
                continue

            # Skip if page not in our XML data
            page = self.xml_processor.get_page_by_id(page_id)
            if not page:
                self.logger.debug(f"Skipping unknown page directory: {page_dir}")
                continue

            # Create output directory for this space
            output_attachments_dir = os.path.join(self.config.OUTPUT_FOLDER, space_key, self.config.ATTACHMENTS_PATH)
            os.makedirs(output_attachments_dir, exist_ok=True)

            # Prepare output dir
            out_dir = os.path.join(output_attachments_dir, page_id)
            os.makedirs(out_dir, exist_ok=True)
            #self.logger.debug(f"Created output directory: {out_dir}")

            # Process each attachment directory
            for att_id in os.listdir(page_dir):
                att_dir = os.path.join(page_dir, att_id)
                if not os.path.isdir(att_dir):
                    continue

                # Get attachment details from XML data
                att_details = self.xml_processor.get_attachment_by_id(att_id)
                if not att_details:
                    self.logger.debug(f"Skipping unknown attachment: {att_dir}")
                    self.skipped_files.add(att_dir)
                    continue

                # Find the latest version file
                latest_version = 0
                latest_file = None
                for filename in os.listdir(att_dir):
                    try:
                        version = int(filename) # in xml attachments: filename == version number
                        if version > latest_version:
                            latest_version = version
                            latest_file = os.path.join(att_dir, filename)
                    except ValueError:
                        # Skip files that aren't version numbers
                        continue

                if not latest_file:
                    self.logger.debug(f"No version files found in {att_dir}")
                    continue

                # Get sanitized filename from attachments dictionary
                new_filename = att_details.get("title", "")
                if not new_filename:
                    new_filename = f"attachment_{att_id}"

                # Create output path using pre-sanitized title
                dst_path = os.path.join(out_dir, new_filename)

                # Copy file
                try:
                    if self.should_copy_file(latest_file, dst_path):
                        #self.logger.debug(f"Copied supplementary attachment: {latest_file} -> {dst_path}")
                        self.file_mapping[latest_file] = dst_path # Map original path to new path
                        shutil.copy2(latest_file, dst_path)
                    else:
                        # File exists and is not older, skip copying but record the mapping for the report
                        self.logger.debug(f"Skipped copying (destination exists and is not older): {latest_file} -> {dst_path}")
                        self.file_mapping[latest_file] = dst_path # Record intended mapping even if not copied
                except Exception as e:
                    self.logger.error(f"Error copying {latest_file} to {dst_path}: {str(e)}")
                    self.missing_files.add(latest_file)
           
    def generate_mapping_file(self) -> str:
        """Generate a file mapping report."""
        mapping_path = os.path.join(self.config.OUTPUT_FOLDER, "attachment_mapping.csv")

        with open(mapping_path, 'w', encoding='utf-8') as f:
            f.write("original_path,new_path,attachment_id,page_id,page_title,attachment_title\n")

            for src_path, dst_path in self.file_mapping.items():
                # Extract IDs from path
                parts = Path(src_path).parts

                # Check for supplementary attachments path structure (e.g., .../page_id/att_id/version)
                if len(parts) >= 4 and parts[-3].isdigit():
                    page_id = parts[-3]
                    att_id = parts[-2]
                    filename = parts[-1]
                # Check for regular attachments path structure (e.g., .../page_id/att_id.ext or .../page_id/filename.ext)
                elif len(parts) >= 3 and parts[-2].isdigit():
                    page_id = parts[-2]
                    filename = parts[-1]
                    att_id = filename.split('.')[0] if '.' in filename and filename.split('.')[0].isdigit() else ""
                    if not att_id:
                         # Fallback or specific handling if needed, for now, we assume att_id is derivable from filename
                         self.logger.warning(f"Could not determine attachment ID reliably for regular attachment: {src_path}")

                # Get page title
                page = self.xml_processor.get_page_by_id(page_id)
                page_title = page.get("title", "") if page else ""
                
                # Get attachment title
                att_title = ""

                # Get attachment title using the new approach
                att_title = ""
                if att_id: # Only proceed if we have an attachment ID
                    att_details = self.xml_processor.get_attachment_by_id(att_id)
                    if att_details:
                        att_title = att_details.get("title", "") # Get title from the attachments dictionary

                # If still no title, use the original filename (without extension if possible)
                if not att_title and filename:
                    att_title = filename.split('.')[0] if '.' in filename else filename

                f.write(f"{src_path},{dst_path},{att_id},{page_id},{page_title},{att_title}\n")

        self.logger.info(f"Generated mapping file: {mapping_path}")
        self.logger.info(f"Processed {len(self.file_mapping)} files")
        self.logger.info(f"Skipped {len(self.skipped_files)} unknown files")

        return mapping_path

    def should_copy_file(self, src, dst):
        """Determine if src should replace dst based on size and modification time."""
        # If destination doesn't exist, always copy
        if not os.path.exists(dst):
            return True

        src_stat = os.stat(src)
        dst_stat = os.stat(dst)

        # If sizes differ, copy the source file
        if src_stat.st_size != dst_stat.st_size:
            return True

        # If sizes are the same, use the newer file
        return src_stat.st_mtime > dst_stat.st_mtime
