# eSchol Sitemaps
- This program generates sitemaps for eScholarship.org, and uploads the files to an s3 bucket.
- It replicates/replaces an [existing function in jschol](https://github.com/eScholarship/jschol/blob/master/app/sitemap.rb)
- This rewrite was undertaken to lessen access on the Database. These XMLs will be generated periodically (daily, weekly), and served directly from s3 via a CloudFront behavior.
- Note: Uses the pub-oapi-tools commons for various functionality.