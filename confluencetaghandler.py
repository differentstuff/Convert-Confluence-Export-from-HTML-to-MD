from typing import Dict, Callable, Optional
from bs4 import BeautifulSoup, Tag
import logging
import re
import html

class ConfluenceTagHandler:
    """Base class for handling Confluence custom tags in XML content."""

    def __init__(self, soup: BeautifulSoup, logger: logging.Logger):
        """
        Initialize the handler with a BeautifulSoup object for tag creation and manipulation.

        Args:
            soup (BeautifulSoup): The BeautifulSoup object for parsing and creating tags.
        """
        self.soup = soup
        self.logger = logger

    def handle(self, tag: Tag) -> None:
        """
        Base method to handle a tag. Subclasses should override this.
        Default behavior is to unwrap the tag, preserving its content.

        Args:
            tag (Tag): The BeautifulSoup tag to process.
        """
        try:
            tag.unwrap()
        except Exception as e:
            self.logger.error(f"Error unwrapping tag {tag.name}: {e}")
            tag.decompose()  # Fallback: remove the tag entirely if unwrapping fails

    def create_replacement(self, tag_name: str, **attrs) -> Tag:
        """
        Utility method to create a replacement tag with specified attributes.

        Args:
            tag_name (str): The name of the tag to create (e.g., 'img', 'div').
            **attrs: Arbitrary keyword arguments for tag attributes.

        Returns:
            Tag: The newly created BeautifulSoup tag.
        """
        return self.soup.new_tag(tag_name, **attrs)

class ImageHandler(ConfluenceTagHandler):
    """Handler for <ac:image> tags, converting them to standard <img> tags."""
    def __init__(self, soup: BeautifulSoup, logger: logging.Logger): # Add logger parameter
        super().__init__(soup, logger) # Pass logger to super

    def handle(self, tag: Tag) -> None:
        """
        Convert <ac:image> to <img> with appropriate src and attributes.

        Args:
            tag (Tag): The <ac:image> tag to process.
        """
        try:
            src = None
            alt = tag.get('ac:alt', '')

            # Extract style attributes
            img_attrs = {}
            for attr in tag.attrs:
                if attr.startswith('ac:'):
                    attr_name = attr.replace('ac:', '')
                    if attr_name not in ['alt', 'width', 'height']:
                        # Convert style attributes to inline style
                        if attr_name == 'style':
                            img_attrs['style'] = tag[attr]
                        else:
                            # Add other attributes with ac: prefix removed
                            img_attrs[attr_name] = tag[attr]

            # Extract width and height if present
            if tag.has_attr('ac:width'):
                img_attrs['width'] = tag['ac:width']
            if tag.has_attr('ac:height'):
                img_attrs['height'] = tag['ac:height']

            # Find attachment or URL reference
            attachment = tag.find('ri:attachment')
            url = tag.find('ri:url')

            if attachment and attachment.has_attr('ri:filename'):
                src = attachment['ri:filename']
                self.logger.debug(f"Found image attachment: {src}")
            elif url and url.has_attr('ri:value'):
                src = url['ri:value']
                self.logger.debug(f"Found image URL: {src}")

            if src:
                # Create a standalone HTML structure that will convert well to Markdown
                # 1. Create the img tag
                img_tag = self.create_replacement('img', src=src, alt=alt or src, **img_attrs)

                # 2. Create a figure element to wrap the image
                figure_tag = self.soup.new_tag('figure')
                figure_tag.append(img_tag)

                # 3. Add a figcaption with the filename as a fallback
                if alt:
                    figcaption = self.soup.new_tag('figcaption')
                    figcaption.string = alt
                    figure_tag.append(figcaption)

                # Log the created HTML structure
                self.logger.debug(f"Created image HTML: {figure_tag}")

                # Replace the original tag with our new structure
                tag.replace_with(figure_tag)
            else:
                self.logger.warning(f"No source found for image tag: {tag}")
                # Instead of removing the tag, replace it with a placeholder
                placeholder = self.soup.new_tag('p')
                placeholder.string = "[Image: No source found]"
                tag.replace_with(placeholder)
        except Exception as e:
            self.logger.error(f"Error handling image tag {tag}: {e}")
            # Create a placeholder for the error
            error_tag = self.soup.new_tag('p')
            error_tag.string = f"[Image processing error: {str(e)}]"
            tag.replace_with(error_tag)

class LinkHandler(ConfluenceTagHandler):
    """Handler for <ac:link> tags, converting them to standard <a> tags."""
    def __init__(self, soup: BeautifulSoup, logger: logging.Logger): # Add logger parameter
        super().__init__(soup, logger) # Pass logger to super

    def handle(self, tag: Tag) -> None:
        """
        Convert <ac:link> to <a> with appropriate href and text content.

        Args:
            tag (Tag): The <ac:link> tag to process.
        """
        try:
            href = None
            text = ""
            link_body = tag.find('ac:plain-text-link-body') or tag.find('ac:link-body')
            if link_body:
                text = link_body.get_text()
            page = tag.find('ri:page')
            attachment = tag.find('ri:attachment')
            url = tag.find('ri:url')
            blog_post = tag.find('ri:blog-post')
            shortcut = tag.find('ri:shortcut')
            anchor = tag.get('ac:anchor')

            if url and url.has_attr('ri:value'):
                href = url['ri:value']
            elif page and page.has_attr('ri:content-title'):
                space = page.get('ri:space-key', '')
                title = page['ri:content-title']
                href = f"/spaces/{space}/pages/{title.replace(' ', '_')}" if space else f"/pages/{title.replace(' ', '_')}"
            elif attachment and attachment.has_attr('ri:filename'):
                href = attachment['ri:filename']
                self.logger.debug(f"Found attachment link: {href}")
            elif blog_post and blog_post.has_attr('ri:content-title'):
                href = f"/blog/{blog_post['ri:content-title'].replace(' ', '_')}"
            elif shortcut and shortcut.has_attr('ri:key'):
                key = shortcut['ri:key']
                param = shortcut.get('ri:parameter', '')
                href = f"/shortcut/{key}/{param}"
            elif anchor:
                href = f"#{anchor}"

            if not text:
                text = href or "link"

            a_tag = self.create_replacement('a', href=href or "#")
            a_tag.string = text
            tag.replace_with(a_tag)
        except Exception as e:
            self.logger.error(f"Error handling link tag {tag}: {e}")
            tag.unwrap()

class StructuredMacroHandler(ConfluenceTagHandler):
    """Handler for <ac:structured-macro> tags, converting them to appropriate HTML structures."""
    def __init__(self, soup: BeautifulSoup, logger: logging.Logger): # Add logger parameter
        super().__init__(soup, logger) # Pass logger to super

    def handle(self, tag: Tag) -> None:
        """
        Convert <ac:structured-macro> based on its type (e.g., code, panel).

        Args:
            tag (Tag): The <ac:structured-macro> tag to process.
        """
        try:
            macro_name = tag.get('ac:name', '')
            if macro_name == "code":
                language = ""
                code_text = ""
                param = tag.find('ac:parameter', {'ac:name': 'language'})
                if param:
                    language = param.get_text()
                code_body = tag.find('ac:plain-text-body')
                if code_body:
                    code_text = code_body.get_text()
                pre = self.create_replacement('pre')
                code = self.create_replacement('code')
                if language:
                    code['class'] = f"language-{language}"
                code.string = code_text
                pre.append(code)
                tag.replace_with(pre)
            elif macro_name == "panel":
                panel_div = self.create_replacement('div', **{'class': 'panel'})
                title = tag.find('ac:parameter', {'ac:name': 'title'})
                if title:
                    title_div = self.create_replacement('div', **{'class': 'panel-title'})
                    title_div.string = title.get_text()
                    panel_div.append(title_div)
                body = tag.find('ac:rich-text-body')
                if body:
                    body_div = self.create_replacement('div', **{'class': 'panel-body'})
                    body_div.append(BeautifulSoup(body.decode_contents(), "html.parser"))
                    panel_div.append(body_div)
                tag.replace_with(panel_div)
            elif macro_name in ["recently-updated", "contentbylabel"]:
                # Convert to a simple div with a class indicating the macro type
                div = self.create_replacement('div', **{'class': f"macro-{macro_name}"})
                div.string = f"[{macro_name} content placeholder]"
                tag.replace_with(div)
            else:
                self.logger.info(f"Unhandled macro: {macro_name}")
                tag.unwrap()
        except Exception as e:
            self.logger.error(f"Error handling structured macro {tag}: {e}")
            tag.unwrap()

class TaskListHandler(ConfluenceTagHandler):
    """Handler for <ac:task-list> tags, converting them to <ul> with checkbox indicators."""
    def __init__(self, soup: BeautifulSoup, logger: logging.Logger): # Add logger parameter
        super().__init__(soup, logger) # Pass logger to super

    def handle(self, tag: Tag) -> None:
        """
        Convert <ac:task-list> to <ul> with tasks as list items.

        Args:
            tag (Tag): The <ac:task-list> tag to process.
        """
        try:
            ul = self.create_replacement('ul', **{'class': 'task-list'})
            for task in tag.find_all('ac:task', recursive=False):
                li = self.create_replacement('li')
                status = task.find('ac:task-status')
                body = task.find('ac:task-body')
                checked = status and status.get_text() == "complete"
                li.string = f"[{'x' if checked else ' '}] {body.get_text() if body else ''}"
                ul.append(li)
            tag.replace_with(ul)
        except Exception as e:
            self.logger.error(f"Error handling task list tag {tag}: {e}")
            tag.unwrap()

class LayoutHandler(ConfluenceTagHandler):
    """Handler for <ac:layout> tags, converting them to structured <div> elements."""
    def __init__(self, soup: BeautifulSoup, logger: logging.Logger): # Add logger parameter
        super().__init__(soup, logger) # Pass logger to super

    def handle(self, tag: Tag) -> None:
        """
        Convert <ac:layout> to nested <div> elements for layout structure.

        Args:
            tag (Tag): The <ac:layout> tag to process.
        """
        try:
            layout_div = self.create_replacement('div', **{'class': 'layout'})
            for section in tag.find_all('ac:layout-section', recursive=False):
                section_div = self.create_replacement('div', **{'class': f"layout-section {section.get('ac:type', '')}"})
                for cell in section.find_all('ac:layout-cell', recursive=False):
                    cell_div = self.create_replacement('div', **{'class': 'layout-cell'})
                    cell_div.append(BeautifulSoup(cell.decode_contents(), "html.parser"))
                    section_div.append(cell_div)
                layout_div.append(section_div)
            tag.replace_with(layout_div)
        except Exception as e:
            self.logger.error(f"Error handling layout tag {tag}: {e}")
            tag.unwrap()

class EmoticonHandler(ConfluenceTagHandler):
    """Handler for <ac:emoticon> tags, converting them to <span> elements."""
    def __init__(self, soup: BeautifulSoup, logger: logging.Logger): # Add logger parameter
        super().__init__(soup, logger) # Pass logger to super

    def handle(self, tag: Tag) -> None:
        """
        Convert <ac:emoticon> to <span> with an emoji class.

        Args:
            tag (Tag): The <ac:emoticon> tag to process.
        """
        try:
            name = tag.get('ac:name', 'emoji')
            span = self.create_replacement('span', **{'class': f"emoji emoji-{name}"})
            span.string = f":{name}:"  # Use emoji shortcode format
            tag.replace_with(span)
        except Exception as e:
            self.logger.error(f"Error handling emoticon tag {tag}: {e}")
            tag.unwrap()

class PlaceholderHandler(ConfluenceTagHandler):
    """Handler for <ac:placeholder> tags, converting them to <span> elements."""
    def __init__(self, soup: BeautifulSoup, logger: logging.Logger): # Add logger parameter
        super().__init__(soup, logger) # Pass logger to super

    def handle(self, tag: Tag) -> None:
        """
        Convert <ac:placeholder> to <span> with placeholder content.

        Args:
            tag (Tag): The <ac:placeholder> tag to process.
        """
        try:
            span = self.create_replacement('span', **{'class': 'placeholder'})
            span.string = tag.get_text()
            tag.replace_with(span)
        except Exception as e:
            self.logger.error(f"Error handling placeholder tag {tag}: {e}")
            tag.unwrap()

class StatusHandler(ConfluenceTagHandler):
    """Handler for <ac:status> tags, converting them to styled <span> elements."""
    def __init__(self, soup: BeautifulSoup, logger: logging.Logger): # Add logger parameter
        super().__init__(soup, logger) # Pass logger to super

    def handle(self, tag: Tag) -> None:
        """
        Convert <ac:status> to <span> with status color and title.

        Args:
            tag (Tag): The <ac:status> tag to process.
        """
        try:
            title = tag.get('ac:title', '')
            color = tag.get('ac:color', '').lower()
            span = self.create_replacement('span', **{'class': f"status status-{color}"})
            span.string = title
            tag.replace_with(span)
        except Exception as e:
            self.logger.error(f"Error handling status tag {tag}: {e}")
            tag.unwrap()

class TextBodyHandler(ConfluenceTagHandler):
    """Handler for <ac:rich-text-body> and <ac:plain-text-body> tags, unwrapping them."""
    def __init__(self, soup: BeautifulSoup, logger: logging.Logger): # Add logger parameter
        super().__init__(soup, logger) # Pass logger to super

    def handle(self, tag: Tag) -> None:
        """
        Unwrap text body tags to preserve their content without extra markup.

        Args:
            tag (Tag): The text body tag to process.
        """
        try:
            tag.unwrap()
        except Exception as e:
            self.logger.error(f"Error unwrapping text body tag {tag}: {e}")
            tag.decompose()

class AttachmentHandler(ConfluenceTagHandler):
    """Handler for <ri:attachment> tags when they appear outside of other tags."""
    def __init__(self, soup: BeautifulSoup, logger: logging.Logger): # Add logger parameter
        super().__init__(soup, logger) # Pass logger to super

    def handle(self, tag: Tag) -> None:
        """
        Convert standalone <ri:attachment> tags to links or references.

        Args:
            tag (Tag): The attachment tag to process.
        """
        try:
            if tag.has_attr('ri:filename'):
                filename = tag['ri:filename']
                # Create a simple link to the attachment
                a_tag = self.create_replacement('a', href=filename)
                a_tag.string = filename
                tag.replace_with(a_tag)
                self.logger.debug(f"Converted standalone attachment to link: {filename}")
            else:
                self.logger.warning(f"Attachment tag without filename: {tag}")
                tag.unwrap()
        except Exception as e:
            self.logger.error(f"Error handling attachment tag {tag}: {e}")
            tag.unwrap()

def convert_custom_tags_to_html(content: str, logger: logging.Logger) -> str:
    """
    Convert Confluence Storage Format (cHTML) custom tags to standard HTML.
    This function is designed to be called from _convert_blog_html_to_md in converter.py.
    Ensures only Confluence custom tags are processed, leaving standard HTML intact.

    Args:
        content (str): The Confluence XML/HTML content to convert.
        logger (logging.Logger): The logger instance to use.

    Returns:
        str: The converted HTML content with custom tags replaced, formatted as standard HTML.
    """
    if not content:
        logger.warning("Input content is empty")
        return ""

    try:
        logger.debug(f"Original content for custom tag conversion (first 100 chars): {content[:100]}")

        # Decode HTML entities to ensure proper processing
        decoded_content = html.unescape(content)
        logger.debug(f"Decoded content (first 100 chars): {decoded_content[:100]}")

        # Ensure the content is wrapped in a root element for proper XML parsing
        # Only do this if it doesn't already have a root element
        #if not (decoded_content.strip().startswith('<') and decoded_content.strip().endswith('>')):
        #    wrapped_content = f"<div>{decoded_content}</div>"
        #else:
        #    wrapped_content = decoded_content
        wrapped_content = decoded_content
        #logger.debug(f"Wrapped content for parsing (first 100 chars): {wrapped_content[:100]}")

        # Try to use 'lxml-xml' parser for XML content, fall back to 'html.parser' if not available
        try:
            soup = BeautifulSoup(wrapped_content, "html.parser")
            #soup = BeautifulSoup(wrapped_content, "lxml-xml")
            logger.debug("Successfully parsed with lxml-xml.")
        except Exception as e:
            logger.warning(f"Failed to use 'lxml-xml' parser: {e}. Falling back to 'html.parser'")
            try:
                soup = BeautifulSoup(wrapped_content, "html.parser")
                logger.debug("Successfully parsed with html.parser.")
            except Exception as e_html_parser:
                logger.error(f"Failed to parse input content with html.parser either: {e_html_parser}")
                return content # Return original content if all parsing fails
    except Exception as e:
        logger.error(f"Failed to parse input content: {e}")
        return content  # Return original content as fallback

    # Define handlers for each custom tag type
    handlers: Dict[str, Callable[[Tag], None]] = {
        'ac:image': ImageHandler(soup, logger).handle,
        'ac:link': LinkHandler(soup, logger).handle,
        'ac:structured-macro': StructuredMacroHandler(soup, logger).handle,
        'ac:task-list': TaskListHandler(soup, logger).handle,
        'ac:layout': LayoutHandler(soup, logger).handle,
        'ac:emoticon': EmoticonHandler(soup, logger).handle,
        'ac:placeholder': PlaceholderHandler(soup, logger).handle,
        'ac:status': StatusHandler(soup, logger).handle,
        'ac:rich-text-body': TextBodyHandler(soup, logger).handle,
        'ac:plain-text-body': TextBodyHandler(soup, logger).handle,
        'ri:attachment': AttachmentHandler(soup, logger).handle,  # Handle standalone attachment tags
    }

    try:
        # Process all Confluence custom tags (ac: or ri: namespace)
        custom_tags = soup.find_all(lambda t: t.name and (t.name.startswith('ac:') or t.name.startswith('ri:')))
        
        logger.debug(f"Found {len(custom_tags)} custom tags: {[t.name for t in custom_tags]}")

        if not custom_tags:
            logger.debug("No custom tags found, returning content as is")
            return decoded_content

        # Process each custom tag
        for tag in custom_tags:
            # Skip ri:attachment tags that are inside ac:image tags
            if tag.name == 'ri:attachment' and tag.parent and tag.parent.name == 'ac:image':
                logger.debug(f"Skipping ri:attachment inside ac:image: {tag}")
                continue

            handler = handlers.get(tag.name)
            if handler:
                logger.debug(f"Processing tag: {tag.name}")
                handler(tag)
            else:
                logger.debug(f"No handler for tag: {tag.name}")
                tag.unwrap()

        # If we wrapped the content in a div, extract its contents
        if wrapped_content.startswith('<div>') and wrapped_content.endswith('</div>'):
            # Get the contents of the div without pretty printing
            result = ''.join(str(c) for c in soup.div.contents)
            return result
        else:
            # Return the full document without pretty printing
            # Remove doctype and xml declaration if present
            result = str(soup)
            result = re.sub(r'<!DOCTYPE[^>]*>', '', result)
            result = re.sub(r'<\?xml[^>]*\?>', '', result)
            return result.strip()
    except Exception as e:
        logger.error(f"Error during tag processing: {e}")
        return content  # Return original content as fallback