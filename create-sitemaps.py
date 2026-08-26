from pub_oapi_tools_common import eschol_db, aws_lambda
import xml.etree.cElementTree as ET
from datetime import datetime, timezone
from time import sleep
import boto3
from botocore.exceptions import ClientError

# Output XML files locally rather than uploading to S3
output_test_xml = False

# items per siteMapItem-#####.xml page
item_page_size = 10000

# S3 client and bucket name
s3_session = boto3.session.Session()
s3_client = s3_session.client('s3', region_name='us-west-2')

bucket_req = {'path': {'folder': 'pub-oapi-tools', 'names': ['eschol-sitemaps-bucket']}}
bucket_path = aws_lambda.get_parameters(bucket_req)['path']['eschol-sitemaps-bucket']

# Global vars, set dynamically below.
eschol_homepage, db_conn, cursor, bucket_dir, sitemap_filenames = None, None, None, None, None


def main():
    """Create sitemaps for various types of eSchol pages"""
    global eschol_homepage, db_conn, cursor, bucket_dir, sitemap_filenames

    for environment in ['stg', 'prd']:
        print(f"Creating sitemaps for environment: {environment}")

        # Environment-specific globals
        if environment == 'stg':
            eschol_homepage = 'https://pub-jschol2-stg.escholarship.org/'
            db_conn = eschol_db.get_connection(env='staging', database='eschol_test')

        elif environment == 'prd':
            eschol_homepage = 'https://escholarship.org/'
            db_conn = eschol_db.get_connection(env='prod', database='eschol')

        # More global inits
        bucket_dir = environment
        sitemap_filenames = []
        cursor = db_conn.cursor()

        # Create (and upload or output) sitemap XMLs
        create_sitemaps_for_unit_types()
        create_sitemaps_for_static_and_browse_pages()
        create_sitemaps_for_item_pages()
        create_sitemap_index()

        # Close conn for next environment reinitialization
        db_conn.close()


def create_sitemaps_for_unit_types():
    """
    Site maps for specific unit types (campus, journal, series, ORU, etc).

    Note: The db's unit.type codes are not exactly 1:1 with the
    site map XML filenames. The unit_types dict handles this mismatch.
    """
    unit_types = [
        {'db': 'campus', 'filename': 'Campus'},
        {'db': 'journal', 'filename': 'Journal'},
        {'db': 'oru', 'filename': 'ORU'},
        {'db': 'series', 'filename': 'Series'},
        {'db': 'monograph_series', 'filename': 'MonographSeries'},
        {'db': 'special', 'filename': 'Special'}]

    for unit_type in unit_types:
        print(f"Building Sitemap: {unit_type['db']} --> siteMap{unit_type['filename']}.xml")
        check_connection(db_conn)

        query = f"""
            select
                concat('uc/', u.id) as url,
                DATE(max(i.updated)) as lastmod
            from
                units u
                    join unit_items ui
                        on u.id = ui.unit_id
                    join items i
                        on i.id = ui.item_id
            where
                u.type = '{unit_type['db']}'
                and u.status != 'hidden'
            group by
                u.id;"""

        cursor.execute(query)
        rows = [row for row in cursor.fetchall()]

        generate_urlset_sitemap(rows=rows,
                                filename=f"siteMap{unit_type['filename']}.xml",
                                include_homepage=False,
                                priority_level="0.7")


def create_sitemaps_for_static_and_browse_pages():
    """Sitemap for campus, unit, and journal pages."""
    print(f"Querying data for Static and Browse pages:")

    # Hardcoded: escholarship.org/campuses & /journals
    urls = ["campuses", 'journals']

    print(f"Querying data for Units / Units.")
    check_connection(db_conn)
    units_query = """
        select concat(id, '/units') as url
        from units 
        where type = 'campus' 
        and id not in ('anrcs', 'lbnl');"""
    cursor.execute(units_query)
    rows = cursor.fetchall()
    urls.extend([row['url'] for row in rows])

    print(f"Querying data for unit Units / Journals.")
    check_connection(db_conn)
    journals_query = """
        select concat(id, '/journals') as url
        from units 
        where type = 'campus' 
        and id not in ('lbnl');"""
    cursor.execute(journals_query)
    rows = cursor.fetchall()
    urls.extend([row['url'] for row in rows])

    print(f"Querying data for static pages.")
    check_connection(db_conn)
    pages_query = """
        select
            case when unit_id = 'root' then slug
                else concat('uc/', unit_id, '/', slug)
            end as url 
        from pages;"""
    cursor.execute(pages_query)
    rows = cursor.fetchall()
    urls.extend([row['url'] for row in rows])

    generate_urlset_sitemap(urls=urls,
                            filename=f"siteMapStatic.xml",
                            include_lastmod=True,
                            include_homepage=True,
                            priority_level=None,
                            change_frequency=None)


def create_sitemaps_for_item_pages():
    """
    Creates item pages containing global var item_page_size items each.
    Uses limit and offset to query as many pages as needed, per item_page_size.
    """
    item_page_counter = 0
    while True:
        print(f"Quering item page: {item_page_counter:05}")
        check_connection(db_conn)
        offset = item_page_counter * item_page_size

        items_query = f"""
            select
                case 
                    when updated is not null then DATE(updated)
                    when last_indexed is not null then DATE(last_indexed)
                    when added is not null then DATE(added) 
                    else CURRENT_DATE
                end as lastmod,
                concat('uc/item/', right(id, 8)) as url
            from items
            where status in ('published', 'embargoed')
            limit {item_page_size} offset {offset};"""

        cursor.execute(items_query)
        rows = cursor.fetchall()

        # Break if we've run out of items
        if len(rows) == 0:
            break

        # Filename: Left pad page with five zeros
        filename = f"siteMapItem-{item_page_counter:05}.xml"
        sitemap_filenames.append(filename)

        # Generate the page XML
        root = ET.Element("urlset")
        set_namespaces(root)

        for row in rows:
            doc = ET.SubElement(root, "url")
            ET.SubElement(doc, "loc").text = f"{eschol_homepage}{row['url']}"
            ET.SubElement(doc, "lastmod").text = row['lastmod'].strftime("%Y-%m-%d")
            # ET.SubElement(doc, "changefreq").text = "monthly"

        if output_test_xml:
            tree = ET.ElementTree(root)
            ET.indent(tree, space=" ")
            tree.write(f"test_xml/{filename}", encoding="utf-8", xml_declaration=True)
        else:
            upload_to_s3(filename, root)

        # Increment counter, pause, loop
        item_page_counter += 1
        sleep(5)


def create_sitemap_index():
    """This sitemap contains links to all the other sitemaps."""

    print("Building sitemap index.")
    root = ET.Element("sitemapindex")
    set_namespaces(root)

    for sitemap_filename in sitemap_filenames:
        doc = ET.SubElement(root, "sitemap")
        ET.SubElement(doc, "loc").text = f"{eschol_homepage}{sitemap_filename}"

    filename = "siteMapIndex.xml"
    if output_test_xml:
        tree = ET.ElementTree(root)
        ET.indent(tree, space=" ")
        tree.write(f"test_xml/{filename}", encoding="utf-8", xml_declaration=True)
    else:
        upload_to_s3(filename, root)


def generate_urlset_sitemap(filename,
                            urls=None,
                            rows=None,
                            include_lastmod=True,
                            include_homepage=False,
                            change_frequency=None,
                            priority_level=None):
    """
    Utility for generating sitemaps in the <urlset> format.

    :param filename: XML filename to be saved / sent to S3
    :param urls: A list of URLS. Appended to the homepage (global var for stg/prd).
        Must include either urls or rows param.
    :param rows: Dicts including specifics for urls, lastmod
        Must include either urls or rows param.
    :param include_lastmod: If True, includes UTC Datetime.now() as <lastmod>
    :param include_homepage: If True, includes the homepage as the first <url> element, with priority 1.0
    :param change_frequency: If provided, include as <url> subelement <changefreq>
    :param priority_level: If provided, include as <url> subelement <priority>
    """

    if not rows and not urls:
        print("WARN: No rows or units here. "
              "This can happen with unit types having no items (e.g., Special), "
              "but it's probably a good idea to double-check this file.")

    print(f"Generating XML tree for: {filename}")
    sitemap_filenames.append(filename)

    # Define the root element with the required namespace
    root = ET.Element("urlset")
    set_namespaces(root)

    # Write homepage
    if include_homepage:
        doc = ET.SubElement(root, "url")
        ET.SubElement(doc, "loc").text = eschol_homepage
        ET.SubElement(doc, "lastmod").text = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        ET.SubElement(doc, "priority").text = "1.0"

    if urls:
        for url in urls:
            doc = ET.SubElement(root, "url")
            ET.SubElement(doc, "loc").text = f"{eschol_homepage}{url}"
            if include_lastmod:
                ET.SubElement(doc, "lastmod").text = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if change_frequency:
                ET.SubElement(doc, "changefreq").text = change_frequency
            if priority_level:
                ET.SubElement(doc, "priority").text = "0.7"

    elif rows:
        for row in rows:
            doc = ET.SubElement(root, "url")
            ET.SubElement(doc, "loc").text = f"{eschol_homepage}{row['url']}"
            if include_lastmod:
                ET.SubElement(doc, "lastmod").text = row['lastmod'].strftime("%Y-%m-%d")
            if change_frequency:
                ET.SubElement(doc, "changefreq").text = change_frequency
            if priority_level:
                ET.SubElement(doc, "priority").text = "0.7"

    if output_test_xml:
        tree = ET.ElementTree(root)
        ET.indent(tree, space=" ")
        tree.write(f"test_xml/{filename}", encoding="utf-8", xml_declaration=True)
    else:
        upload_to_s3(filename, root)


def set_namespaces(root):
    """
    Adds namespace attributes to the provided Element Tree.
    :param root: An ET root
    """
    root.set('xmlns', 'http://www.sitemaps.org/schemas/sitemap/0.9')
    root.set('xmlns:xsi',
             "http://www.w3.org/2001/XMLSchema-instance")
    root.set('xsi:schemaLocation',
             'http://www.sitemaps.org/schemas/sitemap/0.9 http://www.sitemaps.org/schemas/sitemap/0.9/sitemap.xsd')
    root.set('xmlns:image',
             "http://www.google.com/schemas/sitemap-image/1.1")
    root.set('xmlns:video',
             "http://www.google.com/schemas/sitemap-video/1.1")


def upload_to_s3(filename, xml_root):
    """
    Utility for uploading XMLs to S3. Uses the bucket_path & bucket_dir global vars.

    :param filename: XML filename for writing.
    :param xml_root: The root element of the exported XML.
    """
    print(f"Uploading to s3: {bucket_path}/{bucket_dir}/{filename}")

    # Convert ET root into byte string
    ET.indent(xml_root, space=" ")
    xml_bytes = ET.tostring(xml_root, encoding='utf-8', method='xml')

    # Upload byte string to s3 object
    # check result, retry if needed, raise if client error.
    status_code = 0
    retries = 3
    while status_code != 200 and retries > 0:
        try:
            response = s3_client.put_object(Bucket=bucket_path,
                                            Key=f'{bucket_dir}/{filename}',
                                            Body=xml_bytes,
                                            ContentType='application/xml')

            status_code = response['ResponseMetadata']['HTTPStatusCode']
            if status_code != 200:
                print(f"Unexpected status code: {status_code}, retries countdown: {retries}")
                retries -= 1
                sleep(30)

        except ClientError as e:
            raise f"Upload failed: {e.response['Error']['Message']}"


def check_connection(c):
    """
    Checks if the connection has been closed, attempt to reopen.
    :param c: pymysql connection object
    """
    retries = 3
    while not c.open and retries > 0:
        print(f"Connection has been closed, retries countdown: {retries}")
        retries -= 1
        sleep(30)
        c.open()
    if not c.open:
        raise "ERROR: Connection to eschol DB has been lost."


if __name__ == "__main__":
    main()
