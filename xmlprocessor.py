import os
import re
import logging
from urllib.parse import unquote
import unicodedata
import xml.etree.ElementTree as ET
from typing import Dict, Optional, Tuple, List

from config import Config
from conversionstats import ConversionStats

INVALID_CHARS = re.compile(r'[+/\\:*?&"<>|^\[\]]')

class XmlProcessor:
    def __init__(self, config: Config, logger: logging.Logger, stats: ConversionStats, xml_path: Optional[str] = None):
        """Setup configuration"""
        self.config = config
        self.logger = logger
        self.stats = stats
        self.input_folder = config.INPUT_FOLDER
        self.input_folder_xml = config.INPUT_FOLDER_XML
        self.output_folder = config.OUTPUT_FOLDER

        # Main object caches
        self.spaces = {}  # Space ID -> Space object with page IDs
        self.page = {}  # Page/BlogPost ID -> page object with all related items
        self.users = {}   # User ID -> User info
        self.attachments = {}  # Attachment ID -> Attachment object with detailed info

        # Helper caches for lookup
        self._space_by_key = {}  # Space key -> Space ID
        self._page_by_title = {}  # page title -> page ID
        self._page_by_title_space = {}  # "title:spaceId" -> page ID
        self._label_by_id = {}  # name -> label ID
        self.page_id_mapping = {}  # Old Page ID -> New Page ID

        # Track processed XML files
        self.processed_xml_files = set()

    @staticmethod
    def verify_xml_file(root_folder: str, config: Config, logger: Optional[logging.Logger]) -> Tuple[bool, Optional[str]]:
        """
        Verify that a matching XML file exists for the given root folder.

        Args:
            root_folder: The input folder path (e.g., 'input/TUT')
            config: The configuration object containing path information
            logger: Optional logger for logging messages

        Returns:
            A tuple (exists, xml_path) where:
            - exists is True if a matching XML file exists, False otherwise
            - xml_path is the path to the XML file if it exists, None otherwise
        """

        # Extract the space key from the root folder path
        space_key = os.path.basename(root_folder)

        logger.debug(f"Looking for XML file for space key: {space_key}")

        # Pattern to match Confluence export folders for this space key
        pattern = re.compile(f"Confluence-space-export-{re.escape(space_key)}-.*")

        # Look for matching items in the XML input directory
        input_xml_path = config.INPUT_FOLDER_XML

        try:
            # First, check if there's a matching folder in the input-xml directory
            for item in os.listdir(input_xml_path):
                if pattern.match(item):
                    item_path = os.path.join(input_xml_path, item)

                    # Check if this is a directory that contains entities.xml
                    entities_path = os.path.join(item_path, "entities.xml")
                    if os.path.exists(entities_path):
                        logger.debug(f"Found matching XML file: '{entities_path}'")
                        return True, entities_path

            # If no specific match found, try the default location
            default_xml_path = os.path.join(input_xml_path, "entities.xml")
            if os.path.isfile(default_xml_path):
                logger.debug(f"Found default XML file: '{default_xml_path}'")
                return True, default_xml_path

        except (FileNotFoundError, PermissionError) as e:
            logger.error(f"Error accessing {input_xml_path}: {str(e)}")
            return False, None

        # If we didn't find a matching file, log a warning and return False
        logger.warning(f"No matching XML file found for space key: {space_key}")
        return False, None

    def add_xml_file(self, xml_path: str) -> bool:
        """
        Process and add data from an XML file to the existing cache.

        Args:
            xml_path: Path to the XML file

        Returns:
            bool: True if successful, False otherwise
        """
        if xml_path in self.processed_xml_files:
            self.logger.debug(f"XML file already processed: '{xml_path}'")
            return True

        self.logger.info(f"Processing XML file: '{xml_path}'")

        try:
            if not os.path.exists(xml_path):
                self.logger.error(f"XML file not found: {xml_path}")
                return False

            # Parse XML file
            tree = ET.parse(xml_path)
            root = tree.getroot()

            # Extract data from this XML file
            self._extract_users(root)
            self._extract_spaces(root)
            self._extract_pages(root)
            self._extract_comments(root)

            # Extract body content and apply relations
            body_page_relations = self._extract_body_page(root)
            success_count, missing_count = self._apply_body_page_relations(body_page_relations, self.page)
            
            # Update stats with body links results if stats object exists
            if hasattr(self, 'stats') and self.stats:
                self.stats.increment_body_links_stats(success_count, missing_count)
            
            # Link comments to their parent pages
            self._link_comments_to_pages()

            # Now extract attachments (after comments and body content are linked)
            self._extract_attachments(root)
            
            # Finally extract other page relations
            self._extract_outgoing_links(root)
            self._extract_labels_and_labellings(root)
            self._extract_page_properties(root) 

            # Mark as processed
            self.processed_xml_files.add(xml_path)

            # Rebuild helper indexes after adding new data
            self._build_helper_indexes()

            # Mark homepage titles if configured
            if hasattr(self.config, 'UNDERSCORE_HOMEPAGE_TITLES') and self.config.UNDERSCORE_HOMEPAGE_TITLES:
                self._underscore_homepage_titles()
            
            # Add to processed files list
            self.logger.info(f"Successfully processed XML file: '{xml_path}'")
            self.logger.info(f"Cache has {len(self.spaces)} spaces, {len(self.page)} pages, {len(self.users)} users")
            return True

        except FileNotFoundError:
            self.logger.error(f"XML file not found: {xml_path}")
            return False
        except ET.ParseError as e:
            self.logger.error(f"Error parsing XML file {xml_path}: {str(e)}")
            return False
        except Exception as e:
            self.logger.error(f"Error processing XML file {xml_path}: {str(e)}", exc_info=True)
            return False

    def _extract_users(self, root: ET.Element) -> None:
        """Extract all users from XML."""
        self.logger.debug(f"Extracting users from XML")
        user_count = 0

        for user_obj in root.findall(".//object[@class='ConfluenceUserImpl']"):
            id_elem = user_obj.find("./id[@name='key']")
            if id_elem is None or not id_elem.text:
                continue

            user_id = id_elem.text.strip()
            
            # Skip if user already exists in our dictionary
            if user_id in self.users:
                continue

            user = {
                "id": user_id,
                "name": "",
                "lowerName": "",
                "email": ""
            }

            # Get name
            name_elem = user_obj.find("./property[@name='name']")
            if name_elem is not None and name_elem.text:
                user["name"] = name_elem.text.strip()

            # Get lower name
            lower_name_elem = user_obj.find("./property[@name='lowerName']")
            if lower_name_elem is not None and lower_name_elem.text:
                user["lowerName"] = lower_name_elem.text.strip()

            # Get email
            email_elem = user_obj.find("./property[@name='email']")
            if email_elem is not None and email_elem.text:
                user["email"] = email_elem.text.strip()

            user_count += 1
            self.users[user_id] = user
            
        if self.stats:
            self.stats.update_xml_stats("users_extracted", user_count)

    def _extract_spaces(self, root: ET.Element) -> None:
        """Extract all spaces from XML."""
        self.logger.debug(f"Extracting space from XML")
        for space_obj in root.findall(".//object[@class='Space']"):
            id_elem = space_obj.find("./id[@name='id']")
            if id_elem is None or not id_elem.text:
                continue

            space_id = id_elem.text.strip()

            space = {
                "id": space_id,
                "name": "",
                "key": "",
                "description": "",
                "creatorId": "",
                "creationDate": "",
                "lastModifierId": "",
                "lastModificationDate": "",
                "homePageId": "",
                "pageIds": set(),  # All pages in this space
                "blogPostIds": set()  # All blog posts in this space
            }

            # Get name
            name_elem = space_obj.find("./property[@name='name']")
            if name_elem is not None and name_elem.text:
                space["name"] = name_elem.text.strip()

            # Get key
            key_elem = space_obj.find("./property[@name='key']")
            if key_elem is not None and key_elem.text:
                space["key"] = key_elem.text.strip()

            # Get creator ID
            creator_elem = space_obj.find("./property[@name='creator']/id[@name='key']")
            if creator_elem is not None and creator_elem.text:
                space["creatorId"] = creator_elem.text.strip()

            # Get creation date
            creation_date_elem = space_obj.find("./property[@name='creationDate']")
            if creation_date_elem is not None and creation_date_elem.text:
                space["creationDate"] = creation_date_elem.text.strip()

            # Get last modifier ID
            last_mod_elem = space_obj.find("./property[@name='lastModifier']/id[@name='key']")
            if last_mod_elem is not None and last_mod_elem.text:
                space["lastModifierId"] = last_mod_elem.text.strip()

            # Get last modification date
            last_mod_date_elem = space_obj.find("./property[@name='lastModificationDate']")
            if last_mod_date_elem is not None and last_mod_date_elem.text:
                space["lastModificationDate"] = last_mod_date_elem.text.strip()

            # Get home page ID
            home_page_elem = space_obj.find("./property[@name='homePage']/id[@name='id']")
            if home_page_elem is not None and home_page_elem.text:
                space["homePageId"] = home_page_elem.text.strip()

            self.spaces[space_id] = space

    def _extract_pages(self, root: ET.Element) -> None:
        self.logger.debug(f"Extracting pages from XML")
        """Extract all pages and blog posts from XML, only keeping the highest hibernateVersion of each title."""
        # Create dictionaries to track the highest hibernateVersion of each page/blog by title
        page_versions = {}  # title -> (hibernateVersion, page_obj)
        blog_versions = {}  # title -> (hibernateVersion, blog_obj)

        # Find the space ID from the Space object
        space_id = None
        for space_obj in root.findall(".//object[@class='Space']"):
            id_elem = space_obj.find("./id[@name='id']")
            if id_elem is not None and id_elem.text:
                space_id = id_elem.text.strip()
                #self.logger.debug(f"Found space ID: {space_id}")
                break

        if not space_id:
            self.logger.warning("No space ID found in XML, cannot process pages")
            return

        # First pass: collect all versions and find the highest for each page title
        self.logger.debug(f"Collecting page versions")
        for page_obj in root.findall(".//object[@class='Page']"):
            # Get page ID for logging
            id_elem = page_obj.find("./id[@name='id']")
            page_id = id_elem.text.strip() if id_elem is not None and id_elem.text else "unknown"
            
            # Check if this is a draft - skip if it is
            status_elem = page_obj.find("./property[@name='contentStatus']")
            # contentStatus can be: current, deleted, draft
            if status_elem is not None and status_elem.text and status_elem.text.strip() == "draft":
                self.logger.debug(f"Skipping draft page id '{page_id}'")
                continue
            

            # Get title
            title_elem = page_obj.find("./property[@name='title']")
            if title_elem is None or not title_elem.text:
                # Skip pages without titles
                self.logger.debug(f"Skipping page '{page_id}' with no title")
                continue
            title = title_elem.text.strip()

            # Get hibernateVersion number
            hibernate_version_elem = page_obj.find("./property[@name='hibernateVersion']")
            hibernate_version = 0
            if hibernate_version_elem is not None and hibernate_version_elem.text:
                try:
                    hibernate_version = int(hibernate_version_elem.text.strip())
                except ValueError:
                    pass

            # Store the page ID in our mapping, even if it's not the highest version
            self.page_id_mapping[page_id] = page_id

            # Check if we already have this page title and if this hibernateVersion is higher
            if title not in page_versions or hibernate_version > page_versions[title][0]:
                page_versions[title] = (hibernate_version, page_obj)

        # Same for blog posts
        self.logger.debug(f"Collecting blog versions")
        for blog_obj in root.findall(".//object[@class='BlogPost']"):
            # Get blog ID
            id_elem = blog_obj.find("./id[@name='id']")
            if id_elem is None or not id_elem.text:
                # Skip blogs without IDs
                self.logger.debug("Skipping blog with no ID")
                continue

            blog_id = id_elem.text.strip()

            # Get title
            title_elem = blog_obj.find("./property[@name='title']")
            if title_elem is None or not title_elem.text:
                # Skip blogs without titles
                self.logger.debug(f"Skipping blog '{blog_id}' with no title")
                continue
            title = title_elem.text.strip()
            
            # Check if this is a draft - skip if it is
            status_elem = blog_obj.find("./property[@name='contentStatus']")
            # contentStatus can be: current, deleted, draft
            if status_elem is not None and status_elem.text and status_elem.text.strip() == "draft":
                self.logger.debug(f"Skipping draft blog with title '{title}'")
                continue
            if status_elem is not None and status_elem.text and status_elem.text.strip() == "deleted":
                self.logger.debug(f"Skipping deleted blog with title '{title}'")
                continue

            # Get hibernateVersion number
            hibernate_version_elem = blog_obj.find("./property[@name='hibernateVersion']")
            hibernate_version = 0
            if hibernate_version_elem is not None and hibernate_version_elem.text:
                try:
                    hibernate_version = int(hibernate_version_elem.text.strip())
                except ValueError:
                    pass

            # Store the blog ID in our mapping
            self.page_id_mapping[blog_id] = blog_id

            # Check if we already have this blog title and if this hibernateVersion is higher
            if title not in blog_versions or hibernate_version > blog_versions[title][0]:
                blog_versions[title] = (hibernate_version, blog_obj)

        # Second pass: process only the highest version of each page/blog
        # and update the ID mapping to point all versions to the newest one
        self.logger.debug(f"Mapping highest versions")
        for title, (hibernate_version, page_obj) in page_versions.items():
            id_elem = page_obj.find("./id[@name='id']")
            newest_page_id = id_elem.text.strip() if id_elem is not None and id_elem.text else "unknown"
            self.logger.debug(f"Processing highest hibernateVersion '{hibernate_version}' of page '{title}' (ID: '{newest_page_id}')")

            # Update all page IDs with this title to point to the newest version
            for page_obj_all in root.findall(".//object[@class='Page']"):
                title_elem_all = page_obj_all.find("./property[@name='title']")
                if title_elem_all is not None and title_elem_all.text and title_elem_all.text.strip() == title:
                    id_elem_all = page_obj_all.find("./id[@name='id']")
                    if id_elem_all is not None and id_elem_all.text:
                        old_page_id = id_elem_all.text.strip()
                        self.page_id_mapping[old_page_id] = newest_page_id
                        if old_page_id != newest_page_id:
                            self.logger.debug(f"Mapping old page ID '{old_page_id}' to newest version '{newest_page_id}'")

            self._extract_page_item(page_obj, "Page", space_id)
            
        # Same for blog posts
        for title, (hibernate_version, blog_obj) in blog_versions.items():
            id_elem = blog_obj.find("./id[@name='id']")
            newest_blog_id = id_elem.text.strip() if id_elem is not None and id_elem.text else "unknown"
            self.logger.debug(f"Processing highest hibernateVersion '{hibernate_version}' of blog '{title}' (ID: '{newest_blog_id}')")
            
            # Update all blog IDs with this title to point to the newest version
            for blog_obj_all in root.findall(".//object[@class='BlogPost']"):
                title_elem_all = blog_obj_all.find("./property[@name='title']")
                if title_elem_all is not None and title_elem_all.text and title_elem_all.text.strip() == title:
                    id_elem_all = blog_obj_all.find("./id[@name='id']")
                    if id_elem_all is not None and id_elem_all.text:
                        old_blog_id = id_elem_all.text.strip()
                        self.page_id_mapping[old_blog_id] = newest_blog_id
                        if old_blog_id != newest_blog_id:
                            self.logger.debug(f"Mapping old blog ID '{old_blog_id}' to newest version '{newest_blog_id}'")

            self._extract_blog_item(blog_obj, "BlogPost")

    def _extract_page_item(self, page_obj: ET.Element, page_type: str, space_id: str) -> None:
        """Extract a page or blog post from XML, keeping only the highest version."""
        id_elem = page_obj.find("./id[@name='id']")
        if id_elem is None or not id_elem.text:
            return

        # Ensure page_id is stored as string
        page_id = str(id_elem.text.strip())

        # Get title
        title_elem = page_obj.find("./property[@name='title']")
        if title_elem is None or not title_elem.text:
            self.logger.warning(f"No title found for {page_type} {page_id}, using ID as title")
            title = page_id
        else:
            # Extract text content
            raw_xml = ET.tostring(title_elem, encoding='unicode')
            tag_name = title_elem.tag
            attr_name = 'name'
            attr_value = title_elem.get(attr_name)
            pattern = f'<{tag_name} {attr_name}="{attr_value}">(.*?)</{tag_name}>'
            match = re.search(pattern, raw_xml, re.DOTALL)
            if match:
                title = match.group(1)
            else:
                title = title_elem.text or f"{page_id}"

        title = self._sanitize_filename(title)

        # Get version
        version_elem = page_obj.find("./property[@name='version']")
        version = 0
        if version_elem is not None and version_elem.text:
            try:
                version = int(version_elem.text.strip())
            except ValueError:
                pass

        # Create basic page structure
        page = {
            "id": page_id,
            "type": page_type,
            "title": title,
            "version": version,
            "status": "",
            "spaceId": space_id,
            "parentId": "",
            "creatorId": "",
            "creationDate": "",
            "lastModifierId": "",
            "lastModificationDate": "",
            "attachments": [],
            "comments": [],
            "outgoingLinks": [],
            "labels": [],
            "pageProperties": [],
            "bodypage": None
        }

        # Get page status
        status_elem = page_obj.find("./property[@name='pageStatus']")
        if status_elem is not None and status_elem.text:
            page["status"] = status_elem.text.strip()

        # Add this page to the space's list of pages
        if space_id in self.spaces:
            if page_type == "Page":
                self.spaces[space_id]["pageIds"].add(page_id)
            else:
                self.spaces[space_id]["blogPostIds"].add(page_id)

        # Get parent ID (for pages)
        if page_type == "Page":
            parent_elem = page_obj.find("./property[@name='parent']/id[@name='id']")
            if parent_elem is not None and parent_elem.text:
                page["parentId"] = parent_elem.text.strip()

        # Get creator ID
        creator_elem = page_obj.find("./property[@name='creator']/id[@name='key']")
        if creator_elem is not None and creator_elem.text:
            page["creatorId"] = creator_elem.text.strip()

        # Get creation date
        creation_date_elem = page_obj.find("./property[@name='creationDate']")
        if creation_date_elem is not None and creation_date_elem.text:
            page["creationDate"] = creation_date_elem.text.strip()

        # Get last modifier ID
        last_mod_elem = page_obj.find("./property[@name='lastModifier']/id[@name='key']")
        if last_mod_elem is not None and last_mod_elem.text:
            page["lastModifierId"] = last_mod_elem.text.strip()

        # Get last modification date
        last_mod_date_elem = page_obj.find("./property[@name='lastModificationDate']")
        if last_mod_date_elem is not None and last_mod_date_elem.text:
            page["lastModificationDate"] = last_mod_date_elem.text.strip()

        # Store in cache
        self.page[page_id] = page

        # Update lookup dictionaries
        self._page_by_title[title] = page_id
        self._page_by_title_space[f"{title}:{space_id}"] = page_id 
 
    def _extract_blog_item(self, blog_obj: ET.Element, blog_type: str = "BlogPost") -> None:
        """
        Extract a blog post from XML.

        Args:
        blog_obj: The XML element containing the blog post data
        blog_type: The type of blog post (typically "BlogPost")
        """
        id_elem = blog_obj.find("./id[@name='id']")
        if id_elem is None or not id_elem.text:
            return

        # Ensure blog_id is stored as string
        blog_id = str(id_elem.text.strip())

        # Get space ID - with proper error handling
        space_elem = blog_obj.find("./property[@name='space']/id[@name='id']")

        if space_elem is None:
            self.logger.warning(f"No space element found for {blog_type} {blog_id}, skipping")
            return

        if not space_elem.text:
            self.logger.warning(f"Skipping empty space ID for {blog_type} {blog_id}")
            return

        space_id = space_elem.text.strip()

        # Get title
        title_elem = blog_obj.find("./property[@name='title']")
        if title_elem is None or not title_elem.text:
            self.logger.warning(f"No title found for {blog_type} {blog_id}, using ID as title")
            title = blog_id
        else:
            # Extract text content, handling CDATA sections
            raw_xml = ET.tostring(title_elem, encoding='unicode')
            tag_name = title_elem.tag
            attr_name = 'name'
            attr_value = title_elem.get(attr_name)
            pattern = f'<{tag_name} {attr_name}="{attr_value}">(.*?)</{tag_name}>'
            match = re.search(pattern, raw_xml, re.DOTALL)

            if match:
                title = match.group(1)
            else:
                title = title_elem.text or f"{blog_id}"

        title = self._sanitize_filename(title)

        # Get version
        version_elem = blog_obj.find("./property[@name='version']")
        version = 0
        if version_elem is not None and version_elem.text:
            try:
                version = int(version_elem.text.strip())
            except ValueError:
                pass

        # Create basic blog post structure
        blog = {
            "id": blog_id,
            "type": blog_type,
            "title": title,
            "version": version,
            "status": "",
            "spaceId": space_id,
            "creatorId": "",
            "creationDate": "",
            "lastModifierId": "",
            "lastModificationDate": "",
            "attachments": [],
            "comments": [],
            "outgoingLinks": [],
            "labels": [],
            "pageProperties": [],
            "bodypage": None
        }

        # Get blog post status
        status_elem = blog_obj.find("./property[@name='contentStatus']")
        if status_elem is not None and status_elem.text:
            blog["status"] = status_elem.text.strip()

        # Add this blog post to the space's list of blog posts
        if space_id in self.spaces:
            self.spaces[space_id]["blogPostIds"].add(blog_id)

        # Get creator ID
        creator_elem = blog_obj.find("./property[@name='creator']/id[@name='key']")
        if creator_elem is not None and creator_elem.text:
            blog["creatorId"] = creator_elem.text.strip()

        # Get creation date
        creation_date_elem = blog_obj.find("./property[@name='creationDate']")
        if creation_date_elem is not None and creation_date_elem.text:
            blog["creationDate"] = creation_date_elem.text.strip()

        # Get last modifier ID
        last_mod_elem = blog_obj.find("./property[@name='lastModifier']/id[@name='key']")
        if last_mod_elem is not None and last_mod_elem.text:
            blog["lastModifierId"] = last_mod_elem.text.strip()

        # Get last modification date
        last_mod_date_elem = blog_obj.find("./property[@name='lastModificationDate']")
        if last_mod_date_elem is not None and last_mod_date_elem.text:
            blog["lastModificationDate"] = last_mod_date_elem.text.strip()

        # Store in cache
        self.page[blog_id] = blog

        # Update lookup dictionaries
        self._page_by_title[title] = blog_id
        self._page_by_title_space[f"{title}:{space_id}"] = blog_id
        
    def _extract_attachments(self, root: ET.Element) -> None:
        """Extract attachments and link them to their page."""
        self.logger.info(f"Extracting attachments from XML")
        attachment_count = 0

        for att_obj in root.findall(".//object[@class='Attachment']"):
            id_elem = att_obj.find("./id[@name='id']")
            if id_elem is None or not id_elem.text:
                self.logger.debug("Skipping attachment - no ID found")
                continue

            att_id = id_elem.text.strip()

            attachment = {
                "id": att_id,
                "space_id": "",
                "title": "",
                "creatorId": "",
                "lastModifierId": "",
                "creationDate": "",
                "lastModificationDate": "",
                "version": 0,
                "hibernateVersion": 0,
                "containerContent_id": ""
            }

            # Get title (the filename used in the XML)
            title_elem = att_obj.find("./property[@name='title']")
            if title_elem is not None and title_elem.text:
                attachment["title"] = self._sanitize_filename(title_elem.text.strip())

            # Get creator ID
            creator_elem = att_obj.find("./property[@name='creator']/id[@name='key']")
            if creator_elem is not None and creator_elem.text:
                attachment["creatorId"] = creator_elem.text.strip()

            # Get last modifier ID
            last_mod_elem = att_obj.find("./property[@name='lastModifier']/id[@name='key']")
            if last_mod_elem is not None and last_mod_elem.text:
                attachment["lastModifierId"] = last_mod_elem.text.strip()

            # Get creation date
            creation_date_elem = att_obj.find("./property[@name='creationDate']")
            if creation_date_elem is not None and creation_date_elem.text:
                attachment["creationDate"] = creation_date_elem.text.strip()

            # Get last modification date
            last_mod_date_elem = att_obj.find("./property[@name='lastModificationDate']")
            if last_mod_date_elem is not None and last_mod_date_elem.text:
                attachment["lastModificationDate"] = last_mod_date_elem.text.strip()

            # Get version
            version_elem = att_obj.find("./property[@name='version']")
            if version_elem is not None and version_elem.text:
                try:
                    attachment["version"] = int(version_elem.text.strip())
                except ValueError:
                    pass

            # Get hibernateVersion
            hibernate_version_elem = att_obj.find("./property[@name='hibernateVersion']")
            if hibernate_version_elem is not None and hibernate_version_elem.text:
                try:
                    attachment["hibernateVersion"] = int(hibernate_version_elem.text.strip())
                except ValueError:
                    pass

            # Get containerContent ID
            container_elem = att_obj.find("./property[@name='containerContent']/id[@name='id']")
            if container_elem is not None and container_elem.text:
                attachment["containerContent_id"] = container_elem.text.strip()

            # Get space ID
            space_elem = att_obj.find("./property[@name='space']/id[@name='id']")
            if space_elem is not None and space_elem.text:
                attachment["space_id"] = space_elem.text.strip()

            # Store in attachments dictionary
            self.attachments[att_id] = attachment
            attachment_count += 1
        
        self.logger.info(f"Extracted {attachment_count} attachments from XML")

    def _extract_comments(self, root: ET.Element) -> None:
        """Extract comments and create a cache for them."""
        self.logger.info("Extracting comments")

        # Initialize comment cache if it doesn't exist
        if not hasattr(self, 'comments'):
            self.comments = {}
            
        for comment_obj in root.findall(".//object[@class='Comment']"):
            id_elem = comment_obj.find("./id[@name='id']")
            if id_elem is None or not id_elem.text:
                continue

            comment_id = id_elem.text.strip()

            comment = {
                "id": comment_id,
                "creatorId": "",
                "creationDate": "",
                "containerContentId": "",  # Store the ID of the page/blog this comment belongs to
                "bodypage": None  # Will be filled later
            }

            # Get creator ID
            creator_elem = comment_obj.find("./property[@name='creator']/id[@name='key']")
            if creator_elem is not None and creator_elem.text:
                comment["creatorId"] = creator_elem.text.strip()

            # Get creation date
            creation_date_elem = comment_obj.find("./property[@name='creationDate']")
            if creation_date_elem is not None and creation_date_elem.text:
                comment["creationDate"] = creation_date_elem.text.strip()

            # Find the container page (Page or BlogPost)
            container_elem = comment_obj.find("./property[@name='containerContent']/id[@name='id']")
            if container_elem is not None and container_elem.text:
                comment["containerContentId"] = container_elem.text.strip()

            # Store in cache
            self.comments[comment_id] = comment

        self.logger.info(f"Extracted {len(self.comments)} comments")

    def _extract_outgoing_links(self, root: ET.Element) -> None:
        """Extract outgoing links and link them to their source page."""
        for link_obj in root.findall(".//object[@class='OutgoingLink']"):
            id_elem = link_obj.find("./id[@name='id']")
            if id_elem is None or not id_elem.text:
                continue

            link_id = id_elem.text.strip()

            link = {
                "id": link_id,
                "destinationPageTitle": "",
                "destinationSpaceKey": ""
            }

            # Get destination page title
            dest_title_elem = link_obj.find("./property[@name='destinationPageTitle']")
            if dest_title_elem is not None and dest_title_elem.text:
                link["destinationPageTitle"] = dest_title_elem.text.strip()

            # Get destination space key
            dest_space_elem = link_obj.find("./property[@name='destinationSpaceKey']")
            if dest_space_elem is not None and dest_space_elem.text:
                link["destinationSpaceKey"] = dest_space_elem.text.strip()

            # Find the source page
            source_elem = link_obj.find("./property[@name='sourcepage']/id[@name='id']")
            if source_elem is not None and source_elem.text:
                page_id = source_elem.text.strip()
                if page_id in self.page:
                    self.page[page_id]["outgoingLinks"].append(link)

    def _extract_labels_and_labellings(self, root: ET.Element) -> None:
        """Extract labels and link them to their page via labellings."""
        # First extract all labels
        for label_obj in root.findall(".//object[@class='Label']"):
            id_elem = label_obj.find("./id[@name='id']")
            if id_elem is None or not id_elem.text:
                continue

            label_id = id_elem.text.strip()

            label = {
                "id": label_id,
                "name": "",
                "namespace": ""
            }

            # Get name
            name_elem = label_obj.find("./property[@name='name']")
            if name_elem is not None and name_elem.text:
                label["name"] = self._clean_cdata(name_elem.text.strip())

            # Get namespace
            namespace_elem = label_obj.find("./property[@name='namespace']")
            if namespace_elem is not None and namespace_elem.text:
                label["namespace"] = namespace_elem.text.strip()

            self._label_by_id[label_id] = label

        # Now process labellings and link labels to page
        for labelling_obj in root.findall(".//object[@class='Labelling']"):
            # Get label ID
            label_elem = labelling_obj.find("./property[@name='label']/id[@name='id']")
            if label_elem is None or not label_elem.text:
                continue

            label_id = label_elem.text.strip()
            if label_id not in self._label_by_id:
                continue

            # Get page ID
            page_elem = labelling_obj.find("./property[@name='page']/id[@name='id']")
            if page_elem is not None and page_elem.text:
                page_id = page_elem.text.strip()
                if page_id in self.page:
                    self.page[page_id]["labels"].append(self._label_by_id[label_id])

    def _extract_page_properties(self, root: ET.Element) -> None:
        """Extract page properties and link them to their page."""
        for prop_obj in root.findall(".//object[@class='pageProperty']"):
            id_elem = prop_obj.find("./id[@name='id']")
            if id_elem is None or not id_elem.text:
                continue

            prop_id = id_elem.text.strip()

            prop = {
                "id": prop_id,
                "name": "",
                "stringValue": ""
            }

            # Get name
            name_elem = prop_obj.find("./property[@name='name']")
            if name_elem is not None and name_elem.text:
                prop["name"] = name_elem.text.strip()

            # Get string value
            value_elem = prop_obj.find("./property[@name='stringValue']")
            if value_elem is not None and value_elem.text:
                prop["stringValue"] = value_elem.text.strip()

            # Find the page
            page_elem = prop_obj.find("./property[@name='page']/id[@name='id']")
            if page_elem is not None and page_elem.text:
                page_id = page_elem.text.strip()
                if page_id in self.page:
                    self.page[page_id]["pageProperties"].append(prop)

    def _extract_body_page(self, root: ET.Element) -> List:
        """
        Extract body page content and link it to its page or comment.
        
        Args:
            root: XML root element
            
        Returns:
            list: List of tuples (content_id, body_dict) for later linking
        """
        self.logger.info("Starting body page extraction")
        
        body_page_relations = []

        for body_obj in root.findall(".//object[@class='BodyContent']"):
            id_elem = body_obj.find("./id[@name='id']")
            if id_elem is None or not id_elem.text:
                self.logger.debug("Skipping body object - no ID found")
                continue

            body_id = id_elem.text.strip()

            body = {
                "id": body_id,
                "body": "",
                "bodyType": ""
            }

            # Get body content
            body_elem = body_obj.find("./property[@name='body']")
            if body_elem is not None and body_elem.text:
                body["body"] = self._clean_cdata(body_elem.text.strip())
            else:
                self.logger.debug(f"Body found, but no content found for ID '{body_id}'. Body seems empty.")

            # Get body type
            body_type_elem = body_obj.find("./property[@name='bodyType']")
            if body_type_elem is not None and body_type_elem.text:
                body["bodyType"] = body_type_elem.text.strip()

            # Find the associated content (Page, BlogPost, or Comment)
            content_elem = body_obj.find("./property[@name='content']")
            if content_elem is not None:
                content_id_elem = content_elem.find("./id[@name='id']")
                if content_id_elem is not None and content_id_elem.text:
                    content_id = content_id_elem.text.strip()
                    # Store the content class to determine how to handle it
                    content_class = content_elem.get("class")
                    body["content_class"] = content_class
                    body_page_relations.append((content_id, body))
                else:
                    self.logger.debug(f"Content ID not found for: '{body_id}'.")
        
        self.logger.info(f"Collected {len(body_page_relations)} body-page relationships")
        return body_page_relations
    
    def _apply_body_page_relations(self, body_page_relations: List, page_dict: Dict) -> Tuple[int, int]:
        """
        Apply the collected body-content relationships to the appropriate objects.
        Handles both Page/BlogPost and Comment relationships.

        Args:
            body_page_relations: List of tuples (content_id, body_dict)
            page_dict: Dictionary of pages/blog posts
            comment_cache: Dictionary of comments

        Returns:
            Tuple of (success_count, missing_count)
        """
        self.logger.info(f"Applying {len(body_page_relations)} body-page relationships")
        
        success_count = 0
        missing_count = 0
                
        # Process the body-content relationships
        for content_id, body in body_page_relations:
            content_class = body.get("content_class", "")
            
            # Handle comments differently from pages/blog posts
            if "Comment" in content_class:
                if hasattr(self, 'comments') and content_id in self.comments:
                    self.comments[content_id]["bodypage"] = body
                    success_count += 1
                    self.logger.debug(f"Successfully linked comment '{content_id}' to body '{body['id']}'")
                else:
                    missing_count += 1
                    self.logger.debug(f"Could not find comment '{content_id}' for body '{body['id']}'")
            else:
                # Handle Pages and BlogPosts
                if content_id in page_dict:
                    page_dict[content_id]["bodypage"] = body
                    success_count += 1
                else:
                    missing_count += 1
                    self.logger.debug(f"Could not find page '{content_id}' for body '{body['id']}'")
        
        self.logger.info(f"Applied {success_count} relationships, {missing_count} pages/comments not found")
        return success_count, missing_count

    def _link_comments_to_pages(self) -> None:
        """Link comments to their parent pages."""
        if not hasattr(self, 'comments'):
            self.logger.debug("No comments to link")
            return

        self.logger.debug("Linking comments to their parent pages")

        linked_count = 0

        for comment_id, comment in self.comments.items():
            container_id = comment.get("containerContentId")
            if container_id and container_id in self.page:
                # Add this comment to the page's comments list
                self.page[container_id]["comments"].append(comment)
                linked_count += 1
                self.logger.debug(f"Linked comment '{comment_id}' to page '{container_id}'")

        self.logger.debug(f"Linked {linked_count} comments to their parent pages")
        
    def _build_helper_indexes(self) -> None:
        """Build helper indexes for faster lookups."""
        # Space key -> Space ID
        for space_id, space in self.spaces.items():
            if space["key"]:
                self._space_by_key[space["key"]] = space_id

        # page title -> page ID
        for page_id, page in self.page.items():
            if page["title"]:
                self._page_by_title[page["title"]] = page_id

    def _underscore_homepage_titles(self, prepend_underscore: bool = True) -> None:
        """
        Mark homepage titles by prepending an underscore.
        This helps identify space homepages in the file system.

        Args:
            prepend_underscore: Whether to prepend an underscore to homepage titles
        """
        if not prepend_underscore:
            return

        self.logger.info("Marking homepage titles with underscore prefix")

        # Find all space homepages and modify their titles
        for space_id, space in self.spaces.items():
            homepage_id = space.get("homePageId")
            if not homepage_id or homepage_id not in self.page:
                continue

            # Get the homepage
            homepage = self.page[homepage_id]
            original_title = homepage["title"]

            # Skip if already prefixed
            if original_title.startswith("_"):
                continue

            # Update the title with underscore prefix
            new_title = f"_{original_title}"
            homepage["title"] = new_title

            # Update the title in the lookup dictionaries
            if original_title in self._page_by_title:
                del self._page_by_title[original_title]
                self._page_by_title[new_title] = homepage_id

            # Update the title:spaceId key
            old_key = f"{original_title}:{space_id}"
            new_key = f"{new_title}:{space_id}"
            if old_key in self._page_by_title_space:
                del self._page_by_title_space[old_key]
                self._page_by_title_space[new_key] = homepage_id

            self.logger.debug(f"Marked homepage title: '{original_title}' -> '{new_title}'")
            
    def _sanitize_filename(self, filename: str) -> str:
        """
        Consistently sanitize filenames for Obsidian compatibility.

        Combines regex efficiency with specific character handling for optimal
        performance and accuracy.
        """
        if not filename:
            self.logger.debug(f"Could not find a filename to sanitize: '{filename}'")
            return "unnamed"

        # Store input for logging
        original_filename = filename

        # URL decode the filename
        filename = unquote(filename)

        # Normalize Unicode characters
        filename = unicodedata.normalize('NFKC', filename)

        # Filter bad/invisible characters, apply URL decoding
        filename = ''.join(c for c in filename if self.is_valid_char(c))
        
        # Trim leading/trailing periods and spaces
        filename = filename.strip('. ')

        # Replace remaining problematic characters with dashes
        filename = re.sub(INVALID_CHARS, '-', filename)

        # Handle spaces according to configuration
        if self.config.USE_UNDERSCORE_IN_FILENAMES:
            filename = filename.replace(' ', '_')

        # Ensure the filename is not empty
        if not filename:
            self.logger.warning(f"Could not sanitize filename: '{original_filename}'")
            return self._clean_cdata(original_filename)

        self.logger.debug(f"Sanitized filename from '{original_filename}' to '{filename}'")
        return self._clean_cdata(filename)
    
    def _clean_cdata(self, text: str) -> str:
        """
        Clean CDATA tags from text content.
        
        Args:
            text (str): The text that might contain CDATA tags
            
        Returns:
            str: Cleaned text without CDATA markers
        """
        if not text:
            return ""
        
        # Check for CDATA pattern
        if text.startswith('<![CDATA[') and text.endswith(']]>'):
            # Extract content between CDATA markers
            return text[9:-3]  # Remove <![CDATA[ and ]]>
        
        return text
    
    def is_valid_char(self, char):
        """
        Comprehensive character validation that combines all checks:
        - Unicode category validation
        - Private use area detection
        - Specific character exclusions
        """
        if not char:
            return False

        # Mapping of URL-encoded characters to their regular equivalents
        url_encoded_mapping = {
            '%20': ' ',    # Space
            '%3C': '<',    # Less than
            '%3E': '>',    # Greater than
            '%3A': ':',    # Colon
            '%22': '"',    # Double quote
            '%2F': '/',    # Forward slash
            '%5C': '\\',   # Backslash
            '%7C': '|',    # Vertical bar or pipe
            '%3F': '?',    # Question mark
            '%2A': '*'     # Asterisk
        }

        # Check for specific characters to exclude
        excluded_chars = {
            '\u200b',  # Zero width space
            '\u200c',  # Zero width non-joiner
            '\u200d',  # Zero width joiner
            '\u200e',  # Left-to-right mark
            '\u200f',  # Right-to-left mark
            '\ufeff'   # Byte order mark
        }
        
        # Replace URL-encoded characters with their regular equivalents
        if char in url_encoded_mapping:
            char = url_encoded_mapping[char]
        
        # Check for specific characters to exclude
        if char in excluded_chars:
            return False

        # Check for private use areas
        code_point = ord(char)
        if (0xE000 <= code_point <= 0xF8FF or          # Basic Multilingual Plane private use area
            0xF0000 <= code_point <= 0xFFFFD or        # Supplementary Private Use Area-A
            0x100000 <= code_point <= 0x10FFFD):       # Supplementary Private Use Area-B
            return False

        # Get Unicode category
        category = unicodedata.category(char)

        # Accepts these categories:
        # Cc: Other, Control - Non-printable control characters (e.g., \n, \r, \t)
        # Cf: Other, Format - Non-printable format characters (e.g., zero-width joiner, zero-width non-joiner)
        # Co: Other, Private Use - Characters reserved for private use, without standardized meaning
        # Cs: Other, Surrogate - Surrogate code points used in UTF-16 encoding, not valid on their own

        # Reject control characters
        if category in {'Cc', 'Cf', 'Co', 'Cs'}:
            return False

        # Accepts these categories:
        # Lu: Uppercase Letter
        # Ll: Lowercase Letter
        # Lt: Titlecase Letter
        # Lm: Modifier Letter
        # Lo: Other Letter
        # Nd: Decimal Number
        # Nl: Letter Number
        # No: Other Number
        # Pd: Dash Punctuation
        # Pe: Close Punctuation
        # Ps: Open Punctuation
        # Pi: Initial Punctuation
        # Pf: Final Punctuation
        # Pc: Connector Punctuation
        # Po: Other Punctuation
        # Sm: Math Symbol
        # Sc: Currency Symbol
        # Sk: Modifier Symbol
        # So: Other Symbol
        # Zs: Space Separator
        return category.startswith(('L', 'N', 'P', 'S', 'Z'))
    
    # Public
    def get_space_by_id(self, space_id: str) -> Optional[dict]:
        """Get space information by ID."""
        return self.spaces.get(str(space_id))

    def get_space_by_key(self, space_key: str) -> Optional[dict]:
        """Get space information by key."""
        space_id = self._space_by_key.get(space_key)
        return self.spaces.get(space_id) if space_id else None

    def get_space_key_by_xml(self, xml_path: str) -> str:
        """Extract space key from XML"""
        tree = ET.parse(xml_path)
        root = tree.getroot()
        for space_obj in root.findall(".//object[@class='Space']"):
            key = space_obj.find("./property[@name='key']")
            if key is None or not key.text:
                continue

            return key.text.strip()

    def get_space_id_by_page_id(self, page_id: str) -> Optional[str]:
        """
        Get the space ID associated with a given page ID.

        Args:
        page_id: The ID of the page

        Returns:
        The space ID if found, None otherwise
        """
        self.logger.info(f"Trying to get space ID for page ID: {page_id}")

        # Get page info
        page_info = self.get_page_by_id(page_id)
        if not page_info:
            self.logger.debug(f"Page ID '{page_id}' not found in cache")
            return None

        # Get space ID from page info
        space_id = page_info.get("spaceId")
        if space_id:
            return space_id

        self.logger.debug(f"No space ID found for page ID: '{page_id}'")
        return None

    def get_space_key_by_page_id(self, page_id: str) -> Optional[str]:
        """
        Get the space key associated with a given page ID.

        Args:
            page_id: The ID of the page

        Returns:
            The space key if found, None otherwise
        """
        self.logger.info(f"Trying to get space key for page ID: '{page_id}'")

        # First get the space ID
        space_id = self.get_space_id_by_page_id(page_id)
        if not space_id:
            self.logger.debug(f"No space ID found for page ID: '{page_id}'")
            return None

        # Get space info
        space_info = self.get_space_by_id(space_id)
        if not space_info:
            self.logger.debug(f"Space ID '{space_id}' not found in cache")
            return None

        # Get space key from space info
        space_key = space_info.get("key")
        if space_key:
            return space_key

        self.logger.debug(f"No space key found for space ID: '{space_id}'")
        return None
        
    def get_page_by_id(self, page_id: str) -> Optional[dict]:
        """Get page (page or blog post) information by ID, using ID mapping for old versions."""
        if not page_id:
            return None

        page_id_str = str(page_id)

        # Check if this is an old ID that maps to a newer version
        if hasattr(self, 'page_id_mapping') and page_id_str in self.page_id_mapping:
            mapped_id = self.page_id_mapping[page_id_str]
            if mapped_id != page_id_str:
                self.logger.debug(f"Mapped old ID '{page_id_str}' to newest version '{mapped_id}'")
                page_id_str = mapped_id

        return self.page.get(page_id_str)
    
    def get_page_title_by_id(self, page_id: str) -> Optional[str]:
        """
        Get the title of a page by its ID.

        Args:
            page_id: The ID of the page

        Returns:
            The title of the page or None if not found
        """
        # Convert page_id to string to ensure consistent lookup
        page_id_str = str(page_id)
        page = self.get_page_by_id(page_id_str)

        if page and "title" in page:
            return page["title"]
        # Check if this is a space homepage
        for _, space in self.spaces.items():
            if space.get("homePageId") == page_id and space.get("name"):
                return f"{space['name']} Home"

        # If we have a space key, try to use that
        for _, space in self.spaces.items():
            if space.get("homePageId") == page_id and space.get("key"):
                return f"{space['key']} Home"

        # Default fallback
        if page is None:
            self.logger.debug(f"Page ID '{page_id_str}' not found in cache")
        return page

    def get_page_id_by_filename(self, filename: str, input_folder_path: str = None) -> Optional[str]:
        """
        Get page ID by filename, with special handling for index.html files.

        Args:
            filename: The filename to look up
            input_folder_path: The path to the input folder containing the file

        Returns:
            The page ID or None if not found
        """
        #self.logger.info(f"Processing file: '{filename}'")

        # Special handling for index.html (home page)
        if filename.lower() == "index.html":
            # Extract space key from input folder path if provided
            space_key = None
            if input_folder_path:
                # Get the folder name from the path (which should match the space key)
                space_key = os.path.basename(os.path.dirname(input_folder_path))
                #self.logger.debug(f"Extracted space key from path: '{space_key}'")

                # Look up space by key
                space = self.get_space_by_key(space_key)
                if space and space.get("homePageId"):
                    home_page_id = space["homePageId"]
                    #self.logger.debug(f"Found home page ID for space '{space_key}': '{home_page_id}'")
                    return home_page_id

            # Fallback to old behavior if space-specific lookup fails
            for _, space in self.spaces.items():
                if space.get("homePageId"):
                    home_page_id = space["homePageId"]
                    #self.logger.debug(f"Found home page ID: '{home_page_id}'")
                    return home_page_id

        # Regular file lookup by removing extension
        base_name = os.path.splitext(filename)[0]
        #self.logger.debug(f"Base name extracted: '{base_name}'")

        # Case 1: numeric filename (e.g., 1234567890.html)
        if base_name.isdigit():
            self.logger.debug(f"Extracted numeric page ID: '{base_name}'")
            # Verify this ID exists in our page
            if base_name in self.page:
                return base_name
            else:
                self.logger.debug(f"ID '{base_name}' not found in page cache")
                return base_name

        # Case 2: string_numeric filename (e.g., Some-Page_48267601.html)
        name_id_pattern = r'^(.*?)_(\d{6,10})\.html$'
        match = re.search(name_id_pattern, filename)
        if match:
            page_id = match.group(2)  # The numeric ID part
            #self.logger.debug(f"Extracted page ID from filename: '{page_id}'")
            # Verify this ID exists in our page
            if page_id in self.page:
                return page_id
            else:
                self.logger.debug(f"ID '{page_id}' not found in page cache")

        # Case 3: title-based filename (e.g., Some-Page.html)
        title_pattern = r'^(.*?)(?:_\d+)?\.html$'
        title_match = re.search(title_pattern, filename)
        if title_match:
            title_v1 = title_match.group(1).lower()
            title_v2 = title_match.group(1).replace('-', ' ').lower()
            self.logger.debug(f"Looking for page with title similar to: '{title_v1}' or '{title_v2}'")

            # Try to find page with a similar title
            for page_id, page in self.page.items():
                if page["title"].lower() == title_v2 or page["title"].lower() == title_v1:
                    self.logger.debug(f"Found page with matching title: '{page_id}'")
                    return page_id
        
        self.logger.debug(f"Could not extract page ID from filename: '{filename}'")
        return None

    def get_user_by_id(self, user_id: str) -> Optional[dict]:
        """Get user information by ID."""
        return self.users.get(str(user_id))

    def get_pages_by_space_id(self, space_id: str) -> List[dict]:
        """Get all pages in a space."""
        space = self.get_space_by_id(space_id)
        if not space:
            return []
        return [self.page[page_id] for page_id in space["pageIds"] if page_id in self.page]

    def get_page_by_title(self, title: str) -> Optional[dict]:
        """Get page information by title."""
        page_id = self._page_by_title.get(title)
        return self.page.get(page_id) if page_id else None

    def get_all_related_page(self, page_id: str) -> dict:
        """Get all page related to a specific page/blog post."""
        page = self.get_page_by_id(page_id)
        if not page:
            return {}

        # Deep copy to avoid modifying cached data
        result = dict(page)

        # Add user information
        if page.get("creatorId") and page["creatorId"] in self.users:
            result["creator"] = self.users[page["creatorId"]]

        if page.get("lastModifierId") and page["lastModifierId"] in self.users:
            result["lastModifier"] = self.users[page["lastModifierId"]]

        # Add space information
        if page.get("spaceId") and page["spaceId"] in self.spaces:
            result["space"] = self.spaces[page["spaceId"]]

        # Add parent information if it's a page
        if page.get("parentId") and page["parentId"] in self.page:
            result["parent"] = self.page[page["parentId"]]

        return result

    def get_parent_title_by_id(self, page_id: str) -> Optional[str]:
        """
        Get the title of the parent page for a given page ID.
        Uses pre-cached parent title for efficiency.

        Args:
            page_id: The ID of the page

        Returns:
            The title of the parent page or None if not found
        """

        self.logger.info(f"Trying to get parent title for page ID: '{page_id}'")

        # Get page info
        page_info = self.get_page_by_id(page_id)
        if not page_info:
            self.logger.debug(f"Page ID '{page_id}' not found in cache. Using empty text as fallback.")
            return None

        # If no parent ID, use empty fallback
        if not page_info.get("parentId"):
            self.logger.debug(f"Cannot find parent ID. Using empty text as fallback.")
            return None

        # Get parent info using parent ID
        parent_id = page_info["parentId"]
        parent_info = self.get_page_by_id(parent_id)

        if parent_info and parent_info.get("title"):
            self.logger.debug(f"Found parent title: '{parent_info['title']}'")
            return parent_info["title"]

        # If all else fails, return empty
        self.logger.debug("Cannot find parent title. Using empty text as fallback.")
        return None

    def find_space_id_for_blog(self, blog_id: str) -> Optional[str]:
        """
        Alternative methods to find the space ID for a blog post.

        Args:
            blog_id: The ID of the blog post

        Returns:
            The space ID if found, None otherwise
        """
        #self.logger.info(f"Attempting to find space ID for blog ID: '{blog_id}'")

        # Method 1: Check if blog is referenced in any space's blogPostIds
        for space_id, space in self.spaces.items():
            if blog_id in space.get("blogPostIds", set()):
                return space_id

        # Method 2: Check content properties for space reference
        blog = self.page.get(blog_id)
        if blog:
            for prop in blog.get("pageProperties", []):
                if prop.get("name") == "space" and prop.get("stringValue"):
                    space_key = prop.get("stringValue")
                    # Look up space ID by key
                    for space_id, space in self.spaces.items():
                        if space.get("key") == space_key:
                            return space_id

        self.logger.debug(f"Could not find space ID for blog '{blog_id}'")
        return None

    def get_attachment_by_id(self, att_id: str) -> Optional[dict]:
        """Get attachment information by ID."""
        return self.attachments.get(att_id)

    def get_attachments_by_page_id(self, page_id: str) -> List[dict]:
        """Get all attachments for a page."""
        # Ensure page_id is a string for consistent comparison
        page_id_str = str(page_id)
        
        # Check if this is an old ID that maps to a newer version
        if hasattr(self, 'page_id_mapping') and page_id_str in self.page_id_mapping:
            mapped_id = self.page_id_mapping[page_id_str]
            if mapped_id != page_id_str:
                page_id_str = mapped_id
                
        # Get attachments with more detailed logging
        attachments = []
        for att_id, attachment in self.attachments.items():
            # Handle both string and integer page_id values
            att_page_id = str(attachment.get('containerContent_id', ''))

            if att_page_id == page_id_str:
                attachments.append(attachment)
                #self.logger.debug(f"Found attachment '{att_id}' for page '{page_id_str}': '{attachment.get('title', '')}'")
        
        if not attachments:
            self.logger.debug(f"No attachments found in page '{page_id_str}' (checked {len(self.attachments)} attachments)")

            # Check if the page exists in our cache
            if page_id_str not in self.page:
                self.logger.warning(f"Page '{page_id_str}' not found in page cache")
        else:
            self.logger.debug(f"Found {len(attachments)} attachments for page '{page_id_str}'")
        
        return attachments

    def get_attachment_by_filename(self, filename: str) -> Optional[dict]:
        """
        Find an attachment by its filename, optionally filtering by page ID.

        Args:
            filename: The filename to search for
            page_id: Optional page ID to restrict the search

        Returns:
            The attachment dict if found, None otherwise
        """
        self.logger.info(f"Looking for attachment with filename: '{filename}'")

        # Extract just the base filename without query parameters
        base_filename = os.path.basename(filename.split('?')[0])

        # Try exact match
        for _, attachment in self.attachments.items():
            if attachment.get('title') == base_filename:
                return attachment

        self.logger.debug(f"No attachment found for filename: '{base_filename}'")
        return None

    def get_attachment_filename_by_ids(self, page_id: str, att_id: str) -> Optional[str]:
        """
        Get the filename of an attachment by its page ID and attachment ID.
        
        Args:
        page_id: The ID of the page containing the attachment
        att_id: The ID of the attachment
        
        Returns:
        The filename of the attachment if found, None otherwise
        """
        attachment = self.attachments.get(att_id)
        if attachment and attachment.get('containerContent_id') == page_id:
            return attachment.get('title')
        return None

    def get_attachment_id_from_link(self, link: str) -> Optional[str]:
        """
        Extract attachment ID from a Confluence attachment link.

        Args:
            link: The attachment link

        Returns:
            The attachment ID if found, None otherwise
        """
        # Try to extract page ID from the link path
        page_match = re.search(r'/attachments/(\d+)/', link)
        if not page_match:
            self.logger.debug(f"Could not extract page ID from link: '{link}'")
            return None

        page_id = page_match.group(1)

        # Extract the filename from the link
        filename = os.path.basename(link.split('?')[0])

        # Look for the attachment in our data
        for att_id, attachment in self.attachments.items():
            if attachment.get('page_id') == page_id and attachment.get('title') == filename:
                return att_id

        self.logger.debug(f"No attachment ID found for link: '{link}'")
        return None

    def get_page_attachment_mapping(self) -> dict:
        """
        Returns a mapping of page IDs to their attachment IDs.

        Returns:
            dict: Mapping of {page_id: [list of attachment_ids]}
        """
        if not hasattr(self, 'attachments'):
            self.logger.warning("No attachments found in XML data")
            return {}

        page_attachments = {}

        for att_id, attachment in self.attachments.items():
            page_id = attachment.get('page_id')
            if page_id:
                if page_id not in page_attachments:
                    page_attachments[page_id] = []
                page_attachments[page_id].append(att_id)

        return page_attachments
