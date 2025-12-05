#!/usr/bin/env python3
"""
WordPress.com Recipe XML to Hugo Markdown Converter
Specialized for paragraph-based recipe format (Boyd Recipes style)
This handles recipes stored as plain text in paragraph blocks
and WordPress shortcodes.
"""

import xml.etree.ElementTree as ET
import re
import os
import sys
import argparse
from datetime import datetime
from pathlib import Path
import html

# Configuration
OUTPUT_DIR = "content/recipes"     # Hugo content directory
NAMESPACE = {
    'content': 'http://purl.org/rss/1.0/modules/content/',
    'wp': 'http://wordpress.org/export/1.2/'
}

def extract_images(text):
    """Extract images from HTML and convert to markdown"""
    if not text:
        return []

    images = []
    # Find all img tags
    img_pattern = r'<img[^>]+src=["\']([^"\']+)["\'][^>]*(?:alt=["\']([^"\']*)["\'][^>]*)?>'

    for match in re.finditer(img_pattern, text, re.IGNORECASE):
        url = match.group(1)
        alt = match.group(2) if len(match.groups()) > 1 and match.group(2) else ""

        # Fix WordPress URLs
        url = fix_image_references(url)

        images.append({'url': url, 'alt': alt})

    return images

def clean_html(text):
    """Remove HTML tags and decode entities"""
    if not text:
        return ""
    # Decode HTML entities
    text = html.unescape(text)
    # Remove <br /> tags and replace with newlines
    text = re.sub(r'<br\s*/?>', '\n', text)
    # Remove other HTML tags
    text = re.sub(r'<[^>]+>', '', text)
    # Clean up whitespace (but preserve structure)
    text = re.sub(r'\n\s*\n+', '\n\n', text)  # Multiple newlines to double
    text = text.strip()
    return text

def fix_image_references(text):
    """Fix WordPress image URLs to point to Hugo static directory"""
    if not text:
        return ""

    # Match WordPress image URLs and convert to Hugo static file references
    # Pattern: http://example.com/wp-content/uploads/YYYY/MM/image.jpg?query
    # Hugo serves files from static/ at the root, so static/2024/12/img.jpg -> /2024/12/img.jpg
    def replace_wp_url(match):
        path = match.group(1)
        # Strip query string from the path (everything after ?)
        path = path.split('?')[0]
        return f'/{path}'

    text = re.sub(
        r'https?://[^/]+/wp-content/uploads/(\d{4}/\d{2}/[^"\s)]+)',
        replace_wp_url,
        text
    )

    # Also handle relative paths
    def replace_wp_relative(match):
        path = match.group(1)
        # Strip query string from the path
        path = path.split('?')[0]
        return f'/{path}'

    text = re.sub(
        r'wp-content/uploads/(\d{4}/\d{2}/[^"\s)]+)',
        replace_wp_relative,
        text
    )

    return text

def parse_wordpress_shortcode(content, shortcode_name):
    """
    Parse WordPress shortcode and return list of content blocks.
    Handles both [shortcode]content[/shortcode] and [shortcode attr="value"]
    """
    results = []

    # Pattern for paired shortcodes: [name]content[/name]
    pattern = rf'\[{shortcode_name}[^\]]*\](.*?)\[/{shortcode_name}\]'
    matches = re.finditer(pattern, content, re.DOTALL | re.IGNORECASE)

    for match in matches:
        inner_content = match.group(1).strip()
        if inner_content:
            results.append(inner_content)

    return results

def extract_shortcode_attributes(content, shortcode_name):
    """Extract attributes from a WordPress shortcode"""
    pattern = rf'\[{shortcode_name}\s+([^\]]+)\]'
    match = re.search(pattern, content, re.IGNORECASE)

    if not match:
        return {}

    attrs_text = match.group(1)
    attrs = {}

    # Parse attributes like: key="value" or key='value'
    attr_pattern = r'(\w+)=["\']([^"\']+)["\']'
    for attr_match in re.finditer(attr_pattern, attrs_text):
        key = attr_match.group(1)
        value = attr_match.group(2)
        attrs[key] = value

    return attrs

def extract_source_info(content):
    """Extract source attribution from content"""
    source_data = {'name': None, 'url': None}

    # Common source patterns
    patterns = [
        r'[Ss]ource:\s*<a[^>]+href=["\']([^"\']+)["\'][^>]*>([^<]+)</a>',
        r'[Ss]ource:\s*([^\n<]+)',
        r'[Aa]dapted from:\s*<a[^>]+href=["\']([^"\']+)["\'][^>]*>([^<]+)</a>',
        r'[Aa]dapted from:\s*([^\n<]+)',
        r'[Ff]rom:\s*<a[^>]+href=["\']([^"\']+)["\'][^>]*>([^<]+)</a>',
        r'[Ff]rom:\s*([^\n<]+)',
        r'[Rr]ecipe by:\s*<a[^>]+href=["\']([^"\']+)["\'][^>]*>([^<]+)</a>',
        r'[Rr]ecipe by:\s*([^\n<]+)',
    ]

    for pattern in patterns:
        match = re.search(pattern, content)
        if match:
            if len(match.groups()) == 2:
                source_data['url'] = match.group(1)
                source_data['name'] = match.group(2).strip()
            else:
                source_data['name'] = match.group(1).strip()
            break

    return source_data

def parse_recipe_from_shortcodes(content):
    """
    Parse recipe from WordPress shortcodes like [recipe], [recipe-ingredients],
    [recipe-directions], [recipe-notes], etc.
    """
    recipe_data = {
        'prep_time': None,
        'cook_time': None,
        'total_time': None,
        'servings': None,
        'ingredients': [],
        'instructions': [],
        'notes': [],
        'other_content': '',
        'source': None,
        'images': []
    }

    # Extract images before cleaning
    recipe_data['images'] = extract_images(content)

    # Check if content has [recipe] wrapper
    has_recipe_wrapper = re.search(r'\[recipe[^\]]*\]', content, re.IGNORECASE)

    if has_recipe_wrapper:
        # Extract attributes from [recipe] shortcode
        recipe_attrs = extract_shortcode_attributes(content, 'recipe')
        if recipe_attrs:
            recipe_data['prep_time'] = recipe_attrs.get('preptime') or recipe_attrs.get('prep_time')
            recipe_data['cook_time'] = recipe_attrs.get('cooktime') or recipe_attrs.get('cook_time')
            recipe_data['total_time'] = recipe_attrs.get('totaltime') or recipe_attrs.get('total_time')
            recipe_data['servings'] = recipe_attrs.get('servings') or recipe_attrs.get('yield')

    # Extract ingredients from [recipe-ingredients] shortcodes
    ingredients_blocks = parse_wordpress_shortcode(content, 'recipe-ingredients')
    for block in ingredients_blocks:
        # Clean HTML and split into lines
        cleaned = clean_html(block)
        lines = [line.strip() for line in cleaned.split('\n') if line.strip()]
        recipe_data['ingredients'].extend(lines)

    # Extract directions/instructions from [recipe-directions] or [recipe-instructions]
    directions_blocks = parse_wordpress_shortcode(content, 'recipe-directions')
    directions_blocks.extend(parse_wordpress_shortcode(content, 'recipe-instructions'))
    for block in directions_blocks:
        cleaned = clean_html(block)
        lines = [line.strip() for line in cleaned.split('\n') if line.strip()]
        recipe_data['instructions'].extend(lines)

    # Extract notes from [recipe-notes]
    notes_blocks = parse_wordpress_shortcode(content, 'recipe-notes')
    for block in notes_blocks:
        cleaned = clean_html(block)
        lines = [line.strip() for line in cleaned.split('\n') if line.strip()]
        recipe_data['notes'].extend(lines)

    # Get content outside of shortcodes (for any additional info)
    # Remove all shortcodes to get remaining content
    clean_content = re.sub(r'\[recipe[^\]]*\].*?\[/recipe\]', '', content, flags=re.DOTALL | re.IGNORECASE)
    clean_content = re.sub(r'\[[^\]]+\]', '', clean_content)
    clean_content = clean_html(clean_content)
    if clean_content:
        recipe_data['other_content'] = clean_content

    # Extract source information
    source_info = extract_source_info(content)
    if source_info['name']:
        recipe_data['source'] = source_info

    return recipe_data

def parse_recipe_from_paragraph(content_text):
    """
    Parse recipe from a paragraph block that contains ingredients and instructions
    mixed together with <br /> tags separating items.
    
    Strategy: Look for lines and group them as ingredients vs instructions
    Ingredients are typically short, measurement-based lines
    Instructions are typically longer sentences
    """
    if not content_text:
        return None
    
    # Clean HTML first
    cleaned = clean_html(content_text)
    lines = [line.strip() for line in cleaned.split('\n') if line.strip()]
    
    if len(lines) < 3:  # Too short to be a real recipe
        return None
    
    recipe_data = {
        'prep_time': None,
        'cook_time': None,
        'total_time': None,
        'servings': None,
        'ingredients': [],
        'instructions': [],
        'notes': [],
        'other_content': '',
        'source': None,
        'images': []
    }

    # Extract images before cleaning
    recipe_data['images'] = extract_images(content_text)
    
    # Look for common patterns at the start
    for line in lines[:5]:
        # Check for time indicators
        if any(keyword in line.lower() for keyword in ['prep time', 'prep:', 'preparation']):
            time_match = re.search(r'(?:prep\s*time|prep)\s*[:\s]*([^,\n]+)', line, re.IGNORECASE)
            if time_match:
                recipe_data['prep_time'] = clean_html(time_match.group(1)).strip()
        
        if any(keyword in line.lower() for keyword in ['cook time', 'cook:', 'cooking']):
            time_match = re.search(r'(?:cook\s*time|cook)\s*[:\s]*([^,\n]+)', line, re.IGNORECASE)
            if time_match:
                recipe_data['cook_time'] = clean_html(time_match.group(1)).strip()
        
        if any(keyword in line.lower() for keyword in ['total time', 'total:']):
            time_match = re.search(r'(?:total\s*time|total)\s*[:\s]*([^,\n]+)', line, re.IGNORECASE)
            if time_match:
                recipe_data['total_time'] = clean_html(time_match.group(1)).strip()
        
        if any(keyword in line.lower() for keyword in ['servings', 'serves', 'yield']):
            serving_match = re.search(r'(?:servings?|serves|yield)\s*[:\s]*([^,\n]+)', line, re.IGNORECASE)
            if serving_match:
                recipe_data['servings'] = clean_html(serving_match.group(1)).strip()
    
    # Heuristic: categorize lines as ingredients or instructions
    # Ingredients typically have measurements or common ingredient markers
    # Instructions typically start with verbs
    
    ingredient_patterns = [
        r'^\d+[\s\-/½⅓¼]',  # Starts with number (measurement)
        r'^\d+\s*(cup|tablespoon|teaspoon|tbsp|tsp|oz|lb|ml|g)',
        r'(?:cup|tablespoon|teaspoon|tbsp|tsp|oz|lb|ml|g|quart|pint)',  # Contains measurement
        r'^(dash|pinch|splash|to\s+taste|salt|pepper|and)',  # Common ingredient starters
    ]
    
    verb_patterns = [
        r'^(add|place|combine|mix|beat|fold|stir|heat|cook|bake|fry|sauté|boil|simmer|remove|pour|spread|cut|slice|chop|dice)',
    ]
    
    for line in lines:
        line_lower = line.lower()
        
        # Skip metadata lines
        if any(skip in line_lower for skip in ['prep time', 'cook time', 'total time', 'servings', 'yield', 'serves']):
            continue
        
        # Check if it's an ingredient
        is_ingredient = any(re.search(pattern, line, re.IGNORECASE) for pattern in ingredient_patterns)
        
        # Check if it's an instruction
        is_instruction = any(re.search(pattern, line, re.IGNORECASE) for pattern in verb_patterns)
        
        # Heuristic: if it looks like a measurement, it's an ingredient
        # If it's short and starts with a number or measurement unit, it's likely an ingredient
        # If it's longer and starts with a verb, it's likely an instruction
        
        if is_ingredient and len(line) < 100:
            recipe_data['ingredients'].append(line)
        elif is_instruction and len(line) > 20:
            recipe_data['instructions'].append(line)
        elif len(line) > 50 and not is_ingredient:
            # Long lines without clear indicators are likely instructions
            recipe_data['instructions'].append(line)
        elif len(line) < 50 and not is_instruction:
            # Short lines without verbs are likely ingredients
            recipe_data['ingredients'].append(line)
    
    # Validate: a real recipe should have ingredients
    if not recipe_data['ingredients'] and not recipe_data['instructions']:
        return None

    # Extract source information
    source_info = extract_source_info(content_text)
    if source_info['name']:
        recipe_data['source'] = source_info

    return recipe_data

def categorize_post(title, content):
    """Categorize a post to determine how it should be handled"""
    title_lower = title.lower() if title else ""
    content_lower = content.lower() if content else ""

    # Check for social media links
    social_patterns = [
        'twitter', 'facebook', 'instagram', 'linkedin', 'pinterest',
        'social media', 'follow us', 'follow me', 'share this'
    ]
    if any(pattern in title_lower for pattern in social_patterns):
        return 'social_media'

    # Check for media directories or galleries
    media_patterns = [
        'media', 'gallery', 'images', 'photos', 'pictures',
        'directory', 'archive'
    ]
    if any(pattern in title_lower for pattern in media_patterns):
        # Also check if content is mostly image links
        if content and content.count('<img') > 5:
            return 'media_directory'

    # Check for drafts (WordPress draft status or indicators in title/content)
    if 'draft' in title_lower or 'draft' in content_lower[:200]:
        return 'draft'

    # Check if it's a page rather than a post (about, contact, etc.)
    page_patterns = ['about', 'contact', 'privacy', 'terms', 'disclaimer']
    if any(pattern in title_lower for pattern in page_patterns):
        return 'page'

    return 'other'

def create_regular_post(post_title, post_date, post_content, post_slug=None, is_draft=False):
    """Create a regular Hugo markdown file (non-recipe post)"""

    if not post_slug:
        post_slug = re.sub(r'[^a-z0-9]+', '-', post_title.lower()).strip('-')

    # Fix image references in content
    post_content = fix_image_references(post_content)
    # Clean HTML
    post_content = clean_html(post_content)

    draft_status = "true" if is_draft else "false"

    # Build YAML frontmatter
    markdown = f"""---
title: "{post_title.replace('"', '\\"')}"
date: {post_date}
slug: {post_slug}
draft: {draft_status}
---

{post_content}
"""

    return markdown

def create_hugo_markdown(post_title, post_date, post_content, recipe_data, post_slug=None, is_draft=False):
    """Create a Hugo markdown file with recipe shortcodes"""

    if not post_slug:
        post_slug = re.sub(r'[^a-z0-9]+', '-', post_title.lower()).strip('-')

    # Fix image references in content
    post_content = fix_image_references(post_content)

    draft_status = "true" if is_draft else "false"

    # Build YAML frontmatter
    markdown = f"""---
title: "{post_title.replace('"', '\\"')}"
date: {post_date}
slug: {post_slug}
draft: {draft_status}
---

"""

    # Add images if present
    if recipe_data.get('images'):
        for img in recipe_data['images']:
            alt_text = img.get('alt', '')
            url = img.get('url', '')
            # Use HTML img tag instead of markdown for proper styling
            markdown += f'<div class="recipe-image">\n'
            markdown += f'  <img src="{url}" alt="{alt_text}" />\n'
            markdown += f'</div>\n\n'

    # Add any other content before the recipe
    if recipe_data.get('other_content'):
        other_content = fix_image_references(recipe_data['other_content'])
        markdown += f"{other_content}\n\n"

    # Add recipe shortcode with metadata
    markdown += f'{{{{< recipe\n'
    markdown += f'  title="{post_title.replace('"', '\\"')}"\n'
    if recipe_data.get('prep_time'):
        markdown += f'  prepTime="{recipe_data["prep_time"].replace('"', '\\"')}"\n'
    if recipe_data.get('cook_time'):
        markdown += f'  cookTime="{recipe_data["cook_time"].replace('"', '\\"')}"\n'
    if recipe_data.get('total_time'):
        markdown += f'  totalTime="{recipe_data["total_time"].replace('"', '\\"')}"\n'
    if recipe_data.get('servings'):
        markdown += f'  servings="{recipe_data["servings"].replace('"', '\\"')}"\n'
    markdown += '>}}\n\n'

    # Add ingredients
    if recipe_data.get('ingredients'):
        markdown += '{{< recipe-ingredients >}}\n'
        for ingredient in recipe_data['ingredients']:
            # Fix image references and escape special markdown characters
            ingredient = fix_image_references(ingredient)
            ingredient = ingredient.replace('\\', '\\\\')
            # Strip leading dash/bullet if present (avoid double dashes)
            ingredient = ingredient.lstrip('- •*').strip()
            markdown += f'- {ingredient}\n'
        markdown += '{{< /recipe-ingredients >}}\n\n'

    # Add instructions
    if recipe_data.get('instructions'):
        markdown += '{{< recipe-instructions >}}\n'
        for instruction in recipe_data['instructions']:
            # Fix image references and escape special markdown characters
            instruction = fix_image_references(instruction)
            instruction = instruction.replace('\\', '\\\\')
            # Strip leading dash/bullet if present (avoid double dashes)
            instruction = instruction.lstrip('- •*').strip()
            # Strip leading numbers (e.g., "1. " or "23. ") since CSS adds numbering
            instruction = re.sub(r'^\d+\.\s*', '', instruction)
            markdown += f'- {instruction}\n'
        markdown += '{{< /recipe-instructions >}}\n\n'

    # Add notes if present
    if recipe_data.get('notes'):
        markdown += '{{< recipe-notes >}}\n'
        for note in recipe_data['notes']:
            # Fix image references and escape special markdown characters
            note = fix_image_references(note)
            note = note.replace('\\', '\\\\')
            # Strip leading dash/bullet if present (avoid double dashes)
            note = note.lstrip('- •*').strip()
            markdown += f'- {note}\n'
        markdown += '{{< /recipe-notes >}}\n\n'

    markdown += '{{< /recipe >}}\n'

    # Add source attribution if available
    if recipe_data.get('source'):
        source = recipe_data['source']
        markdown += '\n{{< recipe-source\n'
        if source.get('name'):
            markdown += f'  name="{source["name"].replace('"', '\\"')}"\n'
        if source.get('url'):
            markdown += f'  url="{source["url"].replace('"', '\\"')}"\n'
        markdown += '>}}\n'

    return markdown

def main():
    parser = argparse.ArgumentParser(
        description='Convert WordPress recipe export XML to Hugo markdown files'
    )
    parser.add_argument(
        'xml_file',
        help='Path to WordPress export XML file'
    )
    parser.add_argument(
        '-o', '--output',
        default=OUTPUT_DIR,
        help=f'Output directory for Hugo content (default: {OUTPUT_DIR})'
    )
    parser.add_argument(
        '--convert-skipped',
        action='store_true',
        help='Convert skipped posts (without recipes) to regular Hugo posts'
    )

    args = parser.parse_args()

    print("WordPress.com Recipe to Hugo Converter")
    print("=" * 60)

    # Check if XML file exists
    if not os.path.exists(args.xml_file):
        print(f"❌ Error: {args.xml_file} not found!")
        print("Please provide a valid WordPress export XML file.")
        sys.exit(1)

    # Create output directory if it doesn't exist
    output_dir = args.output
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    print(f"📁 Input file: {args.xml_file}")
    print(f"📁 Output directory: {output_dir}")

    # Parse WordPress XML
    print("\n📖 Parsing WordPress export file...")
    try:
        tree = ET.parse(args.xml_file)
        root = tree.getroot()
    except ET.ParseError as e:
        print(f"❌ Error parsing XML: {e}")
        sys.exit(1)

    # Define namespaces for proper parsing
    namespaces = {
        'content': 'http://purl.org/rss/1.0/modules/content/',
        'wp': 'http://wordpress.org/export/1.2/',
        'dc': 'http://purl.org/dc/elements/1.1/'
    }

    # Build attachment ID to URL mapping for featured images
    print("📷 Building attachment map...")
    attachments = {}
    for item in root.findall('.//item'):
        post_type_elem = item.find('{http://wordpress.org/export/1.2/}post_type')
        if post_type_elem is not None and post_type_elem.text == 'attachment':
            post_id_elem = item.find('{http://wordpress.org/export/1.2/}post_id')
            attachment_url_elem = item.find('{http://wordpress.org/export/1.2/}attachment_url')

            if post_id_elem is not None and attachment_url_elem is not None:
                attachments[post_id_elem.text] = attachment_url_elem.text

    print(f"   Found {len(attachments)} attachments")
    
    # Find all posts
    recipe_count = 0
    draft_recipe_count = 0
    skipped_count = 0
    shortcode_count = 0
    paragraph_count = 0
    skipped_posts = []  # Track skipped posts
    missing_images = []  # Track missing image files

    for item in root.findall('.//item'):
        title_elem = item.find('title')
        title = title_elem.text if title_elem is not None else "Untitled"
        # Decode HTML entities in title (e.g., &amp; -> &)
        title = html.unescape(title) if title else "Untitled"

        # Check post type - only process posts, not pages, attachments, etc.
        post_type_elem = item.find('{http://wordpress.org/export/1.2/}post_type')
        post_type = post_type_elem.text if post_type_elem is not None else "post"
        if post_type != "post":
            continue

        # Check post status
        status_elem = item.find('{http://wordpress.org/export/1.2/}status')
        post_status = status_elem.text if status_elem is not None else "publish"
        is_draft = (post_status != "publish")

        # Get post content
        content_elem = item.find('{http://purl.org/rss/1.0/modules/content/}encoded')
        if content_elem is None:
            continue

        content = content_elem.text or ""

        # Extract featured image from post metadata
        featured_image_url = None
        for meta in item.findall('{http://wordpress.org/export/1.2/}postmeta'):
            key_elem = meta.find('{http://wordpress.org/export/1.2/}meta_key')
            if key_elem is not None and key_elem.text == '_thumbnail_id':
                value_elem = meta.find('{http://wordpress.org/export/1.2/}meta_value')
                if value_elem is not None and value_elem.text:
                    thumbnail_id = value_elem.text
                    featured_image_url = attachments.get(thumbnail_id)
                    if featured_image_url:
                        featured_image_url = fix_image_references(featured_image_url)
                    break

        # Get date early so we can use it for skipped posts too
        pub_date_elem = item.find('{http://wordpress.org/export/1.2/}post_date')
        if pub_date_elem is not None and pub_date_elem.text:
            try:
                pub_date = pub_date_elem.text.replace(' ', 'T') + '-05:00'
                pub_date_display = pub_date_elem.text.split()[0]  # Just the date part
            except:
                pub_date = datetime.now().isoformat()
                pub_date_display = datetime.now().strftime('%Y-%m-%d')
        else:
            pub_date = datetime.now().isoformat()
            pub_date_display = datetime.now().strftime('%Y-%m-%d')

        if not content or len(content) < 50:
            category = categorize_post(title, content)
            skipped_posts.append({
                'title': title,
                'date': pub_date_display,
                'date_full': pub_date,
                'reason': f'Too short ({len(content)} chars)',
                'content': content,
                'category': category
            })
            skipped_count += 1
            continue

        # Try to extract recipe - first try shortcodes, then fall back to paragraph parsing
        recipe_data = None

        # Check if content has WordPress shortcodes
        if '[recipe' in content.lower():
            recipe_data = parse_recipe_from_shortcodes(content)
            if recipe_data and (recipe_data['ingredients'] or recipe_data['instructions']):
                shortcode_count += 1
            else:
                recipe_data = None

        # Fall back to paragraph parsing if no shortcodes or shortcode parsing failed
        if recipe_data is None:
            recipe_data = parse_recipe_from_paragraph(content)
            if recipe_data and (recipe_data['ingredients'] or recipe_data['instructions']):
                paragraph_count += 1

        # Skip if no real recipe data found
        if recipe_data is None or (not recipe_data.get('ingredients') and not recipe_data.get('instructions')):
            category = categorize_post(title, content)
            skipped_posts.append({
                'title': title,
                'date': pub_date_display,
                'date_full': pub_date,
                'reason': 'No recipe data found',
                'content': content,
                'category': category
            })
            skipped_count += 1
            continue

        # Add featured image if present
        if featured_image_url:
            # Add featured image at the beginning of the images list
            featured_img = {'url': featured_image_url, 'alt': title}
            if 'images' not in recipe_data or recipe_data['images'] is None:
                recipe_data['images'] = []
            # Insert at beginning so featured image appears first
            recipe_data['images'].insert(0, featured_img)

        # Validate that all image files exist
        if recipe_data.get('images'):
            for img in recipe_data['images']:
                img_url = img.get('url', '')
                if img_url.startswith('/') and not img_url.startswith('http'):
                    # Convert URL path to file system path
                    # Hugo serves static/YYYY/MM/file.jpg as /YYYY/MM/file.jpg
                    img_path = 'static' + img_url
                    if not os.path.exists(img_path):
                        missing_images.append({
                            'recipe': title,
                            'url': img_url,
                            'path': img_path
                        })

        # Create slug from title
        slug = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')

        # Create markdown
        markdown_content = create_hugo_markdown(title, pub_date, content, recipe_data, slug, is_draft)
        
        # Write file
        output_file = os.path.join(output_dir, f"{slug}.md")
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(markdown_content)

        recipe_count += 1
        if is_draft:
            draft_recipe_count += 1
            print(f"✓ {title} (draft)")
        else:
            print(f"✓ {title}")

    # Categorize and handle skipped posts
    converted_drafts = 0
    converted_pages = 0
    converted_other = 0
    ignored_count = 0
    unknown_posts = []

    if args.convert_skipped and skipped_posts:
        print("\n📝 Processing skipped posts...")
        for post in skipped_posts:
            category = post['category']
            slug = re.sub(r'[^a-z0-9]+', '-', post['title'].lower()).strip('-')

            # Ignore social media and media directory posts
            if category in ['social_media', 'media_directory']:
                ignored_count += 1
                continue

            # Convert drafts as draft posts
            if category == 'draft':
                markdown_content = create_regular_post(
                    post['title'],
                    post['date_full'],
                    post['content'],
                    slug,
                    is_draft=True
                )
                output_file = os.path.join(output_dir, f"{slug}.md")
                with open(output_file, 'w', encoding='utf-8') as f:
                    f.write(markdown_content)
                converted_drafts += 1
                print(f"✓ {post['title']} (draft)")

            # Convert pages and other posts as regular posts
            elif category in ['page', 'other']:
                markdown_content = create_regular_post(
                    post['title'],
                    post['date_full'],
                    post['content'],
                    slug,
                    is_draft=False
                )
                output_file = os.path.join(output_dir, f"{slug}.md")
                with open(output_file, 'w', encoding='utf-8') as f:
                    f.write(markdown_content)

                if category == 'page':
                    converted_pages += 1
                    print(f"✓ {post['title']} (page)")
                else:
                    converted_other += 1
                    unknown_posts.append(post)
                    print(f"✓ {post['title']} (regular post)")

    converted_skipped = converted_drafts + converted_pages + converted_other

    print("\n" + "=" * 60)
    print(f"✅ Successfully converted {recipe_count} recipes!")
    if draft_recipe_count > 0:
        print(f"   - {draft_recipe_count} marked as drafts")
    if shortcode_count > 0:
        print(f"   - {shortcode_count} from WordPress shortcodes")
    if paragraph_count > 0:
        print(f"   - {paragraph_count} from paragraph parsing")

    if args.convert_skipped:
        if converted_skipped > 0:
            print(f"\n✅ Converted {converted_skipped} non-recipe posts:")
            if converted_pages > 0:
                print(f"   - {converted_pages} pages")
            if converted_drafts > 0:
                print(f"   - {converted_drafts} drafts")
            if converted_other > 0:
                print(f"   - {converted_other} other posts")
        if ignored_count > 0:
            print(f"⊘ Ignored {ignored_count} social media/media directory posts")

        # Show details of "other" posts that were converted but may need review
        if unknown_posts:
            print(f"\n⚠ Converted {len(unknown_posts)} posts that couldn't be categorized:")
            print("   (These were converted but should be reviewed)")
            print("-" * 60)
            for post in unknown_posts:
                print(f"Title: {post['title']}")
                print(f"Date: {post['date']}")
                print(f"Content preview: {post['content'][:100]}...")
                print("-" * 60)
    else:
        if skipped_count > 0:
            # Categorize skipped posts for display
            categories_summary = {}
            for post in skipped_posts:
                cat = post['category']
                categories_summary[cat] = categories_summary.get(cat, 0) + 1

            print(f"\n⊘ Skipped {skipped_count} posts:")
            for category, count in categories_summary.items():
                category_name = category.replace('_', ' ').title()
                print(f"   - {count} {category_name}")

            print("\nSkipped Posts Details:")
            print("-" * 60)
            for post in skipped_posts:
                print(f"Title: {post['title']}")
                print(f"Date: {post['date']}")
                print(f"Category: {post['category'].replace('_', ' ').title()}")
                print(f"Reason: {post['reason']}")
                print(f"Content preview: {post['content'][:100]}...")
                print("-" * 60)
            print("\n💡 Tip: Use --convert-skipped to automatically handle these posts:")
            print("   - Pages and Other posts → Regular Hugo posts")
            print("   - Drafts → Hugo drafts")
            print("   - Social Media/Media Directory → Ignored")
    # Report missing images
    if missing_images:
        print(f"\n❌ WARNING: {len(missing_images)} missing image(s) detected!")
        print("=" * 60)
        for missing in missing_images:
            print(f"Recipe: {missing['recipe']}")
            print(f"  Missing: {missing['path']}")
            print(f"  URL: {missing['url']}")
            print()
        print("=" * 60)

    print(f"\n📁 Files saved to: {output_dir}")
    print("\n" + "=" * 60)
    print("📝 IMPORTANT: Review the generated files!")
    print("- Image references point to files in static/ directory")
    if missing_images:
        print(f"- ❌ {len(missing_images)} image(s) are missing - see warnings above")
    print("- The automatic parsing may not perfectly categorize all lines.")
    print("- You may need to manually adjust ingredients vs. instructions.")
    print("=" * 60)

if __name__ == "__main__":
    main()

