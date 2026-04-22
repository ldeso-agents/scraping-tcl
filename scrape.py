#!/usr/bin/env python3
"""Scrape certified companies from The Climate Label directory using Playwright."""

import csv
import datetime
import sys

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

BASE_URL = "https://explore.changeclimate.org"
OUTPUT_FILE = "companies.csv"

SOCIAL_MEDIA_DOMAINS = [
    "facebook.com",
    "twitter.com",
    "instagram.com",
    "linkedin.com",
    "youtube.com",
    "tiktok.com",
    "pinterest.com",
    "x.com",
    "threads.net",
]

BRAND_LINK_SELECTOR = 'a[href*="/brand/"]'


def collect_brand_urls(page):
    """Load the directory and paginate through all pages to collect brand URLs."""
    page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_selector(BRAND_LINK_SELECTOR, timeout=30000)

    all_urls = set()
    current_page = 1

    while current_page <= 30:
        links = page.eval_on_selector_all(
            BRAND_LINK_SELECTOR,
            'els => els.map(a => a.getAttribute("href"))',
        )
        all_urls.update(links)

        next_page = current_page + 1

        # Try to click the next page number button directly.
        btn = page.query_selector(f'button:text-is("{next_page}")')
        if not btn:
            # The button isn't visible yet — advance the pagination range.
            arrow = page.query_selector('button[aria-label="Next page"]')
            if not arrow or arrow.is_disabled():
                break
            arrow.click()
            page.wait_for_timeout(1000)
            btn = page.query_selector(f'button:text-is("{next_page}")')
            if not btn:
                break

        # Snapshot current brand links so we can detect when the page updates.
        old_links = page.evaluate(
            """() => Array.from(document.querySelectorAll('a[href*="/brand/"]'))
                     .map(a => a.href).join(",")"""
        )

        btn.click()

        # Wait until the brand links change, indicating the new page loaded.
        try:
            page.wait_for_function(
                """(old) => Array.from(document.querySelectorAll('a[href*="/brand/"]'))
                           .map(a => a.href).join(",") !== old""",
                arg=old_links,
                timeout=10000,
            )
        except PlaywrightTimeoutError:
            break

        page.wait_for_timeout(500)
        current_page = next_page

    return sorted(all_urls)


def scrape_brand_page(page, brand_path):
    """Visit a brand page and extract company name and website."""
    page.goto(f"{BASE_URL}{brand_path}", wait_until="domcontentloaded", timeout=30000)
    page.wait_for_selector("h2", timeout=15000)

    company_name = page.eval_on_selector("h2", "el => el.innerText.trim()")

    # The website link is an external <a> whose visible text looks like a domain
    # (e.g. "www.peakdesign.com"), excluding changeclimate.org and social media.
    company_website = ""
    links = page.eval_on_selector_all(
        "a[href^='http']",
        "els => els.map(a => ({href: a.getAttribute('href'), text: a.innerText.trim()}))",
    )
    for link in links:
        href = link["href"] or ""
        text = link["text"]
        if "changeclimate.org" in href:
            continue
        if any(domain in href for domain in SOCIAL_MEDIA_DOMAINS):
            continue
        if text and "." in text and " " not in text:
            company_website = href
            break

    return company_name, company_website


def load_existing_rows(filepath):
    """Load existing CSV rows keyed by company name."""
    rows = {}
    try:
        with open(filepath, newline="") as f:
            for row in csv.DictReader(f):
                rows[row["company_name"]] = row
    except FileNotFoundError:
        pass
    return rows


def main():
    today = datetime.date.today().isoformat()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        print("Loading directory page...")
        brand_urls = collect_brand_urls(page)
        print(f"Found {len(brand_urls)} certified brands")

        if not brand_urls:
            print("ERROR: No brands found on the directory page.", file=sys.stderr)
            browser.close()
            sys.exit(1)

        companies = []
        for i, brand_path in enumerate(brand_urls, 1):
            slug = brand_path.rsplit("/", 1)[-1]
            print(f"[{i}/{len(brand_urls)}] {slug}")
            try:
                name, website = scrape_brand_page(page, brand_path)
                if name:
                    companies.append(
                        {
                            "date_added": today,
                            "company_name": name,
                            "company_website": website,
                            "date_removed": "",
                        }
                    )
            except PlaywrightTimeoutError:
                print(f"  Timeout — skipping")
            except Exception as e:
                print(f"  Error: {e}")

        browser.close()

    # Preserve the original date_added for companies already in the CSV so
    # the field reflects when the company was *first* recorded, not the last
    # time the scraper ran.
    existing_rows = load_existing_rows(OUTPUT_FILE)
    scraped_names = {c["company_name"] for c in companies}

    for company in companies:
        existing = existing_rows.get(company["company_name"])
        if existing and existing.get("date_added"):
            company["date_added"] = existing["date_added"]

    # Carry over companies that previously appeared but were not found in this
    # scrape. Stamp date_removed on the first run where they go missing.
    for name, existing in existing_rows.items():
        if name in scraped_names:
            continue
        companies.append(
            {
                "date_added": existing.get("date_added", ""),
                "company_name": name,
                "company_website": existing.get("company_website", ""),
                "date_removed": existing.get("date_removed") or today,
            }
        )

    companies.sort(key=lambda c: c["company_name"].lower())

    with open(OUTPUT_FILE, "w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["date_added", "company_name", "company_website", "date_removed"],
        )
        writer.writeheader()
        writer.writerows(companies)

    print(f"\nSaved {len(companies)} companies to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
