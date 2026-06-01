import xml.etree.ElementTree as ET
import difflib


def canonicalize_element(element):
    """Recursively sorts attributes and child elements."""
    # 1. Sort the attributes of the current element
    if element.attrib:
        element.attrib = dict(sorted(element.attrib.items()))

    # 2. Sort the child elements based on tag, attributes, and text
    children = list(element)
    if children:
        # Sort children using a combination of tag name and text to maintain consistency
        children.sort(key=lambda x: (x.tag, str(x.attrib), (x.text or '').strip()))

        # Replace unordered children with the sorted list
        element[:] = children

        # Recurse into children
        for child in element:
            canonicalize_element(child)


filenames = ["siteMapCampus",
             # "siteMapIndex",
             "siteMapJournal",
             "siteMapMonographSeries",
             "siteMapORU",
             "siteMapSpecial",
             "siteMapStatic"]

for filename in filenames:
    print(f"Diffing: {filename}")
    file1_path = f"existing_xml/{filename}.xml"
    file2_path = f"test_xml/{filename}.xml"

    # Parse the files
    tree1 = ET.parse(file1_path)
    tree2 = ET.parse(file2_path)

    # Get root elements
    root1 = tree1.getroot()
    root2 = tree2.getroot()

    # Canonicalize trees in place
    canonicalize_element(root1)
    canonicalize_element(root2)

    # Convert back to pretty-printed strings for line-by-line diffing
    ET.indent(tree1, space="  ")
    ET.indent(tree2, space="  ")

    str1 = ET.tostring(root1, encoding='utf-8').decode('utf-8').splitlines()
    str2 = ET.tostring(root2, encoding='utf-8').decode('utf-8').splitlines()

    # Generate and print the unified diff
    diff = difflib.unified_diff(str1, str2, fromfile=file1_path, tofile=file2_path, lineterm='')
    with open(f"diff_check/{filename}_diff.txt", 'w') as f:
        f.write('\n'.join(diff))
